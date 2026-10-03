"""版本治理与生命周期专项验收。"""
import sys
import sqlite3
import unittest
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
TESTS_DIR = Path(__file__).resolve().parent
for _path in (str(SERVER_DIR), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import main as main_module
import indexing
from database import get_db
from main import app
import test_stage4b_hybrid_retrieval as stage4b


class TestVersionGovernanceLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_env_helper import setup_test_db
        cls.test_db = setup_test_db("version_governance")
        cls.client = TestClient(app)
        admin = cls.client.post("/api/auth/login", json={
            "username": "admin", "password": "Admin@Zhixing2026"
        })
        assert admin.status_code == 200, admin.text
        cls.admin_headers = {"Authorization": f"Bearer {admin.json()['token']}"}
        cls.admin_user = admin.json()["user"]
        cls.org_id = cls.admin_user["organization_id"]

    @classmethod
    def tearDownClass(cls):
        from test_env_helper import cleanup_test_db
        cleanup_test_db(cls.test_db)

    def setUp(self):
        self.helper = stage4b.TestStage4BHybridRetrieval(
            methodName="test_01_real_embedding_persistence_async_and_idempotency"
        )
        self.helper.client = self.client
        self.helper.admin_headers = self.admin_headers
        self.helper.admin_user = self.admin_user
        self.helper.org_id = self.org_id
        self.helper.prefix = "gov_" + uuid.uuid4().hex[:8]

    def _ready_item(self, title="版本治理测试"):
        doc_id, doc_ver, blocks = self.helper._create_document(title)
        statement = f"{title}：旧版服务内容应在新版本就绪前继续有效。"
        item_id, v1, _ = self.helper._create_knowledge(
            doc_id, doc_ver, blocks, title, statement,
            actions=["继续使用旧版"], problem_tags=["版本治理"]
        )
        self.helper._confirm_and_wait(item_id, v1)
        detail = self.client.get(
            f"/api/knowledge/items/{item_id}", headers=self.admin_headers
        ).json()
        return doc_id, doc_ver, item_id, v1, detail

    def _payload(self, detail, statement=None, valid_from="__keep__", valid_until="__keep__"):
        av = detail["active_version"]
        return {
            "revision_token": av["revision_token"],
            "title": av["title"],
            "statement": statement if statement is not None else av["statement"],
            "content": statement if statement is not None else av["content"],
            "primary_category": av["primary_category"],
            "atom_type": av["atom_type"],
            "subject": av["subject"],
            "conditions": av["conditions"],
            "actions": av["actions"],
            "exceptions": av["exceptions"],
            "metric_definition": av["metric_definition"],
            "case_details": av["case_details"],
            "customer_types": av["customer_types"],
            "business_scenes": av["business_scenes"],
            "problem_tags": av["problem_tags"],
            "access_scope": detail["access_scope"],
            "valid_from": av["valid_from"] if valid_from == "__keep__" else valid_from,
            "valid_until": av["valid_until"] if valid_until == "__keep__" else valid_until,
        }

    def test_01_new_version_failure_keeps_old_then_atomic_switch(self):
        doc_id, _, item_id, v1, detail = self._ready_item("原子切换规程")
        new_statement = "原子切换规程：新版唯一关键字 ZHIXING_NEW_READY_2026。"
        saved = self.client.put(
            f"/api/knowledge/items/{item_id}/draft",
            json=self._payload(detail, statement=new_statement),
            headers=self.admin_headers,
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        v2 = saved.json()["version_id"]
        token2 = saved.json()["revision_token"]

        with patch.object(main_module, "submit_task", lambda _task_id: None):
            confirmed = self.client.post(
                f"/api/knowledge/items/{item_id}/confirm",
                json={"revision_token": token2},
                headers=self.admin_headers,
            )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        task_id = confirmed.json()["index_task_id"]

        with get_db() as conn:
            item = conn.execute(
                "SELECT active_version_id, pending_version_id FROM knowledge_items WHERE id=?",
                (item_id,),
            ).fetchone()
        self.assertEqual(item["active_version_id"], v1)
        self.assertEqual(item["pending_version_id"], v2)

        with patch.object(indexing, "embed_texts", side_effect=RuntimeError("injected failure")):
            indexing.execute_build_index_task(task_id)
        with get_db() as conn:
            row = conn.execute(
                "SELECT active_version_id, pending_version_id FROM knowledge_items WHERE id=?",
                (item_id,),
            ).fetchone()
            failed = conn.execute(
                "SELECT index_status FROM knowledge_versions WHERE id=?", (v2,)
            ).fetchone()
        self.assertEqual(row["active_version_id"], v1)
        self.assertEqual(row["pending_version_id"], v2)
        self.assertEqual(failed["index_status"], "failed")

        old_search = self.client.get(
            "/api/knowledge/search",
            params={"q": "旧版服务内容", "document_id": doc_id},
            headers=self.admin_headers,
        ).json()
        self.assertIn(v1, {x["version_id"] for x in old_search["items"]})

        with patch.object(main_module, "submit_task", lambda _task_id: None):
            retry = self.client.post(
                f"/api/knowledge/versions/{v2}/index/retry",
                headers=self.admin_headers,
            )
        self.assertEqual(retry.status_code, 200, retry.text)
        indexing.execute_build_index_task(retry.json()["index_task_id"])

        with get_db() as conn:
            switched = conn.execute(
                "SELECT active_version_id, pending_version_id FROM knowledge_items WHERE id=?",
                (item_id,),
            ).fetchone()
        self.assertEqual(switched["active_version_id"], v2)
        self.assertIsNone(switched["pending_version_id"])

        new_search = self.client.get(
            "/api/knowledge/search",
            params={"q": "ZHIXING_NEW_READY_2026", "document_id": doc_id},
            headers=self.admin_headers,
        ).json()
        self.assertEqual([x["version_id"] for x in new_search["items"]], [v2])

    def test_02_stale_admin_save_conflicts(self):
        _, _, item_id, _, detail = self._ready_item("并发保存规程")
        old_token = detail["active_version"]["revision_token"]
        first_payload = self._payload(detail, statement="管理员A保存的新草稿内容。")
        first = self.client.put(
            f"/api/knowledge/items/{item_id}/draft",
            json=first_payload, headers=self.admin_headers,
        )
        self.assertEqual(first.status_code, 200, first.text)

        stale_payload = dict(first_payload)
        stale_payload["revision_token"] = old_token
        stale_payload["statement"] = "管理员B基于旧页面的覆盖内容。"
        stale = self.client.put(
            f"/api/knowledge/items/{item_id}/draft",
            json=stale_payload, headers=self.admin_headers,
        )
        self.assertEqual(stale.status_code, 409)
        self.assertIn("并发", stale.text)

    def test_03_history_readonly_and_restore_creates_new_draft(self):
        _, _, item_id, v1, detail = self._ready_item("历史恢复规程")
        with get_db() as conn:
            conn.execute(
                """
                UPDATE knowledge_versions
                SET business_importance='critical', importance_rationale='历史版本人工重点依据', importance_adjusted_by='测试管理员'
                WHERE id=?
                """,
                (v1,),
            )
        original = detail["active_version"]["statement"]
        saved = self.client.put(
            f"/api/knowledge/items/{item_id}/draft",
            json=self._payload(detail, statement="历史恢复规程：第二版现行内容。"),
            headers=self.admin_headers,
        )
        v2 = saved.json()["version_id"]
        confirm = self.client.post(
            f"/api/knowledge/items/{item_id}/confirm",
            json={"revision_token": saved.json()["revision_token"]},
            headers=self.admin_headers,
        )
        self.assertEqual(confirm.status_code, 200, confirm.text)
        self.helper._wait_status(v2, "ready")

        history = self.client.get(
            f"/api/knowledge/items/{item_id}/versions/{v1}",
            headers=self.admin_headers,
        )
        self.assertEqual(history.status_code, 200, history.text)
        self.assertTrue(history.json()["readonly"])
        self.assertEqual(history.json()["version"]["statement"], original)

        current = self.client.get(
            f"/api/knowledge/items/{item_id}", headers=self.admin_headers
        ).json()
        restored = self.client.post(
            f"/api/knowledge/items/{item_id}/versions/{v1}/restore",
            json={"revision_token": current["active_version"]["revision_token"]},
            headers=self.admin_headers,
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        v3 = restored.json()["draft_version_id"]
        with get_db() as conn:
            item = conn.execute(
                "SELECT active_version_id, pending_version_id FROM knowledge_items WHERE id=?",
                (item_id,),
            ).fetchone()
            old = conn.execute(
                "SELECT statement FROM knowledge_versions WHERE id=?", (v1,)
            ).fetchone()
            draft = conn.execute(
                """
                SELECT statement, review_status, business_importance, importance_rationale, importance_adjusted_by
                FROM knowledge_versions WHERE id=?
                """, (v3,)
            ).fetchone()
        self.assertEqual(item["active_version_id"], v2)
        self.assertEqual(item["pending_version_id"], v3)
        self.assertEqual(old["statement"], original)
        self.assertEqual(draft["statement"], original)
        self.assertEqual(draft["review_status"], "pending_review")
        self.assertEqual(draft["business_importance"], "critical")
        self.assertEqual(draft["importance_rationale"], "历史版本人工重点依据")
        self.assertEqual(draft["importance_adjusted_by"], "测试管理员")

    def test_04_deleted_item_stays_ineligible_with_cleanup_pending_or_failed(self):
        doc_id, _, item_id, v1, _ = self._ready_item("删除清理规程")
        with get_db() as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM retrieval_records WHERE knowledge_version_id=?", (v1,)
            ).fetchone()[0]
        self.assertGreater(before, 0)

        with patch.object(main_module, "submit_task", lambda _task_id: None):
            deleted = self.client.delete(
                f"/api/knowledge/items/{item_id}", headers=self.admin_headers
            )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["cleanup_status"], "pending")

        with get_db() as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM retrieval_records WHERE knowledge_version_id=?", (v1,)
            ).fetchone()[0]
            task = conn.execute(
                "SELECT id FROM processing_tasks WHERE target_id=? AND task_type='clean_index'",
                (v1,),
            ).fetchone()
            conn.execute(
                "UPDATE processing_tasks SET status='failed', error_message='injected cleanup failure' WHERE id=?",
                (task["id"],),
            )
            conn.execute(
                "UPDATE knowledge_versions SET index_status='pending_cleanup' WHERE id=?", (v1,)
            )
        self.assertEqual(remaining, before)

        search = self.client.get(
            "/api/knowledge/search",
            params={"q": "删除清理规程", "document_id": doc_id},
            headers=self.admin_headers,
        ).json()
        self.assertEqual(search["total"], 0)

    def test_05_validity_change_is_immediate_and_audited(self):
        doc_id, _, item_id, v1, detail = self._ready_item("有效期规程")
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        saved = self.client.put(
            f"/api/knowledge/items/{item_id}/draft",
            json=self._payload(detail, valid_from=future),
            headers=self.admin_headers,
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        blocked = self.client.get(
            "/api/knowledge/search",
            params={"q": "有效期规程", "document_id": doc_id},
            headers=self.admin_headers,
        ).json()
        self.assertEqual(blocked["total"], 0)

        refreshed = self.client.get(
            f"/api/knowledge/items/{item_id}", headers=self.admin_headers
        ).json()
        payload = self._payload(refreshed, valid_from=None)
        payload["revision_token"] = saved.json()["revision_token"]
        cleared = self.client.put(
            f"/api/knowledge/items/{item_id}/draft",
            json=payload, headers=self.admin_headers,
        )
        self.assertEqual(cleared.status_code, 200, cleared.text)

        restored = self.client.get(
            "/api/knowledge/search",
            params={"q": "有效期规程", "document_id": doc_id},
            headers=self.admin_headers,
        ).json()
        self.assertIn(v1, {x["version_id"] for x in restored["items"]})
        with get_db() as conn:
            audit_count = conn.execute(
                """
                SELECT COUNT(*) FROM audit_logs
                WHERE target_id=? AND action='update_knowledge_validity'
                """,
                (item_id,),
            ).fetchone()[0]
        self.assertGreaterEqual(audit_count, 2)

    def test_06_ac44_cross_source_binding_rejected(self):
        _, doc_ver_1, item_id, v1, _ = self._ready_item("来源一致性规程")
        _, doc_ver_2, blocks_2 = self.helper._create_document("另一份不相关资料")

        with self.assertRaises(sqlite3.IntegrityError):
            with get_db() as conn:
                conn.execute(
                    "UPDATE knowledge_versions SET source_document_version_id=? WHERE id=?",
                    (doc_ver_2, v1),
                )

        with get_db() as conn:
            current = conn.execute(
                "SELECT source_document_version_id FROM knowledge_versions WHERE id=?",
                (v1,),
            ).fetchone()
            ev = conn.execute(
                "SELECT id FROM knowledge_evidence WHERE knowledge_version_id=? LIMIT 1",
                (v1,),
            ).fetchone()
        self.assertEqual(current["source_document_version_id"], doc_ver_1)

        with self.assertRaises(sqlite3.IntegrityError):
            with get_db() as conn:
                conn.execute(
                    "UPDATE knowledge_evidence SET source_block_id=? WHERE id=?",
                    (blocks_2[0], ev["id"]),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
