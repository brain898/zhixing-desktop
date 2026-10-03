"""
M02-D 自动化测试：审核状态与动作（PRD 7.2）、审核工作台与编辑（FR09~FR12）、差异记录与导出（FR13）

- 全部在隔离的临时数据库中运行（test_env_helper.setup_test_db），不读写 server/data/zhixing.db。
- 候选由 M02-C 批次流程生成；本文件中的模型返回一律为「模拟返回」（patch skill_generation.post_chat_completion），
  只用于流程、边界与异常测试，不代表真实模型效果。语义召回用固定的模拟检索函数。
- 资格判断走真实 eligibility.py。

覆盖：AC11、AC12、AC13、AC14、AC15、AC19；非法状态流转被拒；差异路径稳定性；导出内容与版本记录一致；
候选列表筛选与默认排序；无依据项处理与待沉淀经验；从全库新增引用须满足 R1；步骤编号不复用；鉴权与企业隔离。
"""

import copy
import json
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from test_env_helper import cleanup_test_db, setup_test_db  # noqa: E402

import config  # noqa: E402
import hybrid_retrieval  # noqa: E402
import scene_catalog as sc  # noqa: E402
import skill_generation as sg  # noqa: E402
from database import get_db  # noqa: E402
from main import app  # noqa: E402
from skill_constants import REVIEW_CHECKLIST  # noqa: E402
from skill_diff import diff_skill  # noqa: E402

FAKE_KEY = "sk-m02d-fake-key-never-sent"
FAKE_MODEL = "deepseek-mock"
ALL_CHECKED = {key: True for key, _ in REVIEW_CHECKLIST}
TASK_A = "核对指定期间收缴率"
TASK_B = "欠费分级与催缴建议"


def _user_payload(messages):
    return json.loads(messages[1]["content"].split("\n", 1)[1])


class FakeModel:
    """模拟返回：按任务名给出不同变体的候选。"""

    def __init__(self, atoms):
        self.atoms = atoms
        self.lock = threading.Lock()
        self.calls = []
        # 每个任务的变体：tbd（含待专家补充）、unsupported（含无依据步骤与数值）、confidence、outsider（引用池外知识）
        self.variants = {TASK_A: {"tbd": True, "unsupported": True, "confidence": "中"},
                         TASK_B: {"tbd": False, "unsupported": False, "confidence": "高"}}
        self.regenerate = None  # 退回重生成时的返回函数

    def candidate(self, payload, variant):
        atoms = payload["atoms"]
        # 与真实返回一致：通用操作步骤不写 refs（Schema 允许省略）
        steps = [{"step_id": "s1", "kind": "输入校验", "action": "检查统计期间与台账是否齐全",
                  "basis": "通用操作", "on_fail": "补问"}]
        refs = []
        for i, atom in enumerate(atoms, start=2):
            sid = f"s{i}"
            steps.append({"step_id": sid, "kind": "规则判断", "action": atom["statement"],
                          "refs": [atom["atom_version_id"]], "basis": "有原子依据", "on_fail": "转人工"})
            refs.append({"atom_item_id": atom["atom_item_id"], "atom_version_id": atom["atom_version_id"],
                         "role": (atom.get("default_roles") or ["判断规则"])[0], "used_in_steps": [sid]})
        if variant.get("unsupported"):
            steps.append({"step_id": f"s{len(atoms) + 2}", "kind": "生成表达", "action": "30 分钟内回访确认处置结果",
                          "refs": [], "basis": "无依据", "on_fail": "暂停"})
        if variant.get("outsider"):
            out = self.atoms["iso"]
            refs.append({"atom_item_id": out["item_id"], "atom_version_id": out["version_id"], "role": "判断规则",
                         "used_in_steps": ["s2"]})
            steps[1]["refs"].append(out["version_id"])
        exceptions = [e for a in atoms for e in a.get("exceptions") or []]
        tbd = variant.get("tbd")
        return {
            "schema_version": "1.0",
            "name": payload["task"]["name"][:20],
            "goal": payload["task"]["goal"],
            "trigger_description": "用户询问" + payload["task"]["name"],
            "task_type": payload["task"]["task_type"],
            "scene_id": "x",
            "applies_to": {"customer_types": ["住宅业主"], "property_types": [], "conditions": []},
            "not_applies_to": ["TBD_EXPERT"] if tbd else ["收缴率下降原因分析属于另一任务"],
            "knowledge_refs": refs,
            "inputs": [{"key": "period", "label": "统计期间", "type": "period", "required": True},
                       {"key": "amount", "label": "当期应收", "type": "number", "unit": "元", "required": True}],
            "preconditions": [],
            "outputs": [{"key": "result", "label": "判断结果", "type": "text", "required": True}],
            "steps": steps,
            "risk_boundary": ["TBD_EXPERT"] if tbd else ["不替代财务人员的最终核算"],
            "escalation_conditions": exceptions or ["无法判断时转人工"],
            "generation_confidence": {"level": variant.get("confidence", "中"), "reason": "模拟返回"},
        }

    def __call__(self, messages, api_key=None, **kwargs):
        assert api_key == FAKE_KEY
        payload = _user_payload(messages)
        with self.lock:
            self.calls.append(payload)
        if "atom_pool" in payload:
            ids = {a["title"]: a["atom_version_id"] for a in payload["atom_pool"]}
            return json.dumps({
                "tasks": [
                    {"name": TASK_A, "goal": "核对收缴率", "task_type": "计算核对",
                     "atom_version_ids": [ids["收缴率计算口径"], ids["实收归属条件"], ids["催缴方法"]],
                     "split_reason": "口径、归属与方法"},
                    {"name": TASK_B, "goal": "判断欠费等级并给出催缴措施", "task_type": "判断分级",
                     "atom_version_ids": [ids["欠费分级标准"], ids["催缴方法"], ids["报修响应要求"]],
                     "split_reason": "分级与催缴"},
                ],
                "unused_atoms": [],
            }, ensure_ascii=False), FAKE_MODEL
        if "review_comment" in payload and self.regenerate is not None:
            return self.regenerate(payload, messages), FAKE_MODEL
        variant = self.variants.get(payload["task"]["name"], {})
        return json.dumps(self.candidate(payload, variant), ensure_ascii=False), FAKE_MODEL


class TestM02DReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db = setup_test_db("m02d_review")
        cls.client = TestClient(app)
        cls.admin_headers, cls.admin_user = cls._login("admin", "Admin@Zhixing2026")
        cls.admin2_headers, _ = cls._login("admin", "Admin@Zhixing2026")  # 同一管理员的第二个会话
        cls.member_headers, _ = cls._login("member", "Member@Zhixing2026")
        cls.other_headers, _ = cls._login("other_admin", "Other@Zhixing2026")
        cls.org_id = cls.admin_user["organization_id"]
        cls.prefix = "m02d_" + uuid.uuid4().hex[:6]
        cls.atoms = {}
        cls._build_fixture()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_db(cls.test_db)

    def setUp(self):
        self.model = FakeModel(self.atoms)
        for p in (patch.object(config, "DEEPSEEK_API_KEY", FAKE_KEY),
                  patch.object(sg, "post_chat_completion", self.model),
                  patch.object(hybrid_retrieval, "hybrid_search", self._fake_search)):
            p.start()
            self.addCleanup(p.stop)

    # ------------------------------------------------------------------ fixture
    @classmethod
    def _login(cls, username, password):
        resp = cls.client.post("/api/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['token']}"}, resp.json()["user"]

    @classmethod
    def _build_fixture(cls):
        now = datetime.now(timezone.utc).isoformat()
        doc_id, ver_id = f"doc_{cls.prefix}", f"ver_{cls.prefix}"
        with get_db() as conn:
            conn.execute(
                """INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted,
                   created_at, updated_at) VALUES (?, ?, 'M02-D 测试资料', ?, 'org_internal', 0, ?, ?)""",
                (doc_id, cls.org_id, ver_id, now, now),
            )
            conn.execute(
                """INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name, file_type,
                   file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                   VALUES (?, ?, ?, 'v1', 'm02d.txt', 'txt', 100, ?, 'm02d.txt', 'usr_admin_001', ?, 'completed')""",
                (ver_id, doc_id, cls.org_id, uuid.uuid4().hex, now),
            )
            specs = [
                ("rate", "收缴率计算口径", "收缴率等于当期实收除以当期应收，按月统计。", ["收缴管理"], "指标数据", "指标",
                 ["当期应收为零时不计算收缴率"]),
                ("attr", "实收归属条件", "实收金额只计入与当期应收对应的款项。", ["收缴管理"], "制度与标准", "规则", []),
                ("grade", "欠费分级标准", "欠费超过3个月的业主列为重点欠费户。", ["收缴管理"], "制度与标准", "规则",
                 ["存在物业服务争议的欠费另行处理"]),
                ("remind", "催缴方法", "催缴先书面通知，再上门沟通。", ["收缴管理"], "方法与工具", "方法", []),
                ("repair", "报修响应要求", "业主报修后应及时派单。", ["报修响应"], "制度与标准", "规则", []),
                ("iso", "绿化修剪频次", "草坪生长季每月修剪两次。", ["绿化养护"], "方法与工具", "方法", []),
                ("visit", "回访时限要求", "处置完成后30分钟内回访业主。", ["客户服务"], "制度与标准", "规则", []),
            ]
            for key, title, statement, scenes, cat, atom_type, exceptions in specs:
                item_id, version_id = f"ki_{cls.prefix}_{key}", f"kv_{cls.prefix}_{key}"
                conn.execute(
                    """INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope,
                       lifecycle_status, is_excluded, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'org_internal', 'active', 0, ?, ?)""",
                    (item_id, doc_id, cls.org_id, version_id, now, now),
                )
                conn.execute(
                    """INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id,
                       version_number, title, content, primary_category, atom_type, subject, statement,
                       conditions_json, actions_json, exceptions_json, customer_types_json, business_scenes_json,
                       review_status, index_status, revision_token, created_at, created_by)
                       VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, '物业管理处', ?, '[]', ?, ?, '["住宅业主"]', ?,
                               'confirmed', 'ready', ?, ?, 'usr_admin_001')""",
                    (version_id, item_id, cls.org_id, ver_id, title, statement, cat, atom_type, statement,
                     json.dumps([statement], ensure_ascii=False), json.dumps(exceptions, ensure_ascii=False),
                     json.dumps(scenes, ensure_ascii=False), uuid.uuid4().hex, now),
                )
                cls.atoms[key] = {"item_id": item_id, "version_id": version_id, "title": title}
            cls.scene = sc.create_scene(conn, cls.org_id, cls.admin_user["id"], {
                "name": "物业费收缴管理", "description": "收缴率核对与欠费催缴", "aliases": ["收缴管理"],
                "typical_problems": ["收缴率怎么算"]})

    @classmethod
    def _fake_search(cls, conn, user, query, now_iso=None, **kwargs):
        if "收缴" not in query:
            return []
        atom = cls.atoms["repair"]
        return [{"item_id": atom["item_id"], "version_id": atom["version_id"], "title": atom["title"],
                 "primary_category": "制度与标准", "atom_type": "规则", "statement": "业主报修后应及时派单。",
                 "business_scenes": ["报修响应"], "relevance_score": 0.6}]

    # ------------------------------------------------------------------ helpers
    def _run_batch(self):
        resp = self.client.post(f"/api/skill-factory/scenes/{self.scene['scene_id']}/batches", json={},
                                headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        batch_id = resp.json()["batch_id"]
        deadline = time.time() + 30
        while time.time() < deadline:
            detail = self.client.get(f"/api/skill-factory/batches/{batch_id}", headers=self.admin_headers).json()
            if detail["status"] != "running":
                return detail
            time.sleep(0.05)
        self.fail("批次等待超时")

    def _candidate(self, task_name=TASK_A, status="pending_review"):
        batch = self._run_batch()
        for c in batch["candidates"]:
            if c["name"] == task_name[:20] and c["status"] == status:
                return c["skill_id"], batch
        self.fail(f"没有找到候选 {task_name} / {status}：{batch['candidates']}")

    def _wb(self, skill_id, headers=None):
        resp = self.client.get(f"/api/skill-factory/skills/{skill_id}/workbench", headers=self.admin_headers if headers is None else headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _action(self, skill_id, action, body, headers=None):
        return self.client.post(f"/api/skill-factory/skills/{skill_id}/review/{action}", json=body,
                                headers=self.admin_headers if headers is None else headers)

    def _versions(self, skill_id):
        with get_db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM skill_versions WHERE skill_id = ? ORDER BY version_number", (skill_id,)).fetchall()]

    def _records(self, skill_id):
        with get_db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM skill_review_records WHERE skill_id = ? ORDER BY created_at", (skill_id,)).fetchall()]

    def _status(self, skill_id):
        with get_db() as conn:
            return conn.execute("SELECT status FROM skills WHERE id = ?", (skill_id,)).fetchone()["status"]

    @staticmethod
    def _clean(content):
        """把候选改成可以通过的内容：替换待专家补充，把无依据步骤标为专家补充并填写理由。"""
        c = copy.deepcopy(content)
        c["not_applies_to"] = ["收缴率下降原因分析属于另一任务"]
        c["risk_boundary"] = ["不替代财务人员的最终核算"]
        for step in c["steps"]:
            if step["basis"] == "无依据":
                step["basis"] = "专家补充"
                step["expert_reason"] = "项目惯例：处置后 30 分钟内回访"
        return c

    def _wait_status(self, skill_id, not_status="generating", timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self._status(skill_id)
            if status != not_status:
                return status
            time.sleep(0.05)
        self.fail("等待状态变化超时")

    def _set_lifecycle(self, key, status):
        with get_db() as conn:
            conn.execute("UPDATE knowledge_items SET lifecycle_status = ? WHERE id = ?",
                         (status, self.atoms[key]["item_id"]))

    # ------------------------------------------------------------------ AC11 通过门槛
    def test_01_ac11_gate_blocks_until_all_conditions_met(self):
        skill_id, _ = self._candidate(TASK_A)
        wb = self._wb(skill_id)
        ev = wb["evaluation"]
        codes = {b["code"] for b in ev["blockers"]}
        self.assertFalse(ev["can_approve"])
        self.assertTrue({"CHECKLIST", "TBD_EXPERT", "UNSUPPORTED_UNRESOLVED"} <= codes, codes)
        tbd = next(b for b in ev["blockers"] if b["code"] == "TBD_EXPERT")
        self.assertEqual(len(tbd["paths"]), 2)
        unsupported = next(b for b in ev["blockers"] if b["code"] == "UNSUPPORTED_UNRESOLVED")
        self.assertEqual(len(unsupported["item_keys"]), 2)  # 无依据步骤 + 疑似无依据数值「30 分钟」

        # 后端强制：绕过界面直接提交也被拒绝，不产生版本、不改状态
        resp = self._action(skill_id, "approve", {"revision_token": wb["revision_token"], "skill_json": wb["content"],
                                                  "checklist": ALL_CHECKED})
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "APPROVAL_BLOCKED")
        blocked = {b["code"] for b in resp.json()["detail"]["blockers"]}
        self.assertIn("TBD_EXPERT", blocked)
        self.assertNotIn("CHECKLIST", blocked)
        self.assertEqual(self._status(skill_id), "pending_review")
        self.assertEqual(len(self._versions(skill_id)), 1)

        # 标为专家补充但不填理由：结构复验不通过（R3）
        content = self._clean(wb["content"])
        for step in content["steps"]:
            if step["basis"] == "专家补充":
                step["expert_reason"] = ""
        check = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                                 json={"skill_json": content, "checklist": ALL_CHECKED},
                                 headers=self.admin_headers).json()["evaluation"]
        self.assertIn("STRUCTURE", {b["code"] for b in check["blockers"]})

        # 引用的知识停用后不满足 R1
        content = self._clean(wb["content"])
        self._set_lifecycle("attr", "disabled")
        try:
            check = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                                     json={"skill_json": content, "checklist": ALL_CHECKED},
                                     headers=self.admin_headers).json()
            # M02-E 起，当前版本所引知识的变更归入「知识变更未处理」（FR14），不再重复作为 R1 问题提示
            self.assertEqual({b["code"] for b in check["evaluation"]["blockers"]}, {"ATOM_CHANGE_UNHANDLED"})
            attr = next(a for a in check["atoms"] if a["atom_version_id"] == self.atoms["attr"]["version_id"])
            self.assertFalse(attr["eligible"])
            listing = self.client.get("/api/skill-factory/skills", headers=self.admin_headers).json()
            self.assertTrue(next(i for i in listing["items"] if i["skill_id"] == skill_id)["atom_changed"])
        finally:
            self._set_lifecycle("attr", "active")

        # 编辑后内容不合格（必填字段为空、number 缺单位）
        broken = copy.deepcopy(content)
        broken["goal"] = ""
        broken["inputs"][1]["unit"] = None
        check = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                                 json={"skill_json": broken, "checklist": ALL_CHECKED},
                                 headers=self.admin_headers).json()["evaluation"]
        structure = next(b for b in check["blockers"] if b["code"] == "STRUCTURE")
        self.assertEqual(len(structure["details"]), 2)

        # 全部满足后可以通过
        check = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                                 json={"skill_json": content, "checklist": ALL_CHECKED},
                                 headers=self.admin_headers).json()["evaluation"]
        self.assertTrue(check["can_approve"], check["blockers"])
        self.assertEqual(check["approve_label"], "修改后通过")

    # ------------------------------------------------------------------ AC13 无修改通过
    def test_02_ac13_approve_without_changes(self):
        skill_id, _ = self._candidate(TASK_B)
        wb = self._wb(skill_id)
        self.assertEqual(wb["evaluation"]["blockers"][0]["code"], "CHECKLIST")
        self.assertEqual(len(wb["evaluation"]["blockers"]), 1, wb["evaluation"]["blockers"])
        self.assertEqual(wb["evaluation"]["approve_label"], "通过")
        # 原稿中省略 refs 的步骤，审核内容里补为空数组（前端按数组处理），且不计为修改
        self.assertNotIn("refs", next(s for s in wb["base_json"]["steps"] if s["step_id"] == "s1"))
        self.assertEqual(next(s for s in wb["content"]["steps"] if s["step_id"] == "s1")["refs"], [])
        self.assertFalse(wb["evaluation"]["has_changes"])
        resp = self._action(skill_id, "approve", {"revision_token": wb["revision_token"], "skill_json": wb["content"],
                                                  "checklist": ALL_CHECKED, "comment": "内容准确"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["action"], "approve")
        self.assertIsNone(body["version_number"])
        versions = self._versions(skill_id)
        self.assertEqual(len(versions), 1)  # 不产生新版本，原稿即通过版本
        self.assertEqual(versions[0]["review_action"], "approve")
        self.assertEqual(versions[0]["reviewed_by"], self.admin_user["id"])
        self.assertEqual(self._status(skill_id), "approved")
        records = self._records(skill_id)
        self.assertEqual([r["action"] for r in records], ["approve"])
        self.assertEqual(records[0]["from_version_id"], records[0]["to_version_id"])
        self.assertEqual(json.loads(records[0]["field_diffs_json"]), [])
        self.assertEqual(json.loads(records[0]["checklist_json"]), ALL_CHECKED)
        self.assertEqual(records[0]["operator_id"], self.admin_user["id"])
        # 已通过后不能再次通过或编辑
        wb = self._wb(skill_id)
        again = self._action(skill_id, "approve", {"revision_token": wb["revision_token"], "checklist": ALL_CHECKED})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["detail"]["code"], "ILLEGAL_TRANSITION")

    # ------------------------------------------------------------------ AC12 修改后通过
    def test_03_ac12_approve_with_changes_records_stable_diffs(self):
        skill_id, _ = self._candidate(TASK_A)
        wb = self._wb(skill_id)
        v1_json = self._versions(skill_id)[0]["skill_json"]
        content = self._clean(wb["content"])
        steps = {s["step_id"]: s for s in content["steps"]}
        self.assertEqual(sorted(steps), ["s1", "s2", "s3", "s4", "s5"])
        steps["s2"]["action"] = "按月统计收缴率：当期实收除以当期应收"
        new_id = f"s{wb['next_step_number']}"
        self.assertEqual(new_id, "s6")
        # 调整顺序（s4 移到 s3 前）、删除 s5、新增一个人工确认步骤
        content["steps"] = [steps["s1"], steps["s2"], steps["s4"], steps["s3"],
                            {"step_id": new_id, "kind": "人工确认", "action": "由收费主管确认核对结论", "refs": [],
                             "basis": "通用操作", "on_fail": "暂停"}]
        content["inputs"][0]["label"] = "统计月份"
        reasons = {"steps[s2].action": "口径表述更准确"}
        resp = self._action(skill_id, "approve", {"revision_token": wb["revision_token"], "skill_json": content,
                                                  "checklist": ALL_CHECKED, "change_reasons": reasons})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["action"], "approve_with_changes")
        self.assertEqual(resp.json()["version_number"], 2)
        versions = self._versions(skill_id)
        self.assertEqual([v["version_kind"] for v in versions], ["ai_original", "expert_revision"])
        self.assertEqual(versions[0]["skill_json"], v1_json)  # 原稿不变
        v2 = json.loads(versions[1]["skill_json"])
        self.assertEqual(v2["status"], "已通过（待测试）")
        self.assertEqual(v2["review"]["action"], "修改后通过")
        self.assertEqual([s["step_id"] for s in v2["steps"]], ["s1", "s2", "s4", "s3", "s6"])

        record = self._records(skill_id)[-1]
        self.assertEqual(record["action"], "approve_with_changes")
        diffs = json.loads(record["field_diffs_json"])
        by_path = {(d["path"], d["op"]) for d in diffs}
        self.assertIn(("steps[s2].action", "modify"), by_path)
        self.assertIn(("steps[s5]", "delete"), by_path)
        self.assertIn(("steps[s6]", "add"), by_path)
        self.assertIn(("steps", "reorder"), by_path)
        self.assertIn(("inputs[period].label", "modify"), by_path)
        # 调整顺序不产生整段误报：s3、s4 内容未改，不出现逐字段修改
        self.assertFalse([d for d in diffs if d["path"].startswith(("steps[s3]", "steps[s4]", "steps[s1]"))])
        self.assertEqual(sum(1 for d in diffs if d["op"] == "reorder"), 1)
        reason = next(d for d in diffs if d["path"] == "steps[s2].action")
        self.assertEqual(reason["reason"], "口径表述更准确")
        self.assertEqual(reason["before"], "收缴率等于当期实收除以当期应收，按月统计。")

        # 无依据项处理结果：无依据步骤 s5 与数值「30 分钟」均因删除而消除
        detail = json.loads(record["detail_json"])
        self.assertEqual(detail["summary"]["unsupported_resolutions"], {"delete": 2})
        self.assertEqual(detail["summary"]["steps_added"], 1)
        self.assertEqual(detail["summary"]["steps_deleted"], 1)

        # 字段对照：默认 AI 原稿对最新版本，结果与审核记录一致
        cmp = self.client.get(f"/api/skill-factory/skills/{skill_id}/compare", headers=self.admin_headers).json()
        self.assertEqual(cmp["from"]["version_number"], 1)
        self.assertEqual(cmp["to"]["version_number"], 2)
        self.assertEqual([(d["path"], d["op"]) for d in cmp["diffs"]], [(d["path"], d["op"]) for d in diffs])
        self.assertEqual(next(d for d in cmp["diffs"] if d["path"] == "steps[s2].action")["reason"], "口径表述更准确")

    # ------------------------------------------------------------------ 差异路径稳定性（纯函数）
    def test_04_diff_paths_are_stable(self):
        base = {
            "name": "核对收缴率",
            "not_applies_to": ["甲", "乙"],
            "knowledge_refs": [{"atom_item_id": "ki1", "atom_version_id": "kv1", "role": "判断规则", "used_in_steps": ["s2"]},
                               {"atom_item_id": "ki2", "atom_version_id": "kv2", "role": "执行动作", "used_in_steps": ["s3"]}],
            "inputs": [{"key": "a", "label": "甲", "type": "text", "required": True},
                       {"key": "b", "label": "乙", "type": "number", "unit": "元", "required": True}],
            "preconditions": [{"text": "条件一", "ref": "kv1"}, {"text": "条件二", "ref": None}],
            "steps": [{"step_id": f"s{i}", "kind": "规则判断", "action": f"动作{i}", "refs": ["kv1", "kv2"],
                       "basis": "有原子依据", "on_fail": "转人工"} for i in range(1, 6)],
            "status": "待审核", "version_number": 1, "unsupported_items": [],
        }
        # 只调整顺序：步骤记一条「调整顺序」；字符串列表、引用、前置条件、步骤内依据顺序变化不记
        moved = copy.deepcopy(base)
        moved["steps"] = [moved["steps"][i] for i in (4, 0, 1, 2, 3)]
        moved["steps"][1]["refs"] = ["kv2", "kv1"]
        moved["not_applies_to"] = ["乙", "甲"]
        moved["knowledge_refs"].reverse()
        moved["preconditions"].reverse()
        moved["status"] = "已通过（待测试）"   # F 组不计入差异
        moved["version_number"] = 2
        diffs = diff_skill(base, moved)
        self.assertEqual([(d["path"], d["op"]) for d in diffs], [("steps", "reorder")])
        self.assertEqual(diffs[0]["after"], ["s5", "s1", "s2", "s3", "s4"])

        # 修改按稳定标识定位，不按下标
        edited = copy.deepcopy(moved)
        edited["steps"][0]["action"] = "新的动作5"
        edited["inputs"][1]["required"] = False
        edited["knowledge_refs"][0]["role"] = "前置条件"
        edited["not_applies_to"] = ["乙", "丙"]
        paths = {(d["path"], d["op"]) for d in diff_skill(moved, edited)}
        self.assertIn(("steps[s5].action", "modify"), paths)
        self.assertIn(("inputs[b].required", "modify"), paths)
        self.assertIn(("knowledge_refs[kv2].role", "modify"), paths)
        self.assertEqual(sum(1 for p, op in paths if p.startswith("not_applies_to[")), 2)
        self.assertEqual(len(paths), 5)
        labels = {d["path"]: d["label"] for d in diff_skill(moved, edited)}
        self.assertEqual(labels["steps[s5].action"], "步骤 s5 · 动作")
        self.assertEqual(labels["inputs[b].required"], "输入字段 b · 是否必需")

    # ------------------------------------------------------------------ AC14 退回重生成
    def test_05_ac14_regenerate_creates_new_ai_version(self):
        skill_id, _ = self._candidate(TASK_B)
        wb = self._wb(skill_id)
        self.assertEqual(wb["regenerate_count"], 0)
        empty = self._action(skill_id, "regenerate", {"revision_token": wb["revision_token"], "comment": "  "})
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(empty.json()["detail"]["code"], "REGENERATE_COMMENT_REQUIRED")
        bad_group = self._action(skill_id, "regenerate", {"revision_token": wb["revision_token"], "comment": "改",
                                                          "field_groups": ["F"]})
        self.assertEqual(bad_group.status_code, 400)

        seen = {}

        def regenerate(payload, messages):
            seen["payload"] = payload
            candidate = self.model.candidate(payload, {"confidence": "中"})
            candidate["not_applies_to"] = ["收缴率下降原因分析属于另一任务", "非住宅项目"]
            return json.dumps(candidate, ensure_ascii=False)

        self.model.regenerate = regenerate
        resp = self._action(skill_id, "regenerate", {"revision_token": wb["revision_token"],
                                                     "comment": "请补充不适用范围", "field_groups": ["A", "E"]})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "generating")
        self.assertEqual(self._wait_status(skill_id), "pending_review")
        self.assertEqual(seen["payload"]["review_comment"], "请补充不适用范围")
        self.assertEqual(seen["payload"]["rewrite_field_groups"], ["A 身份与边界", "E 风险与人工升级"])
        self.assertIn("previous_version", seen["payload"])

        versions = self._versions(skill_id)
        self.assertEqual([v["version_kind"] for v in versions], ["ai_original", "ai_regenerated"])
        wb2 = self._wb(skill_id)
        self.assertEqual(wb2["regenerate_count"], 1)
        self.assertEqual(wb2["current_version_kind"], "ai_regenerated")
        self.assertNotEqual(wb2["revision_token"], resp.json()["revision_token"])  # 新版本入库后令牌轮换
        record = next(r for r in wb2["review_records"] if r["action"] == "regenerate")
        self.assertEqual(record["comment"], "请补充不适用范围")
        self.assertEqual(record["regenerate_groups"], ["A", "E"])
        self.assertEqual(record["to_version_number"], 2)
        self.assertEqual(record["detail"]["state"], "completed")
        # 可比较两个 AI 版本
        cmp = self.client.get(f"/api/skill-factory/skills/{skill_id}/compare",
                              params={"from": versions[0]["id"], "to": versions[1]["id"]},
                              headers=self.admin_headers).json()
        self.assertEqual(cmp["to"]["version_kind"], "ai_regenerated")
        self.assertIn(("not_applies_to", "add"), {(d["field"], d["op"]) for d in cmp["diffs"]})

        # 重生成仍不通过：回到待审核，内容与版本不变，记录失败原因
        def still_bad(payload, messages):
            candidate = self.model.candidate(payload, {"outsider": True})
            return json.dumps(candidate, ensure_ascii=False)

        self.model.regenerate = still_bad
        resp = self._action(skill_id, "regenerate", {"revision_token": wb2["revision_token"], "comment": "再改一次"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._wait_status(skill_id), "pending_review")
        self.assertEqual(len(self._versions(skill_id)), 2)
        wb3 = self._wb(skill_id)
        self.assertEqual(wb3["regenerate_task"]["outcome"], "validation_failed")
        failed = [r for r in wb3["review_records"] if r["action"] == "regenerate"][-1]
        self.assertEqual(failed["detail"]["state"], "failed")
        self.assertIsNone(failed["to_version_id"])

    # ------------------------------------------------------------------ AC15 驳回与恢复
    def test_06_ac15_reject_and_restore(self):
        skill_id, _ = self._candidate(TASK_B)
        token = self._wb(skill_id)["revision_token"]
        no_reason = self._action(skill_id, "reject", {"revision_token": token, "note": "重复"})
        self.assertEqual(no_reason.status_code, 400)
        self.assertEqual(no_reason.json()["detail"]["code"], "REJECT_REASON_REQUIRED")
        bad_reason = self._action(skill_id, "reject", {"revision_token": token, "reason": "不喜欢", "note": "x"})
        self.assertEqual(bad_reason.status_code, 400)
        no_note = self._action(skill_id, "reject", {"revision_token": token, "reason": "与已有 Skill 重复"})
        self.assertEqual(no_note.status_code, 400)
        self.assertEqual(no_note.json()["detail"]["code"], "REJECT_NOTE_REQUIRED")
        resp = self._action(skill_id, "reject", {"revision_token": token, "reason": "与已有 Skill 重复",
                                                 "note": "与上一批的欠费分级重复"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._status(skill_id), "rejected")
        wb = self._wb(skill_id)
        self.assertEqual(wb["allowed_actions"], ["restore"])
        # 驳回后不能通过、退回或再次驳回
        for action, body in (("approve", {"checklist": ALL_CHECKED}), ("reject", {"reason": "其他", "note": "x"}),
                             ("regenerate", {"comment": "x"})):
            r = self._action(skill_id, action, {"revision_token": wb["revision_token"], **body})
            self.assertEqual(r.status_code, 409, (action, r.text))
        resp = self._action(skill_id, "restore", {"revision_token": wb["revision_token"]})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "pending_review")
        records = self._records(skill_id)
        self.assertEqual([r["action"] for r in records], ["reject", "restore"])
        self.assertEqual(records[0]["reject_reason"], "与已有 Skill 重复")
        self.assertEqual(records[0]["comment"], "与上一批的欠费分级重复")
        self.assertEqual(len(self._versions(skill_id)), 1)  # 驳回与恢复不产生版本

    # ------------------------------------------------------------------ AC19 并发
    def test_07_ac19_concurrent_edits_conflict(self):
        skill_id, _ = self._candidate(TASK_A)
        wb_a = self._wb(skill_id, self.admin_headers)
        wb_b = self._wb(skill_id, self.admin2_headers)
        self.assertEqual(wb_a["revision_token"], wb_b["revision_token"])
        content_a = copy.deepcopy(wb_a["content"])
        content_a["goal"] = "会话 A 的修改"
        saved = self.client.put(f"/api/skill-factory/skills/{skill_id}/draft",
                                json={"revision_token": wb_a["revision_token"], "skill_json": content_a},
                                headers=self.admin_headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        token_a = saved.json()["revision_token"]
        self.assertNotEqual(token_a, wb_a["revision_token"])

        content_b = copy.deepcopy(wb_b["content"])
        content_b["goal"] = "会话 B 的修改"
        conflict = self.client.put(f"/api/skill-factory/skills/{skill_id}/draft",
                                   json={"revision_token": wb_b["revision_token"], "skill_json": content_b},
                                   headers=self.admin2_headers)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["code"], "REVISION_CONFLICT")
        # 草稿仍是 A 的内容；草稿不是版本
        wb = self._wb(skill_id)
        self.assertTrue(wb["has_draft"])
        self.assertEqual(wb["content"]["goal"], "会话 A 的修改")
        self.assertEqual(len(self._versions(skill_id)), 1)

        # 缺少令牌
        missing = self.client.put(f"/api/skill-factory/skills/{skill_id}/draft", json={"skill_json": content_b},
                                  headers=self.admin2_headers)
        self.assertEqual(missing.status_code, 400)

        # B 用旧令牌直接提交审核动作同样被拒
        early = self._action(skill_id, "approve", {"revision_token": wb_b["revision_token"],
                                                   "skill_json": self._clean(content_b), "checklist": ALL_CHECKED},
                             self.admin2_headers)
        self.assertEqual(early.status_code, 409)
        self.assertEqual(early.json()["detail"]["code"], "REVISION_CONFLICT")
        self.assertEqual(self._status(skill_id), "pending_review")

        # A 提交通过；B 用旧令牌提交被拒，不覆盖
        clean_a = self._clean(wb["content"])
        ok = self._action(skill_id, "approve", {"revision_token": token_a, "skill_json": clean_a,
                                                "checklist": ALL_CHECKED}, self.admin_headers)
        self.assertEqual(ok.status_code, 200, ok.text)
        late = self._action(skill_id, "approve", {"revision_token": wb_b["revision_token"],
                                                  "skill_json": self._clean(content_b), "checklist": ALL_CHECKED},
                            self.admin2_headers)
        self.assertEqual(late.status_code, 409)
        versions = self._versions(skill_id)
        self.assertEqual(len(versions), 2)
        self.assertEqual(json.loads(versions[1]["skill_json"])["goal"], "会话 A 的修改")

    # ------------------------------------------------------------------ 非法状态流转
    def test_08_illegal_transitions_and_validation_failed_path(self):
        self.model.variants[TASK_A] = {"outsider": True, "confidence": "中"}
        skill_id, _ = self._candidate(TASK_A, status="validation_failed")
        wb = self._wb(skill_id)
        self.assertEqual(wb["allowed_actions"], ["abandon", "regenerate"])
        self.assertTrue(wb["generation_issues"])
        token = wb["revision_token"]
        for action, body in (("approve", {"checklist": ALL_CHECKED}), ("reject", {"reason": "其他", "note": "x"}),
                             ("restore", {})):
            r = self._action(skill_id, action, {"revision_token": token, **body})
            self.assertEqual(r.status_code, 409, (action, r.text))
            self.assertEqual(r.json()["detail"]["code"], "ILLEGAL_TRANSITION")
        draft = self.client.put(f"/api/skill-factory/skills/{skill_id}/draft",
                                json={"revision_token": token, "skill_json": wb["content"]}, headers=self.admin_headers)
        self.assertEqual(draft.status_code, 409)
        unknown = self._action(skill_id, "publish", {"revision_token": token})
        self.assertEqual(unknown.status_code, 404)

        # 放弃 -> 已驳回（无版本）；恢复回到校验未通过
        resp = self._action(skill_id, "abandon", {"revision_token": token})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self._status(skill_id), "rejected")
        resp = self._action(skill_id, "restore", {"revision_token": resp.json()["revision_token"]})
        self.assertEqual(resp.json()["status"], "validation_failed")

        # 校验未通过 -> 重新生成：生成中不能做任何动作；通过后产生第 1 版 AI 原稿并进入待审核
        release = threading.Event()

        def fixed(payload, messages):
            release.wait(10)
            return json.dumps(self.model.candidate(payload, {"confidence": "低"}), ensure_ascii=False)

        self.model.regenerate = fixed
        resp = self._action(skill_id, "regenerate", {"revision_token": resp.json()["revision_token"]})
        self.assertEqual(resp.status_code, 200, resp.text)
        try:
            self.assertEqual(self._status(skill_id), "generating")
            wb = self._wb(skill_id)
            self.assertEqual(wb["allowed_actions"], [])
            for action, body in (("approve", {"checklist": ALL_CHECKED}), ("regenerate", {"comment": "x"}),
                                 ("reject", {"reason": "其他", "note": "x"}), ("abandon", {})):
                r = self._action(skill_id, action, {"revision_token": wb["revision_token"], **body})
                self.assertEqual(r.status_code, 409, (action, r.text))
        finally:
            release.set()
        self.assertEqual(self._wait_status(skill_id), "pending_review", self._wb(skill_id)["regenerate_task"])
        versions = self._versions(skill_id)
        self.assertEqual([(v["version_number"], v["version_kind"]) for v in versions], [(1, "ai_original")])
        comment = next(p for p in self.model.calls if "review_comment" in p)["review_comment"]
        self.assertIn("上次生成未通过程序检查", comment)
        actions = [r["action"] for r in self._records(skill_id)]
        self.assertEqual(actions, ["reject", "restore", "regenerate"])

    # ------------------------------------------------------------------ 导出与版本记录一致
    def test_09_export_matches_version_records(self):
        skill_id, _ = self._candidate(TASK_A)
        wb = self._wb(skill_id)
        content = self._clean(wb["content"])
        content["goal"] = "核对指定期间收缴率并说明差异来源"
        resp = self._action(skill_id, "approve", {"revision_token": wb["revision_token"], "skill_json": content,
                                                  "checklist": ALL_CHECKED, "change_reasons": {"goal": "补充交付结果"}})
        self.assertEqual(resp.status_code, 200, resp.text)
        versions = self._versions(skill_id)
        record = self._records(skill_id)[-1]
        exp = self.client.get(f"/api/skill-factory/skills/{skill_id}/export", params={"format": "json"},
                              headers=self.admin_headers)
        self.assertEqual(exp.status_code, 200, exp.text)
        self.assertIn("attachment", exp.headers["content-disposition"])
        data = exp.json()
        self.assertEqual(data["original"]["skill_json"], json.loads(versions[0]["skill_json"]))
        self.assertEqual(data["final"]["skill_json"], json.loads(versions[1]["skill_json"]))
        record_diffs = json.loads(record["field_diffs_json"])
        self.assertEqual([(d["path"], d["op"], d["before"], d["after"]) for d in data["diffs"]],
                         [(d["path"], d["op"], d["before"], d["after"]) for d in record_diffs])
        self.assertEqual(next(d for d in data["diffs"] if d["path"] == "goal")["reason"], "补充交付结果")
        # 无依据项：步骤标为专家补充，数值随之不再报
        self.assertEqual(data["summary"]["unsupported_resolutions"], {"expert": 2})
        self.assertEqual([r["action"] for r in data["review_records"]], ["approve_with_changes"])

        md = self.client.get(f"/api/skill-factory/skills/{skill_id}/export", params={"format": "markdown"},
                             headers=self.admin_headers)
        self.assertEqual(md.status_code, 200)
        text = md.content.decode("utf-8")
        self.assertIn("## 差异清单", text)
        self.assertIn("`goal`", text)
        self.assertIn("补充交付结果", text)
        self.assertIn("## 原稿", text)
        self.assertIn("## 终稿", text)
        self.assertIn(json.dumps(json.loads(versions[1]["skill_json"]), ensure_ascii=False, indent=2), text)
        bad = self.client.get(f"/api/skill-factory/skills/{skill_id}/export", params={"format": "docx"},
                              headers=self.admin_headers)
        self.assertEqual(bad.status_code, 400)

        # 待沉淀经验清单（S13）
        exp_list = self.client.get("/api/skill-factory/experiences", headers=self.admin_headers).json()
        mine = [e for e in exp_list["items"] if e["skill_id"] == skill_id]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["content"], "30 分钟内回访确认处置结果")
        self.assertEqual(mine[0]["reason"], "项目惯例：处置后 30 分钟内回访")
        self.assertEqual(mine[0]["version_number"], 2)

    # ------------------------------------------------------------------ FR09 候选列表
    def test_10_candidate_list_filters_and_order(self):
        self.model.variants[TASK_A] = {"tbd": True, "unsupported": True, "confidence": "低"}
        low_id, batch = self._candidate(TASK_A)
        high_id = next(c["skill_id"] for c in batch["candidates"] if c["name"] == TASK_B)
        with get_db() as conn:  # 构造一条「待复核」（M02-E 才会自动产生），只用于排序检查
            conn.execute("UPDATE skills SET status = 'needs_recheck' WHERE id = ?", (high_id,))
        try:
            listing = self.client.get("/api/skill-factory/skills", headers=self.admin_headers).json()
            items = listing["items"]
            self.assertEqual(items[0]["status"], "needs_recheck")
            ranks = [{"needs_recheck": 0, "pending_review": 1, "generating": 2, "validation_failed": 3,
                      "approved": 4, "rejected": 5}[i["status"]] for i in items]
            self.assertEqual(ranks, sorted(ranks))
            pending = [i for i in items if i["status"] == "pending_review"]
            order = [{"低": 0, "中": 1, "高": 2}[i["confidence_level"]] for i in pending]
            self.assertEqual(order, sorted(order))
            self.assertEqual(pending[0]["skill_id"], low_id)
            row = next(i for i in items if i["skill_id"] == low_id)
            for field in ("name", "scene_name", "status_label", "confidence_level", "unsupported_count",
                          "ref_count", "version_number", "batch_id", "updated_at", "similar_skills", "atom_changed"):
                self.assertIn(field, row)
            self.assertEqual(row["unsupported_count"], 2)
            self.assertEqual(row["ref_count"], 3)

            def ids(**params):
                return {i["skill_id"] for i in self.client.get("/api/skill-factory/skills", params=params,
                                                                headers=self.admin_headers).json()["items"]}

            self.assertEqual(ids(batch_id=batch["batch_id"]), {low_id, high_id})
            self.assertEqual(ids(needs_recheck="true"), {high_id})
            self.assertIn(low_id, ids(has_unsupported="yes"))
            self.assertNotIn(low_id, ids(has_unsupported="no"))
            self.assertTrue(all(i["status"] == "pending_review" for i in self.client.get(
                "/api/skill-factory/skills", params={"status": "pending_review"}, headers=self.admin_headers).json()["items"]))
            self.assertIn(low_id, ids(scene_id=self.scene["scene_id"]))
            self.assertEqual(ids(scene_id="scene_not_exist"), set())
            self.assertTrue(any(b["batch_id"] == batch["batch_id"] for b in listing["filters"]["batches"]))
        finally:
            with get_db() as conn:
                conn.execute("UPDATE skills SET status = 'pending_review' WHERE id = ?", (high_id,))

    # ------------------------------------------------------------------ FR11 引用调整与步骤编号
    def test_11_reference_editing_requires_r1_and_step_ids_not_reused(self):
        skill_id, batch = self._candidate(TASK_B)
        wb = self._wb(skill_id)
        # 左栏：语义找到的原子带标记；本批次原子池可供选择
        repair = next(a for a in wb["atoms"] if a["atom_version_id"] == self.atoms["repair"]["version_id"])
        self.assertEqual(repair["recall_source"], "semantic")
        self.assertTrue(any(a["atom_version_id"] == self.atoms["rate"]["version_id"] for a in wb["batch_pool"]))
        grade = next(a for a in wb["atoms"] if a["atom_version_id"] == self.atoms["grade"]["version_id"])
        self.assertEqual(grade["exceptions_not_landed"], [])

        # 从全库新增（不在本批次原子池）的「回访时限要求」：满足 R1 即可引用
        visit = self.atoms["visit"]
        content = copy.deepcopy(wb["content"])
        new_sid = f"s{wb['next_step_number']}"
        content["steps"].append({"step_id": new_sid, "kind": "规则判断", "action": "处置完成后30分钟内回访业主",
                                 "refs": [visit["version_id"]], "basis": "有原子依据", "on_fail": "转人工"})
        content["knowledge_refs"].append({"atom_item_id": visit["item_id"], "atom_version_id": visit["version_id"],
                                          "role": "判断规则", "used_in_steps": []})
        check = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                                 json={"skill_json": content, "checklist": ALL_CHECKED},
                                 headers=self.admin_headers).json()
        self.assertTrue(check["evaluation"]["can_approve"], check["evaluation"]["blockers"])
        # 使用步骤由系统按步骤引用维护
        added = next(r for r in check["content"]["knowledge_refs"] if r["atom_version_id"] == visit["version_id"])
        self.assertEqual(added["used_in_steps"], [new_sid])
        new_atom = next(a for a in check["atoms"] if a["atom_version_id"] == visit["version_id"])
        self.assertFalse(new_atom["in_pool"])

        # 停用的知识不能新增引用
        self._set_lifecycle("visit", "disabled")
        try:
            check = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                                     json={"skill_json": content, "checklist": ALL_CHECKED},
                                     headers=self.admin_headers).json()["evaluation"]
            self.assertIn("REF_NOT_ELIGIBLE", {b["code"] for b in check["blockers"]})
        finally:
            self._set_lifecycle("visit", "active")

        # 删除引用后步骤仍引用它：结构复验不通过
        broken = copy.deepcopy(content)
        broken["knowledge_refs"] = [r for r in broken["knowledge_refs"] if r["atom_version_id"] != visit["version_id"]]
        check = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                                 json={"skill_json": broken, "checklist": ALL_CHECKED},
                                 headers=self.admin_headers).json()["evaluation"]
        self.assertIn("STRUCTURE", {b["code"] for b in check["blockers"]})

        # 通过后引用快照写入新版本
        resp = self._action(skill_id, "approve", {"revision_token": wb["revision_token"], "skill_json": content,
                                                  "checklist": ALL_CHECKED})
        self.assertEqual(resp.status_code, 200, resp.text)
        with get_db() as conn:
            snap = conn.execute("SELECT * FROM skill_atom_refs WHERE skill_version_id = ? AND atom_version_id = ?",
                                (resp.json()["version_id"], visit["version_id"])).fetchone()
        self.assertEqual(snap["snapshot_title"], "回访时限要求")

        # 步骤编号不复用：v1 有 s5，v2 删去 s5 后，新增步骤不能再用 s5
        skill_id2, _ = self._candidate(TASK_A)
        v1 = self._versions(skill_id2)[0]
        with get_db() as conn:  # 构造「后续版本删去 s5」：直接写一个 AI 重生成版（只用于编号规则检查）
            data = json.loads(v1["skill_json"])
            data["steps"] = [s for s in data["steps"] if s["step_id"] != "s5"]
            data["version_number"] = 2
            vid = f"skv_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT INTO skill_versions (id, skill_id, organization_id, version_number, schema_version, skill_json,
                       version_kind, based_on_version_id, revision_token, created_by, created_at)
                   VALUES (?, ?, ?, 2, '1.0', ?, 'ai_regenerated', ?, ?, ?, ?)""",
                (vid, skill_id2, self.org_id, json.dumps(data, ensure_ascii=False), v1["id"], uuid.uuid4().hex,
                 self.admin_user["id"], datetime.now(timezone.utc).isoformat()),
            )
            conn.execute("UPDATE skills SET current_version_id = ? WHERE id = ?", (vid, skill_id2))
        wb2 = self._wb(skill_id2)
        self.assertEqual(wb2["next_step_number"], 6)
        content2 = self._clean(wb2["content"])
        content2["steps"].append({"step_id": "s5", "kind": "人工确认", "action": "主管确认", "refs": [],
                                  "basis": "通用操作", "on_fail": "暂停"})
        check = self.client.post(f"/api/skill-factory/skills/{skill_id2}/review/check",
                                 json={"skill_json": content2, "checklist": ALL_CHECKED},
                                 headers=self.admin_headers).json()["evaluation"]
        self.assertIn("STEP_ID_REUSED", {b["code"] for b in check["blockers"]})

    # ------------------------------------------------------------------ 无依据项：专家补充须填理由
    def test_12_unsupported_number_expert_resolution(self):
        self.model.variants[TASK_A] = {"tbd": False, "unsupported": True, "confidence": "中"}
        skill_id, _ = self._candidate(TASK_A)
        wb = self._wb(skill_id)
        items = {i["kind"]: i for i in wb["evaluation"]["unsupported_items"]}
        number = items["疑似无依据数值"]
        step_item = items["无依据步骤"]
        content = copy.deepcopy(wb["content"])
        # 面板里把无依据步骤标为专家补充不算处理：步骤依据必须改为专家补充（R3）
        resolutions = {step_item["item_key"]: {"action": "expert", "reason": "惯例"},
                       number["item_key"]: {"action": "expert", "reason": ""}}
        ev = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                              json={"skill_json": content, "resolutions": resolutions, "checklist": ALL_CHECKED},
                              headers=self.admin_headers).json()["evaluation"]
        blocker = next(b for b in ev["blockers"] if b["code"] == "UNSUPPORTED_UNRESOLVED")
        self.assertEqual(set(blocker["item_keys"]), {step_item["item_key"], number["item_key"]})
        # 补依据：引用含「30」的知识，数值不再报；步骤依据改为有原子依据
        visit = self.atoms["visit"]
        sid = step_item["item_key"].split(":", 1)[1]
        for step in content["steps"]:
            if step["step_id"] == sid:
                step.update({"refs": [visit["version_id"]], "basis": "有原子依据", "kind": "规则判断"})
        content["knowledge_refs"].append({"atom_item_id": visit["item_id"], "atom_version_id": visit["version_id"],
                                          "role": "判断规则", "used_in_steps": []})
        ev = self.client.post(f"/api/skill-factory/skills/{skill_id}/review/check",
                              json={"skill_json": content, "checklist": ALL_CHECKED},
                              headers=self.admin_headers).json()["evaluation"]
        self.assertTrue(ev["can_approve"], ev["blockers"])
        self.assertEqual(ev["summary"]["unsupported_resolutions"], {"add_ref": 2})

    # ------------------------------------------------------------------ 鉴权与企业隔离
    def test_13_member_and_other_org_rejected(self):
        skill_id, _ = self._candidate(TASK_B)
        token = self._wb(skill_id)["revision_token"]
        for headers, code in ((self.member_headers, 403), (self.other_headers, 404), ({}, 401)):
            self.assertEqual(self.client.get(f"/api/skill-factory/skills/{skill_id}/workbench",
                                             headers=headers).status_code, code)
            self.assertEqual(self._action(skill_id, "reject", {"revision_token": token, "reason": "其他", "note": "x"},
                                          headers).status_code, code)
            self.assertEqual(self.client.get(f"/api/skill-factory/skills/{skill_id}/export",
                                             headers=headers).status_code, code)
        self.assertEqual(self.client.get("/api/skill-factory/skills", headers=self.member_headers).status_code, 403)
        other = self.client.get("/api/skill-factory/skills", headers=self.other_headers).json()
        self.assertNotIn(skill_id, {i["skill_id"] for i in other["items"]})
        self.assertEqual(self._status(skill_id), "pending_review")

    # ------------------------------------------------------------------ 操作记录与自动跳转
    def test_14_audit_and_next_candidate(self):
        first, batch = self._candidate(TASK_B)
        other = next(c["skill_id"] for c in batch["candidates"] if c["skill_id"] != first)
        wb = self._wb(first)
        resp = self._action(first, "reject", {"revision_token": wb["revision_token"], "reason": "任务不成立",
                                              "note": "测试", "queue": [first, other]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["next_skill_id"], other)
        with get_db() as conn:
            logs = conn.execute(
                "SELECT action, user_id FROM audit_logs WHERE target_id = ? ORDER BY created_at", (first,)).fetchall()
        self.assertIn(("skill_review_reject", self.admin_user["id"]), [(l["action"], l["user_id"]) for l in logs])
        # 审核动作不调用模型
        calls_before = len(self.model.calls)
        self._action(first, "restore", {"revision_token": resp.json()["revision_token"]})
        self.assertEqual(len(self.model.calls), calls_before)


if __name__ == "__main__":
    unittest.main()
