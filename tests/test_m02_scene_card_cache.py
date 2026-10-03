"""Scene summaries persist without recalling unchanged knowledge; all data is isolated."""

import importlib
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from test_env_helper import setup_test_db, cleanup_test_db
from database import get_db, init_db
import hybrid_retrieval
import scene_catalog as sc
import scene_card_cache as cache


class TestSceneCardCache(unittest.TestCase):
    def setUp(self):
        self.db = setup_test_db("scene_card_cache")
        self.now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        now = self.now.isoformat()
        with get_db() as conn:
            self.user = dict(conn.execute("SELECT id, organization_id, role, account_status FROM users WHERE id = 'usr_admin_001'").fetchone())
            self.other = dict(conn.execute("SELECT id, organization_id, role, account_status FROM users WHERE id = 'usr_other_001'").fetchone())
            org = self.user["organization_id"]
            conn.execute("""INSERT INTO documents(id,organization_id,title,active_version_id,access_scope,is_deleted,created_at,updated_at)
                VALUES('doc_cache',?,'缓存资料','dv_cache','org_internal',0,?,?)""", (org, now, now))
            conn.execute("""INSERT INTO document_versions(id,document_id,organization_id,version_label,file_name,file_type,file_size,
                content_hash,storage_reference,uploaded_by,uploaded_at,processing_status)
                VALUES('dv_cache','doc_cache',?,'v1','cache.txt','txt',1,'h','cache.txt',?,?, 'completed')""",
                (org, self.user["id"], now))
            for i in range(3):
                conn.execute("""INSERT INTO knowledge_items(id,document_id,organization_id,active_version_id,access_scope,
                    lifecycle_status,is_excluded,created_at,updated_at) VALUES(?, 'doc_cache', ?, ?, 'org_internal','active',0,?,?)""",
                    (f"ki_cache{i}", org, f"kv_cache{i}", now, now))
                conn.execute("""INSERT INTO knowledge_versions(id,item_id,organization_id,source_document_version_id,version_number,
                    title,content,statement,primary_category,atom_type,business_scenes_json,review_status,index_status,
                    revision_token,created_at,created_by) VALUES(?,?,?,'dv_cache',1,?,'执行要求','执行要求','制度与标准',
                    '规则','["场景甲"]','confirmed','ready','same-token',?,?)""",
                    (f"kv_cache{i}", f"ki_cache{i}", org, f"知识{i}", now, self.user["id"]))
            self.scene = sc.create_scene(conn, org, self.user["id"], {"name": "场景甲"})
        self.search = patch.object(hybrid_retrieval, "hybrid_search", return_value=[]).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        cleanup_test_db(self.db)

    def cards(self, user=None, now=None, force=False, module=None):
        user = user or self.user
        with get_db() as conn:
            scenes = sc.list_scenes(conn, user["organization_id"], status="active")
            return (module or cache).get_scene_cards(conn, user, scenes, (now or self.now).isoformat(), force)

    def change(self, sql, args=()):
        with get_db() as conn:
            conn.execute(sql, args)

    def test_hit_skips_all_recall_and_survives_module_reload(self):
        first = self.cards()
        restarted = importlib.reload(cache)
        with patch.object(sc, "build_scene_card_context", side_effect=AssertionError("cache hit loaded context")), \
             patch.object(sc, "_eligible_atom_rows", side_effect=AssertionError("cache hit read bodies")), \
             patch.object(sc, "recall_atoms", side_effect=AssertionError("cache hit recalled")):
            self.assertEqual(self.cards(module=restarted), first)
        self.assertEqual(self.search.call_count, 1)
        init_db()
        self.assertEqual(self.cards(), first)
        self.assertEqual(self.search.call_count, 1)

    def test_real_skill_counts_do_not_invalidate_knowledge_summary(self):
        self.cards()
        self.change("""INSERT INTO skills(id,organization_id,scene_id,status,visibility,created_by,created_at,updated_at,revision_token)
            VALUES('sk_cache',?,?,'approved','org_internal',?,?,?,'r')""",
            (self.user["organization_id"], self.scene["scene_id"], self.user["id"], self.now.isoformat(), self.now.isoformat()))
        self.assertEqual(self.cards()[0]["skill_counts"]["approved"], 1)
        self.assertEqual(self.search.call_count, 1)
        self.change("UPDATE skills SET status = 'needs_recheck' WHERE id = 'sk_cache'")
        self.assertEqual(self.cards()[0]["skill_counts"]["needs_recheck"], 1)
        self.assertEqual(self.search.call_count, 1)

    def test_force_refresh_recomputes(self):
        self.cards()
        self.cards(force=True)
        self.assertEqual(self.search.call_count, 2)

    def test_organization_and_role_cache_are_isolated(self):
        self.change("UPDATE knowledge_items SET access_scope = 'admin_only' WHERE id = 'ki_cache0'")
        self.assertEqual(self.cards()[0]["available_atom_count"], 3)
        member = {**self.user, "role": "member"}
        self.assertEqual(self.cards(user=member)[0]["available_atom_count"], 2)
        with get_db() as conn:
            sc.create_scene(conn, self.other["organization_id"], self.other["id"], {"name": "场景甲"})
        self.assertEqual(self.cards(user=self.other)[0]["available_atom_count"], 0)
        with get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scene_card_cache").fetchone()[0], 3)
        self.cards()
        self.assertEqual(self.search.call_count, 3)

    def test_scene_change_only_recomputes_that_scene(self):
        with get_db() as conn:
            other_scene = sc.create_scene(conn, self.user["organization_id"], self.user["id"], {"name": "场景乙"})
        self.cards()
        self.change("UPDATE scenes SET description = '新说明', revision_token = 'new' WHERE id = ?", (other_scene["scene_id"],))
        self.cards()
        self.assertEqual(self.search.call_count, 3)

    def test_status_source_permission_and_same_timestamp_changes_invalidate(self):
        changes = [
            ("UPDATE knowledge_items SET lifecycle_status = 'disabled' WHERE id = 'ki_cache0'", 2),
            ("UPDATE knowledge_items SET lifecycle_status = 'active' WHERE id = 'ki_cache0'", 3),
            ("UPDATE knowledge_items SET is_excluded = 1 WHERE id = 'ki_cache0'", 2),
            ("UPDATE knowledge_items SET is_excluded = 0, access_scope = 'admin_only' WHERE id = 'ki_cache0'", 3),
            ("UPDATE documents SET access_scope = 'admin_only' WHERE id = 'doc_cache'", 3),
            ("UPDATE documents SET active_version_id = NULL WHERE id = 'doc_cache'", 0),
            ("UPDATE documents SET active_version_id = 'dv_cache' WHERE id = 'doc_cache'", 3),
            ("UPDATE documents SET is_deleted = 1 WHERE id = 'doc_cache'", 0),
            ("UPDATE documents SET is_deleted = 0 WHERE id = 'doc_cache'", 3),
            ("UPDATE knowledge_versions SET index_status = 'indexing' WHERE id = 'kv_cache0'", 2),
            ("UPDATE knowledge_versions SET index_status = 'ready' WHERE id = 'kv_cache0'", 3),
        ]
        self.cards()
        for index, (sql, count) in enumerate(changes, start=2):
            with self.subTest(sql=sql):
                self.change(sql)
                self.assertEqual(self.cards()[0]["available_atom_count"], count)
                self.assertEqual(self.search.call_count, index)

    def test_version_metadata_changes_without_revision_rotation_invalidate(self):
        self.cards()
        # A confirmed version's content is immutable. Legacy fixtures may update
        # metadata while pending and restore confirmation without rotating tokens.
        self.change("UPDATE knowledge_versions SET review_status = 'pending_review' WHERE id = 'kv_cache0'")
        self.change("UPDATE knowledge_versions SET primary_category = '方法与工具', business_scenes_json = '[\"场景乙\"]' WHERE id = 'kv_cache0'")
        self.change("UPDATE knowledge_versions SET review_status = 'confirmed' WHERE id = 'kv_cache0'")
        self.assertEqual(self.cards()[0]["available_atom_count"], 2)
        self.assertEqual(self.search.call_count, 2)

    def test_time_boundaries_invalidate_without_database_writes(self):
        start, end = self.now + timedelta(seconds=10), self.now + timedelta(seconds=20)
        self.change("UPDATE knowledge_versions SET valid_from = ?, valid_until = ? WHERE id = 'kv_cache0'",
                    (start.isoformat(), end.isoformat()))
        self.assertEqual(self.cards()[0]["available_atom_count"], 2)
        self.assertEqual(self.cards(now=start)[0]["available_atom_count"], 3)
        self.assertEqual(self.cards(now=end)[0]["available_atom_count"], 3)
        self.assertEqual(self.cards(now=end + timedelta(microseconds=1))[0]["available_atom_count"], 2)
        self.assertEqual(self.search.call_count, 3)

    def test_index_metadata_and_configuration_invalidate(self):
        self.cards()
        self.change("""INSERT INTO retrieval_records(id,knowledge_version_id,organization_id,search_text,model_name,index_version,
            content_hash,config_hash,created_at) VALUES('rr_cache','kv_cache0',?,'要求','fake',2,'h','c',?)""",
            (self.user["organization_id"], self.now.isoformat()))
        self.cards()
        self.change("UPDATE retrieval_records SET content_hash = 'changed' WHERE id = 'rr_cache'")
        self.cards()
        with patch.object(cache, "get_retrieval_config_hash", return_value="new-config"):
            self.cards()
        self.assertEqual(self.search.call_count, 4)

    def test_add_replace_and_delete_knowledge_with_unchanged_timestamps(self):
        self.cards()
        self.change("""INSERT INTO knowledge_items(id,document_id,organization_id,active_version_id,access_scope,
            lifecycle_status,is_excluded,created_at,updated_at)
            SELECT 'ki_cache_new',document_id,organization_id,'kv_cache_new',access_scope,lifecycle_status,is_excluded,
            created_at,updated_at FROM knowledge_items WHERE id = 'ki_cache0'""")
        self.change("""INSERT INTO knowledge_versions(id,item_id,organization_id,source_document_version_id,version_number,
            title,content,statement,primary_category,atom_type,business_scenes_json,review_status,index_status,
            revision_token,created_at,created_by)
            SELECT 'kv_cache_new','ki_cache_new',organization_id,source_document_version_id,1,title,content,statement,
            primary_category,atom_type,business_scenes_json,review_status,index_status,revision_token,created_at,created_by
            FROM knowledge_versions WHERE id = 'kv_cache0'""")
        self.assertEqual(self.cards()[0]["available_atom_count"], 4)
        self.change("""INSERT INTO knowledge_versions(id,item_id,organization_id,source_document_version_id,version_number,
            title,content,statement,primary_category,atom_type,business_scenes_json,review_status,index_status,
            revision_token,created_at,created_by)
            SELECT 'kv_cache_v2',item_id,organization_id,source_document_version_id,2,title,content,statement,
            primary_category,atom_type,business_scenes_json,review_status,index_status,revision_token,created_at,created_by
            FROM knowledge_versions WHERE id = 'kv_cache0'""")
        self.change("UPDATE knowledge_items SET active_version_id = 'kv_cache_v2' WHERE id = 'ki_cache0'")
        self.assertEqual(self.cards()[0]["available_atom_count"], 4)
        self.change("DELETE FROM knowledge_items WHERE id = 'ki_cache_new'")
        self.assertEqual(self.cards()[0]["available_atom_count"], 3)
        self.assertEqual(self.search.call_count, 4)

    def test_route_exposes_force_refresh_and_keeps_live_batch_state(self):
        from fastapi.testclient import TestClient
        from auth import require_admin
        from main import app
        from skill_generation import start_generation_batch
        app.dependency_overrides[require_admin] = lambda: self.user
        self.addCleanup(app.dependency_overrides.pop, require_admin, None)
        client = TestClient(app)
        self.assertEqual(client.get("/api/skill-factory/scenes/cards").status_code, 200)
        with get_db() as conn:
            batch = start_generation_batch(conn, self.user, self.scene["scene_id"])
        # start_generation_batch performs its own fresh recall before creating a batch.
        calls = self.search.call_count
        cards = client.get("/api/skill-factory/scenes/cards").json()["cards"]
        self.assertEqual(cards[0]["running_batch_id"], batch["batch_id"])
        self.assertEqual(cards[0]["latest_batch"]["batch_id"], batch["batch_id"])
        self.assertEqual(self.search.call_count, calls)
        self.assertEqual(client.get("/api/skill-factory/scenes/cards?force_refresh=true").status_code, 200)
        self.assertEqual(self.search.call_count, calls + 1)
        # The task was created but never enqueued; leave no active recovery work.
        self.change("UPDATE skill_generation_tasks SET status = 'cancelled' WHERE batch_id = ?", (batch["batch_id"],))
        self.change("UPDATE skill_generation_batches SET status = 'failed' WHERE id = ?", (batch["batch_id"],))

    def test_semantic_failure_retries_after_short_interval_and_on_force(self):
        self.search.side_effect = RuntimeError("temporarily unavailable")
        self.assertTrue(self.cards()[0]["semantic_error"])
        self.search.side_effect = None
        self.assertTrue(self.cards(now=self.now + timedelta(seconds=29))[0]["semantic_error"])
        self.assertIsNone(self.cards(now=self.now + timedelta(seconds=30))[0]["semantic_error"])
        self.assertEqual(self.search.call_count, 2)
        self.search.side_effect = RuntimeError("temporary")
        self.cards(force=True)
        self.search.side_effect = None
        self.assertIsNone(self.cards(force=True)[0]["semantic_error"])

    def test_concurrent_input_change_does_not_cache_stale_summary(self):
        def search(conn, user, query, now_iso=None):
            self.change("UPDATE knowledge_items SET lifecycle_status = 'disabled' WHERE id = 'ki_cache0'")
            return []
        self.search.side_effect = search
        self.cards()
        with get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scene_card_cache").fetchone()[0], 0)
        self.search.side_effect = None
        self.assertEqual(self.cards()[0]["available_atom_count"], 2)


if __name__ == "__main__":
    unittest.main()
