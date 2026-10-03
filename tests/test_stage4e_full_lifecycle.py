"""Stage 4E：知识资产模块最终生命周期演示验收。

范围只覆盖知识确认、索引、管理员正式检索、来源定位、成员授权检索契约、
权限收紧、停用和删除；不调用 Agent / 回答模型。
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
TESTS_DIR = Path(__file__).resolve().parent
for _path in (str(SERVER_DIR), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from database import get_db
from hybrid_retrieval import hybrid_search
from main import app
import test_stage4b_hybrid_retrieval as stage4b


class TestStage4EFullLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_env_helper import setup_test_db
        cls.test_db = setup_test_db("stage4e_lifecycle")
        cls.client = TestClient(app)
        admin = cls.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Admin@Zhixing2026"},
        )
        member = cls.client.post(
            "/api/auth/login",
            json={"username": "member", "password": "Member@Zhixing2026"},
        )
        assert admin.status_code == 200, admin.text
        assert member.status_code == 200, member.text
        cls.admin_headers = {"Authorization": f"Bearer {admin.json()['token']}"}
        cls.admin_user = admin.json()["user"]
        cls.member_user = member.json()["user"]
        cls.org_id = cls.admin_user["organization_id"]

        cls.helper = stage4b.TestStage4BHybridRetrieval(
            methodName="test_01_real_embedding_persistence_async_and_idempotency"
        )
        cls.helper.client = cls.client
        cls.helper.admin_headers = cls.admin_headers
        cls.helper.admin_user = cls.admin_user
        cls.helper.org_id = cls.org_id
        cls.helper.prefix = "s4e_lifecycle"

    @classmethod
    def tearDownClass(cls):
        from test_env_helper import cleanup_test_db
        cleanup_test_db(cls.test_db)

    def test_01_confirm_index_search_source_and_revoke(self):
        doc_id, doc_ver, blocks = self.helper._create_document("Stage4E最终演示制度")
        with get_db() as conn:
            texts = [
                "设备巡检发现异常时，巡检人员应登记工单并记录异常现象。",
                "涉及人身安全风险时，应先按应急流程处置，再进行后续工单流转。",
                "存在安全风险时不得等待普通工单完成后再采取应急措施。",
                "本条用于 Stage 4E 固定演示，不代表真实企业制度。",
            ]
            for i, (block_id, text) in enumerate(zip(blocks, texts), start=1):
                conn.execute(
                    "UPDATE source_blocks SET text_content=?, heading_path=?, paragraph_anchor=? WHERE id=?",
                    (text, "设备巡检异常处置", f"[line_{i}]", block_id),
                )

        item_id, version_id, _ = self.helper._create_knowledge(
            doc_id,
            doc_ver,
            blocks,
            "设备巡检异常处置",
            "设备巡检发现异常时应登记工单；涉及人身安全风险时先按应急流程处置。",
            conditions=["设备巡检发现异常"],
            actions=["登记工单", "记录异常现象"],
            exceptions=["涉及人身安全风险时先按应急流程处置，不等待普通工单流转"],
            problem_tags=["设备异常", "安全风险"],
            business_scenes=["设施巡检"],
        )

        pending = self.client.get(
            "/api/knowledge/search",
            params={"q": "设备巡检异常"},
            headers=self.admin_headers,
        )
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(pending.json()["total"], 0, "待确认知识不得进入正式检索")

        self.helper._confirm_and_wait(item_id, version_id)

        ready = self.client.get(
            "/api/knowledge/search",
            params={"q": "设备巡检异常"},
            headers=self.admin_headers,
        )
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertEqual(ready.json()["total"], 1)
        result = ready.json()["items"][0]
        self.assertEqual(result["version_id"], version_id)
        self.assertTrue(result["evidence"])
        self.assertEqual(result["source"]["document_version_id"], doc_ver)
        self.assertTrue(
            any(e["source_locator"]["paragraph_anchor"] for e in result["evidence"]),
            "正式检索必须能够定位来源",
        )

        with get_db() as conn:
            member_before = hybrid_search(
                conn,
                self.member_user,
                "设备巡检异常",
                datetime.now(timezone.utc).isoformat(),
            )
        self.assertIn(version_id, {row["version_id"] for row in member_before})

        tighten_doc = self.client.put(
            f"/api/documents/{doc_id}/access-scope",
            json={"access_scope": "admin_only"},
            headers=self.admin_headers,
        )
        self.assertEqual(tighten_doc.status_code, 200, tighten_doc.text)
        with get_db() as conn:
            member_after_doc_tighten = hybrid_search(
                conn,
                self.member_user,
                "设备巡检异常",
                datetime.now(timezone.utc).isoformat(),
            )
        self.assertNotIn(version_id, {row["version_id"] for row in member_after_doc_tighten})

        admin_still_allowed = self.client.get(
            "/api/knowledge/search",
            params={"q": "设备巡检异常"},
            headers=self.admin_headers,
        )
        self.assertEqual(admin_still_allowed.json()["total"], 1)

        restore_doc = self.client.put(
            f"/api/documents/{doc_id}/access-scope",
            json={"access_scope": "org_internal"},
            headers=self.admin_headers,
        )
        self.assertEqual(restore_doc.status_code, 200, restore_doc.text)

        tighten_item = self.client.put(
            f"/api/knowledge/items/{item_id}/access-scope",
            json={"access_scope": "admin_only"},
            headers=self.admin_headers,
        )
        self.assertEqual(tighten_item.status_code, 200, tighten_item.text)
        with get_db() as conn:
            member_after_item_tighten = hybrid_search(
                conn,
                self.member_user,
                "设备巡检异常",
                datetime.now(timezone.utc).isoformat(),
            )
        self.assertNotIn(version_id, {row["version_id"] for row in member_after_item_tighten})

        restore_item = self.client.put(
            f"/api/knowledge/items/{item_id}/access-scope",
            json={"access_scope": "org_internal"},
            headers=self.admin_headers,
        )
        self.assertEqual(restore_item.status_code, 200, restore_item.text)

        disable = self.client.put(
            f"/api/knowledge/items/{item_id}/lifecycle",
            json={"lifecycle_status": "disabled"},
            headers=self.admin_headers,
        )
        self.assertEqual(disable.status_code, 200, disable.text)

        # 验证停用后条目在知识列表中自动排在最底部
        list_resp = self.client.get("/api/knowledge/items", headers=self.admin_headers)
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json()["items"]
        if len(items) > 1:
            self.assertEqual(items[-1]["id"], item_id, "被停用的知识条目必须自动排在整个条目列表的最底部")
            self.assertEqual(items[-1]["lifecycle_status"], "disabled")

        disabled_search = self.client.get(
            "/api/knowledge/search",
            params={"q": "设备巡检异常"},
            headers=self.admin_headers,
        )
        self.assertEqual(disabled_search.json()["total"], 0)

        enable = self.client.put(
            f"/api/knowledge/items/{item_id}/lifecycle",
            json={"lifecycle_status": "active"},
            headers=self.admin_headers,
        )
        self.assertEqual(enable.status_code, 200, enable.text)
        restored_search = self.client.get(
            "/api/knowledge/search",
            params={"q": "设备巡检异常"},
            headers=self.admin_headers,
        )
        self.assertEqual(restored_search.json()["total"], 1)

        delete = self.client.delete(
            f"/api/knowledge/items/{item_id}",
            headers=self.admin_headers,
        )
        self.assertEqual(delete.status_code, 200, delete.text)
        deleted_search = self.client.get(
            "/api/knowledge/search",
            params={"q": "设备巡检异常"},
            headers=self.admin_headers,
        )
        self.assertEqual(deleted_search.json()["total"], 0)

        print(
            "[Stage4E][lifecycle] pending=0 -> confirmed/index_ready=1 -> "
            "source_located -> doc_permission_revoked_for_member -> "
            "item_permission_revoked_for_member -> disabled=0 -> restored=1 -> deleted=0"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
