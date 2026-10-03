import unittest
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

import sys
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

from main import app
from database import init_db, get_db
import config
from auth import create_session
from deepseek_extractor import (
    _strip_leading_list_number,
    evaluate_business_importance,
    classify_atom_issues,
    validate_and_sanitize_atoms,
)
from tasks import execute_parse_task, execute_extract_task


def _fake_extract_atoms_for_audit_tests(source_blocks=None, document_title="", **kwargs):
    atoms = []
    for b in (source_blocks or []):
        txt = b.get("text_content") or ""
        if "每日上午9点" in txt:
            atoms.append({
                "title": "二次供水泵房巡检规程",
                "primary_category": "制度与标准",
                "atom_type": "规则",
                "statement": "巡检人员每日上午9点对园区二次供水泵房进行水压与渗漏巡检。",
                "subject": "巡检人员",
                "conditions": [],
                "actions": [],
                "exceptions": [],
                "_extraction_mode": "offline-fallback",
                "quality_flags": [],
                "field_states": {"statement": "supported"},
                "source_evidence": [{
                    "source_block_id": b["id"],
                    "excerpt": "巡检人员每日上午9点对园区二次供水泵房进行水压与渗漏巡检。",
                    "field_name": "statement",
                }],
            })
        elif "跑水异常" in txt:
            atoms.append({
                "title": "跑水异常应急处置",
                "primary_category": "应急预案",
                "atom_type": "规则",
                "statement": "巡检人员发现跑水异常必须立即关闭阀门并上报工程主管。",
                "subject": "巡检人员",
                "conditions": ["发现跑水异常"],
                "actions": ["立即关闭阀门", "上报工程主管"],
                "exceptions": [],
                "quality_flags": [],
                "field_states": {"statement": "supported"},
                "source_evidence": [{
                    "source_block_id": b["id"],
                    "excerpt": "巡检人员发现跑水异常必须立即关闭阀门并上报工程主管。",
                    "field_name": "statement",
                }],
            })
        elif "高压配电房火警" in txt:
            atoms.append({
                "title": "高压配电房火警应急处置规程",
                "primary_category": "应急预案",
                "atom_type": "规则",
                "statement": "发现高压配电房火警异常时，巡检人员必须立即切断进线主电源并启动灭火系统，涉及人身安全严禁单人进入。",
                "subject": "巡检人员",
                "conditions": ["发现高压配电房火警异常时"],
                "actions": ["立即切断进线主电源", "启动灭火系统"],
                "exceptions": ["涉及人身安全严禁单人进入"],
                "quality_flags": [],
                "field_states": {"statement": "supported"},
                "source_evidence": [{
                    "source_block_id": b["id"],
                    "excerpt": "发现高压配电房火警异常时，巡检人员必须立即切断进线主电源并启动灭火系统，涉及人身安全严禁单人进入。",
                    "field_name": "statement",
                }],
            })
        elif "承压测试标准" in txt:
            atoms.append({
                "title": "给排水管道承压测试标准",
                "primary_category": "指标数据",
                "atom_type": "规则",
                "statement": "给排水管道承压测试标准为不低于0.8MPa并保持稳定30分钟。",
                "subject": "给排水管道",
                "conditions": [],
                "actions": [],
                "exceptions": [],
                "metric_definition": {
                    "criteria": "不低于0.8MPa并保持稳定30分钟",
                    "rows": [{"name": "承压测试标准", "relation": "不低于", "value": "0.8", "unit": "MPa", "period": "30分钟", "note": ""}],
                },
                "quality_flags": [],
                "field_states": {"statement": "supported"},
                "source_evidence": [{
                    "source_block_id": b["id"],
                    "excerpt": "给排水管道承压测试标准为不低于0.8MPa并保持稳定30分钟。",
                    "field_name": "statement",
                }],
            })
        elif "公共指示牌破损" in txt:
            atoms.append({
                "title": "公共指示牌破损处理",
                "primary_category": "制度与标准",
                "atom_type": "规则",
                "statement": "日常巡查中发现公共指示牌破损时，客服管家应在2小时内张贴临时提示并通知维修。",
                "subject": "客服管家",
                "conditions": ["日常巡查中发现公共指示牌破损时"],
                "actions": ["在2小时内张贴临时提示", "通知维修"],
                "exceptions": [],
                "quality_flags": [],
                "field_states": {"statement": "supported"},
                "source_evidence": [{
                    "source_block_id": b["id"],
                    "excerpt": "日常巡查中发现公共指示牌破损时，客服管家应在2小时内张贴临时提示并通知维修。",
                    "field_name": "statement",
                }],
            })
    return atoms, {"batch_count": 1, "raw_candidate_count": len(atoms), "merged_candidate_count": len(atoms)}


class TestAuditReliefAndChapterReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_env_helper import setup_test_db
        cls.test_db = setup_test_db("audit_relief_tests")
        with get_db() as conn:
            conn.execute("DELETE FROM knowledge_evidence")
            conn.execute("DELETE FROM knowledge_versions")
            conn.execute("DELETE FROM knowledge_items")
            conn.execute("DELETE FROM source_blocks")
            conn.execute("DELETE FROM processing_tasks")
            conn.execute("DELETE FROM document_versions")
            conn.execute("DELETE FROM documents")

        cls.client = TestClient(app)
        cls.admin_token = create_session("usr_admin_001", "org_greentown")
        cls.member_token = create_session("usr_member_001", "org_greentown")
        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}
        cls.member_headers = {"Authorization": f"Bearer {cls.member_token}"}

        cls.orig_key = config.DEEPSEEK_API_KEY
        config.DEEPSEEK_API_KEY = "mock_key_for_audit_tests"
        cls.extractor_patcher = patch(
            "deepseek_extractor.extract_atoms_via_deepseek_batched",
            side_effect=_fake_extract_atoms_for_audit_tests,
        )
        cls.extractor_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.extractor_patcher.stop()
        config.DEEPSEEK_API_KEY = cls.orig_key
        from test_env_helper import cleanup_test_db
        cleanup_test_db(cls.test_db)

    def test_01_strip_leading_list_number_safety(self):
        """
        三、去序号验证：
        1. 清理开头无业务含义的序号
        2. 保留小数、标准编号、设备编号及正文步骤引用
        3. 保留动作先后顺序
        """
        # 常见序号应被清理
        self.assertEqual(_strip_leading_list_number("1. 检查水泵仪表"), "检查水泵仪表")
        self.assertEqual(_strip_leading_list_number("2、关闭进水总阀"), "关闭进水总阀")
        self.assertEqual(_strip_leading_list_number("(3) 记录环境温度"), "记录环境温度")
        self.assertEqual(_strip_leading_list_number("（4）启动备用发电机"), "启动备用发电机")
        self.assertEqual(_strip_leading_list_number("一、开展消防维保演练"), "开展消防维保演练")
        self.assertEqual(_strip_leading_list_number("（一）通知受影响业主"), "通知受影响业主")

        # 业务关键编号、小数、标准编号绝不误删
        self.assertEqual(_strip_leading_list_number("0.5MPa水压合格标准"), "0.5MPa水压合格标准")
        self.assertEqual(_strip_leading_list_number("3.14米地下机房净高要求"), "3.14米地下机房净高要求")
        self.assertEqual(_strip_leading_list_number("GB 50016-2014建筑设计防火规范"), "GB 50016-2014建筑设计防火规范")
        self.assertEqual(_strip_leading_list_number("P-101A变频供水泵定期巡检"), "P-101A变频供水泵定期巡检")
        self.assertEqual(_strip_leading_list_number("严格按照第2步流程指引执行处置"), "严格按照第2步流程指引执行处置")

        # 动作先后顺序严格保留
        raw_actions = ["1. 现场断电并挂牌", "2. 确认零电压", "3. 实施接地保护"]
        cleaned_actions = [_strip_leading_list_number(a) for a in raw_actions]
        self.assertEqual(cleaned_actions, ["现场断电并挂牌", "确认零电压", "实施接地保护"])

    def test_02_business_importance_and_issues_decoupling(self):
        """
        五、抽取疑点与业务重要程度解耦：
        1. 涉及安全、应急、火警的操作自动识别为 critical 并给出依据
        2. 普通标准识别为 normal
        3. 疑点区分确定性硬性错误与模型疑点
        4. 明确注明未发现已知规则冲突（不写无冲突），明确保留未实现检测边界
        """
        # 涉及人身安全与应急
        critical_atom = {
            "title": "配电房火警应急处置规程",
            "statement": "配电房发生火警时，巡检人员必须立即切断电源并触发声光报警，涉及人身安全严禁单人灭火。",
            "actions": ["立即切断电源", "触发声光报警", "严禁单人灭火"],
            "conditions": ["配电房发生火警时"],
            "exceptions": ["严禁单人处置"],
        }
        level, rationale = evaluate_business_importance(critical_atom)
        self.assertEqual(level, "critical")
        self.assertIn("安全与应急", rationale)

        # 普通日常检查
        normal_atom = {
            "title": "大堂绿植日常浇水规范",
            "statement": "物业保洁人员每周一、三、五上午对大堂绿植进行适量喷淋浇水。",
            "actions": ["适量喷淋浇水"],
            "conditions": ["每周一、三、五上午"],
            "exceptions": [],
        }
        n_level, n_rat = evaluate_business_importance(normal_atom)
        self.assertEqual(n_level, "normal")

        # 疑点解耦
        issues = classify_atom_issues(
            quality_flags=["待管理员确认主分类", "缺少核心陈述，无法独立理解", "疑似规则冲突待复核：与条目B存在互斥规则"],
            field_states={"statement": "failed"},
            source_anchors=["p_1"],
        )
        self.assertEqual(len(issues["deterministic_errors"]), 3) # 主分类待确认 + 缺少核心陈述 + statement字段failed
        self.assertEqual(len(issues["model_doubts"]), 1) # 疑似规则冲突
        self.assertEqual(issues["conflict_check_status"], "存在疑似规则冲突待复核")
        self.assertTrue(any("正文块内部语义部分遗漏" in cap for cap in issues["unimplemented_capabilities"]))

        # 无冲突时绝不写“无冲突”，写“未发现已知规则冲突”
        no_conflict_issues = classify_atom_issues(
            quality_flags=[],
            field_states={},
            source_anchors=["p_2"],
        )
        self.assertEqual(no_conflict_issues["conflict_check_status"], "未发现已知规则冲突")

    def test_03_chapter_review_flow_and_coverage_status(self):
        """
        二 & 四、按章节组织审核与原文覆盖检查：
        1. 待核对页面按来源文件 + 版本 + 章节路径分组
        2. 无标题内容按原文顺序合理分组，不编造章节标题
        3. 真实维护原文处理状态（已关联候选/已确认无需提取/尚未覆盖待检查）
        4. 存量未处理段落显示为「尚未覆盖，待检查」，不默认视为完整
        5. 有未覆盖段落时章节状态标为 has_uncovered，不能标为已完成
        6. 管理员可标记忽略或一键补充知识条目
        """
        # 上传一个包含有标题章节、无标题段落、应急关键操作、背景段落的综合测试文档
        test_doc_content = """# 综合服务中心操作规程

## 第一章 巡检作业规范
巡检人员每日上午9点对园区二次供水泵房进行水压与渗漏巡检。
巡检人员发现跑水异常必须立即关闭阀门并上报工程主管。

本段为过渡背景介绍文字，简要介绍园区给排水管网的建设历史与基本布局，无需提取为业务规则。

发现高压配电房火警异常时，巡检人员必须立即切断进线主电源并启动灭火系统，涉及人身安全严禁单人进入。

## 第二章 设施维护指标
给排水管道承压测试标准为不低于0.8MPa并保持稳定30分钟。

以下为一段没有独立小节标题的正文说明内容。
日常巡查中发现公共指示牌破损时，客服管家应在2小时内张贴临时提示并通知维修。
"""
        upload_resp = self.client.post(
            "/api/documents/upload",
            files={"file": ("02_综合操作规程.md", test_doc_content.encode("utf-8"), "text/markdown")},
            data={"duplicate_mode": "new_document"},
            headers=self.admin_headers,
        )
        self.assertEqual(upload_resp.status_code, 200)
        doc_id = upload_resp.json()["document_id"]
        ver_id = upload_resp.json()["version_id"]
        self.__class__.document_id = doc_id
        self.__class__.document_version_id = ver_id

        # 等待自动抽取流水线完成
        max_wait = 4.0
        start = time.time()
        while time.time() - start < max_wait:
            doc_detail = self.client.get(f"/api/documents/{doc_id}", headers=self.admin_headers).json()
            if doc_detail["versions"][0]["processing_status"] == "completed":
                break
            time.sleep(0.2)

        # 请求章节审核数据
        review_resp = self.client.get(
            f"/api/documents/{doc_id}/versions/{ver_id}/chapters/review",
            headers=self.admin_headers,
        )
        self.assertEqual(review_resp.status_code, 200)
        review_data = review_resp.json()

        chapters = review_data["chapters"]
        self.assertGreaterEqual(len(chapters), 2)

        # 验证有明确标题的章节
        named_chap = next((c for c in chapters if "第一章 巡检作业规范" in (c["heading_path"] or "")), None)
        self.assertIsNotNone(named_chap)
        self.assertEqual(named_chap["is_derived_group"], False)
        self.assertGreater(named_chap["stats"]["total_blocks"], 0)
        self.assertGreater(named_chap["stats"]["total_items"], 0)

        # 检查是否识别出了重要操作
        self.assertGreater(named_chap["stats"]["critical_items"], 0)

        # 检查覆盖状态：必须有已关联候选块和未覆盖块
        blocks = named_chap["blocks"]
        has_associated = any(b["coverage_status"] == "associated_candidate" for b in blocks)
        self.assertTrue(has_associated)
        # 必须明确文案：“已关联候选条目（提示：存在候选知识并不等同于原文已被完整提取）”
        assoc_block = next(b for b in blocks if b["coverage_status"] == "associated_candidate")
        self.assertIn("并不等同于原文已被完整提取", assoc_block["coverage_note"])

        # 检查未覆盖块
        uncovered_block = next((b for b in blocks if b["coverage_status"] == "uncovered"), None)
        if uncovered_block:
            # 标记为无需提取
            ignore_resp = self.client.post(
                f"/api/documents/{doc_id}/versions/{ver_id}/source-blocks/{uncovered_block['id']}/ignore",
                json={"ignore_reason": "背景历史介绍，不生成知识"},
                headers=self.admin_headers,
            )
            self.assertEqual(ignore_resp.status_code, 200)
            self.assertEqual(ignore_resp.json()["ignore_status"], "admin_ignored")

            # 再次查询，该块变为 admin_ignored
            review_again = self.client.get(
                f"/api/documents/{doc_id}/versions/{ver_id}/chapters/review",
                headers=self.admin_headers,
            ).json()
            updated_chap = next(c for c in review_again["chapters"] if c["chapter_key"] == named_chap["chapter_key"])
            ignored_b = next(b for b in updated_chap["blocks"] if b["id"] == uncovered_block["id"])
            self.assertEqual(ignored_b["coverage_status"], "admin_ignored")
            self.assertIn("管理员已确认无需提取", ignored_b["coverage_note"])

            # 取消忽略
            unign_resp = self.client.delete(
                f"/api/documents/{doc_id}/versions/{ver_id}/source-blocks/{uncovered_block['id']}/ignore",
                headers=self.admin_headers,
            )
            self.assertEqual(unign_resp.status_code, 200)
            self.assertEqual(unign_resp.json()["ignore_status"], "none")

    def test_04_batch_confirm_governance_and_critical_protection(self):
        """
        六、集中核对与批量确认：
        1. 普通内容且无阻断项的条目可被批量确认
        2. 重要操作和存在阻断项的条目进入重点审核，不能被普通批量操作误通过
        3. 后端逐条刚性校验，防并发修改冲突
        4. 记录批量确认审计日志
        """
        # 获取待核对条目
        items_resp = self.client.get("/api/knowledge/items?review_status=pending_review", headers=self.admin_headers)
        self.assertEqual(items_resp.status_code, 200)
        items = items_resp.json()["items"]
        self.assertGreater(len(items), 0)

        # 挑选一条普通条目和一条重要操作条目
        normal_item = next(it for it in items if "每日上午9点" in (it.get("statement") or "") and (it.get("business_importance") or "normal") != "critical")

        # 手动调整一条条目为 critical
        adjust_resp = self.client.put(
            f"/api/knowledge/items/{normal_item['id']}/importance",
            json={
                "business_importance": "critical",
                "importance_rationale": "测试防误批量通过保护",
                "version_id": normal_item["active_version_id"],
                "revision_token": normal_item["revision_token"],
            },
            headers=self.admin_headers,
        )
        self.assertEqual(adjust_resp.status_code, 200)

        # 尝试批量确认此重要操作条目 -> 必须被跳过，并在 skipped_items 中报告原因
        batch_resp = self.client.post(
            "/api/knowledge/items/batch-confirm",
            json={"items": [{"item_id": normal_item["id"], "revision_token": adjust_resp.json()["revision_token"]}]},
            headers=self.admin_headers,
        )
        self.assertEqual(batch_resp.status_code, 200)
        res_data = batch_resp.json()
        self.assertEqual(res_data["confirmed_count"], 0)
        self.assertEqual(res_data["skipped_count"], 1)
        self.assertIn("重点审核", res_data["skipped_items"][0]["reason"])

        # 恢复为 normal 且无阻断项后，批量确认成功
        normal_adjust = self.client.put(
            f"/api/knowledge/items/{normal_item['id']}/importance",
            json={
                "business_importance": "normal",
                "importance_rationale": "常规规范",
                "version_id": normal_item["active_version_id"],
                "revision_token": adjust_resp.json()["revision_token"],
            },
            headers=self.admin_headers,
        )
        self.assertEqual(normal_adjust.status_code, 200)
        # 并发冲突测试：传错误的 revision_token
        conflict_batch = self.client.post(
            "/api/knowledge/items/batch-confirm",
            json={"items": [{"item_id": normal_item["id"], "revision_token": "wrong_token_123"}]},
            headers=self.admin_headers,
        )
        self.assertEqual(conflict_batch.json()["confirmed_count"], 0)
        self.assertEqual(conflict_batch.json()["skipped_count"], 1)
        self.assertIn("已被其他操作修改", conflict_batch.json()["skipped_items"][0]["reason"])

        # 新的离线保真降级仅保留原文。没有人工拆分执行项时，普通内容也不得被批量误确认。
        detail = self.client.get(f"/api/knowledge/items/{normal_item['id']}", headers=self.admin_headers).json()
        correct_token = detail["active_version"]["revision_token"]
        blocked_batch = self.client.post(
            "/api/knowledge/items/batch-confirm",
            json={"items": [{"item_id": normal_item["id"], "revision_token": correct_token}]},
            headers=self.admin_headers,
        )
        self.assertEqual(blocked_batch.json()["confirmed_count"], 0)
        self.assertIn("结构化未完成", blocked_batch.json()["skipped_items"][0]["reason"])

        # 隔离测试数据中由管理员明确补全动作与适用条件，原文及来源证据不变。
        av = detail["active_version"]
        draft_payload = {
            "revision_token": correct_token, "title": av["title"],
            "statement": "巡检人员每日对园区二次供水泵房执行水压与渗漏巡检。",
            "content": "巡检人员每日对园区二次供水泵房执行水压与渗漏巡检。",
            "primary_category": "制度与标准", "atom_type": "规则",
            "subject": "巡检人员", "conditions": ["每日上午9点"],
            "actions": ["对园区二次供水泵房进行水压与渗漏巡检"],
            "exceptions": [], "metric_definition": None, "case_details": None,
        }
        preview = self.client.post(
            f"/api/knowledge/items/{normal_item['id']}/draft/validate",
            json=draft_payload, headers=self.admin_headers,
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertNotIn("结构化未完成", "；".join(preview.json()["quality_flags"]))
        self.assertTrue(preview.json()["can_confirm"])
        # 自动校验只读，校验后的原版本、草稿内容和乐观锁必须仍保持不变。
        unchanged = self.client.get(
            f"/api/knowledge/items/{normal_item['id']}", headers=self.admin_headers,
        ).json()["active_version"]
        self.assertEqual(unchanged["revision_token"], correct_token)
        self.assertEqual(unchanged["actions"], av["actions"])
        self.assertEqual(unchanged["quality_flags"], av["quality_flags"])
        stale = self.client.post(
            f"/api/knowledge/items/{normal_item['id']}/draft/validate",
            json={**draft_payload, "revision_token": "stale"}, headers=self.admin_headers,
        )
        self.assertEqual(stale.status_code, 409)
        denied = self.client.post(
            f"/api/knowledge/items/{normal_item['id']}/draft/validate",
            json=draft_payload, headers=self.member_headers,
        )
        self.assertIn(denied.status_code, (401, 403))
        saved = self.client.put(
            f"/api/knowledge/items/{normal_item['id']}/draft",
            json=draft_payload, headers=self.admin_headers,
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertFalse(any("结构化未完成" in flag for flag in saved.json()["quality_flags"]))
        correct_token = saved.json()["revision_token"]

        # 人工补全后使用正确的 revision_token 批量确认
        ok_batch = self.client.post(
            "/api/knowledge/items/batch-confirm",
            json={"items": [{"item_id": normal_item["id"], "revision_token": correct_token}]},
            headers=self.admin_headers,
        )
        self.assertEqual(ok_batch.json()["confirmed_count"], 1)
        self.assertEqual(ok_batch.json()["skipped_count"], 0)

        # 验证数据库中该条目状态变为了 confirmed
        after_detail = self.client.get(f"/api/knowledge/items/{normal_item['id']}", headers=self.admin_headers).json()
        self.assertEqual(after_detail["active_version"]["review_status"], "confirmed")

    def test_05_supplement_knowledge_from_uncovered_block(self):
        """
        四、管理员查看未覆盖原文并一键补充知识绑定来源
        """
        # 查询一个正文块
        with get_db() as conn:
            sb = conn.execute("SELECT id, document_version_id, (SELECT document_id FROM document_versions WHERE id = source_blocks.document_version_id) as doc_id FROM source_blocks LIMIT 1").fetchone()

        doc_id = sb["doc_id"]
        ver_id = sb["document_version_id"]
        block_id = sb["id"]

        supplement_resp = self.client.post(
            f"/api/documents/{doc_id}/versions/{ver_id}/source-blocks/{block_id}/supplement",
            json={
                "title": "补充的设备维护知识",
                "primary_category": "方法与工具",
                "atom_type": "方法",
                "statement": "设备维护人员应按照维护计划执行检查并记录结果。",
                "content": "管理员根据原始段落补充设备维护要求，后续仍需逐字段核对来源支持。",
                "business_importance": "critical",
            },
            headers=self.admin_headers,
        )
        self.assertEqual(supplement_resp.status_code, 200)
        data = supplement_resp.json()
        new_item_id = data["item_id"]

        # 检查新条目的证据是否真实绑定到了该 block_id
        detail = self.client.get(f"/api/knowledge/items/{new_item_id}", headers=self.admin_headers).json()
        self.assertEqual(detail["active_version"]["title"], "补充的设备维护知识")
        self.assertEqual(detail["active_version"]["statement"], "设备维护人员应按照维护计划执行检查并记录结果。")
        self.assertEqual(detail["active_version"]["content"], "管理员根据原始段落补充设备维护要求，后续仍需逐字段核对来源支持。")
        self.assertEqual(detail["active_version"]["atom_type"], "方法")
        self.assertEqual(detail["active_version"]["primary_category"], "方法与工具")
        self.assertEqual(detail["active_version"]["business_importance"], "critical")
        self.assertEqual(detail["active_version"]["field_states"]["statement"], "unverified")
        self.assertEqual(len(detail["evidence"]), 1)
        self.assertEqual(detail["evidence"][0]["source_block_id"], block_id)
        with get_db() as conn:
            original_text = conn.execute("SELECT text_content FROM source_blocks WHERE id = ?", (block_id,)).fetchone()[0]
        self.assertEqual(detail["evidence"][0]["excerpt"], original_text)
        self.__class__.supplemented_item_id = new_item_id

        # 分类可以明确保留为待分类，但不能伪装成已分类；非法字段必须拒绝且不落库。
        pending_category = self.client.post(
            f"/api/documents/{doc_id}/versions/{ver_id}/source-blocks/{block_id}/supplement",
            json={
                "title": "待分类补充知识",
                "primary_category": None,
                "atom_type": "规则",
                "statement": "管理员补充的待分类业务规则需继续核对。",
                "content": "该条目保留待分类状态，不能冒充已完成分类。",
                "business_importance": "normal",
            },
            headers=self.admin_headers,
        )
        self.assertEqual(pending_category.status_code, 200, pending_category.text)
        pending_detail = self.client.get(
            f"/api/knowledge/items/{pending_category.json()['item_id']}", headers=self.admin_headers
        ).json()
        self.assertIsNone(pending_detail["active_version"]["primary_category"])
        self.assertIn("待管理员确认主分类", pending_detail["active_version"]["quality_flags"])

        with get_db() as conn:
            before_invalid = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        invalid = self.client.post(
            f"/api/documents/{doc_id}/versions/{ver_id}/source-blocks/{block_id}/supplement",
            json={
                "title": "非法类型不应保存",
                "primary_category": "方法与工具",
                "atom_type": "process_flow",
                "statement": "这条输入使用了非法知识类型。",
                "content": "后端必须明确拒绝，不能静默替换为原文。",
                "business_importance": "normal",
            },
            headers=self.admin_headers,
        )
        self.assertEqual(invalid.status_code, 400)
        with get_db() as conn:
            after_invalid = conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        self.assertEqual(before_invalid, after_invalid)

    def test_06_unified_eligibility_tokens_and_single_critical_confirm(self):
        item_id = self.__class__.supplemented_item_id
        detail = self.client.get(f"/api/knowledge/items/{item_id}", headers=self.admin_headers).json()
        version = detail["active_version"]

        # 重要条目及尚未复核的补充疑点均不得进入普通批量确认。
        batch = self.client.post(
            "/api/knowledge/items/batch-confirm",
            json={"items": [{"item_id": item_id, "revision_token": version["revision_token"]}]},
            headers=self.admin_headers,
        ).json()
        self.assertEqual(batch["confirmed_count"], 0)
        self.assertIn("重点审核", batch["skipped_items"][0]["reason"])

        missing_token = self.client.post(
            "/api/knowledge/items/batch-confirm",
            json={"items": [{"item_id": item_id}]},
            headers=self.admin_headers,
        ).json()
        self.assertEqual(missing_token["confirmed_count"], 0)
        self.assertIn("缺少 revision_token", missing_token["skipped_items"][0]["reason"])

        # 单条接口允许管理员明确核对 critical 与非阻断疑点。
        single = self.client.post(
            f"/api/knowledge/items/{item_id}/confirm",
            json={"revision_token": version["revision_token"]},
            headers=self.admin_headers,
        )
        self.assertEqual(single.status_code, 200, single.text)
        rotated_token = single.json()["revision_token"]
        self.assertNotEqual(rotated_token, version["revision_token"])

        # 重复确认在副作用前被拒绝，不新增确认审计。
        with get_db() as conn:
            before_audits = conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE action='confirm_knowledge_version' AND target_id=?",
                (version["id"],),
            ).fetchone()[0]
        repeated = self.client.post(
            f"/api/knowledge/items/{item_id}/confirm",
            json={"revision_token": rotated_token},
            headers=self.admin_headers,
        )
        self.assertEqual(repeated.status_code, 400)
        with get_db() as conn:
            after_audits = conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE action='confirm_knowledge_version' AND target_id=?",
                (version["id"],),
            ).fetchone()[0]
        self.assertEqual(before_audits, after_audits)

    def test_07_critical_edit_and_importance_concurrency(self):
        item_id = self.__class__.supplemented_item_id
        detail = self.client.get(f"/api/knowledge/items/{item_id}", headers=self.admin_headers).json()
        active = detail["active_version"]

        adjusted = self.client.put(
            f"/api/knowledge/items/{item_id}/importance",
            json={
                "business_importance": "critical",
                "importance_rationale": "管理员复核后确认涉及关键设备维护",
                "version_id": active["id"],
                "revision_token": active["revision_token"],
            },
            headers=self.admin_headers,
        )
        self.assertEqual(adjusted.status_code, 200, adjusted.text)

        stale = self.client.put(
            f"/api/knowledge/items/{item_id}/importance",
            json={
                "business_importance": "normal",
                "importance_rationale": "并发覆盖尝试",
                "version_id": active["id"],
                "revision_token": active["revision_token"],
            },
            headers=self.admin_headers,
        )
        self.assertEqual(stale.status_code, 409)

        current = self.client.get(f"/api/knowledge/items/{item_id}", headers=self.admin_headers).json()["active_version"]
        draft = self.client.put(
            f"/api/knowledge/items/{item_id}/draft",
            json={
                "revision_token": current["revision_token"],
                "title": current["title"],
                "statement": current["statement"] + " 编辑后仍保持重点审核。",
                "content": current["content"] + " 编辑后仍保持重点审核。",
                "primary_category": current["primary_category"],
                "atom_type": current["atom_type"],
                "subject": current["subject"],
            },
            headers=self.admin_headers,
        )
        self.assertEqual(draft.status_code, 200, draft.text)
        self.assertTrue(draft.json()["is_new_version_draft"])
        after = self.client.get(f"/api/knowledge/items/{item_id}", headers=self.admin_headers).json()["active_version"]
        self.assertEqual(after["business_importance"], "critical")
        self.assertEqual(after["importance_rationale"], "管理员复核后确认涉及关键设备维护")
        self.assertTrue(after["importance_adjusted_by"])

    def test_08_failed_field_and_model_ignore_remain_pending(self):
        doc_id = self.__class__.document_id
        ver_id = self.__class__.document_version_id
        with get_db() as conn:
            candidate = conn.execute(
                """
                SELECT ki.id, kv.id AS version_id, kv.revision_token
                FROM knowledge_items ki JOIN knowledge_versions kv ON kv.id=ki.active_version_id
                WHERE ki.document_id=? AND kv.review_status='pending_review' LIMIT 1
                """,
                (doc_id,),
            ).fetchone()
            self.assertIsNotNone(candidate)
            conn.execute(
                "UPDATE knowledge_versions SET field_states_json=? WHERE id=?",
                (json.dumps({"statement": "failed"}, ensure_ascii=False), candidate["version_id"]),
            )

            block_id = f"sb_{uuid.uuid4().hex[:12]}"
            block_index = conn.execute(
                "SELECT COALESCE(MAX(block_index), 0) + 1 FROM source_blocks WHERE document_version_id=?",
                (ver_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO source_blocks
                (id, document_version_id, organization_id, block_index, block_type, heading_path,
                 paragraph_anchor, text_content, ignore_status, ignore_reason, created_at)
                VALUES (?, ?, 'org_greentown', ?, 'paragraph', '待人工检查', ?, ?,
                        'model_suggested_ignore', '模型判断为背景说明', ?)
                """,
                (block_id, ver_id, block_index, f"model_ignore_{block_index}", "模型建议忽略但管理员尚未核对的原文。", datetime.now(timezone.utc).isoformat()),
            )

        review = self.client.get(
            f"/api/documents/{doc_id}/versions/{ver_id}/chapters/review",
            headers=self.admin_headers,
        ).json()
        displayed = next(
            item for chapter in review["chapters"] for item in chapter["items"] if item["item_id"] == candidate["id"]
        )
        self.assertFalse(displayed["can_batch_confirm"])
        self.assertTrue(any("statement" in reason for reason in displayed["confirmation_blockers"]))
        self.assertEqual(review["summary"]["model_suggested_ignore_blocks"], 1)
        model_chapter = next(c for c in review["chapters"] if any(b["id"] == block_id for b in c["blocks"]))
        self.assertEqual(model_chapter["chapter_status"], "pending_source_review")

        rejected = self.client.post(
            "/api/knowledge/items/batch-confirm",
            json={"items": [{"item_id": candidate["id"], "revision_token": candidate["revision_token"]}]},
            headers=self.admin_headers,
        ).json()
        self.assertEqual(rejected["confirmed_count"], 0)
        self.assertIn("statement", rejected["skipped_items"][0]["reason"])

        ignored = self.client.post(
            f"/api/documents/{doc_id}/versions/{ver_id}/source-blocks/{block_id}/ignore",
            json={"ignore_reason": "管理员核对后确认属于背景说明"},
            headers=self.admin_headers,
        )
        self.assertEqual(ignored.status_code, 200)
        self.assertEqual(ignored.json()["ignore_reason"], "管理员核对后确认属于背景说明")
        after_ignore = self.client.get(
            f"/api/documents/{doc_id}/versions/{ver_id}/chapters/review", headers=self.admin_headers
        ).json()
        self.assertEqual(after_ignore["summary"]["model_suggested_ignore_blocks"], 0)
        self.assertEqual(after_ignore["summary"]["admin_ignored_blocks"], 1)

        self.client.delete(
            f"/api/documents/{doc_id}/versions/{ver_id}/source-blocks/{block_id}/ignore",
            headers=self.admin_headers,
        )
        restored = self.client.get(
            f"/api/documents/{doc_id}/versions/{ver_id}/chapters/review", headers=self.admin_headers
        ).json()
        self.assertGreaterEqual(restored["summary"]["uncovered_blocks"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
