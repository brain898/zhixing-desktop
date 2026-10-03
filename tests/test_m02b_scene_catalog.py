"""
M02-B 自动化测试：场景目录（FR01~FR03）、M01 衔接（M01-C1~C3）与原子召回（FR04）

依据 0926-Skill工厂模块PRD：AC01~AC04、AC06、AC21 中与场景目录相关的部分。
- 全部在隔离的临时数据库中运行（test_env_helper.setup_test_db），不读写 server/data/zhixing.db。
- 归并建议与抽取的模型返回在本文件中一律为「模拟返回」，只用于边界与异常测试；
  真实模型调用另见 tests/run_m02b_live_merge_suggestion.py。
- 语义召回使用真实 BGE 向量索引（与 Stage 4B 测试相同的确认 -> 建索引流程）。
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
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from test_env_helper import cleanup_test_db, setup_test_db  # noqa: E402

import config  # noqa: E402
import deepseek_extractor as de  # noqa: E402
import scene_catalog as sc  # noqa: E402
from database import get_db  # noqa: E402
from main import app  # noqa: E402
from tasks import execute_extract_task, recover_interrupted_tasks, wait_for_background_tasks  # noqa: E402


def _valid_merge_json(groups, unassigned=None):
    return json.dumps({"groups": groups, "unassigned_tags": unassigned or []}, ensure_ascii=False)


# 模拟返回：第一次引用了输入中不存在的标签，第二次合规
MOCK_INVALID = _valid_merge_json([
    {"name": "应急处置", "description": "设施设备突发故障的应急处置", "typical_problems": ["设备突发故障"],
     "tags": ["应急抢险", "不存在的标签"], "reason": "同属应急"},
])
MOCK_VALID = _valid_merge_json([
    {"name": "设施设备应急处置", "description": "设施设备突发故障与抢修的应急处置",
     "typical_problems": ["设备突发故障如何处置"], "tags": ["应急抢险", "应急抢修", "急修处置"],
     "reason": "均为设施设备突发故障的应急处置"},
    {"name": "防汛与工程查验", "description": "防汛准备与工程查验",
     "typical_problems": ["汛前检查项目有哪些"], "tags": ["防汛管理", "工程查验", "承接查验"],
     "reason": "测试用：故意把两个领域合并，供管理员拆开"},
])


class TestM02BSceneCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db = setup_test_db("m02b_scene_catalog")
        cls.client = TestClient(app)
        cls.admin_headers, cls.admin_user = cls._login("admin", "Admin@Zhixing2026")
        cls.member_headers, _ = cls._login("member", "Member@Zhixing2026")
        cls.other_headers, cls.other_user = cls._login("other_admin", "Other@Zhixing2026")
        cls.org_id = cls.admin_user["organization_id"]
        cls.prefix = "m02b_" + uuid.uuid4().hex[:6]
        cls.atoms = {}
        cls._build_fixture()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_db(cls.test_db)

    # ------------------------------------------------------------------ helpers
    @classmethod
    def _login(cls, username, password):
        resp = cls.client.post("/api/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['token']}"}, resp.json()["user"]

    @classmethod
    def _create_document(cls, title, org_id=None, uploader=None):
        org_id = org_id or cls.org_id
        uploader = uploader or cls.admin_user["id"]
        now = datetime.now(timezone.utc).isoformat()
        doc_id = f"doc_{cls.prefix}_{uuid.uuid4().hex[:6]}"
        ver_id = f"ver_{cls.prefix}_{uuid.uuid4().hex[:6]}"
        texts = [
            "汛期前应完成排水设施检查并记录结果。",
            "物业工程人员应在汛前逐一检查排污泵运行状态。",
            "暴雨预警期间不得安排人员进入地下管井作业。",
            "地下室积水深度超过警戒线时立即启动排水预案。",
        ]
        block_ids = []
        with get_db() as conn:
            conn.execute(
                """INSERT INTO documents (id, organization_id, title, active_version_id, access_scope,
                   is_deleted, created_at, updated_at) VALUES (?, ?, ?, NULL, 'org_internal', 0, ?, ?)""",
                (doc_id, org_id, title, now, now),
            )
            conn.execute(
                """INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name,
                   file_type, file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                   VALUES (?, ?, ?, 'v1', ?, 'txt', 512, ?, ?, ?, ?, 'completed')""",
                (ver_id, doc_id, org_id, title + ".txt", uuid.uuid4().hex, f"/tmp/{ver_id}.txt", uploader, now),
            )
            for i, text in enumerate(texts, start=1):
                block_id = f"blk_{cls.prefix}_{uuid.uuid4().hex[:6]}"
                block_ids.append(block_id)
                conn.execute(
                    """INSERT INTO source_blocks (id, document_version_id, organization_id, block_index, block_type,
                       heading_path, page_number, paragraph_anchor, text_content, created_at)
                       VALUES (?, ?, ?, ?, 'paragraph', '测试章节', NULL, ?, ?, ?)""",
                    (block_id, ver_id, org_id, i, f"[line_{i}]", text, now),
                )
        return doc_id, ver_id, block_ids

    @classmethod
    def _create_atom(cls, doc, title, statement, scenes, category="制度与标准", org_id=None, creator=None):
        org_id = org_id or cls.org_id
        creator = creator or cls.admin_user["id"]
        doc_id, ver_id, block_ids = doc
        now = datetime.now(timezone.utc).isoformat()
        item_id = f"ki_{cls.prefix}_{uuid.uuid4().hex[:6]}"
        version_id = f"kv_{cls.prefix}_{uuid.uuid4().hex[:6]}"
        with get_db() as conn:
            conn.execute(
                """INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope,
                   lifecycle_status, is_excluded, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'org_internal', 'active', 0, ?, ?)""",
                (item_id, doc_id, org_id, version_id, now, now),
            )
            conn.execute(
                """INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id,
                   version_number, title, content, primary_category, atom_type, subject, statement,
                   conditions_json, actions_json, exceptions_json, customer_types_json, business_scenes_json,
                   problem_tags_json, source_anchors_json, review_status, index_status, revision_token,
                   created_at, created_by)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, '规则', '物业工程人员', ?, '[]', ?, '[]', '["住宅业主"]', ?,
                           '[]', '["[line_1]"]', 'pending_review', 'not_indexed', ?, ?, ?)""",
                (
                    version_id, item_id, org_id, ver_id, title, statement, category, statement,
                    json.dumps([statement], ensure_ascii=False),
                    json.dumps(scenes, ensure_ascii=False), uuid.uuid4().hex, now, creator,
                ),
            )
            conn.execute(
                """INSERT INTO knowledge_evidence (id, knowledge_version_id, source_block_id, organization_id,
                   field_name, excerpt, accuracy_level, created_at) VALUES (?, ?, ?, ?, 'statement', ?, 'exact', ?)""",
                (f"ev_{uuid.uuid4().hex[:10]}", version_id, block_ids[0], org_id, "汛期前应完成排水设施检查", now),
            )
        return item_id, version_id

    @classmethod
    def _confirm(cls, item_id, headers=None):
        headers = headers or cls.admin_headers
        detail = cls.client.get(f"/api/knowledge/items/{item_id}", headers=headers).json()
        resp = cls.client.post(
            f"/api/knowledge/items/{item_id}/confirm",
            json={"revision_token": detail["active_version"]["revision_token"]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    @classmethod
    def _wait_ready(cls, version_ids, timeout=120.0):
        deadline = time.time() + timeout
        pending = set(version_ids)
        while pending and time.time() < deadline:
            with get_db() as conn:
                for vid in list(pending):
                    row = conn.execute("SELECT index_status FROM knowledge_versions WHERE id = ?", (vid,)).fetchone()
                    if row["index_status"] == "ready":
                        pending.discard(vid)
                    elif row["index_status"] == "failed":
                        raise AssertionError(f"索引失败 {vid}")
            time.sleep(0.1)
        assert not pending, f"索引等待超时 {pending}"

    @classmethod
    def _build_fixture(cls):
        doc = cls._create_document("防汛与应急测试规程")
        cls.doc = doc
        specs = {
            "flood_1": ("汛前排水设施检查", "汛期前物业工程人员应完成排水设施检查并记录。", ["防汛管理"], "制度与标准"),
            "flood_2": ("汛前排污泵运行检查", "汛前应逐一检查排污泵运行状态与止回装置。", ["防汛管理", "工程查验"], "方法与工具"),
            "flood_3": ("地下室积水警戒处置", "地下室积水超过警戒线时立即启动排水预案。", ["防汛管理"], "指标数据"),
            "emerg_1": ("设备突发故障应急抢险", "设备突发故障时应立即组织应急抢险并隔离现场。", ["应急抢险"], "制度与标准"),
            "emerg_2": ("供水管道爆裂应急抢修", "供水管道爆裂时应关闭上游阀门并组织抢修。", ["应急抢修"], "方法与工具"),
            "check_1": ("承接查验资料移交", "承接查验时应核对竣工资料与设备清单。", ["承接查验"], "制度与标准"),
            "urgent_1": ("入户急修响应", "业主报修漏水时维修人员应尽快上门处置。", ["急修处置"], "方法与工具"),
            # 不满足 R1 的原子：同样带「防汛管理」标签，不得出现在统计与召回中
            "bad_unconfirmed": ("未确认的防汛原子", "未确认原子不得进入召回。", ["防汛管理"], "制度与标准"),
            "bad_disabled": ("已停用的防汛原子", "停用原子不得进入召回。", ["防汛管理", "停用专属标签"], "制度与标准"),
            "bad_excluded": ("已排除的防汛原子", "排除原子不得进入召回。", ["防汛管理", "排除专属标签"], "制度与标准"),
            "bad_expired": ("已过期的防汛原子", "过期原子不得进入召回。", ["防汛管理", "过期专属标签"], "制度与标准"),
        }
        for key, (title, statement, scenes, cat) in specs.items():
            cls.atoms[key] = cls._create_atom(doc, title, statement, scenes, cat)
        to_confirm = [k for k in specs if k != "bad_unconfirmed"]
        for key in to_confirm:
            cls._confirm(cls.atoms[key][0])
        cls._wait_ready([cls.atoms[k][1] for k in to_confirm])

        # 构造不满足 R1 的原子
        disabled_item = cls.atoms["bad_disabled"][0]
        detail = cls.client.get(f"/api/knowledge/items/{disabled_item}", headers=cls.admin_headers).json()
        resp = cls.client.put(
            f"/api/knowledge/items/{disabled_item}/lifecycle",
            json={"lifecycle_status": "disabled", "revision_token": detail["active_version"]["revision_token"]},
            headers=cls.admin_headers,
        )
        if resp.status_code != 200:
            with get_db() as conn:
                conn.execute("UPDATE knowledge_items SET lifecycle_status = 'disabled' WHERE id = ?", (disabled_item,))
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with get_db() as conn:
            conn.execute(
                "UPDATE knowledge_items SET is_excluded = 1, excluded_at = ?, excluded_by = 'test' WHERE id = ?",
                (past, cls.atoms["bad_excluded"][0]),
            )
            conn.execute("UPDATE knowledge_versions SET valid_until = ? WHERE id = ?",
                         (past, cls.atoms["bad_expired"][1]))

        # 其他企业的原子：用于企业隔离
        other_doc = cls._create_document("其他企业资料", org_id=cls.other_user["organization_id"],
                                         uploader=cls.other_user["id"])
        cls.other_atom = cls._create_atom(other_doc, "其他企业防汛原子", "其他企业汛期排水检查要求。", ["防汛管理"],
                                          org_id=cls.other_user["organization_id"], creator=cls.other_user["id"])
        cls._confirm(cls.other_atom[0], headers=cls.other_headers)
        cls._wait_ready([cls.other_atom[1]])

    def _atom_snapshot(self):
        with get_db() as conn:
            versions = conn.execute(
                """SELECT id, item_id, version_number, business_scenes_json, revision_token, review_status, index_status
                   FROM knowledge_versions ORDER BY id"""
            ).fetchall()
            items = conn.execute(
                "SELECT id, active_version_id, pending_version_id, lifecycle_status, updated_at FROM knowledge_items ORDER BY id"
            ).fetchall()
        return [tuple(r) for r in versions], [tuple(r) for r in items]

    def _wait_suggestion(self, suggestion_id, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = self.client.get(f"/api/skill-factory/scene-catalog/suggestions/{suggestion_id}",
                                   headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200, resp.text)
            if resp.json()["status"] not in ("queued", "running"):
                return resp.json()
            time.sleep(0.05)
        self.fail("归并建议等待超时")

    def _run_fake_extraction(self, candidate_scenes):
        """用模拟抽取返回执行 M01 抽取任务，返回 (抽取调用参数列表, 新建原子版本 ID 列表)。"""
        doc_id, ver_id, block_ids = self._create_document("抽取衔接测试资料")
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            conn.execute(
                """INSERT INTO processing_tasks (id, organization_id, target_type, target_id, task_type, status,
                   attempt_count, created_at) VALUES (?, ?, 'document_version', ?, 'extract_atoms', 'queued', 0, ?)""",
                (task_id, self.org_id, ver_id, now),
            )
        calls = []

        def fake_extract(source_blocks, document_title, **kwargs):
            calls.append(kwargs)
            return [{
                "title": "地下室积水巡查要求",
                "primary_category": "制度与标准",
                "atom_type": "规则",
                "subject": "物业工程人员",
                "statement": "汛期前物业工程人员应完成排水设施检查并记录结果。",
                "conditions": [], "actions": ["检查排水设施", "记录检查结果"], "exceptions": [],
                "source_evidence": [{"field_name": "statement", "source_block_id": block_ids[0],
                                     "excerpt": "汛期前应完成排水设施检查并记录结果。"}],
                "field_states": {},
                "customer_types": [], "business_scenes": candidate_scenes, "problem_tags": [],
            }], {"provider": "mock"}

        with patch.object(config, "DEEPSEEK_API_KEY", "mock-key-for-test"), \
                patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_extract):
            execute_extract_task(task_id)
        with get_db() as conn:
            task = conn.execute("SELECT status, error_message FROM processing_tasks WHERE id = ?", (task_id,)).fetchone()
            self.assertEqual(task["status"], "completed", task["error_message"])
            rows = conn.execute(
                "SELECT id, item_id, business_scenes_json FROM knowledge_versions WHERE source_document_version_id = ?",
                (ver_id,),
            ).fetchall()
        return calls, rows, (doc_id, ver_id, block_ids)

    # ------------------------------------------------------------------ tests
    def test_01_ac01_empty_catalog(self):
        """AC01：无场景目录时入口返回 has_catalog=False，没有卡片。"""
        resp = self.client.get("/api/skill-factory/scene-catalog", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(resp.json()["has_catalog"])
        self.assertEqual(resp.json()["scenes"], [])
        cards = self.client.get("/api/skill-factory/scenes/cards", headers=self.admin_headers)
        self.assertEqual(cards.status_code, 200, cards.text)
        self.assertFalse(cards.json()["has_catalog"])
        self.assertEqual(cards.json()["cards"], [])

    def test_02_tag_stats_only_eligible_atoms(self):
        """FR01.1 / R1：标签统计只包含满足资格的原子。"""
        resp = self.client.get("/api/skill-factory/scene-catalog/tag-stats", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        stats = {e["tag"]: e for e in resp.json()["tags"]}
        self.assertEqual(stats["防汛管理"]["count"], 3)
        for tag in ("应急抢险", "应急抢修", "急修处置", "工程查验", "承接查验"):
            self.assertIn(tag, stats)
        for tag in ("停用专属标签", "排除专属标签", "过期专属标签"):
            self.assertNotIn(tag, stats)
        listed_versions = {a["version_id"] for e in stats.values() for a in e["atoms"]}
        for key in ("bad_unconfirmed", "bad_disabled", "bad_excluded", "bad_expired"):
            self.assertNotIn(self.atoms[key][1], listed_versions)
        self.assertNotIn(self.other_atom[1], listed_versions)

    def test_03_c1_no_catalog_extraction_unchanged(self):
        """无场景目录时：抽取不附加目录、批次键不变、不产生待归并标签。"""
        calls, rows, _ = self._run_fake_extraction(["地下室积水巡查"])
        self.assertTrue(calls)
        for kwargs in calls:
            self.assertNotIn("scene_catalog", kwargs)
        self.assertEqual(de.build_scene_catalog_hint([]), "")
        self.assertIsNone(de.scene_catalog_digest([]))
        batch = {"section_path": "x", "chunk_index": 1, "blocks": [{"id": "b1", "text_content": "t"}]}
        base = de.compute_batch_key("o", "v", batch, "t", "m", batch_context="c")
        self.assertEqual(base, de.compute_batch_key("o", "v", batch, "t", "m", batch_context="c",
                                                    scene_catalog_hash=None))
        with get_db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM scene_pending_tags").fetchone()[0]
        self.assertEqual(n, 0)
        self.assertEqual(json.loads(rows[0]["business_scenes_json"]), ["地下室积水巡查"])

    def test_04_merge_response_validation_edges(self):
        """归并建议 JSON 校验边界（模拟返回）。"""
        stats = [{"tag": t, "count": 1, "atoms": []} for t in ("应急抢险", "应急抢修", "防汛管理")]
        groups, unassigned, errors = sc.validate_merge_response("not json", stats)
        self.assertTrue(errors)
        _, _, errors = sc.validate_merge_response(json.dumps({"foo": 1}), stats)
        self.assertIn("返回缺少 groups 数组", errors)
        dup = _valid_merge_json([
            {"name": "A", "description": "d", "typical_problems": [], "tags": ["应急抢险"], "reason": "r"},
            {"name": "B", "description": "d", "typical_problems": [], "tags": ["应急抢险"], "reason": "r"},
        ])
        _, _, errors = sc.validate_merge_response(dup, stats)
        self.assertTrue(any("同时出现" in e for e in errors))
        dup_name = _valid_merge_json([
            {"name": "A", "description": "d", "typical_problems": [], "tags": ["应急抢险"], "reason": "r"},
            {"name": " A ", "description": "d", "typical_problems": [], "tags": ["应急抢修"], "reason": "r"},
        ])
        _, _, errors = sc.validate_merge_response(dup_name, stats)
        self.assertTrue(any("重复" in e for e in errors))
        missing = _valid_merge_json([{"name": "A", "tags": ["应急抢险"]}])
        _, _, errors = sc.validate_merge_response(missing, stats)
        self.assertTrue(any("description" in e for e in errors))
        self.assertTrue(any("reason" in e for e in errors))
        ok = _valid_merge_json([
            {"name": "应急", "description": "d", "typical_problems": ["p"], "tags": ["应急 抢险", "应急抢修"], "reason": "r"},
        ])
        groups, unassigned, errors = sc.validate_merge_response(ok, stats)
        self.assertEqual(errors, [])
        self.assertEqual(groups[0]["tags"], ["应急抢险", "应急抢修"])
        self.assertEqual(unassigned, ["防汛管理"])

    def test_05_merge_suggestion_failure_keeps_catalog(self):
        """模型返回始终不合规或调用失败：建议标记失败，目录不受影响，可再次发起。"""
        with patch.object(config, "DEEPSEEK_API_KEY", "mock-key-for-test"), \
                patch("scene_catalog.post_chat_completion", return_value=(MOCK_INVALID, "mock-model")) as mocked:
            resp = self.client.post("/api/skill-factory/scene-catalog/suggestions", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200, resp.text)
            result = self._wait_suggestion(resp.json()["suggestion_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(mocked.call_count, sc.MERGE_MAX_ATTEMPTS)
        self.assertEqual(result["attempt_count"], sc.MERGE_MAX_ATTEMPTS)
        self.assertIn("未通过校验", result["error_message"])
        # 重试时带上错误清单
        retry_messages = mocked.call_args_list[1][0][0]
        self.assertIn("未通过校验", retry_messages[-1]["content"])

        with patch.object(config, "DEEPSEEK_API_KEY", "mock-key-for-test"), \
                patch("scene_catalog.post_chat_completion", side_effect=RuntimeError("network down")):
            resp = self.client.post("/api/skill-factory/scene-catalog/suggestions", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200, resp.text)
            result = self._wait_suggestion(resp.json()["suggestion_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("模型调用失败", result["error_message"])

        with patch.object(config, "DEEPSEEK_API_KEY", ""):
            resp = self.client.post("/api/skill-factory/scene-catalog/suggestions", headers=self.admin_headers)
        self.assertEqual(resp.status_code, 503)

        catalog = self.client.get("/api/skill-factory/scene-catalog", headers=self.admin_headers).json()
        self.assertFalse(catalog["has_catalog"])

    def test_06_ac02_merge_confirm_with_edits(self):
        """AC02：模型给出建议 -> 管理员改名、拆开、合并后确认 -> 原标签成为别名；原子数据与版本数不变。"""
        before = self._atom_snapshot()
        with patch.object(config, "DEEPSEEK_API_KEY", "mock-key-for-test"), \
                patch("scene_catalog.post_chat_completion",
                      side_effect=[(MOCK_INVALID, "mock-model"), (MOCK_VALID, "mock-model")]) as mocked:
            resp = self.client.post("/api/skill-factory/scene-catalog/suggestions", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200, resp.text)
            suggestion = self._wait_suggestion(resp.json()["suggestion_id"])
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(suggestion["status"], "completed")
        self.assertEqual(suggestion["prompt_version"], sc.SCENE_MERGE_PROMPT_VERSION)
        self.assertEqual(len(suggestion["groups"]), 2)
        self.assertEqual(suggestion["attempts"][0]["ok"], False)
        self.assertEqual(suggestion["attempts"][1]["ok"], True)
        self.assertNotIn("raw_response", suggestion["attempts"][0])

        # 进行中/已完成建议不影响目录：此时目录仍为空
        self.assertFalse(self.client.get("/api/skill-factory/scene-catalog",
                                         headers=self.admin_headers).json()["has_catalog"])

        # 同一标签归入两组被拒
        bad = self.client.post(
            f"/api/skill-factory/scene-catalog/suggestions/{suggestion['suggestion_id']}/confirm",
            json={"groups": [
                {"name": "甲", "tags": ["应急抢险"]}, {"name": "乙", "tags": ["应急抢险"]},
            ]},
            headers=self.admin_headers,
        )
        self.assertEqual(bad.status_code, 400)

        # 管理员修改：改名「设施设备应急处置」为「设备应急处置」，把急修处置拆出与…（留作待归并），
        # 把「防汛与工程查验」拆成两组
        confirmed = self.client.post(
            f"/api/skill-factory/scene-catalog/suggestions/{suggestion['suggestion_id']}/confirm",
            json={"groups": [
                {"name": "设备应急处置", "description": "设施设备突发故障与抢修",
                 "typical_problems": ["设备突发故障如何处置"], "tags": ["应急抢险", "应急抢修"]},
                {"name": "防汛管理", "description": "汛前检查、排水与积水处置",
                 "typical_problems": ["汛前检查项目有哪些", "地下室积水怎么处理"], "tags": ["防汛管理"]},
                {"name": "工程查验", "description": "工程查验与承接查验",
                 "typical_problems": ["承接查验要核对哪些资料"], "tags": ["工程查验", "承接查验"]},
            ]},
            headers=self.admin_headers,
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        body = confirmed.json()
        self.assertEqual(len(body["scenes"]), 3)
        self.assertEqual(body["pending_tags"], ["急修处置"])
        after = self._atom_snapshot()
        self.assertEqual(before, after, "初始化场景目录不得修改原子或产生新版本")

        catalog = self.client.get("/api/skill-factory/scene-catalog", headers=self.admin_headers).json()
        self.assertTrue(catalog["has_catalog"])
        by_name = {s["name"]: s for s in catalog["scenes"]}
        self.assertEqual(by_name["设备应急处置"]["aliases"], ["应急抢险", "应急抢修"])
        self.assertEqual(by_name["工程查验"]["aliases"], ["工程查验", "承接查验"])
        self.assertEqual(catalog["pending_tag_count"], 1)
        pending = self.client.get("/api/skill-factory/scene-catalog/pending-tags", headers=self.admin_headers).json()
        self.assertEqual(pending["items"][0]["tag"], "急修处置")
        self.assertEqual(pending["items"][0]["source_atoms"][0]["source"], "catalog_init")
        self.assertEqual(pending["items"][0]["source_atoms"][0]["version_id"], self.atoms["urgent_1"][1])

        again = self.client.post(
            f"/api/skill-factory/scene-catalog/suggestions/{suggestion['suggestion_id']}/confirm",
            json={"groups": [{"name": "再次", "tags": []}]}, headers=self.admin_headers,
        )
        self.assertEqual(again.status_code, 409)
        # 目录建立后，没归入场景的标签计为「未整理的新标签」，可以再次自动整理（见 test_15）
        catalog = self.client.get("/api/skill-factory/scene-catalog", headers=self.admin_headers).json()
        self.assertEqual(catalog["unorganized_tag_count"], 1)

        with get_db() as conn:
            actions = {r["action"] for r in conn.execute(
                "SELECT action FROM audit_logs WHERE organization_id = ?", (self.org_id,)
            ).fetchall()}
        self.assertIn("scene_merge_suggest_requested", actions)
        self.assertIn("scene_catalog_confirmed", actions)

    def test_07_ac03_c1_c2_extraction_with_catalog(self):
        """AC03 / M01-C1 / C2：抽取提示附加目录；目录外标签保存在原子上并进入待归并列表。"""
        calls, rows, _ = self._run_fake_extraction(["防汛管理", "地下室积水巡查"])
        self.assertTrue(calls)
        catalog_arg = calls[0].get("scene_catalog")
        self.assertIsNotNone(catalog_arg)
        self.assertEqual({s["name"] for s in catalog_arg}, {"设备应急处置", "防汛管理", "工程查验"})
        hint = de.build_scene_catalog_hint(catalog_arg)
        self.assertIn("【业务场景目录】", hint)
        self.assertIn("- 防汛管理：", hint)

        self.assertEqual(json.loads(rows[0]["business_scenes_json"]), ["防汛管理", "地下室积水巡查"])
        type(self).extracted_atom = (rows[0]["item_id"], rows[0]["id"])
        pending = self.client.get("/api/skill-factory/scene-catalog/pending-tags", headers=self.admin_headers).json()
        tags = {p["tag"]: p for p in pending["items"]}
        self.assertIn("地下室积水巡查", tags)
        self.assertNotIn("防汛管理", tags)
        src = tags["地下室积水巡查"]["source_atoms"][0]
        self.assertEqual(src["source"], "extraction")
        self.assertEqual(src["version_id"], rows[0]["id"])

        # 真实请求构造：目录进入 user 消息，系统提示词保持不变
        captured = {}

        def fake_post(messages, **kwargs):
            captured["messages"] = messages
            return json.dumps({"candidates": []}), "mock-model"

        with patch("deepseek_extractor.post_chat_completion", side_effect=fake_post):
            de.extract_atoms_via_deepseek([{"id": "b1", "text_content": "正文"}], "文档", api_key="mock",
                                          scene_catalog=catalog_arg)
        self.assertEqual(captured["messages"][0]["content"], de.SYSTEM_PROMPT)
        self.assertIn("【业务场景目录】", captured["messages"][1]["content"])
        with patch("deepseek_extractor.post_chat_completion", side_effect=fake_post):
            de.extract_atoms_via_deepseek([{"id": "b1", "text_content": "正文"}], "文档", api_key="mock")
        self.assertNotIn("业务场景目录", captured["messages"][1]["content"])

    def test_08_ac03_map_pending_then_recall(self):
        """AC03：待归并标签映射到已有场景（写入别名）后，该原子可被该场景召回。"""
        item_id, version_id = self.extracted_atom
        self._confirm(item_id)
        self._wait_ready([version_id])
        scenes = {s["name"]: s for s in self.client.get(
            "/api/skill-factory/scene-catalog", headers=self.admin_headers).json()["scenes"]}
        flood = scenes["防汛管理"]
        with get_db() as conn:
            before = sc.recall_atoms(conn, self.admin_user, sc.get_scene(conn, self.org_id, flood["scene_id"]),
                                     search_fn=lambda *a, **k: [])
        self.assertIn(version_id, {a["version_id"] for a in before["atoms"]})  # 本身带「防汛管理」标签

        # 另建一个只带目录外标签的原子，映射前不能被标签召回
        item2, ver2 = self._create_atom(self.doc, "积水巡查频次", "汛期每日巡查地下室积水情况。", ["地下室积水巡查"])
        self._confirm(item2)
        self._wait_ready([ver2])
        with get_db() as conn:
            sc.register_unmatched_scene_tags(conn, self.org_id, ["地下室积水巡查"], item2, ver2, "manual", title="积水巡查频次")
            before = sc.recall_atoms(conn, self.admin_user, sc.get_scene(conn, self.org_id, flood["scene_id"]),
                                     search_fn=lambda *a, **k: [])
        self.assertNotIn(ver2, {a["version_id"] for a in before["atoms"]})

        pending = self.client.get("/api/skill-factory/scene-catalog/pending-tags", headers=self.admin_headers).json()
        target = next(p for p in pending["items"] if p["tag"] == "地下室积水巡查")
        self.assertEqual(len(target["source_atoms"]), 2)
        resp = self.client.post(
            f"/api/skill-factory/scene-catalog/pending-tags/{target['pending_tag_id']}/resolve",
            json={"action": "map", "scene_id": flood["scene_id"]}, headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "mapped")
        self.assertIn("地下室积水巡查", resp.json()["scene"]["aliases"])

        recall = self.client.get(f"/api/skill-factory/scenes/{flood['scene_id']}/recall", headers=self.admin_headers)
        self.assertEqual(recall.status_code, 200, recall.text)
        pool = {a["version_id"]: a for a in recall.json()["atoms"]}
        self.assertIn(ver2, pool)
        self.assertEqual(pool[ver2]["recall_source"], "tag")
        self.assertEqual(pool[ver2]["matched_tags"], ["地下室积水巡查"])
        # 原子本身未被修改
        with get_db() as conn:
            row = conn.execute("SELECT business_scenes_json, version_number FROM knowledge_versions WHERE id = ?",
                               (ver2,)).fetchone()
        self.assertEqual(json.loads(row["business_scenes_json"]), ["地下室积水巡查"])
        self.assertEqual(row["version_number"], 1)

    def test_09_c3_manual_tag_enters_pending(self):
        """M01-C3：核对时手动输入的目录外标签进入待归并；目录内名称或别名不进入。"""
        item_id, version_id = self._create_atom(self.doc, "手动标签测试", "汛期前物业工程人员应完成排水设施检查。", [])
        detail = self.client.get(f"/api/knowledge/items/{item_id}", headers=self.admin_headers).json()
        av = detail["active_version"]
        payload = {
            "revision_token": av["revision_token"], "title": av["title"], "primary_category": av["primary_category"],
            "atom_type": av["atom_type"], "subject": av["subject"], "statement": av["statement"],
            "content": av["content"], "conditions": [], "actions": av.get("actions") or [], "exceptions": [],
            "customer_types": [], "business_scenes": ["防汛管理", "应急抢修", "高空坠物排查"], "problem_tags": [],
        }
        resp = self.client.put(f"/api/knowledge/items/{item_id}/draft", json=payload, headers=self.admin_headers)
        self.assertEqual(resp.status_code, 200, resp.text)
        pending = self.client.get("/api/skill-factory/scene-catalog/pending-tags", headers=self.admin_headers).json()
        tags = {p["tag"]: p for p in pending["items"]}
        self.assertIn("高空坠物排查", tags)
        self.assertEqual(tags["高空坠物排查"]["source_atoms"][0]["source"], "manual")
        self.assertNotIn("防汛管理", tags)
        self.assertNotIn("应急抢修", tags)
        # 忽略
        resp = self.client.post(
            f"/api/skill-factory/scene-catalog/pending-tags/{tags['高空坠物排查']['pending_tag_id']}/resolve",
            json={"action": "ignore"}, headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "ignored")
        # 新建场景
        urgent = next(p for p in pending["items"] if p["tag"] == "急修处置")
        resp = self.client.post(
            f"/api/skill-factory/scene-catalog/pending-tags/{urgent['pending_tag_id']}/resolve",
            json={"action": "create", "new_scene": {"name": "报修与维修响应", "description": "业主报修与上门维修",
                                                   "typical_problems": ["漏水报修怎么处理"]}},
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "created")
        self.assertEqual(resp.json()["scene"]["aliases"], ["急修处置"])
        dup = self.client.post(
            f"/api/skill-factory/scene-catalog/pending-tags/{urgent['pending_tag_id']}/resolve",
            json={"action": "ignore"}, headers=self.admin_headers,
        )
        self.assertEqual(dup.status_code, 409)

    def test_10_ac04_ac06_cards_and_recall(self):
        """AC04：可用原子不足 3 条时不可生成；AC06：不满足资格的原子不进入原子池。"""
        # 与其他测试原子无关的场景，只有 1 条标签命中原子
        item_id, version_id = self._create_atom(self.doc, "草坪修剪频次", "小区草坪生长季每月修剪一次。", ["绿化养护"])
        self._confirm(item_id)
        self._wait_ready([version_id])
        created = self.client.post("/api/skill-factory/scenes", json={
            "name": "绿化养护", "description": "小区绿化修剪、浇灌与病虫害防治",
            "typical_problems": ["草坪病虫害如何防治"],
        }, headers=self.admin_headers)
        self.assertEqual(created.status_code, 200, created.text)

        cards = self.client.get("/api/skill-factory/scenes/cards", headers=self.admin_headers)
        self.assertEqual(cards.status_code, 200, cards.text)
        body = cards.json()
        self.assertTrue(body["has_catalog"])
        self.assertFalse(body["generation_available"])
        by_name = {c["name"]: c for c in body["cards"]}

        green = by_name["绿化养护"]
        type(self).green_card = green
        self.assertLess(green["available_atom_count"], 3, green["recall_stats"])
        self.assertFalse(green["can_generate"])
        self.assertEqual(green["generate_blocked_reason"], "可用知识不足 3 条，请先在知识管理中导入或确认相关资料")
        for card in body["cards"]:
            self.assertEqual(card["can_generate"], card["available_atom_count"] >= 3)

        flood = by_name["防汛管理"]
        self.assertGreaterEqual(flood["available_atom_count"], 3)
        self.assertTrue(flood["can_generate"])
        self.assertEqual(flood["skill_counts"], {"pending_review": 0, "approved": 0, "needs_recheck": 0, "total": 0})
        self.assertEqual(sum(flood["category_coverage"].values()), flood["available_atom_count"])
        self.assertIsNone(flood["semantic_error"], flood["semantic_error"])

        recall = self.client.get(f"/api/skill-factory/scenes/{flood['scene_id']}/recall",
                                 headers=self.admin_headers).json()
        ids = {a["version_id"] for a in recall["atoms"]}
        for key in ("flood_1", "flood_2", "flood_3"):
            self.assertIn(self.atoms[key][1], ids)
        for key in ("bad_unconfirmed", "bad_disabled", "bad_excluded", "bad_expired"):
            self.assertNotIn(self.atoms[key][1], ids, key)
        self.assertNotIn(self.other_atom[1], ids)
        self.assertEqual(recall["config"], {"pool_limit": sc.SCENE_RECALL_POOL_LIMIT, "semantic_top_n": 30,
                                            "semantic_reserved": sc.SCENE_SEMANTIC_RESERVED})
        for atom in recall["atoms"]:
            self.assertIn(atom["recall_source"], ("tag", "semantic"))
            if atom["recall_source"] == "semantic":
                self.assertNotIn("tag", atom["matched_by"])
                self.assertIsNotNone(atom["semantic_score"])
        # 标签命中排在语义召回之前
        sources = [a["recall_source"] for a in recall["atoms"]]
        self.assertEqual(sources, sorted(sources, key=lambda s: 0 if s == "tag" else 1))
        self.assertGreater(recall["stats"]["semantic_hits"], 0, "真实 BGE 语义召回应至少命中一条")

    def test_11_recall_limits_and_eligibility_on_semantic(self):
        """FR04：上限截取时标签命中优先；语义结果中不合格原子被 eligibility 过滤。"""
        with get_db() as conn:
            scene = next(s for s in sc.list_scenes(conn, self.org_id) if s["name"] == "防汛管理")
            bad_vid = self.atoms["bad_disabled"][1]
            emerg_vid = self.atoms["emerg_1"][1]

            def fake_search(conn_, user, query, now_iso):
                return [
                    {"item_id": "x", "version_id": bad_vid, "title": "停用", "relevance_score": 0.9},
                    {"item_id": self.atoms["emerg_1"][0], "version_id": emerg_vid, "title": "应急",
                     "primary_category": "制度与标准", "relevance_score": 0.5},
                ]

            result = sc.recall_atoms(conn, self.admin_user, scene, search_fn=fake_search)
            ids = [a["version_id"] for a in result["atoms"]]
            self.assertNotIn(bad_vid, ids)
            self.assertIn(emerg_vid, ids)
            self.assertEqual(ids[-1], emerg_vid)
            # 不保留语义名额时，截取为标签命中优先
            limited = sc.recall_atoms(conn, self.admin_user, scene, pool_limit=2, search_fn=fake_search,
                                      semantic_reserved=0)
            self.assertEqual(len(limited["atoms"]), 2)
            self.assertTrue(all(a["recall_source"] == "tag" for a in limited["atoms"]))
            self.assertTrue(limited["stats"]["truncated"])
            # 默认为内容相关原子保留名额（2026-09-27 用户确认）
            reserved = sc.recall_atoms(conn, self.admin_user, scene, pool_limit=2, search_fn=fake_search)
            self.assertEqual([a["recall_source"] for a in reserved["atoms"]], ["tag", "semantic"])
            self.assertEqual(reserved["stats"]["tag_truncated"], reserved["stats"]["tag_hits"] - 1)
            broken = sc.recall_atoms(conn, self.admin_user, scene,
                                     search_fn=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no model")))
            self.assertIn("no model", broken["semantic_error"])
            self.assertGreaterEqual(broken["stats"]["tag_hits"], 3)

    def test_12_scene_crud_disable_and_audit(self):
        """场景增删改、停用接口；冲突校验；操作写入 audit_logs。"""
        created = self.client.post("/api/skill-factory/scenes", json={
            "name": "安防监控", "description": "监控与告警处置", "aliases": ["智能安防"],
            "typical_problems": ["高空抛物告警如何处理"],
        }, headers=self.admin_headers)
        self.assertEqual(created.status_code, 200, created.text)
        scene = created.json()
        dup = self.client.post("/api/skill-factory/scenes", json={"name": " 安防 监控 "}, headers=self.admin_headers)
        self.assertEqual(dup.status_code, 409)
        alias_clash = self.client.post("/api/skill-factory/scenes", json={"name": "新场景", "aliases": ["应急抢险"]},
                                       headers=self.admin_headers)
        self.assertEqual(alias_clash.status_code, 409)
        empty = self.client.post("/api/skill-factory/scenes", json={"name": "  "}, headers=self.admin_headers)
        self.assertEqual(empty.status_code, 400)

        updated = self.client.put(f"/api/skill-factory/scenes/{scene['scene_id']}", json={
            "revision_token": scene["revision_token"], "aliases": ["智能安防", "告警处置"],
        }, headers=self.admin_headers)
        self.assertEqual(updated.status_code, 200, updated.text)
        stale = self.client.put(f"/api/skill-factory/scenes/{scene['scene_id']}", json={
            "revision_token": scene["revision_token"], "description": "旧令牌",
        }, headers=self.admin_headers)
        self.assertEqual(stale.status_code, 409)

        disabled = self.client.post(f"/api/skill-factory/scenes/{scene['scene_id']}/disable", json={},
                                    headers=self.admin_headers)
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual(disabled.json()["status"], "disabled")
        cards = self.client.get("/api/skill-factory/scenes/cards", headers=self.admin_headers).json()["cards"]
        self.assertNotIn(scene["scene_id"], {c["scene_id"] for c in cards})
        # 停用场景不进入抽取目录提示
        with get_db() as conn:
            self.assertNotIn("安防监控", {s["name"] for s in sc.get_catalog_for_extraction(conn, self.org_id)})
        enabled = self.client.post(f"/api/skill-factory/scenes/{scene['scene_id']}/enable", json={},
                                   headers=self.admin_headers)
        self.assertEqual(enabled.json()["status"], "active")
        deleted = self.client.delete(f"/api/skill-factory/scenes/{scene['scene_id']}", headers=self.admin_headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.client.get(f"/api/skill-factory/scenes/{scene['scene_id']}/recall",
                                         headers=self.admin_headers).status_code, 404)

        with get_db() as conn:
            rows = conn.execute(
                "SELECT action, details FROM audit_logs WHERE target_id = ? ORDER BY created_at",
                (scene["scene_id"],),
            ).fetchall()
            pending_actions = {r["action"] for r in conn.execute(
                "SELECT action FROM audit_logs WHERE target_type = 'scene_pending_tag'").fetchall()}
        actions = [r["action"] for r in rows]
        for expected in ("scene_create", "scene_update", "scene_disable", "scene_enable", "scene_delete"):
            self.assertIn(expected, actions)
        self.assertTrue({"scene_pending_tag_map", "scene_pending_tag_ignore", "scene_pending_tag_create"} <= pending_actions)
        for r in rows:
            self.assertNotIn("key", (r["details"] or "").lower())

    def test_13_member_forbidden_and_org_isolation(self):
        """AC21（场景目录部分）：普通成员接口拒绝；其他企业看不到也改不了本企业场景。"""
        with get_db() as conn:
            scene = sc.list_scenes(conn, self.org_id)[0]
            pending = conn.execute("SELECT id FROM scene_pending_tags WHERE organization_id = ? LIMIT 1",
                                   (self.org_id,)).fetchone()
        member_calls = [
            ("get", "/api/skill-factory/scene-catalog", None),
            ("get", "/api/skill-factory/scene-catalog/tag-stats", None),
            ("post", "/api/skill-factory/scene-catalog/suggestions", None),
            ("get", "/api/skill-factory/scene-catalog/pending-tags", None),
            ("post", f"/api/skill-factory/scene-catalog/pending-tags/{pending['id']}/resolve", {"action": "ignore"}),
            ("post", "/api/skill-factory/scenes", {"name": "成员新建"}),
            ("put", f"/api/skill-factory/scenes/{scene['scene_id']}", {"name": "成员改名"}),
            ("post", f"/api/skill-factory/scenes/{scene['scene_id']}/disable", {}),
            ("delete", f"/api/skill-factory/scenes/{scene['scene_id']}", None),
            ("get", "/api/skill-factory/scenes/cards", None),
            ("get", f"/api/skill-factory/scenes/{scene['scene_id']}/recall", None),
        ]
        for method, url, body in member_calls:
            kwargs = {"headers": self.member_headers}
            if body is not None:
                kwargs["json"] = body
            resp = getattr(self.client, method)(url, **kwargs)
            self.assertEqual(resp.status_code, 403, f"{method} {url} -> {resp.status_code}")
        unauth = self.client.get("/api/skill-factory/scene-catalog")
        self.assertEqual(unauth.status_code, 401)

        other_catalog = self.client.get("/api/skill-factory/scene-catalog", headers=self.other_headers).json()
        self.assertFalse(other_catalog["has_catalog"])
        self.assertEqual(other_catalog["scenes"], [])
        self.assertEqual(self.client.get(f"/api/skill-factory/scenes/{scene['scene_id']}/recall",
                                         headers=self.other_headers).status_code, 404)
        self.assertEqual(self.client.put(f"/api/skill-factory/scenes/{scene['scene_id']}", json={"name": "越权"},
                                         headers=self.other_headers).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/skill-factory/scenes/{scene['scene_id']}",
                                            headers=self.other_headers).status_code, 404)
        self.assertEqual(self.client.post(
            f"/api/skill-factory/scene-catalog/pending-tags/{pending['id']}/resolve",
            json={"action": "ignore"}, headers=self.other_headers).status_code, 404)
        stats = self.client.get("/api/skill-factory/scene-catalog/tag-stats", headers=self.other_headers).json()
        other_versions = {a["version_id"] for e in stats["tags"] for a in e["atoms"]}
        self.assertEqual(other_versions, {self.other_atom[1]})
        # 其他企业可以建同名场景，互不影响
        same_name = self.client.post("/api/skill-factory/scenes", json={"name": scene["name"]},
                                     headers=self.other_headers)
        self.assertEqual(same_name.status_code, 200, same_name.text)
        other_recall = self.client.get(f"/api/skill-factory/scenes/{same_name.json()['scene_id']}/recall",
                                       headers=self.other_headers).json()
        self.assertTrue({a["version_id"] for a in other_recall["atoms"]} <= {self.other_atom[1]})

    def test_14_interrupted_suggestion_recovered(self):
        """后台任务恢复：服务重启时 running 的归并建议重新排队执行（模拟返回）。"""
        other_org = self.other_user["organization_id"]
        with get_db() as conn:
            conn.execute("DELETE FROM scenes WHERE organization_id = ?", (other_org,))
            stats = sc.collect_scene_tag_stats(conn, self.other_user)
            sid = f"smg_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """INSERT INTO scene_merge_suggestions (id, organization_id, status, tag_stats_json, prompt_version,
                   requested_by, created_at, started_at) VALUES (?, ?, 'running', ?, 'scene-merge-v1', ?, ?, ?)""",
                (sid, other_org, json.dumps(stats, ensure_ascii=False), self.other_user["id"],
                 datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
            )
        valid = _valid_merge_json([{"name": "防汛管理", "description": "防汛", "typical_problems": [],
                                    "tags": ["防汛管理"], "reason": "单一标签"}])
        with patch.object(config, "DEEPSEEK_API_KEY", "mock-key-for-test"), \
                patch("scene_catalog.post_chat_completion", return_value=(valid, "mock-model")):
            recover_interrupted_tasks()
            self.assertTrue(wait_for_background_tasks(timeout=60.0))
        with get_db() as conn:
            row = conn.execute("SELECT status FROM scene_merge_suggestions WHERE id = ?", (sid,)).fetchone()
        self.assertEqual(row["status"], "completed")


    def test_15_incremental_organize_new_tags(self):
        """目录建立后整理新标签：新标签归入已有场景或组成新场景；已有场景只追加标签；忽略的标签不再参与。"""
        new_atoms = {}
        for key, title, tags in [
            ("drain", "排水设施日常巡查", ["排水设施巡查"]),
            ("fire_check", "消防设施月度检查", ["消防设施检查"]),
            ("fire_drill", "消防演练组织要求", ["消防演练"]),
            ("ignored", "高空坠物隐患排查", ["高空坠物排查"]),  # 该标签在 test_09 中已被忽略
        ]:
            new_atoms[key] = self._create_atom(self.doc, title, title + "的具体要求。", tags)
            self._confirm(new_atoms[key][0])
        self._wait_ready([v for _, v in new_atoms.values()])

        catalog = self.client.get("/api/skill-factory/scene-catalog", headers=self.admin_headers).json()
        self.assertEqual(catalog["unorganized_tag_count"], 3)
        flood = next(s for s in catalog["scenes"] if s["name"] == "防汛管理")

        # 校验边界（模拟返回）：已有场景名称不存在、新场景名称与已有标签重复
        existing = [s for s in catalog["scenes"] if s["status"] == "active"]
        stats = [{"tag": t, "count": 1, "atoms": []} for t in ("排水设施巡查", "消防设施检查", "消防演练")]
        _, _, errors = sc.validate_merge_response(_valid_merge_json([
            {"existing": True, "name": "不存在的场景", "tags": ["排水设施巡查"], "reason": "r"},
        ]), stats, existing)
        self.assertTrue(any("不在现有场景目录中" in e for e in errors))
        _, _, errors = sc.validate_merge_response(_valid_merge_json([
            {"existing": False, "name": "应急抢险", "description": "d", "typical_problems": [],
             "tags": ["消防演练"], "reason": "r"},
        ]), stats, existing)
        self.assertTrue(any("应改为归入现有场景" in e for e in errors))

        mock_reply = _valid_merge_json([
            {"existing": True, "name": "防汛管理", "description": "", "typical_problems": [],
             "tags": ["排水设施巡查"], "reason": "排水巡查属于防汛"},
            {"existing": False, "name": "消防管理", "description": "消防设施检查与消防演练",
             "typical_problems": ["消防设施多久检查一次"], "tags": ["消防设施检查", "消防演练"], "reason": "同属消防"},
        ])
        with patch.object(config, "DEEPSEEK_API_KEY", "mock-key-for-test"), \
                patch("scene_catalog.post_chat_completion", return_value=(mock_reply, "mock-model")) as mocked:
            resp = self.client.post("/api/skill-factory/scene-catalog/suggestions", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["mode"], "incremental")
            self.assertEqual({t["tag"] for t in resp.json()["tag_stats"]}, {"排水设施巡查", "消防设施检查", "消防演练"})
            suggestion = self._wait_suggestion(resp.json()["suggestion_id"])
        self.assertEqual(suggestion["status"], "completed")
        self.assertEqual(suggestion["prompt_version"], sc.SCENE_MERGE_INCREMENTAL_PROMPT_VERSION)
        user_msg = mocked.call_args[0][0][1]["content"]
        self.assertIn("现有场景目录", user_msg)
        self.assertIn("防汛管理", user_msg)
        self.assertNotIn("高空坠物排查", user_msg)
        existing_group = next(g for g in suggestion["groups"] if g["existing"])
        self.assertEqual(existing_group["target_scene_id"], flood["scene_id"])

        before = self._atom_snapshot()
        confirmed = self.client.post(
            f"/api/skill-factory/scene-catalog/suggestions/{suggestion['suggestion_id']}/confirm",
            json={"groups": [
                {"target_scene_id": flood["scene_id"], "name": "防汛管理", "tags": ["排水设施巡查"]},
                {"name": "消防安全", "description": "消防设施检查与消防演练", "typical_problems": ["消防设施多久检查一次"],
                 "tags": ["消防设施检查", "消防演练"]},
            ]},
            headers=self.admin_headers,
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        body = confirmed.json()
        self.assertEqual([s["name"] for s in body["scenes"]], ["消防安全"])
        self.assertEqual([s["name"] for s in body["extended_scenes"]], ["防汛管理"])
        self.assertEqual(body["pending_tags"], [])
        self.assertEqual(before, self._atom_snapshot(), "整理新标签不得修改原子或产生新版本")

        catalog = self.client.get("/api/skill-factory/scene-catalog", headers=self.admin_headers).json()
        flood_after = next(s for s in catalog["scenes"] if s["scene_id"] == flood["scene_id"])
        self.assertEqual(flood_after["name"], flood["name"])
        self.assertEqual(flood_after["description"], flood["description"])
        self.assertEqual(flood_after["aliases"], flood["aliases"] + ["排水设施巡查"])
        self.assertEqual(catalog["unorganized_tag_count"], 0)

        recall = self.client.get(f"/api/skill-factory/scenes/{flood['scene_id']}/recall", headers=self.admin_headers).json()
        pool = {a["version_id"]: a for a in recall["atoms"]}
        self.assertEqual(pool[new_atoms["drain"][1]]["recall_source"], "tag")

        with patch.object(config, "DEEPSEEK_API_KEY", "mock-key-for-test"):
            none_left = self.client.post("/api/skill-factory/scene-catalog/suggestions", headers=self.admin_headers)
        self.assertEqual(none_left.status_code, 409)
        self.assertEqual(none_left.json()["detail"], "目前没有需要整理的新标签")

        with get_db() as conn:
            detail = conn.execute(
                "SELECT details FROM audit_logs WHERE target_id = ? AND action = 'scene_catalog_confirmed'",
                (suggestion["suggestion_id"],),
            ).fetchone()
        self.assertEqual(json.loads(detail["details"])["mode"], "incremental")


if __name__ == "__main__":
    unittest.main(verbosity=2)
