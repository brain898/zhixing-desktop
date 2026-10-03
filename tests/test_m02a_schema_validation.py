"""
M02-A 自动化测试：Skill Schema v1、结构与引用校验器、M02 数据表

依据 0926-Skill工厂模块PRD：
- 第 3 章 Schema（3.3~3.10）、第 4 章 R1/R3/R4/R6、4.3 完整性提示、FR07 校验分级、FR13 稳定路径。
- 全部在隔离的临时数据目录与临时数据库中运行，不读写 server/data/zhixing.db。
- 本测试不调用大模型，也不使用模拟模型返回。
"""

import copy
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

PRD_PATH = Path(__file__).resolve().parents[3] / "02-方案" / "0926-Skill工厂模块PRD.md"

# PRD 3.10 设计示例（原样）
PRD_EXAMPLE = {
    "schema_version": "1.0",
    "name": "巡检设备异常分级处置",
    "goal": "判断巡检发现的设备异常应走普通工单还是应急流程，并给出处置动作",
    "trigger_description": "用户描述巡检中发现设备异常，询问如何处理",
    "task_type": "判断分级",
    "scene_id": "scene_equipment_inspection",
    "applies_to": {"customer_types": ["住宅业主"], "property_types": ["住宅"], "conditions": []},
    "not_applies_to": ["TBD_EXPERT"],
    "knowledge_refs": [
        {"atom_item_id": "ki_a", "atom_version_id": "kv_a2", "role": "判断规则", "used_in_steps": ["s2"]},
        {"atom_item_id": "ki_b", "atom_version_id": "kv_b1", "role": "执行动作", "used_in_steps": ["s3"]},
        {"atom_item_id": "ki_c", "atom_version_id": "kv_c1", "role": "例外处理", "used_in_steps": ["s2"]},
    ],
    "inputs": [
        {"key": "anomaly_desc", "label": "异常描述", "type": "text", "required": True},
        {"key": "safety_risk", "label": "是否涉及人身安全", "type": "boolean", "required": True},
    ],
    "preconditions": [{"text": "异常已由巡检人员现场确认", "ref": "kv_a2"}],
    "steps": [
        {"step_id": "s1", "kind": "输入校验", "action": "检查必需输入是否齐全", "refs": [], "basis": "通用操作", "on_fail": "补问"},
        {"step_id": "s2", "kind": "规则判断", "condition": "safety_risk == true", "action": "转入应急流程，不等待普通工单", "refs": ["kv_a2", "kv_c1"], "basis": "有原子依据", "on_fail": "转人工"},
        {"step_id": "s3", "kind": "规则判断", "condition": "safety_risk == false", "action": "登记工单并同步通知责任人", "refs": ["kv_b1"], "basis": "有原子依据", "on_fail": "暂停"},
        {"step_id": "s4", "kind": "生成表达", "action": "30 分钟内回访确认处置结果", "refs": [], "basis": "无依据", "on_fail": "暂停"},
    ],
    "outputs": [{"key": "route", "label": "处置路径", "type": "enum", "allowed_values": ["应急流程", "普通工单"], "required": True}],
    "risk_boundary": ["不替代现场人员对安全风险的判断"],
    "escalation_conditions": ["无法判断是否涉及人身安全时转人工"],
    "generation_confidence": {"level": "中", "reason": "s4 的回访时限在引用原子中未出现"},
}

INDEX_PATTERN = re.compile(r"\[\d+\]")


def remap_ids(obj, mapping):
    """把示例中的占位 ID 精确替换为夹具中的真实 ID。"""
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        return [remap_ids(v, mapping) for v in obj]
    if isinstance(obj, dict):
        return {k: remap_ids(v, mapping) for k, v in obj.items()}
    return obj


class TestM02ASchemaValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_data_dir = os.environ.get("ZHIXING_DATA_DIR")
        cls.data_dir = Path(tempfile.mkdtemp(prefix="zhixing_m02a_data_"))
        os.environ["ZHIXING_DATA_DIR"] = str(cls.data_dir)

        from test_env_helper import setup_test_db
        cls.test_db = setup_test_db("m02a_tests")

        from database import get_db
        cls.get_db = staticmethod(get_db)
        with get_db() as conn:
            cls.admin_user = dict(conn.execute("SELECT * FROM users WHERE id = 'usr_admin_001'").fetchone())
            cls.other_user = dict(conn.execute("SELECT * FROM users WHERE id = 'usr_other_001'").fetchone())
        cls.org_id = cls.admin_user["organization_id"]

        # PRD 3.10 示例所需三条原子（内容为机制演示构造，沿用 M01 PRD 5.6 示例）
        doc_id, dv_id = cls._create_document("巡检规程", "org_internal")
        cls.atom_a = cls._create_atom(
            doc_id, dv_id, title="设备巡检异常处置", category="制度与标准", atom_type="规则",
            statement="住宅项目设备巡检发现异常时登记工单；涉及人身安全风险时优先按应急流程处置，不得等待普通工单流转。",
            conditions=["住宅项目设备巡检发现异常，异常已由巡检人员现场确认"],
            actions=["登记工单"],
            exceptions=["涉及人身安全风险时优先按应急流程处置，不得等待普通工单流转"],
        )
        cls.atom_b = cls._create_atom(
            doc_id, dv_id, title="非安全类异常工单登记", category="方法与工具", atom_type="方法",
            statement="不涉及人身安全的设备异常登记工单并同步通知责任人。",
            actions=["登记工单并同步通知责任人"],
        )
        cls.atom_c = cls._create_atom(
            doc_id, dv_id, title="安全风险优先应急", category="专家经验", atom_type="经验",
            statement="涉及人身安全风险时直接转入应急流程，不等待普通工单。",
            exceptions=["涉及人身安全风险时转入应急流程，不等待普通工单"],
        )
        cls.id_map = {
            "ki_a": cls.atom_a[0], "kv_a2": cls.atom_a[1],
            "ki_b": cls.atom_b[0], "kv_b1": cls.atom_b[1],
            "ki_c": cls.atom_c[0], "kv_c1": cls.atom_c[1],
        }
        cls.pool = [cls.atom_a[1], cls.atom_b[1], cls.atom_c[1]]

    @classmethod
    def tearDownClass(cls):
        from test_env_helper import cleanup_test_db
        cleanup_test_db(cls.test_db)
        shutil.rmtree(cls.data_dir, ignore_errors=True)
        if cls._old_data_dir is None:
            os.environ.pop("ZHIXING_DATA_DIR", None)
        else:
            os.environ["ZHIXING_DATA_DIR"] = cls._old_data_dir

    # ------------------------------------------------------------------
    # 夹具
    # ------------------------------------------------------------------

    @classmethod
    def _create_document(cls, title, access_scope, org_id=None):
        org_id = org_id or cls.org_id
        now = datetime.now(timezone.utc).isoformat()
        doc_id = f"doc_m02a_{uuid.uuid4().hex[:8]}"
        dv_id = f"dv_m02a_{uuid.uuid4().hex[:8]}"
        with cls.get_db() as conn:
            conn.execute(
                "INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted, created_at, updated_at) "
                "VALUES (?, ?, ?, NULL, ?, 0, ?, ?)",
                (doc_id, org_id, title, access_scope, now, now),
            )
            conn.execute(
                "INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name, file_type, file_size, "
                "content_hash, storage_reference, uploaded_by, uploaded_at, processing_status) "
                "VALUES (?, ?, ?, 'v1', ?, 'md', 100, ?, 'fixture.md', 'usr_admin_001', ?, 'completed')",
                (dv_id, doc_id, org_id, f"{title}.md", uuid.uuid4().hex, now),
            )
            conn.execute("UPDATE documents SET active_version_id = ? WHERE id = ?", (dv_id, doc_id))
        return doc_id, dv_id

    @classmethod
    def _create_atom(cls, doc_id, dv_id, *, title, category, atom_type, statement,
                     conditions=None, actions=None, exceptions=None, metric=None,
                     access_scope=None, lifecycle="active", review="confirmed",
                     excluded=0, valid_until=None, org_id=None, deleted=False):
        org_id = org_id or cls.org_id
        now = datetime.now(timezone.utc).isoformat()
        item_id = f"ki_m02a_{uuid.uuid4().hex[:8]}"
        ver_id = f"kv_m02a_{uuid.uuid4().hex[:8]}"
        with cls.get_db() as conn:
            if access_scope is None:
                access_scope = conn.execute("SELECT access_scope FROM documents WHERE id = ?", (doc_id,)).fetchone()[0]
            conn.execute(
                "INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope, lifecycle_status, "
                "is_excluded, deleted_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, doc_id, org_id, ver_id, access_scope, lifecycle, excluded, now if deleted else None, now, now),
            )
            conn.execute(
                "INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id, version_number, title, content, "
                "primary_category, atom_type, subject, statement, conditions_json, actions_json, exceptions_json, metric_definition_json, "
                "valid_until, review_status, index_status, revision_token, created_at, created_by) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, '测试主体', ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, 'usr_admin_001')",
                (
                    ver_id, item_id, org_id, dv_id, title, statement, category, atom_type, statement,
                    json.dumps(conditions or [], ensure_ascii=False),
                    json.dumps(actions or [], ensure_ascii=False),
                    json.dumps(exceptions or [], ensure_ascii=False),
                    json.dumps(metric, ensure_ascii=False) if metric is not None else None,
                    valid_until, review, uuid.uuid4().hex, now,
                ),
            )
        return item_id, ver_id

    def example(self):
        return remap_ids(copy.deepcopy(PRD_EXAMPLE), self.id_map)

    def validate(self, skill, **kwargs):
        from skill_validation import validate_skill
        kwargs.setdefault("pool_ids", self.pool)
        with self.get_db() as conn:
            before = conn.total_changes
            report = validate_skill(conn, skill, kwargs.pop("user", self.admin_user), **kwargs)
            self.assertEqual(conn.total_changes, before, "校验器不得写数据库")
        return report

    def codes(self, report, level=None):
        return {(i.code, i.path) for i in report.issues if level is None or i.level == level}

    def step(self, skill, step_id):
        return next(s for s in skill["steps"] if s["step_id"] == step_id)

    def ref(self, skill, version_id):
        return next(r for r in skill["knowledge_refs"] if r["atom_version_id"] == version_id)

    # ------------------------------------------------------------------
    # 1. Schema 文件与 PRD 示例
    # ------------------------------------------------------------------

    def test_01_schema_file_matches_prd_field_definitions(self):
        from skill_validation import load_skill_schema
        schema = load_skill_schema()
        props = schema["properties"]
        self.assertEqual(props["schema_version"]["const"], "1.0")
        self.assertEqual(props["task_type"]["enum"], ["计算核对", "判断分级", "流程指引", "诊断建议"])
        defs = schema["$defs"]
        self.assertEqual(defs["knowledgeRef"]["properties"]["role"]["enum"],
                         ["前置条件", "判断规则", "执行动作", "例外处理", "指标口径", "案例参考"])
        self.assertEqual(defs["ioField"]["properties"]["type"]["enum"],
                         ["text", "number", "boolean", "enum", "date", "period", "file"])
        step = defs["step"]["properties"]
        self.assertEqual(step["kind"]["enum"], ["输入校验", "知识检索", "计算", "规则判断", "生成表达", "人工确认"])
        self.assertEqual(step["basis"]["enum"], ["有原子依据", "无依据", "通用操作", "专家补充"])
        self.assertEqual(step["on_fail"]["enum"], ["补问", "暂停", "转人工", "返回不可计算"])
        self.assertEqual(props["generation_confidence"]["properties"]["level"]["enum"], ["高", "中", "低"])
        self.assertEqual(props["status"]["enum"][:-1], ["生成中", "校验未通过", "待审核", "已通过（待测试）", "已驳回", "待复核"])
        self.assertEqual(props["tools"]["items"]["properties"]["implementation_status"]["enum"], ["未实现", "已实现", "已验证"])
        tbd_fields = sorted(k for k, v in props.items() if v.get("x-allow-tbd"))
        self.assertEqual(tbd_fields, ["escalation_conditions", "not_applies_to", "risk_boundary"])
        # A~E 必填、F/G 允许为空
        groups = {k: v.get("x-group") for k, v in props.items()}
        for key in schema["required"]:
            self.assertIn(groups[key], {"A", "B", "C", "D", "E", "F"}, key)
        for key, group in groups.items():
            if group == "G" or (group == "F" and key != "schema_version"):
                self.assertNotIn(key, schema["required"], key)

    def test_02_prd_example_passes_structure_validation(self):
        from skill_validation import validate_structure
        self.assertEqual([i.to_dict() for i in validate_structure(PRD_EXAMPLE)], [])

    @unittest.skipUnless(PRD_PATH.exists(), "PRD 文件不在预期位置")
    def test_03_prd_document_example_is_the_tested_example(self):
        from skill_validation import validate_structure
        text = PRD_PATH.read_text(encoding="utf-8")
        block = re.search(r"### 3\.10.*?```json\n(.*?)```", text, re.S).group(1)
        example = json.loads(block)
        self.assertEqual(example, PRD_EXAMPLE)
        self.assertEqual(validate_structure(example), [])

    def test_04_schema_is_valid_draft_2020_12_when_jsonschema_available(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("当前运行时未安装 jsonschema（便携包不依赖它）")
        from skill_validation import load_skill_schema
        schema = load_skill_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        self.assertEqual(list(jsonschema.Draft202012Validator(schema).iter_errors(PRD_EXAMPLE)), [])

    def test_05_prd_example_passes_reference_validation_with_real_atoms(self):
        report = self.validate(self.example())
        self.assertTrue(report.passed, [i.to_dict() for i in report.hard_errors])
        # PRD 3.10：s4 汇总出两条无依据项（步骤无依据 + 「30 分钟」数值）
        kinds = sorted((u["kind"], u["path"]) for u in report.unsupported_items)
        self.assertEqual(kinds, [("无依据步骤", "steps[s4]"), ("疑似无依据数值", "steps[s4].action")])
        self.assertIn(("R6_UNSUPPORTED_NUMBER", "steps[s4].action"), self.codes(report, "hint"))
        self.assertNotIn("EXCEPTION_NOT_LANDED", {i.code for i in report.issues})
        self.assertNotIn("ROLE_DEFAULT_MISMATCH", {i.code for i in report.issues})
        self.assertEqual(report.visibility, "org_internal")
        # 报告格式：每项含 level/code/path/message
        data = report.to_dict()
        self.assertTrue(data["passed"])
        for issue in data["issues"]:
            self.assertEqual({"level", "code", "path", "message"} - set(issue), set())
            self.assertIn(issue["level"], ("hard_error", "hint"))

    def test_06_derived_fields_written_and_still_schema_valid(self):
        from skill_validation import apply_derived_fields, validate_structure
        skill = self.example()
        report = self.validate(skill)
        derived = apply_derived_fields(skill, report)
        self.assertEqual(derived["visibility"], "org_internal")
        self.assertEqual(len(derived["unsupported_items"]), 2)
        self.assertNotIn("unsupported_items", skill, "原候选不被原地修改")
        self.assertEqual(validate_structure(derived), [])

    # ------------------------------------------------------------------
    # 2. 结构错误
    # ------------------------------------------------------------------

    def test_07_structure_errors_are_hard_errors(self):
        from skill_validation import validate_structure
        skill = copy.deepcopy(PRD_EXAMPLE)
        del skill["goal"]
        skill["task_type"] = "核对计算"
        skill["steps"][1]["kind"] = "判断"
        skill["knowledge_refs"][0]["role"] = "规则"
        skill["inputs"].append({"key": "amount", "label": "应收金额", "type": "number", "required": True})
        skill["inputs"].append({"key": "level", "label": "异常等级", "type": "enum", "required": True})
        skill["outputs"][0]["required"] = "是"
        skill["extra_field"] = 1
        found = {(i.code, i.path) for i in validate_structure(skill)}
        expected = {
            ("SCHEMA_REQUIRED", "goal"),
            ("SCHEMA_ENUM", "task_type"),
            ("SCHEMA_ENUM", "steps[s2].kind"),
            ("SCHEMA_ENUM", "knowledge_refs[kv_a2].role"),
            ("NUMBER_UNIT_MISSING", "inputs[amount].unit"),
            ("ENUM_ALLOWED_VALUES_MISSING", "inputs[level].allowed_values"),
            ("SCHEMA_TYPE", "outputs[route].required"),
            ("SCHEMA_ADDITIONAL_PROPERTY", "extra_field"),
        }
        self.assertEqual(found, expected)
        self.assertTrue(all(i.level == "hard_error" for i in validate_structure(skill)))

    def test_08_more_structure_rules(self):
        from skill_validation import validate_structure
        skill = copy.deepcopy(PRD_EXAMPLE)
        skill["applies_to"] = {"customer_types": [], "property_types": [], "conditions": []}
        skill["steps"] = skill["steps"][:1]
        skill["name"] = "这是一个超过二十个字的名称用于测试长度上限是否生效"
        skill["goal"] = "TBD_EXPERT"
        skill["risk_boundary"] = ["TBD_EXPERT"]
        skill["escalation_conditions"] = ["TBD_EXPERT"]
        skill["knowledge_refs"][1]["used_in_steps"] = []
        skill["inputs"][0]["label"] = "   "
        skill["inputs"].append({"key": "anomaly_desc", "label": "重复键", "type": "text", "required": False})
        skill["schema_version"] = "2.0"
        found = {(i.code, i.path) for i in validate_structure(skill)}
        self.assertEqual(found, {
            ("APPLIES_TO_EMPTY", "applies_to"),
            ("SCHEMA_MIN_ITEMS", "steps"),
            ("SCHEMA_MAX_LENGTH", "name"),
            ("TBD_NOT_ALLOWED", "goal"),
            ("USED_IN_STEPS_EMPTY", "knowledge_refs[kv_b1].used_in_steps"),
            ("SCHEMA_PATTERN", "inputs[anomaly_desc].label"),
            ("DUPLICATE_ID", "inputs[anomaly_desc]"),
            ("SCHEMA_CONST", "schema_version"),
        })
        # 案例参考允许 used_in_steps 为空；number 带单位、enum 带可选值则通过
        ok = copy.deepcopy(PRD_EXAMPLE)
        ok["knowledge_refs"][1]["role"] = "案例参考"
        ok["knowledge_refs"][1]["used_in_steps"] = []
        ok["inputs"].append({"key": "amount", "label": "应收金额", "type": "number", "required": True, "unit": "元"})
        ok["inputs"].append({"key": "level", "label": "等级", "type": "enum", "required": True, "allowed_values": ["一般", "紧急"]})
        self.assertEqual(validate_structure(ok), [])
        self.assertEqual([i.code for i in validate_structure([])], ["SCHEMA_TYPE"])

    # ------------------------------------------------------------------
    # 3. 引用校验 R1 与原子池
    # ------------------------------------------------------------------

    def _swap_b(self, skill, new_item_id, new_version_id):
        old_vid = self.atom_b[1]
        ref = self.ref(skill, old_vid)
        ref["atom_item_id"] = new_item_id
        ref["atom_version_id"] = new_version_id
        self.step(skill, "s3")["refs"] = [new_version_id]
        return skill

    def test_09_reference_to_missing_atom_is_hard_error(self):
        skill = self._swap_b(self.example(), "ki_missing", "kv_missing")
        report = self.validate(skill, pool_ids=self.pool + ["kv_missing"])
        self.assertFalse(report.passed)
        self.assertIn(("REF_ATOM_NOT_FOUND", "knowledge_refs[kv_missing]"), self.codes(report, "hard_error"))
        self.assertEqual(report.visibility, "admin_only", "引用无法解析时按最严格权限推导")

    def test_10_ineligible_atoms_are_hard_errors(self):
        doc_id, dv_id = self._create_document("资格夹具", "org_internal")
        base = dict(doc_id=doc_id, dv_id=dv_id, category="方法与工具", atom_type="方法",
                    statement="登记工单并同步通知责任人。", actions=["登记工单并同步通知责任人"])
        cases = {
            "已停用": (dict(lifecycle="disabled"), "ITEM_LIFECYCLE_NOT_ACTIVE"),
            "未确认": (dict(review="pending_review"), "REVIEW_NOT_CONFIRMED"),
            "已排除": (dict(excluded=1), "ITEM_EXCLUDED_OR_DELETED"),
            "已删除": (dict(lifecycle="deleted", deleted=True), "ITEM_LIFECYCLE_NOT_ACTIVE"),
            "已过期": (dict(valid_until="2020-01-01T00:00:00+00:00"), "EXPIRED"),
        }
        for label, (overrides, expected_code) in cases.items():
            with self.subTest(label=label):
                item_id, ver_id = self._create_atom(title=f"{label}原子", **base, **overrides)
                skill = self._swap_b(self.example(), item_id, ver_id)
                report = self.validate(skill, pool_ids=self.pool + [ver_id])
                hits = [i for i in report.hard_errors if i.code == "REF_NOT_ELIGIBLE"]
                self.assertEqual(len(hits), 1, [i.to_dict() for i in report.issues])
                self.assertEqual(hits[0].path, f"knowledge_refs[{ver_id}]")
                self.assertEqual(hits[0].detail["eligibility_code"], expected_code)

        # 非当前生效版本（历史版本）同样不能引用
        item_id, v1 = self._create_atom(title="历史版本原子", **base)
        v2 = f"kv_m02a_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        with self.get_db() as conn:
            conn.execute(
                "INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id, version_number, title, content, "
                "primary_category, atom_type, statement, review_status, index_status, revision_token, created_at, created_by) "
                "VALUES (?, ?, ?, ?, 2, '历史版本原子', '新版', '方法与工具', '方法', '新版', 'confirmed', 'ready', ?, ?, 'usr_admin_001')",
                (v2, item_id, self.org_id, dv_id, uuid.uuid4().hex, now),
            )
            conn.execute("UPDATE knowledge_items SET active_version_id = ? WHERE id = ?", (v2, item_id))
        report = self.validate(self._swap_b(self.example(), item_id, v1), pool_ids=self.pool + [v1])
        self.assertEqual([i.detail["eligibility_code"] for i in report.hard_errors if i.code == "REF_NOT_ELIGIBLE"],
                         ["NOT_ACTIVE_ITEM_VERSION"])

    def test_11_reference_outside_pool_is_hard_error(self):
        report = self.validate(self.example(), pool_ids=[self.atom_a[1], self.atom_b[1]])
        self.assertIn(("REF_NOT_IN_POOL", f"knowledge_refs[{self.atom_c[1]}]"), self.codes(report, "hard_error"))
        # 不提供 pool_ids 时不做池检查
        report = self.validate(self.example(), pool_ids=None)
        self.assertTrue(report.passed)

    def test_12_other_organization_atom_is_not_found(self):
        doc_id, dv_id = self._create_document("他企业资料", "org_internal", org_id="org_other")
        item_id, ver_id = self._create_atom(
            doc_id, dv_id, title="他企业原子", category="方法与工具", atom_type="方法",
            statement="登记工单。", org_id="org_other",
        )
        report = self.validate(self._swap_b(self.example(), item_id, ver_id), pool_ids=None)
        self.assertIn(("REF_ATOM_NOT_FOUND", f"knowledge_refs[{ver_id}]"), self.codes(report, "hard_error"))

    def test_13_item_version_mismatch_and_undeclared_refs(self):
        skill = self.example()
        self.ref(skill, self.atom_b[1])["atom_item_id"] = self.atom_a[0]
        skill["preconditions"].append({"text": "工单系统可用", "ref": "kv_not_declared"})
        report = self.validate(skill)
        codes = self.codes(report, "hard_error")
        self.assertIn(("REF_ITEM_MISMATCH", f"knowledge_refs[{self.atom_b[1]}].atom_item_id"), codes)
        self.assertTrue(any(c == "REF_NOT_DECLARED" and p.startswith("preconditions[~") for c, p in codes), codes)

    # ------------------------------------------------------------------
    # 4. R3 步骤依据与 used_in_steps 一致性
    # ------------------------------------------------------------------

    def test_14_rule_or_calculation_step_marked_generic_is_hard_error(self):
        skill = self.example()
        s2 = self.step(skill, "s2")
        s2["basis"] = "通用操作"
        report = self.validate(skill)
        self.assertIn(("R3_GENERIC_KIND_INVALID", "steps[s2].basis"), self.codes(report, "hard_error"))

        skill = self.example()
        s4 = self.step(skill, "s4")
        s4["kind"] = "计算"
        s4["basis"] = "通用操作"
        report = self.validate(skill)
        self.assertIn(("R3_GENERIC_KIND_INVALID", "steps[s4].basis"), self.codes(report, "hard_error"))

        skill = self.example()
        self.step(skill, "s3")["refs"] = []
        self.ref(skill, self.atom_b[1])["role"] = "案例参考"
        self.ref(skill, self.atom_b[1])["used_in_steps"] = []
        report = self.validate(skill)
        self.assertIn(("R3_REFS_MISSING", "steps[s3].refs"), self.codes(report, "hard_error"))

    def test_15_expert_supplement_requires_reason(self):
        skill = self.example()
        s4 = self.step(skill, "s4")
        s4["basis"] = "专家补充"
        report = self.validate(skill, stage="review")
        self.assertIn(("R3_EXPERT_REASON_MISSING", "steps[s4].expert_reason"), self.codes(report, "hard_error"))

        s4["expert_reason"] = "   "
        report = self.validate(skill, stage="review")
        self.assertIn(("R3_EXPERT_REASON_MISSING", "steps[s4].expert_reason"), self.codes(report, "hard_error"))

        s4["expert_reason"] = "项目回访惯例，由运营主管确认"
        report = self.validate(skill, stage="review")
        self.assertTrue(report.passed, [i.to_dict() for i in report.hard_errors])
        # 专家补充的步骤不再计入无依据项
        self.assertEqual(report.unsupported_items, [])

        # 生成阶段模型不得自行标「专家补充」
        report = self.validate(skill, stage="generation")
        self.assertIn(("R3_EXPERT_BASIS_NOT_ALLOWED", "steps[s4].basis"), self.codes(report, "hard_error"))

    def test_16_used_in_steps_must_match_step_refs(self):
        skill = self.example()
        self.ref(skill, self.atom_b[1])["used_in_steps"] = ["s2"]
        report = self.validate(skill)
        codes = self.codes(report, "hard_error")
        self.assertIn(("USED_IN_STEPS_MISMATCH", f"knowledge_refs[{self.atom_b[1]}].used_in_steps"), codes)
        self.assertIn(("USED_IN_STEPS_MISMATCH", "steps[s3].refs"), codes)

        skill = self.example()
        self.ref(skill, self.atom_c[1])["used_in_steps"] = ["s2", "s9"]
        report = self.validate(skill)
        self.assertIn(("USED_IN_UNKNOWN_STEP", f"knowledge_refs[{self.atom_c[1]}].used_in_steps"), self.codes(report, "hard_error"))

        skill = self.example()
        self.step(skill, "s1")["refs"] = ["kv_undeclared"]
        report = self.validate(skill, pool_ids=None)
        self.assertIn(("USED_IN_STEPS_MISMATCH", "steps[s1].refs"), self.codes(report, "hard_error"))

    # ------------------------------------------------------------------
    # 5. 提示项：R6 数值、例外落点、4.3 完整性
    # ------------------------------------------------------------------

    def test_17_numbers_missing_from_atoms_are_flagged(self):
        doc_id, dv_id = self._create_document("收费口径", "org_internal")
        metric = self._create_atom(
            doc_id, dv_id, title="收缴率口径", category="指标数据", atom_type="指标",
            statement="收缴率 = 实收金额 / 应收金额。",
            metric={"name": "收缴率", "unit": "%", "period": "月", "criteria": "收缴率低于 90% 时需要预警"},
        )
        skill = self.example()
        skill["knowledge_refs"].append({"atom_item_id": metric[0], "atom_version_id": metric[1], "role": "指标口径", "used_in_steps": ["s5"]})
        skill["steps"].append({"step_id": "s5", "kind": "计算", "action": "收缴率低于 90% 时标记预警，低于 85% 时转人工",
                               "condition": "rate < 0.9", "refs": [metric[1]], "basis": "有原子依据", "on_fail": "返回不可计算"})
        skill["preconditions"].append({"text": "统计周期不少于 3 个月", "ref": metric[1]})
        skill["outputs"].append({"key": "alert", "label": "预警等级", "type": "enum", "required": True,
                                 "allowed_values": ["正常", "低于90%", "低于八成"]})
        report = self.validate(skill, pool_ids=self.pool + [metric[1]])
        self.assertTrue(report.passed, [i.to_dict() for i in report.hard_errors])
        flagged = {(u["path"], u["value"]) for u in report.unsupported_items if u["kind"] == "疑似无依据数值"}
        self.assertIn(("steps[s5].action", "85%"), flagged)
        self.assertIn(("steps[s4].action", "30"), flagged)
        self.assertTrue(any(p.startswith("preconditions[~") and v == "3" for p, v in flagged), flagged)
        # 原子 metric_definition 中已有的 90%，以及等价的 0.9 不误报
        self.assertNotIn(("steps[s5].action", "90%"), flagged)
        self.assertNotIn(("steps[s5].condition", "0.9"), flagged)
        self.assertNotIn(("outputs[alert].allowed_values", "90%"), flagged)
        for u in report.unsupported_items:
            self.assertIn(u["kind"], ("无依据步骤", "疑似无依据数值", "例外未落点"))

    def test_18_unlanded_atom_exception_is_hinted(self):
        doc_id, dv_id = self._create_document("入户维修规范", "org_internal")
        exc_atom = self._create_atom(
            doc_id, dv_id, title="入户维修例外", category="专家经验", atom_type="经验",
            statement="入户维修需征得业主同意。",
            exceptions=["业主拒绝入户时不得强行进入，应记录并上报客服主管"],
        )
        skill = self.example()
        skill["knowledge_refs"].append({"atom_item_id": exc_atom[0], "atom_version_id": exc_atom[1], "role": "例外处理", "used_in_steps": ["s3"]})
        self.step(skill, "s3")["refs"].append(exc_atom[1])
        report = self.validate(skill, pool_ids=self.pool + [exc_atom[1]])
        self.assertTrue(report.passed)
        self.assertIn(("EXCEPTION_NOT_LANDED", f"knowledge_refs[{exc_atom[1]}]"), self.codes(report, "hint"))
        landed = [u for u in report.unsupported_items if u["kind"] == "例外未落点"]
        self.assertEqual(len(landed), 1)
        self.assertEqual(landed[0]["atom_version_id"], exc_atom[1])
        # 原 PRD 示例中 kv_a2、kv_c1 的例外已由 s2 分支落点，不应提示
        self.assertNotIn(f"knowledge_refs[{self.atom_a[1]}]", {u["path"] for u in landed})

        # 在人工升级条件中补上落点后，提示消失
        skill["escalation_conditions"].append("业主拒绝入户时记录并上报客服主管，不强行进入")
        report = self.validate(skill, pool_ids=self.pool + [exc_atom[1]])
        self.assertNotIn("EXCEPTION_NOT_LANDED", {i.code for i in report.issues})

    def test_19_completeness_hints(self):
        doc_id, dv_id = self._create_document("完整性夹具", "org_internal")
        judge = self._create_atom(
            doc_id, dv_id, title="方法类判断原子", category="方法与工具", atom_type="判断",
            statement="设备异常先判断是否影响正常使用。",
        )
        # 仅两条同类原子：数量与单一类别提示；角色与默认不一致提示
        skill = self.example()
        skill["knowledge_refs"] = [r for r in skill["knowledge_refs"] if r["atom_version_id"] == self.atom_b[1]]
        skill["knowledge_refs"].append({"atom_item_id": judge[0], "atom_version_id": judge[1], "role": "判断规则", "used_in_steps": ["s2"]})
        skill["knowledge_refs"][0]["role"] = "判断规则"
        self.step(skill, "s2")["refs"] = [judge[1]]
        skill["preconditions"] = []
        report = self.validate(skill, pool_ids=self.pool + [judge[1]])
        self.assertTrue(report.passed, [i.to_dict() for i in report.hard_errors])
        hints = self.codes(report, "hint")
        self.assertIn(("REF_COUNT_OUT_OF_RANGE", "knowledge_refs"), hints)
        self.assertIn(("SINGLE_CATEGORY", "knowledge_refs"), hints)
        self.assertIn(("ROLE_DEFAULT_MISMATCH", f"knowledge_refs[{self.atom_b[1]}].role"), hints)
        # 方法与工具 + atom_type=判断：默认角色取判断规则，不提示
        self.assertNotIn(("ROLE_DEFAULT_MISMATCH", f"knowledge_refs[{judge[1]}].role"), hints)

        # 项目案例不能单独支撑规则判断步骤
        case = self._create_atom(doc_id, dv_id, title="案例原子", category="项目案例", atom_type="案例",
                                 statement="某项目巡检异常处理过程。")
        skill = self.example()
        skill["knowledge_refs"].append({"atom_item_id": case[0], "atom_version_id": case[1], "role": "案例参考", "used_in_steps": ["s3"]})
        self.step(skill, "s3")["refs"] = [case[1]]
        skill["knowledge_refs"] = [r for r in skill["knowledge_refs"] if r["atom_version_id"] != self.atom_b[1]]
        report = self.validate(skill, pool_ids=self.pool + [case[1]])
        self.assertIn(("CASE_ONLY_RULE_STEP", "steps[s3].refs"), self.codes(report, "hint"))

    # ------------------------------------------------------------------
    # 6. R4 可见范围
    # ------------------------------------------------------------------

    def test_20_visibility_follows_strictest_atom_permission(self):
        admin_doc, admin_dv = self._create_document("管理员专享资料", "admin_only")
        admin_atom = self._create_atom(admin_doc, admin_dv, title="专享原子", category="方法与工具", atom_type="方法",
                                       statement="登记工单并同步通知责任人。")
        skill = self._swap_b(self.example(), *admin_atom)
        report = self.validate(skill, pool_ids=self.pool + [admin_atom[1]])
        self.assertTrue(report.passed, [i.to_dict() for i in report.hard_errors])
        self.assertEqual(report.visibility, "admin_only")

        # 文件为企业内部、条目收紧为管理员专享
        doc_id, dv_id = self._create_document("内部资料", "org_internal")
        tight = self._create_atom(doc_id, dv_id, title="收紧原子", category="方法与工具", atom_type="方法",
                                  statement="登记工单并同步通知责任人。", access_scope="admin_only")
        report = self.validate(self._swap_b(self.example(), *tight), pool_ids=self.pool + [tight[1]])
        self.assertEqual(report.visibility, "admin_only")

        # 全部企业内部时为 org_internal
        self.assertEqual(self.validate(self.example()).visibility, "org_internal")

    # ------------------------------------------------------------------
    # 7. 稳定路径
    # ------------------------------------------------------------------

    def test_21_issue_paths_are_stable_under_reordering(self):
        skill = self.example()
        self.step(skill, "s3")["basis"] = "通用操作"
        skill["inputs"].append({"key": "amount", "label": "金额", "type": "number", "required": True})
        skill["preconditions"].append({"text": "工单系统在 2 小时内可用", "ref": self.atom_a[1]})
        skill["not_applies_to"] = ["TBD_EXPERT", "收费下降原因分析属于另一任务"]
        skill["risk_boundary"].append("")

        reordered = copy.deepcopy(skill)
        reordered["steps"].reverse()
        reordered["inputs"].reverse()
        reordered["preconditions"].reverse()
        reordered["knowledge_refs"].reverse()
        reordered["not_applies_to"].reverse()
        reordered["risk_boundary"].reverse()

        first = self.validate(skill)
        second = self.validate(reordered)
        paths_first = sorted((i.level, i.code, i.path) for i in first.issues)
        paths_second = sorted((i.level, i.code, i.path) for i in second.issues)
        self.assertEqual(paths_first, paths_second)
        self.assertIn(("hard_error", "R3_GENERIC_KIND_INVALID", "steps[s3].basis"), paths_first)
        self.assertIn(("hard_error", "NUMBER_UNIT_MISSING", "inputs[amount].unit"), paths_first)
        self.assertTrue(any(p.startswith("risk_boundary[~") for _l, _c, p in paths_first))
        for _level, _code, path in paths_first:
            self.assertIsNone(INDEX_PATTERN.search(path), f"路径含数组下标：{path}")
        self.assertEqual(sorted(u["item_key"] for u in first.unsupported_items),
                         sorted(u["item_key"] for u in second.unsupported_items))

    # ------------------------------------------------------------------
    # 8. 数据表
    # ------------------------------------------------------------------

    def _m01_snapshot(self, conn):
        tables = ["documents", "document_versions", "knowledge_items", "knowledge_versions", "processing_tasks"]
        ddl = {t: conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0] for t in tables}
        rows = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()] for t in tables}
        return ddl, rows

    def test_22_skill_tables_reinit_on_existing_db_without_touching_m01(self):
        from database import init_db
        skill_tables = ["skills", "skill_versions", "skill_atom_refs", "skill_review_records", "skill_generation_batches"]
        with self.get_db() as conn:
            before = self._m01_snapshot(conn)
            self.assertGreater(len(before[1]["knowledge_versions"]), 0)
            # 模拟 M02 之前的旧库：删除 M02 表（仅在隔离库中）
            for t in reversed(skill_tables):
                conn.execute(f"DROP TABLE IF EXISTS {t}")
        init_db()
        init_db()
        with self.get_db() as conn:
            after = self._m01_snapshot(conn)
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(before, after, "M01 核心表结构与数据不得变化")
        self.assertTrue(set(skill_tables) <= names)
        with self.get_db() as conn:
            for table in skill_tables:
                cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                self.assertIn("organization_id", cols, table)
            ref_cols = {r["name"] for r in conn.execute("PRAGMA table_info(skill_atom_refs)")}
            for col in ("snapshot_title", "snapshot_statement", "snapshot_conditions_json", "snapshot_actions_json",
                        "snapshot_exceptions_json", "snapshot_primary_category"):
                self.assertIn(col, ref_cols)
            ver_cols = {r["name"] for r in conn.execute("PRAGMA table_info(skill_versions)")}
            for col in ("version_number", "schema_version", "skill_json", "generation_json", "revision_token",
                        "reviewed_by", "reviewed_at", "review_action", "created_by", "created_at"):
                self.assertIn(col, ver_cols)
            batch_cols = {r["name"] for r in conn.execute("PRAGMA table_info(skill_generation_batches)")}
            for col in ("scene_id", "initiated_by", "initiated_at", "focus_note", "atom_pool_json", "task_split_json",
                        "model_name", "prompt_version", "task_results_json", "status"):
                self.assertIn(col, batch_cols)

    def _insert_skill(self, conn, org_id=None):
        org_id = org_id or self.org_id
        now = datetime.now(timezone.utc).isoformat()
        skill_id = f"sk_{uuid.uuid4().hex[:8]}"
        version_id = f"skv_{uuid.uuid4().hex[:8]}"
        conn.execute(
            "INSERT INTO skills (id, organization_id, scene_id, status, created_by, created_at, updated_at) "
            "VALUES (?, ?, 'scene_equipment_inspection', 'pending_review', 'usr_admin_001', ?, ?)",
            (skill_id, org_id, now, now),
        )
        conn.execute(
            "INSERT INTO skill_versions (id, skill_id, organization_id, version_number, schema_version, skill_json, version_kind, "
            "revision_token, created_by, created_at) VALUES (?, ?, ?, 1, '1.0', ?, 'ai_original', ?, 'usr_admin_001', ?)",
            (version_id, skill_id, org_id, json.dumps(self.example(), ensure_ascii=False), uuid.uuid4().hex, now),
        )
        conn.execute("UPDATE skills SET current_version_id = ? WHERE id = ?", (version_id, skill_id))
        return skill_id, version_id

    def test_23_skill_table_constraints(self):
        now = datetime.now(timezone.utc).isoformat()
        with self.get_db() as conn:
            skill_id, version_id = self._insert_skill(conn)
            conn.execute(
                "INSERT INTO skill_atom_refs (id, organization_id, skill_id, skill_version_id, atom_item_id, atom_version_id, role, "
                "snapshot_title, snapshot_statement, created_at) VALUES (?, ?, ?, ?, ?, ?, '判断规则', '设备巡检异常处置', 'x', ?)",
                (uuid.uuid4().hex, self.org_id, skill_id, version_id, self.atom_a[0], self.atom_a[1], now),
            )
            # 审核信息可以更新
            conn.execute("UPDATE skill_versions SET reviewed_by = 'usr_admin_001', review_action = 'approve' WHERE id = ?", (version_id,))

        def expect_fail(sql, params, message_part):
            with self.assertRaises(sqlite3.DatabaseError) as ctx:
                with self.get_db() as conn:
                    conn.execute(sql, params)
            self.assertIn(message_part, str(ctx.exception))

        expect_fail("UPDATE skill_versions SET skill_json = '{}' WHERE id = ?", (version_id,), "immutable")
        expect_fail("DELETE FROM skill_versions WHERE id = ?", (version_id,), "cannot be deleted")
        expect_fail("UPDATE skill_atom_refs SET snapshot_title = 'x' WHERE skill_version_id = ?", (version_id,), "immutable")
        expect_fail("UPDATE skills SET status = 'unknown' WHERE id = ?", (skill_id,), "CHECK")
        expect_fail("UPDATE skills SET current_version_id = 'skv_other' WHERE id = ?", (skill_id,), "current version mismatch")
        # 原子条目与版本不匹配
        expect_fail(
            "INSERT INTO skill_atom_refs (id, organization_id, skill_id, skill_version_id, atom_item_id, atom_version_id, role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '执行动作', ?)",
            (uuid.uuid4().hex, self.org_id, skill_id, version_id, self.atom_a[0], self.atom_b[1], now),
            "reference mismatch",
        )
        # 跨企业写入被拦截
        expect_fail(
            "INSERT INTO skill_versions (id, skill_id, organization_id, version_number, schema_version, skill_json, version_kind, "
            "revision_token, created_by, created_at) VALUES (?, ?, 'org_other', 2, '1.0', '{}', 'expert_revision', 'x', 'u', ?)",
            (uuid.uuid4().hex, skill_id, now),
            "organization mismatch",
        )
        expect_fail(
            "INSERT INTO skill_review_records (id, organization_id, skill_id, action, operator_id, created_at) "
            "VALUES (?, 'org_other', ?, 'approve', 'u', ?)",
            (uuid.uuid4().hex, skill_id, now),
            "organization mismatch",
        )
        expect_fail(
            "INSERT INTO skill_review_records (id, organization_id, skill_id, action, operator_id, reject_reason, created_at) "
            "VALUES (?, ?, ?, 'reject', 'u', '不想要', ?)",
            (uuid.uuid4().hex, self.org_id, skill_id, now),
            "CHECK",
        )
        # 同一场景只允许一个进行中的批次
        with self.get_db() as conn:
            conn.execute(
                "INSERT INTO skill_generation_batches (id, organization_id, scene_id, initiated_by, initiated_at, status, updated_at) "
                "VALUES (?, ?, 'scene_x', 'usr_admin_001', ?, 'running', ?)",
                (uuid.uuid4().hex, self.org_id, now, now),
            )
        expect_fail(
            "INSERT INTO skill_generation_batches (id, organization_id, scene_id, initiated_by, initiated_at, status, updated_at) "
            "VALUES (?, ?, 'scene_x', 'usr_admin_001', ?, 'running', ?)",
            (uuid.uuid4().hex, self.org_id, now, now),
            "UNIQUE",
        )
        with self.get_db() as conn:
            conn.execute(
                "INSERT INTO skill_generation_batches (id, organization_id, scene_id, initiated_by, initiated_at, status, updated_at) "
                "VALUES (?, ?, 'scene_x', 'usr_admin_001', ?, 'completed', ?)",
                (uuid.uuid4().hex, self.org_id, now, now),
            )

        # 引用快照不依赖原子表：专用原子的版本被移除后，快照仍可读、不被级联删除
        doc_id, dv_id = self._create_document("快照夹具", "org_internal")
        snap_item, snap_ver = self._create_atom(doc_id, dv_id, title="快照原子", category="方法与工具", atom_type="方法",
                                                statement="快照陈述。")
        with self.get_db() as conn:
            conn.execute(
                "INSERT INTO skill_atom_refs (id, organization_id, skill_id, skill_version_id, atom_item_id, atom_version_id, role, "
                "snapshot_title, snapshot_statement, created_at) VALUES (?, ?, ?, ?, ?, ?, '执行动作', '快照原子', '快照陈述。', ?)",
                (uuid.uuid4().hex, self.org_id, skill_id, version_id, snap_item, snap_ver, now),
            )
            conn.execute("DELETE FROM knowledge_versions WHERE id = ?", (snap_ver,))
            row = conn.execute("SELECT snapshot_title, snapshot_statement FROM skill_atom_refs WHERE atom_version_id = ?",
                               (snap_ver,)).fetchone()
            self.assertEqual((row["snapshot_title"], row["snapshot_statement"]), ("快照原子", "快照陈述。"))

    def test_24_status_codes_match_prd_labels(self):
        from skill_constants import BATCH_STATUS_LABELS, SKILL_STATUS_LABELS
        from skill_validation import load_skill_schema
        schema_status = [s for s in load_skill_schema()["properties"]["status"]["enum"] if s]
        self.assertEqual(list(SKILL_STATUS_LABELS.values()), schema_status)
        self.assertEqual(list(BATCH_STATUS_LABELS.values()), ["进行中", "完成", "部分完成", "失败", "无可生成任务"])


class TestM02Common(unittest.TestCase):
    def test_json_null_policies_remain_distinct(self):
        from m02_common import json_value, loads
        self.assertIsNone(loads("null", []))
        self.assertEqual(json_value("null", []), [])
        for malformed in (None, "", "invalid", 12):
            self.assertEqual(loads(malformed, []), [])

    def test_reference_paths_are_stable_and_ignore_malformed_fields(self):
        from m02_common import content_key, iter_skill_refs
        pre = {"text": "条件", "ref": " kv2 "}
        skill = {"knowledge_refs": [{"atom_version_id": " kv1 "}, None, {}],
                 "steps": [{"step_id": "s1", "refs": ["kv1", None, 1, ""]}, {"refs": "invalid"}],
                 "preconditions": [pre], "inputs": [{"key": "x", "source_ref": "kv2"}],
                 "outputs": "invalid"}
        self.assertEqual(list(iter_skill_refs(skill)), [
            ("kv1", "knowledge_refs[kv1]", "declaration"),
            ("kv1", "steps[s1].refs", "step"),
            ("kv2", f"preconditions[{content_key(pre)}].ref", "precondition"),
            ("kv2", "inputs[x].source_ref", "inputs"),
        ])
        self.assertEqual(list(iter_skill_refs(None)), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
