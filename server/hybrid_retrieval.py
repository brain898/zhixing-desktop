"""Stage 4B：关键词 + Dense 中文向量的可复现混合检索。"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Sequence

from config import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL_NAME,
    RETRIEVAL_CANDIDATE_LIMIT,
    RETRIEVAL_CONFIG_VERSION,
    RETRIEVAL_DENSE_MIN_SCORE,
    RETRIEVAL_INDEX_VERSION,
    RETRIEVAL_RRF_K,
)
from eligibility import build_eligibility_sql, check_knowledge_eligibility
from embedding_service import cosine_similarity, embed_query


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def get_retrieval_config() -> Dict[str, Any]:
    return {
        "config_version": RETRIEVAL_CONFIG_VERSION,
        "index_version": RETRIEVAL_INDEX_VERSION,
        "model_name": EMBEDDING_MODEL_NAME,
        "embedding_dim": EMBEDDING_DIM,
        "normalize_embeddings": True,
        "similarity": "cosine",
        "fusion": "rrf",
        "rrf_k": RETRIEVAL_RRF_K,
        "dense_min_score": RETRIEVAL_DENSE_MIN_SCORE,
        "fragment_schema": "knowledge-atom-fragments-v1",
    }


def get_retrieval_config_hash() -> str:
    payload = _stable_json(get_retrieval_config()).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _loads(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _flatten_json(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if item not in (None, "", [], {}):
                parts.append(f"{key}:{_flatten_json(item)}")
        return "；".join(parts)
    if isinstance(value, list):
        return "；".join(_flatten_json(item) for item in value if item not in (None, ""))
    return str(value or "").strip()


def _evidence_ids(evidence_rows: Sequence[Any], field_keywords: Sequence[str]) -> List[str]:
    wanted = [x.lower() for x in field_keywords]
    matched = []
    for row in evidence_rows:
        field_name = str(row["field_name"] or "").lower()
        if any(word in field_name for word in wanted):
            matched.append(row["id"])
    return matched


def build_retrieval_fragments(row: Any, evidence_rows: Sequence[Any]) -> List[Dict[str, Any]]:
    """把一个知识版本拆为多个可重建检索片段；正文仍以 knowledge_versions 为权威。"""
    conditions = _loads(row["conditions_json"], [])
    actions = _loads(row["actions_json"], [])
    exceptions = _loads(row["exceptions_json"], [])
    metric = _loads(row["metric_definition_json"], {})
    case = _loads(row["case_details_json"], {})
    customer_types = _loads(row["customer_types_json"], [])
    business_scenes = _loads(row["business_scenes_json"], [])
    problem_tags = _loads(row["problem_tags_json"], [])
    fragments: List[Dict[str, Any]] = []

    def add_fragment(key: str, fragment_type: str, parts: Iterable[str], fields: Sequence[str]):
        cleaned = [str(part).strip() for part in parts if str(part or "").strip()]
        if not cleaned:
            return
        text = "\n".join(cleaned)
        fragments.append({
            "fragment_key": key,
            "fragment_type": fragment_type,
            "search_text": text,
            "evidence_ids": _evidence_ids(evidence_rows, fields),
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })

    add_fragment(
        "core",
        "core",
        [
            f"【标题】{row['title']}",
            f"【分类】{row['primary_category'] or ''}",
            f"【主体】{row['subject'] or ''}",
            f"【核心陈述】{row['statement'] or ''}",
            f"【客户类型】{' '.join(customer_types)}",
            f"【业务场景】{' '.join(business_scenes)}",
            f"【问题标签】{' '.join(problem_tags)}",
        ],
        ("title", "statement", "subject"),
    )
    if row["content"] and row["content"] != row["statement"]:
        add_fragment(
            "content",
            "content",
            [f"【正文】{row['content']}"],
            ("content", "statement"),
        )

    add_fragment(
        "conditions_actions",
        "conditions_actions",
        [
            f"【适用条件】{_flatten_json(conditions)}" if conditions else "",
            f"【执行动作】{_flatten_json(actions)}" if actions else "",
        ],
        ("condition", "action"),
    )
    add_fragment(
        "exceptions",
        "exceptions",
        [f"【例外/停止条件】{_flatten_json(exceptions)}"] if exceptions else [],
        ("exception", "stop", "禁止"),
    )
    add_fragment(
        "metric",
        "metric",
        [f"【指标】{_flatten_json(metric)}"] if metric else [],
        ("metric", "指标"),
    )
    add_fragment(
        "case",
        "case",
        [f"【案例】{_flatten_json(case)}"] if case else [],
        ("case", "案例"),
    )
    add_fragment(
        "source",
        "source",
        [
            f"【来源资料】{row['doc_title'] or ''}",
            f"【来源文件】{row['file_name'] or ''}",
            f"【文件版本】{row['document_version_label'] or ''}",
        ],
        (),
    )

    if not fragments:
        raise ValueError(f"知识版本 {row['id']} 无可索引文本")
    return fragments


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _query_terms(query: str) -> List[str]:
    query = str(query or "").strip()
    if not query:
        return []
    terms = [query]
    terms.extend(
        token.strip()
        for token in re.split(r"[\s,，;；。！？!?、:：()（）\[\]【】]+", query)
        if len(token.strip()) >= 2
    )
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", query))
    if 5 <= len(cjk) <= 40:
        terms.extend(cjk[i:i + 4] for i in range(0, len(cjk) - 3))
    deduped = []
    seen = set()
    for term in terms:
        norm = _normalize_text(term)
        if len(norm) < 2 or norm in seen:
            continue
        seen.add(norm)
        deduped.append(term)
    return deduped


def _keyword_score(query: str, search_text: str) -> float:
    q = _normalize_text(query)
    text = _normalize_text(search_text)
    if not q or not text:
        return 0.0

    score = 0.0
    if q in text:
        score += 200.0 + min(len(q), 30)
    for term in _query_terms(query):
        norm = _normalize_text(term)
        if norm == q:
            continue
        if norm in text:
            score += 8.0 + min(len(norm), 8)
    return score


def _make_snippet(text: str, query: str) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    lowered = compact.lower()
    positions = []
    for term in _query_terms(query):
        idx = lowered.find(str(term).lower())
        if idx >= 0:
            positions.append(idx)
    if not positions:
        return compact[:180] + ("..." if len(compact) > 180 else "")
    idx = min(positions)
    start = max(0, idx - 45)
    end = min(len(compact), idx + 135)
    prefix = "..." if start else ""
    suffix = "..." if end < len(compact) else ""
    return prefix + compact[start:end] + suffix


def _chunked(values: Sequence[str], size: int = 800):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _load_records(conn, version_ids: Sequence[str], config_hash: str) -> List[Any]:
    rows: List[Any] = []
    for batch in _chunked(list(version_ids)):
        placeholders = ",".join("?" for _ in batch)
        rows.extend(conn.execute(
            f"""
            SELECT id, knowledge_version_id, fragment_key, fragment_type, search_text,
                   vector_json, embedding_dim, evidence_ids_json, model_name, config_hash
            FROM retrieval_records
            WHERE knowledge_version_id IN ({placeholders}) AND config_hash = ?
            """,
            [*batch, config_hash],
        ).fetchall())
    return rows


def load_evidence(conn, version_ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {vid: [] for vid in version_ids}
    for batch in _chunked(list(version_ids)):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT ke.id, ke.knowledge_version_id, ke.source_block_id, ke.field_name,
                   ke.excerpt, ke.accuracy_level,
                   sb.block_index, sb.block_type, sb.heading_path, sb.page_number, sb.paragraph_anchor
            FROM knowledge_evidence ke
            LEFT JOIN source_blocks sb ON sb.id = ke.source_block_id
            WHERE ke.knowledge_version_id IN ({placeholders})
            ORDER BY ke.knowledge_version_id, ke.created_at, ke.id
            """,
            list(batch),
        ).fetchall()
        for row in rows:
            grouped.setdefault(row["knowledge_version_id"], []).append({
                "id": row["id"],
                "source_block_id": row["source_block_id"],
                "field_name": row["field_name"],
                "excerpt": row["excerpt"],
                "accuracy_level": row["accuracy_level"],
                "source_locator": {
                    "block_index": row["block_index"],
                    "block_type": row["block_type"],
                    "heading_path": row["heading_path"],
                    "page_number": row["page_number"],
                    "paragraph_anchor": row["paragraph_anchor"],
                },
            })
    return grouped


# Keep the previous private name for existing callers; the query and result are unchanged.
_load_evidence = load_evidence


def _normalize_filter_values(value: str | Sequence[str] | None) -> List[str]:
    """兼容旧单值调用，并将同一筛选维度统一为 OR 值集合。"""
    if value is None:
        return []
    raw_values = [value] if isinstance(value, str) else list(value)
    return [str(item).strip() for item in raw_values if str(item).strip()]


def hybrid_search(
    conn,
    current_user: Dict[str, Any],
    query: str,
    now_iso: str,
    document_id: str | None = None,
    category: str | Sequence[str] | None = None,
    customer_type: str | Sequence[str] | None = None,
    business_scene: str | Sequence[str] | None = None,
    problem_tag: str | Sequence[str] | None = None,
) -> List[Dict[str, Any]]:
    """严格先做 Stage 4A eligibility，再在 eligible 范围内执行关键词与向量召回。"""
    categories = _normalize_filter_values(category)
    customer_types = _normalize_filter_values(customer_type)
    business_scenes = _normalize_filter_values(business_scene)
    problem_tags = _normalize_filter_values(problem_tag)

    where, params = build_eligibility_sql(current_user, now_iso)
    if document_id:
        where.append("ki.document_id = ?")
        params.append(document_id)
    if categories:
        placeholders = ",".join("?" for _ in categories)
        where.append(f"kv.primary_category IN ({placeholders})")
        params.extend(categories)

    rows = conn.execute(
        f"""
        SELECT ki.id AS item_id, ki.document_id, ki.access_scope, ki.lifecycle_status,
               d.title AS document_title,
               dv.id AS document_version_id, dv.version_label AS document_version_label,
               dv.file_name,
               kv.*
        FROM knowledge_versions kv
        JOIN knowledge_items ki ON kv.item_id = ki.id
        JOIN documents d ON ki.document_id = d.id
        JOIN document_versions dv ON kv.source_document_version_id = dv.id
        WHERE {' AND '.join(where)}
        """,
        params,
    ).fetchall()

    eligible: Dict[str, Any] = {}
    for row in rows:
        c_types = _loads(row["customer_types_json"], [])
        b_scenes = _loads(row["business_scenes_json"], [])
        p_tags = _loads(row["problem_tags_json"], [])
        if customer_types and not any(value in c_types for value in customer_types):
            continue
        if business_scenes and not any(value in b_scenes for value in business_scenes):
            continue
        if problem_tags and not any(value in p_tags for value in problem_tags):
            continue
        eligible[row["id"]] = row

    if not eligible:
        return []

    config_hash = get_retrieval_config_hash()
    records = _load_records(conn, list(eligible), config_hash)
    if not records:
        return []

    keyword_best: Dict[str, tuple] = {}
    for record in records:
        score = _keyword_score(query, record["search_text"])
        vid = record["knowledge_version_id"]
        if score > 0 and (vid not in keyword_best or score > keyword_best[vid][0]):
            keyword_best[vid] = (score, record)

    keyword_ranked = sorted(keyword_best.items(), key=lambda item: item[1][0], reverse=True)
    keyword_rank = {
        vid: (rank, data[1])
        for rank, (vid, data) in enumerate(keyword_ranked[:RETRIEVAL_CANDIDATE_LIMIT], start=1)
    }

    query_vector = embed_query(query)
    dense_best: Dict[str, tuple] = {}
    for record in records:
        if record["model_name"] != EMBEDDING_MODEL_NAME:
            continue
        if int(record["embedding_dim"] or 0) != EMBEDDING_DIM or not record["vector_json"]:
            continue
        vector = _loads(record["vector_json"], [])
        if len(vector) != EMBEDDING_DIM:
            continue
        similarity = cosine_similarity(query_vector, vector)
        vid = record["knowledge_version_id"]
        if similarity >= RETRIEVAL_DENSE_MIN_SCORE and (
            vid not in dense_best or similarity > dense_best[vid][0]
        ):
            dense_best[vid] = (similarity, record)

    dense_ranked = sorted(dense_best.items(), key=lambda item: item[1][0], reverse=True)
    dense_rank = {
        vid: (rank, data[1], data[0])
        for rank, (vid, data) in enumerate(dense_ranked[:RETRIEVAL_CANDIDATE_LIMIT], start=1)
    }

    candidate_ids = set(keyword_rank) | set(dense_rank)
    fused = []
    for vid in candidate_ids:
        relevance = 0.0
        if vid in keyword_rank:
            relevance += 1.0 / (RETRIEVAL_RRF_K + keyword_rank[vid][0])
        if vid in dense_rank:
            relevance += 1.0 / (RETRIEVAL_RRF_K + dense_rank[vid][0])
        fused.append((vid, relevance))
    fused.sort(key=lambda item: item[1], reverse=True)
    fused = fused[:RETRIEVAL_CANDIDATE_LIMIT]

    result_ids = [vid for vid, _ in fused]
    evidence_by_version = load_evidence(conn, result_ids)
    output: List[Dict[str, Any]] = []
    for vid, relevance in fused:
        eligibility = check_knowledge_eligibility(conn, vid, current_user, now_iso)
        if not eligibility.is_eligible:
            continue
        row = eligible.get(vid)
        if row is None:
            continue

        matched = []
        chosen = []
        if vid in keyword_rank:
            rank, record = keyword_rank[vid]
            chosen.append(("keyword", record, rank, None))
        if vid in dense_rank:
            rank, record, similarity = dense_rank[vid]
            chosen.append(("dense", record, rank, similarity))
        seen_records = set()
        for channel, record, rank, similarity in chosen:
            if record["id"] in seen_records:
                for item in matched:
                    if item["fragment_key"] == record["fragment_key"]:
                        item["channels"].append(channel)
                        if similarity is not None:
                            item["dense_similarity"] = round(similarity, 6)
                continue
            seen_records.add(record["id"])
            matched.append({
                "fragment_key": record["fragment_key"],
                "fragment_type": record["fragment_type"],
                "channels": [channel],
                "rank": rank,
                "dense_similarity": round(similarity, 6) if similarity is not None else None,
                "snippet": _make_snippet(record["search_text"], query),
                "evidence_ids": _loads(record["evidence_ids_json"], []),
            })

        output.append({
            "item_id": row["item_id"],
            "version_id": row["id"],
            "version_number": row["version_number"],
            "title": row["title"],
            "primary_category": row["primary_category"],
            "atom_type": row["atom_type"],
            "subject": row["subject"],
            "statement": row["statement"],
            "content": row["content"],
            "document_id": row["document_id"],
            "document_title": row["document_title"],
            "document_version_label": row["document_version_label"],
            "conditions": _loads(row["conditions_json"], []),
            "actions": _loads(row["actions_json"], []),
            "exceptions": _loads(row["exceptions_json"], []),
            "metric_definition": _loads(row["metric_definition_json"], {}),
            "case_details": _loads(row["case_details_json"], {}),
            "customer_types": _loads(row["customer_types_json"], []),
            "business_scenes": _loads(row["business_scenes_json"], []),
            "problem_tags": _loads(row["problem_tags_json"], []),
            "valid_from": row["valid_from"],
            "valid_until": row["valid_until"],
            "access_scope": row["access_scope"],
            "lifecycle_status": row["lifecycle_status"],
            "source": {
                "document_id": row["document_id"],
                "document_title": row["document_title"],
                "document_version_id": row["document_version_id"],
                "document_version_label": row["document_version_label"],
                "file_name": row["file_name"],
                "source_anchors": _loads(row["source_anchors_json"], []),
            },
            "evidence": evidence_by_version.get(vid, []),
            "evidence_count": len(evidence_by_version.get(vid, [])),
            "matched_fragments": matched,
            "matched_snippets": [item["snippet"] for item in matched[:3]],
            "relevance_score": round(relevance, 8),
            "score": round(relevance, 8),
            "score_type": "rrf_relevance",
            "score_explanation": "仅表示查询相关性，不表示事实可信度",
        })
    return output
