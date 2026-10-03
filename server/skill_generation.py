"""
知行有策 - M02 Skill 候选生成批次（PRD 第 6 章 FR04~FR08、6.2、6.3）

流程：G1 发起 -> G2 召回原子池 -> G3 任务拆分 -> G4 候选生成 -> G5 程序校验 -> G6 入库。

- 后台任务写入独立任务表 skill_generation_tasks（不占用 processing_tasks.task_type），
  复用 tasks.py 的线程池 _executor 与完成跟踪；任务之间用「完成后入队下一步」串联，
  不在工作线程里阻塞等待子任务，并发上限即 M01 抽取所用线程池的 2 个工作线程。
- 可引用资格一律调用 eligibility.py（R1）；召回复用 scene_catalog.recall_atoms；
  候选校验一律交给 skill_validation.validate_skill，不信任模型自报的格式正确。
- 模型调用复用 deepseek_extractor.post_chat_completion（模型名、Base URL、密钥取自现有配置）；
  每次调用的原始返回保存在任务表 model_calls_json，不保存密钥与请求头。
- 提示词单独成文件（server/prompts/），版本号写入批次与 Skill 版本记录。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import config
import scene_catalog as sc
from deepseek_extractor import post_chat_completion
from eligibility import filter_eligible_version_ids
from m02_common import dumps as _dumps, loads as _loads, now_iso as _now
from scene_catalog import SceneCatalogError
from skill_constants import (
    BATCH_STATUS_LABELS,
    FOCUS_NOTE_MAX,
    SCENE_MIN_ATOMS_FOR_GENERATION,
    SKILL_AUTO_REPAIR_ROUNDS,
    SKILL_MODEL_CALL_MAX_ATTEMPTS,
    SKILL_SCHEMA_VERSION,
    SKILL_SIMILAR_THRESHOLD,
    SKILL_STATUS_LABELS,
    SKILL_TASK_TYPE_LABELS,
    SPLIT_FORMAT_RETRY_ROUNDS,
    SPLIT_MAX_TASKS,
    SPLIT_MIN_ATOMS_FOR_GENERATION,
    TASK_OVERLAP_THRESHOLD,
    TASK_RESULT_LABELS,
)
from skill_validation import (
    HARD_ERROR,
    REF_COUNT_MAX,
    REF_COUNT_MIN,
    ValidationIssue,
    _default_roles,
    _json_value,
    _text_list,
    apply_derived_fields,
    load_skill_schema,
    validate_skill,
)

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().with_name("prompts")
SPLIT_PROMPT_FILE = "skill_split_v1.md"
GENERATE_PROMPT_FILE = config.SKILL_GENERATE_PROMPT
_PROMPT_VERSION_RE = re.compile(r"<!--\s*prompt_version:\s*([\w.\-]+)\s*-->")

RAW_RESPONSE_KEEP = 40000   # 单次原始返回最多保存的字符数
ISSUES_KEEP = 60            # 单个候选最多保存的问题条数

# F 组治理字段与 G 组后续模块字段由系统或后续模块填写，模型填了也不采用
SYSTEM_FIELDS = (
    "skill_id", "version_number", "status", "generation", "review", "stale_reason",
    "visibility", "unsupported_items", "test_set_ref", "pricing", "license",
    "maintainer", "published_at",
)

ACTIVE_SKILL_STATUSES = ("pending_review", "approved", "needs_recheck")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

@contextmanager
def _write_tx():
    """立即取得写锁的短事务：批次记录的读改写在同一事务内完成，避免并发任务互相覆盖。"""
    from database import get_db
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def _read_conn():
    from database import get_db
    return get_db()


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """重合度：两组原子交集 / 并集（FR05 任务重复、FR08 相似 Skill 共用此口径）。"""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


@lru_cache(maxsize=8)
def load_prompt(filename: str) -> Tuple[str, str]:
    """读取提示词文件，返回 (版本号, 正文)。正文去掉 HTML 注释，生成提示词中嵌入 Schema 全文。"""
    raw = (PROMPT_DIR / filename).read_text(encoding="utf-8")
    match = _PROMPT_VERSION_RE.search(raw)
    if not match:
        raise RuntimeError(f"提示词文件 {filename} 缺少版本号")
    text = re.sub(r"<!--.*?-->\s*", "", raw, flags=re.S).strip()
    if "{{SKILL_SCHEMA_JSON}}" in text:
        text = text.replace("{{SKILL_SCHEMA_JSON}}", json.dumps(load_skill_schema(), ensure_ascii=False, indent=1))
    return match.group(1), text


def prompt_versions() -> Dict[str, str]:
    return {"split": load_prompt(SPLIT_PROMPT_FILE)[0], "generate": load_prompt(GENERATE_PROMPT_FILE)[0]}


def parse_model_json(raw: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """解析模型返回的 JSON 对象；容忍代码块标记和前后多余文字。"""
    text = (raw or "").strip()
    if not text:
        return None, "返回内容为空"
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for chunk in candidates:
        try:
            value = json.loads(chunk)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value, None
        return None, "返回内容不是 JSON 对象"
    return None, "返回内容不是合法 JSON（可能被截断）"


class ModelNotConfigured(RuntimeError):
    pass


def _invoke_model(messages: List[Dict[str, str]], *, max_tokens: Optional[int] = None,
                  timeout: Optional[float] = None) -> Tuple[str, str, float]:
    """单次模型调用（失败重试由任务层按 SKILL_MODEL_CALL_MAX_ATTEMPTS 负责）。"""
    if not config.DEEPSEEK_API_KEY:
        raise ModelNotConfigured("在线模型未配置")
    started = time.monotonic()
    raw, model = post_chat_completion(
        messages, api_key=config.DEEPSEEK_API_KEY, max_tokens=max_tokens, timeout=timeout, max_attempts=1,
    )
    return raw or "", model, round(time.monotonic() - started, 2)


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}：{str(exc)[:300]}"


def _call_record(purpose: str, prompt_version: str, attempt: int, **fields) -> Dict[str, Any]:
    record = {"purpose": purpose, "prompt_version": prompt_version, "attempt": attempt, "at": _now()}
    raw = fields.pop("raw_response", None)
    if raw is not None:
        record["raw_length"] = len(raw)
        record["raw_response"] = raw[:RAW_RESPONSE_KEEP]
    record.update(fields)
    return record


# ---------------------------------------------------------------------------
# 原子读取
# ---------------------------------------------------------------------------

def load_actor(conn, user_id: str, org_id: str) -> Optional[Dict[str, Any]]:
    """后台任务按发起人的当前账号状态判断资格（与接口请求同一口径）。"""
    row = conn.execute(
        """SELECT id, organization_id, username, display_name, role, account_status
           FROM users WHERE id = ? AND organization_id = ?""",
        (user_id, org_id),
    ).fetchone()
    return dict(row) if row else None


def load_full_atoms(conn, user: Dict[str, Any], version_ids: Sequence[str],
                    now_iso: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    读取原子完整结构字段；读取前后都经 eligibility 过滤（R1），返回 (可用原子, 已不满足资格的版本 ID)。
    顺序与 version_ids 一致。
    """
    ids = [v for v in dict.fromkeys(version_ids) if isinstance(v, str) and v]
    if not ids:
        return [], []
    eligible = filter_eligible_version_ids(conn, ids, user, now_iso)
    rows = {}
    if eligible:
        placeholders = ",".join("?" for _ in eligible)
        for row in conn.execute(
            f"""SELECT kv.id, kv.item_id, kv.title, kv.primary_category, kv.atom_type, kv.subject, kv.statement,
                       kv.conditions_json, kv.actions_json, kv.exceptions_json, kv.metric_definition_json,
                       kv.case_details_json, kv.customer_types_json, kv.business_scenes_json, kv.problem_tags_json
                FROM knowledge_versions kv
                WHERE kv.organization_id = ? AND kv.id IN ({placeholders})""",
            [user["organization_id"], *eligible],
        ).fetchall():
            rows[row["id"]] = row
    still = set(filter_eligible_version_ids(conn, list(rows), user, now_iso))
    atoms = []
    for vid in ids:
        row = rows.get(vid)
        if row is None or vid not in still:
            continue
        atom = {
            "atom_item_id": row["item_id"],
            "atom_version_id": vid,
            "title": row["title"],
            "primary_category": row["primary_category"],
            "atom_type": row["atom_type"],
            "subject": row["subject"],
            "statement": row["statement"] or "",
            "conditions": _text_list(row["conditions_json"]),
            "actions": _text_list(row["actions_json"]),
            "exceptions": _text_list(row["exceptions_json"]),
            "metric_definition": _json_value(row["metric_definition_json"], None),
            "case_details": _json_value(row["case_details_json"], None),
            "customer_types": _text_list(row["customer_types_json"]),
            "business_scenes": _text_list(row["business_scenes_json"]),
            "problem_tags": _text_list(row["problem_tags_json"]),
        }
        atom["default_roles"] = list(_default_roles(atom))
        atoms.append(atom)
    lost = [vid for vid in ids if vid not in {a["atom_version_id"] for a in atoms}]
    return atoms, lost


def _compact(atom: Dict[str, Any], keys: Sequence[str]) -> Dict[str, Any]:
    """只保留有内容的字段，减少模型输入长度；statement 始终保留。"""
    out = {}
    for key in keys:
        value = atom.get(key)
        if key == "statement" or value not in (None, "", [], {}):
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# G3 任务拆分
# ---------------------------------------------------------------------------

SPLIT_ATOM_FIELDS = ("atom_version_id", "primary_category", "atom_type", "title", "statement",
                     "conditions", "actions", "exceptions", "metric_definition", "recall_source")


def build_split_messages(scene: Dict[str, Any], focus_note: Optional[str], atoms: List[Dict[str, Any]],
                         recall_sources: Dict[str, str]) -> Tuple[str, List[Dict[str, str]]]:
    version, system_prompt = load_prompt(SPLIT_PROMPT_FILE)
    pool = []
    for atom in atoms:
        entry = dict(atom)
        entry["recall_source"] = recall_sources.get(atom["atom_version_id"], "tag")
        pool.append(_compact(entry, SPLIT_ATOM_FIELDS))
    user_content = {
        "scene": {
            "name": scene["name"],
            "description": scene.get("description") or "",
            "typical_problems": scene.get("typical_problems") or [],
        },
        "focus_note": focus_note or "",
        "max_tasks": SPLIT_MAX_TASKS,
        "atom_pool": pool,
    }
    return version, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请按规则拆分任务，只输出 JSON。\n" + _dumps(user_content)},
    ]


TASK_TYPES = tuple(load_skill_schema()["properties"]["task_type"]["enum"])


def validate_split_response(raw: str) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """拆分返回的格式校验（硬性问题会带错误清单重试一次）。原子 ID 是否在池内由 finalize_split 处理。"""
    data, parse_error = parse_model_json(raw)
    if parse_error:
        return None, [parse_error]
    errors: List[str] = []
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return None, ["缺少 tasks 数组"]
    for i, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            errors.append(f"第 {i} 个任务不是对象")
            continue
        for key in ("name", "goal", "split_reason"):
            if not isinstance(task.get(key), str) or not task.get(key).strip():
                errors.append(f"第 {i} 个任务缺少 {key}")
        if task.get("task_type") not in TASK_TYPES:
            errors.append(f"第 {i} 个任务的 task_type「{task.get('task_type')}」不在 {'/'.join(TASK_TYPES)} 中")
        ids = task.get("atom_version_ids")
        if not isinstance(ids, list) or not all(isinstance(v, str) for v in ids):
            errors.append(f"第 {i} 个任务的 atom_version_ids 必须是字符串数组")
    unused = data.get("unused_atoms", [])
    if unused is not None and not isinstance(unused, list):
        errors.append("unused_atoms 必须是数组")
    if not tasks and not str(data.get("no_task_reason") or "").strip():
        errors.append("tasks 为空时必须填写 no_task_reason")
    return (data if not errors else None), errors


def finalize_split(data: Dict[str, Any], atoms: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    程序校验拆分结果（FR05）：原子 ID 必须在池内（池外 ID 剔除并记录）；
    超过 5 个的任务、所用原子少于 2 条的任务不进入生成；3~8 条以外给提示；
    两个任务所用原子重合度超过阈值时互相标记「可能重复」；未使用原子均有原因。
    """
    by_vid = {a["atom_version_id"]: a for a in atoms}
    tasks: List[Dict[str, Any]] = []
    for index, raw_task in enumerate(data.get("tasks") or [], start=1):
        ids = [v.strip() for v in raw_task.get("atom_version_ids") or [] if isinstance(v, str) and v.strip()]
        ids = list(dict.fromkeys(ids))
        in_pool = [v for v in ids if v in by_vid]
        out_of_pool = [v for v in ids if v not in by_vid]
        hints: List[str] = []
        if out_of_pool:
            hints.append(f"已剔除原子池外的原子 ID：{'、'.join(out_of_pool)}")
        status, skip_reason = "to_generate", None
        if index > SPLIT_MAX_TASKS:
            status, skip_reason = "skipped", f"超出单批次 {SPLIT_MAX_TASKS} 个任务上限"
        elif len(in_pool) < SPLIT_MIN_ATOMS_FOR_GENERATION:
            status, skip_reason = "skipped", f"可用的知识少于 {SPLIT_MIN_ATOMS_FOR_GENERATION} 条，不进入生成"
        if in_pool and not (REF_COUNT_MIN <= len(in_pool) <= REF_COUNT_MAX):
            hints.append(f"所用知识 {len(in_pool)} 条，建议 {REF_COUNT_MIN} 到 {REF_COUNT_MAX} 条")
        if in_pool and all(
            by_vid[v].get("primary_category") == "项目案例" or by_vid[v].get("atom_type") == "案例" for v in in_pool
        ):
            hints.append("所用知识全部是项目案例，案例不能单独作为判断规则")
        tasks.append({
            "task_key": f"t{index}",
            "name": str(raw_task.get("name") or "").strip(),
            "goal": str(raw_task.get("goal") or "").strip(),
            "task_type": raw_task.get("task_type"),
            "split_reason": str(raw_task.get("split_reason") or "").strip(),
            "atom_version_ids": in_pool,
            "out_of_pool_ids": out_of_pool,
            "status": status,
            "skip_reason": skip_reason,
            "hints": hints,
            "possible_duplicate_of": [],
        })

    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            overlap = jaccard(a["atom_version_ids"], b["atom_version_ids"])
            if overlap > TASK_OVERLAP_THRESHOLD:
                a["possible_duplicate_of"].append({"task_key": b["task_key"], "overlap": round(overlap, 2)})
                b["possible_duplicate_of"].append({"task_key": a["task_key"], "overlap": round(overlap, 2)})

    used = {v for t in tasks if t["status"] == "to_generate" for v in t["atom_version_ids"]}
    unused: Dict[str, Dict[str, Any]] = {}
    for entry in data.get("unused_atoms") or []:
        if not isinstance(entry, dict):
            continue
        vid = str(entry.get("atom_version_id") or "").strip()
        if vid in by_vid and vid not in used and vid not in unused:
            unused[vid] = {"atom_version_id": vid, "title": by_vid[vid]["title"],
                           "reason": str(entry.get("reason") or "").strip() or "未说明原因", "source": "model"}
    for vid, atom in by_vid.items():
        if vid not in used and vid not in unused:
            unused[vid] = {"atom_version_id": vid, "title": atom["title"],
                           "reason": "没有任务使用，也未说明原因", "source": "program"}

    return {
        "tasks": tasks,
        "unused_atoms": list(unused.values()),
        "no_task_reason": str(data.get("no_task_reason") or "").strip() or None,
        "generate_count": sum(1 for t in tasks if t["status"] == "to_generate"),
    }


# ---------------------------------------------------------------------------
# G4 候选生成 / G5 自动修复 / 退回重生成
# ---------------------------------------------------------------------------

GENERATE_ATOM_FIELDS = ("atom_item_id", "atom_version_id", "title", "primary_category", "atom_type",
                        "default_roles", "subject", "statement", "conditions", "actions", "exceptions",
                        "metric_definition", "case_details", "customer_types", "problem_tags")


def build_generate_messages(scene: Dict[str, Any], task_def: Dict[str, Any], atoms: List[Dict[str, Any]],
                            focus_note: Optional[str] = None,
                            review_comment: Optional[str] = None,
                            field_groups: Optional[List[str]] = None,
                            previous_version: Optional[Dict[str, Any]] = None) -> Tuple[str, List[Dict[str, str]]]:
    version, system_prompt = load_prompt(GENERATE_PROMPT_FILE)
    user_content: Dict[str, Any] = {
        "scene": {"scene_id": scene["scene_id"], "name": scene["name"], "description": scene.get("description") or ""},
        "task": {
            "name": task_def.get("name"),
            "goal": task_def.get("goal"),
            "task_type": task_def.get("task_type"),
            "split_reason": task_def.get("split_reason"),
        },
        "allowed_atom_version_ids": [a["atom_version_id"] for a in atoms],
        "atoms": [_compact(a, GENERATE_ATOM_FIELDS) for a in atoms],
    }
    if focus_note:
        user_content["focus_note"] = focus_note
    intro = "请为以下任务生成一个 Skill 候选，只输出 JSON。"
    if review_comment:
        # 退回重生成（FR08 / FR12）：以原任务、原原子和审核意见为输入
        intro = "审核人退回了这个 Skill，请按审核意见重新生成完整的 Skill 候选，只输出 JSON。"
        user_content["review_comment"] = review_comment
        if field_groups:
            user_content["rewrite_field_groups"] = field_groups
        if previous_version is not None:
            user_content["previous_version"] = previous_version
    return version, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": intro + "\n" + _dumps(user_content)},
    ]


def build_repair_message(issues: List[Dict[str, Any]], allowed_ids: Sequence[str]) -> str:
    lines = [f"- [{i.get('code')}] {i.get('path') or '（整体）'}：{i.get('message')}" for i in issues[:40]]
    return (
        "上次输出未通过程序校验。请逐条修正以下问题，重新输出完整的 Skill JSON（不要只输出修改部分）：\n"
        + "\n".join(lines)
        + "\n允许引用的原子版本 ID 只有：" + "、".join(allowed_ids)
        + "\n不要为了通过校验编造原子中没有的内容；缺少依据的步骤标「无依据」。"
    )


def normalize_candidate(candidate: Dict[str, Any], scene_id: str) -> Tuple[Dict[str, Any], List[str]]:
    """系统字段由系统填写：去掉模型填写的 F/G 组系统字段，写入 schema_version 与所选场景（3.3、3.8）。"""
    result = dict(candidate)
    removed = [f for f in SYSTEM_FIELDS if f in result]
    for f in removed:
        result.pop(f, None)
    result["schema_version"] = SKILL_SCHEMA_VERSION
    result["scene_id"] = scene_id
    tools = result.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict):
                tool["implementation_status"] = "未实现"
    return result, removed


def _issue_dicts(issues: Iterable[ValidationIssue]) -> List[Dict[str, Any]]:
    return [i.to_dict() for i in issues]


def validate_model_candidate(conn, raw: str, scene_id: str, user: Dict[str, Any], pool_ids):
    """Shared parse/normalize/validate core; callers retain their transaction boundaries."""
    parsed, parse_error = parse_model_json(raw)
    if parse_error:
        issues = [ValidationIssue(HARD_ERROR, "MODEL_OUTPUT_NOT_JSON", "", parse_error).to_dict()]
        return None, [], None, issues
    candidate, removed = normalize_candidate(parsed, scene_id)
    report = validate_skill(conn, candidate, user, pool_ids=pool_ids)
    return candidate, removed, report, _issue_dicts(report.issues)


def _generation_raw(result: Dict[str, Any], calls=None) -> str:
    """Read full output from new task results and truncated output from legacy tasks."""
    index = result.get("raw_response_call")
    if isinstance(index, int) and calls and 0 <= index < len(calls):
        return calls[index].get("raw_response") or ""
    return result.get("raw_response") or ""


def _repair_previous_raw(conn, payload: Dict[str, Any]) -> str:
    task_id = payload.get("previous_generate_task_id")
    if task_id:
        row = conn.execute("SELECT result_json, model_calls_json FROM skill_generation_tasks WHERE id = ?", (task_id,)).fetchone()
        return _generation_raw(_loads(row["result_json"] if row else None, {}) or {},
                               _loads(row["model_calls_json"] if row else None, []) or [])
    return payload.get("previous_raw") or ""


# ---------------------------------------------------------------------------
# 批次与任务表
# ---------------------------------------------------------------------------

def _insert_task(conn, org_id: str, batch_id: str, task_type: str, task_key: Optional[str] = None,
                 payload: Optional[Dict[str, Any]] = None, max_attempts: int = 1) -> str:
    task_id = f"sgt_{uuid.uuid4().hex[:12]}"
    now_iso = _now()
    conn.execute(
        """INSERT INTO skill_generation_tasks (id, organization_id, batch_id, task_type, task_key, status,
               attempt_count, max_attempts, payload_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)""",
        (task_id, org_id, batch_id, task_type, task_key, max_attempts, _dumps(payload or {}), now_iso, now_iso),
    )
    return task_id


def _enqueue(task_ids: Iterable[str]) -> None:
    from tasks import enqueue_skill_generation_task
    for task_id in task_ids:
        enqueue_skill_generation_task(task_id)


def _get_batch_row(conn, batch_id: str):
    return conn.execute("SELECT * FROM skill_generation_batches WHERE id = ?", (batch_id,)).fetchone()


def _task_results(conn, batch_id: str) -> Dict[str, Dict[str, Any]]:
    row = conn.execute("SELECT task_results_json FROM skill_generation_batches WHERE id = ?", (batch_id,)).fetchone()
    return _loads(row["task_results_json"] if row else None, {}) or {}


def _update_task_result(conn, batch_id: str, task_key: str, **fields) -> None:
    results = _task_results(conn, batch_id)
    entry = results.setdefault(task_key, {"task_key": task_key})
    entry.update(fields)
    if "status" in fields:
        entry["status_label"] = TASK_RESULT_LABELS.get(fields["status"], fields["status"])
    conn.execute(
        "UPDATE skill_generation_batches SET task_results_json = ?, updated_at = ? WHERE id = ?",
        (_dumps(results), _now(), batch_id),
    )


def _finish_task(conn, task_id: str, status: str, result: Optional[Dict[str, Any]] = None,
                 error: Optional[str] = None, calls: Optional[List[Dict[str, Any]]] = None) -> None:
    now_iso = _now()
    sets = ["status = ?", "completed_at = ?", "updated_at = ?", "error_message = ?"]
    params: List[Any] = [status, now_iso, now_iso, error]
    if result is not None:
        sets.append("result_json = ?")
        params.append(_dumps(result))
    if calls is not None:
        sets.append("model_calls_json = ?")
        params.append(_dumps(calls))
    params.append(task_id)
    conn.execute(f"UPDATE skill_generation_tasks SET {', '.join(sets)} WHERE id = ?", params)


def _fail_batch(conn, batch_id: str, reason: str) -> None:
    now_iso = _now()
    conn.execute(
        """UPDATE skill_generation_batches SET status = 'failed', status_reason = ?, completed_at = ?, updated_at = ?
           WHERE id = ? AND status = 'running'""",
        (reason, now_iso, now_iso, batch_id),
    )
    conn.execute(
        """UPDATE skill_generation_tasks SET status = 'cancelled', completed_at = ?, updated_at = ?
           WHERE batch_id = ? AND status IN ('queued', 'running')""",
        (now_iso, now_iso, batch_id),
    )


def maybe_finish_batch(conn, batch_id: str) -> Optional[str]:
    """所有任务结束后按各任务结果确定批次状态（6.2）：全部成功=完成，部分成功=部分完成，全部未成功=失败。"""
    active = conn.execute(
        "SELECT COUNT(*) AS n FROM skill_generation_tasks WHERE batch_id = ? AND status IN ('queued', 'running')",
        (batch_id,),
    ).fetchone()["n"]
    if active:
        return None
    batch = _get_batch_row(conn, batch_id)
    if not batch or batch["status"] != "running":
        return None
    results = [r for r in (_loads(batch["task_results_json"], {}) or {}).values() if r.get("status") != "skipped"]
    split = _loads(batch["task_split_json"], None)
    if split is None:
        _fail_batch(conn, batch_id, "处理中断，未完成任务拆分")
        return "failed"
    stored = sum(1 for r in results if r.get("status") == "stored")
    failed_validation = sum(1 for r in results if r.get("status") == "validation_failed")
    failed_call = sum(1 for r in results if r.get("status") == "call_failed")
    if not results:
        status, reason = "no_tasks", split.get("no_task_reason") or "拆分出的任务都没有足够的知识支撑"
    elif stored == len(results):
        status, reason = "completed", f"全部 {stored} 个候选进入待审核"
    elif stored:
        status, reason = "partial", f"{stored} 个进入待审核，{failed_validation} 个校验未通过，{failed_call} 个生成失败"
    else:
        status, reason = "failed", f"没有候选进入待审核：{failed_validation} 个校验未通过，{failed_call} 个生成失败"
    now_iso = _now()
    conn.execute(
        """UPDATE skill_generation_batches SET status = ?, status_reason = ?, completed_at = ?, updated_at = ?
           WHERE id = ? AND status = 'running'""",
        (status, reason, now_iso, now_iso, batch_id),
    )
    return status


# ---------------------------------------------------------------------------
# G1 发起
# ---------------------------------------------------------------------------

def start_generation_batch(conn, user: Dict[str, Any], scene_id: str, focus_note: Optional[str] = None) -> Dict[str, Any]:
    """
    G1：校验场景与发起条件，创建批次与召回任务（调用方在事务提交后入队 task_id）。
    - 同一场景已有进行中的批次时拒绝（AC07，唯一部分索引兜底）；
    - 可用原子少于 3 条时拒绝（AC04，口径同场景卡片：recall_atoms 返回数，决策 B1）。
    """
    org_id = user["organization_id"]
    scene = sc.get_scene(conn, org_id, scene_id)
    if not scene:
        raise SceneCatalogError(404, "场景不存在")
    if scene["status"] != "active":
        raise SceneCatalogError(409, "这个场景已停用，不能生成")
    focus = sc.clean_tag(focus_note) if focus_note else ""
    if len(focus) > FOCUS_NOTE_MAX:
        raise SceneCatalogError(400, f"生成侧重说明不能超过 {FOCUS_NOTE_MAX} 字")
    running = conn.execute(
        "SELECT id FROM skill_generation_batches WHERE organization_id = ? AND scene_id = ? AND status = 'running'",
        (org_id, scene_id),
    ).fetchone()
    if running:
        raise SceneCatalogError(409, "这个场景正在生成，请等当前这一批完成后再发起")
    recall = sc.recall_atoms(conn, user, scene)
    if recall["stats"]["returned"] < SCENE_MIN_ATOMS_FOR_GENERATION:
        raise SceneCatalogError(
            409, f"可用知识不足 {SCENE_MIN_ATOMS_FOR_GENERATION} 条，请先在知识管理中导入或确认相关资料"
        )
    batch_id = f"sgb_{uuid.uuid4().hex[:12]}"
    now_iso = _now()
    try:
        conn.execute(
            """INSERT INTO skill_generation_batches (id, organization_id, scene_id, initiated_by, initiated_at,
                   focus_note, model_name, prompt_version, task_results_json, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 'running', ?)""",
            (batch_id, org_id, scene_id, user["id"], now_iso, focus or None, config.DEEPSEEK_MODEL,
             _dumps(prompt_versions()), now_iso),
        )
    except sqlite3.IntegrityError:
        raise SceneCatalogError(409, "这个场景正在生成，请等当前这一批完成后再发起")
    task_id = _insert_task(conn, org_id, batch_id, "recall_atoms")
    sc.write_audit(conn, org_id, user["id"], "skill_batch_start", "skill_generation_batch", batch_id,
                   {"scene_id": scene_id, "scene_name": scene["name"], "focus_note": focus or None,
                    "available_atoms": recall["stats"]["returned"]}, now_iso)
    return {"batch_id": batch_id, "task_id": task_id, "scene_id": scene_id, "status": "running"}


# ---------------------------------------------------------------------------
# 任务执行
# ---------------------------------------------------------------------------

class _TaskContext:
    def __init__(self, task: Dict[str, Any], batch: Dict[str, Any], attempt: int):
        self.task = task
        self.batch = batch
        self.attempt = attempt
        self.task_id = task["id"]
        self.batch_id = batch["id"]
        self.org_id = batch["organization_id"]
        self.payload = _loads(task.get("payload_json"), {}) or {}
        self.calls: List[Dict[str, Any]] = _loads(task.get("model_calls_json"), []) or []


def run_generation_task(task_id: str) -> None:
    """后台入口：领取任务并按类型执行；未预期的异常把任务与批次收敛到失败，不留下进行中状态。"""
    try:
        ctx = _claim_task(task_id)
        if ctx is None:
            return
        handler = {
            "recall_atoms": _handle_recall,
            "split_tasks": _handle_split,
            "generate_skill": _handle_generate,
            "validate_store": _handle_validate_store,
        }[ctx.task["task_type"]]
        handler(ctx)
    except Exception as exc:  # 防御：任何意外都不能让批次卡在进行中
        logger.exception("Skill generation task %s crashed", task_id)
        try:
            _crash_task(task_id, exc)
        except Exception:
            logger.exception("Failed to record crash for %s", task_id)


def _claim_task(task_id: str) -> Optional[_TaskContext]:
    with _write_tx() as conn:
        task = conn.execute("SELECT * FROM skill_generation_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task or task["status"] not in ("queued", "running"):
            return None
        batch = _get_batch_row(conn, task["batch_id"])
        if not batch or batch["status"] != "running":
            _finish_task(conn, task_id, "cancelled", error="批次已结束")
            return None
        now_iso = _now()
        conn.execute(
            """UPDATE skill_generation_tasks SET status = 'running', attempt_count = attempt_count + 1,
                   started_at = ?, updated_at = ?, error_message = NULL WHERE id = ?""",
            (now_iso, now_iso, task_id),
        )
        return _TaskContext(dict(task), dict(batch), int(task["attempt_count"] or 0) + 1)


def _crash_task(task_id: str, exc: BaseException) -> None:
    with _write_tx() as conn:
        task = conn.execute("SELECT * FROM skill_generation_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task or task["status"] not in ("queued", "running"):
            return
        message = f"内部错误：{_error_text(exc)}"
        _finish_task(conn, task_id, "failed", error=message)
        if task["task_type"] in ("recall_atoms", "split_tasks"):
            _fail_batch(conn, task["batch_id"], "处理出错，请重新发起")
        else:
            key = task["task_key"]
            if key:
                _update_task_result(conn, task["batch_id"], key, status="call_failed", error=message)
            maybe_finish_batch(conn, task["batch_id"])


def _retry_or_fail_call(ctx: _TaskContext, error: str) -> bool:
    """模型调用失败或超时：未到上限则重新排队（M01 任务机制），返回 True；否则返回 False 由调用方收尾。"""
    if ctx.attempt < int(ctx.task.get("max_attempts") or 1):
        with _write_tx() as conn:
            conn.execute(
                """UPDATE skill_generation_tasks SET status = 'queued', error_message = ?, model_calls_json = ?,
                       updated_at = ? WHERE id = ? AND status = 'running'""",
                (error, _dumps(ctx.calls), _now(), ctx.task_id),
            )
        _enqueue([ctx.task_id])
        return True
    return False


def _load_scene_and_actor(conn, ctx: _TaskContext) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    actor = load_actor(conn, ctx.batch["initiated_by"], ctx.org_id)
    if not actor or actor.get("role") != "admin" or actor.get("account_status") != "active":
        return None, None, "发起人已不是可用的管理员账号"
    scene = sc.get_scene(conn, ctx.org_id, ctx.batch["scene_id"])
    if not scene:
        return None, None, "场景已被删除"
    return scene, actor, None


# ---- G2 召回 ----------------------------------------------------------------

def _handle_recall(ctx: _TaskContext) -> None:
    next_ids: List[str] = []
    # 召回可能加载向量模型，放在只读连接中执行，不占用写锁
    with _read_conn() as conn:
        scene, actor, problem = _load_scene_and_actor(conn, ctx)
        if not problem and scene["status"] != "active":
            problem = "场景已停用"
        recall = None if problem else sc.recall_atoms(conn, actor, scene, focus_note=ctx.batch.get("focus_note"))
    with _write_tx() as conn:
        if problem:
            _finish_task(conn, ctx.task_id, "failed", error=problem)
            _fail_batch(conn, ctx.batch_id, problem)
            return
        pool = {
            "atoms": [
                {
                    "atom_version_id": a["version_id"],
                    "atom_item_id": a["item_id"],
                    "title": a["title"],
                    "primary_category": a["primary_category"],
                    "atom_type": a["atom_type"],
                    "recall_source": a["recall_source"],
                    "matched_by": a["matched_by"],
                    "matched_tags": a["matched_tags"],
                    "semantic_score": a["semantic_score"],
                }
                for a in recall["atoms"]
            ],
            "stats": recall["stats"],
            "category_coverage": recall["category_coverage"],
            "semantic_query": recall["semantic_query"],
            "semantic_error": recall["semantic_error"],
            "config": recall["config"],
            "recalled_at": _now(),
        }
        conn.execute(
            "UPDATE skill_generation_batches SET atom_pool_json = ?, updated_at = ? WHERE id = ?",
            (_dumps(pool), _now(), ctx.batch_id),
        )
        result = {"returned": recall["stats"]["returned"], "tag_in_pool": recall["stats"]["tag_in_pool"],
                  "semantic_in_pool": recall["stats"]["semantic_in_pool"], "semantic_error": recall["semantic_error"]}
        if recall["stats"]["returned"] < SCENE_MIN_ATOMS_FOR_GENERATION:
            reason = f"可用知识不足 {SCENE_MIN_ATOMS_FOR_GENERATION} 条"
            _finish_task(conn, ctx.task_id, "failed", result=result, error=reason)
            _fail_batch(conn, ctx.batch_id, reason)
            return
        _finish_task(conn, ctx.task_id, "completed", result=result)
        next_ids.append(_insert_task(conn, ctx.org_id, ctx.batch_id, "split_tasks",
                                     max_attempts=SKILL_MODEL_CALL_MAX_ATTEMPTS))
    _enqueue(next_ids)


# ---- G3 拆分 ----------------------------------------------------------------

def _pool_atoms(batch: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (_loads(batch.get("atom_pool_json"), {}) or {}).get("atoms") or []


def _handle_split(ctx: _TaskContext) -> None:
    atoms: List[Dict[str, Any]] = []
    lost: List[str] = []
    with _read_conn() as conn:
        scene, actor, problem = _load_scene_and_actor(conn, ctx)
        if not problem:
            pool = _pool_atoms(ctx.batch)
            atoms, lost = load_full_atoms(conn, actor, [a["atom_version_id"] for a in pool])
    if problem or len(atoms) < SCENE_MIN_ATOMS_FOR_GENERATION:
        reason = problem or f"原子池中仍可用的知识不足 {SCENE_MIN_ATOMS_FOR_GENERATION} 条"
        with _write_tx() as conn:
            _finish_task(conn, ctx.task_id, "failed", error=reason)
            _fail_batch(conn, ctx.batch_id, reason)
        return

    recall_sources = {a["atom_version_id"]: a.get("recall_source") or "tag" for a in _pool_atoms(ctx.batch)}
    version, messages = build_split_messages(scene, ctx.batch.get("focus_note"), atoms, recall_sources)
    data, errors, model_used = None, ["未调用"], None
    for round_no in range(SPLIT_FORMAT_RETRY_ROUNDS + 1):
        try:
            raw, model_used, elapsed = _invoke_model(messages)
        except Exception as exc:
            error = _error_text(exc)
            ctx.calls.append(_call_record("split", version, ctx.attempt, round=round_no, ok=False, error=error))
            if not isinstance(exc, ModelNotConfigured) and _retry_or_fail_call(ctx, error):
                return
            with _write_tx() as conn:
                _finish_task(conn, ctx.task_id, "failed", error=error, calls=ctx.calls)
                _fail_batch(conn, ctx.batch_id, "拆分任务时调用失败，请稍后重新发起")
            return
        data, errors = validate_split_response(raw)
        ctx.calls.append(_call_record("split", version, ctx.attempt, round=round_no, model=model_used,
                                      elapsed_seconds=elapsed, ok=not errors, errors=errors[:20], raw_response=raw))
        if not errors:
            break
        messages = messages[:2] + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "上次输出未通过校验，请修正以下问题后重新输出完整 JSON：\n- " + "\n- ".join(errors[:20])},
        ]

    next_ids: List[str] = []
    with _write_tx() as conn:
        if errors or data is None:
            reason = "拆分结果不符合格式要求：" + "；".join(errors[:5])
            _finish_task(conn, ctx.task_id, "failed", error=reason, calls=ctx.calls)
            _fail_batch(conn, ctx.batch_id, "拆分结果不符合要求，请稍后重新发起")
            return
        split = finalize_split(data, atoms)
        split.update({"model_name": model_used, "prompt_version": version, "lost_atoms": lost, "split_at": _now()})
        conn.execute(
            "UPDATE skill_generation_batches SET task_split_json = ?, model_name = ?, updated_at = ? WHERE id = ?",
            (_dumps(split), model_used, _now(), ctx.batch_id),
        )
        _finish_task(conn, ctx.task_id, "completed", calls=ctx.calls,
                     result={"task_count": len(split["tasks"]), "generate_count": split["generate_count"]})
        task_results = {}
        for task in split["tasks"]:
            if task["status"] == "skipped":
                task_results[task["task_key"]] = {"task_key": task["task_key"], "name": task["name"],
                    "status": "skipped", "status_label": TASK_RESULT_LABELS["skipped"],
                    "skip_reason": task["skip_reason"]}
                continue
            task_results[task["task_key"]] = {"task_key": task["task_key"], "name": task["name"],
                "status": "generating", "status_label": TASK_RESULT_LABELS["generating"], "repair_count": 0}
            next_ids.append(_insert_task(conn, ctx.org_id, ctx.batch_id, "generate_skill", task["task_key"],
                                         {"task_key": task["task_key"], "mode": "initial", "round": 0},
                                         max_attempts=SKILL_MODEL_CALL_MAX_ATTEMPTS))
        conn.execute("UPDATE skill_generation_batches SET task_results_json = ?, updated_at = ? WHERE id = ?",
                     (_dumps(task_results), _now(), ctx.batch_id))
        if not next_ids:
            maybe_finish_batch(conn, ctx.batch_id)
    _enqueue(next_ids)


# ---- G4 生成 ----------------------------------------------------------------

def _split_task_def(batch: Dict[str, Any], task_key: str) -> Optional[Dict[str, Any]]:
    split = _loads(batch.get("task_split_json"), {}) or {}
    for task in split.get("tasks") or []:
        if task.get("task_key") == task_key:
            return task
    return None


def _handle_generate(ctx: _TaskContext) -> None:
    task_key = ctx.payload.get("task_key")
    round_no = int(ctx.payload.get("round") or 0)
    with _read_conn() as conn:
        scene, actor, problem = _load_scene_and_actor(conn, ctx)
        task_def = _split_task_def(ctx.batch, task_key)
        atoms: List[Dict[str, Any]] = []
        if not problem and task_def:
            atoms, _lost = load_full_atoms(conn, actor, task_def["atom_version_ids"])
        previous_raw = _repair_previous_raw(conn, ctx.payload) if ctx.payload.get("mode") == "repair" else ""
    if problem or not task_def or len(atoms) < SPLIT_MIN_ATOMS_FOR_GENERATION:
        reason = problem or ("任务定义缺失" if not task_def else "生成前所用知识已失效，不足 2 条")
        with _write_tx() as conn:
            _finish_task(conn, ctx.task_id, "failed", error=reason)
            _update_task_result(conn, ctx.batch_id, task_key, status="call_failed", error=reason)
            maybe_finish_batch(conn, ctx.batch_id)
        return

    version, messages = build_generate_messages(scene, task_def, atoms, ctx.batch.get("focus_note"))
    allowed = [a["atom_version_id"] for a in atoms]
    if ctx.payload.get("mode") == "repair":
        messages = messages + [
            {"role": "assistant", "content": previous_raw},
            {"role": "user", "content": build_repair_message(ctx.payload.get("issues") or [], allowed)},
        ]
    try:
        raw, model_used, elapsed = _invoke_model(
            messages, max_tokens=config.SKILL_MODEL_MAX_OUTPUT_TOKENS, timeout=config.SKILL_MODEL_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        error = _error_text(exc)
        ctx.calls.append(_call_record("generate" if round_no == 0 else "repair", version, ctx.attempt,
                                      round=round_no, ok=False, error=error))
        if not isinstance(exc, ModelNotConfigured) and _retry_or_fail_call(ctx, error):
            return
        with _write_tx() as conn:
            _finish_task(conn, ctx.task_id, "failed", error=error, calls=ctx.calls)
            _update_task_result(conn, ctx.batch_id, task_key, status="call_failed", error="生成调用失败：" + error)
            maybe_finish_batch(conn, ctx.batch_id)
        return

    ctx.calls.append(_call_record("generate" if round_no == 0 else "repair", version, ctx.attempt,
                                  round=round_no, model=model_used, elapsed_seconds=elapsed, ok=True,
                                  raw_response=raw))
    with _write_tx() as conn:
        raw_result = ({"raw_response_call": len(ctx.calls) - 1} if len(raw) <= RAW_RESPONSE_KEEP
                      else {"raw_response": raw})
        _finish_task(conn, ctx.task_id, "completed", calls=ctx.calls,
                     result={**raw_result, "model_name": model_used,
                             "prompt_version": version, "allowed_atom_version_ids": allowed})
        _update_task_result(conn, ctx.batch_id, task_key, status="validating")
        next_id = _insert_task(conn, ctx.org_id, ctx.batch_id, "validate_store", task_key,
                               {"task_key": task_key, "generate_task_id": ctx.task_id, "round": round_no})
    _enqueue([next_id])


# ---- G5 校验 / G6 入库 --------------------------------------------------------

def find_similar_skills(conn, org_id: str, item_ids: Iterable[str], exclude_skill_id: Optional[str] = None,
                        threshold: float = SKILL_SIMILAR_THRESHOLD) -> List[Dict[str, Any]]:
    """FR08：与已有 Skill（待审核、已通过、待复核）当前版本所用原子条目的重合度超过阈值即视为相似。"""
    mine = set(item_ids)
    if not mine:
        return []
    placeholders = ",".join("?" for _ in ACTIVE_SKILL_STATUSES)
    rows = conn.execute(
        f"""SELECT s.id AS skill_id, s.status, r.atom_item_id, sv.skill_json
            FROM skills s
            JOIN skill_versions sv ON sv.id = s.current_version_id
            JOIN skill_atom_refs r ON r.skill_version_id = s.current_version_id
            WHERE s.organization_id = ? AND s.status IN ({placeholders}) AND s.id != ?""",
        [org_id, *ACTIVE_SKILL_STATUSES, exclude_skill_id or ""],
    ).fetchall()
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row["skill_id"] not in grouped:
            grouped[row["skill_id"]] = {"items": set(), "status": row["status"],
                                       "name": (_loads(row["skill_json"], {}) or {}).get("name")}
        entry = grouped[row["skill_id"]]
        entry["items"].add(row["atom_item_id"])
    similar = []
    for skill_id, entry in grouped.items():
        overlap = jaccard(mine, entry["items"])
        if overlap > threshold:
            similar.append({"skill_id": skill_id, "name": entry["name"], "status": entry["status"],
                            "overlap": round(overlap, 2)})
    similar.sort(key=lambda x: -x["overlap"])
    return similar


def insert_atom_refs(conn, org_id: str, skill_id: str, version_id: str, refs: Iterable[Dict[str, Any]],
                     snapshots: Dict[str, Dict[str, Any]], now_iso: str) -> None:
    """写入引用关系与引用时的原子快照（PRD 4.2、R2、R7）；快照随版本锁定，之后不可修改。"""
    for ref in refs:
        vid = ref.get("atom_version_id")
        snap = snapshots.get(vid) or {}
        conn.execute(
            """INSERT INTO skill_atom_refs (id, organization_id, skill_id, skill_version_id, atom_item_id,
                   atom_version_id, role, snapshot_title, snapshot_statement, snapshot_conditions_json,
                   snapshot_actions_json, snapshot_exceptions_json, snapshot_primary_category, snapshot_atom_type,
                   snapshot_metric_definition_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"sar_{uuid.uuid4().hex[:12]}", org_id, skill_id, version_id, ref.get("atom_item_id"), vid,
                ref.get("role"), snap.get("title"), snap.get("statement"),
                _dumps(snap.get("conditions") or []), _dumps(snap.get("actions") or []),
                _dumps(snap.get("exceptions") or []), snap.get("primary_category"), snap.get("atom_type"),
                _dumps(snap.get("metric_definition")) if snap.get("metric_definition") is not None else None,
                now_iso,
            ),
        )


def store_candidate(conn, *, org_id: str, scene_id: str, batch_id: Optional[str], task_key: Optional[str],
                    candidate: Dict[str, Any], report, generation: Dict[str, Any], created_by: str,
                    skill_id: Optional[str] = None, version_number: int = 1, version_kind: str = "ai_original",
                    based_on_version_id: Optional[str] = None) -> Dict[str, Any]:
    """
    G6 入库：写入 skills（首版时）、skill_versions（第 1 版为 AI 原稿，触发器保证之后不可修改）、
    skill_atom_refs（含引用时的原子快照，R2/R7），状态为待审核；标记与已有 Skill 相似（FR08）。
    """
    now_iso = _now()
    is_new = skill_id is None
    skill_id = skill_id or f"sk_{uuid.uuid4().hex[:12]}"
    version_id = f"skv_{uuid.uuid4().hex[:12]}"
    refs = [r for r in candidate.get("knowledge_refs") or [] if isinstance(r, dict)]
    item_ids = {r.get("atom_item_id") for r in refs if r.get("atom_item_id")}
    similar = find_similar_skills(conn, org_id, item_ids, exclude_skill_id=skill_id)

    skill_json = apply_derived_fields(candidate, report)
    skill_json.update({
        "schema_version": SKILL_SCHEMA_VERSION,
        "skill_id": skill_id,
        "version_number": version_number,
        "status": SKILL_STATUS_LABELS["pending_review"],
        "generation": {
            "scene_id": scene_id,
            "batch_id": batch_id,
            "model_name": generation.get("model_name"),
            "prompt_version": generation.get("prompt_version"),
            "generated_at": now_iso,
        },
    })
    if is_new:
        conn.execute(
            """INSERT INTO skills (id, organization_id, scene_id, current_version_id, status, visibility, created_by,
                   created_at, updated_at, batch_id, task_key, similar_skills_json, revision_token)
               VALUES (?, ?, ?, NULL, 'pending_review', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (skill_id, org_id, scene_id, report.visibility, created_by, now_iso, now_iso, batch_id, task_key,
             _dumps(similar), uuid.uuid4().hex),
        )
    conn.execute(
        """INSERT INTO skill_versions (id, skill_id, organization_id, version_number, schema_version, skill_json,
               version_kind, based_on_version_id, batch_id, generation_json, validation_report_json,
               revision_token, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (version_id, skill_id, org_id, version_number, SKILL_SCHEMA_VERSION, _dumps(skill_json), version_kind,
         based_on_version_id, batch_id, _dumps({**generation, "generated_at": now_iso}),
         _dumps(report.to_dict()), uuid.uuid4().hex, created_by, now_iso),
    )
    insert_atom_refs(conn, org_id, skill_id, version_id, refs, report.atom_snapshots, now_iso)
    conn.execute(
        """UPDATE skills SET current_version_id = ?, status = 'pending_review', visibility = ?,
               similar_skills_json = ?, generation_error_json = NULL, revision_token = ?, updated_at = ?
           WHERE id = ?""",
        (version_id, report.visibility, _dumps(similar), uuid.uuid4().hex, now_iso, skill_id),
    )
    return {"skill_id": skill_id, "version_id": version_id, "similar_skills": similar,
            "visibility": report.visibility}


def _handle_validate_store(ctx: _TaskContext) -> None:
    task_key = ctx.payload.get("task_key")
    round_no = int(ctx.payload.get("round") or 0)
    next_ids: List[str] = []
    with _write_tx() as conn:
        scene, actor, problem = _load_scene_and_actor(conn, ctx)
        gen_row = conn.execute("SELECT * FROM skill_generation_tasks WHERE id = ?",
                               (ctx.payload.get("generate_task_id"),)).fetchone()
        gen_result = _loads(gen_row["result_json"] if gen_row else None, {}) or {}
        if problem or not gen_row:
            reason = problem or "找不到生成结果"
            _finish_task(conn, ctx.task_id, "failed", error=reason)
            _update_task_result(conn, ctx.batch_id, task_key, status="call_failed", error=reason)
            maybe_finish_batch(conn, ctx.batch_id)
            return
        raw = _generation_raw(gen_result, _loads(gen_row["model_calls_json"], []) or [])
        pool_ids = [a["atom_version_id"] for a in _pool_atoms(ctx.batch)]
        candidate, removed, report, issues = validate_model_candidate(conn, raw, scene["scene_id"], actor, pool_ids)
        hard = [i for i in issues if i["level"] == HARD_ERROR]
        results = _task_results(conn, ctx.batch_id)
        repair_count = int((results.get(task_key) or {}).get("repair_count") or 0)
        summary = {"round": round_no, "passed": not hard, "hard_error_count": len(hard),
                   "hint_count": len(issues) - len(hard), "issues": issues[:ISSUES_KEEP],
                   "removed_system_fields": removed}

        if hard and round_no < SKILL_AUTO_REPAIR_ROUNDS:
            _finish_task(conn, ctx.task_id, "completed", result={**summary, "action": "auto_repair"})
            _update_task_result(conn, ctx.batch_id, task_key, status="repairing", repair_count=repair_count + 1,
                                last_hard_errors=hard[:10])
            next_ids.append(_insert_task(
                conn, ctx.org_id, ctx.batch_id, "generate_skill", task_key,
                {"task_key": task_key, "mode": "repair", "round": round_no + 1,
                 "previous_generate_task_id": gen_row["id"],
                 "issues": hard[:40]},
                max_attempts=SKILL_MODEL_CALL_MAX_ATTEMPTS,
            ))
        elif hard:
            task_def = _split_task_def(ctx.batch, task_key) or {}
            skill_id = f"sk_{uuid.uuid4().hex[:12]}"
            now_iso = _now()
            error_payload = {
                "issues": hard[:ISSUES_KEEP],
                "hint_count": len(issues) - len(hard),
                "repair_count": repair_count,
                "candidate": candidate,
                "generate_task_id": gen_row["id"],
                "task": {k: task_def.get(k) for k in ("task_key", "name", "goal", "task_type", "split_reason",
                                                      "atom_version_ids")},
                "model_name": gen_result.get("model_name"),
                "prompt_version": gen_result.get("prompt_version"),
            }
            conn.execute(
                """INSERT INTO skills (id, organization_id, scene_id, current_version_id, status, visibility,
                       created_by, created_at, updated_at, batch_id, task_key, generation_error_json, revision_token)
                   VALUES (?, ?, ?, NULL, 'validation_failed', 'admin_only', ?, ?, ?, ?, ?, ?, ?)""",
                (skill_id, ctx.org_id, scene["scene_id"], ctx.batch["initiated_by"], now_iso, now_iso,
                 ctx.batch_id, task_key, _dumps(error_payload), uuid.uuid4().hex),
            )
            _finish_task(conn, ctx.task_id, "completed", result={**summary, "action": "validation_failed",
                                                                  "skill_id": skill_id})
            _update_task_result(conn, ctx.batch_id, task_key, status="validation_failed", skill_id=skill_id,
                                hard_error_count=len(hard), errors=[i["message"] for i in hard[:5]])
            sc.write_audit(conn, ctx.org_id, ctx.batch["initiated_by"], "skill_candidate_validation_failed",
                           "skill", skill_id, {"batch_id": ctx.batch_id, "task_key": task_key,
                                               "hard_error_count": len(hard)}, now_iso)
            maybe_finish_batch(conn, ctx.batch_id)
        else:
            task_def = _split_task_def(ctx.batch, task_key) or {}
            generation = {
                "scene_id": scene["scene_id"],
                "batch_id": ctx.batch_id,
                "model_name": gen_result.get("model_name"),
                "prompt_version": gen_result.get("prompt_version"),
                "prompt_versions": _loads(ctx.batch.get("prompt_version"), {}),
                "task": {k: task_def.get(k) for k in ("task_key", "name", "goal", "task_type", "split_reason",
                                                      "atom_version_ids")},
                "focus_note": ctx.batch.get("focus_note"),
                "repair_count": repair_count,
                "generate_task_id": gen_row["id"],
                "removed_system_fields": removed,
            }
            stored = store_candidate(conn, org_id=ctx.org_id, scene_id=scene["scene_id"], batch_id=ctx.batch_id,
                                     task_key=task_key, candidate=candidate, report=report, generation=generation,
                                     created_by=ctx.batch["initiated_by"])
            _finish_task(conn, ctx.task_id, "completed", result={**summary, "action": "stored", **stored})
            _update_task_result(
                conn, ctx.batch_id, task_key, status="stored", skill_id=stored["skill_id"],
                hint_count=len(issues), unsupported_count=len(report.unsupported_items),
                similar_skills=stored["similar_skills"],
            )
            sc.write_audit(conn, ctx.org_id, ctx.batch["initiated_by"], "skill_candidate_created", "skill",
                           stored["skill_id"], {"batch_id": ctx.batch_id, "task_key": task_key,
                                                "version_id": stored["version_id"], "repair_count": repair_count},
                           _now())
            maybe_finish_batch(conn, ctx.batch_id)
    _enqueue(next_ids)


# ---------------------------------------------------------------------------
# 退回重生成（FR08 / FR12；M02-D 接入界面）
# ---------------------------------------------------------------------------

def regenerate_skill_version(skill_id: str, user: Dict[str, Any], review_comment: str,
                             field_groups: Optional[List[str]] = None, *,
                             on_stored: Optional[Callable[[Any, Dict[str, Any]], None]] = None,
                             refresh_atoms: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    以原任务、原原子和审核意见为输入复用 G4 生成同一 Skill 的新版本。
    同步执行：生成 -> 校验 -> 硬性错误自动修复一次 -> 通过则写入新版本并回到待审核。
    不通过或调用失败时不改动 Skill，返回问题清单。

    - 有当前版本（待审核被退回）：意见必填，结果为 AI 重生成版（FR08 / FR12）。
    - 没有版本（校验未通过后「重新生成」，PRD 7.2 / FR07）：以上次的候选与问题清单为补充输入，
      意见可空；通过后产生第 1 版 AI 原稿。
    on_stored(conn, result)：M02-D 在同一事务内写审核记录、轮换令牌；抛出异常则整笔回滚。
    refresh_atoms：M02-E 原子变更后退回重生成时，{旧原子版本: 当前生效的新版本}；新版本替换旧版本交给模型，
    并视为可引用（不在原批次原子池内也可以）。
    状态置为「生成中」与后台执行由 M02-D（skill_review.py）负责。
    """
    comment = (review_comment or "").strip()
    org_id = user["organization_id"]
    with _read_conn() as conn:
        skill = conn.execute("SELECT * FROM skills WHERE id = ? AND organization_id = ?", (skill_id, org_id)).fetchone()
        if not skill:
            raise SceneCatalogError(404, "Skill 不存在")
        current = None
        error_payload = None
        if skill["current_version_id"]:
            if not comment:
                raise SceneCatalogError(400, "退回意见不能为空")
            current = conn.execute("SELECT * FROM skill_versions WHERE id = ?", (skill["current_version_id"],)).fetchone()
            generation = _loads(current["generation_json"], {}) or {}
            previous = _loads(current["skill_json"], {}) or {}
            ref_rows = conn.execute("SELECT atom_version_id FROM skill_atom_refs WHERE skill_version_id = ?",
                                    (current["id"],)).fetchall()
            ref_ids = [r["atom_version_id"] for r in ref_rows]
        else:
            error_payload = _loads(skill["generation_error_json"], None)
            if not isinstance(error_payload, dict):
                raise SceneCatalogError(409, "找不到上次生成的记录，不能重新生成")
            generation = {
                "scene_id": skill["scene_id"],
                "batch_id": skill["batch_id"],
                "task": error_payload.get("task") or {},
            }
            previous = error_payload.get("candidate") if isinstance(error_payload.get("candidate"), dict) else {}
            ref_ids = [r.get("atom_version_id") for r in (previous.get("knowledge_refs") or []) if isinstance(r, dict)]
            if not comment:
                plain = sorted({plain_issue(i) for i in error_payload.get("issues") or []})
                comment = "上次生成未通过程序检查，请修正以下问题后重新生成：" + ("；".join(plain) or "格式不完整")
        task_def = generation.get("task") or {}
        scene = sc.get_scene(conn, org_id, skill["scene_id"])
        batch = _get_batch_row(conn, skill["batch_id"]) if skill["batch_id"] else None
        original_ids = list(dict.fromkeys((task_def.get("atom_version_ids") or []) + [v for v in ref_ids if v]))
        if batch:
            # 只提供本批次原子池内的原子（校验未通过的候选可能引用了池外原子）
            batch_pool = {a["atom_version_id"] for a in _pool_atoms(dict(batch))}
            original_ids = [v for v in original_ids if v in batch_pool or v in (refresh_atoms or {})]
        if refresh_atoms:
            original_ids = list(dict.fromkeys(refresh_atoms.get(v, v) for v in original_ids))
        atoms, lost = load_full_atoms(conn, user, original_ids)
    if not scene:
        raise SceneCatalogError(409, "场景已被删除，不能重生成")
    if not atoms:
        raise SceneCatalogError(409, "原来引用的知识都已失效，不能重生成")
    focus_note = generation.get("focus_note") or (dict(batch).get("focus_note") if batch else None)
    version, messages = build_generate_messages(
        scene, task_def or {"name": previous.get("name"), "goal": previous.get("goal"),
                            "task_type": previous.get("task_type")},
        atoms, focus_note, review_comment=comment, field_groups=field_groups,
        previous_version={k: v for k, v in previous.items() if k not in SYSTEM_FIELDS},
    )
    allowed = [a["atom_version_id"] for a in atoms]
    if error_payload is not None and error_payload.get("issues"):
        # 校验未通过后重新生成：把上次的硬性问题单独作为一条消息交给模型（与 FR07 自动修复的输入一致）
        messages.append({"role": "user", "content": build_repair_message(error_payload.get("issues") or [], allowed)})
    pool_ids = [a["atom_version_id"] for a in _pool_atoms(dict(batch))] if batch else allowed
    if refresh_atoms:
        pool_ids = list(dict.fromkeys(list(pool_ids) + list(refresh_atoms.values())))
    calls: List[Dict[str, Any]] = []
    last_issues: List[Dict[str, Any]] = []
    last_candidate: Optional[Dict[str, Any]] = None
    for round_no in range(SKILL_AUTO_REPAIR_ROUNDS + 1):
        raw, model_used = None, None
        for attempt in range(1, SKILL_MODEL_CALL_MAX_ATTEMPTS + 1):
            try:
                raw, model_used, elapsed = _invoke_model(
                    messages, max_tokens=config.SKILL_MODEL_MAX_OUTPUT_TOKENS, timeout=config.SKILL_MODEL_TIMEOUT_SECONDS,
                )
                calls.append(_call_record("regenerate", version, attempt, round=round_no, model=model_used,
                                          elapsed_seconds=elapsed, ok=True, raw_response=raw))
                break
            except Exception as exc:
                calls.append(_call_record("regenerate", version, attempt, round=round_no, ok=False,
                                          error=_error_text(exc)))
                if isinstance(exc, ModelNotConfigured):
                    break
        if raw is None:
            return {"ok": False, "reason": "call_failed", "model_calls": calls, "prompt_version": version}
        with _write_tx() as conn:
            candidate, removed, report, issues = validate_model_candidate(conn, raw, scene["scene_id"], user, pool_ids)
            last_issues = [i for i in issues if i["level"] == HARD_ERROR]
            if candidate is not None:
                last_candidate = candidate
                if not last_issues:
                    number = conn.execute("SELECT MAX(version_number) AS n FROM skill_versions WHERE skill_id = ?",
                                          (skill_id,)).fetchone()["n"] or 0
                    stored = store_candidate(
                        conn, org_id=org_id, scene_id=scene["scene_id"], batch_id=skill["batch_id"],
                        task_key=skill["task_key"], candidate=candidate, report=report,
                        generation={**generation, "model_name": model_used, "prompt_version": version,
                                    "review_comment": comment, "rewrite_field_groups": field_groups or [],
                                    "repair_count": round_no, "removed_system_fields": removed},
                        created_by=user["id"], skill_id=skill_id, version_number=int(number) + 1,
                        version_kind="ai_regenerated" if current is not None else "ai_original",
                        based_on_version_id=current["id"] if current is not None else None,
                    )
                    result = {"ok": True, **stored, "version_number": int(number) + 1, "model_calls": calls,
                              "lost_atoms": lost, "repair_count": round_no, "prompt_version": version}
                    if on_stored is not None:
                        on_stored(conn, result)
                    return result
        messages = messages[:2] + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": build_repair_message(last_issues, allowed)},
        ]
    return {"ok": False, "reason": "validation_failed", "issues": last_issues, "model_calls": calls,
            "candidate": last_candidate, "prompt_version": version}


# ---------------------------------------------------------------------------
# 启动恢复
# ---------------------------------------------------------------------------

def recover_generation_tasks() -> List[str]:
    """服务重启：进行中批次的 queued/running 任务重新排队；已结束批次的残留任务取消；无任务的进行中批次收尾。"""
    resubmit: List[str] = []
    with _write_tx() as conn:
        rows = conn.execute(
            """SELECT t.id, t.batch_id, b.status AS batch_status FROM skill_generation_tasks t
               LEFT JOIN skill_generation_batches b ON b.id = t.batch_id
               WHERE t.status IN ('queued', 'running') ORDER BY t.created_at"""
        ).fetchall()
        now_iso = _now()
        for row in rows:
            if row["batch_status"] == "running":
                conn.execute(
                    "UPDATE skill_generation_tasks SET status = 'queued', started_at = NULL, updated_at = ? WHERE id = ?",
                    (now_iso, row["id"]),
                )
                resubmit.append(row["id"])
            else:
                conn.execute(
                    """UPDATE skill_generation_tasks SET status = 'cancelled', error_message = '恢复时批次已结束',
                           completed_at = ?, updated_at = ? WHERE id = ?""",
                    (now_iso, now_iso, row["id"]),
                )
        for batch in conn.execute("SELECT id FROM skill_generation_batches WHERE status = 'running'").fetchall():
            maybe_finish_batch(conn, batch["id"])
    _enqueue(resubmit)
    return resubmit


# ---------------------------------------------------------------------------
# 查询（批次详情、最小候选列表）
# ---------------------------------------------------------------------------

# 校验问题的大白话说明（界面展示用；完整问题保留在记录中）
_PLAIN_ISSUES = {
    "REF_ATOM_NOT_FOUND": "引用了不存在的知识",
    "REF_NOT_ELIGIBLE": "引用的知识已停用、失效或被修改",
    "REF_NOT_IN_POOL": "引用了本次范围以外的知识",
    "REF_ITEM_MISMATCH": "引用的知识编号前后不一致",
    "REF_NOT_DECLARED": "用到的知识没有列入引用清单",
    "USED_IN_STEPS_MISMATCH": "知识与步骤的对应关系不一致",
    "USED_IN_UNKNOWN_STEP": "知识标注了不存在的步骤",
    "USED_IN_STEPS_EMPTY": "知识没有标注用在哪个步骤",
    "R3_REFS_MISSING": "步骤标了有依据却没有写明依据",
    "R3_GENERIC_KIND_INVALID": "判断或计算步骤缺少依据",
    "R3_EXPERT_BASIS_NOT_ALLOWED": "生成内容自行标了专家补充",
    "R3_EXPERT_REASON_MISSING": "专家补充缺少理由",
    "MODEL_OUTPUT_NOT_JSON": "生成内容无法读取",
    "NUMBER_UNIT_MISSING": "数值字段缺少单位",
    "ENUM_ALLOWED_VALUES_MISSING": "选项字段缺少可选值",
    "APPLIES_TO_EMPTY": "没有写适用范围",
    "TBD_NOT_ALLOWED": "不该留空的内容填了待专家补充",
    "DUPLICATE_ID": "编号重复",
}


def plain_issue(issue: Dict[str, Any]) -> str:
    code = issue.get("code") or ""
    if code in _PLAIN_ISSUES:
        return _PLAIN_ISSUES[code]
    if code.startswith("SCHEMA_"):
        return "格式不完整或取值不符合要求"
    return "不符合要求"


_STAGES = (
    ("recall", "查找知识", ("recall_atoms",)),
    ("split", "拆分任务", ("split_tasks",)),
    ("generate", "生成候选", ("generate_skill",)),
    ("validate", "检查入库", ("validate_store",)),
)


def _stage_progress(batch: Dict[str, Any], tasks: List[Dict[str, Any]], results: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    stages = []
    to_generate = [r for r in results.values() if r.get("status") != "skipped"]
    for key, label, types in _STAGES:
        rows = [t for t in tasks if t["task_type"] in types]
        if any(t["status"] in ("queued", "running") for t in rows):
            status = "running"
        elif not rows:
            status = "skipped" if batch["status"] != "running" else "waiting"
        elif all(t["status"] in ("failed", "cancelled") for t in rows):
            status = "failed"
        else:
            status = "done"
        detail = None
        if key == "recall":
            pool = _loads(batch.get("atom_pool_json"), None)
            if pool:
                detail = f"找到 {len(pool.get('atoms') or [])} 条可用知识"
        elif key == "split":
            split = _loads(batch.get("task_split_json"), None)
            if split:
                detail = f"拆出 {len(split.get('tasks') or [])} 个任务，{split.get('generate_count', 0)} 个进入生成"
        elif key == "generate" and to_generate:
            finished = sum(1 for r in to_generate if r.get("status") not in ("generating",))
            detail = f"{finished} / {len(to_generate)} 个已生成"
        elif key == "validate" and to_generate:
            passed = sum(1 for r in to_generate if r.get("status") == "stored")
            failed = sum(1 for r in to_generate if r.get("status") == "validation_failed")
            detail = f"通过 {passed} 个，未通过 {failed} 个"
        stages.append({"key": key, "label": label, "status": status, "detail": detail})
    return stages


def _candidate_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    skill_json = _loads(row.get("skill_json"), None)
    error = _loads(row.get("generation_error_json"), None)
    source = skill_json or (error or {}).get("candidate") or {}
    task = (error or {}).get("task") or {}
    confidence = source.get("generation_confidence") if isinstance(source, dict) else None
    refs = source.get("knowledge_refs") if isinstance(source, dict) else None
    unsupported = (skill_json or {}).get("unsupported_items") if skill_json else None
    issues = (error or {}).get("issues") or []
    return {
        "skill_id": row["id"],
        "batch_id": row.get("batch_id"),
        "task_key": row.get("task_key"),
        "name": (source.get("name") if isinstance(source, dict) else None) or task.get("name") or "（未命名）",
        "status": row["status"],
        "status_label": SKILL_STATUS_LABELS.get(row["status"], row["status"]),
        "confidence_level": confidence.get("level") if isinstance(confidence, dict) else None,
        "unsupported_count": len(unsupported) if isinstance(unsupported, list) else None,
        "ref_count": len(refs) if isinstance(refs, list) else 0,
        "version_number": row.get("version_number"),
        "similar_skills": _loads(row.get("similar_skills_json"), []) or [],
        "error_summary": sorted({plain_issue(i) for i in issues}) if issues else [],
        "updated_at": row.get("updated_at"),
    }


def list_batch_candidates(conn, org_id: str, batch_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT s.id, s.batch_id, s.task_key, s.status, s.similar_skills_json, s.generation_error_json,
                  s.updated_at, sv.skill_json, sv.version_number
           FROM skills s LEFT JOIN skill_versions sv ON sv.id = s.current_version_id
           WHERE s.organization_id = ? AND s.batch_id = ?
           ORDER BY s.task_key, s.created_at""",
        (org_id, batch_id),
    ).fetchall()
    return [_candidate_summary(dict(r)) for r in rows]


def serialize_batch_summary(row: Dict[str, Any], scene_name: Optional[str] = None) -> Dict[str, Any]:
    results = _loads(row.get("task_results_json"), {}) or {}
    return {
        "batch_id": row["id"],
        "scene_id": row["scene_id"],
        "scene_name": scene_name,
        "status": row["status"],
        "status_label": BATCH_STATUS_LABELS.get(row["status"], row["status"]),
        "status_reason": row.get("status_reason"),
        "focus_note": row.get("focus_note"),
        "initiated_at": row["initiated_at"],
        "completed_at": row.get("completed_at"),
        "stored_count": sum(1 for r in results.values() if r.get("status") == "stored"),
        "task_count": sum(1 for r in results.values() if r.get("status") != "skipped"),
    }


def list_batches(conn, org_id: str, scene_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    params: List[Any] = [org_id]
    where = "b.organization_id = ?"
    if scene_id:
        where += " AND b.scene_id = ?"
        params.append(scene_id)
    rows = conn.execute(
        f"""SELECT b.*, s.name AS scene_name FROM skill_generation_batches b
            LEFT JOIN scenes s ON s.id = b.scene_id AND s.organization_id = b.organization_id
            WHERE {where} ORDER BY b.initiated_at DESC LIMIT ?""",
        [*params, limit],
    ).fetchall()
    return [serialize_batch_summary(dict(r), r["scene_name"]) for r in rows]


def running_batches_by_scene(conn, org_id: str) -> Dict[str, str]:
    rows = conn.execute(
        "SELECT id, scene_id FROM skill_generation_batches WHERE organization_id = ? AND status = 'running'",
        (org_id,),
    ).fetchall()
    return {r["scene_id"]: r["id"] for r in rows}


def get_batch_detail(conn, org_id: str, batch_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """SELECT b.*, s.name AS scene_name FROM skill_generation_batches b
           LEFT JOIN scenes s ON s.id = b.scene_id AND s.organization_id = b.organization_id
           WHERE b.id = ? AND b.organization_id = ?""",
        (batch_id, org_id),
    ).fetchone()
    if not row:
        return None
    batch = dict(row)
    tasks = [dict(t) for t in conn.execute(
        "SELECT id, task_type, task_key, status, attempt_count, error_message, created_at, completed_at "
        "FROM skill_generation_tasks WHERE batch_id = ? ORDER BY created_at",
        (batch_id,),
    ).fetchall()]
    results = _loads(batch.get("task_results_json"), {}) or {}
    pool = _loads(batch.get("atom_pool_json"), None)
    split = _loads(batch.get("task_split_json"), None)
    titles = {a["atom_version_id"]: a["title"] for a in (pool or {}).get("atoms") or []}
    if split:
        for task in split.get("tasks") or []:
            task["atoms"] = [{"atom_version_id": v, "title": titles.get(v)} for v in task.get("atom_version_ids") or []]
            task["result"] = results.get(task["task_key"])
    return {
        **serialize_batch_summary(batch, batch.get("scene_name")),
        "model_name": batch.get("model_name"),
        "prompt_versions": _loads(batch.get("prompt_version"), {}),
        "stages": _stage_progress(batch, tasks, results),
        "atom_pool": pool,
        "task_split": split,
        "task_results": list(results.values()),
        "tasks": [{**t, "task_type_label": SKILL_TASK_TYPE_LABELS.get(t["task_type"], t["task_type"])} for t in tasks],
        "candidates": list_batch_candidates(conn, org_id, batch_id),
    }


def get_skill_detail(conn, org_id: str, skill_id: str) -> Optional[Dict[str, Any]]:
    """候选的完整 JSON（只读）：有版本时为当前版本；校验未通过时为最后一次候选与问题清单。"""
    row = conn.execute(
        """SELECT s.*, sv.skill_json, sv.version_number, sv.version_kind, sv.validation_report_json
           FROM skills s LEFT JOIN skill_versions sv ON sv.id = s.current_version_id
           WHERE s.id = ? AND s.organization_id = ?""",
        (skill_id, org_id),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    error = _loads(data.get("generation_error_json"), None)
    report = _loads(data.get("validation_report_json"), None)
    issues = (report or {}).get("issues") if report else (error or {}).get("issues")
    return {
        **_candidate_summary(data),
        "scene_id": data["scene_id"],
        "visibility": data.get("visibility"),
        "version_kind": data.get("version_kind"),
        "skill_json": _loads(data.get("skill_json"), None) or (error or {}).get("candidate"),
        "issues": [{**i, "plain": plain_issue(i)} for i in (issues or [])],
        "repair_count": (error or {}).get("repair_count"),
    }
