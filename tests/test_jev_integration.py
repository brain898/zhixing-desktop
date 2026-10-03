import json
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from test_env_helper import cleanup_test_db, setup_test_db


def choice(value, options, confidence=0.8):
    probabilities = {key: 0.0 for key in options}
    probabilities[value] = 0.8
    remainder = 0.2 / max(1, len(options) - 1)
    for key in probabilities:
        if key != value:
            probabilities[key] = remainder
    return {"type": "choice", "choice": value, "probabilities": probabilities, "confidence": confidence}


class TestJevIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = setup_test_db("jev_integration")
        import jev_evaluator as jev
        cls.jev = jev

    @classmethod
    def tearDownClass(cls):
        cleanup_test_db(cls.db_path)

    def setUp(self):
        from database import get_db
        now = datetime.now(timezone.utc).isoformat()
        suffix = uuid.uuid4().hex[:8]
        self.doc_id = f"doc_jev_{suffix}"
        self.doc_version_id = f"dv_jev_{suffix}"
        self.item_id = f"ki_jev_{suffix}"
        self.version_id = f"kv_jev_{suffix}"
        self.block_id = f"sb_jev_{suffix}"
        self.revision = f"rev_{suffix}"
        with get_db() as conn:
            conn.execute(
                "INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted, created_at, updated_at) VALUES (?, 'org_greentown', 'Jev测试', ?, 'admin_only', 0, ?, ?)",
                (self.doc_id, self.doc_version_id, now, now),
            )
            conn.execute(
                """INSERT INTO document_versions
                   (id, document_id, organization_id, version_label, file_name, file_type, file_size, content_hash,
                    storage_reference, uploaded_by, uploaded_at, processing_status)
                   VALUES (?, ?, 'org_greentown', 'v1', 'jev.md', 'md', 10, ?, 'jev.md', 'usr_admin_001', ?, 'completed')""",
                (self.doc_version_id, self.doc_id, suffix, now),
            )
            conn.execute(
                """INSERT INTO knowledge_items
                   (id, document_id, organization_id, active_version_id, access_scope, lifecycle_status, created_at, updated_at)
                   VALUES (?, ?, 'org_greentown', ?, 'admin_only', 'active', ?, ?)""",
                (self.item_id, self.doc_id, self.version_id, now, now),
            )
            conn.execute(
                """INSERT INTO knowledge_versions
                   (id, item_id, organization_id, source_document_version_id, version_number, title, content,
                    primary_category, atom_type, subject, statement, conditions_json, actions_json, exceptions_json,
                    field_states_json, quality_flags_json, customer_types_json, business_scenes_json, problem_tags_json,
                    source_anchors_json, review_status, index_status, revision_token, created_at, created_by)
                   VALUES (?, ?, 'org_greentown', ?, 1, '报修响应', '一级报修15分钟内到场', '制度与标准', '规则',
                           '维修技工', '一级报修须在15分钟内到场', '["一级报修"]', '["15分钟内到场"]', '[]',
                           '{}', '[]', '[]', '[]', '[]', '[]', 'pending_review', 'not_indexed', ?, ?, 'system_extractor')""",
                (self.version_id, self.item_id, self.doc_version_id, self.revision, now),
            )
            source_text = "发生室内跑水等一级紧急报修时，维修技工应在15分钟内到达现场；恶劣天气导致道路封闭时除外。"
            conn.execute(
                """INSERT INTO source_blocks
                   (id, document_version_id, organization_id, block_index, block_type, heading_path, paragraph_anchor, text_content, created_at)
                   VALUES (?, ?, 'org_greentown', 1, 'paragraph', '报修标准', 'line_1', ?, ?)""",
                (self.block_id, self.doc_version_id, source_text, now),
            )
            conn.execute(
                """INSERT INTO knowledge_evidence
                   (id, knowledge_version_id, source_block_id, organization_id, field_name, excerpt, accuracy_level, created_at)
                   VALUES (?, ?, ?, 'org_greentown', 'statement', ?, 'exact', ?)""",
                (f"ke_{suffix}", self.version_id, self.block_id, source_text, now),
            )

    def _response(self, overrides=None):
        jev = self.jev
        overrides = overrides or {}
        answers = {
            "primary_category": choice("制度与标准", jev.CATEGORY_DEFINITIONS),
            "applicability_changed": choice("no_issue", jev.CHECK_OPTIONS),
            "exception_omitted": choice("suspected_issue", jev.CHECK_OPTIONS, 0.91),
            "unsupported_addition": choice("no_issue", jev.CHECK_OPTIONS),
            "numeric_relation_changed": choice("no_issue", jev.CHECK_OPTIONS),
            "rules_merged": choice("insufficient_evidence", jev.CHECK_OPTIONS, 0.55),
            "missing_context": choice("not_applicable", jev.CHECK_OPTIONS),
        }
        answers.update(overrides)
        return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 50}}

    def test_questions_are_independent_choice_questions_and_state_contains_original_source(self):
        from database import get_db
        jev = self.jev
        questions = jev.build_questions()
        self.assertEqual(set(questions), set(jev.QUESTION_META))
        self.assertTrue(all(q["type"] == "choice" for q in questions.values()))
        self.assertIn("证据不足", questions["exception_omitted"]["criteria"]["insufficient_evidence"])
        with get_db() as conn:
            version = conn.execute("SELECT * FROM knowledge_versions WHERE id=?", (self.version_id,)).fetchone()
            evidence = conn.execute(
                """SELECT ke.source_block_id, ke.field_name, ke.excerpt, sb.heading_path, sb.page_number,
                          sb.paragraph_anchor, sb.text_content FROM knowledge_evidence ke
                   JOIN source_blocks sb ON sb.id=ke.source_block_id WHERE ke.knowledge_version_id=?""",
                (self.version_id,),
            ).fetchall()
        state = json.loads(jev.build_state(version, evidence))
        self.assertIn("恶劣天气", state["source_blocks"][0]["text"])
        self.assertEqual(state["candidate_version"]["candidate_revision_token"], self.revision)
        self.assertIn("待分析数据", state["data_handling_note"])

    def test_mock_evaluation_persists_probabilities_confidence_priority_and_does_not_overwrite_category(self):
        from database import get_db
        jev = self.jev
        with patch.object(jev, "JEV_ENABLED", True), patch.object(jev, "JEV_API_KEY", "test-key"):
            run = jev.create_or_get_evaluation(self.version_id)
            with patch.object(jev, "call_jev", return_value=(self._response(), 37)):
                jev.execute_evaluation(run["evaluation_id"])
        with get_db() as conn:
            saved = jev.serialize_evaluation(conn, self.version_id, self.revision)
            category = conn.execute("SELECT primary_category FROM knowledge_versions WHERE id=?", (self.version_id,)).fetchone()[0]
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["actual_model"], "jev-1.13.0")
        self.assertEqual(saved["review_priority"], "high")
        self.assertEqual(saved["usage"]["input_tokens"], 100)
        omitted = next(a for a in saved["answers"] if a["question_id"] == "exception_omitted")
        self.assertEqual(omitted["choice"], "suspected_issue")
        self.assertAlmostEqual(omitted["confidence"], 0.91)
        self.assertIn("suspected_issue", omitted["probabilities"])
        self.assertEqual(category, "制度与标准")

    def test_correct_no_issue_insufficient_and_not_applicable_remain_distinct(self):
        jev = self.jev
        response = self._response({
            "exception_omitted": choice("no_issue", jev.CHECK_OPTIONS),
            "rules_merged": choice("insufficient_evidence", jev.CHECK_OPTIONS),
            "numeric_relation_changed": choice("not_applicable", jev.CHECK_OPTIONS),
        })
        parsed = {qid: jev._validate_answer(qid, response["answers"][qid]) for qid in jev.QUESTION_META}
        self.assertEqual(parsed["exception_omitted"]["choice"], "no_issue")
        self.assertEqual(parsed["rules_merged"]["choice"], "insufficient_evidence")
        self.assertEqual(parsed["numeric_relation_changed"]["choice"], "not_applicable")

    def test_each_requested_semantic_problem_routes_to_priority_without_free_text(self):
        jev = self.jev
        for question_id in (
            "applicability_changed", "exception_omitted", "unsupported_addition",
            "numeric_relation_changed", "rules_merged", "missing_context",
        ):
            response = self._response({
                key: choice("no_issue", jev.CHECK_OPTIONS) for key in jev.QUESTION_META if key != "primary_category"
            })
            response["answers"][question_id] = choice("suspected_issue", jev.CHECK_OPTIONS, 0.9)
            parsed = {qid: jev._validate_answer(qid, response["answers"][qid]) for qid in jev.QUESTION_META}
            priority, reasons = jev._priority(parsed, "制度与标准")
            self.assertEqual(priority, "high", question_id)
            self.assertTrue(reasons, question_id)
            self.assertEqual(set(parsed[question_id]), {"choice", "probabilities", "confidence"})

    def test_missing_key_and_disabled_are_explicit_and_idempotent(self):
        jev = self.jev
        with patch.object(jev, "JEV_ENABLED", True), patch.object(jev, "JEV_API_KEY", ""):
            first = jev.create_or_get_evaluation(self.version_id)
            second = jev.create_or_get_evaluation(self.version_id)
        self.assertEqual(first["status"], "not_configured")
        self.assertEqual(first["evaluation_id"], second["evaluation_id"])
        self.assertFalse(first["should_submit"])

    def test_timeout_is_saved_as_failure_not_pass(self):
        from database import get_db
        jev = self.jev
        with patch.object(jev, "JEV_ENABLED", True), patch.object(jev, "JEV_API_KEY", "test-key"):
            run = jev.create_or_get_evaluation(self.version_id)
            with patch.object(jev, "call_jev", side_effect=jev.JevError("timeout", "Jev 请求超时", True)):
                jev.execute_evaluation(run["evaluation_id"])
        with get_db() as conn:
            saved = jev.serialize_evaluation(conn, self.version_id, self.revision)
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["error_code"], "timeout")
        self.assertEqual(saved["answers"], [])

    def test_ignored_semantic_hints_no_longer_raise_review_priority(self):
        from database import get_db
        jev = self.jev
        with patch.object(jev, "JEV_ENABLED", True), patch.object(jev, "JEV_API_KEY", "test-key"):
            run = jev.create_or_get_evaluation(self.version_id)
            with patch.object(jev, "call_jev", return_value=(self._response(), 12)):
                jev.execute_evaluation(run["evaluation_id"])
        with get_db() as conn:
            conn.execute(
                """UPDATE jev_evaluation_answers SET ignored_by='usr_admin_001', ignored_at=?, ignore_reason='已人工核对'
                   WHERE evaluation_id=? AND question_id IN ('exception_omitted', 'rules_merged')""",
                (datetime.now(timezone.utc).isoformat(), run["evaluation_id"]),
            )
            saved = jev.serialize_evaluation(conn, self.version_id, self.revision)
        self.assertEqual(saved["review_priority"], "normal")
        self.assertTrue(all(a["ignored"] for a in saved["answers"] if a["question_id"] in ("exception_omitted", "rules_merged")))

    def test_late_response_is_marked_stale_after_revision_race(self):
        from database import get_db
        jev = self.jev
        with patch.object(jev, "JEV_ENABLED", True), patch.object(jev, "JEV_API_KEY", "test-key"):
            run = jev.create_or_get_evaluation(self.version_id)

            def race_response(_state):
                with get_db() as conn:
                    conn.execute("UPDATE knowledge_versions SET revision_token='rev_changed' WHERE id=?", (self.version_id,))
                return self._response(), 20

            with patch.object(jev, "call_jev", side_effect=race_response):
                jev.execute_evaluation(run["evaluation_id"])
        with get_db() as conn:
            status = conn.execute("SELECT status FROM jev_evaluations WHERE id=?", (run["evaluation_id"],)).fetchone()[0]
            answer_count = conn.execute("SELECT COUNT(*) FROM jev_evaluation_answers WHERE evaluation_id=?", (run["evaluation_id"],)).fetchone()[0]
        self.assertEqual(status, "stale")
        self.assertEqual(answer_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
