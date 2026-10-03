"""Stage 4B 验收：真实 BGE 持久化索引与关键词 + 语义 RRF 混合检索。"""
import json
import sys
import time
import uuid
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from main import app
from database import get_db
from config import EMBEDDING_DIM, EMBEDDING_MODEL_NAME
from embedding_service import get_embedding_runtime_info
from indexing import (
    create_or_get_build_index_task,
    execute_build_index_task,
)
from tasks import recover_interrupted_tasks
from hybrid_retrieval import get_retrieval_config_hash, hybrid_search


class TestStage4BHybridRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_env_helper import setup_test_db
        cls.test_db = setup_test_db("stage4b_tests")
        cls.client = TestClient(app)

        admin = cls.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Admin@Zhixing2026"},
        )
        assert admin.status_code == 200, admin.text
        cls.admin_headers = {"Authorization": f"Bearer {admin.json()['token']}"}
        cls.admin_user = admin.json()["user"]

        member = cls.client.post(
            "/api/auth/login",
            json={"username": "member", "password": "Member@Zhixing2026"},
        )
        assert member.status_code == 200, member.text
        cls.member_headers = {"Authorization": f"Bearer {member.json()['token']}"}
        cls.member_user = member.json()["user"]
        cls.org_id = cls.admin_user["organization_id"]

    @classmethod
    def tearDownClass(cls):
        from test_env_helper import cleanup_test_db
        cleanup_test_db(cls.test_db)

    def setUp(self):
        self.prefix = "s4b_" + uuid.uuid4().hex[:8]

    def _wait_status(self, version_id, expected, timeout=40.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT review_status, index_status FROM knowledge_versions WHERE id = ?",
                    (version_id,),
                ).fetchone()
                if row and row["index_status"] == expected:
                    return row
                if row and row["index_status"] == "failed" and expected != "failed":
                    task = conn.execute(
                        """SELECT status, attempt_count, error_message
                           FROM processing_tasks
                           WHERE target_id = ? AND task_type = 'build_index'
                           ORDER BY created_at DESC LIMIT 1""",
                        (version_id,),
                    ).fetchone()
                    self.fail("索引失败: " + str(dict(task) if task else "unknown"))
            time.sleep(0.05)
        self.fail(f"等待 {version_id} -> {expected} 超时")

    def _create_document(self, title, access_scope="org_internal"):
        now = datetime.now(timezone.utc).isoformat()
        doc_id = f"doc_{self.prefix}_{uuid.uuid4().hex[:6]}"
        ver_id = f"ver_{self.prefix}_{uuid.uuid4().hex[:6]}"
        block_ids = [f"blk_{self.prefix}_{uuid.uuid4().hex[:6]}" for _ in range(4)]
        with get_db() as conn:
            conn.execute(
                """INSERT INTO documents
                   (id, organization_id, title, active_version_id, access_scope,
                    is_deleted, created_at, updated_at)
                   VALUES (?, ?, ?, NULL, ?, 0, ?, ?)""",
                (doc_id, self.org_id, title, access_scope, now, now),
            )
            conn.execute(
                """INSERT INTO document_versions
                   (id, document_id, organization_id, version_label, file_name,
                    file_type, file_size, content_hash, storage_reference,
                    uploaded_by, uploaded_at, processing_status)
                   VALUES (?, ?, ?, 'v1', ?, 'txt', 512, ?, ?, ?, ?, 'completed')""",
                (
                    ver_id,
                    doc_id,
                    self.org_id,
                    title + ".txt",
                    uuid.uuid4().hex,
                    f"/tmp/{ver_id}.txt",
                    self.admin_user["id"],
                    now,
                ),
            )
            texts = [
                "核心陈述原文证据",
                "适用条件与执行动作原文证据",
                "例外与停止条件原文证据",
                "指标与案例原文证据",
            ]
            for i, (block_id, text) in enumerate(zip(block_ids, texts), start=1):
                conn.execute(
                    """INSERT INTO source_blocks
                       (id, document_version_id, organization_id, block_index,
                        block_type, heading_path, page_number, paragraph_anchor,
                        text_content, created_at)
                       VALUES (?, ?, ?, ?, 'paragraph', ?, NULL, ?, ?, ?)""",
                    (
                        block_id, ver_id, self.org_id, i,
                        "测试章节", f"[line_{i}]", text, now,
                    ),
                )
        return doc_id, ver_id, block_ids

    def _create_knowledge(
        self,
        doc_id,
        doc_ver_id,
        block_ids,
        title,
        statement,
        conditions=None,
        actions=None,
        exceptions=None,
        metric=None,
        case=None,
        access_scope="org_internal",
        problem_tags=None,
        primary_category="制度与标准",
        customer_types=None,
        business_scenes=None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        item_id = f"ki_{self.prefix}_{uuid.uuid4().hex[:6]}"
        version_id = f"kv_{self.prefix}_{uuid.uuid4().hex[:6]}"
        conditions = conditions or []
        actions = actions or []
        exceptions = exceptions or []
        metric = metric or {}
        case = case or {}
        problem_tags = problem_tags or []
        customer_types = customer_types or ["住宅物业"]
        business_scenes = business_scenes or ["现场作业"]
        content = statement + " 该知识用于物业现场执行与核对。"
        with get_db() as conn:
            conn.execute(
                """INSERT INTO knowledge_items
                   (id, document_id, organization_id, active_version_id,
                    access_scope, lifecycle_status, is_excluded, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'active', 0, ?, ?)""",
                (
                    item_id, doc_id, self.org_id, version_id,
                    access_scope, now, now,
                ),
            )
            conn.execute(
                """INSERT INTO knowledge_versions
                   (id, item_id, organization_id, source_document_version_id,
                    version_number, title, content, primary_category, atom_type,
                    subject, statement, conditions_json, actions_json,
                    exceptions_json, metric_definition_json, case_details_json,
                    customer_types_json, business_scenes_json, problem_tags_json,
                    source_anchors_json, review_status, index_status,
                    revision_token, created_at, created_by)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, '规则', '物业服务人员',
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review',
                           'not_indexed', ?, ?, ?)""",
                (
                    version_id, item_id, self.org_id, doc_ver_id,
                    title, content, primary_category, statement,
                    json.dumps(conditions, ensure_ascii=False),
                    json.dumps(actions, ensure_ascii=False),
                    json.dumps(exceptions, ensure_ascii=False),
                    json.dumps(metric, ensure_ascii=False),
                    json.dumps(case, ensure_ascii=False),
                    json.dumps(customer_types, ensure_ascii=False),
                    json.dumps(business_scenes, ensure_ascii=False),
                    json.dumps(problem_tags, ensure_ascii=False),
                    json.dumps([f"[line_{i}]" for i in range(1, 5)], ensure_ascii=False),
                    uuid.uuid4().hex, now, self.admin_user["id"],
                ),
            )
            evidence_specs = [
                ("statement", statement, block_ids[0]),
                ("conditions", "；".join(conditions) or statement, block_ids[1]),
                ("actions", "；".join(actions) or statement, block_ids[1]),
                ("exceptions", "；".join(exceptions) or statement, block_ids[2]),
            ]
            evidence_ids = []
            for field_name, excerpt, block_id in evidence_specs:
                ev_id = f"ev_{uuid.uuid4().hex[:10]}"
                evidence_ids.append(ev_id)
                conn.execute(
                    """INSERT INTO knowledge_evidence
                       (id, knowledge_version_id, source_block_id, organization_id,
                        field_name, excerpt, accuracy_level, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'high', ?)""",
                    (
                        ev_id, version_id, block_id, self.org_id,
                        field_name, excerpt, now,
                    ),
                )
        return item_id, version_id, evidence_ids

    def _confirm_and_wait(self, item_id, version_id):
        started = time.perf_counter()
        detail = self.client.get(f"/api/knowledge/items/{item_id}", headers=self.admin_headers).json()
        resp = self.client.post(
            f"/api/knowledge/items/{item_id}/confirm",
            json={"revision_token": detail["active_version"]["revision_token"]},
            headers=self.admin_headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["review_status"], "confirmed")
        self.assertEqual(resp.json()["index_status"], "indexing")
        self._wait_status(version_id, "ready")
        return resp, time.perf_counter() - started

    def _pump_fixture(self):
        doc_id, doc_ver, blocks = self._create_document("二次供水运行规程")
        item_id, version_id, evidence_ids = self._create_knowledge(
            doc_id, doc_ver, blocks,
            "二次供水泵房巡检标准",
            "物业工程人员应每日检查二次供水泵房压力表、变频泵运行状态及水箱液位。",
            conditions=["当泵房处于正常运行状态时", "每日巡检"],
            actions=["记录压力表读数", "检查变频泵是否异响", "核对水箱液位"],
            exceptions=["发现水质异常或设备漏水时，应立即停止供水设备并上报，不得继续运行"],
            metric={"name": "巡检频次", "unit": "次/日", "criteria": "至少1次"},
            case={"background": "泵房日常巡检", "actions": ["核表", "查泵"], "results": "异常及时处置"},
            problem_tags=["二次供水", "泵房巡检"],
        )
        return doc_id, doc_ver, item_id, version_id, evidence_ids

    def test_01_real_embedding_persistence_async_and_idempotency(self):
        doc_id, _, item_id, version_id, _ = self._pump_fixture()
        confirm, elapsed = self._confirm_and_wait(item_id, version_id)
        config_hash = get_retrieval_config_hash()
        with get_db() as conn:
            records = conn.execute(
                """SELECT fragment_key, vector_json, model_name, embedding_dim,
                          content_hash, config_hash
                   FROM retrieval_records
                   WHERE knowledge_version_id = ? AND config_hash = ?
                   ORDER BY fragment_key""",
                (version_id, config_hash),
            ).fetchall()
            self.assertGreaterEqual(len(records), 5)
            for record in records:
                vector = json.loads(record["vector_json"])
                self.assertEqual(len(vector), 512)
                self.assertTrue(any(abs(value) > 1e-9 for value in vector))
                self.assertEqual(record["model_name"], EMBEDDING_MODEL_NAME)
                self.assertEqual(record["embedding_dim"], EMBEDDING_DIM)
                self.assertTrue(record["content_hash"])
            task = conn.execute(
                """SELECT id, status, attempt_count
                   FROM processing_tasks
                   WHERE target_id = ? AND task_type = 'build_index'""",
                (version_id,),
            ).fetchone()
            before_count = len(records)
            repeat = create_or_get_build_index_task(
                conn, version_id, self.org_id, retry_failed=False
            )
            after_count = conn.execute(
                "SELECT COUNT(*) FROM retrieval_records WHERE knowledge_version_id = ?",
                (version_id,),
            ).fetchone()[0]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["attempt_count"], 1)
        self.assertEqual(repeat["task_id"], task["id"])
        self.assertFalse(repeat["should_submit"])
        self.assertEqual(before_count, after_count)

        exact_q = "二次供水泵房"
        exact = self.client.get(
            f"/api/knowledge/search?q={exact_q}&document_id={doc_id}",
            headers=self.admin_headers,
        ).json()
        self.assertEqual(exact["total"], 1)
        self.assertEqual(exact["items"][0]["version_id"], version_id)

        synonym_q = "水泵房每天要看哪些东西"
        synonym = self.client.get(
            f"/api/knowledge/search?q={synonym_q}&document_id={doc_id}",
            headers=self.admin_headers,
        ).json()
        self.assertEqual(synonym["total"], 1)
        channels = {
            channel
            for fragment in synonym["items"][0]["matched_fragments"]
            for channel in fragment["channels"]
        }
        self.assertIn("dense", channels)

        none_q = "量子纠缠火星轨道望远镜维修"
        none_result = self.client.get(
            f"/api/knowledge/search?q={none_q}&document_id={doc_id}",
            headers=self.admin_headers,
        ).json()
        self.assertEqual(none_result["total"], 0)

        runtime = get_embedding_runtime_info()
        print(
            f"[Stage4B] model={runtime['model_name']} dim={runtime['embedding_dim']} "
            f"fragments={before_count} index_elapsed_s={elapsed:.3f} "
            f"attempt_count={task['attempt_count']} duplicate_delta={after_count-before_count}"
        )
        print(f"[Stage4B] keyword_query={exact_q!r} -> {exact['items'][0]['title']}")
        print(f"[Stage4B] synonym_query={synonym_q!r} channels={sorted(channels)}")
        print(f"[Stage4B] no_result_query={none_q!r} total={none_result['total']}")

    def test_02_long_question_conditions_exceptions_full_atom_and_source(self):
        doc_id, _, item_id, version_id, evidence_ids = self._pump_fixture()
        self._confirm_and_wait(item_id, version_id)

        queries = [
            "正常运行的泵房每天巡检要满足什么条件",
            "如果泵房发现漏水还能继续运行吗",
            "小区二次供水泵房正常运行时，物业工程人员每天巡检要检查哪些设备、记录什么，如果发现设备漏水又该怎么处理？",
        ]
        results = []
        for query in queries:
            payload = self.client.get(
                f"/api/knowledge/search?q={query}&document_id={doc_id}",
                headers=self.admin_headers,
            ).json()
            self.assertGreaterEqual(payload["total"], 1, query)
            target = next(
                (x for x in payload["items"] if x["version_id"] == version_id),
                None,
            )
            self.assertIsNotNone(target, query)
            results.append(target)

        target = results[-1]
        self.assertEqual(target["conditions"], ["当泵房处于正常运行状态时", "每日巡检"])
        self.assertIn("记录压力表读数", target["actions"])
        self.assertIn("不得继续运行", target["exceptions"][0])
        self.assertEqual(target["metric_definition"]["unit"], "次/日")
        self.assertEqual(target["case_details"]["background"], "泵房日常巡检")
        self.assertEqual(set(evidence_ids), {x["id"] for x in target["evidence"]})
        self.assertTrue(all(x["source_locator"]["paragraph_anchor"] for x in target["evidence"]))
        self.assertEqual(target["source"]["document_id"], doc_id)
        self.assertTrue(target["matched_fragments"])
        self.assertEqual(target["score_type"], "rrf_relevance")
        self.assertIn("不表示事实可信度", target["score_explanation"])

        payload = self.client.get(
            f"/api/knowledge/search?q=泵房&document_id={doc_id}",
            headers=self.admin_headers,
        ).json()
        self.assertEqual(
            sum(1 for x in payload["items"] if x["version_id"] == version_id),
            1,
            "同一知识多个 fragment 命中后必须按 knowledge_version_id 合并",
        )
        print(
            "[Stage4B] long_query matched_fragments="
            + ",".join(x["fragment_type"] for x in target["matched_fragments"])
        )

    def test_03_single_file_all_scope_and_member_permission(self):
        doc1, ver1, blocks1 = self._create_document("电梯应急处置规程")
        item1, kv1, _ = self._create_knowledge(
            doc1, ver1, blocks1,
            "电梯困人应急处置",
            "接到乘客被困电梯报警后，物业值班人员应立即安抚被困人员并联系电梯维保单位救援。",
            conditions=["收到电梯困人报警"],
            actions=["保持通话安抚", "通知电梯维保单位", "设置现场警戒"],
            exceptions=["非专业人员不得强行撬门救人"],
            problem_tags=["电梯困人", "应急处置"],
        )
        self._confirm_and_wait(item1, kv1)

        doc2, ver2, blocks2 = self._create_document("访客通行管理制度")
        item2, kv2, _ = self._create_knowledge(
            doc2, ver2, blocks2,
            "未预约访客登记规则",
            "未预约访客应核验身份证件并联系被访人确认后方可放行。",
            conditions=["访客未提前预约"],
            actions=["核验证件", "联系被访人确认"],
            exceptions=["无法确认被访人时不得放行"],
            problem_tags=["访客登记", "门岗"],
        )
        self._confirm_and_wait(item2, kv2)

        query = "访客没有提前登记，门岗应该怎么处理"
        all_scope = self.client.get(
            f"/api/knowledge/search?q={query}",
            headers=self.admin_headers,
        ).json()
        self.assertIn(kv2, {x["version_id"] for x in all_scope["items"]})

        single_file = self.client.get(
            f"/api/knowledge/search?q={query}&document_id={doc1}",
            headers=self.admin_headers,
        ).json()
        self.assertNotIn(kv2, {x["version_id"] for x in single_file["items"]})

        with get_db() as conn:
            member_before = hybrid_search(
                conn,
                self.member_user,
                "电梯困人",
                datetime.now(timezone.utc).isoformat(),
                document_id=doc1,
            )
        self.assertIn(kv1, {x["version_id"] for x in member_before})

        tighten = self.client.put(
            f"/api/documents/{doc1}/access-scope",
            json={"access_scope": "admin_only"},
            headers=self.admin_headers,
        )
        self.assertEqual(tighten.status_code, 200, tighten.text)

        with get_db() as conn:
            member_after = hybrid_search(
                conn,
                self.member_user,
                "电梯困人",
                datetime.now(timezone.utc).isoformat(),
                document_id=doc1,
            )
        admin_after = self.client.get(
            f"/api/knowledge/search?q=电梯困人&document_id={doc1}",
            headers=self.admin_headers,
        ).json()
        self.assertNotIn(kv1, {x["version_id"] for x in member_after})
        self.assertIn(kv1, {x["version_id"] for x in admin_after["items"]})
        print(
            f"[Stage4B] scope_query all_has_visitor={kv2 in {x['version_id'] for x in all_scope['items']}} "
            f"single_file_has_visitor={kv2 in {x['version_id'] for x in single_file['items']}}"
        )

    def test_04_failed_index_retry_attempt_count_and_no_duplicates(self):
        doc_id, doc_ver, blocks = self._create_document("消防水泵巡检制度")
        item_id, version_id, _ = self._create_knowledge(
            doc_id, doc_ver, blocks,
            "消防水泵巡检要求",
            "物业工程人员每周应试运行消防水泵并记录压力和运行声音。",
            conditions=["每周例行巡检"],
            actions=["试运行消防泵", "记录压力"],
            exceptions=["发现异常振动时停止试运行并报修"],
            problem_tags=["消防泵", "巡检"],
        )

        with patch("indexing.embed_texts", side_effect=RuntimeError("stage4b injected embedding failure")):
            detail = self.client.get(f"/api/knowledge/items/{item_id}", headers=self.admin_headers).json()
            response = self.client.post(
                f"/api/knowledge/items/{item_id}/confirm",
                json={"revision_token": detail["active_version"]["revision_token"]},
                headers=self.admin_headers,
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["index_status"], "indexing")
            self._wait_status(version_id, "failed")

        with get_db() as conn:
            failed = conn.execute(
                """SELECT id, status, attempt_count, error_message
                   FROM processing_tasks
                   WHERE target_id = ? AND task_type = 'build_index'""",
                (version_id,),
            ).fetchone()
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["attempt_count"], 1)
            self.assertIn("injected embedding failure", failed["error_message"])
            before = conn.execute(
                "SELECT COUNT(*) FROM retrieval_records WHERE knowledge_version_id = ?",
                (version_id,),
            ).fetchone()[0]
            state = conn.execute(
                "SELECT review_status, index_status FROM knowledge_versions WHERE id = ?",
                (version_id,),
            ).fetchone()
            self.assertEqual(state["review_status"], "confirmed")
            self.assertEqual(state["index_status"], "failed")
            self.assertEqual(before, 0)

        retry = self.client.post(
            f"/api/knowledge/versions/{version_id}/index/retry",
            headers=self.admin_headers,
        )
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["index_task_id"], failed["id"])
        self._wait_status(version_id, "ready")

        with get_db() as conn:
            completed = conn.execute(
                """SELECT status, attempt_count
                   FROM processing_tasks WHERE id = ?""",
                (failed["id"],),
            ).fetchone()
            rows = conn.execute(
                """SELECT fragment_key, config_hash
                   FROM retrieval_records
                   WHERE knowledge_version_id = ?""",
                (version_id,),
            ).fetchall()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["attempt_count"], 2)
        self.assertGreater(len(rows), 0)
        self.assertEqual(
            len(rows),
            len({(x["fragment_key"], x["config_hash"]) for x in rows}),
            "失败重试后不能产生重复片段",
        )
        found = self.client.get(
            f"/api/knowledge/search?q=消防水泵&document_id={doc_id}",
            headers=self.admin_headers,
        ).json()
        self.assertIn(version_id, {x["version_id"] for x in found["items"]})
        print(
            f"[Stage4B] retry task={failed['id']} attempts={completed['attempt_count']} "
            f"records={len(rows)}"
        )

    def test_05_late_task_cannot_revive_disabled_content(self):
        doc_id, doc_ver, blocks = self._create_document("迟到任务测试规程")
        item_id, version_id, _ = self._create_knowledge(
            doc_id, doc_ver, blocks,
            "迟到索引任务测试知识",
            "该条知识仅用于验证失效内容不能被迟到任务重新置为可检索。",
            actions=["验证任务取消"],
        )
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            conn.execute(
                """UPDATE knowledge_versions
                   SET review_status='confirmed', reviewed_by=?, reviewed_at=?,
                       index_status='not_indexed'
                   WHERE id=?""",
                (self.admin_user["id"], now, version_id),
            )
            conn.execute(
                "UPDATE documents SET active_version_id=? WHERE id=?",
                (doc_ver, doc_id),
            )
            task = create_or_get_build_index_task(
                conn, version_id, self.org_id, retry_failed=False
            )
            self.assertTrue(task["should_submit"])
            conn.execute(
                "UPDATE knowledge_items SET lifecycle_status='disabled' WHERE id=?",
                (item_id,),
            )

        execute_build_index_task(task["task_id"])
        with get_db() as conn:
            task_row = conn.execute(
                "SELECT status, attempt_count FROM processing_tasks WHERE id=?",
                (task["task_id"],),
            ).fetchone()
            state = conn.execute(
                "SELECT index_status FROM knowledge_versions WHERE id=?",
                (version_id,),
            ).fetchone()
            record_count = conn.execute(
                "SELECT COUNT(*) FROM retrieval_records WHERE knowledge_version_id=?",
                (version_id,),
            ).fetchone()[0]
        self.assertEqual(task_row["status"], "cancelled")
        self.assertEqual(task_row["attempt_count"], 1)
        self.assertEqual(state["index_status"], "not_indexed")
        self.assertEqual(record_count, 0)


    def test_06_restart_recovers_running_build_index(self):
        doc_id, doc_ver, blocks = self._create_document("重启恢复索引规程")
        item_id, version_id, _ = self._create_knowledge(
            doc_id, doc_ver, blocks,
            "重启恢复测试知识",
            "物业项目应在系统重启后继续完成已经排队或运行中的正式索引任务。",
            actions=["恢复后台索引任务"],
        )
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            conn.execute(
                """UPDATE knowledge_versions
                   SET review_status='confirmed', reviewed_by=?, reviewed_at=?,
                       index_status='not_indexed'
                   WHERE id=?""",
                (self.admin_user["id"], now, version_id),
            )
            conn.execute(
                "UPDATE documents SET active_version_id=? WHERE id=?",
                (doc_ver, doc_id),
            )
            task = create_or_get_build_index_task(
                conn, version_id, self.org_id, retry_failed=False
            )
            conn.execute(
                """UPDATE processing_tasks
                   SET status='running', attempt_count=1, started_at=?
                   WHERE id=?""",
                (now, task["task_id"]),
            )
            conn.execute(
                "UPDATE knowledge_versions SET index_status='indexing' WHERE id=?",
                (version_id,),
            )

        recover_interrupted_tasks()
        self._wait_status(version_id, "ready")
        with get_db() as conn:
            recovered = conn.execute(
                "SELECT status, attempt_count FROM processing_tasks WHERE id=?",
                (task["task_id"],),
            ).fetchone()
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["attempt_count"], 2)
        print(
            f"[Stage4B] restart_recovery task={task['task_id']} "
            f"attempts={recovered['attempt_count']}"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
