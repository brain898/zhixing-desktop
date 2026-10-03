"""Persistent, metadata-validated recall summaries for the Skill factory entry."""

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import config
import scene_catalog as sc
from eligibility import build_eligibility_sql
from hybrid_retrieval import get_retrieval_config_hash
from m02_common import dumps, loads, now_iso as current_time

FAILURE_RETRY_SECONDS = 30
CACHE_VERSION = 1
SUMMARY_FIELDS = (
    "available_atom_count", "category_coverage", "recall_stats", "semantic_error",
    "can_generate", "generate_blocked_reason", "min_atoms_for_generation",
)
EMPTY_COUNTS = {"pending_review": 0, "approved": 0, "needs_recheck": 0, "total": 0}
logger = logging.getLogger(__name__)
_refresh_lock = threading.Lock()
_refreshes = {}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def knowledge_signature(conn, user, now_iso):
    """Read IDs/status/version metadata only, never text bodies or vectors."""
    clauses, params = build_eligibility_sql(user, now_iso)
    joins = """FROM knowledge_versions kv
        JOIN knowledge_items ki ON ki.id = kv.item_id
        JOIN documents d ON d.id = ki.document_id
        JOIN document_versions dv ON dv.id = kv.source_document_version_id"""
    where = " AND ".join(clauses)
    atoms = conn.execute(f"""SELECT kv.id, kv.revision_token, kv.index_status, kv.title, kv.created_at,
        kv.valid_from, kv.valid_until, kv.primary_category, kv.atom_type, kv.business_scenes_json,
        ki.id, ki.active_version_id, ki.access_scope, ki.lifecycle_status, ki.is_excluded, ki.deleted_at,
        d.id, d.active_version_id, d.access_scope, d.is_deleted, dv.id, dv.content_hash
        {joins} WHERE {where} ORDER BY kv.id""", params).fetchall()
    records = conn.execute(f"""SELECT rr.id, rr.knowledge_version_id, rr.fragment_key,
        rr.fragment_type, rr.model_name, rr.index_version, rr.embedding_dim,
        rr.content_hash, rr.config_hash, rr.created_at, (rr.vector_json IS NOT NULL)
        {joins} JOIN retrieval_records rr ON rr.knowledge_version_id = kv.id
        WHERE {where} ORDER BY rr.id""", params).fetchall()
    retrieval = (get_retrieval_config_hash(), config.RETRIEVAL_CANDIDATE_LIMIT,
                 sc.SCENE_RECALL_POOL_LIMIT, sc.SCENE_SEMANTIC_TOP_N,
                 sc.SCENE_SEMANTIC_RESERVED, sc.SCENE_MIN_ATOMS_FOR_GENERATION)
    return _hash([CACHE_VERSION, user.get("account_status"), retrieval,
                  [list(row) for row in atoms], [list(row) for row in records]])


def _scene_signature(knowledge, scene):
    return _hash([knowledge, scene["scene_id"], scene.get("revision_token"), scene["name"],
                  scene.get("description"), scene.get("aliases"), scene.get("typical_problems"), scene.get("status")])


def get_scene_cards(conn, user, scenes, now_iso, force_refresh=False):
    """Reuse stable recall while keeping Skill counts and caller's batch state live."""
    if not scenes:
        return []
    org_id, role = user["organization_id"], user["role"]
    knowledge = knowledge_signature(conn, user, now_iso)
    signatures = {s["scene_id"]: _scene_signature(knowledge, s) for s in scenes}
    cached = {row["scene_id"]: row for row in conn.execute(
        "SELECT * FROM scene_card_cache WHERE organization_id = ? AND role = ?", (org_id, role),
    ).fetchall()}
    summaries, missing = {}, []
    for scene in scenes:
        row = cached.get(scene["scene_id"])
        summary = loads(row["summary_json"], None) if row else None
        if (not force_refresh and row and row["input_signature"] == signatures[scene["scene_id"]]
                and (not row["retry_after"] or now_iso < row["retry_after"])
                and isinstance(summary, dict) and all(key in summary for key in SUMMARY_FIELDS)):
            summaries[scene["scene_id"]] = summary
        else:
            missing.append(scene)
    counts = sc.scene_skill_counts(conn, org_id)
    if missing:
        # All expensive work happens before the first cache write/SQLite write lock.
        context = sc.build_scene_card_context(conn, user, now_iso, skill_counts=counts)
        for scene in missing:
            card = sc.build_scene_card(conn, user, scene, now_iso, context=context)
            summaries[scene["scene_id"]] = {key: card[key] for key in SUMMARY_FIELDS}
        # A concurrent knowledge change must not stamp the old result with fresh inputs.
        if knowledge_signature(conn, user, now_iso) == knowledge:
            current = {s["scene_id"]: s for s in sc.list_scenes(conn, org_id)}
            for scene in missing:
                sid = scene["scene_id"]
                if sid not in current or _scene_signature(knowledge, current[sid]) != signatures[sid]:
                    continue
                summary = summaries[sid]
                retry = ((datetime.fromisoformat(now_iso) + timedelta(seconds=FAILURE_RETRY_SECONDS)).isoformat()
                         if summary["semantic_error"] else None)
                conn.execute("""INSERT INTO scene_card_cache
                    (organization_id, role, scene_id, input_signature, summary_json, computed_at, retry_after)
                    SELECT ?, ?, ?, ?, ?, ?, ?
                    WHERE EXISTS (SELECT 1 FROM scenes WHERE id = ? AND organization_id = ?)
                    ON CONFLICT(organization_id, role, scene_id) DO UPDATE SET
                    input_signature = excluded.input_signature, summary_json = excluded.summary_json,
                    computed_at = excluded.computed_at, retry_after = excluded.retry_after""",
                    (org_id, role, sid, signatures[sid], dumps(summary), now_iso, retry, sid, org_id))
    return [{**scene, **summaries[scene["scene_id"]],
             "skill_counts": counts.get(scene["scene_id"], dict(EMPTY_COUNTS))} for scene in scenes]


def _refresh_scene_cards(database_path, user, state, key, force_refresh):
    """Use the captured database, never the mutable process-wide fixture/data path."""
    error = None
    conn = None
    try:
        conn = sqlite3.connect(Path(database_path).as_uri() + "?mode=rw", uri=True, timeout=20.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        scenes = sc.list_scenes(conn, user["organization_id"], status="active")
        get_scene_cards(conn, user, scenes, current_time(), force_refresh=force_refresh)
        conn.commit()
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        error = f"统计刷新失败：{type(exc).__name__}：{str(exc)[:160]}"
        logger.exception("Scene card background refresh failed")
    finally:
        if conn is not None:
            conn.close()
        with _refresh_lock:
            if _refreshes.get(key) is state:
                if error:
                    state.update(running=False, error=error,
                                 retry_at=time.monotonic() + FAILURE_RETRY_SECONDS)
                else:
                    _refreshes.pop(key, None)


def _empty_summary():
    return {
        "available_atom_count": 0, "category_coverage": {c: 0 for c in sc.PRIMARY_CATEGORIES},
        "recall_stats": {}, "semantic_error": None, "can_generate": False,
        "generate_blocked_reason": "可用知识统计更新中，请稍候",
        "min_atoms_for_generation": sc.SCENE_MIN_ATOMS_FOR_GENERATION,
    }


def get_scene_cards_async(conn, user, scenes, now_iso, force_refresh=False):
    """Return cached/placeholder summaries immediately, scheduling a single refresh."""
    if not scenes:
        return []
    database_path = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    if not database_path:
        raise ValueError("后台统计需要独立数据库文件")
    database_path = str(Path(database_path).resolve())
    key = (os.path.normcase(database_path), user["organization_id"], user["role"])
    knowledge = knowledge_signature(conn, user, now_iso)
    signatures = {s["scene_id"]: _scene_signature(knowledge, s) for s in scenes}
    signature = _hash(sorted(signatures.items()))
    cached = {row["scene_id"]: row for row in conn.execute(
        "SELECT * FROM scene_card_cache WHERE organization_id = ? AND role = ?", key[1:],
    ).fetchall()}
    summaries, missing = {}, set()
    for scene in scenes:
        sid = scene["scene_id"]
        row = cached.get(sid)
        summary = loads(row["summary_json"], None) if row else None
        has_summary = isinstance(summary, dict) and all(field in summary for field in SUMMARY_FIELDS)
        summaries[sid] = summary if has_summary else _empty_summary()
        if (force_refresh or not has_summary or row["input_signature"] != signatures[sid]
                or (row["retry_after"] and now_iso >= row["retry_after"])):
            missing.add(sid)
    counts = sc.scene_skill_counts(conn, user["organization_id"])
    error, error_scenes, pending = None, set(), set(missing)
    with _refresh_lock:
        state = _refreshes.get(key)
        if state and state["running"]:
            pending.update(state["scene_ids"])
        elif (state and state.get("signature") == signature and state.get("error")
              and time.monotonic() < state["retry_at"] and not force_refresh):
            error = state["error"]
            error_scenes = set(state["scene_ids"])
            pending.clear()
        elif missing:
            state = {"running": True, "signature": signature, "scene_ids": set(missing)}
            _refreshes[key] = state
            try:
                from tasks import submit_background
                submit_background(_refresh_scene_cards, database_path, dict(user), state, key, force_refresh)
            except Exception as exc:
                error = f"统计刷新失败：{type(exc).__name__}：{str(exc)[:160]}"
                error_scenes = set(missing)
                state.update(running=False, error=error, retry_at=time.monotonic() + FAILURE_RETRY_SECONDS)
                pending.clear()
        elif state:
            _refreshes.pop(key, None)
    cards = []
    for scene in scenes:
        sid = scene["scene_id"]
        card = {**scene, **summaries[sid], "skill_counts": counts.get(sid, dict(EMPTY_COUNTS)),
                "statistics_pending": sid in pending}
        if card["statistics_pending"]:
            card.update(can_generate=False, generate_blocked_reason="可用知识统计更新中，请稍候")
        elif error and sid in error_scenes:
            card.update(can_generate=False, semantic_error=error, generate_blocked_reason="可用知识统计失败，请重试")
        cards.append(card)
    return cards
