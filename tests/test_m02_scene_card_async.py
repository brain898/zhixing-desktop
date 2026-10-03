"""Nonblocking scene summaries use isolated DB-bound background workers."""

import sys
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from test_env_helper import setup_test_db, cleanup_test_db
from database import get_db
import hybrid_retrieval
import scene_catalog as sc
import scene_card_cache as cache
from tasks import wait_for_background_tasks


class TestAsyncSceneCards(unittest.TestCase):
    def setUp(self):
        self.db = setup_test_db("scene_card_async")
        self.now = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            self.user = dict(conn.execute("SELECT id, organization_id, role, account_status FROM users WHERE id = 'usr_admin_001'").fetchone())
            self.scene = sc.create_scene(conn, self.user["organization_id"], self.user["id"], {"name": "场景甲"})
        self.search = patch.object(hybrid_retrieval, "hybrid_search", return_value=[]).start()
        self.addCleanup(patch.stopall)
        self.block = threading.Event()
        self.addCleanup(self.block.set)

    def tearDown(self):
        self.block.set()
        cleanup_test_db(self.db)

    def cards(self, force=False, user=None):
        user = user or self.user
        with get_db() as conn:
            return cache.get_scene_cards_async(conn, user, sc.list_scenes(conn, user["organization_id"], "active"), self.now, force)

    def wait(self):
        self.assertTrue(wait_for_background_tasks(5))

    def test_http_returns_while_two_second_refresh_is_running_and_single_flight(self):
        from fastapi.testclient import TestClient
        from auth import require_admin
        from main import app
        started = threading.Event()
        original = cache.get_scene_cards
        calls = []
        def slow(*args, **kwargs):
            calls.append(1)
            started.set()
            time.sleep(2)
            return original(*args, **kwargs)
        app.dependency_overrides[require_admin] = lambda: self.user
        self.addCleanup(app.dependency_overrides.pop, require_admin, None)
        with patch.object(cache, "get_scene_cards", side_effect=slow):
            client = TestClient(app)
            before = time.monotonic()
            response = client.get("/api/skill-factory/scenes/cards?background_refresh=true")
            elapsed = time.monotonic() - before
            self.assertEqual(response.status_code, 200)
            self.assertLess(elapsed, 1.0)
            self.assertTrue(started.wait(1))
            self.assertTrue(response.json()["cards"][0]["statistics_pending"])
            for _ in range(3):
                cards = client.get("/api/skill-factory/scenes/cards?background_refresh=true&force_refresh=true").json()["cards"]
                self.assertTrue(cards[0]["statistics_pending"])
                self.assertFalse(cards[0]["can_generate"])
            self.wait()
            self.assertEqual(len(calls), 1)
            final = client.get("/api/skill-factory/scenes/cards?background_refresh=true").json()["cards"][0]
            self.assertFalse(final["statistics_pending"])
            self.assertEqual(len(calls), 1)

    def test_force_returns_old_summary_then_completion_returns_new_values(self):
        with get_db() as conn:
            cache.get_scene_cards(conn, self.user, [self.scene], self.now)
        entered = threading.Event()
        original = cache.get_scene_cards
        def blocked(*args, **kwargs):
            entered.set()
            self.block.wait(4)
            return original(*args, **kwargs)
        with patch.object(cache, "get_scene_cards", side_effect=blocked):
            card = self.cards(force=True)[0]
            self.assertTrue(entered.wait(1))
            self.assertTrue(card["statistics_pending"])
            self.assertFalse(card["can_generate"])
            self.assertIn("更新中", card["generate_blocked_reason"])
            self.block.set()
            self.wait()
        self.assertFalse(self.cards()[0]["statistics_pending"])
        self.assertEqual(self.search.call_count, 2)

    def test_worker_keeps_captured_database_when_environment_switches(self):
        entered = threading.Event()
        original = cache.get_scene_cards
        def blocked(conn, *args, **kwargs):
            entered.set()
            self.block.wait(4)
            return original(conn, *args, **kwargs)
        other_db = None
        with patch.object(cache, "get_scene_cards", side_effect=blocked):
            self.cards()
            self.assertTrue(entered.wait(1))
            other_db = setup_test_db("scene_card_async_other")
            self.block.set()
            self.wait()
        import sqlite3
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scene_card_cache").fetchone()[0], 1)
        with closing(sqlite3.connect(other_db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scene_card_cache").fetchone()[0], 0)
        cleanup_test_db(other_db)

    def test_failure_stops_pending_and_retries_only_on_force_or_backoff_expiry(self):
        with patch.object(cache, "get_scene_cards", side_effect=RuntimeError("injected failure")) as refresh:
            self.cards()
            self.wait()
            for _ in range(3):
                card = self.cards()[0]
                self.assertFalse(card["statistics_pending"])
                self.assertFalse(card["can_generate"])
                self.assertIn("injected failure", card["semantic_error"])
            self.assertEqual(refresh.call_count, 1)
        self.assertTrue(self.cards(force=True)[0]["statistics_pending"])
        self.wait()
        self.assertFalse(self.cards()[0]["statistics_pending"])

    def test_semantic_failure_is_cached_instead_of_repeatedly_resubmitting(self):
        self.search.side_effect = RuntimeError("temporary embedding failure")
        self.cards()
        self.wait()
        for _ in range(3):
            card = self.cards()[0]
            self.assertFalse(card["statistics_pending"])
            self.assertIn("temporary embedding failure", card["semantic_error"])
        self.assertEqual(self.search.call_count, 1)
        self.search.side_effect = None
        self.cards(force=True)
        self.wait()
        self.assertIsNone(self.cards()[0]["semantic_error"])

    def test_failure_backoff_expiry_schedules_recovery(self):
        with patch.object(cache, "get_scene_cards", side_effect=RuntimeError("injected failure")):
            self.cards()
            self.wait()
        future = time.monotonic() + cache.FAILURE_RETRY_SECONDS + 1
        with patch.object(cache.time, "monotonic", return_value=future):
            self.assertTrue(self.cards()[0]["statistics_pending"])
        self.wait()
        self.assertFalse(self.cards()[0]["statistics_pending"])

    def test_organization_and_role_have_independent_single_flights(self):
        with get_db() as conn:
            other = dict(conn.execute("SELECT id, organization_id, role, account_status FROM users WHERE id = 'usr_other_001'").fetchone())
            sc.create_scene(conn, other["organization_id"], other["id"], {"name": "另一组织"})
        member = {**self.user, "role": "member"}
        original = cache.get_scene_cards
        observed = []
        def blocked(conn, user, *args, **kwargs):
            observed.append((user["organization_id"], user["role"]))
            self.block.wait(4)
            return original(conn, user, *args, **kwargs)
        with patch.object(cache, "get_scene_cards", side_effect=blocked):
            for user in (self.user, member, other):
                self.assertTrue(self.cards(user=user)[0]["statistics_pending"])
            self.block.set()
            self.wait()
        self.assertEqual(set(observed), {(u["organization_id"], u["role"]) for u in (self.user, member, other)})
        self.assertEqual(len(observed), 3)
        for user in (self.user, member, other):
            self.assertFalse(self.cards(user=user)[0]["statistics_pending"])

    def test_scene_change_during_worker_does_not_store_old_signature(self):
        entered = threading.Event()
        original_build = sc.build_scene_card
        def blocked(*args, **kwargs):
            entered.set()
            self.block.wait(4)
            return original_build(*args, **kwargs)
        with patch.object(sc, "build_scene_card", side_effect=blocked):
            self.cards()
            self.assertTrue(entered.wait(1))
            with get_db() as conn:
                conn.execute("UPDATE scenes SET description = '已修改', revision_token = 'new' WHERE id = ?", (self.scene["scene_id"],))
            self.block.set()
            self.wait()
        with get_db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scene_card_cache").fetchone()[0], 0)
        self.assertTrue(self.cards()[0]["statistics_pending"])
        self.wait()
        self.assertFalse(self.cards()[0]["statistics_pending"])


if __name__ == "__main__":
    unittest.main()
