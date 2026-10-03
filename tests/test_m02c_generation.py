"""
M02-C 自动化测试：生成批次（PRD 第 6 章 FR04~FR08、6.2、6.3）

- 全部在隔离的临时数据库中运行（test_env_helper.setup_test_db），不读写 server/data/zhixing.db。
- 本文件中的模型返回一律为「模拟返回」（patch skill_generation.post_chat_completion），
  只用于流程、边界与异常测试；真实模型调用见 tests/run_m02c_live_generation.py。
- 语义召回用固定的模拟检索函数（patch hybrid_retrieval.hybrid_search），保证原子池可复现；
  真实 BGE 语义召回已在 M02-B 测试中覆盖。
- 测试原子直接写成「已确认 + 索引可用」，资格判断仍走 eligibility.py。

覆盖：AC04、AC05、AC07、AC08、AC09、AC10；模型调用失败与超时的重试、重试耗尽后其他任务继续；
拆分为空；拆分程序校验（池外 ID、原子不足、任务上限、可能重复）；与已有 Skill 相似；
生成过程中原子被停用时入库前复查 R1；启动恢复；成员与跨企业访问；退回重生成预留函数。
"""

import json
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

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

FAKE_KEY = "sk-m02c-fake-key-never-sent"
FAKE_MODEL = "deepseek-mock"


# ---------------------------------------------------------------------------
# 模拟模型
# ---------------------------------------------------------------------------

def _user_payload(messages):
    return json.loads(messages[1]["content"].split("\n", 1)[1])


def build_candidate(payload, *, drop_exceptions=False, extra_step=None, override_refs=None):
    """按用户消息中的原子构造一个结构合格的 Skill 候选（模拟返回）。"""
    atoms = payload["atoms"]
    steps = [{"step_id": "s1", "kind": "输入校验", "action": "检查必需输入是否齐全", "refs": [],
              "basis": "通用操作", "on_fail": "补问"}]
    refs = []
    for i, atom in enumerate(atoms, start=2):
        sid = f"s{i}"
        steps.append({"step_id": sid, "kind": "规则判断", "action": atom["statement"],
                      "refs": [atom["atom_version_id"]], "basis": "有原子依据", "on_fail": "转人工"})
        refs.append({"atom_item_id": atom["atom_item_id"], "atom_version_id": atom["atom_version_id"],
                     "role": (atom.get("default_roles") or ["判断规则"])[0], "used_in_steps": [sid]})
    if extra_step:
        steps.append(extra_step)
    if override_refs:
        override_refs(steps, refs)
    exceptions = [e for a in atoms for e in a.get("exceptions") or []]
    return {
        "schema_version": "1.0",
        "name": (payload["task"]["name"] or "测试任务")[:20],
        "goal": payload["task"]["goal"] or "测试目标",
        "trigger_description": "用户询问" + (payload["task"]["name"] or "测试任务"),
        "task_type": payload["task"]["task_type"] or "判断分级",
        "scene_id": "model-wrote-wrong-scene",
        "applies_to": {"customer_types": ["住宅业主"], "property_types": [], "conditions": []},
        "not_applies_to": ["TBD_EXPERT"],
        "knowledge_refs": refs,
        "inputs": [{"key": "period", "label": "统计期间", "type": "period", "required": True}],
        "preconditions": [],
        "outputs": [{"key": "result", "label": "判断结果", "type": "text", "required": True}],
        "steps": steps,
        "risk_boundary": ["TBD_EXPERT"],
        "escalation_conditions": ([] if drop_exceptions else exceptions) or ["TBD_EXPERT"],
        "generation_confidence": {"level": "中", "reason": "模拟返回"},
        # 模型误填的系统字段应被系统丢弃
        "status": "已通过（待测试）",
        "visibility": "org_internal",
    }


def default_split(payload, atom_filter=None):
    ids = [a["atom_version_id"] for a in payload["atom_pool"]
           if (atom_filter is None or atom_filter(a)) and a.get("recall_source") == "tag"]
    return {
        "tasks": [
            {"name": "核对指定期间收缴率", "goal": "核对收缴率", "task_type": "计算核对",
             "atom_version_ids": ids[:3], "split_reason": "收缴口径与归属"},
            {"name": "欠费分级与催缴建议", "goal": "判断欠费等级并给出催缴措施", "task_type": "判断分级",
             "atom_version_ids": ids[2:5], "split_reason": "分级与催缴"},
        ],
        "unused_atoms": [],
    }


class FakeModel:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.split = lambda payload, messages: json.dumps(default_split(payload), ensure_ascii=False)
        self.generate = lambda payload, messages: json.dumps(build_candidate(payload), ensure_ascii=False)

    def __call__(self, messages, api_key=None, **kwargs):
        assert api_key == FAKE_KEY
        payload = _user_payload(messages)
        kind = "split" if "atom_pool" in payload else "generate"
        with self.lock:
            self.calls.append({"kind": kind, "task": (payload.get("task") or {}).get("name"),
                               "repair": len(messages) > 2, "messages": messages, "kwargs": kwargs})
        fn = self.split if kind == "split" else self.generate
        return fn(payload, messages), FAKE_MODEL

    def count(self, kind=None, task=None, repair=None):
        with self.lock:
            return sum(1 for c in self.calls
                       if (kind is None or c["kind"] == kind) and (task is None or c["task"] == task)
                       and (repair is None or c["repair"] == repair))


class TestM02CGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_db = setup_test_db("m02c_generation")
        cls.client = TestClient(app)
        cls.admin_headers, cls.admin_user = cls._login("admin", "Admin@Zhixing2026")
        cls.member_headers, _ = cls._login("member", "Member@Zhixing2026")
        cls.other_headers, cls.other_user = cls._login("other_admin", "Other@Zhixing2026")
        cls.org_id = cls.admin_user["organization_id"]
        with get_db() as conn:
            cls.actor = sg.load_actor(conn, cls.admin_user["id"], cls.org_id)
        cls.prefix = "m02c_" + uuid.uuid4().hex[:6]
        cls.atoms = {}
        cls._build_fixture()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_db(cls.test_db)

    def setUp(self):
        self.model = FakeModel()
        patches = [
            patch.object(config, "DEEPSEEK_API_KEY", FAKE_KEY),
            patch.object(sg, "post_chat_completion", self.model),
            patch.object(hybrid_retrieval, "hybrid_search", self._fake_search),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    # ------------------------------------------------------------------ fixture
    @classmethod
    def _login(cls, username, password):
        resp = cls.client.post("/api/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['token']}"}, resp.json()["user"]

    @classmethod
    def _create_document(cls, title, org_id=None):
        org_id = org_id or cls.org_id
        now = datetime.now(timezone.utc).isoformat()
        doc_id = f"doc_{cls.prefix}_{uuid.uuid4().hex[:6]}"
        ver_id = f"ver_{cls.prefix}_{uuid.uuid4().hex[:6]}"
        with get_db() as conn:
            conn.execute(
                """INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted,
                   created_at, updated_at) VALUES (?, ?, ?, ?, 'org_internal', 0, ?, ?)""",
                (doc_id, org_id, title, ver_id, now, now),
            )
            conn.execute(
                """INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name, file_type,
                   file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                   VALUES (?, ?, ?, 'v1', ?, 'txt', 100, ?, ?, 'usr_admin_001', ?, 'completed')""",
                (ver_id, doc_id, org_id, title + ".txt", uuid.uuid4().hex, f"{ver_id}.txt", now),
            )
        return doc_id, ver_id

    @classmethod
    def _add_atom(cls, key, doc, title, statement, scenes, category, atom_type="规则", exceptions=None,
                  conditions=None, metric=None, org_id=None, access="org_internal"):
        org_id = org_id or cls.org_id
        doc_id, ver_id = doc
        now = datetime.now(timezone.utc).isoformat()
        item_id = f"ki_{cls.prefix}_{uuid.uuid4().hex[:6]}"
        version_id = f"kv_{cls.prefix}_{uuid.uuid4().hex[:6]}"
        with get_db() as conn:
            conn.execute(
                """INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope,
                   lifecycle_status, is_excluded, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'active', 0, ?, ?)""",
                (item_id, doc_id, org_id, version_id, access, now, now),
            )
            conn.execute(
                """INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id,
                   version_number, title, content, primary_category, atom_type, subject, statement,
                   conditions_json, actions_json, exceptions_json, metric_definition_json, customer_types_json,
                   business_scenes_json, review_status, index_status, revision_token, created_at, created_by)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, '物业管理处', ?, ?, ?, ?, ?, '["住宅业主"]', ?,
                           'confirmed', 'ready', ?, ?, 'usr_admin_001')""",
                (version_id, item_id, org_id, ver_id, title, statement, category, atom_type, statement,
                 json.dumps(conditions or [], ensure_ascii=False), json.dumps([statement], ensure_ascii=False),
                 json.dumps(exceptions or [], ensure_ascii=False),
                 json.dumps(metric, ensure_ascii=False) if metric else None,
                 json.dumps(scenes, ensure_ascii=False), uuid.uuid4().hex, now),
            )
        cls.atoms[key] = {"item_id": item_id, "version_id": version_id, "title": title}

    @classmethod
    def _build_fixture(cls):
        doc = cls._create_document("物业费收缴测试资料")
        cls._add_atom("rate", doc, "收缴率计算口径", "收缴率等于当期实收除以当期应收，按月统计。", ["收缴管理"],
                      "指标数据", "指标", exceptions=["当期应收为零时不计算收缴率"],
                      metric={"formula": "实收/应收", "unit": "%"})
        cls._add_atom("attr", doc, "实收归属条件", "实收金额只计入与当期应收对应的款项。", ["收缴管理"], "制度与标准",
                      conditions=["同一统计范围"])
        cls._add_atom("grade", doc, "欠费分级标准", "欠费超过3个月的业主列为重点欠费户。", ["收缴管理"], "制度与标准",
                      exceptions=["存在物业服务争议的欠费另行处理"])
        cls._add_atom("remind", doc, "催缴方法", "催缴先书面通知，再上门沟通。", ["收缴管理"], "方法与工具", "方法")
        cls._add_atom("case", doc, "分级催缴案例", "某项目通过分级催缴提升了收缴率。", ["收缴管理"], "项目案例", "案例")
        cls._add_atom("repair", doc, "报修响应要求", "业主报修后应及时派单。", ["报修响应"], "制度与标准")
        cls._add_atom("few1", doc, "孤立知识一", "孤立场景的第一条规则。", ["孤立标签"], "制度与标准")
        cls._add_atom("few2", doc, "孤立知识二", "孤立场景的第二条规则。", ["孤立标签"], "方法与工具", "方法")
        # R1 测试专用场景的原子，避免影响其他用例
        for i in range(1, 4):
            cls._add_atom(f"r1_{i}", doc, f"巡检规则{i}", f"巡检第{i}项要求。", ["巡检管理"], "制度与标准")
        with get_db() as conn:
            cls.scene_fee = sc.create_scene(conn, cls.org_id, cls.admin_user["id"], {
                "name": "物业费收缴管理", "description": "收缴率核对与欠费催缴", "aliases": ["收缴管理"],
                "typical_problems": ["收缴率怎么算"]})
            cls.scene_few = sc.create_scene(conn, cls.org_id, cls.admin_user["id"], {
                "name": "孤立场景", "description": "只有两条知识", "aliases": ["孤立标签"]})
            cls.scene_patrol = sc.create_scene(conn, cls.org_id, cls.admin_user["id"], {
                "name": "巡检管理", "description": "日常巡检", "aliases": []})

    @classmethod
    def _fake_search(cls, conn, user, query, now_iso=None, **kwargs):
        """固定的模拟语义召回：收缴场景额外找到一条「报修响应」原子（来源为语义）。"""
        if "收缴" not in query:
            return []
        atom = cls.atoms["repair"]
        return [{"item_id": atom["item_id"], "version_id": atom["version_id"], "title": atom["title"],
                 "primary_category": "制度与标准", "atom_type": "规则", "statement": "业主报修后应及时派单。",
                 "business_scenes": ["报修响应"], "relevance_score": 0.61}]

    # ------------------------------------------------------------------ helpers
    def _start(self, scene, focus=None, headers=None):
        return self.client.post(f"/api/skill-factory/scenes/{scene['scene_id']}/batches",
                                json={"focus_note": focus} if focus else {}, headers=headers or self.admin_headers)

    def _wait_batch(self, batch_id, timeout=30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = self.client.get(f"/api/skill-factory/batches/{batch_id}", headers=self.admin_headers)
            self.assertEqual(resp.status_code, 200, resp.text)
            if resp.json()["status"] != "running":
                return resp.json()
            time.sleep(0.05)
        self.fail("批次等待超时")

    def _run_batch(self, scene, focus=None):
        resp = self._start(scene, focus)
        self.assertEqual(resp.status_code, 200, resp.text)
        return self._wait_batch(resp.json()["batch_id"])

    def _tasks(self, batch_id, task_type=None):
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM skill_generation_tasks WHERE batch_id = ? ORDER BY created_at",
                                (batch_id,)).fetchall()
        return [dict(r) for r in rows if task_type is None or r["task_type"] == task_type]

    def _skill_rows(self, batch_id):
        with get_db() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM skills WHERE batch_id = ? ORDER BY task_key", (batch_id,)).fetchall()]

    def _set_lifecycle(self, key, status):
        with get_db() as conn:
            conn.execute("UPDATE knowledge_items SET lifecycle_status = ? WHERE id = ?",
                         (status, self.atoms[key]["item_id"]))

    # ------------------------------------------------------------------ tests
    def test_01_ac04_insufficient_atoms_rejected(self):
        resp = self._start(self.scene_few)
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertIn("不足 3 条", resp.json()["detail"])
        cards = self.client.get("/api/skill-factory/scenes/cards", headers=self.admin_headers).json()
        few = next(c for c in cards["cards"] if c["scene_id"] == self.scene_few["scene_id"])
        self.assertFalse(few["can_generate"])
        self.assertTrue(cards["generation_available"])
        with get_db() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM skill_generation_batches WHERE scene_id = ?",
                             (self.scene_few["scene_id"],)).fetchone()["n"]
        self.assertEqual(n, 0)
        self.assertEqual(self.model.count(), 0)

    def test_02_ac05_full_batch_flow(self):
        batch = self._run_batch(self.scene_fee, focus="侧重收缴率核对")
        self.assertEqual(batch["status"], "completed", batch.get("status_reason"))
        self.assertEqual([s["status"] for s in batch["stages"]], ["done", "done", "done", "done"])
        self.assertEqual(batch["focus_note"], "侧重收缴率核对")

        # 原子池快照：版本 ID + 召回来源（标签 / 语义）
        pool = batch["atom_pool"]["atoms"]
        sources = {a["atom_version_id"]: a["recall_source"] for a in pool}
        self.assertEqual(sources[self.atoms["rate"]["version_id"]], "tag")
        self.assertEqual(sources[self.atoms["repair"]["version_id"]], "semantic")
        self.assertNotIn(self.atoms["few1"]["version_id"], sources)

        # 拆分结果含未使用原子及原因（程序补齐模型未说明的）
        split = batch["task_split"]
        self.assertEqual(split["generate_count"], 2)
        unused = {u["atom_version_id"]: u for u in split["unused_atoms"]}
        self.assertIn(self.atoms["repair"]["version_id"], unused)
        self.assertEqual(unused[self.atoms["repair"]["version_id"]]["source"], "program")
        self.assertEqual(split["prompt_version"], "skill-split-v1")
        self.assertEqual(batch["prompt_versions"], {"split": "skill-split-v1", "generate": "skill-generate-v2"})
        # 生成侧重说明参与本批次的内容检索
        self.assertTrue(batch["atom_pool"]["semantic_query"].startswith("侧重收缴率核对"))

        # G6 入库：待审核、第 1 版 AI 原稿、引用快照
        self.assertEqual(len(batch["candidates"]), 2)
        for cand in batch["candidates"]:
            self.assertEqual(cand["status"], "pending_review")
            self.assertEqual(cand["confidence_level"], "中")
            self.assertEqual(cand["ref_count"], 3)
            self.assertEqual(cand["similar_skills"], [])
        with get_db() as conn:
            skill_id = batch["candidates"][0]["skill_id"]
            skill = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
            version = conn.execute("SELECT * FROM skill_versions WHERE id = ?", (skill["current_version_id"],)).fetchone()
            refs = conn.execute("SELECT * FROM skill_atom_refs WHERE skill_version_id = ?", (version["id"],)).fetchall()
            audit = conn.execute("SELECT action FROM audit_logs WHERE target_id IN (?, ?)",
                                 (batch["batch_id"], skill_id)).fetchall()
        self.assertEqual(skill["scene_id"], self.scene_fee["scene_id"])
        self.assertEqual(version["version_number"], 1)
        self.assertEqual(version["version_kind"], "ai_original")
        skill_json = json.loads(version["skill_json"])
        self.assertEqual(skill_json["scene_id"], self.scene_fee["scene_id"])  # 场景由系统写入
        self.assertEqual(skill_json["status"], "待审核")                        # 模型误填的系统字段被覆盖
        self.assertEqual(skill_json["generation"]["prompt_version"], "skill-generate-v2")
        self.assertEqual(skill_json["generation"]["batch_id"], batch["batch_id"])
        self.assertEqual(skill_json["generation"]["model_name"], FAKE_MODEL)
        self.assertIsInstance(skill_json["unsupported_items"], list)
        self.assertEqual(len(refs), 3)
        titles = {self.atoms[k]["version_id"]: self.atoms[k]["title"] for k in self.atoms}
        for ref in refs:
            self.assertEqual(ref["snapshot_title"], titles[ref["atom_version_id"]])
        self.assertEqual({r["action"] for r in audit}, {"skill_batch_start", "skill_candidate_created"})
        # AI 原稿不可修改
        with self.assertRaises(Exception):
            with get_db() as conn:
                conn.execute("UPDATE skill_versions SET skill_json = '{}' WHERE id = ?", (version["id"],))

        # 每次模型调用保存原始返回，且不含密钥
        calls = [c for t in self._tasks(batch["batch_id"]) for c in json.loads(t["model_calls_json"] or "[]")]
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c.get("raw_response") for c in calls))
        with get_db() as conn:
            dump = "\n".join(str(tuple(r)) for table in ("skill_generation_tasks", "skill_generation_batches",
                                                        "skill_versions", "skills", "audit_logs")
                             for r in conn.execute(f"SELECT * FROM {table}").fetchall())
        self.assertNotIn(FAKE_KEY, dump)
        # 生成调用使用 Skill 专用的输出长度与等待时间
        gen_call = next(c for c in self.model.calls if c["kind"] == "generate")
        self.assertEqual(gen_call["kwargs"]["timeout"], config.SKILL_MODEL_TIMEOUT_SECONDS)
        self.assertEqual(gen_call["kwargs"]["max_tokens"], config.SKILL_MODEL_MAX_OUTPUT_TOKENS)
        # 生成提示词包含 Schema、字段映射、角色对应与生成规则
        system = gen_call["messages"][0]["content"]
        for text in ('"$defs"', "原子字段到 Skill 字段的映射", "五类主分类到默认引用角色", "不得编造", "例外不得丢失",
                     "只描述算法与口径", "核对与原因诊断是不同任务", "案例不能单独支撑规则判断",
                     "步骤与引用的写法顺序", "输出前自检", "依据要如实标注"):
            self.assertIn(text, system)

        # 场景卡片的已有 Skill 数随入库更新
        cards = self.client.get("/api/skill-factory/scenes/cards", headers=self.admin_headers).json()
        card = next(c for c in cards["cards"] if c["scene_id"] == self.scene_fee["scene_id"])
        self.assertEqual(card["skill_counts"]["pending_review"], 2)
        self.assertIsNone(card["running_batch_id"])
        self.assertEqual(card["latest_batch"]["batch_id"], batch["batch_id"])

        # 最小候选查看：完整 JSON 只读
        detail = self.client.get(f"/api/skill-factory/skills/{skill_id}", headers=self.admin_headers).json()
        self.assertEqual(detail["skill_json"]["skill_id"], skill_id)
        self.assertEqual(detail["version_number"], 1)

    def test_03_ac07_running_batch_blocks_second_start(self):
        release = threading.Event()
        entered = threading.Event()
        original = self.model.split

        def slow_split(payload, messages):
            entered.set()
            release.wait(10)
            return original(payload, messages)

        self.model.split = slow_split
        first = self._start(self.scene_fee)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(entered.wait(10))
        second = self._start(self.scene_fee)
        self.assertEqual(second.status_code, 409, second.text)
        self.assertIn("正在生成", second.json()["detail"])
        cards = self.client.get("/api/skill-factory/scenes/cards", headers=self.admin_headers).json()
        card = next(c for c in cards["cards"] if c["scene_id"] == self.scene_fee["scene_id"])
        self.assertEqual(card["running_batch_id"], first.json()["batch_id"])
        release.set()
        batch = self._wait_batch(first.json()["batch_id"])
        self.assertEqual(batch["status"], "completed")
        # 与第一批所用原子相同，标记「与已有 Skill 相似」（FR08）
        self.assertTrue(all(c["similar_skills"] for c in batch["candidates"]))
        self.assertGreater(batch["candidates"][0]["similar_skills"][0]["overlap"], 0.8)
        # 批次结束后可以再次发起
        again = self._run_batch(self.scene_fee)
        self.assertEqual(again["status"], "completed")

    def test_04_ac08_out_of_pool_ref_repair_then_fail(self):
        outsider = self.atoms["few1"]  # 满足 R1 但不在本场景原子池内

        def bad_refs(steps, refs):
            refs.append({"atom_item_id": outsider["item_id"], "atom_version_id": outsider["version_id"],
                         "role": "判断规则", "used_in_steps": ["s2"]})
            steps[1]["refs"].append(outsider["version_id"])

        self.model.generate = lambda payload, messages: json.dumps(
            build_candidate(payload, override_refs=bad_refs), ensure_ascii=False)
        batch = self._run_batch(self.scene_fee)
        self.assertEqual(batch["status"], "failed")
        for result in batch["task_results"]:
            self.assertEqual(result["status"], "validation_failed")
            self.assertEqual(result["repair_count"], 1)
        # 每个任务：初次生成 1 次 + 自动修复 1 次，修复请求带上了问题清单
        for name in ("核对指定期间收缴率", "欠费分级与催缴建议"):
            self.assertEqual(self.model.count("generate", name, repair=False), 1)
            self.assertEqual(self.model.count("generate", name, repair=True), 1)
        repair_call = next(c for c in self.model.calls if c["repair"])
        self.assertIn("REF_NOT_IN_POOL", repair_call["messages"][-1]["content"])
        # 校验未通过：不产生版本、不进入待审核，保存错误与候选
        for row in self._skill_rows(batch["batch_id"]):
            self.assertEqual(row["status"], "validation_failed")
            self.assertIsNone(row["current_version_id"])
            error = json.loads(row["generation_error_json"])
            self.assertTrue(any(i["code"] == "REF_NOT_IN_POOL" for i in error["issues"]))
            self.assertIsNotNone(error["candidate"])
        with get_db() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM skill_versions WHERE batch_id = ?",
                             (batch["batch_id"],)).fetchone()["n"]
        self.assertEqual(n, 0)
        cand = batch["candidates"][0]
        self.assertEqual(cand["status"], "validation_failed")
        self.assertIn("引用了本次范围以外的知识", cand["error_summary"])

    def test_05_ac08_repair_succeeds(self):
        outsider = self.atoms["few1"]

        def generate(payload, messages):
            if len(messages) > 2:  # 修复轮：按问题清单改正
                return json.dumps(build_candidate(payload), ensure_ascii=False)

            def bad_refs(steps, refs):
                refs.append({"atom_item_id": outsider["item_id"], "atom_version_id": outsider["version_id"],
                             "role": "判断规则", "used_in_steps": ["s2"]})
                steps[1]["refs"].append(outsider["version_id"])
            return json.dumps(build_candidate(payload, override_refs=bad_refs), ensure_ascii=False)

        self.model.generate = generate
        batch = self._run_batch(self.scene_fee)
        self.assertEqual(batch["status"], "completed")
        self.assertTrue(all(r["repair_count"] == 1 for r in batch["task_results"]))
        with get_db() as conn:
            gen = json.loads(conn.execute(
                "SELECT generation_json FROM skill_versions WHERE batch_id = ? LIMIT 1", (batch["batch_id"],)
            ).fetchone()["generation_json"])
        self.assertEqual(gen["repair_count"], 1)

    def test_06_ac09_unsupported_number_listed(self):
        extra = {"step_id": "s9", "kind": "生成表达", "action": "30 分钟内电话回访业主", "refs": [],
                 "basis": "无依据", "on_fail": "暂停"}
        self.model.generate = lambda payload, messages: json.dumps(
            build_candidate(payload, extra_step=extra), ensure_ascii=False)
        batch = self._run_batch(self.scene_fee)
        self.assertEqual(batch["status"], "completed")
        detail = self.client.get(f"/api/skill-factory/skills/{batch['candidates'][0]['skill_id']}",
                                 headers=self.admin_headers).json()
        self.assertEqual(detail["status"], "pending_review")
        kinds = {(u["kind"], u.get("value")) for u in detail["skill_json"]["unsupported_items"]}
        self.assertIn(("疑似无依据数值", "30"), kinds)
        self.assertTrue(any(k == "无依据步骤" for k, _ in kinds))
        self.assertGreaterEqual(batch["candidates"][0]["unsupported_count"], 2)
        # 原子中已有的数值（欠费超过 3 个月）不被误报
        self.assertNotIn(("疑似无依据数值", "3"), kinds)

    def test_07_ac10_exception_not_landed_recorded(self):
        self.model.generate = lambda payload, messages: json.dumps(
            build_candidate(payload, drop_exceptions=True), ensure_ascii=False)
        batch = self._run_batch(self.scene_fee)
        self.assertEqual(batch["status"], "completed")
        landed = {}
        for cand in batch["candidates"]:
            self.assertEqual(cand["status"], "pending_review")  # 提示项不阻断进入审核
            detail = self.client.get(f"/api/skill-factory/skills/{cand['skill_id']}", headers=self.admin_headers).json()
            for u in detail["skill_json"]["unsupported_items"]:
                if u["kind"] == "例外未落点":
                    landed[u["value"]] = u["atom_version_id"]
            if any(u["kind"] == "例外未落点" for u in detail["skill_json"]["unsupported_items"]):
                self.assertTrue(any(i["code"] == "EXCEPTION_NOT_LANDED" for i in detail["issues"]))
        self.assertEqual(landed, {
            "当期应收为零时不计算收缴率": self.atoms["rate"]["version_id"],
            "存在物业服务争议的欠费另行处理": self.atoms["grade"]["version_id"],
        })

    def test_08_model_failure_and_timeout_retry(self):
        state = {"t1": 0}

        def generate(payload, messages):
            name = payload["task"]["name"]
            if name == "核对指定期间收缴率":
                state["t1"] += 1
                if state["t1"] == 1:
                    raise TimeoutError("timed out")  # 第一次超时，第二次成功
                return json.dumps(build_candidate(payload), ensure_ascii=False)
            raise RuntimeError("DeepSeek 服务调用失败 (已重试 1 次): HTTP 503")  # 一直失败

        self.model.generate = generate
        batch = self._run_batch(self.scene_fee)
        self.assertEqual(batch["status"], "partial", batch.get("status_reason"))
        results = {r["task_key"]: r for r in batch["task_results"]}
        self.assertEqual(results["t1"]["status"], "stored")
        self.assertEqual(results["t2"]["status"], "call_failed")
        gens = {t["task_key"]: t for t in self._tasks(batch["batch_id"], "generate_skill")}
        self.assertEqual(gens["t1"]["attempt_count"], 2)
        self.assertEqual(gens["t1"]["status"], "completed")
        t1_calls = json.loads(gens["t1"]["model_calls_json"])
        self.assertEqual([c["ok"] for c in t1_calls], [False, True])
        self.assertIn("TimeoutError", t1_calls[0]["error"])
        self.assertEqual(gens["t2"]["status"], "failed")
        self.assertEqual(gens["t2"]["attempt_count"], config_attempts())
        self.assertEqual(len(self._skill_rows(batch["batch_id"])), 1)

    def test_09_split_empty_no_tasks(self):
        self.model.split = lambda payload, messages: json.dumps(
            {"tasks": [], "unused_atoms": [], "no_task_reason": "这些知识都是背景说明，构不成可测试的任务"},
            ensure_ascii=False)
        batch = self._run_batch(self.scene_fee)
        self.assertEqual(batch["status"], "no_tasks")
        self.assertEqual(batch["status_reason"], "这些知识都是背景说明，构不成可测试的任务")
        self.assertEqual(self.model.count("generate"), 0)
        self.assertEqual(batch["candidates"], [])
        self.assertEqual(batch["stages"][2]["status"], "skipped")

    def test_10_split_program_checks(self):
        def split(payload, messages):
            ids = [a["atom_version_id"] for a in payload["atom_pool"] if a.get("recall_source") == "tag"]
            same = ids[:3]
            tasks = [
                {"name": "任务一", "goal": "目标一", "task_type": "判断分级", "atom_version_ids": same + ["kv_fake_x"],
                 "split_reason": "理由"},
                {"name": "任务二", "goal": "目标二", "task_type": "流程指引", "atom_version_ids": same,
                 "split_reason": "理由"},
                {"name": "任务三", "goal": "目标三", "task_type": "流程指引", "atom_version_ids": ids[:1] + ["kv_fake_y"],
                 "split_reason": "理由"},
            ] + [
                {"name": f"任务{i}", "goal": "目标", "task_type": "流程指引", "atom_version_ids": ids[1:4],
                 "split_reason": "理由"} for i in range(4, 8)
            ]
            return json.dumps({"tasks": tasks, "unused_atoms": [
                {"atom_version_id": ids[0], "reason": "已被使用，不应出现在未使用列表"},
                {"atom_version_id": "kv_fake_z", "reason": "池外"}]}, ensure_ascii=False)

        self.model.split = split
        batch = self._run_batch(self.scene_fee)
        tasks = {t["task_key"]: t for t in batch["task_split"]["tasks"]}
        self.assertEqual(tasks["t1"]["out_of_pool_ids"], ["kv_fake_x"])
        self.assertEqual(tasks["t1"]["status"], "to_generate")
        self.assertEqual([d["task_key"] for d in tasks["t1"]["possible_duplicate_of"]], ["t2"])
        self.assertEqual(tasks["t3"]["status"], "skipped")        # 只剩 1 条原子
        self.assertEqual(tasks["t6"]["status"], "skipped")        # 超过 5 个任务上限
        self.assertIn("5 个任务上限", tasks["t6"]["skip_reason"])
        self.assertEqual(batch["task_split"]["generate_count"], 4)  # t1 t2 t4 t5
        unused_ids = {u["atom_version_id"] for u in batch["task_split"]["unused_atoms"]}
        self.assertNotIn("kv_fake_z", unused_ids)
        self.assertEqual(self.model.count("generate", repair=False), 4)
        skipped = [r for r in batch["task_results"] if r["status"] == "skipped"]
        self.assertEqual(len(skipped), 3)

    def test_11_split_invalid_format_retry_then_fail(self):
        self.model.split = lambda payload, messages: "这不是 JSON"
        batch = self._run_batch(self.scene_fee)
        self.assertEqual(batch["status"], "failed")
        self.assertEqual(self.model.count("split"), 2)  # 首次 + 带错误清单重试一次
        split_task = self._tasks(batch["batch_id"], "split_tasks")[0]
        self.assertEqual(split_task["status"], "failed")
        self.assertEqual(len(json.loads(split_task["model_calls_json"])), 2)

    def test_12_atom_disabled_during_generation_rechecked(self):
        """6.3：生成过程中原子被停用，G5 按 R1 复查，自动修复后仍引用则校验未通过。"""
        target = "r1_1"

        def generate(payload, messages):
            if len(messages) > 2:
                return messages[2]["content"]  # 修复轮仍原样返回上次候选（模拟模型没有改正）
            self._set_lifecycle(target, "disabled")  # 模拟生成期间管理员停用了该原子
            return json.dumps(build_candidate(payload), ensure_ascii=False)

        self.model.split = self._patrol_split
        self.model.generate = generate
        try:
            batch = self._run_batch(self.scene_patrol)
        finally:
            self._set_lifecycle(target, "active")
        self.assertEqual(batch["status"], "failed")
        row = self._skill_rows(batch["batch_id"])[0]
        self.assertEqual(row["status"], "validation_failed")
        codes = {i["code"] for i in json.loads(row["generation_error_json"])["issues"]}
        self.assertIn("REF_NOT_ELIGIBLE", codes)
        # 修复轮的原子清单已排除被停用的原子（生成前再次过资格）
        repair_call = next(c for c in self.model.calls if c["repair"])
        allowed = _user_payload(repair_call["messages"])["allowed_atom_version_ids"]
        self.assertNotIn(self.atoms[target]["version_id"], allowed)

    @staticmethod
    def _patrol_split(payload, messages):
        ids = [a["atom_version_id"] for a in payload["atom_pool"]]
        return json.dumps({"tasks": [{"name": "巡检核对", "goal": "核对巡检", "task_type": "流程指引",
                                      "atom_version_ids": ids, "split_reason": "巡检"}]}, ensure_ascii=False)

    def test_12b_atom_disabled_then_repaired_without_it(self):
        target = "r1_2"

        def generate(payload, messages):
            if len(messages) == 2:
                self._set_lifecycle(target, "disabled")
            return json.dumps(build_candidate(payload), ensure_ascii=False)  # 修复轮按新的原子清单生成

        self.model.split = self._patrol_split
        self.model.generate = generate
        try:
            batch = self._run_batch(self.scene_patrol)
        finally:
            self._set_lifecycle(target, "active")
        self.assertEqual(batch["status"], "completed")
        row = self._skill_rows(batch["batch_id"])[0]
        with get_db() as conn:
            refs = {r["atom_version_id"] for r in conn.execute(
                "SELECT atom_version_id FROM skill_atom_refs WHERE skill_version_id = ?",
                (row["current_version_id"],)).fetchall()}
        self.assertNotIn(self.atoms[target]["version_id"], refs)
        self.assertEqual(batch["task_results"][0]["repair_count"], 1)

    def test_13_recover_interrupted_batch(self):
        with get_db() as conn:
            started = sg.start_generation_batch(conn, self.actor, self.scene_fee["scene_id"])
            conn.execute("UPDATE skill_generation_tasks SET status = 'running', attempt_count = 1 WHERE id = ?",
                         (started["task_id"],))
        resubmitted = sg.recover_generation_tasks()
        self.assertIn(started["task_id"], resubmitted)
        batch = self._wait_batch(started["batch_id"])
        self.assertEqual(batch["status"], "completed")

    def test_14_member_forbidden_and_org_isolation(self):
        self.assertEqual(self._start(self.scene_fee, headers=self.member_headers).status_code, 403)
        self.assertEqual(self.client.get("/api/skill-factory/batches", headers=self.member_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/skill-factory/scenes/x/batches").status_code, 401)
        batch = self._run_batch(self.scene_fee)
        self.assertEqual(self._start(self.scene_fee, headers=self.other_headers).status_code, 404)
        self.assertEqual(self.client.get(f"/api/skill-factory/batches/{batch['batch_id']}",
                                         headers=self.other_headers).status_code, 404)
        skill_id = batch["candidates"][0]["skill_id"]
        self.assertEqual(self.client.get(f"/api/skill-factory/skills/{skill_id}",
                                         headers=self.other_headers).status_code, 404)
        other_list = self.client.get("/api/skill-factory/batches", headers=self.other_headers).json()
        self.assertEqual(other_list["total"], 0)

    def test_15_no_model_config_rejected(self):
        with patch.object(config, "DEEPSEEK_API_KEY", ""):
            resp = self._start(self.scene_fee)
            cards = self.client.get("/api/skill-factory/scenes/cards", headers=self.admin_headers).json()
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(cards["generation_available"])

    def test_16_regenerate_reserved_function(self):
        batch = self._run_batch(self.scene_fee)
        skill_id = batch["candidates"][0]["skill_id"]
        with get_db() as conn:
            v1 = dict(conn.execute("SELECT * FROM skill_versions WHERE skill_id = ?", (skill_id,)).fetchone())

        def generate(payload, messages):
            self.assertEqual(payload["review_comment"], "请补充不适用范围")
            self.assertIn("previous_version", payload)
            candidate = build_candidate(payload)
            candidate["not_applies_to"] = ["收缴率下降原因分析属于另一任务"]
            return json.dumps(candidate, ensure_ascii=False)

        self.model.generate = generate
        with self.assertRaises(sc.SceneCatalogError):
            sg.regenerate_skill_version(skill_id, self.actor, "  ")
        result = sg.regenerate_skill_version(skill_id, self.actor, "请补充不适用范围", ["A"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["version_number"], 2)
        with get_db() as conn:
            versions = conn.execute("SELECT * FROM skill_versions WHERE skill_id = ? ORDER BY version_number",
                                    (skill_id,)).fetchall()
            skill = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        self.assertEqual([v["version_kind"] for v in versions], ["ai_original", "ai_regenerated"])
        self.assertEqual(versions[0]["skill_json"], v1["skill_json"])  # 原稿不变
        self.assertEqual(versions[1]["based_on_version_id"], v1["id"])
        self.assertEqual(skill["current_version_id"], versions[1]["id"])
        self.assertEqual(skill["status"], "pending_review")
        self.assertEqual(json.loads(versions[1]["skill_json"])["not_applies_to"], ["收缴率下降原因分析属于另一任务"])


    def test_17_full_output_is_validated_and_audit_is_bounded(self):
        long_goal = "真实输入中的详细业务目标" * 4000

        def generate(payload, messages):
            candidate = build_candidate(payload)
            candidate["goal"] = long_goal
            return json.dumps(candidate, ensure_ascii=False)

        self.model.generate = generate
        batch = self._run_batch(self.scene_fee)
        self.assertEqual(batch["status"], "completed")
        for task in self._tasks(batch["batch_id"], "generate_skill"):
            result = json.loads(task["result_json"])
            calls = json.loads(task["model_calls_json"])
            self.assertGreater(len(result["raw_response"]), sg.RAW_RESPONSE_KEEP)
            self.assertEqual(len(calls[-1]["raw_response"]), sg.RAW_RESPONSE_KEEP)
            self.assertEqual(json.loads(sg._generation_raw(result, calls))["goal"], long_goal)
        with get_db() as conn:
            for row in conn.execute("SELECT sv.skill_json FROM skill_versions sv JOIN skills s ON s.current_version_id = sv.id WHERE s.batch_id = ?",
                                    (batch["batch_id"],)).fetchall():
                self.assertEqual(json.loads(row["skill_json"])["goal"], long_goal)

    def test_18_repair_links_output_and_reads_legacy_payload(self):
        def generate(payload, messages):
            candidate = build_candidate(payload)
            if len(messages) == 2:
                candidate["steps"][1]["refs"] = ["kv_outside"]
            return json.dumps(candidate, ensure_ascii=False)

        self.model.generate = generate
        batch = self._run_batch(self.scene_fee)
        self.assertEqual(batch["status"], "completed")
        tasks = self._tasks(batch["batch_id"], "generate_skill")
        for task in tasks:
            result, calls = json.loads(task["result_json"]), json.loads(task["model_calls_json"])
            self.assertNotIn("raw_response", result)
            self.assertEqual(sg._generation_raw(result, calls), calls[result["raw_response_call"]]["raw_response"])
            payload = json.loads(task["payload_json"])
            if payload.get("mode") == "repair":
                self.assertIn("previous_generate_task_id", payload)
                self.assertNotIn("previous_raw", payload)
        self.assertEqual(sg._repair_previous_raw(None, {"previous_raw": "legacy output"}), "legacy output")
        self.assertEqual(sg._generation_raw({"raw_response": "legacy output"}), "legacy output")


class TestM02CPoolQuota(unittest.TestCase):
    """原子池名额（2026-09-27 用户确认）：上限 60，内容相关保留 10 个名额；标签超出时按相关度、新近程度截取。"""

    def test_quota_and_ordering(self):
        def entry(i, title):
            return {"item_id": f"ki{i}", "version_id": f"kv{i}", "title": title, "primary_category": "制度与标准",
                    "atom_type": "规则", "statement": title, "business_scenes": ["大量标签"]}

        tag_rows = [{"version_id": f"kv{i}", "item_id": f"ki{i}", "title": f"标签知识{i}", "primary_category": "制度与标准",
                     "atom_type": "规则", "statement": "x", "business_scenes_json": '["大量标签"]'} for i in range(1, 6)]
        scene = {"scene_id": "s", "name": "大量标签", "description": "说明", "aliases": [], "typical_problems": []}
        hits = [dict(entry(2, "标签知识2"), relevance_score=0.7), dict(entry(4, "标签知识4"), relevance_score=0.9),
                dict(entry(9, "内容相关9"), relevance_score=0.6)]
        queries = []

        def search(conn, user, query, now_iso=None):
            queries.append(query)
            return hits

        with patch.object(sc, "_eligible_atom_rows", return_value=tag_rows),              patch.object(sc, "filter_eligible_version_ids", side_effect=lambda c, ids, u, n=None: list(ids)):
            r = sc.recall_atoms(None, {}, scene, pool_limit=3, semantic_reserved=1, search_fn=search,
                                focus_note="侧重夜间")
            self.assertEqual([a["version_id"] for a in r["atoms"]], ["kv4", "kv2", "kv9"])
            self.assertEqual(r["stats"]["tag_truncated"], 3)
            self.assertTrue(queries[0].startswith("侧重夜间"))
            # 没有内容相关原子时，名额全部给标签，按新近程度（后确认的在前）
            r2 = sc.recall_atoms(None, {}, scene, pool_limit=2, search_fn=lambda *a, **k: [])
            self.assertEqual([a["version_id"] for a in r2["atoms"]], ["kv5", "kv4"])
            self.assertNotIn("_seq", r2["atoms"][0])
        self.assertEqual((sc.SCENE_RECALL_POOL_LIMIT, sc.SCENE_SEMANTIC_RESERVED), (60, 10))

    def test_cards_share_atom_rows_and_counts_but_keep_semantic_queries(self):
        rows = [{"version_id": "kv1", "item_id": "ki1", "title": "共用知识", "primary_category": "制度与标准",
                 "atom_type": "规则", "statement": "x", "business_scenes_json": '["场景甲", "场景乙"]'}]
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [
            {"scene_id": "s1", "status": "approved", "n": 2},
            {"scene_id": "s1", "status": "rejected", "n": 1},
        ]
        scenes = [{"scene_id": sid, "name": name, "description": "", "aliases": [], "typical_problems": []}
                  for sid, name in (("s1", "场景甲"), ("s2", "场景乙"))]
        queries = []
        def search(conn, user, query, now_iso=None):
            queries.append(query)
            return []
        with patch.object(sc, "_eligible_atom_rows", return_value=rows) as read_atoms, \
             patch.object(sc, "filter_eligible_version_ids", side_effect=lambda c, ids, u, n=None: list(ids)), \
             patch.object(hybrid_retrieval, "hybrid_search", side_effect=search), \
             patch.object(sc, "count_scene_skills", side_effect=AssertionError("counts must be shared")):
            context = sc.build_scene_card_context(conn, {"organization_id": "org"}, "now")
            cards = [sc.build_scene_card(conn, {"organization_id": "org"}, scene, "now", context) for scene in scenes]
        read_atoms.assert_called_once()
        conn.execute.assert_called_once()
        self.assertEqual(queries, ["场景甲", "场景乙"])
        self.assertEqual([c["available_atom_count"] for c in cards], [1, 1])
        self.assertEqual(cards[0]["skill_counts"], {"pending_review": 0, "approved": 2, "needs_recheck": 0, "total": 3})
        self.assertEqual(cards[1]["skill_counts"]["total"], 0)


def config_attempts():
    from skill_constants import SKILL_MODEL_CALL_MAX_ATTEMPTS
    return SKILL_MODEL_CALL_MAX_ATTEMPTS


if __name__ == "__main__":
    unittest.main(verbosity=2)
