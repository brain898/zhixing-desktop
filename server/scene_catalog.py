"""
知行有策 - M02 场景目录与原子召回（PRD 第 5 章 FR01~FR03、FR04 原子召回）

- 场景目录只维护 scenes / scene_pending_tags / scene_merge_suggestions 三张表，
  全程不修改知识原子，也不产生原子新版本（FR01.4）。
- 可引用资格一律调用 eligibility.py（R1），语义召回复用 hybrid_retrieval.hybrid_search。
- 归并建议的模型调用复用 deepseek_extractor.post_chat_completion 的请求封装与配置。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from deepseek_extractor import post_chat_completion
from eligibility import build_eligibility_sql, filter_eligible_version_ids
from m02_common import dumps as _dumps, loads as _loads, now_iso as _now
from scene_merge_prompt import (
    SCENE_MERGE_INCREMENTAL_PROMPT_VERSION,
    SCENE_MERGE_INCREMENTAL_SYSTEM_PROMPT,
    SCENE_MERGE_PROMPT_VERSION,
    SCENE_MERGE_SYSTEM_PROMPT,
)
from skill_constants import (
    PENDING_TAG_SOURCE_LABELS,
    SCENE_MIN_ATOMS_FOR_GENERATION,
    SCENE_RECALL_POOL_LIMIT,
    SCENE_SEMANTIC_RESERVED,
    SCENE_SEMANTIC_TOP_N,
)

logger = logging.getLogger(__name__)

PRIMARY_CATEGORIES = ("制度与标准", "方法与工具", "项目案例", "指标数据", "专家经验")

# 字段长度约束
SCENE_NAME_MAX = 30
SCENE_DESCRIPTION_MAX = 300
SCENE_ALIAS_MAX = 30
SCENE_ALIASES_MAX_COUNT = 50
SCENE_PROBLEM_MAX = 100
SCENE_PROBLEMS_MAX_COUNT = 10

# 归并建议：模型返回校验失败时带错误清单重试，总尝试次数上限
MERGE_MAX_ATTEMPTS = 2
MERGE_ATOM_TITLES_PER_TAG = 3
MERGE_RAW_RESPONSE_KEEP = 20000


class SceneCatalogError(Exception):
    """带 HTTP 语义的业务错误，由接口层转换为 HTTPException。"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def clean_tag(value: Any) -> str:
    """展示用：去首尾空白并把内部连续空白压为一个空格。"""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_tag(value: Any) -> str:
    """匹配用：去掉全部空白并转小写，空白差异不形成新标签（M01 PRD 5.3.4）。"""
    return re.sub(r"\s+", "", str(value or "")).lower()


def write_audit(conn, org_id: str, user_id: str, action: str, target_type: str, target_id: str,
                details: Optional[Dict[str, Any]] = None, now_iso: Optional[str] = None) -> None:
    """复用 audit_logs；details 只记录必要摘要，不写全文与模型密钥。"""
    conn.execute(
        """
        INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"aud_{uuid.uuid4().hex[:12]}",
            org_id,
            user_id,
            action,
            target_type,
            target_id,
            _dumps(details or {}),
            now_iso or _now(),
        ),
    )


# ---------------------------------------------------------------------------
# 场景目录读取
# ---------------------------------------------------------------------------

def serialize_scene(row: Any) -> Dict[str, Any]:
    return {
        "scene_id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "aliases": _loads(row["aliases_json"], []),
        "typical_problems": _loads(row["typical_problems_json"], []),
        "status": row["status"],
        "origin": row["origin"],
        "revision_token": row["revision_token"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def list_scenes(conn, org_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM scenes WHERE organization_id = ?"
    params: List[Any] = [org_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at, name"
    return [serialize_scene(r) for r in conn.execute(sql, params).fetchall()]


def get_scene(conn, org_id: str, scene_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM scenes WHERE id = ? AND organization_id = ?",
        (scene_id, org_id),
    ).fetchone()
    return serialize_scene(row) if row else None


def catalog_exists(conn, org_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM scenes WHERE organization_id = ? LIMIT 1", (org_id,)).fetchone()
    return row is not None


def get_catalog_for_extraction(conn, org_id: str) -> List[Dict[str, Any]]:
    """M01-C1：抽取时提供的启用场景（名称与说明）；无目录返回空列表。"""
    return [
        {"name": s["name"], "description": s["description"]}
        for s in list_scenes(conn, org_id, status="active")
    ]


def build_tag_lookup(scenes: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """规范化标签 -> scene_id，覆盖场景名称与全部别名。"""
    lookup: Dict[str, str] = {}
    for scene in scenes:
        for value in [scene["name"], *scene.get("aliases", [])]:
            norm = normalize_tag(value)
            if norm and norm not in lookup:
                lookup[norm] = scene["scene_id"]
    return lookup


# ---------------------------------------------------------------------------
# FR01.1 汇总可引用原子的场景标签
# ---------------------------------------------------------------------------

def _eligible_atom_rows(conn, user: Dict[str, Any], now_iso: Optional[str] = None) -> List[Any]:
    where, params = build_eligibility_sql(user, now_iso)
    return conn.execute(
        f"""
        SELECT kv.id AS version_id, ki.id AS item_id, kv.title, kv.primary_category,
               kv.atom_type, kv.statement, kv.business_scenes_json, kv.problem_tags_json
        FROM knowledge_versions kv
        JOIN knowledge_items ki ON kv.item_id = ki.id
        JOIN documents d ON ki.document_id = d.id
        JOIN document_versions dv ON kv.source_document_version_id = dv.id
        WHERE {' AND '.join(where)}
        ORDER BY kv.created_at, kv.id
        """,
        params,
    ).fetchall()


def collect_scene_tag_stats(conn, user: Dict[str, Any], now_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    """汇总全部满足 R1 的原子的 business_scenes 标签及出现次数。"""
    groups: Dict[str, Dict[str, Any]] = {}
    for row in _eligible_atom_rows(conn, user, now_iso):
        seen_in_atom = set()
        for raw in _loads(row["business_scenes_json"], []):
            tag = clean_tag(raw)
            norm = normalize_tag(tag)
            if not norm or norm in seen_in_atom:
                continue
            seen_in_atom.add(norm)
            entry = groups.setdefault(norm, {"tag": tag, "count": 0, "atoms": []})
            entry["count"] += 1
            entry["atoms"].append({
                "item_id": row["item_id"],
                "version_id": row["version_id"],
                "title": row["title"],
            })
    return sorted(groups.values(), key=lambda e: (-e["count"], e["tag"]))


# ---------------------------------------------------------------------------
# M01-C2 / C3 待归并标签
# ---------------------------------------------------------------------------

def serialize_pending_tag(row: Any) -> Dict[str, Any]:
    return {
        "pending_tag_id": row["id"],
        "tag": row["tag"],
        "status": row["status"],
        "source_atoms": _loads(row["source_atoms_json"], []),
        "resolved_scene_id": row["resolved_scene_id"],
        "resolution_note": row["resolution_note"],
        "resolved_by": row["resolved_by"],
        "resolved_at": row["resolved_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _register_pending_sources(conn, org_id, tag, norm, atom_refs, now_iso):
    """Merge all sources for one tag with a single read and write."""
    row = conn.execute(
        "SELECT * FROM scene_pending_tags WHERE organization_id = ? AND tag_normalized = ?", (org_id, norm),
    ).fetchone()
    sources = _loads(row["source_atoms_json"], []) if row else []
    known = {(s.get("version_id"), s.get("source")) for s in sources}
    for atom_ref in atom_refs:
        key = (atom_ref.get("version_id"), atom_ref.get("source"))
        if key not in known:
            sources.append(atom_ref)
            known.add(key)
    if not row:
        conn.execute(
            """INSERT INTO scene_pending_tags
               (id, organization_id, tag, tag_normalized, source_atoms_json, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (f"spt_{uuid.uuid4().hex[:12]}", org_id, tag, norm, _dumps(sources), now_iso, now_iso),
        )
        return tag
    status, note = row["status"], row["resolution_note"]
    if status in ("mapped", "created"):
        status, note = "pending", "原归并关系已不在目录中，重新进入待归并"
    conn.execute(
        """UPDATE scene_pending_tags SET source_atoms_json = ?, status = ?, resolution_note = ?, updated_at = ?
           WHERE id = ?""", (_dumps(sources), status, note, now_iso, row["id"]),
    )
    return row["tag"] if status == "pending" else None


def register_unmatched_scene_tags(
    conn,
    org_id: str,
    tags: Iterable[Any],
    item_id: Optional[str],
    version_id: Optional[str],
    source: str,
    title: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> List[str]:
    """
    目录外标签进入待归并列表；原子上的标签保持不变。
    场景目录为空时直接返回，M01 行为与没有本模块时一致。
    """
    if source not in PENDING_TAG_SOURCE_LABELS:
        raise ValueError(f"未知的待归并来源 {source}")
    scenes = list_scenes(conn, org_id)
    if not scenes:
        return []
    lookup = build_tag_lookup(scenes)
    now_iso = now_iso or _now()
    registered: List[str] = []
    seen = set()
    for raw in tags or []:
        tag = clean_tag(raw)
        norm = normalize_tag(tag)
        if not norm or norm in seen or norm in lookup:
            continue
        seen.add(norm)
        atom_ref = {
            "item_id": item_id,
            "version_id": version_id,
            "title": title,
            "source": source,
            "at": now_iso,
        }
        registered_tag = _register_pending_sources(conn, org_id, tag, norm, [atom_ref], now_iso)
        if registered_tag:
            registered.append(registered_tag)
    return registered


def list_pending_tags(conn, org_id: str, status: Optional[str] = "pending") -> List[Dict[str, Any]]:
    sql = "SELECT * FROM scene_pending_tags WHERE organization_id = ?"
    params: List[Any] = [org_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC, tag"
    return [serialize_pending_tag(r) for r in conn.execute(sql, params).fetchall()]


def _mark_pending_resolved_by_scene(conn, org_id: str, scene: Dict[str, Any], user_id: str,
                                    status: str, now_iso: str) -> int:
    """场景名称或别名覆盖了待归并标签时，标签视为已归入该场景。"""
    norms = {normalize_tag(v) for v in [scene["name"], *scene["aliases"]] if normalize_tag(v)}
    if not norms:
        return 0
    placeholders = ",".join("?" for _ in norms)
    cur = conn.execute(
        f"""
        UPDATE scene_pending_tags
        SET status = ?, resolved_scene_id = ?, resolved_by = ?, resolved_at = ?, updated_at = ?,
            resolution_note = COALESCE(resolution_note, '已归入场景目录')
        WHERE organization_id = ? AND status = 'pending' AND tag_normalized IN ({placeholders})
        """,
        [status, scene["scene_id"], user_id, now_iso, now_iso, org_id, *norms],
    )
    return cur.rowcount


# ---------------------------------------------------------------------------
# 场景增删改、停用
# ---------------------------------------------------------------------------

def _clean_str_list(values: Any, field_label: str, max_len: int, max_count: int) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        raise SceneCatalogError(400, f"{field_label}必须是文本列表")
    cleaned: List[str] = []
    seen = set()
    for v in values:
        text = clean_tag(v)
        norm = normalize_tag(text)
        if not norm or norm in seen:
            continue
        if len(text) > max_len:
            raise SceneCatalogError(400, f"{field_label}「{text[:12]}…」超过 {max_len} 字")
        seen.add(norm)
        cleaned.append(text)
    if len(cleaned) > max_count:
        raise SceneCatalogError(400, f"{field_label}最多 {max_count} 项")
    return cleaned


def validate_scene_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    name = clean_tag(payload.get("name"))
    if not name:
        raise SceneCatalogError(400, "场景名称不能为空")
    if len(name) > SCENE_NAME_MAX:
        raise SceneCatalogError(400, f"场景名称不超过 {SCENE_NAME_MAX} 字")
    description = str(payload.get("description") or "").strip()
    if len(description) > SCENE_DESCRIPTION_MAX:
        raise SceneCatalogError(400, f"场景说明不超过 {SCENE_DESCRIPTION_MAX} 字")
    aliases = _clean_str_list(payload.get("aliases"), "包含的标签", SCENE_ALIAS_MAX, SCENE_ALIASES_MAX_COUNT)
    problems = _clean_str_list(
        payload.get("typical_problems"), "常见问题", SCENE_PROBLEM_MAX, SCENE_PROBLEMS_MAX_COUNT
    )
    return {"name": name, "description": description, "aliases": aliases, "typical_problems": problems}


def _check_catalog_conflicts(conn, org_id: str, fields: Dict[str, Any],
                             exclude_scene_id: Optional[str] = None) -> None:
    """名称在企业内唯一；名称与别名不得与其他场景的名称或别名重合，保证标签只归属一个场景。"""
    own = {normalize_tag(fields["name"])} | {normalize_tag(a) for a in fields["aliases"]}
    for scene in list_scenes(conn, org_id):
        if scene["scene_id"] == exclude_scene_id:
            continue
        if normalize_tag(scene["name"]) == normalize_tag(fields["name"]):
            raise SceneCatalogError(409, f"场景名称「{fields['name']}」已存在")
        others = {normalize_tag(scene["name"])} | {normalize_tag(a) for a in scene["aliases"]}
        clash = own & others
        if clash:
            raise SceneCatalogError(
                409, f"名称或包含的标签与场景「{scene['name']}」重复：{'、'.join(sorted(clash))}"
            )


def create_scene(conn, org_id: str, user_id: str, payload: Dict[str, Any],
                 origin: str = "manual", now_iso: Optional[str] = None,
                 pending_status: str = "created", audit: bool = True) -> Dict[str, Any]:
    fields = validate_scene_fields(payload)
    _check_catalog_conflicts(conn, org_id, fields)
    now_iso = now_iso or _now()
    scene_id = f"scene_{uuid.uuid4().hex[:12]}"
    try:
        conn.execute(
            """
            INSERT INTO scenes
            (id, organization_id, name, name_normalized, description, aliases_json, typical_problems_json,
             status, origin, revision_token, created_by, created_at, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
            """,
            (
                scene_id, org_id, fields["name"], normalize_tag(fields["name"]), fields["description"],
                _dumps(fields["aliases"]), _dumps(fields["typical_problems"]), origin,
                uuid.uuid4().hex, user_id, now_iso, user_id, now_iso,
            ),
        )
    except sqlite3.IntegrityError:
        raise SceneCatalogError(409, f"场景名称「{fields['name']}」已存在")
    scene = get_scene(conn, org_id, scene_id)
    _mark_pending_resolved_by_scene(conn, org_id, scene, user_id, pending_status, now_iso)
    if audit:
        write_audit(conn, org_id, user_id, "scene_create", "scene", scene_id,
                    {"name": fields["name"], "alias_count": len(fields["aliases"]), "origin": origin}, now_iso)
    return scene


def _require_scene(conn, org_id: str, scene_id: str) -> Dict[str, Any]:
    scene = get_scene(conn, org_id, scene_id)
    if not scene:
        raise SceneCatalogError(404, "场景不存在")
    return scene


def _check_revision(scene: Dict[str, Any], payload: Dict[str, Any]) -> None:
    token = payload.get("revision_token")
    if token and token != scene["revision_token"]:
        raise SceneCatalogError(409, "场景已被其他操作修改，请刷新后再保存")


def update_scene(conn, org_id: str, user_id: str, scene_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    scene = _require_scene(conn, org_id, scene_id)
    _check_revision(scene, payload)
    merged = {
        "name": payload.get("name", scene["name"]),
        "description": payload.get("description", scene["description"]),
        "aliases": payload.get("aliases", scene["aliases"]),
        "typical_problems": payload.get("typical_problems", scene["typical_problems"]),
    }
    fields = validate_scene_fields(merged)
    _check_catalog_conflicts(conn, org_id, fields, exclude_scene_id=scene_id)
    now_iso = _now()
    try:
        conn.execute(
            """
            UPDATE scenes
            SET name = ?, name_normalized = ?, description = ?, aliases_json = ?, typical_problems_json = ?,
                revision_token = ?, updated_by = ?, updated_at = ?
            WHERE id = ? AND organization_id = ?
            """,
            (
                fields["name"], normalize_tag(fields["name"]), fields["description"],
                _dumps(fields["aliases"]), _dumps(fields["typical_problems"]),
                uuid.uuid4().hex, user_id, now_iso, scene_id, org_id,
            ),
        )
    except sqlite3.IntegrityError:
        raise SceneCatalogError(409, f"场景名称「{fields['name']}」已存在")
    updated = get_scene(conn, org_id, scene_id)
    _mark_pending_resolved_by_scene(conn, org_id, updated, user_id, "mapped", now_iso)
    changed = [k for k in ("name", "description", "aliases", "typical_problems") if scene[k] != updated[k]]
    write_audit(conn, org_id, user_id, "scene_update", "scene", scene_id,
                {"name": updated["name"], "changed_fields": changed}, now_iso)
    return updated


def set_scene_status(conn, org_id: str, user_id: str, scene_id: str, status: str,
                     payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if status not in ("active", "disabled"):
        raise SceneCatalogError(400, "场景状态只能是启用或停用")
    scene = _require_scene(conn, org_id, scene_id)
    _check_revision(scene, payload or {})
    if scene["status"] == status:
        return scene
    now_iso = _now()
    conn.execute(
        "UPDATE scenes SET status = ?, revision_token = ?, updated_by = ?, updated_at = ? WHERE id = ? AND organization_id = ?",
        (status, uuid.uuid4().hex, user_id, now_iso, scene_id, org_id),
    )
    write_audit(conn, org_id, user_id, "scene_enable" if status == "active" else "scene_disable",
                "scene", scene_id, {"name": scene["name"]}, now_iso)
    return get_scene(conn, org_id, scene_id)


def count_scene_skills(conn, org_id: str, scene_id: str) -> Dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM skills WHERE organization_id = ? AND scene_id = ? GROUP BY status",
        (org_id, scene_id),
    ).fetchall()
    counts = {r["status"]: int(r["n"]) for r in rows}
    return {
        "pending_review": counts.get("pending_review", 0),
        "approved": counts.get("approved", 0),
        "needs_recheck": counts.get("needs_recheck", 0),
        "total": sum(counts.values()),
    }


def delete_scene(conn, org_id: str, user_id: str, scene_id: str) -> Dict[str, Any]:
    scene = _require_scene(conn, org_id, scene_id)
    skill_total = count_scene_skills(conn, org_id, scene_id)["total"]
    if skill_total:
        raise SceneCatalogError(409, f"该场景下已有 {skill_total} 个 Skill，不能删除，请改为停用")
    now_iso = _now()
    conn.execute("DELETE FROM scenes WHERE id = ? AND organization_id = ?", (scene_id, org_id))
    write_audit(conn, org_id, user_id, "scene_delete", "scene", scene_id,
                {"name": scene["name"], "alias_count": len(scene["aliases"])}, now_iso)
    return scene


def resolve_pending_tag(conn, org_id: str, user_id: str, pending_tag_id: str,
                        payload: Dict[str, Any]) -> Dict[str, Any]:
    """待归并标签处理：映射到已有场景（写入别名）、新建场景或忽略。"""
    row = conn.execute(
        "SELECT * FROM scene_pending_tags WHERE id = ? AND organization_id = ?",
        (pending_tag_id, org_id),
    ).fetchone()
    if not row:
        raise SceneCatalogError(404, "这个标签不存在或已处理")
    if row["status"] != "pending":
        raise SceneCatalogError(409, "这个标签已经处理过了，请刷新页面")
    action = payload.get("action")
    now_iso = _now()
    tag = row["tag"]
    scene: Optional[Dict[str, Any]] = None

    if action == "map":
        scene = _require_scene(conn, org_id, str(payload.get("scene_id") or ""))
        scene = _append_scene_aliases(conn, org_id, user_id, scene, [tag], now_iso, resolve_pending=False)
        new_status = "mapped"
    elif action == "create":
        new_scene = payload.get("new_scene") or {}
        name = clean_tag(new_scene.get("name")) or tag
        aliases = list(new_scene.get("aliases") or [])
        if normalize_tag(tag) not in {normalize_tag(a) for a in aliases}:
            aliases.append(tag)
        scene = create_scene(
            conn, org_id, user_id,
            {
                "name": name,
                "description": new_scene.get("description") or "",
                "aliases": aliases,
                "typical_problems": new_scene.get("typical_problems") or [],
            },
            origin="pending_tag", now_iso=now_iso, audit=True,
        )
        new_status = "created"
    elif action == "ignore":
        new_status = "ignored"
    else:
        raise SceneCatalogError(400, "处理方式只能是 map、create 或 ignore")

    note = str(payload.get("note") or "").strip()[:200] or None
    conn.execute(
        """
        UPDATE scene_pending_tags
        SET status = ?, resolved_scene_id = ?, resolution_note = ?, resolved_by = ?, resolved_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_status, scene["scene_id"] if scene else None, note, user_id, now_iso, now_iso, pending_tag_id),
    )
    write_audit(conn, org_id, user_id, f"scene_pending_tag_{action}", "scene_pending_tag", pending_tag_id,
                {"tag": tag, "scene_id": scene["scene_id"] if scene else None,
                 "scene_name": scene["name"] if scene else None}, now_iso)
    result = serialize_pending_tag(conn.execute(
        "SELECT * FROM scene_pending_tags WHERE id = ?", (pending_tag_id,)
    ).fetchone())
    result["scene"] = get_scene(conn, org_id, scene["scene_id"]) if scene else None
    return result


# ---------------------------------------------------------------------------
# FR01.2~4 归并建议
# ---------------------------------------------------------------------------

def serialize_suggestion(row: Any) -> Dict[str, Any]:
    keys = row.keys()
    return {
        "suggestion_id": row["id"],
        "mode": row["mode"] if "mode" in keys and row["mode"] else "initial",
        "status": row["status"],
        "tag_stats": _loads(row["tag_stats_json"], []),
        "groups": _loads(row["groups_json"], []),
        "unassigned_tags": _loads(row["unassigned_tags_json"], []),
        "model_name": row["model_name"],
        "prompt_version": row["prompt_version"],
        "attempt_count": row["attempt_count"],
        "attempts": [
            {k: v for k, v in a.items() if k != "raw_response"}
            for a in _loads(row["attempts_json"], [])
        ],
        "error_message": row["error_message"],
        "requested_by": row["requested_by"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "confirmed_by": row["confirmed_by"],
        "confirmed_at": row["confirmed_at"],
    }


def get_suggestion(conn, org_id: str, suggestion_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM scene_merge_suggestions WHERE id = ? AND organization_id = ?",
        (suggestion_id, org_id),
    ).fetchone()
    return serialize_suggestion(row) if row else None


def get_latest_suggestion(conn, org_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM scene_merge_suggestions WHERE organization_id = ? ORDER BY created_at DESC LIMIT 1",
        (org_id,),
    ).fetchone()
    return serialize_suggestion(row) if row else None


def collect_unorganized_tags(conn, user: Dict[str, Any], now_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    目录建立后仍未归入任何场景的标签：只统计满足 R1 的原子；已被管理员忽略的标签不再列入。
    目录为空时返回空列表（此时应走首次整理）。
    """
    org_id = user["organization_id"]
    scenes = list_scenes(conn, org_id)
    if not scenes:
        return []
    lookup = build_tag_lookup(scenes)
    ignored = {
        r["tag_normalized"]
        for r in conn.execute(
            "SELECT tag_normalized FROM scene_pending_tags WHERE organization_id = ? AND status = 'ignored'",
            (org_id,),
        ).fetchall()
    }
    return [
        entry for entry in collect_scene_tag_stats(conn, user, now_iso)
        if normalize_tag(entry["tag"]) not in lookup and normalize_tag(entry["tag"]) not in ignored
    ]


def create_merge_suggestion(conn, user: Dict[str, Any]) -> Dict[str, Any]:
    """
    发起场景整理：
    - 目录为空：首次整理，汇总全部可引用原子的场景标签（mode=initial）；
    - 目录已存在：整理新标签，只汇总目录外、未被忽略的标签，建议归入已有场景或组成新场景（mode=incremental）。
    """
    org_id = user["organization_id"]
    if catalog_exists(conn, org_id):
        mode = "incremental"
        prompt_version = SCENE_MERGE_INCREMENTAL_PROMPT_VERSION
        stats = collect_unorganized_tags(conn, user)
        if not stats:
            raise SceneCatalogError(409, "目前没有需要整理的新标签")
    else:
        mode = "initial"
        prompt_version = SCENE_MERGE_PROMPT_VERSION
        stats = collect_scene_tag_stats(conn, user)
        if not stats:
            raise SceneCatalogError(409, "知识库里还没有带业务场景标签的已确认知识，暂时无法整理")
    now_iso = _now()
    suggestion_id = f"smg_{uuid.uuid4().hex[:12]}"
    try:
        conn.execute(
            """
            INSERT INTO scene_merge_suggestions
            (id, organization_id, mode, status, tag_stats_json, prompt_version, requested_by, created_at)
            VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (suggestion_id, org_id, mode, _dumps(stats), prompt_version, user["id"], now_iso),
        )
    except sqlite3.IntegrityError:
        raise SceneCatalogError(409, "正在整理中，请稍候")
    write_audit(conn, org_id, user["id"], "scene_merge_suggest_requested", "scene_merge_suggestion",
                suggestion_id, {"mode": mode, "tag_count": len(stats), "prompt_version": prompt_version}, now_iso)
    return get_suggestion(conn, org_id, suggestion_id)


def _tag_lines(tag_stats: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for entry in tag_stats:
        titles = "；".join(a["title"] for a in entry["atoms"][:MERGE_ATOM_TITLES_PER_TAG] if a.get("title"))
        lines.append(f"- {entry['tag']} | {entry['count']} | {titles}")
    return lines


def build_merge_user_content(tag_stats: List[Dict[str, Any]],
                             existing_scenes: Optional[List[Dict[str, Any]]] = None) -> str:
    if existing_scenes is None:
        return "\n".join(["业务场景标签清单（标签 | 出现次数 | 示例原子标题）：", *_tag_lines(tag_stats)])
    scene_lines = [
        f"- {s['name']} | {s.get('description') or '无说明'} | 已包含标签：{'、'.join(s.get('aliases') or []) or '无'}"
        for s in existing_scenes
    ]
    return "\n".join([
        "现有场景目录（名称 | 说明 | 已包含标签）：", *scene_lines, "",
        "新标签清单（标签 | 出现次数 | 示例原子标题）：", *_tag_lines(tag_stats),
    ])


def validate_merge_response(
    raw_text: str,
    tag_stats: List[Dict[str, Any]],
    existing_scenes: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """
    校验模型返回的整理建议 JSON。返回 (groups, unassigned_tags, errors)；errors 非空即视为不通过。
    - 标签只能来自输入，且每个标签最多归入一个分组；未被覆盖的标签自动列入 unassigned_tags。
    - existing_scenes 非空（整理新标签）时：existing=true 的分组必须对应一个启用场景，写入 target_scene_id；
      新场景名称不得与已有场景的名称或标签重复。
    """
    errors: List[str] = []
    try:
        data = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError) as exc:
        return [], [], [f"返回不是合法 JSON：{exc}"]
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        return [], [], ["返回缺少 groups 数组"]

    incremental = existing_scenes is not None
    existing_by_name = {normalize_tag(s["name"]): s for s in (existing_scenes or [])}
    existing_terms = set()
    for s in existing_scenes or []:
        existing_terms.add(normalize_tag(s["name"]))
        existing_terms.update(normalize_tag(a) for a in s.get("aliases") or [])

    input_tags = {normalize_tag(e["tag"]): e["tag"] for e in tag_stats}
    used: Dict[str, str] = {}
    names = set()
    groups: List[Dict[str, Any]] = []
    for idx, group in enumerate(data["groups"], start=1):
        if not isinstance(group, dict):
            errors.append(f"第 {idx} 组不是对象")
            continue
        name = clean_tag(group.get("name"))
        is_existing = bool(group.get("existing")) if incremental else False
        if incremental is False and group.get("existing"):
            errors.append(f"第 {idx} 组不应标记为已有场景")
        target = None
        if not name:
            errors.append(f"第 {idx} 组缺少 name")
        elif len(name) > SCENE_NAME_MAX:
            errors.append(f"第 {idx} 组 name 超过 {SCENE_NAME_MAX} 字")
        elif normalize_tag(name) in names:
            errors.append(f"分组名称「{name}」重复")
        elif is_existing:
            target = existing_by_name.get(normalize_tag(name))
            if not target:
                errors.append(f"第 {idx} 组标记为已有场景，但「{name}」不在现有场景目录中")
            else:
                name = target["name"]
        elif incremental and normalize_tag(name) in existing_terms:
            errors.append(f"新场景名称「{name}」与现有场景的名称或标签重复，应改为归入现有场景")
        names.add(normalize_tag(name))

        raw_tags = group.get("tags")
        if not isinstance(raw_tags, list) or not raw_tags:
            errors.append(f"第 {idx} 组「{name}」缺少 tags")
            raw_tags = []
        tags: List[str] = []
        for raw in raw_tags:
            norm = normalize_tag(raw)
            if norm not in input_tags:
                errors.append(f"第 {idx} 组包含输入中不存在的标签「{clean_tag(raw)}」")
                continue
            if norm in used and used[norm] != name:
                errors.append(f"标签「{input_tags[norm]}」同时出现在「{used[norm]}」和「{name}」")
                continue
            if norm not in used:
                used[norm] = name
                tags.append(input_tags[norm])

        description = str(group.get("description") or "").strip()
        problems = group.get("typical_problems")
        if problems is None and is_existing:
            problems = []
        if not isinstance(problems, list):
            errors.append(f"第 {idx} 组「{name}」typical_problems 必须是列表")
            problems = []
        if not is_existing and not description:
            errors.append(f"第 {idx} 组「{name}」缺少 description")
        reason = str(group.get("reason") or "").strip()
        if not reason:
            errors.append(f"第 {idx} 组「{name}」缺少 reason")
        item = {
            "name": name,
            "description": description[:SCENE_DESCRIPTION_MAX],
            "typical_problems": [clean_tag(p)[:SCENE_PROBLEM_MAX] for p in problems if clean_tag(p)][:SCENE_PROBLEMS_MAX_COUNT],
            "tags": tags,
            "reason": reason,
        }
        if incremental:
            item["existing"] = is_existing
            item["target_scene_id"] = target["scene_id"] if target else None
            if target:
                item["description"] = target.get("description") or ""
        groups.append(item)

    if not groups:
        errors.append("groups 为空")

    unassigned = [tag for norm, tag in input_tags.items() if norm not in used]
    return groups, unassigned, errors


def run_merge_suggestion(suggestion_id: str, api_key: Optional[str] = None) -> None:
    """
    后台执行整理建议（复用 tasks.py 的线程池与恢复模式）。
    校验失败时把错误清单连同上次输出交回模型重试；全部失败只把建议标为失败，不触碰场景目录。
    """
    from database import get_db
    import config

    with get_db() as conn:
        row = conn.execute("SELECT * FROM scene_merge_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if not row or row["status"] not in ("queued", "running"):
            return
        conn.execute(
            "UPDATE scene_merge_suggestions SET status = 'running', started_at = ?, error_message = NULL WHERE id = ?",
            (_now(), suggestion_id),
        )
        tag_stats = _loads(row["tag_stats_json"], [])
        prior_attempts = int(row["attempt_count"] or 0)
        mode = row["mode"] or "initial"
        existing_scenes = list_scenes(conn, row["organization_id"], status="active") if mode == "incremental" else None

    key = api_key or config.DEEPSEEK_API_KEY
    attempts: List[Dict[str, Any]] = []
    system_prompt = SCENE_MERGE_INCREMENTAL_SYSTEM_PROMPT if mode == "incremental" else SCENE_MERGE_SYSTEM_PROMPT
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_merge_user_content(tag_stats, existing_scenes)},
    ]
    groups: List[Dict[str, Any]] = []
    unassigned: List[str] = []
    model_used: Optional[str] = None
    final_error: Optional[str] = None

    for attempt in range(1, MERGE_MAX_ATTEMPTS + 1):
        record: Dict[str, Any] = {"attempt": attempt, "at": _now()}
        try:
            if not key:
                raise ValueError("在线模型未配置，无法生成归并建议")
            raw_text, model_used = post_chat_completion(messages, api_key=key)
        except Exception as exc:
            final_error = f"模型调用失败：{type(exc).__name__}：{str(exc)[:200]}"
            record.update({"ok": False, "errors": [final_error]})
            attempts.append(record)
            break
        groups, unassigned, errors = validate_merge_response(raw_text, tag_stats, existing_scenes)
        record.update({
            "ok": not errors,
            "errors": errors[:20],
            "raw_length": len(raw_text or ""),
            "raw_response": (raw_text or "")[:MERGE_RAW_RESPONSE_KEEP],
        })
        attempts.append(record)
        if not errors:
            final_error = None
            break
        final_error = "归并建议未通过校验：" + "；".join(errors[:5])
        messages = messages[:2] + [
            {"role": "assistant", "content": raw_text or ""},
            {"role": "user", "content": "上次输出未通过校验，请修正以下问题后重新输出完整 JSON：\n- " + "\n- ".join(errors[:20])},
        ]

    with get_db() as conn:
        current = conn.execute("SELECT status FROM scene_merge_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if not current or current["status"] != "running":
            return
        status = "failed" if final_error else "completed"
        conn.execute(
            """
            UPDATE scene_merge_suggestions
            SET status = ?, groups_json = ?, unassigned_tags_json = ?, model_name = ?, attempt_count = ?,
                attempts_json = ?, error_message = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                _dumps(groups) if not final_error else None,
                _dumps(unassigned) if not final_error else None,
                model_used or config.DEEPSEEK_MODEL,
                prior_attempts + len(attempts),
                _dumps(attempts),
                final_error,
                _now(),
                suggestion_id,
            ),
        )


def _append_scene_aliases(conn, org_id: str, user_id: str, scene: Dict[str, Any], tags: List[str],
                          now_iso: str, *, resolve_pending: bool = True) -> Dict[str, Any]:
    """把标签追加为已有场景的别名；名称、说明、典型问题保持不变。"""
    known = {normalize_tag(a) for a in scene["aliases"]} | {normalize_tag(scene["name"])}
    aliases = list(scene["aliases"]) + [t for t in tags if normalize_tag(t) not in known]
    fields = validate_scene_fields({**scene, "aliases": aliases})
    _check_catalog_conflicts(conn, org_id, fields, exclude_scene_id=scene["scene_id"])
    conn.execute(
        "UPDATE scenes SET aliases_json = ?, revision_token = ?, updated_by = ?, updated_at = ? WHERE id = ? AND organization_id = ?",
        (_dumps(fields["aliases"]), uuid.uuid4().hex, user_id, now_iso, scene["scene_id"], org_id),
    )
    updated = get_scene(conn, org_id, scene["scene_id"])
    if resolve_pending:
        _mark_pending_resolved_by_scene(conn, org_id, updated, user_id, "mapped", now_iso)
    return updated


def confirm_merge_suggestion(conn, org_id: str, user_id: str, suggestion_id: str,
                             groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    管理员逐组确认（可改名、拆开、合并）后写入场景目录；原标签写入 aliases。
    整理新标签时，带 target_scene_id 的分组只向该已有场景追加标签，不改其名称和说明。
    不修改原子标签，不产生原子新版本。未归入任何场景的标签进入待归并列表。
    """
    row = conn.execute(
        "SELECT * FROM scene_merge_suggestions WHERE id = ? AND organization_id = ?",
        (suggestion_id, org_id),
    ).fetchone()
    if not row:
        raise SceneCatalogError(404, "这份整理结果不存在")
    if row["status"] != "completed":
        raise SceneCatalogError(409, "这份整理结果已处理过，请刷新页面")
    if not isinstance(groups, list) or not groups:
        raise SceneCatalogError(400, "至少保留一个场景")
    mode = row["mode"] or "initial"

    tag_stats = _loads(row["tag_stats_json"], [])
    stat_by_norm = {normalize_tag(e["tag"]): e for e in tag_stats}
    seen_names: Dict[str, int] = {}
    seen_targets = set()
    tag_owner: Dict[str, str] = {}
    cleaned: List[Tuple[Optional[Dict[str, Any]], Dict[str, Any]]] = []
    for idx, group in enumerate(groups, start=1):
        target = None
        target_id = group.get("target_scene_id") if mode == "incremental" else None
        if target_id:
            target = get_scene(conn, org_id, str(target_id))
            if not target:
                raise SceneCatalogError(409, "要归入的场景已不存在，请刷新页面")
            if target["scene_id"] in seen_targets:
                raise SceneCatalogError(400, f"场景「{target['name']}」重复了")
            seen_targets.add(target["scene_id"])
            fields = {
                "name": target["name"],
                "aliases": _clean_str_list(group.get("tags"), "包含的标签", SCENE_ALIAS_MAX, SCENE_ALIASES_MAX_COUNT),
            }
        else:
            payload = {
                "name": group.get("name"),
                "description": group.get("description"),
                "aliases": group.get("tags") if group.get("tags") is not None else group.get("aliases"),
                "typical_problems": group.get("typical_problems"),
            }
            fields = validate_scene_fields(payload)
            name_norm = normalize_tag(fields["name"])
            if name_norm in seen_names:
                raise SceneCatalogError(400, f"场景名称「{fields['name']}」重复了")
            seen_names[name_norm] = idx
        for alias in fields["aliases"]:
            norm = normalize_tag(alias)
            if norm in tag_owner and tag_owner[norm] != fields["name"]:
                raise SceneCatalogError(400, f"标签「{alias}」同时放在了「{tag_owner[norm]}」和「{fields['name']}」里")
            tag_owner[norm] = fields["name"]
        cleaned.append((target, fields))

    now_iso = _now()
    created: List[Dict[str, Any]] = []
    extended: List[Dict[str, Any]] = []
    origin = "catalog_init" if mode == "initial" else "catalog_organize"
    for target, fields in cleaned:
        if target:
            if fields["aliases"]:
                extended.append(_append_scene_aliases(conn, org_id, user_id, target, fields["aliases"], now_iso))
        else:
            created.append(create_scene(conn, org_id, user_id, fields, origin=origin, now_iso=now_iso, audit=False))

    # 未归入任何场景的标签：进入待归并列表，保留来源原子
    lookup = build_tag_lookup(list_scenes(conn, org_id))
    leftover = []
    for norm, entry in stat_by_norm.items():
        if norm in lookup:
            continue
        leftover.append(entry["tag"])
        atom_refs = [{"item_id": a["item_id"], "version_id": a["version_id"], "title": a.get("title"),
                      "source": origin, "at": now_iso} for a in entry["atoms"]]
        if atom_refs:
            _register_pending_sources(conn, org_id, entry["tag"], norm, atom_refs, now_iso)

    conn.execute(
        """
        UPDATE scene_merge_suggestions
        SET status = 'confirmed', confirmed_by = ?, confirmed_at = ?, confirmed_groups_json = ?
        WHERE id = ?
        """,
        (user_id, now_iso, _dumps([{**f, "target_scene_id": t["scene_id"] if t else None} for t, f in cleaned]),
         suggestion_id),
    )
    write_audit(conn, org_id, user_id, "scene_catalog_confirmed", "scene_merge_suggestion", suggestion_id, {
        "mode": mode,
        "scene_count": len(created),
        "scene_names": [s["name"] for s in created],
        "extended_scene_names": [s["name"] for s in extended],
        "pending_tag_count": len(leftover),
    }, now_iso)
    return {"scenes": created, "extended_scenes": extended, "pending_tags": leftover}


def discard_merge_suggestion(conn, org_id: str, user_id: str, suggestion_id: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT status FROM scene_merge_suggestions WHERE id = ? AND organization_id = ?",
        (suggestion_id, org_id),
    ).fetchone()
    if not row:
        raise SceneCatalogError(404, "这份整理结果不存在")
    if row["status"] not in ("completed", "failed"):
        raise SceneCatalogError(409, "这份整理结果已处理过，请刷新页面")
    now_iso = _now()
    conn.execute(
        "UPDATE scene_merge_suggestions SET status = 'discarded', completed_at = COALESCE(completed_at, ?) WHERE id = ?",
        (now_iso, suggestion_id),
    )
    write_audit(conn, org_id, user_id, "scene_merge_suggest_discarded", "scene_merge_suggestion",
                suggestion_id, {}, now_iso)
    return get_suggestion(conn, org_id, suggestion_id)


# ---------------------------------------------------------------------------
# FR04 原子召回
# ---------------------------------------------------------------------------

def build_semantic_query(scene: Dict[str, Any]) -> str:
    parts = [scene.get("name") or "", scene.get("description") or "", *scene.get("typical_problems", [])]
    return "；".join(clean_tag(p) for p in parts if clean_tag(p))


def recall_atoms(
    conn,
    user: Dict[str, Any],
    scene: Dict[str, Any],
    now_iso: Optional[str] = None,
    pool_limit: int = SCENE_RECALL_POOL_LIMIT,
    semantic_top_n: int = SCENE_SEMANTIC_TOP_N,
    search_fn: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    semantic_reserved: int = SCENE_SEMANTIC_RESERVED,
    focus_note: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    FR04 两路召回：
    1. 标签召回：原子 business_scenes 命中场景名称或任一别名；
    2. 语义召回：以名称、说明、典型问题（及生成侧重说明）为查询，调用现有混合检索取前 semantic_top_n 条；
    3. 合并去重后经 eligibility.py 过滤，截取 pool_limit 条：
       - 为只由语义召回的原子保留 semantic_reserved 个名额，语义原子不足时剩余名额还给标签命中；
       - 标签命中超出名额时，同时被语义命中的按检索得分在前，其余按新近程度（新的在前）。
    每条记录召回来源 recall_source（tag / semantic）及全部命中通道 matched_by。
    """
    now_iso = now_iso or _now()
    if search_fn is None:
        from hybrid_retrieval import hybrid_search
        search_fn = hybrid_search

    scene_norms = {normalize_tag(v) for v in [scene["name"], *scene.get("aliases", [])] if normalize_tag(v)}
    pool: Dict[str, Dict[str, Any]] = {}

    def _entry(row_like: Dict[str, Any]) -> Dict[str, Any]:
        return pool.setdefault(row_like["version_id"], {
            "item_id": row_like["item_id"],
            "version_id": row_like["version_id"],
            "title": row_like["title"],
            "primary_category": row_like["primary_category"],
            "atom_type": row_like["atom_type"],
            "statement": row_like["statement"],
            "business_scenes": row_like["business_scenes"],
            "matched_by": [],
            "matched_tags": [],
            "semantic_score": None,
            "semantic_rank": None,
        })

    tag_hits = 0
    rows = context["atom_rows"] if context is not None else _eligible_atom_rows(conn, user, now_iso)
    for seq, row in enumerate(rows):
        scenes_on_atom = (row["scene_tags"] if context is not None else
                          [clean_tag(t) for t in _loads(row["business_scenes_json"], []) if clean_tag(t)])
        tag_norms = row["scene_tag_norms"] if context is not None else [normalize_tag(t) for t in scenes_on_atom]
        matched = [t for t, norm in zip(scenes_on_atom, tag_norms) if norm in scene_norms]
        if not matched:
            continue
        entry = _entry({
            "item_id": row["item_id"], "version_id": row["version_id"], "title": row["title"],
            "primary_category": row["primary_category"], "atom_type": row["atom_type"],
            "statement": row["statement"], "business_scenes": scenes_on_atom,
        })
        entry["matched_by"].append("tag")
        entry["matched_tags"] = matched
        entry["_seq"] = seq  # 按确认时间升序，用于新近程度排序
        tag_hits += 1

    semantic_hits = 0
    semantic_error: Optional[str] = None
    query = build_semantic_query(scene)
    if focus_note and clean_tag(focus_note):
        query = f"{clean_tag(focus_note)}；{query}" if query else clean_tag(focus_note)
    if query and semantic_top_n > 0:
        try:
            results = search_fn(conn, user, query, now_iso)
        except Exception as exc:  # 向量模型不可用时仅标签召回生效，并如实返回原因
            semantic_error = f"{type(exc).__name__}：{str(exc)[:160]}"
            logger.warning("Scene semantic recall failed for %s: %s", scene.get("scene_id"), semantic_error)
            results = []
        for rank, res in enumerate(results[:semantic_top_n], start=1):
            entry = _entry({
                "item_id": res["item_id"], "version_id": res["version_id"], "title": res["title"],
                "primary_category": res.get("primary_category"), "atom_type": res.get("atom_type"),
                "statement": res.get("statement"), "business_scenes": res.get("business_scenes") or [],
            })
            if "semantic" not in entry["matched_by"]:
                entry["matched_by"].append("semantic")
            entry["semantic_score"] = res.get("relevance_score", res.get("score"))
            entry["semantic_rank"] = rank
            semantic_hits += 1

    merged_count = len(pool)
    eligible_ids = set(filter_eligible_version_ids(conn, list(pool), user, now_iso))
    candidates = [e for vid, e in pool.items() if vid in eligible_ids]
    for e in candidates:
        e["recall_source"] = "tag" if "tag" in e["matched_by"] else "semantic"

    def _score(e: Dict[str, Any]) -> float:
        return e["semantic_score"] if e["semantic_score"] is not None else -1.0

    tag_list = sorted((e for e in candidates if e["recall_source"] == "tag"),
                      key=lambda e: (-_score(e), -e.get("_seq", 0), e["version_id"]))
    semantic_list = sorted((e for e in candidates if e["recall_source"] == "semantic"),
                           key=lambda e: (-_score(e), e["title"] or "", e["version_id"]))
    limit = max(0, pool_limit)
    reserved = min(max(0, semantic_reserved), len(semantic_list), limit)
    chosen_tags = tag_list[:limit - reserved]
    chosen_semantic = semantic_list[:limit - len(chosen_tags)]
    atoms = chosen_tags + chosen_semantic
    for e in pool.values():
        e.pop("_seq", None)

    coverage = {c: 0 for c in PRIMARY_CATEGORIES}
    for a in atoms:
        if a["primary_category"] in coverage:
            coverage[a["primary_category"]] += 1

    return {
        "scene_id": scene.get("scene_id"),
        "atoms": atoms,
        "stats": {
            "tag_hits": tag_hits,
            "semantic_hits": semantic_hits,
            "merged": merged_count,
            "eligible": len(candidates),
            "returned": len(atoms),
            "truncated": len(candidates) > len(atoms),
            "tag_truncated": len(tag_list) - len(chosen_tags),
            "semantic_truncated": len(semantic_list) - len(chosen_semantic),
            "tag_in_pool": sum(1 for a in atoms if a["recall_source"] == "tag"),
            "semantic_in_pool": sum(1 for a in atoms if a["recall_source"] == "semantic"),
        },
        "category_coverage": coverage,
        "semantic_query": query,
        "semantic_error": semantic_error,
        "config": {"pool_limit": pool_limit, "semantic_top_n": semantic_top_n, "semantic_reserved": semantic_reserved},
    }


def scene_skill_counts(conn, org_id: str) -> Dict[str, Dict[str, int]]:
    """Fetch live per-scene Skill totals without running recall."""
    counts = {}
    for row in conn.execute(
        "SELECT scene_id, status, COUNT(*) AS n FROM skills WHERE organization_id = ? GROUP BY scene_id, status", (org_id,),
    ).fetchall():
        entry = counts.setdefault(row["scene_id"], {"pending_review": 0, "approved": 0, "needs_recheck": 0, "total": 0})
        entry["total"] += int(row["n"])
        if row["status"] in entry:
            entry[row["status"]] = int(row["n"])
    return counts


def build_scene_card_context(conn, user: Dict[str, Any], now_iso: Optional[str] = None,
                             skill_counts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Request-local data shared by all cards; each scene still runs semantic recall."""
    atoms = []
    for row in _eligible_atom_rows(conn, user, now_iso):
        tags = [clean_tag(t) for t in _loads(row["business_scenes_json"], []) if clean_tag(t)]
        atoms.append({**dict(row), "scene_tags": tags, "scene_tag_norms": [normalize_tag(t) for t in tags]})
    return {"atom_rows": atoms, "skill_counts": (scene_skill_counts(conn, user["organization_id"])
            if skill_counts is None else skill_counts)}


def build_scene_card(conn, user: Dict[str, Any], scene: Dict[str, Any], now_iso: Optional[str] = None,
                     context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """FR03 场景卡片：可用原子数以 recall_atoms 结果为准。"""
    recall = recall_atoms(conn, user, scene, now_iso=now_iso, context=context)
    available = recall["stats"]["returned"]
    can_generate = available >= SCENE_MIN_ATOMS_FOR_GENERATION
    return {
        **scene,
        "available_atom_count": available,
        "category_coverage": recall["category_coverage"],
        "recall_stats": recall["stats"],
        "semantic_error": recall["semantic_error"],
        "skill_counts": (context["skill_counts"].get(scene["scene_id"],
                         {"pending_review": 0, "approved": 0, "needs_recheck": 0, "total": 0}) if context is not None
                         else count_scene_skills(conn, user["organization_id"], scene["scene_id"])),
        "can_generate": can_generate,
        "generate_blocked_reason": None if can_generate else "可用知识不足 3 条，请先在知识管理中导入或确认相关资料",
        "min_atoms_for_generation": SCENE_MIN_ATOMS_FOR_GENERATION,
    }
