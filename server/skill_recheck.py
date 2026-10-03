"""
知行有策 - M02 原子变更复核（PRD R4、R5、R7、FR14，FR02 中的 M01-C4）

- 触发（R5）：M01 在原子新版本生效、停用、删除或排除、权限收紧、来源文件换版时调用 notify_atom_change；
  有效期到期没有显式事件，采用「服务启动时 + 定时扫描」（scan_stale_references），同一扫描也补记
  因钩子异常而漏记的失效。钩子在 SAVEPOINT 中执行，出错只回滚钩子自己的写入，M01 原有行为和返回不变。
- 影响：已通过 -> 待复核并写 stale_reason；待审核 -> 状态不变，记录变更提示，处理后才能通过；
  已驳回、生成中、校验未通过 -> 不处理。系统不自动改写 Skill 内容。
- 权限（R4）：权限收紧时按引用原子的当前权限重新推导可见范围，只收紧不放宽。
- 双向可查（R7）：count_skill_references 供 M01 删除影响评估使用。
- 复核视图与选择（FR14）：atom_changes 汇总受影响知识（旧版本 / 新版本差异、引用它的步骤与字段、可选处理方式）；
  apply_stale_resolutions 把「更新引用 / 确认无影响 / 移除引用」应用到审核内容。
- 资格判断一律经 eligibility.check_knowledge_eligibility。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence

from eligibility import check_knowledge_eligibility
from m02_common import dumps as _dumps, loads as _loads, now_iso as _now, iter_skill_refs
from skill_constants import (
    SKILL_STATUS_LABELS,
    STALE_NOTE_MAX,
    STALE_REMOVE_ONLY_TRIGGERS,
    STALE_RESOLUTION_LABELS,
    STALE_SCAN_INTERVAL_SECONDS,
    STALE_TRIGGER_LABELS,
)
from skill_validation import derive_visibility, load_atom_snapshots

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "system_recheck"
# 需要处理的 Skill 状态；其余（已驳回、生成中、校验未通过）不处理
TRACKED_STATUSES = ("approved", "pending_review", "needs_recheck")
# 资格代码 -> 触发类型；索引重建中、未到生效时间等暂时状态不视为原子变更
_CODE_TRIGGER = {
    "EXPIRED": "expired",
    "NOT_ACTIVE_ITEM_VERSION": "new_version",
    "DOCUMENT_DELETED": "deleted",
    "SOURCE_DOC_VERSION_INACTIVE": "source_replaced",
    "VERSION_NOT_FOUND": "deleted",
}
ATOM_DIFF_FIELDS = (
    ("title", "标题"),
    ("statement", "核心陈述"),
    ("conditions", "适用条件"),
    ("actions", "动作"),
    ("exceptions", "例外"),
    ("metric_definition", "指标口径"),
    ("primary_category", "主分类"),
    ("atom_type", "知识类型"),
    ("valid_from", "生效时间"),
    ("valid_until", "失效时间"),
)


def _system_user(org_id: str) -> Dict[str, Any]:
    """资格判断只用到企业、角色与账号状态；复核以管理员口径判断原子是否仍可引用。"""
    return {"id": SYSTEM_ACTOR, "organization_id": org_id, "role": "admin", "account_status": "active"}


def _write_audit(conn, org_id: str, user_id: str, action: str, target_id: str, details: Dict[str, Any],
                 now_iso: str) -> None:
    conn.execute(
        """INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
           VALUES (?, ?, ?, ?, 'skill', ?, ?, ?)""",
        (f"aud_{uuid.uuid4().hex[:12]}", org_id, user_id, action, target_id, _dumps(details), now_iso),
    )


# ---------------------------------------------------------------------------
# 资格分类
# ---------------------------------------------------------------------------

def classify_atom(conn, org_id: str, atom_version_id: str, now_iso: Optional[str] = None) -> Optional[str]:
    """原子版本当前不能再被引用的原因（触发类型）；仍可引用或只是暂时状态时返回 None。"""
    result = check_knowledge_eligibility(conn, atom_version_id, _system_user(org_id), now_iso)
    if result.is_eligible:
        return None
    code = result.code
    if code in ("ITEM_LIFECYCLE_NOT_ACTIVE", "ITEM_EXCLUDED_OR_DELETED"):
        row = conn.execute(
            """SELECT ki.lifecycle_status, ki.is_excluded FROM knowledge_versions kv
               JOIN knowledge_items ki ON ki.id = kv.item_id WHERE kv.id = ?""",
            (atom_version_id,),
        ).fetchone()
        if not row:
            return "deleted"
        if row["lifecycle_status"] == "deleted":
            return "deleted"
        if int(row["is_excluded"] or 0) == 1:
            return "excluded"
        return "disabled"
    return _CODE_TRIGGER.get(code)


def _is_eligible(conn, org_id: str, atom_version_id: Optional[str], now_iso: Optional[str] = None) -> bool:
    if not atom_version_id:
        return False
    return check_knowledge_eligibility(conn, atom_version_id, _system_user(org_id), now_iso).is_eligible


# ---------------------------------------------------------------------------
# 引用反查（R7）
# ---------------------------------------------------------------------------

def _current_refs(conn, org_id: str, item_ids: Sequence[str]) -> List[Dict[str, Any]]:
    """引用这些原子条目（任一版本）的 Skill 当前版本引用行。"""
    ids = sorted({i for i in item_ids if i})
    if not ids:
        return []
    rows: List[Dict[str, Any]] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows += [dict(r) for r in conn.execute(
            f"""SELECT sar.skill_id, sar.skill_version_id, sar.atom_item_id, sar.atom_version_id,
                       sar.snapshot_title, s.status, s.current_version_id
                FROM skill_atom_refs sar
                JOIN skills s ON s.id = sar.skill_id AND s.current_version_id = sar.skill_version_id
                WHERE sar.organization_id = ? AND s.organization_id = ? AND sar.atom_item_id IN ({placeholders})""",
            [org_id, org_id, *chunk],
        ).fetchall()]
    return rows


def count_skill_references(conn, org_id: str, item_ids: Sequence[str]) -> Dict[str, Any]:
    """M01 删除影响评估（M01-C4 / R7）：引用这些原子的 Skill 数（按当前版本统计，含各状态）。"""
    rows = _current_refs(conn, org_id, item_ids)
    by_skill: Dict[str, str] = {}
    for r in rows:
        by_skill[r["skill_id"]] = r["status"]
    status_counts: Dict[str, int] = {}
    for status in by_skill.values():
        status_counts[status] = status_counts.get(status, 0) + 1
    names: Dict[str, Optional[str]] = {}
    if by_skill:
        placeholders = ",".join("?" for _ in by_skill)
        for row in conn.execute(
            f"""SELECT s.id, sv.skill_json FROM skills s LEFT JOIN skill_versions sv ON sv.id = s.current_version_id
                WHERE s.id IN ({placeholders})""", list(by_skill)).fetchall():
            names[row["id"]] = (_loads(row["skill_json"], {}) or {}).get("name")
    return {
        "count": len(by_skill),
        "status_counts": {SKILL_STATUS_LABELS.get(k, k): v for k, v in status_counts.items()},
        "skills": [{"skill_id": sid, "name": names.get(sid), "status": st,
                    "status_label": SKILL_STATUS_LABELS.get(st, st)}
                   for sid, st in sorted(by_skill.items(), key=lambda x: names.get(x[0]) or "")][:20],
    }


# ---------------------------------------------------------------------------
# 事件记录与状态变更
# ---------------------------------------------------------------------------

def open_events(conn, skill_id: str, skill_version_id: Optional[str]) -> List[Dict[str, Any]]:
    if not skill_version_id:
        return []
    return [dict(r) for r in conn.execute(
        """SELECT * FROM skill_stale_events WHERE skill_id = ? AND skill_version_id = ? AND resolved_at IS NULL
           ORDER BY created_at""",
        (skill_id, skill_version_id),
    ).fetchall()]


def _stale_reason(conn, skill_id: str, skill_version_id: str) -> Optional[str]:
    parts: List[str] = []
    titles = {r["atom_version_id"]: r["snapshot_title"] for r in conn.execute(
        "SELECT atom_version_id, snapshot_title FROM skill_atom_refs WHERE skill_version_id = ?",
        (skill_version_id,)).fetchall()}
    for ev in open_events(conn, skill_id, skill_version_id):
        text = f"引用知识「{titles.get(ev['atom_version_id']) or ev['atom_version_id']}」{STALE_TRIGGER_LABELS[ev['trigger_type']]}"
        if text not in parts:
            parts.append(text)
    return "；".join(parts) or None


def _rederive_visibility(conn, org_id: str, skill_ids: Iterable[str], actor_id: str, now_iso: str) -> List[str]:
    """R4：按当前版本所引原子的当前权限重新推导可见范围；只收紧，不因推导结果而放宽。"""
    changed: List[str] = []
    for skill_id in sorted(set(skill_ids)):
        skill = conn.execute("SELECT id, visibility, current_version_id FROM skills WHERE id = ? AND organization_id = ?",
                             (skill_id, org_id)).fetchone()
        if not skill or not skill["current_version_id"]:
            continue
        vids = [r["atom_version_id"] for r in conn.execute(
            "SELECT atom_version_id FROM skill_atom_refs WHERE skill_version_id = ?", (skill["current_version_id"],))]
        snaps = load_atom_snapshots(conn, org_id, vids)
        derived = derive_visibility(snaps.values(), has_unresolved=len(snaps) < len(set(vids)))
        if derived == "admin_only" and skill["visibility"] != "admin_only":
            conn.execute("UPDATE skills SET visibility = 'admin_only', updated_at = ? WHERE id = ?", (now_iso, skill_id))
            _write_audit(conn, org_id, actor_id, "skill_visibility_tightened", skill_id,
                         {"from": skill["visibility"], "to": "admin_only"}, now_iso)
            changed.append(skill_id)
    return changed


def _mark(conn, org_id: str, rows: List[Dict[str, Any]], trigger: str, actor_id: str, now_iso: str,
          detail: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    """把一组（Skill 当前版本 x 原子版本）的变更写入事件表并按状态处理。"""
    newly_stale: List[str] = []
    notified: List[str] = []
    touched: Dict[str, str] = {}
    for row in rows:
        status = row["status"]
        if status not in TRACKED_STATUSES:
            continue
        new_vid = None
        if trigger == "new_version":
            active = conn.execute("SELECT active_version_id FROM knowledge_items WHERE id = ?",
                                  (row["atom_item_id"],)).fetchone()
            new_vid = active["active_version_id"] if active else None
            if not new_vid or new_vid == row["atom_version_id"]:
                continue
        effect = "pending_notice" if status == "pending_review" else "needs_recheck"
        cur = conn.execute(
            """INSERT OR IGNORE INTO skill_stale_events (id, organization_id, skill_id, skill_version_id, atom_item_id,
                   atom_version_id, new_atom_version_id, trigger_type, skill_status, effect, detail_json,
                   created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"sse_{uuid.uuid4().hex[:12]}", org_id, row["skill_id"], row["skill_version_id"], row["atom_item_id"],
             row["atom_version_id"], new_vid, trigger, status, effect, _dumps(detail or {}), actor_id, now_iso),
        )
        if cur.rowcount:
            touched[row["skill_id"]] = status
    for skill_id, status in touched.items():
        version_id = conn.execute("SELECT current_version_id FROM skills WHERE id = ?", (skill_id,)).fetchone()[0]
        reason = _stale_reason(conn, skill_id, version_id)
        if status == "approved":
            # 已通过 -> 待复核；轮换令牌，已打开的页面提交时会收到冲突提示
            conn.execute(
                """UPDATE skills SET status = 'needs_recheck', stale_reason = ?, revision_token = ?, updated_at = ?
                   WHERE id = ? AND status = 'approved'""",
                (reason, uuid.uuid4().hex, now_iso, skill_id),
            )
            newly_stale.append(skill_id)
        elif status == "needs_recheck":
            conn.execute("UPDATE skills SET stale_reason = ?, updated_at = ? WHERE id = ?", (reason, now_iso, skill_id))
        else:
            notified.append(skill_id)
        _write_audit(conn, org_id, actor_id, "skill_atom_changed", skill_id,
                     {"trigger": trigger, "skill_status": status,
                      "effect": "needs_recheck" if status != "pending_review" else "pending_notice",
                      **({"source": detail.get("source")} if detail and detail.get("source") else {})}, now_iso)
    return {"needs_recheck": newly_stale, "pending_notice": notified, "touched": list(touched)}


def notify_atom_change(conn, org_id: str, item_ids: Sequence[str], trigger: str, actor_id: Optional[str] = None,
                       detail: Optional[Dict[str, Any]] = None, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """
    M01 状态变更钩子（与 M01 写入同一事务）。在 SAVEPOINT 中执行：出错时只回滚本钩子的写入并记录日志，
    不影响 M01 原有操作（漏记的失效由 scan_stale_references 补记）。
    """
    if trigger not in STALE_TRIGGER_LABELS:
        raise ValueError(f"unknown stale trigger {trigger}")
    now_iso = now_iso or _now()
    actor_id = actor_id or SYSTEM_ACTOR
    savepoint = f"skill_recheck_{uuid.uuid4().hex[:8]}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        rows = _current_refs(conn, org_id, item_ids)
        if trigger == "source_replaced":
            # 文件换版只影响来源为旧文件版本的原子
            rows = [r for r in rows if classify_atom(conn, org_id, r["atom_version_id"], now_iso) == trigger]
        result: Dict[str, Any] = {"needs_recheck": [], "pending_notice": [], "visibility_tightened": []}
        if rows:
            marked = _mark(conn, org_id, rows, trigger, actor_id, now_iso, detail)
            result.update({k: marked[k] for k in ("needs_recheck", "pending_notice")})
            if trigger == "scope_tightened":
                result["visibility_tightened"] = _rederive_visibility(
                    conn, org_id, [r["skill_id"] for r in rows], actor_id, now_iso)
        conn.execute(f"RELEASE {savepoint}")
        return result
    except Exception:
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
        logger.exception("Skill recheck hook failed (trigger=%s)", trigger)
        return {"needs_recheck": [], "pending_notice": [], "visibility_tightened": [], "error": True}


def notify_document_change(conn, org_id: str, document_id: str, trigger: str, actor_id: Optional[str] = None,
                           detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    item_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM knowledge_items WHERE document_id = ? AND organization_id = ?", (document_id, org_id))]
    return notify_atom_change(conn, org_id, item_ids, trigger, actor_id,
                              {**(detail or {}), "source": "document", "document_id": document_id})


def resolve_events(conn, skill_id: str, user_id: str, record_id: Optional[str],
                   resolution: str, now_iso: str) -> None:
    """复核处理、退回重生成完成、驳回后，关闭该 Skill 所有未处理的变更记录。"""
    conn.execute(
        """UPDATE skill_stale_events SET resolved_at = ?, resolved_by = ?, resolved_record_id = ?, resolution = ?
           WHERE skill_id = ? AND resolved_at IS NULL""",
        (now_iso, user_id, record_id, resolution, skill_id),
    )


# ---------------------------------------------------------------------------
# 有效期到期：启动与定时扫描（同时补记漏记的失效）
# ---------------------------------------------------------------------------

def scan_stale_references(conn, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """
    扫描已通过、待审核、待复核 Skill 当前版本引用的原子：已到期（及钩子漏记的停用、删除、排除、新版本、
    来源换版）则按 R5 处理。每个（Skill 版本, 原子版本, 触发类型）只记一次。
    """
    now_iso = now_iso or _now()
    rows = [dict(r) for r in conn.execute(
        """SELECT sar.organization_id, sar.skill_id, sar.skill_version_id, sar.atom_item_id, sar.atom_version_id,
                  sar.snapshot_title, s.status, s.current_version_id
           FROM skill_atom_refs sar
           JOIN skills s ON s.id = sar.skill_id AND s.current_version_id = sar.skill_version_id
           WHERE s.status IN ('approved', 'pending_review', 'needs_recheck')"""
    ).fetchall()]
    by_trigger: Dict[tuple, List[Dict[str, Any]]] = {}
    cache: Dict[tuple, Optional[str]] = {}
    for row in rows:
        key = (row["organization_id"], row["atom_version_id"])
        if key not in cache:
            cache[key] = classify_atom(conn, row["organization_id"], row["atom_version_id"], now_iso)
        trigger = cache[key]
        if trigger:
            by_trigger.setdefault((row["organization_id"], trigger), []).append(row)
    summary = {"checked_refs": len(rows), "needs_recheck": [], "pending_notice": []}
    for (org_id, trigger), group in by_trigger.items():
        marked = _mark(conn, org_id, group, trigger, SYSTEM_ACTOR, now_iso, {"source": "scan"})
        summary["needs_recheck"] += marked["needs_recheck"]
        summary["pending_notice"] += marked["pending_notice"]
    return summary


_scan_timer: Optional[threading.Timer] = None
_scan_lock = threading.Lock()


def run_scan_once() -> Dict[str, Any]:
    from database import get_db
    try:
        with get_db() as conn:
            return scan_stale_references(conn)
    except Exception:
        logger.exception("Skill stale scan failed")
        return {"error": True}


def start_periodic_scan(interval: int = STALE_SCAN_INTERVAL_SECONDS) -> None:
    """服务启动时扫描一次，之后每隔 interval 秒扫描一次（守护线程，不阻塞退出）。"""
    global _scan_timer

    def tick():
        global _scan_timer
        run_scan_once()
        with _scan_lock:
            _scan_timer = threading.Timer(interval, tick)
            _scan_timer.daemon = True
            _scan_timer.start()

    with _scan_lock:
        if _scan_timer is not None:
            return
        _scan_timer = threading.Timer(0, tick)
        _scan_timer.daemon = True
        _scan_timer.start()


# ---------------------------------------------------------------------------
# 复核视图与复核选择（FR14）
# ---------------------------------------------------------------------------

def _atom_state(conn, org_id: str, version_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """SELECT kv.id, kv.item_id, kv.version_number, kv.title, kv.statement, kv.conditions_json, kv.actions_json,
                  kv.exceptions_json, kv.metric_definition_json, kv.primary_category, kv.atom_type,
                  kv.valid_from, kv.valid_until, kv.reviewed_at
           FROM knowledge_versions kv WHERE kv.id = ? AND kv.organization_id = ?""",
        (version_id, org_id),
    ).fetchone()
    if not row:
        return None
    return {
        "atom_version_id": row["id"],
        "atom_item_id": row["item_id"],
        "version_number": row["version_number"],
        "title": row["title"],
        "statement": row["statement"] or "",
        "conditions": _loads(row["conditions_json"], []) or [],
        "actions": _loads(row["actions_json"], []) or [],
        "exceptions": _loads(row["exceptions_json"], []) or [],
        "metric_definition": _loads(row["metric_definition_json"], None),
        "primary_category": row["primary_category"],
        "atom_type": row["atom_type"],
        "valid_from": row["valid_from"],
        "valid_until": row["valid_until"],
        "reviewed_at": row["reviewed_at"],
    }


def snapshot_from_ref(row) -> Dict[str, Any]:
    """引用行转换为版本快照；审核、复核和历史查看共用。"""
    return {
        "atom_version_id": row["atom_version_id"],
        "atom_item_id": row["atom_item_id"],
        "title": row["snapshot_title"],
        "statement": row["snapshot_statement"] or "",
        "conditions": _loads(row["snapshot_conditions_json"], []) or [],
        "actions": _loads(row["snapshot_actions_json"], []) or [],
        "exceptions": _loads(row["snapshot_exceptions_json"], []) or [],
        "metric_definition": _loads(row["snapshot_metric_definition_json"], None),
        "primary_category": row["snapshot_primary_category"],
        "atom_type": row["snapshot_atom_type"],
        "role": row["role"],
    }


def load_ref_snapshots(conn, org_id: str, skill_version_id: str) -> Dict[str, Dict[str, Any]]:
    return {row["atom_version_id"]: snapshot_from_ref(row) for row in conn.execute(
        "SELECT * FROM skill_atom_refs WHERE skill_version_id = ? AND organization_id = ?",
        (skill_version_id, org_id))}


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for v in value]
    if isinstance(value, dict):
        return [f"{k}：{v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}" for k, v in value.items()]
    return [str(value)]


def atom_version_diff(old: Optional[Dict[str, Any]], new: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """原子旧版本（引用快照 / M01 版本记录）与新版本（M01 版本记录）的字段差异。"""
    if not old or not new:
        return []
    out: List[Dict[str, Any]] = []
    for field, label in ATOM_DIFF_FIELDS:
        a, b = old.get(field), new.get(field)
        if field in ("conditions", "actions", "exceptions", "metric_definition"):
            la, lb = _as_list(a), _as_list(b)
            if la == lb:
                continue
            out.append({"field": field, "label": label, "change": "modified", "list": True,
                        "before": la, "after": lb,
                        "removed": [x for x in la if x not in lb], "added": [x for x in lb if x not in la]})
        else:
            if (a or None) == (b or None) or field not in new:
                continue
            out.append({"field": field, "label": label, "change": "modified", "list": False,
                        "before": a, "after": b})
    return out


def referencing_paths(content: Dict[str, Any], vid: str) -> List[str]:
    """引用某原子版本的步骤与字段（稳定路径）。"""
    paths = (path.rsplit(".", 1)[0] if kind in ("step", "precondition") else path
             for ref_id, path, kind in iter_skill_refs(content) if ref_id == vid)
    return list(dict.fromkeys(paths))


def normalize_stale_resolutions(raw: Any) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for vid, value in raw.items():
        if not isinstance(vid, str) or not isinstance(value, dict):
            continue
        action = value.get("action")
        if action not in STALE_RESOLUTION_LABELS:
            continue
        out[vid] = {"action": action, "note": str(value.get("note") or "").strip()[:STALE_NOTE_MAX]}
    return out


def atom_changes(conn, org_id: str, skill: Dict[str, Any], base_version_id: Optional[str],
                 base_json: Dict[str, Any], now_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    受影响知识清单：当前版本引用的原子中，已不能再引用（实时资格判断）或有未处理变更记录（含权限收紧）的。
    每条给出旧版本（引用快照）与新版本（M01 知识版本记录）的差异，以及可选的复核处理方式。
    """
    if not base_version_id or skill.get("status") not in TRACKED_STATUSES:
        return []
    org_id = skill["organization_id"] if skill.get("organization_id") else org_id
    events_by_vid: Dict[str, List[Dict[str, Any]]] = {}
    for ev in open_events(conn, skill["id"], base_version_id):
        events_by_vid.setdefault(ev["atom_version_id"], []).append(ev)
    snapshots = None
    entries: List[Dict[str, Any]] = []
    for ref in base_json.get("knowledge_refs") or []:
        if not isinstance(ref, dict) or not ref.get("atom_version_id"):
            continue
        vid = ref["atom_version_id"]
        live = classify_atom(conn, org_id, vid, now_iso)
        events = events_by_vid.get(vid, [])
        if not live and not events:
            continue
        if snapshots is None:
            snapshots = load_ref_snapshots(conn, org_id, base_version_id)
        triggers = list(dict.fromkeys([ev["trigger_type"] for ev in events] + ([live] if live else [])))
        old = snapshots.get(vid) or {}
        live_old = _atom_state(conn, org_id, vid)
        if live_old:
            old = {**live_old, **{k: v for k, v in old.items() if v not in (None, "", [])}}
        item_id = ref.get("atom_item_id") or old.get("atom_item_id")
        active = conn.execute("SELECT active_version_id, lifecycle_status, is_excluded FROM knowledge_items WHERE id = ? AND organization_id = ?",
                              (item_id, org_id)).fetchone() if item_id else None
        new_vid = active["active_version_id"] if active and active["active_version_id"] != vid else None
        new_ok = _is_eligible(conn, org_id, new_vid, now_iso) if new_vid else False
        new_state = _atom_state(conn, org_id, new_vid) if new_vid else None
        eligible_now = live is None
        remove_only = any(t in STALE_REMOVE_ONLY_TRIGGERS for t in triggers) and not eligible_now and not new_ok
        options = ["remove"]
        if new_ok:
            options = ["update", "no_impact", "remove"]
        elif eligible_now:
            options = ["no_impact", "remove"]
        entries.append({
            "atom_version_id": vid,
            "atom_item_id": item_id,
            "title": old.get("title") or vid,
            "role": ref.get("role"),
            "triggers": triggers,
            "trigger_labels": [STALE_TRIGGER_LABELS[t] for t in triggers],
            "events": [{"trigger": ev["trigger_type"], "trigger_label": STALE_TRIGGER_LABELS[ev["trigger_type"]],
                        "created_at": ev["created_at"], "effect": ev["effect"]} for ev in events],
            "still_eligible": eligible_now,
            "exists": bool(live_old) and not (active and active["lifecycle_status"] == "deleted"),
            "old_version": old or None,
            "new_version_id": new_vid if new_ok else None,
            "new_version": new_state if new_ok else None,
            "diff": atom_version_diff(old, new_state) if new_ok else [],
            "options": options,
            "option_labels": {o: STALE_RESOLUTION_LABELS[o] for o in options},
            "remove_only": remove_only,
            "affected_paths": referencing_paths(base_json, vid),
        })
    return entries


def apply_stale_resolutions(content: Dict[str, Any], entries: List[Dict[str, Any]],
                            resolutions: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """
    把复核选择应用到审核内容（幂等）：更新引用 / 确认无影响（有新版本时）改指新版本；
    移除引用时同步去掉步骤、前置条件与口径依据中的引用，仅剩该引用的「有原子依据」步骤改为「无依据」，
    按 FR11 处理。确认无影响且原子仍可引用时保留原引用。
    """
    by_vid = {e["atom_version_id"]: e for e in entries}
    for vid, res in resolutions.items():
        entry = by_vid.get(vid)
        if not entry or res["action"] not in entry["options"]:
            continue
        action = res["action"]
        new_vid = entry.get("new_version_id")
        if action in ("update", "no_impact") and new_vid:
            _rewrite_ref(content, vid, new_vid)
        elif action == "remove":
            _rewrite_ref(content, vid, None)
    return content


def _rewrite_ref(content: Dict[str, Any], old: str, new: Optional[str]) -> None:
    """替换或移除所有引用位置，统一保持去重和无依据步骤规则。"""
    refs = [r for r in content.get("knowledge_refs") or [] if isinstance(r, dict)]
    if new is None:
        content["knowledge_refs"] = [r for r in content.get("knowledge_refs") or []
                                     if not (isinstance(r, dict) and r.get("atom_version_id") == old)]
    elif any(r.get("atom_version_id") == new for r in refs):
        content["knowledge_refs"] = [r for r in refs if r.get("atom_version_id") != old]
    else:
        for r in refs:
            if r.get("atom_version_id") == old:
                r["atom_version_id"] = new
    for step in content.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("refs"), list):
            if new is not None:
                step["refs"] = list(dict.fromkeys(new if v == old else v for v in step["refs"]))
            elif old in step["refs"]:
                step["refs"] = [v for v in step["refs"] if v != old]
                if not step["refs"] and step.get("basis") == "有原子依据":
                    step["basis"] = "无依据"
    for pre in content.get("preconditions") or []:
        if isinstance(pre, dict) and pre.get("ref") == old:
            pre["ref"] = new
    for group in ("inputs", "outputs"):
        for io in content.get(group) or []:
            if isinstance(io, dict) and io.get("source_ref") == old:
                io["source_ref"] = new


def evaluate_changes(entries: List[Dict[str, Any]], content: Dict[str, Any],
                     resolutions: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    """每条受影响知识是否已处理：不再被引用（已更新或移除），或确认无影响（须填说明，原子仍可引用）。"""
    referenced = {vid for vid, _path, _kind in iter_skill_refs(content)}
    out = []
    for entry in entries:
        vid = entry["atom_version_id"]
        res = resolutions.get(vid)
        still = vid in referenced
        action = res["action"] if res and res["action"] in entry["options"] else None
        note = (res or {}).get("note") or ""
        problem = None
        if action == "no_impact" and not note:
            problem = "确认无影响须填写说明"
        if still:
            if action == "no_impact" and entry["still_eligible"] and note:
                handled = True
            else:
                handled = False
                if not problem:
                    problem = ("这条知识已不能继续引用，请移除引用、改用新版本，或退回重生成、驳回"
                               if not entry["still_eligible"] else "请选择更新引用、确认无影响或移除引用")
        else:
            handled = not problem
            if action is None:
                # 审核人直接在编辑中删掉或替换了引用
                action = "update" if entry.get("new_version_id") and entry["new_version_id"] in referenced else "remove"
        out.append({**entry, "still_referenced": still, "resolution": action,
                    "resolution_label": STALE_RESOLUTION_LABELS.get(action) if action else None,
                    "note": note, "handled": handled, "problem": None if handled else problem})
    return out


def resolution_summary(changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"atom_version_id": c["atom_version_id"], "atom_item_id": c.get("atom_item_id"), "title": c["title"],
             "triggers": c["triggers"], "trigger_labels": c["trigger_labels"], "resolution": c["resolution"],
             "resolution_label": c["resolution_label"], "note": c["note"] or None,
             "new_version_id": c.get("new_version_id")} for c in changes]


def version_ref_snapshots(conn, org_id: str, skill_version_id: str) -> List[Dict[str, Any]]:
    """AC17：Skill 任一历史版本的引用快照（原子之后被删除也能看到当时依据的内容），附原子当前状态。"""
    out = []
    for row in conn.execute("""SELECT sar.*, ki.id AS current_item_id, ki.lifecycle_status AS current_lifecycle_status
                               FROM skill_atom_refs sar LEFT JOIN knowledge_items ki
                                 ON ki.id = sar.atom_item_id AND ki.organization_id = sar.organization_id
                               WHERE sar.skill_version_id = ? AND sar.organization_id = ? ORDER BY sar.created_at""",
                            (skill_version_id, org_id)).fetchall():
        snap = snapshot_from_ref(row)
        trigger = classify_atom(conn, org_id, row["atom_version_id"])
        out.append({**snap, "current_state": trigger or "eligible",
                    "current_state_label": STALE_TRIGGER_LABELS.get(trigger, "仍可引用") if trigger else "仍可引用",
                    "item_deleted": row["current_lifecycle_status"] == "deleted" or row["current_item_id"] is None})
    return out
