"""Stage 4E：固定人工标注检索集与 A/B/C 可复现对照实验。

A = 普通固定长度切片；B = 原文结构块；C = 已确认知识原子 + 必要 evidence。
本文件只比较检索与上下文完整性，不调用 Agent / 回答模型。
"""
import json
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
TESTS_DIR = Path(__file__).resolve().parent
for _path in (str(SERVER_DIR), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from config import RETRIEVAL_DENSE_MIN_SCORE, RETRIEVAL_RRF_K
from database import get_db
from embedding_service import cosine_similarity, embed_query, embed_texts, get_embedding_runtime_info
from hybrid_retrieval import _keyword_score, hybrid_search
import test_stage4b_hybrid_retrieval as stage4b


CONTEXT_BUDGET = 520
TOP_K = 3
BENCHMARK_CASES = [
    {
        "id": "term",
        "query": "二次供水泵房巡检",
        "target": "water",
        "required": ["二次供水泵房", "每日检查"],
    },
    {
        "id": "synonym",
        "query": "水泵房每天要看哪些东西",
        "target": "water",
        "required": ["压力", "运行声音"],
    },
    {
        "id": "long_question",
        "query": "有人被困在电梯里，物业值班人员先做什么，哪些事情不能做？",
        "target": "elevator",
        "required": ["安抚", "联系电梯维保单位", "不得强行撬门"],
    },
    {
        "id": "condition",
        "query": "未预约访客到访时门岗应该怎样处理",
        "target": "visitor",
        "required": ["核验身份证件", "联系被访人确认"],
    },
    {
        "id": "exception",
        "query": "电梯困人时物业人员可以自己撬门救人吗",
        "target": "elevator",
        "required": ["不得强行撬门"],
    },
    {
        "id": "stop_condition",
        "query": "消防水泵试运行时什么情况要停止并报修",
        "target": "firepump",
        "required": ["异常振动", "停止试运行", "报修"],
    },
    {
        "id": "no_result",
        "query": "量子纠缠火星轨道望远镜维修",
        "target": None,
        "required": [],
    },
]


SOURCE_TEXT = {
    "water": [
        "二次供水泵房应每日检查压力表、控制柜指示状态和运行声音，并形成巡检记录。",
        "发现压力异常、明显异响或渗漏时，应记录异常并通知工程负责人处理。",
        "设备处于检修隔离状态时，不执行常规试运行，按照检修作业要求处理。",
    ],
    "elevator": [
        "住宅项目接到电梯困人报警后，物业值班人员应保持通话安抚被困人员，并立即联系电梯维保单位。",
        "现场应设置必要警戒；如存在人员受伤、轿厢异常移动等情况，应升级应急流程。",
        "未经专业维保人员到场确认，物业人员不得强行撬门救人。",
    ],
    "visitor": [
        "未预约访客到访时，门岗应核验身份证件，并联系被访人确认后方可放行。",
        "无法联系被访人或身份信息无法核验时，不得放行，并做好来访登记。",
        "已经完成预约且信息匹配的访客，可按项目既定通行流程办理。",
    ],
    "firepump": [
        "消防水泵按例行计划进行试运行时，应观察压力、运行声音和振动情况并做好记录。",
        "试运行过程中发现异常振动、异响或压力异常时，应立即停止试运行并报修。",
        "故障未排除前不得重复强行启动，恢复运行需按设备管理要求确认。",
    ],
}


def _pack_units(ranked, budget=CONTEXT_BUDGET):
    selected, used = [], 0
    for unit in ranked[:TOP_K]:
        text = unit["text"]
        remaining = budget - used
        if remaining <= 0:
            break
        clipped = text[:remaining]
        selected.append({**unit, "text": clipped})
        used += len(clipped)
    return selected


def _build_fixed_chunks(blocks, chunk_size=120, overlap=20):
    units = []
    by_doc = {}
    for block in blocks:
        by_doc.setdefault(block["document_id"], []).append(block)
    for doc_id, doc_blocks in by_doc.items():
        doc_blocks.sort(key=lambda x: x["block_index"])
        pieces, spans, cursor = [], [], 0
        for block in doc_blocks:
            if pieces:
                pieces.append("\n")
                cursor += 1
            text = block["text_content"]
            start = cursor
            pieces.append(text)
            cursor += len(text)
            spans.append((start, cursor, block["id"]))
        joined = "".join(pieces)
        step = max(1, chunk_size - overlap)
        for index, start in enumerate(range(0, len(joined), step), start=1):
            end = min(len(joined), start + chunk_size)
            text = joined[start:end]
            if not text:
                continue
            anchors = [block_id for left, right, block_id in spans if left < end and right > start]
            units.append({
                "id": f"{doc_id}:chunk:{index}",
                "document_id": doc_id,
                "text": text,
                "anchors": anchors,
            })
    return units


def _build_structure_units(blocks):
    return [
        {
            "id": block["id"],
            "document_id": block["document_id"],
            "text": f"{block['heading_path'] or ''}\n{block['text_content']}",
            "anchors": [block["id"]],
        }
        for block in blocks
    ]


def _rank_units(units, vectors, query):
    query_vector = embed_query(query)
    keyword = []
    dense = []
    for unit, vector in zip(units, vectors):
        kw = _keyword_score(query, unit["text"])
        if kw > 0:
            keyword.append((unit, kw))
        sim = cosine_similarity(query_vector, vector)
        if sim >= RETRIEVAL_DENSE_MIN_SCORE:
            dense.append((unit, sim))

    keyword.sort(key=lambda x: x[1], reverse=True)
    dense.sort(key=lambda x: x[1], reverse=True)
    kw_rank = {row[0]["id"]: i for i, row in enumerate(keyword, start=1)}
    de_rank = {row[0]["id"]: i for i, row in enumerate(dense, start=1)}
    by_id = {unit["id"]: unit for unit in units}
    candidate_ids = set(kw_rank) | set(de_rank)
    fused = []
    for unit_id in candidate_ids:
        score = 0.0
        if unit_id in kw_rank:
            score += 1.0 / (RETRIEVAL_RRF_K + kw_rank[unit_id])
        if unit_id in de_rank:
            score += 1.0 / (RETRIEVAL_RRF_K + de_rank[unit_id])
        fused.append((by_id[unit_id], score))
    fused.sort(key=lambda x: x[1], reverse=True)
    return [row[0] for row in fused]


def _context_text(units):
    return "\n".join(unit["text"] for unit in units)


def _atom_context(result):
    parts = [
        result.get("title") or "",
        result.get("statement") or "",
        "；".join(result.get("conditions") or []),
        "；".join(result.get("actions") or []),
        "；".join(result.get("exceptions") or []),
    ]
    parts.extend(e.get("excerpt") or "" for e in result.get("evidence") or [])
    return "\n".join(part for part in parts if part)


class TestStage4EBenchmarkSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_env_helper import setup_test_db
        cls.test_db = setup_test_db("stage4e_benchmark")
        cls.client = stage4b.TestStage4BHybridRetrieval.client if hasattr(stage4b.TestStage4BHybridRetrieval, "client") else None
        from fastapi.testclient import TestClient
        from main import app
        cls.client = TestClient(app)

        admin = cls.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Admin@Zhixing2026"},
        )
        assert admin.status_code == 200, admin.text
        cls.admin_headers = {"Authorization": f"Bearer {admin.json()['token']}"}
        cls.admin_user = admin.json()["user"]
        cls.org_id = cls.admin_user["organization_id"]

        cls.helper = stage4b.TestStage4BHybridRetrieval(methodName="test_01_real_embedding_persistence_async_and_idempotency")
        cls.helper.client = cls.client
        cls.helper.admin_headers = cls.admin_headers
        cls.helper.admin_user = cls.admin_user
        cls.helper.org_id = cls.org_id
        cls.helper.prefix = "s4e"

        cls.ids = {}
        for key, title in [
            ("water", "二次供水泵房巡检制度"),
            ("elevator", "电梯困人应急处置规程"),
            ("visitor", "访客通行管理制度"),
            ("firepump", "消防水泵试运行规程"),
        ]:
            cls._create_fixture(key, title)
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT sb.id, sb.block_index, sb.heading_path, sb.text_content,
                       dv.document_id
                FROM source_blocks sb
                JOIN document_versions dv ON sb.document_version_id = dv.id
                WHERE dv.document_id IN ({})
                ORDER BY dv.document_id, sb.block_index
                """.format(",".join("?" for _ in cls.ids)),
                [cls.ids[key]["doc_id"] for key in cls.ids],
            ).fetchall()
        cls.blocks = [dict(row) for row in rows]
        cls.fixed_units = _build_fixed_chunks(cls.blocks)
        cls.structure_units = _build_structure_units(cls.blocks)
        cls.fixed_vectors = embed_texts(unit["text"] for unit in cls.fixed_units)
        cls.structure_vectors = embed_texts(unit["text"] for unit in cls.structure_units)

    @classmethod
    def tearDownClass(cls):
        from test_env_helper import cleanup_test_db
        cleanup_test_db(cls.test_db)

    @classmethod
    def _create_fixture(cls, key, title):
        cls.helper.prefix = f"s4e_{key}"
        doc_id, doc_ver, block_ids = cls.helper._create_document(title)
        texts = SOURCE_TEXT[key]
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            for index, block_id in enumerate(block_ids):
                text = texts[min(index, len(texts) - 1)]
                conn.execute(
                    """
                    UPDATE source_blocks
                    SET text_content=?, heading_path=?, paragraph_anchor=?
                    WHERE id=?
                    """,
                    (text, title, f"[line_{index + 1}]", block_id),
                )

        if key == "water":
            statement = "二次供水泵房应每日检查压力表、控制柜指示状态和运行声音，并形成巡检记录。"
            conditions = ["每日例行巡检"]
            actions = ["检查压力表和控制柜", "听取运行声音", "形成巡检记录"]
            exceptions = ["设备处于检修隔离状态时不执行常规试运行"]
        elif key == "elevator":
            statement = "接到电梯困人报警后应安抚被困人员并立即联系电梯维保单位。"
            conditions = ["发生电梯困人报警"]
            actions = ["保持通话安抚", "联系电梯维保单位", "设置现场警戒"]
            exceptions = ["未经专业维保人员到场确认不得强行撬门救人"]
        elif key == "visitor":
            statement = "未预约访客应核验身份证件并联系被访人确认后方可放行。"
            conditions = ["访客未提前预约"]
            actions = ["核验身份证件", "联系被访人确认", "做好来访登记"]
            exceptions = ["无法确认被访人或身份信息时不得放行"]
        else:
            statement = "消防水泵试运行时应观察压力、运行声音和振动情况并做好记录。"
            conditions = ["按例行计划进行消防水泵试运行"]
            actions = ["观察压力", "观察运行声音和振动", "做好记录"]
            exceptions = ["发现异常振动、异响或压力异常时立即停止试运行并报修"]

        item_id, version_id, _ = cls.helper._create_knowledge(
            doc_id,
            doc_ver,
            block_ids,
            title,
            statement,
            conditions=conditions,
            actions=actions,
            exceptions=exceptions,
            problem_tags=[title.replace("制度", "").replace("规程", "")],
            business_scenes=["设施管理" if key != "visitor" else "门岗管理"],
        )
        cls.helper._confirm_and_wait(item_id, version_id)
        cls.ids[key] = {
            "doc_id": doc_id,
            "doc_ver": doc_ver,
            "item_id": item_id,
            "version_id": version_id,
        }

    def _evaluate_baseline(self, units, vectors, case):
        started = time.perf_counter()
        ranked = _rank_units(units, vectors, case["query"])
        selected = _pack_units(ranked)
        elapsed_ms = (time.perf_counter() - started) * 1000
        context = _context_text(selected)
        target_doc = self.ids[case["target"]]["doc_id"] if case["target"] else None
        selected_docs = {unit["document_id"] for unit in selected}
        hit = (target_doc in selected_docs) if target_doc else (len(selected) == 0)
        complete = all(term in context for term in case["required"])
        return {
            "hit": hit,
            "complete": complete,
            "result_count": len(selected),
            "context_chars": len(context),
            "source_anchor_count": len({a for unit in selected for a in unit["anchors"]}),
            "latency_ms": round(elapsed_ms, 3),
        }

    def _evaluate_atom(self, case):
        started = time.perf_counter()
        with get_db() as conn:
            results = hybrid_search(
                conn,
                self.admin_user,
                case["query"],
                datetime.now(timezone.utc).isoformat(),
            )
        elapsed_ms = (time.perf_counter() - started) * 1000
        selected = results[:TOP_K]
        context_parts = []
        used = 0
        for result in selected:
            text = _atom_context(result)
            remaining = CONTEXT_BUDGET - used
            if remaining <= 0:
                break
            context_parts.append(text[:remaining])
            used += min(len(text), remaining)
        context = "\n".join(context_parts)
        target_version = self.ids[case["target"]]["version_id"] if case["target"] else None
        selected_versions = {result["version_id"] for result in selected}
        hit = (target_version in selected_versions) if target_version else (len(selected) == 0)
        complete = all(term in context for term in case["required"])
        anchors = {
            evidence.get("source_block_id")
            for result in selected
            for evidence in result.get("evidence") or []
            if evidence.get("source_block_id")
        }
        return {
            "hit": hit,
            "complete": complete,
            "result_count": len(selected),
            "context_chars": len(context),
            "source_anchor_count": len(anchors),
            "latency_ms": round(elapsed_ms, 3),
        }

    def test_01_fixed_human_labeled_query_set(self):
        rows = []
        for case in BENCHMARK_CASES:
            row = {
                "case": case["id"],
                "A_fixed_chunks": self._evaluate_baseline(self.fixed_units, self.fixed_vectors, case),
                "B_structure_blocks": self._evaluate_baseline(self.structure_units, self.structure_vectors, case),
                "C_atoms_evidence": self._evaluate_atom(case),
            }
            rows.append(row)

        positives = [row for row, case in zip(rows, BENCHMARK_CASES) if case["target"]]
        for method in ("A_fixed_chunks", "B_structure_blocks", "C_atoms_evidence"):
            self.assertGreaterEqual(sum(1 for row in positives if row[method]["hit"]), 5)
        no_result = rows[-1]
        self.assertTrue(no_result["C_atoms_evidence"]["hit"])
        print("[Stage4E][benchmark] " + json.dumps(rows, ensure_ascii=False))

    def test_02_reproducible_summary_and_runtime_info(self):
        runtime = get_embedding_runtime_info()
        self.assertEqual(runtime["embedding_dim"], 512)
        summaries = {}
        for method, units in (
            ("A_fixed_chunks", self.fixed_units),
            ("B_structure_blocks", self.structure_units),
        ):
            summaries[method] = {
                "representation_units": len(units),
                "representation_chars": sum(len(unit["text"]) for unit in units),
            }
        with get_db() as conn:
            atom_units = conn.execute(
                "SELECT COUNT(*) FROM retrieval_records WHERE knowledge_version_id IN ({})".format(
                    ",".join("?" for _ in self.ids)
                ),
                [self.ids[key]["version_id"] for key in self.ids],
            ).fetchone()[0]
        summaries["C_atoms_evidence"] = {
            "representation_units": atom_units,
            "manual_review_cost": "未自动测量；该方案要求管理员确认知识原子与证据",
        }
        print("[Stage4E][runtime] " + json.dumps(runtime, ensure_ascii=False))
        print("[Stage4E][summary] " + json.dumps(summaries, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
