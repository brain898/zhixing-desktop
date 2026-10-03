"""FR13 统计口径与 AC21 隔离。合成数据库记录，不调用或模拟大模型。"""
import json
import unittest
from unittest.mock import patch

from test_env_helper import setup_test_db, cleanup_test_db
from database import get_db
from main import app
from fastapi.testclient import TestClient
from skill_statistics import _aggregate, get_statistics
import skill_statistics


def skill(ident, status="pending_review", scene="s", batch="b"):
    return {"id": ident, "status": status, "scene_id": scene, "batch_id": batch}


def batch(**results):
    return {"task_results_json": json.dumps({k: {"skill_id": k, "status": v} for k, v in results.items()})}


def record(ident, action, diffs=None, items=None, reason=None):
    return {"skill_id": ident, "action": action, "field_diffs_json": json.dumps(diffs) if diffs is not None else None,
            "detail_json": json.dumps({"unsupported_items": items or []}), "reject_reason": reason}


class TestStatisticsMetrics(unittest.TestCase):
    def test_empty_is_null_not_zero_percent(self):
        m = _aggregate([], [], [])
        self.assertEqual(m["candidate_count"], 0)
        self.assertIsNone(m["validation_pass_rate"]["value"])
        self.assertIsNone(m["review_pass_rate"]["value"])
        self.assertIsNone(m["average_modified_fields"])

    def test_initial_validation_not_current_status_or_regeneration(self):
        ss = [skill("a", "rejected"), skill("b", "approved"), skill("c", "generating")]
        m = _aggregate(ss, [batch(a="stored", b="validation_failed", c="generating", outsider="stored")],
                       [record("a", "reject"), record("b", "regenerate"), record("b", "approve", [])])
        self.assertEqual(m["candidate_count"], 3)
        self.assertEqual(m["validation_pass_rate"], {"numerator": 1, "denominator": 2, "value": .5})
        self.assertEqual(m["validation_unrecorded_count"], 1)

    def test_latest_decision_and_restored_or_returned_pending(self):
        ss = [skill(k) for k in "abcd"]
        rr = [record("a", "reject", reason="其他"), record("a", "restore"), record("a", "approve", []),
              record("b", "reject", reason="原子依据不足"), record("c", "approve", []),
              record("c", "regenerate"), record("d", "reject"), record("d", "restore")]
        m = _aggregate(ss, [], rr)
        self.assertEqual(m["review_pass_rate"]["value"], .5)
        self.assertEqual(m["review_pass_rate"]["denominator"], 2)
        self.assertEqual(m["review_pending_count"], 2)
        self.assertEqual(sum(m["rejection_reasons"].values()), 3)

    def test_manual_diff_mean_excludes_model_and_missing_records(self):
        rr = [record("a", "approve", []), record("a", "recheck", [{"path": "x"}, {"path": "x"}]),
              record("b", "approve_with_changes", [{"path": "y"}, {"path": "z"}]),
              record("b", "regenerate", [{"path": "model"}]), record("c", "approve")]
        m = _aggregate([skill(k) for k in "abc"], [], rr)
        self.assertEqual(m["average_modified_fields"], 1)
        self.assertEqual(m["manual_review_record_count"], 3)
        self.assertEqual(m["modified_field_total"], 3)

    def test_unsupported_submitted_events_only_and_dedup(self):
        items = [{"item_key": "step:s1", "resolution": "delete"},
                 {"item_key": "step:s1", "resolution": "delete"},
                 {"item_key": "number:s1", "resolution": "expert"},
                 {"item_key": "exception:kv1", "resolution": "landed"},
                 {"item_key": "number:s2", "resolution": None}]
        rr = [record("a", "approve_with_changes", [], items), record("a", "regenerate", [], items),
              record("foreign", "approve", [], items)]
        self.assertEqual(_aggregate([skill("a")], [], rr)["unsupported_resolutions"],
                         {"delete": 1, "expert": 1, "landed": 1})

    def test_no_validation_evidence_no_inferred_rate(self):
        m = _aggregate([skill("a", "approved")], [], [record("a", "approve", [])])
        self.assertIsNone(m["validation_pass_rate"]["value"])
        self.assertEqual(m["review_pass_rate"]["value"], 1)


class TestStatisticsAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = setup_test_db("m02f_statistics")
        cls.client = TestClient(app)
        cls.headers = {}
        for username, password in [("admin", "Admin@Zhixing2026"), ("member", "Member@Zhixing2026"),
                                   ("other_admin", "Other@Zhixing2026")]:
            result = cls.client.post("/api/auth/login", json={"username": username, "password": password})
            cls.headers[username] = {"Authorization": "Bearer " + result.json()["token"]}
        for username, scene in [("admin", "本企业场景"), ("other_admin", "其他企业场景")]:
            cls.client.post("/api/skill-factory/scenes", headers=cls.headers[username],
                            json={"name": scene, "description": "统计隔离", "aliases": [], "typical_problems": []}).raise_for_status()
        with get_db() as conn:
            scene = conn.execute("SELECT id FROM scenes WHERE organization_id='org_greentown'").fetchone()[0]
            for i in range(2):
                bid, sid = f"stats_batch_{i}", f"stats_skill_{i}"
                conn.execute("""INSERT INTO skill_generation_batches
                    (id, organization_id, scene_id, initiated_by, initiated_at, status, updated_at, task_results_json)
                    VALUES (?, 'org_greentown', ?, 'usr_admin_001', '2026-09-30', 'completed', '2026-09-30', ?)""",
                             (bid, scene, json.dumps({"t1": {"status": "stored", "skill_id": sid}})))
                conn.execute("""INSERT INTO skills
                    (id, organization_id, scene_id, status, created_by, created_at, updated_at, batch_id)
                    VALUES (?, 'org_greentown', ?, 'pending_review', 'usr_admin_001', '2026-09-30', '2026-09-30', ?)""",
                             (sid, scene, bid))
        cls.scene = scene

    @classmethod
    def tearDownClass(cls):
        cleanup_test_db(cls.db)

    def test_admin_summary_groups_filters_reconcile(self):
        def fetch(**params):
            response = self.client.get("/api/skill-factory/statistics", headers=self.headers["admin"], params=params)
            self.assertEqual(response.status_code, 200)
            return response.json()
        all_ = fetch()
        self.assertEqual(all_["summary"]["candidate_count"], 2)
        self.assertEqual(sum(b["candidate_count"] for b in all_["by_batch"]), 2)
        self.assertEqual(sum(s["candidate_count"] for s in all_["by_scene"]), 2)
        self.assertEqual(fetch(scene_id=self.scene)["summary"]["candidate_count"], 2)
        self.assertEqual(fetch(batch_id="stats_batch_0")["summary"]["candidate_count"], 1)
        self.assertEqual(fetch(scene_id="foreign")["summary"]["candidate_count"], 0)

    def test_groups_share_parsed_results_and_review_events(self):
        with get_db() as conn:
            conn.execute("""INSERT INTO skill_review_records
                (id, organization_id, skill_id, action, operator_id, created_at, field_diffs_json, detail_json)
                VALUES ('stats_manual_once', 'org_greentown', 'stats_skill_0', 'approve_with_changes',
                        'usr_admin_001', '2026-10-01', ?, ?)""",
                         (json.dumps([{"path": "goal"}, {"path": "goal"}]), json.dumps({"unsupported_items": []})))
        try:
            with get_db() as conn, patch.object(skill_statistics, "_json", wraps=skill_statistics._json) as parse:
                result = get_statistics(conn, {"organization_id": "org_greentown"})
                # 两个批次结果、一份人工差异、一份人工处理明细，与分组数量无关。
                self.assertEqual(parse.call_count, 4)
            self.assertEqual(result["summary"]["modified_field_total"], 1)
            self.assertEqual(sum(b["modified_field_total"] for b in result["by_batch"]), 1)
            self.assertEqual(sum(s["modified_field_total"] for s in result["by_scene"]), 1)
        finally:
            with get_db() as conn:
                conn.execute("DELETE FROM skill_review_records WHERE id = 'stats_manual_once'")

    def test_auth_tenant_isolation_and_no_mutation(self):
        self.assertEqual(self.client.get("/api/skill-factory/statistics").status_code, 401)
        self.assertEqual(self.client.get("/api/skill-factory/statistics", headers=self.headers["member"]).status_code, 403)
        other = self.client.get("/api/skill-factory/statistics", headers=self.headers["other_admin"]).json()
        self.assertEqual(other["summary"]["candidate_count"], 0)
        self.assertEqual([s["name"] for s in other["by_scene"]], ["其他企业场景"])
        foreign = self.client.get("/api/skill-factory/statistics", headers=self.headers["other_admin"],
                                  params={"batch_id": "stats_batch_0"}).json()
        self.assertEqual(foreign["by_batch"], [])
        with get_db() as conn:
            before = conn.total_changes
            get_statistics(conn, {"organization_id": "org_greentown"})
            self.assertEqual(conn.total_changes, before)


if __name__ == "__main__":
    unittest.main()
