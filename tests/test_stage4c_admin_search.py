"""Stage 4C 管理员正式检索：多选筛选、范围与历史来源版本验收。"""
import sys
import time
import uuid
import unittest
from pathlib import Path
from datetime import datetime, timezone

from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
TESTS_DIR = Path(__file__).resolve().parent
for path in (str(SERVER_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from main import app
from database import get_db
import test_stage4b_hybrid_retrieval as stage4b


class TestStage4CAdminSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_env_helper import setup_test_db
        cls.test_db = setup_test_db("stage4c_tests")
        cls.client = TestClient(app)
        login = cls.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Admin@Zhixing2026"},
        )
        assert login.status_code == 200, login.text
        cls.admin_user = login.json()["user"]
        cls.admin_headers = {"Authorization": f"Bearer {login.json()['token']}"}
        cls.org_id = cls.admin_user["organization_id"]

    @classmethod
    def tearDownClass(cls):
        from test_env_helper import cleanup_test_db
        cleanup_test_db(cls.test_db)

    def setUp(self):
        self.prefix = "s4c_" + uuid.uuid4().hex[:8]

    def _wait_status(self, *args, **kwargs):
        return stage4b.TestStage4BHybridRetrieval._wait_status(self, *args, **kwargs)

    def _create_document(self, *args, **kwargs):
        return stage4b.TestStage4BHybridRetrieval._create_document(self, *args, **kwargs)

    def _create_knowledge(self, *args, **kwargs):
        return stage4b.TestStage4BHybridRetrieval._create_knowledge(self, *args, **kwargs)

    def _confirm_and_wait(self, *args, **kwargs):
        return stage4b.TestStage4BHybridRetrieval._confirm_and_wait(self, *args, **kwargs)

    def test_01_multiselect_or_across_dimensions_and_document_scope(self):
        doc_a, ver_a, blocks_a = self._create_document("客诉处理手册甲")
        doc_b, ver_b, blocks_b = self._create_document("客诉处理手册乙")

        item_a1, kv_a1, _ = self._create_knowledge(
            doc_a, ver_a, blocks_a,
            "住宅投诉闭环标准",
            "投诉处理应建立闭环记录并在约定时限内反馈。",
            problem_tags=["投诉未闭环"],
            primary_category="制度与标准",
            customer_types=["住宅物业"],
            business_scenes=["客诉处理"],
        )
        item_a2, kv_a2, _ = self._create_knowledge(
            doc_a, ver_a, blocks_a,
            "商业投诉响应方法",
            "投诉处理需要记录首次响应时间并持续跟进。",
            problem_tags=["响应超时"],
            primary_category="方法与工具",
            customer_types=["商业物业"],
            business_scenes=["客诉处理"],
        )
        item_b, kv_b, _ = self._create_knowledge(
            doc_b, ver_b, blocks_b,
            "园区投诉案例",
            "投诉处理案例要求复盘问题来源并形成改进清单。",
            problem_tags=["投诉未闭环"],
            primary_category="项目案例",
            customer_types=["产业园区"],
            business_scenes=["项目复盘"],
        )

        for item_id, version_id in ((item_a1, kv_a1), (item_a2, kv_a2), (item_b, kv_b)):
            self._confirm_and_wait(item_id, version_id)

        multi_params = [
            ("q", "投诉处理"),
            ("category", "制度与标准"),
            ("category", "方法与工具"),
            ("customer_type", "住宅物业"),
            ("customer_type", "商业物业"),
            ("business_scene", "客诉处理"),
            ("problem_tag", "投诉未闭环"),
            ("problem_tag", "响应超时"),
        ]
        response = self.client.get("/api/knowledge/search", params=multi_params, headers=self.admin_headers)
        self.assertEqual(response.status_code, 200, response.text)
        result_ids = {item["version_id"] for item in response.json()["items"]}
        self.assertEqual(result_ids, {kv_a1, kv_a2})

        current = self.client.get(
            "/api/knowledge/search",
            params=[("q", "投诉处理"), ("document_id", doc_a)],
            headers=self.admin_headers,
        )
        self.assertEqual(current.status_code, 200, current.text)
        current_ids = {item["version_id"] for item in current.json()["items"]}
        self.assertEqual(current_ids, {kv_a1, kv_a2})

        all_docs = self.client.get(
            "/api/knowledge/search",
            params={"q": "投诉处理"},
            headers=self.admin_headers,
        )
        self.assertEqual(all_docs.status_code, 200, all_docs.text)
        all_ids = {item["version_id"] for item in all_docs.json()["items"]}
        self.assertTrue({kv_a1, kv_a2, kv_b}.issubset(all_ids))

    def test_02_source_uses_bound_historical_document_version(self):
        doc_id, old_doc_version, blocks = self._create_document("历史制度文件")
        item_id, knowledge_version, _ = self._create_knowledge(
            doc_id,
            old_doc_version,
            blocks,
            "历史版本来源定位",
            "历史来源定位必须固定到知识确认时绑定的文件版本。",
            problem_tags=["版本追溯"],
        )

        self._confirm_and_wait(item_id, knowledge_version)
        response = self.client.get(
            "/api/knowledge/search",
            params={"q": "历史来源定位", "document_id": doc_id},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)
        result = response.json()["items"][0]

        # 模拟检索完成后文件出现并切换到新版本；UI 仍必须使用结果中捕获的旧来源版本。
        now = datetime.now(timezone.utc).isoformat()
        new_doc_version = f"ver_{self.prefix}_new"
        with get_db() as conn:
            conn.execute(
                """INSERT INTO document_versions
                   (id, document_id, organization_id, version_label, file_name,
                    file_type, file_size, content_hash, storage_reference,
                    uploaded_by, uploaded_at, processing_status)
                   VALUES (?, ?, ?, 'v2', '历史制度文件_v2.txt', 'txt', 256, ?, ?, ?, ?, 'completed')""",
                (
                    new_doc_version,
                    doc_id,
                    self.org_id,
                    uuid.uuid4().hex,
                    f"/tmp/{new_doc_version}.txt",
                    self.admin_user["id"],
                    now,
                ),
            )
            conn.execute(
                "UPDATE documents SET active_version_id = ?, updated_at = ? WHERE id = ?",
                (new_doc_version, now, doc_id),
            )

        self.assertEqual(result["source"]["document_version_id"], old_doc_version)
        self.assertNotEqual(result["source"]["document_version_id"], new_doc_version)
        self.assertEqual(result["source"]["document_version_label"], "v1")
        self.assertTrue(result["evidence"])
        evidence_anchors = {
            evidence["source_locator"]["paragraph_anchor"]
            for evidence in result["evidence"]
        }
        self.assertIn("[line_1]", evidence_anchors)

        source_blocks = self.client.get(
            f"/api/documents/{doc_id}/versions/{old_doc_version}/source-blocks",
            headers=self.admin_headers,
        )
        self.assertEqual(source_blocks.status_code, 200, source_blocks.text)
        self.assertEqual(source_blocks.json()[0]["paragraph_anchor"], "[line_1]")


if __name__ == "__main__":
    unittest.main(verbosity=2)
