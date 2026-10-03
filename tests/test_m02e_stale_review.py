"""
M02-E 自动化测试：原子变更复核（PRD R4、R5、R7、FR14，FR02 中的 M01-C4）

- 全部在隔离的临时数据库中运行（test_env_helper.setup_test_db），不读写 server/data/zhixing.db。
- M01 状态变更一律走 M01 现有接口（确认新版本、停用、删除、排除、权限、文件删除、文件版本启用），
  验证钩子加在 M01 函数中且 M01 返回不变；新版本生效走「保存草稿 -> 确认 -> 索引任务切换」真实链路。
- 模拟部分：索引向量用固定向量代替 embedding 模型（只为让索引任务完成切换）；退回重生成的模型返回为
  「模拟返回」（patch skill_generation.post_chat_completion）。二者都不代表真实模型效果。
- 资格判断走真实 eligibility.py。

覆盖：AC16、AC17、AC18、AC21；七类触发各一例（新版本生效、停用、删除、排除、权限收紧、有效期到期、
来源文件换版）；待审核候选遇变更时不能通过；已驳回不处理；复核动作（更新引用、确认无影响、移除引用、
退回重生成、驳回）；删除影响中的 Skill 引用数；钩子异常不影响 M01；企业隔离。
"""

import json
import sys
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(SERVER_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_env_helper import cleanup_test_db, setup_test_db  # noqa: E402

import config  # noqa: E402
import indexing  # noqa: E402
import main as main_module  # noqa: E402
import scene_catalog as sc  # noqa: E402
import skill_generation as sg  # noqa: E402
import skill_recheck as rc  # noqa: E402
from database import get_db  # noqa: E402
from main import app  # noqa: E402
from skill_constants import REVIEW_CHECKLIST  # noqa: E402
from skill_validation import validate_skill  # noqa: E402

FAKE_KEY = "sk-m02e-fake-key-never-sent"
ALL_CHECKED = {key: True for key, _ in REVIEW_CHECKLIST}


def _fake_embed(texts):
    """模拟向量：只为让索引任务完成、触发版本切换，不代表检索效果。"""
    return [[0.01] * config.EMBEDDING_DIM for _ in list(texts)]


def _user_payload(messages):
    return json.loads(messages[1]["content"].split("\n", 1)[1])


def candidate_json(atoms, name="巡检异常分级处置"):
    """固定候选：每条原子一个规则判断步骤，首条原子同时作为前置条件依据。"""
    steps = [{"step_id": "s1", "kind": "输入校验", "action": "检查现场情况描述是否齐全", "refs": [],
              "basis": "通用操作", "on_fail": "补问"}]
    refs = []
    for i, atom in enumerate(atoms, start=2):
        sid = f"s{i}"
        steps.append({"step_id": sid, "kind": "规则判断", "action": f"按「{atom['title']}」判断处理方式",
                      "refs": [atom["version_id"]], "basis": "有原子依据", "on_fail": "转人工"})
        refs.append({"atom_item_id": atom["item_id"], "atom_version_id": atom["version_id"], "role": "判断规则",
                     "used_in_steps": [sid]})
    return {
        "schema_version": "1.0",
        "name": name,
        "goal": "判断巡检发现的异常应如何处置",
        "trigger_description": "用户描述巡检中发现的异常，询问如何处理",
        "task_type": "判断分级",
        "scene_id": "x",
        "applies_to": {"customer_types": ["住宅业主"], "property_types": [], "conditions": []},
        "not_applies_to": ["收费争议属于另一任务"],
        "knowledge_refs": refs,
        "inputs": [{"key": "situation", "label": "现场情况", "type": "text", "required": True}],
        "preconditions": [{"text": "异常已由巡检人员现场确认", "ref": atoms[0]["version_id"]}],
        "outputs": [{"key": "route", "label": "处置方式", "type": "text", "required": True}],
        "steps": steps,
        "risk_boundary": ["不替代现场人员对安全风险的判断"],
        "escalation_conditions": ["无法判断时转人工"],
        "generation_confidence": {"level": "高", "reason": "测试固定候选"},
    }


class TestM02EStaleReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db = setup_test_db("m02e_stale")
        cls.client = TestClient(app)
        cls.admin_headers, cls.admin_user = cls._login("admin", "Admin@Zhixing2026")
        cls.member_headers, _ = cls._login("member", "Member@Zhixing2026")
        cls.other_headers, cls.other_user = cls._login("other_admin", "Other@Zhixing2026")
        cls.org_id = cls.admin_user["organization_id"]
        with get_db() as conn:
            cls.actor = sg.load_actor(conn, cls.admin_user["id"], cls.org_id)
            cls.scene = sc.create_scene(conn, cls.org_id, cls.admin_user["id"], {
                "name": "设备巡检", "description": "巡检异常处置", "aliases": ["设备巡检"],
                "typical_problems": ["巡检发现异常怎么办"]})

    @classmethod
    def tearDownClass(cls):
        cleanup_test_db(cls.test_db)

    @classmethod
    def _login(cls, username, password):
        resp = cls.client.post("/api/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['token']}"}, resp.json()["user"]

    def setUp(self):
        self.prefix = "m02e_" + uuid.uuid4().hex[:6]
        for p in (patch.object(indexing, "embed_texts", _fake_embed),
                  patch.object(main_module, "submit_task", self._sync_submit),
                  patch.object(config, "DEEPSEEK_API_KEY", FAKE_KEY)):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _sync_submit(task_id):
        """清理索引任务同步执行（删除后的 M01 行为）；建索引任务由测试在确认后手动执行。"""
        with get_db() as conn:
            row = conn.execute("SELECT task_type FROM processing_tasks WHERE id = ?", (task_id,)).fetchone()
        if row and row["task_type"] == "clean_index":
            indexing.execute_clean_index_task(task_id)

    # ------------------------------------------------------------------ M01 fixture（走 M01 接口）
    def _document(self, scope="org_internal"):
        now = datetime.now(timezone.utc).isoformat()
        doc_id, ver_id = f"doc_{self.prefix}_{uuid.uuid4().hex[:4]}", f"ver_{self.prefix}_{uuid.uuid4().hex[:4]}"
        blocks = [f"blk_{uuid.uuid4().hex[:10]}" for _ in range(2)]
        with get_db() as conn:
            conn.execute(
                """INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted,
                   created_at, updated_at) VALUES (?, ?, 'M02-E 测试资料', NULL, ?, 0, ?, ?)""",
                (doc_id, self.org_id, scope, now, now))
            conn.execute(
                """INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name, file_type,
                   file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                   VALUES (?, ?, ?, 'v1', 'm02e.txt', 'txt', 100, ?, 'm02e.txt', ?, ?, 'completed')""",
                (ver_id, doc_id, self.org_id, uuid.uuid4().hex, self.admin_user["id"], now))
            for i, block in enumerate(blocks, start=1):
                conn.execute(
                    """INSERT INTO source_blocks (id, document_version_id, organization_id, block_index, block_type,
                       heading_path, paragraph_anchor, text_content, created_at)
                       VALUES (?, ?, ?, ?, 'paragraph', '巡检', ?, '巡检原文证据', ?)""",
                    (block, ver_id, self.org_id, i, f"[line_{i}]", now))
        return {"doc_id": doc_id, "ver_id": ver_id, "blocks": blocks}

    def _atom(self, doc, title, statement, scope="org_internal", confirm=True):
        """插入待确认原子（带证据），再经 M01 确认接口与索引任务成为当前生效版本。"""
        now = datetime.now(timezone.utc).isoformat()
        item_id, version_id = f"ki_{uuid.uuid4().hex[:10]}", f"kv_{uuid.uuid4().hex[:10]}"
        with get_db() as conn:
            conn.execute(
                """INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope,
                   lifecycle_status, is_excluded, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'active', 0, ?, ?)""",
                (item_id, doc["doc_id"], self.org_id, version_id, scope, now, now))
            conn.execute(
                """INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id,
                   version_number, title, content, primary_category, atom_type, subject, statement, conditions_json,
                   actions_json, exceptions_json, customer_types_json, business_scenes_json, problem_tags_json,
                   source_anchors_json, review_status, index_status, revision_token, created_at, created_by)
                   VALUES (?, ?, ?, ?, 1, ?, ?, '制度与标准', '规则', '物业服务人员', ?, '[]', ?, '[]',
                           '["住宅业主"]', '["设备巡检"]', '[]', '["[line_1]"]', 'pending_review', 'not_indexed',
                           ?, ?, ?)""",
                (version_id, item_id, self.org_id, doc["ver_id"], title, statement, statement,
                 json.dumps([statement], ensure_ascii=False), uuid.uuid4().hex, now, self.admin_user["id"]))
            for field, block in (("statement", doc["blocks"][0]), ("actions", doc["blocks"][1])):
                conn.execute(
                    """INSERT INTO knowledge_evidence (id, knowledge_version_id, source_block_id, organization_id,
                       field_name, excerpt, accuracy_level, created_at) VALUES (?, ?, ?, ?, ?, ?, 'high', ?)""",
                    (f"ev_{uuid.uuid4().hex[:10]}", version_id, block, self.org_id, field, statement, now))
        atom = {"item_id": item_id, "version_id": version_id, "title": title}
        if confirm:
            self._confirm(item_id)
        return atom

    def _item_detail(self, item_id):
        resp = self.client.get(f"/api/knowledge/items/{item_id}", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _confirm(self, item_id):
        detail = self._item_detail(item_id)
        target = detail.get("pending_version") or detail["active_version"]
        resp = self.client.post(f"/api/knowledge/items/{item_id}/confirm",
                                json={"revision_token": target["revision_token"]}, headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        indexing.execute_build_index_task(resp.json()["index_task_id"])
        return resp.json()

    def _new_version(self, item_id, statement):
        """M01：修改已生效知识 -> 待确认新版本 -> 确认 -> 索引就绪后切换（R5 新版本确认生效）。"""
        detail = self._item_detail(item_id)
        av = detail["active_version"]
        payload = {k: av[k] for k in ("title", "primary_category", "atom_type", "subject", "conditions", "actions",
                                      "exceptions", "metric_definition", "case_details", "customer_types",
                                      "business_scenes", "problem_tags", "valid_from", "valid_until")}
        payload.update({"revision_token": av["revision_token"], "statement": statement, "content": statement,
                        "access_scope": detail["access_scope"]})
        saved = self.client.put(f"/api/knowledge/items/{item_id}/draft", json=payload, headers=self.admin_headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        new_vid = saved.json()["version_id"]
        self._confirm(item_id)
        with get_db() as conn:
            active = conn.execute("SELECT active_version_id FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
        self.assertEqual(active["active_version_id"], new_vid, "M01 新版本应在索引就绪后切换为当前生效版本")
        return new_vid

    # ------------------------------------------------------------------ Skill fixture
    def _skill(self, atoms, approve=True, name="巡检异常分级处置"):
        cand = candidate_json(atoms, name)
        with get_db() as conn:
            cand, _removed = sg.normalize_candidate(cand, self.scene["scene_id"])
            report = validate_skill(conn, cand, self.actor, pool_ids=None)
            hard = [i.to_dict() for i in report.issues if i.level == "hard_error"]
            self.assertEqual(hard, [])
            stored = sg.store_candidate(
                conn, org_id=self.org_id, scene_id=self.scene["scene_id"], batch_id=None, task_key=None,
                candidate=cand, report=report,
                generation={"model_name": "fixture", "prompt_version": "fixture",
                            "task": {"name": name, "goal": cand["goal"], "task_type": cand["task_type"],
                                     "atom_version_ids": [a["version_id"] for a in atoms]}},
                created_by=self.admin_user["id"])
        skill_id = stored["skill_id"]
        if approve:
            wb = self._wb(skill_id)
            self.assertEqual({x["code"] for x in wb["evaluation"]["blockers"]}, {"CHECKLIST"})
            resp = self._action(skill_id, "approve", {"revision_token": wb["revision_token"],
                                                      "skill_json": wb["content"], "checklist": ALL_CHECKED})
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(self._skill_row(skill_id)["status"], "approved")
        return skill_id

    def _wb(self, skill_id, headers=None):
        resp = self.client.get(f"/api/skill-factory/skills/{skill_id}/workbench",
                               headers=self.admin_headers if headers is None else headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _action(self, skill_id, action, body, headers=None):
        return self.client.post(f"/api/skill-factory/skills/{skill_id}/review/{action}", json=body,
                                headers=self.admin_headers if headers is None else headers)

    def _recheck(self, skill_id, stale, content=None, **extra):
        wb = self._wb(skill_id)
        body = {"revision_token": wb["revision_token"], "stale_resolutions": stale, **extra}
        if content is not None:
            body["skill_json"] = content
        return self._action(skill_id, "recheck", body)

    def _skill_row(self, skill_id):
        with get_db() as conn:
            return dict(conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone())

    def _versions(self, skill_id):
        with get_db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM skill_versions WHERE skill_id = ? ORDER BY version_number", (skill_id,)).fetchall()]

    def _events(self, skill_id, open_only=True):
        with get_db() as conn:
            sql = "SELECT * FROM skill_stale_events WHERE skill_id = ?" + (" AND resolved_at IS NULL" if open_only else "")
            return [dict(r) for r in conn.execute(sql, (skill_id,)).fetchall()]

    def _current_ref_ids(self, skill_id):
        with get_db() as conn:
            return {r["atom_version_id"] for r in conn.execute(
                """SELECT sar.atom_version_id FROM skill_atom_refs sar JOIN skills s ON s.current_version_id = sar.skill_version_id
                   WHERE s.id = ?""", (skill_id,)).fetchall()}

    def _two_atoms(self, scope="org_internal"):
        doc = self._document(scope)
        a = self._atom(doc, "异常分级标准", "涉及人身安全的异常转入应急流程。", scope)
        b = self._atom(doc, "工单登记要求", "一般异常登记工单并通知责任人。", scope)
        return doc, a, b

    # ================================================================== 触发：新版本确认生效（AC16）
    def test_01_ac16_new_version_to_recheck_diff_and_update_ref(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        v1 = self._versions(skill_id)
        new_vid = self._new_version(a["item_id"], "涉及人身安全或设备停运的异常转入应急流程。")

        row = self._skill_row(skill_id)
        self.assertEqual(row["status"], "needs_recheck")
        self.assertIn("异常分级标准", row["stale_reason"])
        self.assertIn("新版本", row["stale_reason"])
        self.assertEqual(len(self._versions(skill_id)), len(v1), "变更不产生新版本，不自动改写内容")
        events = self._events(skill_id)
        self.assertEqual([(e["trigger_type"], e["atom_version_id"], e["new_atom_version_id"]) for e in events],
                         [("new_version", a["version_id"], new_vid)])

        # 列表：待复核在最前、标记知识已变更
        listing = self.client.get("/api/skill-factory/skills?needs_recheck=true", headers=self.admin_headers).json()
        item = next(i for i in listing["items"] if i["skill_id"] == skill_id)
        self.assertTrue(item["atom_changed"])
        self.assertIn("新版本", item["stale_reason"])

        # 复核视图：旧版本与新版本差异（M01 版本记录）、引用它的步骤与字段
        wb = self._wb(skill_id)
        self.assertIn("recheck", wb["allowed_actions"])
        self.assertNotIn("approve", wb["allowed_actions"])
        change = next(c for c in wb["atom_changes"] if c["atom_version_id"] == a["version_id"])
        self.assertEqual(change["new_version_id"], new_vid)
        self.assertEqual(change["options"], ["update", "no_impact", "remove"])
        statement_diff = next(d for d in change["diff"] if d["field"] == "statement")
        self.assertEqual(statement_diff["before"], "涉及人身安全的异常转入应急流程。")
        self.assertEqual(statement_diff["after"], "涉及人身安全或设备停运的异常转入应急流程。")
        self.assertEqual(change["new_version"]["version_number"], 2)
        self.assertIn("steps[s2]", change["affected_paths"])
        self.assertTrue(any(p.startswith("preconditions[") for p in change["affected_paths"]))
        self.assertFalse(wb["evaluation"]["can_approve"])
        self.assertIn("ATOM_CHANGE_UNHANDLED", {x["code"] for x in wb["evaluation"]["blockers"]})

        # 不处理直接提交：拒绝
        resp = self._recheck(skill_id, {})
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "RECHECK_BLOCKED")

        # 更新引用（同时编辑受影响的步骤）-> 产生复核处理版，回到已通过
        content = wb["content"]
        for step in content["steps"]:
            if step["step_id"] == "s2":
                step["action"] = "按「异常分级标准」判断，涉及人身安全或设备停运时转入应急流程"
        resp = self._recheck(skill_id, {a["version_id"]: {"action": "update"}}, content=content,
                             comment="知识补充了设备停运情形")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "approved")
        versions = self._versions(skill_id)
        self.assertEqual(len(versions), len(v1) + 1)
        self.assertEqual(versions[-1]["version_kind"], "recheck_revision")
        self.assertEqual(versions[-1]["review_action"], "recheck")
        new_json = json.loads(versions[-1]["skill_json"])
        s2 = next(s for s in new_json["steps"] if s["step_id"] == "s2")
        self.assertEqual(s2["refs"], [new_vid])
        self.assertIn("设备停运", s2["action"])
        self.assertEqual(new_json["preconditions"][0]["ref"], new_vid)
        self.assertEqual(self._current_ref_ids(skill_id), {new_vid, b["version_id"]})
        # 引用快照是新版本内容
        with get_db() as conn:
            snap = conn.execute("SELECT snapshot_statement FROM skill_atom_refs WHERE skill_version_id = ? AND atom_version_id = ?",
                                (versions[-1]["id"], new_vid)).fetchone()
            record = conn.execute("SELECT * FROM skill_review_records WHERE id = ?", (resp.json()["record_id"],)).fetchone()
        self.assertIn("设备停运", snap["snapshot_statement"])
        self.assertEqual(record["action"], "recheck")
        detail = json.loads(record["detail_json"])
        self.assertEqual(detail["atom_changes"][0]["resolution"], "update")
        self.assertIn("更新引用", detail["action_label"])
        diffs = json.loads(record["field_diffs_json"])
        self.assertIn("steps[s2].action", {d["path"] for d in diffs})
        row = self._skill_row(skill_id)
        self.assertEqual(row["status"], "approved")
        self.assertIsNone(row["stale_reason"])
        self.assertEqual(self._events(skill_id), [])
        self.assertEqual(len(self._events(skill_id, open_only=False)), 1)

    def test_02_confirm_no_impact_requires_note_and_only_changes_ref(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        before = json.loads(self._versions(skill_id)[-1]["skill_json"])
        new_vid = self._new_version(b["item_id"], "一般异常登记工单并通知责任人，登记时注明位置。")

        resp = self._recheck(skill_id, {b["version_id"]: {"action": "no_impact", "note": ""}})
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("说明", json.dumps(resp.json(), ensure_ascii=False))

        resp = self._recheck(skill_id, {b["version_id"]: {"action": "no_impact", "note": "只补充了登记要求，不影响判断"}})
        self.assertEqual(resp.status_code, 200, resp.text)
        versions = self._versions(skill_id)
        self.assertEqual(versions[-1]["version_kind"], "recheck_revision")
        after = json.loads(versions[-1]["skill_json"])
        # 只改引用：步骤动作、名称等不变，引用改指新版本
        self.assertEqual([s["action"] for s in after["steps"]], [s["action"] for s in before["steps"]])
        self.assertEqual(next(s for s in after["steps"] if s["step_id"] == "s3")["refs"], [new_vid])
        with get_db() as conn:
            record = conn.execute("SELECT detail_json FROM skill_review_records WHERE id = ?",
                                  (resp.json()["record_id"],)).fetchone()
        change = json.loads(record["detail_json"])["atom_changes"][0]
        self.assertEqual((change["resolution"], change["note"]), ("no_impact", "只补充了登记要求，不影响判断"))

    # ================================================================== 触发：停用（不能保持旧引用）
    def test_03_disabled_cannot_keep_ref_remove_then_fr11(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        resp = self.client.put(f"/api/knowledge/items/{b['item_id']}/lifecycle", json={"lifecycle_status": "disabled"},
                               headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        # M01 返回不变
        self.assertEqual(set(resp.json()), {"message", "item_id", "lifecycle_status", "previous_status"})
        self.assertEqual(self._skill_row(skill_id)["status"], "needs_recheck")
        self.assertEqual({e["trigger_type"] for e in self._events(skill_id)}, {"disabled"})

        wb = self._wb(skill_id)
        change = next(c for c in wb["atom_changes"] if c["atom_version_id"] == b["version_id"])
        self.assertEqual(change["options"], ["remove"])
        self.assertTrue(change["remove_only"])
        # 不能保持旧引用：确认无影响无效，仍被拦截
        resp = self._recheck(skill_id, {b["version_id"]: {"action": "no_impact", "note": "想保留"}})
        self.assertEqual(resp.status_code, 422, resp.text)

        # 移除引用：s3 只剩这一条依据，改为「无依据」，须按 FR11 处理
        check = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                                 json={"skill_json": wb["content"], "stale_resolutions": {b["version_id"]: {"action": "remove"}}},
                                 headers=self.admin_headers).json()
        s3 = next(s for s in check["content"]["steps"] if s["step_id"] == "s3")
        self.assertEqual((s3["refs"], s3["basis"]), ([], "无依据"))
        self.assertNotIn(b["version_id"], {r["atom_version_id"] for r in check["content"]["knowledge_refs"]})
        codes = {x["code"] for x in check["evaluation"]["blockers"]}
        self.assertEqual(codes, {"UNSUPPORTED_UNRESOLVED"})
        resp = self._recheck(skill_id, {b["version_id"]: {"action": "remove"}})
        self.assertEqual(resp.status_code, 422, resp.text)

        content = check["content"]
        s3 = next(s for s in content["steps"] if s["step_id"] == "s3")
        s3.update({"basis": "专家补充", "expert_reason": "工单登记是项目通行做法"})
        resp = self._recheck(skill_id, {b["version_id"]: {"action": "remove"}}, content=content)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._current_ref_ids(skill_id), {a["version_id"]})
        self.assertEqual(self._skill_row(skill_id)["status"], "approved")

    # ================================================================== 触发：删除（AC17）与删除影响（M01-C4）
    def test_04_ac17_deleted_snapshot_history_and_deletion_impact(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        old_version_id = self._skill_row(skill_id)["current_version_id"]

        impact = self.client.get(f"/api/knowledge/items/{b['item_id']}/deletion-impact", headers=self.admin_headers).json()
        self.assertEqual(impact["skill_reference_count"], 1)
        self.assertEqual(impact["skill_references"][0]["skill_id"], skill_id)

        resp = self.client.delete(f"/api/knowledge/items/{b['item_id']}", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "deleted")
        self.assertEqual(self._skill_row(skill_id)["status"], "needs_recheck")
        self.assertIn("已删除", self._skill_row(skill_id)["stale_reason"])

        wb = self._wb(skill_id)
        change = next(c for c in wb["atom_changes"] if c["atom_version_id"] == b["version_id"])
        self.assertEqual(change["options"], ["remove"])
        self.assertFalse(change["exists"])
        self.assertEqual(change["old_version"]["statement"], "一般异常登记工单并通知责任人。")
        resp = self._recheck(skill_id, {b["version_id"]: {"action": "no_impact", "note": "保留"}})
        self.assertEqual(resp.status_code, 422, "删除的原子不能保持旧引用")

        content = wb["content"]
        content["steps"] = [s for s in content["steps"] if s["step_id"] != "s3"]
        resp = self._recheck(skill_id, {b["version_id"]: {"action": "remove"}}, content=content)
        self.assertEqual(resp.status_code, 200, resp.text)

        # M01 历史版本接口对已删除条目返回 404，Skill 历史版本仍通过引用快照看到当时依据
        m01 = self.client.get(f"/api/knowledge/items/{b['item_id']}/versions/{b['version_id']}", headers=self.admin_headers)
        self.assertEqual(m01.status_code, 404)
        refs = self.client.get(f"/api/skill-factory/skills/{skill_id}/versions/{old_version_id}/refs",
                               headers=self.admin_headers)
        self.assertEqual(refs.status_code, 200, refs.text)
        snap = next(r for r in refs.json()["refs"] if r["atom_version_id"] == b["version_id"])
        self.assertEqual(snap["title"], "工单登记要求")
        self.assertEqual(snap["statement"], "一般异常登记工单并通知责任人。")
        self.assertTrue(snap["item_deleted"])
        self.assertEqual(snap["current_state"], "deleted")
        compare = self.client.get(f"/api/skill-factory/skills/{skill_id}/compare", headers=self.admin_headers)
        self.assertEqual(compare.status_code, 200, compare.text)

    # ================================================================== 触发：排除
    def test_05_excluded(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        resp = self.client.post(f"/api/knowledge/items/{a['item_id']}/exclude", json={"reason": "重复"},
                                headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["is_excluded"])
        self.assertEqual(self._skill_row(skill_id)["status"], "needs_recheck")
        self.assertEqual({e["trigger_type"] for e in self._events(skill_id)}, {"excluded"})
        change = next(c for c in self._wb(skill_id)["atom_changes"] if c["atom_version_id"] == a["version_id"])
        self.assertEqual(change["options"], ["remove"])

    # ================================================================== 触发：权限收紧（AC18）
    def test_06_ac18_scope_tightened_visibility_and_confirm_no_impact(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        self.assertEqual(self._skill_row(skill_id)["visibility"], "org_internal")
        resp = self.client.put(f"/api/knowledge/items/{a['item_id']}/access-scope", json={"access_scope": "admin_only"},
                               headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(set(resp.json()), {"message", "item_id", "access_scope", "previous_scope"})
        row = self._skill_row(skill_id)
        self.assertEqual(row["visibility"], "admin_only", "R4：可见范围随引用原子最严格的权限收紧")
        self.assertEqual(row["status"], "needs_recheck")
        self.assertIn("权限", row["stale_reason"])

        wb = self._wb(skill_id)
        self.assertEqual(wb["visibility"], "admin_only")
        change = next(c for c in wb["atom_changes"] if c["atom_version_id"] == a["version_id"])
        self.assertEqual(change["options"], ["no_impact", "remove"])
        resp = self._recheck(skill_id, {a["version_id"]: {"action": "no_impact", "note": "权限收紧不影响内容"}})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNone(resp.json()["version_number"], "只确认无影响且引用不变时不产生新版本")
        row = self._skill_row(skill_id)
        self.assertEqual((row["status"], row["visibility"]), ("approved", "admin_only"))

        # 文件权限收紧：派生知识一并收紧，引用它们的 Skill 可见范围收紧
        doc2, c, d = self._two_atoms()
        skill2 = self._skill([c, d], name="另一处置任务")
        resp = self.client.put(f"/api/documents/{doc2['doc_id']}/access-scope", json={"access_scope": "admin_only"},
                               headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        row = self._skill_row(skill2)
        self.assertEqual((row["visibility"], row["status"]), ("admin_only", "needs_recheck"))

    # ================================================================== 触发：有效期到期（启动与定时扫描）
    def test_07_expired_by_scan_once(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with get_db() as conn:
            conn.execute("UPDATE knowledge_versions SET valid_until = ? WHERE id = ?", (past, a["version_id"]))
        # 没有显式事件：扫描前状态不变
        self.assertEqual(self._skill_row(skill_id)["status"], "approved")
        with get_db() as conn:
            first = rc.scan_stale_references(conn)
        self.assertIn(skill_id, first["needs_recheck"])
        with get_db() as conn:
            second = rc.scan_stale_references(conn)
        self.assertNotIn(skill_id, second["needs_recheck"])
        events = self._events(skill_id)
        self.assertEqual([(e["trigger_type"], e["created_by"]) for e in events], [("expired", rc.SYSTEM_ACTOR)])
        self.assertIn("有效期", self._skill_row(skill_id)["stale_reason"])
        change = next(c for c in self._wb(skill_id)["atom_changes"] if c["atom_version_id"] == a["version_id"])
        self.assertEqual(change["options"], ["remove"])
        # M01 延长有效期后，原子重新可引用，可确认无影响
        with get_db() as conn:
            conn.execute("UPDATE knowledge_versions SET valid_until = NULL WHERE id = ?", (a["version_id"],))
        resp = self._recheck(skill_id, {a["version_id"]: {"action": "no_impact", "note": "有效期已延长"}})
        self.assertEqual(resp.status_code, 200, resp.text)

    # ================================================================== 触发：来源文件启用新版本
    def test_08_source_document_version_replaced(self):
        doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        now = datetime.now(timezone.utc).isoformat()
        ver2 = f"ver_{uuid.uuid4().hex[:10]}"
        with get_db() as conn:
            conn.execute(
                """INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name, file_type,
                   file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                   VALUES (?, ?, ?, 'v2', 'm02e.txt', 'txt', 100, ?, 'm02e2.txt', ?, ?, 'completed')""",
                (ver2, doc["doc_id"], self.org_id, uuid.uuid4().hex, self.admin_user["id"], now))
            conn.execute(
                """INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope,
                   lifecycle_status, is_excluded, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'org_internal', 'active', 0, ?, ?)""",
                (f"ki_{ver2}", doc["doc_id"], self.org_id, f"kv_{ver2}", now, now))
            conn.execute(
                """INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id, version_number,
                   title, content, primary_category, statement, review_status, index_status, revision_token,
                   created_at, created_by) VALUES (?, ?, ?, ?, 1, '新版异常分级', '新版内容', '制度与标准', '新版内容',
                   'confirmed', 'ready', ?, ?, ?)""",
                (f"kv_{ver2}", f"ki_{ver2}", self.org_id, ver2, uuid.uuid4().hex, now, self.admin_user["id"]))
        resp = self.client.post(f"/api/documents/{doc['doc_id']}/versions/{ver2}/activate", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._skill_row(skill_id)["status"], "needs_recheck")
        self.assertEqual({e["trigger_type"] for e in self._events(skill_id)}, {"source_replaced"})

    # ================================================================== 待审核候选遇变更
    def test_09_pending_candidate_keeps_status_but_cannot_approve(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b], approve=False)
        resp = self.client.put(f"/api/knowledge/items/{a['item_id']}/access-scope", json={"access_scope": "admin_only"},
                               headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200)
        new_vid = self._new_version(b["item_id"], "一般异常登记工单，并在两小时内通知责任人。")
        row = self._skill_row(skill_id)
        self.assertEqual(row["status"], "pending_review", "待审核候选状态不变")
        self.assertEqual(row["visibility"], "admin_only")
        self.assertEqual({(e["trigger_type"], e["effect"]) for e in self._events(skill_id)},
                         {("scope_tightened", "pending_notice"), ("new_version", "pending_notice")})

        wb = self._wb(skill_id)
        self.assertTrue(wb["atom_changed"])
        self.assertEqual(len(wb["atom_changes"]), 2)
        self.assertIn("ATOM_CHANGE_UNHANDLED", {x["code"] for x in wb["evaluation"]["blockers"]})
        resp = self._action(skill_id, "approve", {"revision_token": wb["revision_token"], "skill_json": wb["content"],
                                                  "checklist": ALL_CHECKED})
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("ATOM_CHANGE_UNHANDLED", {x["code"] for x in resp.json()["detail"]["blockers"]})
        listing = self.client.get("/api/skill-factory/skills", headers=self.admin_headers).json()
        self.assertTrue(next(i for i in listing["items"] if i["skill_id"] == skill_id)["atom_changed"])

        # 草稿保存复核选择，处理后可以通过（修改后通过，引用改指新版本）
        stale = {a["version_id"]: {"action": "no_impact", "note": "只收紧权限"}, b["version_id"]: {"action": "update"}}
        saved = self.client.put(f"/api/skill-factory/skills/{skill_id}/draft",
                                json={"revision_token": wb["revision_token"], "skill_json": wb["content"],
                                      "checklist": ALL_CHECKED, "stale_resolutions": stale},
                                headers=self.admin_headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        wb2 = self._wb(skill_id)
        self.assertEqual(wb2["stale_resolutions"][a["version_id"]]["action"], "no_impact")
        self.assertTrue(wb2["evaluation"]["can_approve"], wb2["evaluation"]["blockers"])
        resp = self._action(skill_id, "approve", {"revision_token": wb2["revision_token"], "checklist": ALL_CHECKED})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["action"], "approve_with_changes")
        self.assertEqual(self._current_ref_ids(skill_id), {a["version_id"], new_vid})
        self.assertEqual(self._events(skill_id), [])

    def test_pending_no_impact_can_approve_without_saving_draft(self):
        """检查和提交携带同一复核选择，权限收紧确认无影响无需先保存草稿。"""
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b], approve=False)
        response = self.client.put(f"/api/knowledge/items/{a['item_id']}/access-scope",
                                   json={"access_scope": "admin_only"}, headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        wb = self._wb(skill_id)
        payload = {"skill_json": wb["content"], "checklist": ALL_CHECKED,
                   "stale_resolutions": {a["version_id"]: {"action": "no_impact", "note": "仅收紧权限，内容无变化"}}}
        checked = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                                   json=payload, headers=self.admin_headers)
        self.assertEqual(checked.status_code, 200)
        self.assertTrue(checked.json()["evaluation"]["can_approve"])
        with get_db() as conn:
            self.assertIsNone(conn.execute("SELECT skill_id FROM skill_review_drafts WHERE skill_id = ?",
                                           (skill_id,)).fetchone())
        response = self._action(skill_id, "approve", {**payload, "revision_token": wb["revision_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._skill_row(skill_id)["visibility"], "admin_only")
        self.assertEqual(self._events(skill_id), [])

    def test_10_rejected_is_not_processed(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b], approve=False)
        wb = self._wb(skill_id)
        resp = self._action(skill_id, "reject", {"revision_token": wb["revision_token"], "reason": "其他", "note": "测试"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.client.put(f"/api/knowledge/items/{a['item_id']}/lifecycle", json={"lifecycle_status": "disabled"},
                        headers=self.admin_headers)
        self.assertEqual(self._skill_row(skill_id)["status"], "rejected")
        self.assertEqual(self._events(skill_id), [])
        self.assertIsNone(self._skill_row(skill_id)["stale_reason"])

    # ================================================================== 其余复核动作：退回重生成、驳回
    def test_11_regenerate_from_recheck_uses_new_atom_version(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        new_vid = self._new_version(a["item_id"], "涉及人身安全的异常立即转入应急流程并上报。")
        calls = []

        def fake_model(messages, api_key=None, **kwargs):  # 模拟返回
            assert api_key == FAKE_KEY
            payload = _user_payload(messages)
            calls.append(payload)
            atoms = [{"item_id": x["atom_item_id"], "version_id": x["atom_version_id"], "title": x["title"]}
                     for x in payload["atoms"]]
            return json.dumps(candidate_json(atoms), ensure_ascii=False), "deepseek-mock"

        with patch.object(sg, "post_chat_completion", fake_model):
            wb = self._wb(skill_id)
            resp = self._action(skill_id, "regenerate", {"revision_token": wb["revision_token"], "comment": ""})
            self.assertEqual(resp.status_code, 400, "退回意见必填")
            resp = self._action(skill_id, "regenerate", {"revision_token": wb["revision_token"],
                                                         "comment": "按知识新版本重写判断步骤", "field_groups": ["D"]})
            self.assertEqual(resp.status_code, 200, resp.text)
            deadline = time.time() + 30
            while time.time() < deadline and self._skill_row(skill_id)["status"] == "generating":
                time.sleep(0.05)
        self.assertEqual(self._skill_row(skill_id)["status"], "pending_review")
        given = {x["atom_version_id"] for x in calls[-1]["atoms"]}
        self.assertIn(new_vid, given)
        self.assertNotIn(a["version_id"], given)
        versions = self._versions(skill_id)
        self.assertEqual(versions[-1]["version_kind"], "ai_regenerated")
        self.assertEqual(self._current_ref_ids(skill_id), {new_vid, b["version_id"]})
        self.assertEqual(self._events(skill_id), [])
        self.assertIsNone(self._skill_row(skill_id)["stale_reason"])

    def test_12_reject_from_recheck(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        self.client.put(f"/api/knowledge/items/{a['item_id']}/lifecycle", json={"lifecycle_status": "disabled"},
                        headers=self.admin_headers)
        wb = self._wb(skill_id)
        self.assertIn("reject", wb["allowed_actions"])
        resp = self._action(skill_id, "reject", {"revision_token": wb["revision_token"], "reason": "原子依据不足",
                                                 "note": "依据知识已停用，任务不再成立"})
        self.assertEqual(resp.status_code, 200, resp.text)
        row = self._skill_row(skill_id)
        self.assertEqual((row["status"], row["stale_reason"]), ("rejected", None))
        self.assertEqual(self._events(skill_id), [])

    # ================================================================== 文件删除与删除影响
    def test_13_document_deletion_impact_and_delete(self):
        doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        pending_id = self._skill([a], approve=False, name="单条知识任务")
        impact = self.client.get(f"/api/documents/{doc['doc_id']}/deletion-impact", headers=self.admin_headers).json()
        self.assertEqual(impact["skill_reference_count"], 2)
        self.assertEqual(sum(impact["skill_reference_status_counts"].values()), 2)
        resp = self.client.delete(f"/api/documents/{doc['doc_id']}", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._skill_row(skill_id)["status"], "needs_recheck")
        self.assertEqual(self._skill_row(pending_id)["status"], "pending_review")
        self.assertEqual({e["trigger_type"] for e in self._events(skill_id)}, {"deleted"})

    # ================================================================== 钩子异常不影响 M01；并发
    def test_14_hook_failure_does_not_break_m01_and_scan_catches_up(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        with patch.object(rc, "_mark", side_effect=RuntimeError("injected")):
            resp = self.client.put(f"/api/knowledge/items/{a['item_id']}/lifecycle",
                                   json={"lifecycle_status": "disabled"}, headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        with get_db() as conn:
            self.assertEqual(conn.execute("SELECT lifecycle_status FROM knowledge_items WHERE id = ?",
                                          (a["item_id"],)).fetchone()[0], "disabled")
        self.assertEqual(self._skill_row(skill_id)["status"], "approved")
        with get_db() as conn:
            rc.scan_stale_references(conn)
        self.assertEqual(self._skill_row(skill_id)["status"], "needs_recheck")
        self.assertEqual({e["trigger_type"] for e in self._events(skill_id)}, {"disabled"})

    def test_15_stale_token_after_marking_conflicts(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        old_token = self._wb(skill_id)["revision_token"]
        self.client.put(f"/api/knowledge/items/{a['item_id']}/lifecycle", json={"lifecycle_status": "disabled"},
                        headers=self.admin_headers)
        resp = self._action(skill_id, "recheck", {"revision_token": old_token,
                                                  "stale_resolutions": {a["version_id"]: {"action": "remove"}}})
        self.assertEqual(resp.status_code, 409, resp.text)
        # 状态流转：待复核不能直接「通过」，已通过不能「复核」
        wb = self._wb(skill_id)
        self.assertEqual(self._action(skill_id, "approve", {"revision_token": wb["revision_token"]}).status_code, 409)
        other = self._skill(list(self._two_atoms()[1:]), name="未变更任务")
        wb2 = self._wb(other)
        self.assertEqual(self._action(other, "recheck", {"revision_token": wb2["revision_token"]}).status_code, 409)

    # ================================================================== AC21 权限与企业隔离
    def test_16_ac21_member_and_other_org_rejected(self):
        _doc, a, b = self._two_atoms()
        skill_id = self._skill([a, b])
        self.client.put(f"/api/knowledge/items/{a['item_id']}/lifecycle", json={"lifecycle_status": "disabled"},
                        headers=self.admin_headers)
        version_id = self._skill_row(skill_id)["current_version_id"]
        paths = [("get", f"/api/skill-factory/skills/{skill_id}/workbench"),
                 ("get", f"/api/skill-factory/skills/{skill_id}/versions/{version_id}/refs"),
                 ("post", f"/api/skill-factory/skills/{skill_id}/review/recheck"),
                 ("get", "/api/skill-factory/skills")]
        for method, path in paths:
            kwargs = {"json": {}} if method == "post" else {}
            self.assertEqual(getattr(self.client, method)(path, headers=self.member_headers, **kwargs).status_code, 403, path)
            self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 401, path)
        for method, path in paths[:3]:
            kwargs = {"json": {"revision_token": "x"}} if method == "post" else {}
            self.assertEqual(getattr(self.client, method)(path, headers=self.other_headers, **kwargs).status_code, 404, path)
        other_list = self.client.get("/api/skill-factory/skills", headers=self.other_headers).json()
        self.assertNotIn(skill_id, {i["skill_id"] for i in other_list["items"]})
        # 其他企业看不到本企业知识的删除影响
        self.assertEqual(self.client.get(f"/api/knowledge/items/{b['item_id']}/deletion-impact",
                                         headers=self.other_headers).status_code, 404)
        # 引用计数与变更记录不跨企业
        with get_db() as conn:
            self.assertEqual(rc.count_skill_references(conn, self.other_user["organization_id"], [b["item_id"]])["count"], 0)
            orgs = {r[0] for r in conn.execute("SELECT DISTINCT organization_id FROM skill_stale_events").fetchall()}
        self.assertEqual(orgs, {self.org_id})


if __name__ == "__main__":
    unittest.main()
