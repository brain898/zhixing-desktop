"""
知行有策 - M02 Skill 审核（PRD 第 7 章状态流转、第 8 章 FR09~FR13）

- 审核人即管理员（S12），每个动作记录实际操作人；全部接口由 main.py 以 require_admin 保护。
- 状态流转集中在 TRANSITIONS，后端拒绝表外的流转；「待复核」相关流转（FR14）由 M02-E 补入，
  原子变更的触发、记录与复核视图见 skill_recheck.py。
- 编辑先保存为草稿（skill_review_drafts），草稿不是版本；只有审核动作提交时才产生版本。
  乐观锁沿用 M01 revision_token 做法：skills.revision_token 在草稿保存与每个审核动作时校验并轮换，
  后提交者收到冲突提示，不覆盖先提交的内容（AC19）。
- 通过门槛（FR12）全部在后端检查：审核清单、TBD_EXPERT、无依据项处理、R1、结构校验（M02-A 校验器）。
- 退回重生成复用 M02-C 预留的 skill_generation.regenerate_skill_version，在独立任务表
  skill_review_tasks 中后台执行（复用 tasks.py 线程池与启动恢复）。审核环节其余动作不调用模型（10.2）。
- 资格判断一律经 eligibility.py（由 skill_validation.validate_skill 与 filter_eligible_version_ids 调用）。
"""

from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

import scene_catalog as sc
import skill_generation as sg
import skill_recheck as rc
from eligibility import filter_eligible_version_ids
from m02_common import dumps as _dumps, loads as _loads, now_iso as _now
from skill_constants import (
    FIELD_GROUP_LABELS,
    REGENERATE_FIELD_GROUPS,
    REJECT_NOTE_MAX,
    REJECT_REASONS,
    REVIEW_ACTION_LABELS,
    REVIEW_CHECKLIST,
    REVIEW_COMMENT_MAX,
    SKILL_SCHEMA_VERSION,
    SKILL_STATUS_LABELS,
    SKILL_VERSION_KIND_LABELS,
    TBD_EXPERT,
    UNSUPPORTED_RESOLUTION_LABELS,
)
from skill_diff import attach_reasons, describe_path, diff_skill, render_export_markdown, summarize_diffs
from skill_validation import (
    STAGE_REVIEW,
    UNSUPPORTED_EXCEPTION,
    UNSUPPORTED_NUMBER,
    UNSUPPORTED_STEP,
    content_key,
    load_atom_snapshots,
    validate_skill,
)

logger = logging.getLogger(__name__)

# A~E 组中审核人可编辑的字段（FR11）。generation_confidence 是模型对「忠实于所引原子」的自评，
# 审核人修改后就不再是模型自评，因此保持只读；unsupported_items 由系统汇总。
EDITABLE_FIELDS = (
    "name", "goal", "trigger_description", "task_type", "applies_to", "not_applies_to",
    "knowledge_refs", "inputs", "preconditions", "outputs", "output_template",
    "steps", "risk_boundary", "escalation_conditions",
)

# PRD 7.2 状态流转。键为动作，值为 {当前状态: 结果状态}。
# 「已通过 -> 待复核」由原子变更触发（skill_recheck.notify_atom_change / scan_stale_references），不是审核动作。
TRANSITIONS: Dict[str, Dict[str, str]] = {
    "save_draft": {"pending_review": "pending_review", "needs_recheck": "needs_recheck"},
    "approve": {"pending_review": "approved"},              # 通过 / 修改后通过：按是否存在差异记录动作类型
    "regenerate": {"pending_review": "generating",          # 退回重生成
                   "validation_failed": "generating",       # 校验未通过 -> 重新生成
                   "needs_recheck": "generating"},          # 待复核 -> 退回重生成（FR14，完成后回到待审核）
    "reject": {"pending_review": "rejected",                # 驳回
               "needs_recheck": "rejected"},                # 待复核 -> 驳回（FR14）
    "abandon": {"validation_failed": "rejected"},           # 校验未通过 -> 放弃
    "restore": {"rejected": "pending_review"},              # 恢复（没有版本的放弃候选回到校验未通过）
    "recheck": {"needs_recheck": "approved"},               # 待复核 -> 复核处理（更新引用 / 确认无影响 / 移除引用）
}
ACTION_TEXT = {
    "save_draft": "保存修改",
    "approve": "通过",
    "regenerate": "重新生成",
    "reject": "驳回",
    "abandon": "放弃",
    "restore": "恢复",
    "recheck": "提交复核",
}

CONFIDENCE_ORDER = {"低": 0, "中": 1, "高": 2}
STATUS_ORDER = {"needs_recheck": 0, "pending_review": 1, "generating": 2, "validation_failed": 3,
                "approved": 4, "rejected": 5}

R1_CODES = ("REF_NOT_ELIGIBLE", "REF_ATOM_NOT_FOUND")
_STEP_NUM = re.compile(r"^s(\d+)$")


class ReviewError(sc.SceneCatalogError):
    def __init__(self, status_code: int, message: str, code: Optional[str] = None,
                 extra: Optional[Dict[str, Any]] = None):
        super().__init__(status_code, message)
        self.code = code
        self.extra = extra or {}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _new_token() -> str:
    return uuid.uuid4().hex


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _user_names(conn, org_id: str) -> Dict[str, str]:
    rows = conn.execute("SELECT id, display_name, username FROM users WHERE organization_id = ?", (org_id,)).fetchall()
    return {r["id"]: r["display_name"] or r["username"] for r in rows}


def _load_skill(conn, org_id: str, skill_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM skills WHERE id = ? AND organization_id = ?", (skill_id, org_id)).fetchone()
    if not row:
        raise ReviewError(404, "Skill 不存在")
    skill = dict(row)
    if not skill.get("revision_token"):
        skill["revision_token"] = _new_token()
        conn.execute("UPDATE skills SET revision_token = ? WHERE id = ?", (skill["revision_token"], skill_id))
    return skill


def _version_row(conn, version_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not version_id:
        return None
    row = conn.execute("SELECT * FROM skill_versions WHERE id = ?", (version_id,)).fetchone()
    return dict(row) if row else None


def check_transition(action: str, status: str) -> str:
    allowed = TRANSITIONS.get(action) or {}
    if status not in allowed:
        label = SKILL_STATUS_LABELS.get(status, status)
        raise ReviewError(409, f"「{label}」的 Skill 不能{ACTION_TEXT.get(action, action)}", "ILLEGAL_TRANSITION")
    return allowed[status]


def check_token(skill: Dict[str, Any], token: Any) -> None:
    if not token:
        raise ReviewError(400, "缺少 revision_token，无法执行并发安全的提交")
    if token != skill["revision_token"]:
        raise ReviewError(
            409, "这条 Skill 刚被其他人修改或审核过，请刷新后再操作；你的修改没有覆盖对方的内容",
            "REVISION_CONFLICT",
        )


def _rotate(conn, skill_id: str, now_iso: str, **fields) -> str:
    token = _new_token()
    sets = ["revision_token = ?", "updated_at = ?"]
    params: List[Any] = [token, now_iso]
    for key, value in fields.items():
        sets.append(f"{key} = ?")
        params.append(value)
    params.append(skill_id)
    conn.execute(f"UPDATE skills SET {', '.join(sets)} WHERE id = ?", params)
    return token


def _audit(conn, user: Dict[str, Any], action: str, skill_id: str, details: Dict[str, Any], now_iso: str) -> None:
    sc.write_audit(conn, user["organization_id"], user["id"], action, "skill", skill_id, details, now_iso)


# ---------------------------------------------------------------------------
# 审核内容
# ---------------------------------------------------------------------------

def strip_system_fields(skill_json: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """去掉 F 组与系统汇总字段，保留 maintainer（G 组中审核时可填的字段）与其余预留字段。"""
    if not isinstance(skill_json, dict):
        return {}
    data = copy.deepcopy(skill_json)
    maintainer = data.get("maintainer")
    for key in sg.SYSTEM_FIELDS:
        data.pop(key, None)
    if isinstance(maintainer, str) and maintainer.strip():
        data["maintainer"] = maintainer.strip()
    return data


def _sync_used_in_steps(content: Dict[str, Any]) -> None:
    """使用步骤由系统按步骤引用维护，保证与 steps.refs 双向一致（3.4）。"""
    steps = [s for s in content.get("steps") or [] if isinstance(s, dict)]
    for step in steps:
        if step.get("basis") != "专家补充":
            step.pop("expert_reason", None)
    for ref in content.get("knowledge_refs") or []:
        if not isinstance(ref, dict):
            continue
        vid = ref.get("atom_version_id")
        ref["used_in_steps"] = [s.get("step_id") for s in steps
                                if isinstance(s.get("refs"), list) and vid in s.get("refs") and s.get("step_id")]


def build_review_content(base_json: Optional[Dict[str, Any]], client_json: Optional[Dict[str, Any]],
                         scene_id: str) -> Dict[str, Any]:
    """合成审核中的 Skill 内容：以当前版本为底，只采用可编辑字段；系统字段一律由系统填写（FR11）。"""
    content = strip_system_fields(base_json)
    if isinstance(client_json, dict):
        for key in EDITABLE_FIELDS:
            if key in client_json:
                content[key] = copy.deepcopy(client_json[key])
        if "maintainer" in client_json:
            maintainer = client_json.get("maintainer")
            if isinstance(maintainer, str) and maintainer.strip():
                content["maintainer"] = maintainer.strip()[:50]
            else:
                content.pop("maintainer", None)
        if content.get("output_template") in ("",):
            content.pop("output_template", None)
    content["schema_version"] = SKILL_SCHEMA_VERSION
    content["scene_id"] = scene_id
    # Schema 允许省略步骤的 refs（如通用操作步骤），审核界面按空数组处理
    for step in content.get("steps") or []:
        if isinstance(step, dict) and step.get("refs") is None:
            step["refs"] = []
    _sync_used_in_steps(content)
    return content


def _prepare(conn, user: Dict[str, Any], skill: Dict[str, Any], version: Optional[Dict[str, Any]],
             base_json: Dict[str, Any], client_json: Optional[Dict[str, Any]], stale_raw: Any):
    """合成审核内容并应用原子变更的复核选择（FR14）；返回 (内容, 受影响知识的处理情况, 复核选择)。"""
    content = build_review_content(base_json, client_json, skill["scene_id"])
    entries = rc.atom_changes(conn, user["organization_id"], skill, version["id"] if version else None, base_json)
    stale = rc.normalize_stale_resolutions(stale_raw)
    if entries and stale:
        rc.apply_stale_resolutions(content, entries, stale)
        _sync_used_in_steps(content)
    return content, rc.evaluate_changes(entries, content, stale), stale


def find_tbd_paths(value: Any, path: str = "") -> List[str]:
    """内容中仍为 TBD_EXPERT 的位置（稳定路径）。"""
    found: List[str] = []
    if isinstance(value, str):
        if value.strip() == TBD_EXPERT:
            found.append(path)
    elif isinstance(value, dict):
        for key, sub in value.items():
            found += find_tbd_paths(sub, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        id_field = {"steps": "step_id", "inputs": "key", "outputs": "key", "knowledge_refs": "atom_version_id"}.get(path)
        for item in value:
            if id_field and isinstance(item, dict) and isinstance(item.get(id_field), str):
                key = item[id_field]
            else:
                key = content_key(item)
            found += find_tbd_paths(item, f"{path}[{key}]")
    return found


def _normalize_resolutions(raw: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        action = value.get("action")
        if action not in UNSUPPORTED_RESOLUTION_LABELS:
            continue
        entry = {"action": action, "reason": _clean_text(value.get("reason"), 500)}
        out[key] = entry
    return out


def _normalize_checklist(raw: Any) -> Dict[str, bool]:
    raw = raw if isinstance(raw, dict) else {}
    return {key: bool(raw.get(key)) for key, _label in REVIEW_CHECKLIST}


def _resolve_text(content: Dict[str, Any], path: str) -> Optional[str]:
    """按稳定路径取文本（R6 数值所在位置）；位置已不存在时返回 None。"""
    m = re.match(r"^(\w+)(?:\[([^\]]+)\])?(?:\.(\w+))?$", path or "")
    if not m:
        return None
    root, key, prop = m.groups()
    value = content.get(root)
    if key is not None:
        if not isinstance(value, list):
            return None
        id_field = {"steps": "step_id", "inputs": "key", "outputs": "key"}.get(root)
        match = None
        for item in value:
            if key.startswith("~"):
                if content_key(item) == key:
                    match = item
                    break
            elif id_field and isinstance(item, dict) and item.get(id_field) == key:
                match = item
                break
        value = match
    if prop is not None:
        value = value.get(prop) if isinstance(value, dict) else None
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return value if isinstance(value, str) else None


def _find_step(content: Dict[str, Any], sid: str) -> Optional[Dict[str, Any]]:
    for step in content.get("steps") or []:
        if isinstance(step, dict) and step.get("step_id") == sid:
            return step
    return None


def classify_unsupported(base_items: List[Dict[str, Any]], current_items: List[Dict[str, Any]],
                         resolutions: Dict[str, Dict[str, Any]], content: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    FR11 无依据项处理：原有项与编辑后仍存在的项逐项给出处理结果。
    - 编辑后仍被校验器列出的项：只有「标为专家补充并填写理由」才算已处理；
      无依据步骤须把步骤依据改为「专家补充」（R3），不能只在面板中标注。
    - 编辑后消失的项：按编辑结果推断为删除、补依据、专家补充、已补落点或修改后消除。
    """
    current_by_key = {i.get("item_key"): i for i in current_items if i.get("item_key")}
    refs_now = {r.get("atom_version_id") for r in content.get("knowledge_refs") or [] if isinstance(r, dict)}
    seen = set()
    results: List[Dict[str, Any]] = []
    for item in list(base_items) + list(current_items):
        key = item.get("item_key")
        if not key or key in seen:
            continue
        seen.add(key)
        explicit = resolutions.get(key) or {}
        action, reason = None, None
        if key in current_by_key:
            item = current_by_key[key]
            if item.get("kind") != UNSUPPORTED_STEP and explicit.get("action") == "expert" and explicit.get("reason"):
                action, reason = "expert", explicit["reason"]
        elif item.get("kind") == UNSUPPORTED_STEP:
            step = _find_step(content, key.split(":", 1)[1])
            if step is None:
                action = "delete"
            elif step.get("basis") == "专家补充":
                action, reason = "expert", step.get("expert_reason")
            elif step.get("refs"):
                action = "add_ref"
            else:
                action = "edited"
        elif item.get("kind") == UNSUPPORTED_NUMBER:
            text = _resolve_text(content, item.get("path") or "")
            sid_match = re.match(r"^steps\[([^\]]+)\]", item.get("path") or "")
            step = _find_step(content, sid_match.group(1)) if sid_match else None
            if text is None or (item.get("value") and item["value"] not in text):
                action = "delete"
            elif step is not None and step.get("basis") == "专家补充":
                action, reason = "expert", step.get("expert_reason")
            else:
                action = "add_ref"
        elif item.get("kind") == UNSUPPORTED_EXCEPTION:
            action = "delete" if item.get("atom_version_id") not in refs_now else "landed"
        else:
            action = "edited"
        results.append({
            **{k: item.get(k) for k in ("kind", "path", "item_key", "value", "atom_version_id", "message")},
            "label": describe_path(item.get("path") or ""),
            "still_present": key in current_by_key,
            "resolution": action,
            "resolution_label": UNSUPPORTED_RESOLUTION_LABELS.get(action) if action else None,
            "reason": reason,
        })
    return results


def resolution_counts(items: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        action = item.get("resolution")
        if action:
            counts[action] = counts.get(action, 0) + 1
    return counts


def _historical_step_ids(conn, skill_id: str) -> set:
    """只收集已入库版本的编号，用于禁止历史编号复用。"""
    used = set()
    for row in conn.execute("SELECT skill_json FROM skill_versions WHERE skill_id = ?", (skill_id,)).fetchall():
        for step in (_loads(row["skill_json"], {}) or {}).get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("step_id"), str):
                used.add(step["step_id"])
    return used


def _used_step_numbers(conn, skill_id: str, historical_ids: Optional[set] = None) -> Tuple[set, int]:
    """分配编号还需避开本轮草稿中的步骤，草稿不属于历史复用检查。"""
    used = set(_historical_step_ids(conn, skill_id) if historical_ids is None else historical_ids)
    draft = conn.execute("SELECT content_json FROM skill_review_drafts WHERE skill_id = ?", (skill_id,)).fetchone()
    if draft:
        for step in (_loads(draft["content_json"], {}) or {}).get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("step_id"), str):
                used.add(step["step_id"])
    numbers = [int(m.group(1)) for m in (_STEP_NUM.match(s) for s in used) if m]
    return used, (max(numbers) if numbers else 0) + 1


def evaluate_content(conn, user: Dict[str, Any], skill: Dict[str, Any], base_json: Dict[str, Any],
                     content: Dict[str, Any], resolutions: Dict[str, Dict[str, Any]],
                     checklist: Dict[str, bool], changes: Optional[List[Dict[str, Any]]] = None,
                      require_checklist: bool = True, historical_ids: Optional[set] = None) -> Dict[str, Any]:
    """通过门槛（FR12）与编辑后的校验结果；changes 为受影响知识的处理情况（FR14）。只读，不写库。"""
    report = validate_skill(conn, content, user, pool_ids=None, stage=STAGE_REVIEW)
    issues = [i.to_dict() for i in report.issues]
    for issue in issues:
        issue["plain"] = sg.plain_issue(issue)
        issue["label"] = describe_path(issue.get("path") or "") if issue.get("path") else "整体"
    hard = [i for i in issues if i["level"] == "hard_error"]
    hints = [i for i in issues if i["level"] == "hint"]
    base_items = [i for i in (base_json.get("unsupported_items") or []) if isinstance(i, dict)]
    items = classify_unsupported(base_items, report.unsupported_items, resolutions, content)
    base_content = strip_system_fields(base_json)
    diffs = diff_skill(base_content, content)

    blockers: List[Dict[str, Any]] = []
    changes = changes or []
    unchecked = [label for key, label in REVIEW_CHECKLIST if not checklist.get(key)] if require_checklist else []
    if unchecked:
        blockers.append({"code": "CHECKLIST", "message": f"审核清单还有 {len(unchecked)} 项没有勾选",
                         "details": unchecked})
    tbd = find_tbd_paths(content)
    if tbd:
        blockers.append({"code": "TBD_EXPERT", "message": f"还有 {len(tbd)} 处「待专家补充」没有替换",
                         "details": [describe_path(p) for p in tbd], "paths": tbd})
    unresolved = [i for i in items if i["still_present"] and not i["resolution"]]
    if unresolved:
        blockers.append({
            "code": "UNSUPPORTED_UNRESOLVED",
            "message": f"还有 {len(unresolved)} 个无依据项没有处理（删除、补充依据或标为专家补充并填写理由）",
            "details": [f"{i['label']}：{i['message']}" for i in unresolved],
            "item_keys": [i["item_key"] for i in unresolved],
        })
    unhandled = [c for c in changes if not c["handled"]]
    if unhandled:
        blockers.append({
            "code": "ATOM_CHANGE_UNHANDLED",
            "message": f"有 {len(unhandled)} 条依据知识发生了变化，还没有处理",
            "details": [f"「{c['title']}」{'、'.join(c['trigger_labels'])}：{c['problem']}" for c in unhandled],
            "atom_version_ids": [c["atom_version_id"] for c in unhandled],
        })
    # 已在「知识变更」中列出的引用不再重复作为 R1 问题提示
    changed_vids = {c["atom_version_id"] for c in unhandled}
    r1 = [i for i in hard if i["code"] in R1_CODES
          and not any(v in (i.get("path") or "") or v in (i.get("message") or "") for v in changed_vids)]
    if r1:
        blockers.append({"code": "REF_NOT_ELIGIBLE", "message": f"有 {len(r1)} 处引用的知识已停用、失效或被修改",
                         "details": [f"{i['label']}：{i['plain']}" for i in r1]})
    structural = [i for i in hard if i["code"] not in R1_CODES]
    if structural:
        blockers.append({"code": "STRUCTURE", "message": f"修改后的内容有 {len(structural)} 处没有通过格式检查",
                         "details": [f"{i['label']}：{i['message']}" for i in structural]})
    # 新增步骤不能使用本 Skill 任一历史版本用过、当前版本已没有的编号（草稿内的编号属于同一轮编辑，不算复用）
    base_ids = {s.get("step_id") for s in base_content.get("steps") or [] if isinstance(s, dict)}
    version_ids_used = _historical_step_ids(conn, skill["id"]) if historical_ids is None else historical_ids
    reused = sorted(s.get("step_id") for s in content.get("steps") or []
                    if isinstance(s, dict) and s.get("step_id") not in base_ids
                    and s.get("step_id") in version_ids_used)
    if reused:
        blockers.append({"code": "STEP_ID_REUSED", "message": "新增步骤使用了以前用过的编号，步骤编号不能复用",
                         "details": reused})

    return {
        "can_approve": not blockers,
        "blockers": blockers,
        "has_changes": bool(diffs),
        "approve_label": REVIEW_ACTION_LABELS["approve_with_changes" if diffs else "approve"],
        "diffs": diffs,
        "summary": summarize_diffs(diffs, resolution_counts(items)),
        "issues": issues,
        "hint_count": len(hints),
        "hard_error_count": len(hard),
        "unsupported_items": items,
        "visibility": report.visibility,
        "atom_changes": changes,
        "_report": report,
    }


def public_evaluation(ev: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in ev.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# 候选列表（FR09）
# ---------------------------------------------------------------------------

def _changed_atom_versions(conn, user: Dict[str, Any], version_ids: List[str]) -> set:
    """当前已不满足正式资格（R1）的原子版本：停用、删除、排除、过期或已有新版本生效。"""
    ids = sorted({v for v in version_ids if v})
    if not ids:
        return set()
    eligible = set()
    for i in range(0, len(ids), 500):
        eligible.update(filter_eligible_version_ids(conn, ids[i:i + 500], user))
    return set(ids) - eligible


def list_skills(conn, user: Dict[str, Any], *, scene_id: Optional[str] = None, status: Optional[str] = None,
                batch_id: Optional[str] = None, has_unsupported: Optional[str] = None,
                needs_recheck: bool = False) -> Dict[str, Any]:
    org_id = user["organization_id"]
    rows = [dict(r) for r in conn.execute(
        """SELECT s.id, s.scene_id, s.batch_id, s.task_key, s.status, s.similar_skills_json, s.generation_error_json,
                  s.updated_at, s.created_at, s.current_version_id, s.stale_reason, s.visibility,
                  sv.skill_json, sv.version_number, sc.name AS scene_name, b.initiated_at AS batch_initiated_at
           FROM skills s
           LEFT JOIN skill_versions sv ON sv.id = s.current_version_id
           LEFT JOIN scenes sc ON sc.id = s.scene_id AND sc.organization_id = s.organization_id
           LEFT JOIN skill_generation_batches b ON b.id = s.batch_id
           WHERE s.organization_id = ?""",
        (org_id,),
    ).fetchall()]
    current_ids = [r["current_version_id"] for r in rows if r["current_version_id"]]
    refs_by_version: Dict[str, List[str]] = {}
    for i in range(0, len(current_ids), 500):
        chunk = current_ids[i:i + 500]
        placeholders = ",".join("?" for _ in chunk)
        for ref in conn.execute(
            f"SELECT skill_version_id, atom_version_id FROM skill_atom_refs WHERE skill_version_id IN ({placeholders})",
            chunk,
        ).fetchall():
            refs_by_version.setdefault(ref["skill_version_id"], []).append(ref["atom_version_id"])
    changed = _changed_atom_versions(conn, user, [v for vs in refs_by_version.values() for v in vs])
    regen = {r["skill_id"]: r["n"] for r in conn.execute(
        """SELECT skill_id, COUNT(*) AS n FROM skill_versions WHERE organization_id = ? AND version_kind = 'ai_regenerated'
           GROUP BY skill_id""", (org_id,)).fetchall()}
    drafts = {r["skill_id"] for r in conn.execute(
        "SELECT skill_id FROM skill_review_drafts WHERE organization_id = ?", (org_id,)).fetchall()}
    # 未处理的原子变更记录（含权限收紧等实时资格判断看不出的变化），只算 Skill 当前版本上的
    stale_events = {(r["skill_id"], r["skill_version_id"]) for r in conn.execute(
        "SELECT skill_id, skill_version_id FROM skill_stale_events WHERE organization_id = ? AND resolved_at IS NULL",
        (org_id,)).fetchall()}

    items = []
    for row in rows:
        summary = sg._candidate_summary(row)
        refs = refs_by_version.get(row["current_version_id"] or "", [])
        summary.update({
            "scene_id": row["scene_id"],
            "scene_name": row["scene_name"],
            "batch_initiated_at": row["batch_initiated_at"],
            "created_at": row["created_at"],
            "atom_changed": row["status"] in ("pending_review", "approved", "needs_recheck")
                            and (any(v in changed for v in refs)
                                 or (row["id"], row["current_version_id"]) in stale_events),
            "stale_reason": row["stale_reason"],
            "visibility": row["visibility"],
            "regenerate_count": regen.get(row["id"], 0),
            "has_draft": row["id"] in drafts,
        })
        items.append(summary)

    def keep(item):
        if scene_id and item["scene_id"] != scene_id:
            return False
        if status and status != "all" and item["status"] != status:
            return False
        if batch_id and item["batch_id"] != batch_id:
            return False
        if has_unsupported == "yes" and not (item["unsupported_count"] or 0):
            return False
        if has_unsupported == "no" and (item["unsupported_count"] or 0):
            return False
        if needs_recheck and item["status"] != "needs_recheck":
            return False
        return True

    filtered = sorted((i for i in items if keep(i)), key=_list_sort_key)
    batches = sorted({(i["batch_id"], i["batch_initiated_at"], i["scene_name"]) for i in items if i["batch_id"]},
                     key=lambda x: x[1] or "", reverse=True)
    counts: Dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "items": filtered,
        "total": len(filtered),
        "status_counts": counts,
        "filters": {
            "scenes": sorted({(i["scene_id"], i["scene_name"] or "") for i in items}, key=lambda x: x[1]),
            "batches": [{"batch_id": b, "initiated_at": at, "scene_name": name} for b, at, name in batches],
        },
    }


def _list_sort_key(item: Dict[str, Any]):
    """FR09 默认排序：待复核优先，其次待审核按生成把握度从低到高，其余按更新时间新的在前。"""
    status_rank = STATUS_ORDER.get(item["status"], 9)
    confidence = CONFIDENCE_ORDER.get(item.get("confidence_level") or "", -1)
    updated = item.get("updated_at") or ""
    inverted = "".join(chr(0x10FFFF - ord(c)) for c in updated)
    return (status_rank, confidence if item["status"] == "pending_review" else 0, inverted)


def next_pending_skill(conn, user: Dict[str, Any], exclude_skill_id: str,
                       queue: Optional[List[str]] = None) -> Optional[str]:
    """提交后跳到下一条待审核候选：优先按页面传入的列表顺序，其次按 FR09 默认排序。"""
    listing = list_skills(conn, user, status="pending_review")["items"]
    pending = [i["skill_id"] for i in listing if i["skill_id"] != exclude_skill_id]
    if queue:
        pending_set = set(pending)
        if exclude_skill_id in queue:
            idx = queue.index(exclude_skill_id)
            ordered = queue[idx + 1:] + queue[:idx]
        else:
            ordered = list(queue)
        for sid in ordered:
            if sid in pending_set:
                return sid
    return pending[0] if pending else None


# ---------------------------------------------------------------------------
# 审核工作台（FR10）
# ---------------------------------------------------------------------------

def _base_json(conn, skill: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """当前版本行与内容；没有版本（校验未通过）时取最后一次候选。"""
    version = _version_row(conn, skill.get("current_version_id"))
    if version:
        return version, _loads(version["skill_json"], {}) or {}
    error = _loads(skill.get("generation_error_json"), {}) or {}
    candidate = error.get("candidate") if isinstance(error.get("candidate"), dict) else {}
    return None, candidate


def _load_draft(conn, skill: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM skill_review_drafts WHERE skill_id = ?", (skill["id"],)).fetchone()
    return dict(row) if row else None


def _pool_sources(conn, skill: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    batch = conn.execute("SELECT atom_pool_json FROM skill_generation_batches WHERE id = ?",
                         (skill.get("batch_id") or "",)).fetchone()
    pool = _loads(batch["atom_pool_json"] if batch else None, {}) or {}
    return {a["atom_version_id"]: a for a in pool.get("atoms") or [] if isinstance(a, dict) and a.get("atom_version_id")}


def _atom_panel(conn, user: Dict[str, Any], skill: Dict[str, Any], version: Optional[Dict[str, Any]],
                content: Dict[str, Any], issues: List[Dict[str, Any]],
                pool: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """左栏：所引原子（含引用快照、结构字段、资格状态、召回来源、例外未落点）。"""
    org_id = user["organization_id"]
    snapshots = rc.load_ref_snapshots(conn, org_id, version["id"]) if version else {}
    refs = [r for r in content.get("knowledge_refs") or [] if isinstance(r, dict)]
    ids = [r.get("atom_version_id") for r in refs if r.get("atom_version_id")]
    live = load_atom_snapshots(conn, org_id, ids)
    extra = conn.execute(
        f"""SELECT kv.id, kv.version_number, kv.subject, kv.case_details_json, ki.active_version_id
            FROM knowledge_versions kv JOIN knowledge_items ki ON ki.id = kv.item_id
            WHERE kv.organization_id = ? AND kv.id IN ({','.join('?' for _ in ids) or "''"})""",
        [org_id, *ids],
    ).fetchall() if ids else []
    extra_by_id = {r["id"]: r for r in extra}
    eligible = set(filter_eligible_version_ids(conn, ids, user)) if ids else set()
    pool = _pool_sources(conn, skill) if pool is None else pool
    not_landed: Dict[str, List[str]] = {}
    for issue in issues:
        if issue.get("code") == "EXCEPTION_NOT_LANDED":
            m = re.match(r"^knowledge_refs\[([^\]]+)\]", issue.get("path") or "")
            if m:
                not_landed.setdefault(m.group(1), []).append((issue.get("detail") or {}).get("exception") or "")
    atoms = []
    for ref in refs:
        vid = ref.get("atom_version_id")
        snap = snapshots.get(vid) or live.get(vid) or {}
        ext = extra_by_id.get(vid)
        pool_entry = pool.get(vid) or {}
        atoms.append({
            "atom_version_id": vid,
            "atom_item_id": ref.get("atom_item_id") or snap.get("atom_item_id"),
            "role": ref.get("role"),
            "used_in_steps": ref.get("used_in_steps") or [],
            "title": snap.get("title") or "（知识已不存在）",
            "statement": snap.get("statement") or "",
            "conditions": snap.get("conditions") or [],
            "actions": snap.get("actions") or [],
            "exceptions": snap.get("exceptions") or [],
            "metric_definition": snap.get("metric_definition"),
            "primary_category": snap.get("primary_category"),
            "atom_type": snap.get("atom_type"),
            "subject": ext["subject"] if ext else None,
            "case_details": _loads(ext["case_details_json"], None) if ext else None,
            "version_number": ext["version_number"] if ext else None,
            "default_roles": list(sg._default_roles(snap)) if snap else [],
            "eligible": vid in eligible,
            "has_newer_version": bool(ext and ext["active_version_id"] and ext["active_version_id"] != vid),
            "exists": bool(live.get(vid)),
            "recall_source": pool_entry.get("recall_source") or ("outside_pool" if pool else None),
            "in_pool": vid in pool,
            "exceptions_not_landed": not_landed.get(vid, []),
        })
    return atoms


def _versions(conn, skill_id: str, names: Dict[str, str]) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, version_number, version_kind, based_on_version_id, review_action, reviewed_by, reviewed_at,
                  created_by, created_at FROM skill_versions WHERE skill_id = ? ORDER BY version_number""",
        (skill_id,),
    ).fetchall()
    return [{
        "version_id": r["id"],
        "version_number": r["version_number"],
        "version_kind": r["version_kind"],
        "version_kind_label": SKILL_VERSION_KIND_LABELS.get(r["version_kind"], r["version_kind"]),
        "based_on_version_id": r["based_on_version_id"],
        "review_action": r["review_action"],
        "review_action_label": REVIEW_ACTION_LABELS.get(r["review_action"]) if r["review_action"] else None,
        "reviewed_by_name": names.get(r["reviewed_by"]) if r["reviewed_by"] else None,
        "reviewed_at": r["reviewed_at"],
        "created_by_name": names.get(r["created_by"], r["created_by"]),
        "created_at": r["created_at"],
    } for r in rows]


def _review_records(conn, skill_id: str, names: Dict[str, str],
                    versions: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    numbers = ({v["version_id"]: v["version_number"] for v in versions} if versions is not None else
               {r["id"]: r["version_number"] for r in conn.execute(
                   "SELECT id, version_number FROM skill_versions WHERE skill_id = ?", (skill_id,)).fetchall()})
    rows = conn.execute("SELECT * FROM skill_review_records WHERE skill_id = ? ORDER BY created_at", (skill_id,)).fetchall()
    out = []
    for r in rows:
        detail = _loads(r["detail_json"], {}) or {}
        out.append({
            "record_id": r["id"],
            "action": r["action"],
            "action_label": detail.get("action_label") or REVIEW_ACTION_LABELS.get(r["action"], r["action"]),
            "operator_id": r["operator_id"],
            "operator_name": names.get(r["operator_id"], r["operator_id"]),
            "from_version_id": r["from_version_id"],
            "to_version_id": r["to_version_id"],
            "from_version_number": numbers.get(r["from_version_id"]),
            "to_version_number": numbers.get(r["to_version_id"]),
            "comment": r["comment"],
            "reject_reason": r["reject_reason"],
            "regenerate_groups": _loads(r["regenerate_groups_json"], []) or [],
            "checklist": _loads(r["checklist_json"], None),
            "field_diffs": _loads(r["field_diffs_json"], []) or [],
            "detail": detail,
            "created_at": r["created_at"],
        })
    return out


def _latest_task(conn, skill_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM skill_review_tasks WHERE skill_id = ? ORDER BY created_at DESC LIMIT 1",
                       (skill_id,)).fetchone()
    if not row:
        return None
    result = _loads(row["result_json"], {}) or {}
    return {
        "task_id": row["id"],
        "status": row["status"],
        "source_status": row["source_status"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "outcome": result.get("outcome"),
        "message": result.get("message"),
    }


def get_workbench(conn, user: Dict[str, Any], skill_id: str) -> Dict[str, Any]:
    org_id = user["organization_id"]
    skill = _load_skill(conn, org_id, skill_id)
    names = _user_names(conn, org_id)
    version, base_json = _base_json(conn, skill)
    draft = _load_draft(conn, skill)
    draft_valid = bool(draft and version and draft["base_version_id"] == version["id"])
    content, changes, stale = _prepare(
        conn, user, skill, version, base_json, _loads(draft["content_json"], None) if draft_valid else None,
        _loads(draft["stale_resolutions_json"], {}) if draft_valid else {})
    resolutions = _normalize_resolutions(_loads(draft["resolutions_json"], {}) if draft_valid else {})
    checklist = _normalize_checklist(_loads(draft["checklist_json"], {}) if draft_valid else {})
    change_reasons = (_loads(draft["change_reasons_json"], {}) or {}) if draft_valid else {}
    historical_ids = _historical_step_ids(conn, skill_id)
    ev = evaluate_content(conn, user, skill, base_json, content, resolutions, checklist, changes,
                          require_checklist=skill["status"] != "needs_recheck", historical_ids=historical_ids)
    base_report = _loads(version["validation_report_json"], {}) if version else {}
    scene = sc.get_scene(conn, org_id, skill["scene_id"])
    _used, next_step = _used_step_numbers(conn, skill_id, historical_ids)
    versions = _versions(conn, skill_id, names)
    pool = _pool_sources(conn, skill)
    pool_ids = list(pool)
    pool_eligible = set(filter_eligible_version_ids(conn, pool_ids, user)) if pool_ids else set()
    summary = sg._candidate_summary({**skill, "skill_json": version["skill_json"] if version else None,
                                     "version_number": version["version_number"] if version else None})
    error = _loads(skill.get("generation_error_json"), None)
    atoms = _atom_panel(conn, user, skill, version, content, ev["issues"], pool)
    return {
        **summary,
        "scene_id": skill["scene_id"],
        "scene_name": scene["name"] if scene else None,
        "status": skill["status"],
        "status_label": SKILL_STATUS_LABELS.get(skill["status"], skill["status"]),
        "visibility": skill["visibility"],
        "revision_token": skill["revision_token"],
        "current_version_id": skill.get("current_version_id"),
        "current_version_kind": version["version_kind"] if version else None,
        "current_version_kind_label": SKILL_VERSION_KIND_LABELS.get(version["version_kind"]) if version else None,
        "generation_confidence": base_json.get("generation_confidence"),
        "base_json": base_json,
        "content": content,
        "has_draft": draft_valid,
        "stale_draft": bool(draft and not draft_valid),
        "draft_updated_at": draft["updated_at"] if draft_valid else None,
        "draft_updated_by_name": names.get(draft["updated_by"]) if draft_valid else None,
        "resolutions": resolutions,
        "checklist": checklist,
        "change_reasons": change_reasons,
        "evaluation": public_evaluation(ev),
        "base_hints": [i for i in (base_report or {}).get("issues") or [] if i.get("level") == "hint"],
        "atoms": atoms,
        "atom_changed": (any(not a["eligible"] for a in atoms) or bool(changes)) if version else False,
        "stale_reason": skill.get("stale_reason"),
        "atom_changes": changes,
        "stale_resolutions": stale,
        "batch_pool": [{**a, "eligible": a["atom_version_id"] in pool_eligible} for a in pool.values()],
        "next_step_number": next_step,
        "versions": versions,
        "review_records": _review_records(conn, skill_id, names, versions),
        "regenerate_count": sum(1 for v in versions if v["version_kind"] == "ai_regenerated"),
        "regenerate_task": _latest_task(conn, skill_id),
        "generation_issues": [{**i, "plain": sg.plain_issue(i)} for i in (error or {}).get("issues") or []]
        if skill["status"] in ("validation_failed", "rejected") and not version else [],
        "checklist_items": [{"key": k, "label": label} for k, label in REVIEW_CHECKLIST],
        "reject_reasons": list(REJECT_REASONS),
        "field_groups": [{"key": g, "label": FIELD_GROUP_LABELS[g]} for g in REGENERATE_FIELD_GROUPS],
        "allowed_actions": sorted(a for a, table in TRANSITIONS.items() if skill["status"] in table),
    }


def check_draft(conn, user: Dict[str, Any], skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """编辑中的实时检查：不保存、不轮换令牌，只返回门槛与校验结果（界面同步显示不可通过原因）。"""
    skill = _load_skill(conn, user["organization_id"], skill_id)
    version, base_json = _base_json(conn, skill)
    content, changes, _stale = _prepare(conn, user, skill, version, base_json, payload.get("skill_json"),
                                        payload.get("stale_resolutions"))
    ev = evaluate_content(conn, user, skill, base_json, content, _normalize_resolutions(payload.get("resolutions")),
                          _normalize_checklist(payload.get("checklist")), changes,
                          require_checklist=skill["status"] != "needs_recheck")
    atoms = _atom_panel(conn, user, skill, version, content, ev["issues"])
    return {"evaluation": public_evaluation(ev), "content": content, "atoms": atoms}


# ---------------------------------------------------------------------------
# 草稿
# ---------------------------------------------------------------------------

def save_draft(conn, user: Dict[str, Any], skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    skill = _load_skill(conn, user["organization_id"], skill_id)
    check_transition("save_draft", skill["status"])
    check_token(skill, payload.get("revision_token"))
    version, base_json = _base_json(conn, skill)
    content, changes, stale = _prepare(conn, user, skill, version, base_json, payload.get("skill_json"),
                                       payload.get("stale_resolutions"))
    now_iso = _now()
    resolutions = _normalize_resolutions(payload.get("resolutions"))
    checklist = _normalize_checklist(payload.get("checklist"))
    reasons = {k: _clean_text(v, 300) for k, v in (payload.get("change_reasons") or {}).items()
               if isinstance(k, str) and isinstance(v, str) and v.strip()}
    conn.execute(
        """INSERT INTO skill_review_drafts (skill_id, organization_id, base_version_id, content_json, resolutions_json,
               checklist_json, change_reasons_json, stale_resolutions_json, created_by, created_at, updated_by,
               updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(skill_id) DO UPDATE SET base_version_id = excluded.base_version_id,
               content_json = excluded.content_json, resolutions_json = excluded.resolutions_json,
               checklist_json = excluded.checklist_json, change_reasons_json = excluded.change_reasons_json,
               stale_resolutions_json = excluded.stale_resolutions_json,
               updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
        (skill_id, user["organization_id"], version["id"], _dumps(content), _dumps(resolutions), _dumps(checklist),
         _dumps(reasons), _dumps(stale), user["id"], now_iso, user["id"], now_iso),
    )
    token = _rotate(conn, skill_id, now_iso)
    ev = evaluate_content(conn, user, skill, base_json, content, resolutions, checklist, changes,
                          require_checklist=skill["status"] != "needs_recheck")
    return {"revision_token": token, "saved_at": now_iso, "evaluation": public_evaluation(ev), "content": content}


def discard_draft(conn, user: Dict[str, Any], skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    skill = _load_skill(conn, user["organization_id"], skill_id)
    check_token(skill, payload.get("revision_token"))
    conn.execute("DELETE FROM skill_review_drafts WHERE skill_id = ?", (skill_id,))
    return {"revision_token": _rotate(conn, skill_id, _now())}


# ---------------------------------------------------------------------------
# 审核动作（FR12）
# ---------------------------------------------------------------------------

def _insert_record(conn, user: Dict[str, Any], skill_id: str, action: str, *, from_version_id: Optional[str],
                   to_version_id: Optional[str], comment: Optional[str] = None,
                   checklist: Optional[Dict[str, bool]] = None, field_diffs: Optional[List[Dict[str, Any]]] = None,
                   reject_reason: Optional[str] = None, regenerate_groups: Optional[List[str]] = None,
                   detail: Optional[Dict[str, Any]] = None, now_iso: str) -> str:
    record_id = f"srr_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO skill_review_records (id, organization_id, skill_id, from_version_id, to_version_id, action,
               operator_id, comment, checklist_json, field_diffs_json, reject_reason, regenerate_groups_json,
               detail_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (record_id, user["organization_id"], skill_id, from_version_id, to_version_id, action, user["id"],
         comment or None, _dumps(checklist) if checklist is not None else None,
         _dumps(field_diffs) if field_diffs is not None else None, reject_reason,
         _dumps(regenerate_groups) if regenerate_groups is not None else None,
         _dumps(detail) if detail is not None else None, now_iso),
    )
    return record_id


def _expert_entries(content: Dict[str, Any], items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """S13 待沉淀经验：专家补充的步骤与标为专家补充的无依据项。"""
    entries = []
    for step in content.get("steps") or []:
        if isinstance(step, dict) and step.get("basis") == "专家补充" and (step.get("expert_reason") or "").strip():
            entries.append({"source_kind": "专家补充步骤", "source_path": f"steps[{step.get('step_id')}]",
                            "content": step.get("action") or "", "reason": step["expert_reason"].strip()})
    for item in items:
        if item.get("still_present") and item.get("resolution") == "expert" and item.get("reason"):
            entries.append({"source_kind": item.get("kind") or "无依据项", "source_path": item.get("path") or "",
                            "content": item.get("value") or item.get("message") or "", "reason": item["reason"]})
    return entries


def _commit_review(conn, user: Dict[str, Any], skill: Dict[str, Any], version: Dict[str, Any],
                   base_json: Dict[str, Any], content: Dict[str, Any], ev: Dict[str, Any], *,
                   changed_action: str, unchanged_action: str, version_kind: str, checklist: Optional[Dict[str, bool]],
                   comment: str, reasons: Dict[str, Any], extra_detail: Dict[str, Any],
                   mark_version_when_unchanged: bool, now_iso: str) -> Dict[str, Any]:
    """
    审核提交的公共部分：有差异时产生新版本（专家修订版 / 复核处理版）并写引用快照，
    无差异时不产生版本；写审核记录与待沉淀经验，关闭原子变更记录，状态置为已通过。
    """
    org_id = user["organization_id"]
    skill_id = skill["id"]
    report = ev["_report"]
    items = ev["unsupported_items"]
    detail = {"summary": ev["summary"], "unsupported_items": items, **extra_detail}
    if not ev["has_changes"]:
        action, to_version_id = unchanged_action, version["id"]
        if mark_version_when_unchanged:
            conn.execute(
                """UPDATE skill_versions SET review_action = ?, reviewed_by = ?, reviewed_at = ?, review_comment = ?
                   WHERE id = ?""",
                (action, user["id"], now_iso, comment or None, version["id"]),
            )
        record_id = _insert_record(conn, user, skill_id, action, from_version_id=version["id"],
                                   to_version_id=version["id"], comment=comment, checklist=checklist, field_diffs=[],
                                   detail=detail, now_iso=now_iso)
        new_version_number = None
    else:
        action = changed_action
        number = conn.execute("SELECT MAX(version_number) AS n FROM skill_versions WHERE skill_id = ?",
                              (skill_id,)).fetchone()["n"] or 0
        new_version_number = int(number) + 1
        to_version_id = f"skv_{uuid.uuid4().hex[:12]}"
        diffs = attach_reasons(ev["diffs"], reasons)
        final_items = []
        for item in items:
            if item["still_present"]:
                final_items.append({
                    **{k: item.get(k) for k in ("kind", "path", "item_key", "value", "atom_version_id", "message")
                       if item.get(k) is not None},
                    "resolution": {"action": item["resolution"], "reason": item.get("reason")},
                })
        generation = _loads(version["generation_json"], {}) or {}
        skill_json = copy.deepcopy(content)
        skill_json.update({
            "skill_id": skill_id,
            "version_number": new_version_number,
            "status": SKILL_STATUS_LABELS["approved"],
            "generation": base_json.get("generation"),
            "review": {"reviewer": user.get("display_name") or user.get("username") or user["id"],
                       "reviewed_at": now_iso, "action": REVIEW_ACTION_LABELS[action], "comment": comment or None},
            "visibility": report.visibility,
            "unsupported_items": final_items,
            "generation_confidence": base_json.get("generation_confidence"),
        })
        conn.execute(
            """INSERT INTO skill_versions (id, skill_id, organization_id, version_number, schema_version, skill_json,
                   version_kind, based_on_version_id, batch_id, generation_json, validation_report_json,
                   review_action, reviewed_by, reviewed_at, review_comment, revision_token, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (to_version_id, skill_id, org_id, new_version_number, SKILL_SCHEMA_VERSION, _dumps(skill_json),
             version_kind, version["id"], skill.get("batch_id"),
             _dumps({**generation, "revised_from_version_id": version["id"]}),
             _dumps(report.to_dict()), action, user["id"], now_iso, comment or None, _new_token(), user["id"], now_iso),
        )
        sg.insert_atom_refs(conn, org_id, skill_id, to_version_id, content.get("knowledge_refs") or [],
                            report.atom_snapshots, now_iso)
        record_id = _insert_record(conn, user, skill_id, action, from_version_id=version["id"],
                                   to_version_id=to_version_id, comment=comment, checklist=checklist,
                                   field_diffs=diffs, detail=detail, now_iso=now_iso)
    for entry in _expert_entries(content, items):
        conn.execute(
            """INSERT OR IGNORE INTO skill_expert_experiences (id, organization_id, skill_id, skill_version_id,
                   review_record_id, source_kind, source_path, content, reason, status, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (f"see_{uuid.uuid4().hex[:12]}", org_id, skill_id, to_version_id, record_id, entry["source_kind"],
             entry["source_path"], entry["content"], entry["reason"], user["id"], now_iso),
        )
    conn.execute("DELETE FROM skill_review_drafts WHERE skill_id = ?", (skill_id,))
    rc.resolve_events(conn, skill_id, user["id"], record_id, action, now_iso)
    token = _rotate(conn, skill_id, now_iso, status="approved", current_version_id=to_version_id,
                    visibility=report.visibility, stale_reason=None)
    return {"action": action, "to_version_id": to_version_id, "new_version_number": new_version_number,
            "record_id": record_id, "token": token}


def _submitted_content(conn, user: Dict[str, Any], skill: Dict[str, Any], payload: Dict[str, Any]):
    """审核提交时的内容：请求中未带内容或复核选择时取草稿（草稿须基于当前版本）。"""
    version, base_json = _base_json(conn, skill)
    draft = _load_draft(conn, skill)
    draft_valid = bool(draft and version and draft["base_version_id"] == version["id"])
    client_json = payload.get("skill_json")
    if client_json is None and draft_valid:
        client_json = _loads(draft["content_json"], None)
    stale_raw = payload.get("stale_resolutions")
    if stale_raw is None and draft_valid:
        stale_raw = _loads(draft["stale_resolutions_json"], {})
    content, changes, _stale = _prepare(conn, user, skill, version, base_json, client_json, stale_raw)
    return version, base_json, content, changes


def approve(conn, user: Dict[str, Any], skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    通过 / 修改后通过（同一按钮）：后端复查全部门槛；有差异时产生专家修订版，无差异时原版本即通过版本（AC13）。
    待审核候选遇原子变更时（PRD 7.2 说明、FR14），变更处理后才能通过。
    """
    org_id = user["organization_id"]
    skill = _load_skill(conn, org_id, skill_id)
    check_transition("approve", skill["status"])
    check_token(skill, payload.get("revision_token"))
    version, base_json, content, changes = _submitted_content(conn, user, skill, payload)
    resolutions = _normalize_resolutions(payload.get("resolutions"))
    checklist = _normalize_checklist(payload.get("checklist"))
    ev = evaluate_content(conn, user, skill, base_json, content, resolutions, checklist, changes)
    if not ev["can_approve"]:
        raise ReviewError(422, "还不能通过：" + "；".join(b["message"] for b in ev["blockers"]),
                          "APPROVAL_BLOCKED", {"blockers": ev["blockers"]})
    comment = _clean_text(payload.get("comment"), REVIEW_COMMENT_MAX)
    reasons = payload.get("change_reasons") if isinstance(payload.get("change_reasons"), dict) else {}
    now_iso = _now()
    extra = {"atom_changes": rc.resolution_summary(changes)} if changes else {}
    done = _commit_review(conn, user, skill, version, base_json, content, ev,
                          changed_action="approve_with_changes", unchanged_action="approve",
                          version_kind="expert_revision", checklist=checklist, comment=comment, reasons=reasons,
                          extra_detail=extra, mark_version_when_unchanged=True, now_iso=now_iso)
    action = done["action"]
    _audit(conn, user, f"skill_review_{action}", skill_id,
           {"from_version_id": version["id"], "to_version_id": done["to_version_id"], "record_id": done["record_id"],
            "changed_field_count": ev["summary"]["changed_field_count"]}, now_iso)
    return {
        "action": action,
        "action_label": REVIEW_ACTION_LABELS[action],
        "status": "approved",
        "version_id": done["to_version_id"],
        "version_number": done["new_version_number"],
        "record_id": done["record_id"],
        "revision_token": done["token"],
        "next_skill_id": next_pending_skill(conn, user, skill_id, payload.get("queue")),
    }


def recheck(conn, user: Dict[str, Any], skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    FR14 复核处理（待复核 -> 已通过）：逐条受影响知识选择「更新引用 / 确认无影响（须填说明）/ 移除引用」，
    可同时编辑受影响字段。引用或内容有变化时产生复核处理版，只确认无影响且引用不变时不产生版本。
    原子被停用、删除或排除时不能保持旧引用；移除后出现的无依据步骤须按 FR11 处理。
    其余门槛与「通过」相同（不要求逐段勾选审核清单）。
    """
    org_id = user["organization_id"]
    skill = _load_skill(conn, org_id, skill_id)
    check_transition("recheck", skill["status"])
    check_token(skill, payload.get("revision_token"))
    version, base_json, content, changes = _submitted_content(conn, user, skill, payload)
    resolutions = _normalize_resolutions(payload.get("resolutions"))
    ev = evaluate_content(conn, user, skill, base_json, content, resolutions, {}, changes, require_checklist=False)
    if not ev["can_approve"]:
        raise ReviewError(422, "还不能提交复核：" + "；".join(b["message"] for b in ev["blockers"]),
                          "RECHECK_BLOCKED", {"blockers": ev["blockers"]})
    comment = _clean_text(payload.get("comment"), REVIEW_COMMENT_MAX)
    reasons = payload.get("change_reasons") if isinstance(payload.get("change_reasons"), dict) else {}
    now_iso = _now()
    summary = rc.resolution_summary(changes)
    labels = list(dict.fromkeys(c["resolution_label"] for c in summary if c["resolution_label"]))
    action_label = REVIEW_ACTION_LABELS["recheck"] + (f"（{'、'.join(labels)}）" if labels else "")
    extra = {"atom_changes": summary, "stale_reason": skill.get("stale_reason"), "action_label": action_label}
    done = _commit_review(conn, user, skill, version, base_json, content, ev,
                          changed_action="recheck", unchanged_action="recheck", version_kind="recheck_revision",
                          checklist=None, comment=comment, reasons=reasons, extra_detail=extra,
                          mark_version_when_unchanged=False, now_iso=now_iso)
    _audit(conn, user, "skill_review_recheck", skill_id,
           {"from_version_id": version["id"], "to_version_id": done["to_version_id"], "record_id": done["record_id"],
            "resolutions": [{"atom_version_id": c["atom_version_id"], "resolution": c["resolution"]} for c in summary]},
           now_iso)
    return {
        "action": "recheck",
        "action_label": action_label,
        "status": "approved",
        "version_id": done["to_version_id"],
        "version_number": done["new_version_number"],
        "record_id": done["record_id"],
        "revision_token": done["token"],
        "next_skill_id": None,
    }


def reject(conn, user: Dict[str, Any], skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """驳回：原因枚举与说明必填；驳回的候选保留，可恢复（AC15）。"""
    skill = _load_skill(conn, user["organization_id"], skill_id)
    check_transition("reject", skill["status"])
    check_token(skill, payload.get("revision_token"))
    reason = payload.get("reason")
    if reason not in REJECT_REASONS:
        raise ReviewError(400, "请选择驳回原因", "REJECT_REASON_REQUIRED")
    note = _clean_text(payload.get("note"), REJECT_NOTE_MAX)
    if not note:
        raise ReviewError(400, "请填写驳回说明", "REJECT_NOTE_REQUIRED")
    now_iso = _now()
    record_id = _insert_record(conn, user, skill_id, "reject", from_version_id=skill.get("current_version_id"),
                               to_version_id=None, comment=note, reject_reason=reason,
                               detail={"from_status": skill["status"]}, now_iso=now_iso)
    rc.resolve_events(conn, skill_id, user["id"], record_id, "reject", now_iso)
    token = _rotate(conn, skill_id, now_iso, status="rejected", stale_reason=None)
    _audit(conn, user, "skill_review_reject", skill_id, {"reason": reason, "record_id": record_id}, now_iso)
    return {"status": "rejected", "record_id": record_id, "revision_token": token,
            "next_skill_id": next_pending_skill(conn, user, skill_id, payload.get("queue"))}


def abandon(conn, user: Dict[str, Any], skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验未通过 -> 放弃：进入已驳回，不产生版本（PRD 7.2）。"""
    skill = _load_skill(conn, user["organization_id"], skill_id)
    check_transition("abandon", skill["status"])
    check_token(skill, payload.get("revision_token"))
    now_iso = _now()
    note = _clean_text(payload.get("note"), REJECT_NOTE_MAX)
    record_id = _insert_record(conn, user, skill_id, "reject", from_version_id=None, to_version_id=None,
                               comment=note or None,
                               detail={"from_status": "validation_failed", "abandon": True, "action_label": "放弃"},
                               now_iso=now_iso)
    token = _rotate(conn, skill_id, now_iso, status="rejected")
    _audit(conn, user, "skill_review_abandon", skill_id, {"record_id": record_id}, now_iso)
    return {"status": "rejected", "record_id": record_id, "revision_token": token}


def restore(conn, user: Dict[str, Any], skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """已驳回 -> 恢复 -> 待审核；没有版本的（校验未通过后放弃）恢复为校验未通过。"""
    skill = _load_skill(conn, user["organization_id"], skill_id)
    check_transition("restore", skill["status"])
    check_token(skill, payload.get("revision_token"))
    target = "pending_review" if skill.get("current_version_id") else "validation_failed"
    now_iso = _now()
    record_id = _insert_record(conn, user, skill_id, "restore", from_version_id=skill.get("current_version_id"),
                               to_version_id=skill.get("current_version_id"),
                               comment=_clean_text(payload.get("comment"), REVIEW_COMMENT_MAX) or None,
                               detail={"to_status": target}, now_iso=now_iso)
    token = _rotate(conn, skill_id, now_iso, status=target)
    _audit(conn, user, "skill_review_restore", skill_id, {"record_id": record_id, "to_status": target}, now_iso)
    return {"status": target, "record_id": record_id, "revision_token": token}


def request_regenerate(conn, user: Dict[str, Any], skill_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    退回重生成（待审核）或重新生成（校验未通过）：状态置为「生成中」，写审核记录并创建后台任务；
    调用方在事务提交后入队 task_id。
    """
    skill = _load_skill(conn, user["organization_id"], skill_id)
    source_status = skill["status"]
    check_transition("regenerate", source_status)
    check_token(skill, payload.get("revision_token"))
    comment = _clean_text(payload.get("comment"), REVIEW_COMMENT_MAX)
    if source_status in ("pending_review", "needs_recheck") and not comment:
        raise ReviewError(400, "请填写退回意见，说明需要怎么改", "REGENERATE_COMMENT_REQUIRED")
    groups_raw = payload.get("field_groups") or []
    if not isinstance(groups_raw, list) or any(g not in REGENERATE_FIELD_GROUPS for g in groups_raw):
        raise ReviewError(400, "需要重写的字段组只能是 A 至 E 组")
    groups = [g for g in REGENERATE_FIELD_GROUPS if g in groups_raw]
    group_labels = [f"{g} {FIELD_GROUP_LABELS[g]}" for g in groups]
    # 原子变更后退回重生成：把受影响原子当前可引用的新版本一并交给模型（FR14），旧版本不再提供
    refresh: Dict[str, str] = {}
    if skill.get("current_version_id"):
        version, base_json = _base_json(conn, skill)
        for entry in rc.atom_changes(conn, user["organization_id"], skill, version["id"], base_json):
            if entry.get("new_version_id"):
                refresh[entry["atom_version_id"]] = entry["new_version_id"]
    now_iso = _now()
    record_id = _insert_record(
        conn, user, skill_id, "regenerate", from_version_id=skill.get("current_version_id"), to_version_id=None,
        comment=comment or None, regenerate_groups=groups,
        detail={"state": "running", "source_status": source_status,
                "action_label": REVIEW_ACTION_LABELS["regenerate"] if source_status != "validation_failed" else "重新生成"},
        now_iso=now_iso,
    )
    task_id = f"srt_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO skill_review_tasks (id, organization_id, skill_id, review_record_id, task_type, source_status,
               status, attempt_count, payload_json, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'regenerate', ?, 'queued', 0, ?, ?, ?, ?)""",
        (task_id, user["organization_id"], skill_id, record_id, source_status,
         _dumps({"comment": comment, "field_groups": group_labels, "refresh_atoms": refresh}),
         user["id"], now_iso, now_iso),
    )
    conn.execute("DELETE FROM skill_review_drafts WHERE skill_id = ?", (skill_id,))
    token = _rotate(conn, skill_id, now_iso, status="generating")
    _audit(conn, user, "skill_review_regenerate", skill_id,
           {"record_id": record_id, "task_id": task_id, "source_status": source_status, "field_groups": groups},
           now_iso)
    return {"status": "generating", "task_id": task_id, "record_id": record_id, "revision_token": token,
            "next_skill_id": next_pending_skill(conn, user, skill_id, payload.get("queue"))}


# ---------------------------------------------------------------------------
# 重生成后台任务（复用 tasks.py 线程池与启动恢复）
# ---------------------------------------------------------------------------

def _tx():
    return sg._write_tx()


def _claim_review_task(task_id: str) -> Optional[Dict[str, Any]]:
    with _tx() as conn:
        task = conn.execute("SELECT * FROM skill_review_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task or task["status"] not in ("queued", "running"):
            return None
        now_iso = _now()
        conn.execute(
            """UPDATE skill_review_tasks SET status = 'running', attempt_count = attempt_count + 1, started_at = ?,
                   updated_at = ?, error_message = NULL WHERE id = ?""",
            (now_iso, now_iso, task_id),
        )
        return dict(task)


def _finish_review_task(conn, task: Dict[str, Any], *, ok: bool, outcome: str, message: str,
                        result: Dict[str, Any]) -> None:
    now_iso = _now()
    calls = result.get("model_calls")
    conn.execute(
        """UPDATE skill_review_tasks SET status = ?, result_json = ?, model_calls_json = ?, error_message = ?,
               completed_at = ?, updated_at = ? WHERE id = ?""",
        ("completed" if ok else "failed",
         _dumps({"outcome": outcome, "message": message,
                 **{k: v for k, v in result.items() if k not in ("model_calls", "candidate")}}),
         _dumps(calls) if calls is not None else None, None if ok else message, now_iso, now_iso, task["id"]),
    )


def _fail_regeneration(task: Dict[str, Any], outcome: str, message: str, result: Dict[str, Any]) -> None:
    """重生成未成功：Skill 回到原状态、内容不变；校验未通过的候选更新最后一次候选与问题清单。"""
    with _tx() as conn:
        skill = conn.execute("SELECT * FROM skills WHERE id = ?", (task["skill_id"],)).fetchone()
        now_iso = _now()
        if skill and skill["status"] == "generating":
            fields: Dict[str, Any] = {"status": task["source_status"]}
            if task["source_status"] == "validation_failed" and result.get("issues"):
                error = _loads(skill["generation_error_json"], {}) or {}
                error.update({"issues": result.get("issues")[:60], "candidate": result.get("candidate"),
                              "regenerated_at": now_iso})
                fields["generation_error_json"] = _dumps(error)
            _rotate(conn, task["skill_id"], now_iso, **fields)
        record = conn.execute("SELECT detail_json FROM skill_review_records WHERE id = ?",
                              (task.get("review_record_id") or "",)).fetchone()
        if record:
            detail = _loads(record["detail_json"], {}) or {}
            detail.update({"state": "failed", "outcome": outcome, "message": message})
            conn.execute("UPDATE skill_review_records SET detail_json = ? WHERE id = ?",
                         (_dumps(detail), task["review_record_id"]))
        _finish_review_task(conn, task, ok=False, outcome=outcome, message=message, result=result)
        sc.write_audit(conn, task["organization_id"], task["created_by"], "skill_regenerate_failed", "skill",
                       task["skill_id"], {"task_id": task["id"], "outcome": outcome}, now_iso)


def run_review_task(task_id: str) -> None:
    """后台入口：执行重生成；任何异常都把 Skill 从「生成中」恢复，不留下进行中状态。"""
    task = None
    try:
        task = _claim_review_task(task_id)
        if task is None:
            return
        payload = _loads(task.get("payload_json"), {}) or {}
        with sg._read_conn() as conn:
            actor = sg.load_actor(conn, task["created_by"], task["organization_id"])
            skill = conn.execute("SELECT * FROM skills WHERE id = ?", (task["skill_id"],)).fetchone()
            prev_version = _version_row(conn, skill["current_version_id"]) if skill else None
        if not skill or skill["status"] != "generating":
            with _tx() as conn:
                _finish_review_task(conn, task, ok=False, outcome="cancelled", message="Skill 已不在生成中", result={})
            return
        if not actor or actor.get("role") != "admin" or actor.get("account_status") != "active":
            _fail_regeneration(task, "actor_unavailable", "发起人已不是可用的管理员账号", {})
            return
        prev_json = (_loads(prev_version["skill_json"], {}) if prev_version
                     else (_loads(skill["generation_error_json"], {}) or {}).get("candidate")) or {}

        def on_stored(conn, result):
            current = conn.execute("SELECT status FROM skills WHERE id = ?", (task["skill_id"],)).fetchone()
            if not current or current["status"] != "pending_review":
                raise RuntimeError("Skill 状态已变化，放弃写入")
            new_version = _version_row(conn, result["version_id"])
            new_json = _loads(new_version["skill_json"], {}) or {}
            diffs = diff_skill(strip_system_fields(prev_json), strip_system_fields(new_json))
            record = conn.execute("SELECT detail_json FROM skill_review_records WHERE id = ?",
                                  (task.get("review_record_id") or "",)).fetchone()
            if record:
                detail = _loads(record["detail_json"], {}) or {}
                detail.update({"state": "completed", "version_number": result["version_number"],
                               "repair_count": result.get("repair_count"),
                               "summary": summarize_diffs(diffs)})
                conn.execute(
                    "UPDATE skill_review_records SET to_version_id = ?, field_diffs_json = ?, detail_json = ? WHERE id = ?",
                    (result["version_id"], _dumps(diffs), _dumps(detail), task["review_record_id"]),
                )
            rc.resolve_events(conn, task["skill_id"], task["created_by"], task.get("review_record_id"), "regenerate", _now())
            conn.execute("UPDATE skills SET stale_reason = NULL WHERE id = ?", (task["skill_id"],))
            _finish_review_task(conn, task, ok=True, outcome="stored",
                                message=f"已生成第 {result['version_number']} 版，回到待审核", result=result)
            sc.write_audit(conn, task["organization_id"], task["created_by"], "skill_regenerate_completed", "skill",
                           task["skill_id"], {"task_id": task["id"], "version_id": result["version_id"]}, _now())

        result = sg.regenerate_skill_version(task["skill_id"], actor, payload.get("comment") or "",
                                             payload.get("field_groups") or None, on_stored=on_stored,
                                             refresh_atoms=payload.get("refresh_atoms") or None)
        if not result.get("ok"):
            outcome = result.get("reason") or "failed"
            message = ("生成服务这次没有响应，内容没有改动，可以稍后再试" if outcome == "call_failed"
                       else "重新生成的内容仍未通过检查，内容没有改动，可以修改意见后再试")
            _fail_regeneration(task, outcome, message, result)
    except sc.SceneCatalogError as exc:
        if task is not None:
            _fail_regeneration(task, "not_possible", exc.message, {})
    except Exception as exc:  # 防御：任何意外都不能让 Skill 卡在生成中
        logger.exception("Skill review task %s crashed", task_id)
        if task is not None:
            try:
                _fail_regeneration(task, "crashed", "处理出错，内容没有改动，可以再试一次", {"error": str(exc)[:300]})
            except Exception:
                logger.exception("Failed to record crash for review task %s", task_id)


def recover_review_tasks() -> List[str]:
    """服务重启：排队或进行中的重生成任务重新排队；没有任务的「生成中」Skill 恢复到可操作状态。"""
    resubmit: List[str] = []
    with _tx() as conn:
        now_iso = _now()
        for row in conn.execute(
            "SELECT id FROM skill_review_tasks WHERE status IN ('queued', 'running') ORDER BY created_at"
        ).fetchall():
            conn.execute("UPDATE skill_review_tasks SET status = 'queued', started_at = NULL, updated_at = ? WHERE id = ?",
                         (now_iso, row["id"]))
            resubmit.append(row["id"])
        for skill in conn.execute(
            """SELECT s.id, s.current_version_id FROM skills s WHERE s.status = 'generating' AND NOT EXISTS (
                   SELECT 1 FROM skill_review_tasks t WHERE t.skill_id = s.id AND t.status IN ('queued', 'running'))"""
        ).fetchall():
            _rotate(conn, skill["id"], now_iso,
                    status="pending_review" if skill["current_version_id"] else "validation_failed")
    from tasks import enqueue_skill_review_task
    for task_id in resubmit:
        enqueue_skill_review_task(task_id)
    return resubmit


# ---------------------------------------------------------------------------
# 差异与导出（FR13）
# ---------------------------------------------------------------------------

def _version_content(conn, skill_id: str, version_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM skill_versions WHERE id = ? AND skill_id = ?", (version_id, skill_id)).fetchone()
    if not row:
        raise ReviewError(404, "版本不存在")
    return dict(row)


def _default_pair(conn, skill: Dict[str, Any], from_id: Optional[str], to_id: Optional[str]) -> Tuple[str, str]:
    versions = conn.execute("SELECT id, version_kind FROM skill_versions WHERE skill_id = ? ORDER BY version_number",
                            (skill["id"],)).fetchall()
    if not versions:
        raise ReviewError(409, "这条 Skill 还没有版本")
    original = next((v["id"] for v in versions if v["version_kind"] == "ai_original"), versions[0]["id"])
    latest = skill.get("current_version_id") or versions[-1]["id"]
    return from_id or original, to_id or latest


def compare_versions(conn, user: Dict[str, Any], skill_id: str, from_id: Optional[str] = None,
                     to_id: Optional[str] = None) -> Dict[str, Any]:
    """任意两个版本的字段差异，默认 AI 原稿对最新版本；修改理由取自审核记录。"""
    org_id = user["organization_id"]
    skill = _load_skill(conn, org_id, skill_id)
    from_id, to_id = _default_pair(conn, skill, from_id, to_id)
    a = _version_content(conn, skill_id, from_id)
    b = _version_content(conn, skill_id, to_id)
    a_json = _loads(a["skill_json"], {}) or {}
    b_json = _loads(b["skill_json"], {}) or {}
    reasons: Dict[str, str] = {}
    for row in conn.execute(
        "SELECT field_diffs_json FROM skill_review_records WHERE skill_id = ? AND field_diffs_json IS NOT NULL ORDER BY created_at",
        (skill_id,),
    ).fetchall():
        for d in _loads(row["field_diffs_json"], []) or []:
            if isinstance(d, dict) and d.get("reason"):
                reasons[d["path"]] = d["reason"]
    diffs = attach_reasons(diff_skill(strip_system_fields(a_json), strip_system_fields(b_json)), reasons)
    a_items = [i for i in a_json.get("unsupported_items") or [] if isinstance(i, dict)]
    b_items = [i for i in b_json.get("unsupported_items") or [] if isinstance(i, dict)]
    explicit = {i["item_key"]: (i.get("resolution") or {}) for i in b_items
                if i.get("item_key") and isinstance(i.get("resolution"), dict)}
    items = classify_unsupported(a_items, b_items, explicit, strip_system_fields(b_json)) if a["id"] != b["id"] else []
    names = _user_names(conn, org_id)

    def side(v, data):
        return {
            "version_id": v["id"],
            "version_number": v["version_number"],
            "version_kind": v["version_kind"],
            "version_kind_label": SKILL_VERSION_KIND_LABELS.get(v["version_kind"], v["version_kind"]),
            "created_at": v["created_at"],
            "created_by_name": names.get(v["created_by"], v["created_by"]),
            "skill_json": data,
        }

    return {
        "skill_id": skill_id,
        "from": side(a, a_json),
        "to": side(b, b_json),
        "diffs": diffs,
        "summary": summarize_diffs(diffs, resolution_counts(items)),
        "unsupported_items": items,
    }


def export_skill(conn, user: Dict[str, Any], skill_id: str, fmt: str, from_id: Optional[str] = None,
                 to_id: Optional[str] = None) -> Tuple[str, str, str]:
    """单个 Skill 导出「原稿、终稿、差异清单」：返回 (文件内容, 媒体类型, 文件名)。"""
    org_id = user["organization_id"]
    skill = _load_skill(conn, org_id, skill_id)
    cmp = compare_versions(conn, user, skill_id, from_id, to_id)
    scene = sc.get_scene(conn, org_id, skill["scene_id"])
    names = _user_names(conn, org_id)
    payload = {
        "skill_id": skill_id,
        "name": (cmp["to"]["skill_json"] or {}).get("name"),
        "scene_id": skill["scene_id"],
        "scene_name": scene["name"] if scene else None,
        "status": skill["status"],
        "status_label": SKILL_STATUS_LABELS.get(skill["status"], skill["status"]),
        "exported_at": _now(),
        "original": cmp["from"],
        "final": cmp["to"],
        "summary": cmp["summary"],
        "diffs": cmp["diffs"],
        "unsupported_items": cmp["unsupported_items"],
        "review_records": [{k: v for k, v in r.items() if k not in ("field_diffs", "checklist")}
                           for r in _review_records(conn, skill_id, names)],
    }
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", payload["name"] or skill_id)[:40]
    base = f"{safe}_v{cmp['from']['version_number']}-v{cmp['to']['version_number']}_差异"
    _audit(conn, user, "skill_export", skill_id, {"format": fmt, "from_version_id": cmp["from"]["version_id"],
                                                  "to_version_id": cmp["to"]["version_id"]}, _now())
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2), "application/json; charset=utf-8", base + ".json"
    return render_export_markdown(payload), "text/markdown; charset=utf-8", base + ".md"


def list_experiences(conn, user: Dict[str, Any]) -> List[Dict[str, Any]]:
    """S13 待沉淀经验清单（只读清单；转为 M01 待确认原子列为后续增强）。"""
    org_id = user["organization_id"]
    names = _user_names(conn, org_id)
    rows = conn.execute(
        """SELECT e.*, sv.skill_json, sv.version_number FROM skill_expert_experiences e
           LEFT JOIN skill_versions sv ON sv.id = e.skill_version_id
           WHERE e.organization_id = ? ORDER BY e.created_at DESC""",
        (org_id,),
    ).fetchall()
    return [{
        "experience_id": r["id"],
        "skill_id": r["skill_id"],
        "skill_name": (_loads(r["skill_json"], {}) or {}).get("name"),
        "version_number": r["version_number"],
        "source_kind": r["source_kind"],
        "source_path": r["source_path"],
        "source_label": describe_path(r["source_path"]),
        "content": r["content"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by_name": names.get(r["created_by"], r["created_by"]),
        "created_at": r["created_at"],
    } for r in rows]
