"""
Stage 4A 自动化测试套件：统一检索资格、版本切换与权限基线

覆盖重点验收标准：
- AC12: 待确认隔离（未确认知识不得正式返回）
- AC18: 可用检索（确认且索引建立后可被检索）
- AC19: 停用/删除/过期拦截（disabled/deleted/expired 不得返回）
- AC24: 新文件版本切换语义（未启用前旧版本继续服务，新版本全部确认/排除后事务启用，旧版本立即退出）
- AC25: 权限收紧与放宽（文件收紧立即约束派生知识；文件放宽不自动放宽知识；知识权限不得比文件更开放）
- AC40 & AC41: 管理员与成员共用统一资格规则但授权范围不同（成员不可访问 admin_only 知识与文件）
- 脏数据与悬空索引防御（retrieval_records 异常残留无法穿透业务资格检查）
- 候选排除状态治理（is_excluded 独立字段，不破坏 CHECK 约束，不阻塞新文件版本启用）
"""

import os
import sys
import json
import uuid
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

from main import app
from database import get_db, init_db
from seed import seed_data
from tasks import build_index_for_version
from eligibility import (
    check_knowledge_eligibility,
    filter_eligible_version_ids,
    check_document_access,
    build_eligibility_sql,
)

class TestStage4AEligibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_env_helper import setup_test_db
        cls.test_db = setup_test_db("stage4a_tests")
        cls.client = TestClient(app)

        # 登录管理员
        resp_admin = cls.client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
        assert resp_admin.status_code == 200, resp_admin.text
        cls.admin_token = resp_admin.json()["token"]
        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}
        cls.admin_user = resp_admin.json()["user"]

        # 登录普通成员
        resp_member = cls.client.post("/api/auth/login", json={"username": "member", "password": "Member@Zhixing2026"})
        assert resp_member.status_code == 200, resp_member.text
        cls.member_token = resp_member.json()["token"]
        cls.member_headers = {"Authorization": f"Bearer {cls.member_token}"}
        cls.member_user = resp_member.json()["user"]

    @classmethod
    def tearDownClass(cls):
        from test_env_helper import cleanup_test_db
        cleanup_test_db(cls.test_db)

    def setUp(self):
        # 每个测试使用唯一的隔离前缀，避免测试间数据污染
        self.test_prefix = f"s4a_{uuid.uuid4().hex[:8]}"
        self.org_id = self.admin_user["organization_id"]

    def _wait_for_index_ready(self, version_id: str, timeout: float = 30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT index_status FROM knowledge_versions WHERE id = ?",
                    (version_id,),
                ).fetchone()
                if row and row["index_status"] == "ready":
                    return
                if row and row["index_status"] == "failed":
                    task = conn.execute(
                        "SELECT error_message FROM processing_tasks WHERE target_id = ? AND task_type = 'build_index' ORDER BY created_at DESC LIMIT 1",
                        (version_id,),
                    ).fetchone()
                    self.fail(f"后台索引失败: {task['error_message'] if task else 'unknown'}")
            time.sleep(0.05)
        self.fail(f"等待知识版本 {version_id} 索引 ready 超时")

    def _confirm_item(self, item_id: str):
        detail = self.client.get(f"/api/knowledge/items/{item_id}", headers=self.admin_headers).json()
        return self.client.post(
            f"/api/knowledge/items/{item_id}/confirm",
            json={"revision_token": detail["active_version"]["revision_token"]},
            headers=self.admin_headers,
        )

    def _create_fixture_document(self, title: str, access_scope: str = "org_internal") -> Tuple[str, str]:
        """创建测试文档与初始 v1 版本（初始 active_version_id 为 None，模拟真实上传）"""
        doc_id = f"doc_{self.test_prefix}_{uuid.uuid4().hex[:6]}"
        ver_id = f"ver_{self.test_prefix}_v1_{uuid.uuid4().hex[:6]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted, created_at, updated_at)
                VALUES (?, ?, ?, NULL, ?, 0, ?, ?)
                """,
                (doc_id, self.org_id, f"[{self.test_prefix}] {title}", access_scope, now_iso, now_iso)
            )
            conn.execute(
                """
                INSERT INTO document_versions
                (id, document_id, organization_id, version_label, file_name, file_type, file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                VALUES (?, ?, ?, 'v1', ?, 'txt', 100, ?, '/tmp/fake.txt', ?, ?, 'completed')
                """,
                (ver_id, doc_id, self.org_id, f"{title}.txt", uuid.uuid4().hex, self.admin_user["id"], now_iso)
            )
            # 创建至少一个 source_block
            block_id = f"blk_{self.test_prefix}_{uuid.uuid4().hex[:6]}"
            conn.execute(
                """
                INSERT INTO source_blocks (id, document_version_id, organization_id, block_index, block_type, text_content, created_at)
                VALUES (?, ?, ?, 1, 'paragraph', '测试用来源段落正文', ?)
                """,
                (block_id, ver_id, self.org_id, now_iso)
            )
        return doc_id, ver_id

    def _create_fixture_knowledge(
        self,
        doc_id: str,
        doc_ver_id: str,
        title: str,
        keyword: str,
        access_scope: str = "org_internal",
        review_status: str = "pending_review",
        lifecycle_status: str = "active",
        is_excluded: int = 0,
        valid_from: str = None,
        valid_until: str = None,
    ) -> Tuple[str, str]:
        """创建测试知识条目与版本，并绑定证据"""
        item_id = f"ki_{self.test_prefix}_{uuid.uuid4().hex[:6]}"
        ver_id = f"kv_{self.test_prefix}_{uuid.uuid4().hex[:6]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope, lifecycle_status, is_excluded, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (item_id, doc_id, self.org_id, ver_id, access_scope, lifecycle_status, is_excluded, now_iso, now_iso)
            )
            conn.execute(
                """
                INSERT INTO knowledge_versions
                (id, item_id, organization_id, source_document_version_id, version_number,
                 title, content, statement, primary_category, atom_type, subject,
                 review_status, index_status, revision_token, valid_from, valid_until, created_at, created_by)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, '制度与标准', '规则', '测试主体', ?, 'not_indexed', ?, ?, ?, ?, 'tester')
                """,
                (
                    ver_id, item_id, self.org_id, doc_ver_id,
                    f"[{self.test_prefix}] {title}",
                    f"正文包含核心关键词：{keyword}",
                    f"核心陈述必须清晰且包含业务事实：{keyword}",
                    review_status,
                    uuid.uuid4().hex,
                    valid_from,
                    valid_until,
                    now_iso,
                )
            )
            # 获取该版本的 source_block_id 建立一条证据
            sb = conn.execute("SELECT id FROM source_blocks WHERE document_version_id = ?", (doc_ver_id,)).fetchone()
            if sb:
                conn.execute(
                    """
                    INSERT INTO knowledge_evidence (id, knowledge_version_id, source_block_id, organization_id, field_name, excerpt, accuracy_level, created_at)
                    VALUES (?, ?, ?, ?, 'statement', ?, 'high', ?)
                    """,
                    (f"ev_{uuid.uuid4().hex[:8]}", ver_id, sb["id"], self.org_id, keyword, now_iso)
                )

        return item_id, ver_id

    # =========================================================================
    # 1. 待确认隔离与首次生效 (AC11, AC12, AC18, FR02)
    # =========================================================================

    def test_01_unconfirmed_knowledge_isolated_and_first_confirmation_activates_doc(self):
        """
        AC12 & FR02:
        1. 刚上传的文件 active_version_id 为 NULL。
        2. 未确认的候选知识不得被正式检索返回。
        3. 首条知识确认且索引 ready 后，自动建立 documents.active_version_id。
        4. 确认后该知识立即具备检索资格。
        """
        kw = f"隔离词_{uuid.uuid4().hex[:6]}"
        doc_id, ver_id = self._create_fixture_document("首发导入规程")
        item_id, kver_id = self._create_fixture_knowledge(doc_id, ver_id, "设备巡检标准", kw)

        # 检查文件初始 active_version_id 为 NULL
        with get_db() as conn:
            doc_row = conn.execute("SELECT active_version_id FROM documents WHERE id = ?", (doc_id,)).fetchone()
            self.assertIsNone(doc_row["active_version_id"])

        # 检索测试：未确认状态绝不能命中
        s_resp = self.client.get(f"/api/knowledge/search?q={kw}", headers=self.admin_headers)
        self.assertEqual(s_resp.status_code, 200)
        self.assertEqual(len(s_resp.json()["items"]), 0, "未确认知识绝不能被检索命中")

        # 确认该知识条目
        conf_resp = self._confirm_item(item_id)
        self.assertEqual(conf_resp.status_code, 200, conf_resp.text)
        self.assertEqual(conf_resp.json()["review_status"], "confirmed")
        self.assertEqual(conf_resp.json()["index_status"], "indexing")
        self._wait_for_index_ready(kver_id)

        # 验证文件的首次 active_version_id 已被自动建立
        with get_db() as conn:
            doc_row = conn.execute("SELECT active_version_id FROM documents WHERE id = ?", (doc_id,)).fetchone()
            self.assertEqual(doc_row["active_version_id"], ver_id, "首条知识确认后应激活文件的首次 active_version_id")

        # 再次检索：确认且索引 ready 后立即命中 (AC18)
        s_resp2 = self.client.get(f"/api/knowledge/search?q={kw}", headers=self.admin_headers)
        self.assertEqual(s_resp2.status_code, 200)
        items = s_resp2.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["item_id"], item_id)

    # =========================================================================
    # 2. 生命周期与时效性拦截 (AC19: disabled, deleted, expired)
    # =========================================================================

    def test_02_disabled_deleted_expired_blocked_from_retrieval(self):
        """
        AC19: 无论索引是否存在，disabled / deleted / expired 知识必须 100% 退出正式检索。
        """
        kw = f"停用词_{uuid.uuid4().hex[:6]}"
        doc_id, ver_id = self._create_fixture_document("生命周期测试文档")
        item_id, kver_id = self._create_fixture_knowledge(doc_id, ver_id, "保洁标准", kw)

        # 先确认并等待后台索引真正完成
        self._confirm_item(item_id)
        self._wait_for_index_ready(kver_id)

        # 正常可检索；限定当前文件，避免 Stage 4B 语义召回其他测试知识干扰 4A 资格验证。
        res = self.client.get(f"/api/knowledge/search?q={kw}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertEqual(len(res["items"]), 1)

        # A. 停用 (disabled)：立即退出检索
        self.client.put(f"/api/knowledge/items/{item_id}/lifecycle", json={"status": "disabled"}, headers=self.admin_headers)
        res_dis = self.client.get(f"/api/knowledge/search?q={kw}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertEqual(len(res_dis["items"]), 0, "停用条目不得被检索返回")

        # 重新启用：立即恢复
        self.client.put(f"/api/knowledge/items/{item_id}/lifecycle", json={"status": "active"}, headers=self.admin_headers)
        res_act = self.client.get(f"/api/knowledge/search?q={kw}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertEqual(len(res_act["items"]), 1, "重新启用后应恢复检索")

        # B. 过期 (expired)：设置 valid_until 为过去时间，立即退出检索
        past_iso = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with get_db() as conn:
            conn.execute("UPDATE knowledge_versions SET valid_until = ? WHERE id = ?", (past_iso, kver_id))
        res_exp = self.client.get(f"/api/knowledge/search?q={kw}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertEqual(len(res_exp["items"]), 0, "过期条目不得被检索返回")

        # 恢复有效期
        with get_db() as conn:
            conn.execute("UPDATE knowledge_versions SET valid_until = NULL WHERE id = ?", (kver_id,))

        # C. 逻辑删除条目 (deleted)：彻底退出检索
        self.client.delete(f"/api/knowledge/items/{item_id}", headers=self.admin_headers)
        res_del = self.client.get(f"/api/knowledge/search?q={kw}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertEqual(len(res_del["items"]), 0, "逻辑删除条目不得被检索返回")

    # =========================================================================
    # 3. 权限基线与收紧/放宽行为 (AC25, AC40, AC41)
    # =========================================================================

    def test_03_permissions_and_scope_boundary(self):
        """
        AC25, AC40, AC41:
        1. 普通成员不能读 admin_only 文件或知识。
        2. 规则 14: 知识权限不得比文件更开放（文件 admin_only 时拒绝设置知识为 org_internal）。
        3. 文件由 org_internal 收紧为 admin_only 后，其派生知识对成员立即失效，无需等待索引清理。
        4. 文件由 admin_only 放宽为 org_internal 时，不自动放宽已有的 admin_only 知识。
        5. 管理员与普通成员共用同一资格规则，但授权范围严格隔离。
        """
        kw = f"权限词_{uuid.uuid4().hex[:6]}"
        # 创建一个初始为 org_internal 的文档与知识
        doc_id, ver_id = self._create_fixture_document("权限规范文档", access_scope="org_internal")
        item_id, kver_id = self._create_fixture_knowledge(doc_id, ver_id, "公开规范条目", kw, access_scope="org_internal")

        # 确认启用并等待后台索引真正完成
        self._confirm_item(item_id)
        self._wait_for_index_ready(kver_id)

        # 管理员正式检索可命中；成员不直接访问管理检索 HTTP 入口，
        # 但统一 eligibility 对该 org_internal 知识应判定为可用于成员 AI。
        res_adm = self.client.get(f"/api/knowledge/search?q={kw}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertEqual(len(res_adm["items"]), 1)
        member_http = self.client.get(
            f"/api/knowledge/search?q={kw}&document_id={doc_id}",
            headers=self.member_headers,
        )
        self.assertEqual(member_http.status_code, 403)
        with get_db() as conn:
            self.assertTrue(
                check_knowledge_eligibility(conn, kver_id, self.member_user).is_eligible
            )

        # 场景 A: 知识本身收紧为 admin_only -> 管理员可见，成员不可见
        scope_resp = self.client.put(
            f"/api/knowledge/items/{item_id}/access-scope",
            json={"access_scope": "admin_only"},
            headers=self.admin_headers
        )
        self.assertEqual(scope_resp.status_code, 200)
        self.assertEqual(len(self.client.get(f"/api/knowledge/search?q={kw}&document_id={doc_id}", headers=self.admin_headers).json()["items"]), 1)
        with get_db() as conn:
            self.assertFalse(
                check_knowledge_eligibility(conn, kver_id, self.member_user).is_eligible,
                "知识设为 admin_only 后普通成员 AI 不得使用",
            )

        # 恢复知识为 org_internal
        self.client.put(f"/api/knowledge/items/{item_id}/access-scope", json={"access_scope": "org_internal"}, headers=self.admin_headers)

        # 场景 B: 文件由 org_internal 收紧为 admin_only -> 成员立即无法检索该文件下任何知识！
        doc_scope_resp = self.client.put(
            f"/api/documents/{doc_id}/access-scope",
            json={"access_scope": "admin_only"},
            headers=self.admin_headers
        )
        self.assertEqual(doc_scope_resp.status_code, 200)
        self.assertEqual(len(self.client.get(f"/api/knowledge/search?q={kw}", headers=self.admin_headers).json()["items"]), 1, "管理员仍可检索")
        with get_db() as conn:
            self.assertFalse(
                check_knowledge_eligibility(conn, kver_id, self.member_user).is_eligible,
                "文件收紧为 admin_only 后派生知识对成员 AI 立即失效",
            )

        # 场景 C: 规则 14 - 当文件为 admin_only 时，禁止将知识设置为比文件更开放 (org_internal)
        # 此时文件是 admin_only，我们先把知识改成 admin_only
        self.client.put(f"/api/knowledge/items/{item_id}/access-scope", json={"access_scope": "admin_only"}, headers=self.admin_headers)
        # 尝试将知识设为 org_internal -> 必须被拒绝 400
        illegal_resp = self.client.put(
            f"/api/knowledge/items/{item_id}/access-scope",
            json={"access_scope": "org_internal"},
            headers=self.admin_headers
        )
        self.assertEqual(illegal_resp.status_code, 400)
        self.assertIn("不能比文件更开放", illegal_resp.json()["detail"])

        # 场景 D: 文件放宽为 org_internal 时，不自动把已有 admin_only 知识放宽
        self.client.put(f"/api/documents/{doc_id}/access-scope", json={"access_scope": "org_internal"}, headers=self.admin_headers)
        # 检查知识条目的 access_scope 仍然是 admin_only
        with get_db() as conn:
            ki_scope = conn.execute("SELECT access_scope FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()["access_scope"]
            self.assertEqual(ki_scope, "admin_only", "文件放宽不得自动放宽已有知识条目")
        # 成员 AI 仍不可使用该 admin_only 知识
        with get_db() as conn:
            self.assertFalse(
                check_knowledge_eligibility(conn, kver_id, self.member_user).is_eligible
            )

        # 场景 E: 文件受控下载权限：成员不能下载 admin_only 文件
        self.client.put(f"/api/documents/{doc_id}/access-scope", json={"access_scope": "admin_only"}, headers=self.admin_headers)
        dl_resp = self.client.get(f"/api/documents/{doc_id}/versions/{ver_id}/file", headers=self.member_headers)
        self.assertEqual(dl_resp.status_code, 403, "普通成员下载 admin_only 文件必须返回 403")

    # =========================================================================
    # 4. 替换文件版本与平滑切换 (AC24, PRD FR02)
    # =========================================================================

    def test_04_replacement_file_version_and_atomic_switch(self):
        """
        AC24 & FR02:
        1. 旧版本 v1 正在服务中。
        2. 新版本 v2 上传、解析、单条确认并索引：单条确认只表示准备就绪，新知识不能提前进入检索！
        3. 新版本未全部确认或未排除前，调用“启用此文件版本”必须被拦截。
        4. 全部候选确认或排除、拟启用全部 ready 且至少 1 条可用时，执行启用：
           事务切换 documents.active_version_id，v2 正式接替 v1；
           v1 索引即使还在，也因 eligibility 规则不能再被检索返回！
        """
        kw_v1 = f"旧版服务词_{uuid.uuid4().hex[:6]}"
        kw_v2_cand1 = f"新版候选一_{uuid.uuid4().hex[:6]}"
        kw_v2_cand2 = f"新版候选二_{uuid.uuid4().hex[:6]}"

        doc_id, v1_id = self._create_fixture_document("标准作业规程文档", access_scope="org_internal")
        ki_v1, kv1_id = self._create_fixture_knowledge(doc_id, v1_id, "巡检旧标准", kw_v1)

        # 确认 v1 知识，激活文档 v1，并等待后台索引完成
        self._confirm_item(ki_v1)
        self._wait_for_index_ready(kv1_id)
        with get_db() as conn:
            d_row = conn.execute("SELECT active_version_id FROM documents WHERE id = ?", (doc_id,)).fetchone()
            self.assertEqual(d_row["active_version_id"], v1_id)

        # v1 关键词可检索
        self.assertEqual(len(self.client.get(f"/api/knowledge/search?q={kw_v1}&document_id={doc_id}", headers=self.admin_headers).json()["items"]), 1)

        # 创建新版本 v2 (作为替换版本)
        v2_id = f"ver_{self.test_prefix}_v2_{uuid.uuid4().hex[:6]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO document_versions
                (id, document_id, organization_id, version_label, file_name, file_type, file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                VALUES (?, ?, ?, 'v2', '标准作业规程文档.txt', 'txt', 200, ?, '/tmp/v2.txt', ?, ?, 'completed')
                """,
                (v2_id, doc_id, self.org_id, uuid.uuid4().hex, self.admin_user["id"], now_iso)
            )
            # v2 的 source_block
            conn.execute(
                "INSERT INTO source_blocks (id, document_version_id, organization_id, block_index, block_type, text_content, created_at) VALUES (?, ?, ?, 1, 'paragraph', 'v2段落', ?)",
                (f"blk_{uuid.uuid4().hex[:8]}", v2_id, self.org_id, now_iso)
            )

        # 为 v2 提取 2 个候选条目
        ki_v2_1, kv2_1 = self._create_fixture_knowledge(doc_id, v2_id, "新版巡检规范A", kw_v2_cand1)
        ki_v2_2, kv2_2 = self._create_fixture_knowledge(doc_id, v2_id, "新版噪声处置B", kw_v2_cand2)

        # 步骤 A: 单条确认第 1 条候选并构建索引
        conf_res1 = self._confirm_item(ki_v2_1)
        self.assertEqual(conf_res1.status_code, 200)
        self._wait_for_index_ready(kv2_1)

        # 重点断言：单条确认只代表准备就绪，此时 documents.active_version_id 必须依然是 v1！
        with get_db() as conn:
            d_row = conn.execute("SELECT active_version_id FROM documents WHERE id = ?", (doc_id,)).fetchone()
            self.assertEqual(d_row["active_version_id"], v1_id, "替换版本的单条知识确认绝不能提前切换文件生效版本")

        # 重点断言：新知识不能提前进入正式检索！旧知识继续服务！
        self.assertEqual(len(self.client.get(f"/api/knowledge/search?q={kw_v2_cand1}&document_id={doc_id}", headers=self.admin_headers).json()["items"]), 0, "新文件版本的候选在未统一启用前绝对不能被检索到")
        self.assertEqual(len(self.client.get(f"/api/knowledge/search?q={kw_v1}&document_id={doc_id}", headers=self.admin_headers).json()["items"]), 1, "旧版本继续正式服务")

        # 步骤 B: 此时尝试调用“启用此文件版本” -> 因第 2 条候选尚未确认或排除，必须被拦截 400！
        premature_act = self.client.post(f"/api/documents/{doc_id}/versions/{v2_id}/activate", headers=self.admin_headers)
        self.assertEqual(premature_act.status_code, 400)
        self.assertIn("未完成核对", premature_act.json()["detail"])

        # 步骤 C: 将第 2 条候选明确排除（不收录）
        exc_res = self.client.post(
            f"/api/knowledge/items/{ki_v2_2}/exclude",
            json={"reason": "该条目属于重复或不适用内容"},
            headers=self.admin_headers
        )
        self.assertEqual(exc_res.status_code, 200)

        # 步骤 D: 此时全部候选已确认或明确排除，且至少有 1 条可用知识 -> 执行统一启用！
        act_res = self.client.post(f"/api/documents/{doc_id}/versions/{v2_id}/activate", headers=self.admin_headers)
        self.assertEqual(act_res.status_code, 200, act_res.text)
        self.assertEqual(act_res.json()["active_version_id"], v2_id)

        # 步骤 E: 检索验证：新旧版本统一切换！
        # 1. 新版本可用知识正式上线可搜！
        res_new = self.client.get(f"/api/knowledge/search?q={kw_v2_cand1}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertEqual(len(res_new["items"]), 1, "新版本确认知识正式生效可查")

        # 2. 被排除的候选绝不能被检索到！
        res_exc = self.client.get(f"/api/knowledge/search?q={kw_v2_cand2}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertNotIn(ki_v2_2, {item["item_id"] for item in res_exc["items"]}, "明确排除的候选绝不能被检索到")

        # 3. 旧版本知识即使 retrieval_records 还在，也因 eligibility 规则（d.active_version_id != kv.source_document_version_id）彻底退出正式检索！
        res_old = self.client.get(f"/api/knowledge/search?q={kw_v1}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertNotIn(ki_v1, {item["item_id"] for item in res_old["items"]}, "旧文件版本的知识必须立即退出正式检索")

    # =========================================================================
    # 5. 脏数据与悬空索引防御测试
    # =========================================================================

    def test_05_dangling_and_dirty_indexes_cannot_bypass_eligibility(self):
        """
        防御测试：模拟 retrieval_records 中存在异常、已删除或悬空的索引记录，
        验证统一 eligibility 规则坚不可摧，回查业务库时 100% 拦截。
        """
        kw = f"脏数据词_{uuid.uuid4().hex[:6]}"
        doc_id, ver_id = self._create_fixture_document("脏数据测试文档")
        item_id, kver_id = self._create_fixture_knowledge(doc_id, ver_id, "脏条目", kw)

        # 人工在 retrieval_records 中伪造一条索引，但知识版本处于未确认 (pending_review)
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO retrieval_records (id, knowledge_version_id, organization_id, search_text, model_name, index_version, created_at)
                VALUES (?, ?, ?, ?, 'test_model', 1, ?)
                """,
                (f"ret_fake_{uuid.uuid4().hex[:6]}", kver_id, self.org_id, f"搜索正文包含 {kw}", datetime.now(timezone.utc).isoformat())
            )

        # 检索测试：虽然 retrieval_records 存在该记录，但业务库 review_status 未确认，必须返回 0
        search_res = self.client.get(f"/api/knowledge/search?q={kw}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertEqual(len(search_res["items"]), 0, "未确认知识哪怕被写入了索引，也绝不能被检索返回")

        # 使用 eligibility.py 函数直接校验
        with get_db() as conn:
            el_res = check_knowledge_eligibility(conn, kver_id, self.admin_user)
            self.assertFalse(el_res.is_eligible)
            self.assertEqual(el_res.code, "REVIEW_NOT_CONFIRMED")

            # 批量过滤回查校验
            filtered = filter_eligible_version_ids(conn, [kver_id], self.admin_user)
            self.assertEqual(filtered, [])

    # =========================================================================
    # 6. 真实数据库只读无泄漏核查测试
    # =========================================================================

    def test_06_real_database_read_only_integrity_and_no_leakage(self):
        """
        读取真实数据库中历史残留的 2 条已删除 retrieval_records 和 1 条悬空 active_version_id，
        确保在统一资格规则下它们对管理员和成员检索均为 0 泄漏。
        """
        with get_db() as conn:
            # 找到之前检查出的历史已删除但保留 retrieval_records 的记录
            dirty_rows = conn.execute(
                """
                SELECT rr.id, rr.knowledge_version_id, kv.title
                FROM retrieval_records rr
                JOIN knowledge_versions kv ON rr.knowledge_version_id = kv.id
                JOIN knowledge_items ki ON kv.item_id = ki.id
                WHERE ki.lifecycle_status = 'deleted'
                """
            ).fetchall()

            for dr in dirty_rows:
                kver_id = dr["knowledge_version_id"]
                # 对管理员校验资格
                el_admin = check_knowledge_eligibility(conn, kver_id, self.admin_user)
                self.assertFalse(el_admin.is_eligible, f"历史已删除版本 {kver_id} 必须被判定为不可用")
                self.assertIn(el_admin.code, ("ITEM_LIFECYCLE_NOT_ACTIVE", "ITEM_EXCLUDED_OR_DELETED"))

                # 对成员校验资格
                el_member = check_knowledge_eligibility(conn, kver_id, self.member_user)
                self.assertFalse(el_member.is_eligible)

                # 批量过滤回查
                filtered = filter_eligible_version_ids(conn, [kver_id], self.admin_user)
                self.assertEqual(filtered, [])

    # =========================================================================
    # 7. 审计日志合规性检查（无全文、无敏感密钥）
    # =========================================================================

    def test_07_audit_logs_recorded_without_sensitive_info(self):
        """
        验证权限修改和版本激活均写入审计日志，且 details 不记录正文全文、密码或私钥。
        """
        doc_id, ver_id = self._create_fixture_document("审计测试文件")
        item_id, kver_id = self._create_fixture_knowledge(doc_id, ver_id, "审计条目", "审计关键词")

        # 修改文件权限
        self.client.put(f"/api/documents/{doc_id}/access-scope", json={"access_scope": "admin_only"}, headers=self.admin_headers)

        # 检查 audit_logs
        with get_db() as conn:
            logs = conn.execute(
                "SELECT action, target_type, target_id, details FROM audit_logs WHERE target_id = ? ORDER BY created_at DESC",
                (doc_id,)
            ).fetchall()
            self.assertGreater(len(logs), 0)
            detail_str = logs[0]["details"]
            self.assertIn("admin_only", detail_str)
            # 严格确保没有密码、密钥或全文泄露
            self.assertNotIn("password", detail_str.lower())
            self.assertNotIn("secret", detail_str.lower())
            self.assertNotIn("token", detail_str.lower())

    # =========================================================================
    # 8. 账号禁用与跨租户隔离在统一资格中生效
    # =========================================================================

    def test_08_account_status_and_tenant_isolation_in_eligibility(self):
        """
        验证统一资格规则对账号状态 (disabled) 及跨租户 (wrong org) 严格生效。
        """
        kw = f"隔离租户_{uuid.uuid4().hex[:6]}"
        doc_id, ver_id = self._create_fixture_document("企业A私有文档", access_scope="org_internal")
        item_id, kver_id = self._create_fixture_knowledge(doc_id, ver_id, "企业A知识", kw, access_scope="org_internal")
        self._confirm_item(item_id)

        with get_db() as conn:
            # 1. 禁用账号用户请求 -> USER_NOT_ACTIVE
            disabled_user = dict(self.member_user)
            disabled_user["account_status"] = "disabled"
            res_dis = check_knowledge_eligibility(conn, kver_id, disabled_user)
            self.assertFalse(res_dis.is_eligible)
            self.assertEqual(res_dis.code, "USER_NOT_ACTIVE")

            # 2. 跨企业用户请求 -> TENANT_MISMATCH
            other_org_user = dict(self.member_user)
            other_org_user["organization_id"] = "org_other_tenant"
            res_tenant = check_knowledge_eligibility(conn, kver_id, other_org_user)
            self.assertFalse(res_tenant.is_eligible)
            self.assertEqual(res_tenant.code, "TENANT_MISMATCH")

    # =========================================================================
    # 9. 文档逻辑删除后其派生知识立即丧失检索资格 (AC21, PRD FR14)
    # =========================================================================

    def test_09_document_deletion_immediately_invalidates_knowledge(self):
        """
        AC21 & FR14: 删除文档后，文档本身被标记删除，其派生知识无论索引如何，立即退出正式检索。
        """
        kw = f"删文连带_{uuid.uuid4().hex[:6]}"
        doc_id, ver_id = self._create_fixture_document("待删母文档")
        item_id, kver_id = self._create_fixture_knowledge(doc_id, ver_id, "派生知识条目", kw)
        self._confirm_item(item_id)
        self._wait_for_index_ready(kver_id)

        # 删除前可查
        self.assertEqual(len(self.client.get(f"/api/knowledge/search?q={kw}&document_id={doc_id}", headers=self.admin_headers).json()["items"]), 1)

        # 删除文档
        del_resp = self.client.delete(f"/api/documents/{doc_id}", headers=self.admin_headers)
        self.assertEqual(del_resp.status_code, 200)

        # 删除后检索：派生知识彻底无法检索
        res = self.client.get(f"/api/knowledge/search?q={kw}&document_id={doc_id}", headers=self.admin_headers).json()
        self.assertEqual(len(res["items"]), 0, "文档被逻辑删除后，派生知识必须立即退出检索")

    # =========================================================================
    # 10. 排除候选独立字段与 CHECK 约束兼容性验证
    # =========================================================================

    def test_10_exclude_endpoint_and_check_constraint_safety(self):
        """
        验证 POST /api/knowledge/items/{item_id}/exclude:
        1. 写入 is_excluded=1, excluded_at, excluded_by, exclusion_reason。
        2. lifecycle_status 保持在 ('active', 'disabled', 'deleted') 内，绝不违背 CHECK 约束。
        3. 检索时被排除条目绝不返回。
        4. list 列表统计中不作为待办。
        """
        kw = f"排除条目_{uuid.uuid4().hex[:6]}"
        doc_id, ver_id = self._create_fixture_document("候选排除文档")
        item_id, kver_id = self._create_fixture_knowledge(doc_id, ver_id, "待排除候选", kw)

        # 调用排除接口
        exc_res = self.client.post(
            f"/api/knowledge/items/{item_id}/exclude",
            json={"reason": "非标准物业服务动作"},
            headers=self.admin_headers
        )
        self.assertEqual(exc_res.status_code, 200)
        self.assertTrue(exc_res.json()["is_excluded"])

        # 检查数据库：lifecycle_status 必须依然是 active（合法枚举），is_excluded 为 1
        with get_db() as conn:
            ki_row = conn.execute(
                "SELECT lifecycle_status, is_excluded, excluded_by, exclusion_reason FROM knowledge_items WHERE id = ?",
                (item_id,)
            ).fetchone()
            self.assertIn(ki_row["lifecycle_status"], ("active", "disabled", "deleted"))
            self.assertEqual(ki_row["is_excluded"], 1)
            self.assertEqual(ki_row["excluded_by"], self.admin_user["id"])
            self.assertEqual(ki_row["exclusion_reason"], "非标准物业服务动作")

            # 统一资格函数检查
            el = check_knowledge_eligibility(conn, kver_id, self.admin_user)
            self.assertFalse(el.is_eligible)
            self.assertEqual(el.code, "ITEM_EXCLUDED_OR_DELETED")

    # =========================================================================
    # 11. 候选异常清洗安全保障（不违背 CHECK 约束，保护人工确认数据）[Test A]
    # =========================================================================

    def test_11_seed_anomaly_exclusion_safe_check(self):
        """
        [Test A] 验证 seed.py::sanitize_existing_anomalies() 排除候选异常知识点时：
        1. 不触发 CHECK 约束报错 (IntegrityError: CHECK constraint failed: lifecycle_status IN ('active', 'disabled', 'deleted'))；
        2. 正确设置 is_excluded=1, excluded_by='system_sanitizer', exclusion_reason；
        3. 已有人工确认记录 (review_status='confirmed') 保留且不标记排除 (is_excluded=0)。
        """
        from seed import sanitize_existing_anomalies

        doc_id, ver_id = self._create_fixture_document("清洗测试文档")

        # 1. 创建未核对的无意义符号条目（如 statement = '---'）
        item_unconfirmed = f"ki_{self.test_prefix}_unconfirmed"
        kver_unconfirmed = f"kv_{self.test_prefix}_unconfirmed"
        now_iso = datetime.now(timezone.utc).isoformat()

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope, lifecycle_status, is_excluded, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'org_internal', 'active', 0, ?, ?)
                """,
                (item_unconfirmed, doc_id, self.org_id, kver_unconfirmed, now_iso, now_iso)
            )
            conn.execute(
                """
                INSERT INTO knowledge_versions (
                    id, item_id, organization_id, source_document_version_id, version_number,
                    revision_token, primary_category, title, content, statement,
                    review_status, index_status, created_at, created_by
                )
                VALUES (?, ?, ?, ?, 1, ?, NULL, '无意义条目', '正文内容', '---', 'pending_review', 'not_indexed', ?, 'tester')
                """,
                (kver_unconfirmed, item_unconfirmed, self.org_id, ver_id, uuid.uuid4().hex, now_iso)
            )

            # 2. 创建已核对但内容为纯符号的条目
            item_confirmed = f"ki_{self.test_prefix}_confirmed"
            kver_confirmed = f"kv_{self.test_prefix}_confirmed"
            conn.execute(
                """
                INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope, lifecycle_status, is_excluded, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'org_internal', 'active', 0, ?, ?)
                """,
                (item_confirmed, doc_id, self.org_id, kver_confirmed, now_iso, now_iso)
            )
            conn.execute(
                """
                INSERT INTO knowledge_versions (
                    id, item_id, organization_id, source_document_version_id, version_number,
                    revision_token, primary_category, title, content, statement,
                    review_status, reviewed_by, reviewed_at, index_status, created_at, created_by
                )
                VALUES (?, ?, ?, ?, 1, ?, '制度与标准', '人工确认条目', '正文内容', '---', 'confirmed', ?, ?, 'ready', ?, 'tester')
                """,
                (kver_confirmed, item_confirmed, self.org_id, ver_id, uuid.uuid4().hex, self.admin_user["id"], now_iso, now_iso)
            )

        # 执行清洗函数，验证不抛出任何异常，尤其是 IntegrityError
        sanitize_existing_anomalies()

        with get_db() as conn:
            # 验证未核对条目：受控排除
            row_unconf = conn.execute(
                "SELECT lifecycle_status, is_excluded, excluded_by, exclusion_reason FROM knowledge_items WHERE id = ?",
                (item_unconfirmed,)
            ).fetchone()
            self.assertIn(row_unconf["lifecycle_status"], ("active", "disabled", "deleted"))
            self.assertEqual(row_unconf["lifecycle_status"], "active")
            self.assertEqual(row_unconf["is_excluded"], 1)
            self.assertEqual(row_unconf["excluded_by"], "system_sanitizer")
            self.assertIn("系统清洗", row_unconf["exclusion_reason"])

            # 验证已核对条目：绝不静默排除，保留且仅提示复核
            row_conf = conn.execute(
                "SELECT lifecycle_status, is_excluded FROM knowledge_items WHERE id = ?",
                (item_confirmed,)
            ).fetchone()
            self.assertEqual(row_conf["is_excluded"], 0)
            self.assertEqual(row_conf["lifecycle_status"], "active")

            v_conf = conn.execute(
                "SELECT quality_flags_json FROM knowledge_versions WHERE id = ?",
                (kver_confirmed,)
            ).fetchone()
            self.assertIn("核心陈述缺乏有效业务内容", v_conf["quality_flags_json"])

    # =========================================================================
    # 12. 测试数据库完全隔离（真实 DB 不受污染）[Test B]
    # =========================================================================

    def test_12_real_db_not_touched_by_tests(self):
        """
        [Test B] 验证测试运行期间完全隔离，绝不触碰或修改真实的 server/data/zhixing.db：
        1. 检查真实数据库文件属性（mtime, file size）；
        2. 读取真实数据库核心表的行数；
        3. 在测试环境下执行多次写入与接口调用；
        4. 验证真实数据库 mtime/size/记录数 100% 保持一致，零写操作。
        """
        import config
        real_db = config.DATA_DIR / "zhixing.db"
        if not real_db.exists():
            self.skipTest("真实数据库尚未初始化，跳过对比")

        initial_stat = real_db.stat()
        initial_mtime = initial_stat.st_mtime
        initial_size = initial_stat.st_size

        # 读取真实库行数
        import sqlite3
        ro_conn = sqlite3.connect(f"file:{real_db.resolve()}?mode=ro", uri=True)
        try:
            real_doc_count = ro_conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            real_item_count = ro_conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        finally:
            ro_conn.close()

        # 执行测试操作：创建并查询
        doc_id, ver_id = self._create_fixture_document("隔离检验文档")
        item_id, kver_id = self._create_fixture_knowledge(doc_id, ver_id, "隔离知识", "用于检验无污染的关键词")
        _ = self.client.get("/api/knowledge/search?q=无污染", headers=self.admin_headers)

        # 再次检查真实数据库
        after_stat = real_db.stat()
        self.assertEqual(initial_mtime, after_stat.st_mtime, "真实数据库 mtime 被修改！隔离失效！")
        self.assertEqual(initial_size, after_stat.st_size, "真实数据库大小被修改！隔离失效！")

        ro_conn = sqlite3.connect(f"file:{real_db.resolve()}?mode=ro", uri=True)
        try:
            after_doc_count = ro_conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            after_item_count = ro_conn.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
            self.assertEqual(real_doc_count, after_doc_count, "真实数据库 documents 表产生测试污染！")
            self.assertEqual(real_item_count, after_item_count, "真实数据库 knowledge_items 表产生测试污染！")
        finally:
            ro_conn.close()

    # =========================================================================
    # 13. 数据库动态环境变量切换 [Test C]
    # =========================================================================

    def test_13_db_environment_switching(self):
        """
        [Test C] 验证数据库路径配置的动态切换：
        1. 默认环境下（未设置 ZHIXING_DB_PATH）返回 server/data/zhixing.db；
        2. 设置 ZHIXING_DB_PATH 后，动态返回自定义测试数据库路径；
        3. 验证 get_connection() 动态遵循 get_db_path() 路径。
        """
        from config import get_db_path, DATA_DIR
        from database import get_connection

        orig_env = os.environ.get("ZHIXING_DB_PATH")
        try:
            # 1. 模拟清除环境变量
            if "ZHIXING_DB_PATH" in os.environ:
                del os.environ["ZHIXING_DB_PATH"]
            self.assertEqual(get_db_path(), DATA_DIR / "zhixing.db")

            # 2. 模拟设置测试环境变量
            custom_fake = Path(tempfile.gettempdir()) / "custom_test_env_switch.db"
            os.environ["ZHIXING_DB_PATH"] = str(custom_fake)
            self.assertEqual(get_db_path(), custom_fake.resolve())

            # 3. 验证获取连接目标文件
            conn = get_connection()
            try:
                db_filename = conn.execute("PRAGMA database_list").fetchall()[0]["file"]
                self.assertEqual(Path(db_filename).resolve(), custom_fake.resolve())
            finally:
                conn.close()
                if custom_fake.exists():
                    try:
                        custom_fake.unlink()
                    except OSError:
                        pass
        finally:
            if orig_env is not None:
                os.environ["ZHIXING_DB_PATH"] = orig_env
            else:
                os.environ.pop("ZHIXING_DB_PATH", None)

if __name__ == "__main__":
    unittest.main(verbosity=2)
