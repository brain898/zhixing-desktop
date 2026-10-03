"""M03 v0.3 §§4–8. Controlled consultation over published, eligible Skill versions."""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from contextlib import contextmanager
from typing import Any

import config
from auth import require_admin
from consult_calculator import parse_date, recompute_calculations
from database import get_db
from deepseek_extractor import post_chat_completion
from eligibility import filter_eligible_version_ids
from fastapi import APIRouter, Depends, HTTPException
from hybrid_retrieval import hybrid_search, load_evidence
from m02_common import now_iso
from model_errors import classify_model_error
from scene_catalog import write_audit
from skill_constants import SKILL_MODEL_CALL_MAX_ATTEMPTS
from skill_generation import load_actor, load_prompt, parse_model_json
from skill_recheck import load_ref_snapshots

router = APIRouter(prefix="/api/consult", dependencies=[Depends(require_admin)])
DISCLAIMER = "本报告由 AI 按已审核 Skill 生成，结论需人工确认。"
PROMPTS = {"route": "consult_route_v1.md", "execute": "consult_execute_v1.md",
           "report": "consult_report_v1.md", "fallback": "consult_fallback_v1.md"}


def _safe(value):
    if isinstance(value, str):
        key = config.DEEPSEEK_API_KEY
        return value.replace(key, "[已隐藏]") if key else value
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items() if str(k).lower() not in ("api_key", "token", "authorization")}
    if isinstance(value, list):
        return [_safe(v) for v in value]
    return value


def dumps(value):
    return json.dumps(_safe(value), ensure_ascii=False, allow_nan=False)


def loads(value, default=None):
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


@contextmanager
def _tx():
    with get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def init_consult_tables(conn):
    """§7. Add only five new tables; leave M01/M02 tables unchanged."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS consult_skill_publications (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id),
        skill_id TEXT NOT NULL REFERENCES skills(id),
        skill_version_id TEXT NOT NULL REFERENCES skill_versions(id),
        status TEXT NOT NULL CHECK(status IN ('published','unpublished')),
        published_by TEXT NOT NULL, published_at TEXT NOT NULL,
        unpublished_by TEXT, unpublished_at TEXT, unpublish_reason TEXT,
        UNIQUE(organization_id, skill_id)
    );
    CREATE TABLE IF NOT EXISTS consult_sessions (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id),
        user_id TEXT NOT NULL REFERENCES users(id), question TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('routing','awaiting_input','running','completed','failed')),
        route_json TEXT, form_json TEXT, inputs_json TEXT, report_json TEXT, error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS consult_runs (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES consult_sessions(id),
        skill_id TEXT NOT NULL REFERENCES skills(id), skill_version_id TEXT NOT NULL REFERENCES skill_versions(id),
        order_index INTEGER NOT NULL, inputs_json TEXT, output_json TEXT,
        recompute_json TEXT, validation_json TEXT, evidence_json TEXT, model_calls_json TEXT,
        status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','skipped','uncomputable','needs_human')),
        started_at TEXT, completed_at TEXT, UNIQUE(session_id, order_index)
    );
    CREATE TABLE IF NOT EXISTS consult_tasks (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES consult_sessions(id),
        task_type TEXT NOT NULL CHECK(task_type IN ('route','execute','report','fallback')),
        status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')),
        attempt INTEGER NOT NULL DEFAULT 0, payload_json TEXT, model_calls_json TEXT,
        error TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS consult_handoffs (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES consult_sessions(id),
        source TEXT NOT NULL CHECK(source IN ('user','system')), reasons_json TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('open','resolved')),
        resolved_by TEXT, resolved_at TEXT, note TEXT, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_consult_sessions_org ON consult_sessions(organization_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_consult_tasks_status ON consult_tasks(status, created_at);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_consult_tasks_one_active ON consult_tasks(session_id)
        WHERE status IN ('queued','running');
    CREATE UNIQUE INDEX IF NOT EXISTS idx_consult_handoffs_one_open ON consult_handoffs(session_id) WHERE status='open';
    """)


def _skill(conn, user, skill_id):
    row = conn.execute("""SELECT s.*, sv.skill_json, sv.version_number FROM skills s
        LEFT JOIN skill_versions sv ON sv.id=s.current_version_id AND sv.organization_id=s.organization_id
        WHERE s.id=? AND s.organization_id=?""", (skill_id, user["organization_id"])).fetchone()
    if not row:
        raise HTTPException(404, "没有找到这个 Skill")
    data = dict(row)
    data["definition"] = loads(data.pop("skill_json"), {})
    return data


def consult_availability(conn, user, skill_id, *, expected_version_id=None):
    """Single gate used by listing, routing and immediately before every execution (AC03)."""
    skill = _skill(conn, user, skill_id)
    pub = conn.execute("SELECT * FROM consult_skill_publications WHERE organization_id=? AND skill_id=?",
                       (user["organization_id"], skill_id)).fetchone()
    label, reason = "咨询中", ""
    if not pub:
        label = "未上架"
    elif pub["status"] != "published":
        label = "已下架"
    elif skill["status"] == "needs_recheck":
        label, reason = "暂停：待复核", skill.get("stale_reason") or "Skill 正在等待复核"
    elif skill["current_version_id"] != pub["skill_version_id"] or (expected_version_id and expected_version_id != skill["current_version_id"]):
        label, reason = "暂停：版本已更新", "请重新上架确认新版本"
    elif skill["status"] != "approved":
        label, reason = "暂停：待复核", "Skill 尚未通过审核"
    else:
        ids = [r.get("atom_version_id") for r in skill["definition"].get("knowledge_refs", []) if isinstance(r, dict)]
        snapshots = load_ref_snapshots(conn, user["organization_id"], skill["current_version_id"])
        eligible = set(filter_eligible_version_ids(conn, ids, user))
        if not ids or any(vid not in eligible or vid not in snapshots for vid in ids):
            label, reason = "暂停：知识不可用", "引用知识已失效、未确认、来源变更或引用快照缺失"
    definition = skill["definition"]
    return {"id": skill_id, "skill_id": skill_id, "name": definition.get("name", "未命名 Skill"), "goal": definition.get("goal", ""),
            "trigger_description": definition.get("trigger_description", ""), "status": skill["status"],
            "consult_status": label, "pause_reason": reason, "available": label == "咨询中",
            "published": bool(pub and pub["status"] == "published"),
            "skill_version_id": skill["current_version_id"], "version_number": skill["version_number"]}


def list_consult_skills(conn, user):
    rows = conn.execute("""SELECT s.id FROM skills s LEFT JOIN consult_skill_publications p
        ON p.skill_id=s.id AND p.organization_id=s.organization_id
        WHERE s.organization_id=? AND (s.status='approved' OR p.id IS NOT NULL) ORDER BY s.created_at, s.id""",
                        (user["organization_id"],)).fetchall()
    return [consult_availability(conn, user, row["id"]) for row in rows]


def publish_skill(conn, user, skill_id):
    skill = _skill(conn, user, skill_id)
    if skill["status"] != "approved" or not skill["current_version_id"]:
        raise HTTPException(400, "只有已通过的 Skill 才能上架")
    name = skill["definition"].get("name", "").strip()
    for row in conn.execute("""SELECT p.skill_id, sv.skill_json FROM consult_skill_publications p
        JOIN skills s ON s.id=p.skill_id JOIN skill_versions sv ON sv.id=s.current_version_id
        WHERE p.organization_id=? AND p.status='published' AND p.skill_id<>?""", (user["organization_id"], skill_id)):
        if loads(row["skill_json"], {}).get("name", "").strip() == name:
            raise HTTPException(409, "已有同名 Skill 在架，请先下架另一个")
    stamp = now_iso()
    conn.execute("""INSERT INTO consult_skill_publications
        (id,organization_id,skill_id,skill_version_id,status,published_by,published_at)
        VALUES (?,?,?,?,'published',?,?) ON CONFLICT(organization_id,skill_id) DO UPDATE SET
        skill_version_id=excluded.skill_version_id,status='published',published_by=excluded.published_by,
        published_at=excluded.published_at,unpublished_by=NULL,unpublished_at=NULL,unpublish_reason=NULL""",
                 (uuid.uuid4().hex, user["organization_id"], skill_id, skill["current_version_id"], user["id"], stamp))
    write_audit(conn, user["organization_id"], user["id"], "consult_publish", "skill", skill_id,
                {"skill_version_id": skill["current_version_id"]}, stamp)
    return consult_availability(conn, user, skill_id)


def _session(conn, user, session_id):
    row = conn.execute("SELECT * FROM consult_sessions WHERE id=? AND organization_id=?",
                       (session_id, user["organization_id"])).fetchone()
    if not row:
        raise HTTPException(404, "没有找到这次咨询")
    return dict(row)


def _task(conn, session_id, task_type, created_by, payload=None):
    task_id, stamp = uuid.uuid4().hex, now_iso()
    conn.execute("""INSERT INTO consult_tasks
        (id,session_id,task_type,status,payload_json,created_by,created_at,updated_at)
        VALUES (?,?,?,'queued',?,?,?,?)""", (task_id, session_id, task_type, dumps(payload or {}), created_by, stamp, stamp))
    return task_id


def _enqueue(task_id):
    if task_id:
        from tasks import enqueue_consult_task
        enqueue_consult_task(task_id)


def _run_data(conn, row):
    data = dict(row)
    for key, default in (("inputs", {}), ("output", {}), ("recompute", []), ("validation", []), ("evidence", []), ("model_calls", [])):
        data[key] = loads(data.pop(key + "_json"), default)
    version = conn.execute("SELECT skill_json, version_number FROM skill_versions WHERE id=?", (data["skill_version_id"],)).fetchone()
    definition = loads(version["skill_json"], {}) if version else {}
    data.update(name=definition.get("name", ""), version_number=version["version_number"] if version else None,
                output_fields=definition.get("outputs", []), risk_boundary=definition.get("risk_boundary", []))
    return data


def _handoff_data(row):
    data = dict(row)
    data["reasons"] = loads(data.pop("reasons_json"), [])
    data["snapshot"] = loads(data.pop("snapshot_json"), {})
    return data


def session_detail(conn, user, session_id):
    data = _session(conn, user, session_id)
    for key, default in (("route", {}), ("form", {"fields": []}), ("inputs", {}), ("report", None)):
        data[key] = loads(data.pop(key + "_json"), default)
    data["runs"] = [_run_data(conn, r) for r in conn.execute("SELECT * FROM consult_runs WHERE session_id=? ORDER BY order_index", (session_id,))]
    data["tasks"] = []
    for row in conn.execute("SELECT * FROM consult_tasks WHERE session_id=? ORDER BY created_at", (session_id,)):
        task = dict(row)
        task["payload"] = loads(task.pop("payload_json"), {})
        task["model_calls"] = loads(task.pop("model_calls_json"), [])
        data["tasks"].append(task)
    data["handoffs"] = [_handoff_data(r) for r in conn.execute("SELECT * FROM consult_handoffs WHERE session_id=? ORDER BY created_at", (session_id,))]
    if data["status"] == "failed":
        failed = next((t for t in reversed(data["tasks"]) if t["status"] == "failed"), None)
        calls = failed["model_calls"] if failed else []
        last = calls[-1] if calls else {}
        code = last.get("error_code") or ("invalid_output" if last.get("validation_errors") else "legacy_unknown")
        unconfigured = data["error"] == "在线模型未配置，无法执行咨询"
        stages = {"route": "选择咨询内容", "execute": "执行咨询内容", "report": "整理咨询报告", "fallback": "整理知识依据"}
        data["failure"] = {"stage": stages.get(failed["task_type"], "处理咨询") if failed else "处理咨询",
                           "error_code": "configuration" if unconfigured else code,
                           "http_status": last.get("http_status"), "retryable": False if unconfigured else last.get("retryable", True)}
    current = next((r["order_index"] + 1 for r in data["runs"] if r["status"] == "running"), 0)
    total = len(data["runs"])
    messages = {"routing": "正在选择 Skill", "awaiting_input": "请补充必要信息", "running": f"正在执行第 {current or 1} 个，共 {total} 个", "completed": "咨询已完成", "failed": "咨询已停止，请查看失败原因"}
    if data["status"] == "running" and any(t["task_type"] == "report" and t["status"] in ("queued", "running") for t in data["tasks"]):
        messages["running"] = "正在整理报告"
    if any(t["task_type"] == "fallback" and t["status"] in ("queued", "running") for t in data["tasks"]):
        messages[data["status"]] = "正在查找知识依据"
    data["progress"] = {"message": messages[data["status"]], "current": current, "total": total}
    data["model_call_count"] = sum(len(t["model_calls"]) for t in data["tasks"])
    return _safe(data)


def _handoff(conn, user, session_id, source, reasons):
    existing = conn.execute("SELECT * FROM consult_handoffs WHERE session_id=? AND status='open'", (session_id,)).fetchone()
    detail = session_detail(conn, user, session_id)
    snapshot = {k: detail[k] for k in ("question", "route", "inputs", "runs", "report")}
    if existing:
        combined = list(dict.fromkeys(loads(existing["reasons_json"], []) + reasons))
        conn.execute("UPDATE consult_handoffs SET reasons_json=?,snapshot_json=? WHERE id=?", (dumps(combined), dumps(snapshot), existing["id"]))
        return existing["id"]
    identifier, stamp = uuid.uuid4().hex, now_iso()
    conn.execute("""INSERT INTO consult_handoffs (id,session_id,source,reasons_json,snapshot_json,status,created_at)
        VALUES (?,?,?,?,?,'open',?)""", (identifier, session_id, source, dumps(reasons), dumps(snapshot), stamp))
    write_audit(conn, user["organization_id"], user["id"], "consult_handoff", "consult_session", session_id,
                {"source": source, "reasons": _safe(reasons)}, stamp)
    return identifier


def validate_input(field, value):
    kind = field.get("type")
    if value is None or value == "":
        raise ValueError("不能为空")
    if kind == "number":
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("请输入有效数字")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError("请选择是或否")
    elif kind == "enum":
        if value not in (field.get("allowed_values") or []):
            raise ValueError("请选择提供的选项")
    elif kind == "date":
        parse_date(value)
    elif kind == "period":
        if isinstance(value, str):
            if not value.strip() or len(value) > 200:
                raise ValueError("请说明统计期间")
        elif isinstance(value, dict) and set(value) == {"start", "end"}:
            if parse_date(value["start"]) > parse_date(value["end"]):
                raise ValueError("结束日期不能早于开始日期")
        else:
            raise ValueError("请填写期间说明或开始、结束日期")
    elif kind in ("text", "file"):
        if not isinstance(value, str) or not value.strip() or len(value) > 10000:
            raise ValueError("请填写文字说明")
    else:
        raise ValueError("不支持的输入类型")
    return value


def normalize_route(conn, user, raw, available):
    choices = raw.get("selected_skills", raw.get("skills", []))
    if not isinstance(choices, list):
        raise ValueError("选中的 Skill 必须为列表")
    allowed = {s["id"]: s for s in available}
    chosen, discarded, seen = [], [], set()
    for value in choices:
        entry = {"skill_id": value} if isinstance(value, str) else value
        sid = entry.get("skill_id") if isinstance(entry, dict) else None
        if sid not in allowed or sid in seen or len(chosen) >= 4:
            discarded.append({"skill_id": sid, "reason": "超出本次可用范围、重复选择或超过最多 4 个"})
            continue
        availability = consult_availability(conn, user, sid)
        if not availability["available"]:
            discarded.append({"skill_id": sid, "reason": availability["consult_status"]})
            continue
        definition = _skill(conn, user, sid)["definition"]
        fields = {field["key"]: field for field in definition.get("inputs", [])}
        inputs = entry.get("inputs") if isinstance(entry.get("inputs"), dict) else {}
        extracted = {}
        for key, value in inputs.items():
            if key in fields and value is not None and value != "":
                try:
                    extracted[key] = validate_input(fields[key], value)
                except (ValueError, TypeError):
                    discarded.append({"skill_id": sid, "input_key": key, "reason": "抽取值不符合输入类型，改为补问"})
        mapping = entry.get("from_previous") if isinstance(entry.get("from_previous"), dict) else {}
        previous = {}
        previous_ids = {item["skill_id"] for item in chosen}
        for key, source in mapping.items():
            if key not in fields:
                continue
            if isinstance(source, str):
                source = {"skill_id": chosen[-1]["skill_id"] if chosen else None, "output_key": source}
            if isinstance(source, dict) and source.get("skill_id") in previous_ids and isinstance(source.get("output_key"), str):
                previous[key] = {"skill_id": source["skill_id"], "output_key": source["output_key"]}
            else:
                discarded.append({"skill_id": sid, "input_key": key, "reason": "前序来源无效，改为补问"})
        chosen.append({"skill_id": sid, "skill_version_id": availability["skill_version_id"], "name": availability["name"],
                       "reason": str(entry.get("reason") or ""), "inputs": extracted, "from_previous": previous})
        seen.add(sid)
    return {"selected_skills": chosen, "discarded": discarded}


def build_form(conn, user, route):
    fields = []
    for entry in route["selected_skills"]:
        definition = _skill(conn, user, entry["skill_id"])["definition"]
        for field in definition.get("inputs", []):
            if field.get("required") and field["key"] not in entry["inputs"] and field["key"] not in entry["from_previous"]:
                control = {**field, "skill_id": entry["skill_id"], "skill_name": entry["name"]}
                if control["type"] == "file":
                    control["type"], control["help"] = "text", "请用文字说明资料内容"
                fields.append(control)
    return {"fields": fields}


def execution_structure_errors(data, skill):
    errors = []
    for key, typ in (("outputs", dict), ("step_results", list), ("calculations", list), ("escalation", dict), ("missing_inputs", list), ("summary", str)):
        if not isinstance(data.get(key), typ):
            errors.append(f"{key} 结构不合格")
    if data.get("status") not in ("完成", "待补充", "不可计算", "需人工"):
        errors.append("status 取值不合格")
    if isinstance(data.get("outputs"), dict) and data.get("status") == "完成":
        for field in skill.get("outputs", []):
            if field.get("required") and data["outputs"].get(field["key"]) in (None, ""):
                errors.append(f"缺少必填输出 {field['key']}")
    if isinstance(data.get("step_results"), list):
        for step in data["step_results"]:
            if (not isinstance(step, dict) or not isinstance(step.get("step_id"), str)
                    or not isinstance(step.get("conclusion"), str) or not isinstance(step.get("refs"), list)
                    or not all(isinstance(v, str) for v in step.get("refs", []))
                    or step.get("state") not in ("完成", "跳过", "未通过")):
                errors.append("步骤结果结构不合格")
    if isinstance(data.get("calculations"), list):
        for calculation in data["calculations"]:
            if (not isinstance(calculation, dict) or not isinstance(calculation.get("step_id"), str)
                    or not isinstance(calculation.get("expression"), str) or not isinstance(calculation.get("label"), str)
                    or not isinstance(calculation.get("output_key"), str) or "model_result" not in calculation):
                errors.append("计算结果结构不合格")
    escalation = data.get("escalation")
    if isinstance(escalation, dict) and (type(escalation.get("triggered")) is not bool
            or not isinstance(escalation.get("matched_conditions"), list) or not isinstance(escalation.get("reason"), str)):
        errors.append("转人工结果结构不合格")
    return errors


def validate_execution(data, skill):
    validation, steps = [], {s["step_id"]: s for s in skill.get("steps", [])}
    allowed = {r["atom_version_id"] for r in skill.get("knowledge_refs", [])}
    result = json.loads(json.dumps(data))
    output_fields = {f["key"]: f for f in skill.get("outputs", [])}
    valid_outputs = {}
    for key, value in result["outputs"].items():
        field = output_fields.get(key)
        if field is None:
            validation.append(f"未声明的输出已剔除：{key}")
            continue
        try:
            valid_outputs[key] = validate_input(field, value)
        except (ValueError, TypeError):
            validation.append(f"取值不合规：{key}")
    result["invalid_outputs"] = {k: v for k, v in result["outputs"].items() if k not in valid_outputs}
    result["outputs"] = valid_outputs
    clean_steps = []
    for step in result["step_results"]:
        if step["step_id"] not in steps:
            validation.append(f"无效步骤已剔除：{step['step_id']}")
            continue
        refs = [ref for ref in step["refs"] if ref in allowed]
        if refs != step["refs"]:
            validation.append(f"越界引用已剔除：{step['step_id']}")
        clean_steps.append({**step, "refs": refs})
    result["step_results"] = clean_steps
    clean_calculations = []
    for calculation in result["calculations"]:
        if calculation["step_id"] not in steps or calculation["output_key"] not in output_fields:
            validation.append("无效计算步骤或输出已剔除")
        elif output_fields[calculation["output_key"]].get("type") != "number":
            validation.append("计算只能覆盖数字输出，该计算已剔除")
        else:
            clean_calculations.append(calculation)
    recompute, corrected = recompute_calculations(clean_calculations, valid_outputs)
    result["outputs"], result["calculations"] = corrected, clean_calculations
    result["unverified_outputs"] = [r["output_key"] for r in recompute if r["status"] == "未经复算"]
    reasons = []
    if result["escalation"]["triggered"] or result["status"] == "需人工":
        reasons = [str(v) for v in result["escalation"]["matched_conditions"]]
        reasons.append(result["escalation"].get("reason") or "Skill 提示需要人工确认")
    for step in clean_steps:
        if step["state"] == "未通过" and steps[step["step_id"]].get("on_fail") == "转人工":
            reasons.append(f"步骤 {step['step_id']} 未通过，按 Skill 要求转人工")
    if reasons:
        result["status"] = "需人工"
    # Summary/step prose is excluded from synthesis when it may contain invalid output values.
    if result["invalid_outputs"]:
        result["summary"] = "部分输出取值不合规，已剔除；请人工确认"
    return result, recompute, validation, list(dict.fromkeys(reasons))


def _evidence(conn, user, version_id, definition):
    snapshots = load_ref_snapshots(conn, user["organization_id"], version_id)
    ids = [r["atom_version_id"] for r in definition.get("knowledge_refs", [])]
    excerpts = load_evidence(conn, ids)
    evidence = []
    for vid in ids:
        snapshot = snapshots.get(vid)
        if not snapshot:
            continue
        source = conn.execute("""SELECT dv.file_name,dv.version_label,kv.source_document_version_id
            FROM knowledge_versions kv JOIN document_versions dv ON dv.id=kv.source_document_version_id
            WHERE kv.id=? AND kv.organization_id=? AND dv.organization_id=?""",
                              (vid, user["organization_id"], user["organization_id"])).fetchone()
        evidence.append({**snapshot, **(dict(source) if source else {"file_name": "来源已不可用", "version_label": ""}), "evidence": excerpts.get(vid, [])})
    return evidence


def _model(task_id, purpose, payload, validator=None):
    if not config.DEEPSEEK_API_KEY:
        raise ModelFailure("在线模型未配置，无法执行咨询", [])
    version, prompt = load_prompt(PROMPTS[purpose])
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": dumps(payload)}]
    calls, repair_count, transport_failures = [], 0, 0
    while True:
        started, stamp = time.monotonic(), now_iso()
        try:
            try:
                raw, model = post_chat_completion(messages, api_key=config.DEEPSEEK_API_KEY,
                    base_url=config.DEEPSEEK_BASE_URL, model_name=config.DEEPSEEK_MODEL,
                    timeout=config.SKILL_MODEL_TIMEOUT_SECONDS, max_tokens=config.SKILL_MODEL_MAX_OUTPUT_TOKENS, max_attempts=1)
            except Exception as exc:
                error = classify_model_error(exc)
                transport_failures += 1
                record = {"purpose": purpose, "prompt_version": version, "model": config.DEEPSEEK_MODEL,
                          "at": stamp, "elapsed_seconds": round(time.monotonic()-started, 3), "success": False,
                          "error_type": type(exc).__name__, **error.diagnostic()}
                calls.append(record)
                _persist_calls(task_id, record)
                if not error.retryable or transport_failures >= SKILL_MODEL_CALL_MAX_ATTEMPTS:
                    raise ModelFailure(str(error), calls) from None
                time.sleep(min(transport_failures, 2))
                continue
            data, parse_error = parse_model_json(raw)
            issues = [parse_error] if parse_error else (validator(data) if validator else [])
            calls.append({"purpose": purpose, "prompt_version": version, "model": model,
                          "at": stamp, "elapsed_seconds": round(time.monotonic()-started, 3),
                          "success": not issues, "validation_errors": issues})
            _persist_calls(task_id, calls[-1])
            if not issues:
                return _safe(data), calls
            if repair_count >= 1:
                raise ModelFailure("模型返回内容仍未通过结构检查", calls)
            repair_count += 1
            messages.append({"role": "assistant", "content": _safe(raw or "")})
            messages.append({"role": "user", "content": "请修正以下结构错误后重新输出完整 JSON：" + dumps(issues)})
        except ModelFailure:
            raise
        except Exception as exc:
            record = {"purpose": purpose, "prompt_version": version, "model": config.DEEPSEEK_MODEL,
                      "at": stamp, "elapsed_seconds": round(time.monotonic()-started, 3), "success": False,
                      "error_type": type(exc).__name__, "error_code": "processing_error",
                      "error_message": "模型回复处理出错，已停止本次任务", "retryable": False}
            calls.append(record)
            _persist_calls(task_id, record)
            raise ModelFailure(record["error_message"], calls) from None


class ModelFailure(RuntimeError):
    def __init__(self, message, calls):
        super().__init__(message)
        self.calls = calls


def _persist_calls(task_id, record):
    with _tx() as conn:
        task = conn.execute("SELECT model_calls_json FROM consult_tasks WHERE id=?", (task_id,)).fetchone()
        calls = loads(task["model_calls_json"], [])
        conn.execute("UPDATE consult_tasks SET model_calls_json=?,updated_at=? WHERE id=?", (dumps(calls+[record]), now_iso(), task_id))


def _finish(conn, task_id):
    stamp = now_iso()
    conn.execute("UPDATE consult_tasks SET status='completed',completed_at=?,updated_at=? WHERE id=?", (stamp, stamp, task_id))


def _route(task, session, user):
    with get_db() as conn:
        available = [s for s in list_consult_skills(conn, user) if s["available"]]
        if len(available) > 12:
            terms = set(re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]{2}", session["question"]))
            available.sort(key=lambda s: sum(term in (s["name"]+s["goal"]+s["trigger_description"]) for term in terms), reverse=True)
            available = available[:12]
        descriptions = [{**s, "inputs": _skill(conn, user, s["id"])["definition"].get("inputs", []),
                         "outputs": _skill(conn, user, s["id"])["definition"].get("outputs", [])} for s in available]
    raw = {"selected_skills": []}
    if available:
        raw, _ = _model(task["id"], "route", {"question": session["question"], "skills": descriptions},
                        lambda d: [] if isinstance(d.get("selected_skills", d.get("skills")), list) else ["缺少 selected_skills 列表"])
    with _tx() as conn:
        route = normalize_route(conn, user, raw, available)
        form = build_form(conn, user, route)
        inputs = {e["skill_id"]: e["inputs"] for e in route["selected_skills"]}
        conn.execute("UPDATE consult_sessions SET route_json=?,form_json=?,inputs_json=?,status=?,updated_at=? WHERE id=?",
                     (dumps(route), dumps(form), dumps(inputs), "awaiting_input" if form["fields"] else "running", now_iso(), session["id"]))
        for index, entry in enumerate(route["selected_skills"]):
            conn.execute("""INSERT OR IGNORE INTO consult_runs
                (id,session_id,skill_id,skill_version_id,order_index,status) VALUES (?,?,?,?,?,'queued')""",
                         (uuid.uuid4().hex, session["id"], entry["skill_id"], entry["skill_version_id"], index))
        _finish(conn, task["id"])
        if form["fields"]:
            return None
        return _task(conn, session["id"], "execute" if route["selected_skills"] else "fallback", user["id"])


def _execute(task, session, user):
    route = loads(session["route_json"], {})
    supplied = loads(session["inputs_json"], {})
    previous = {}
    for index, entry in enumerate(route.get("selected_skills", [])):
        with _tx() as conn:
            run = dict(conn.execute("SELECT * FROM consult_runs WHERE session_id=? AND order_index=?", (session["id"], index)).fetchone())
            if run["status"] in ("completed", "needs_human", "skipped", "uncomputable"):
                previous[entry["skill_id"]] = loads(run["output_json"], {}).get("outputs", {})
                continue
            version = conn.execute("SELECT skill_json FROM skill_versions WHERE id=? AND organization_id=?", (run["skill_version_id"], user["organization_id"])).fetchone()
            definition = loads(version["skill_json"], {}) if version else {}
            try:
                availability = consult_availability(conn, user, entry["skill_id"], expected_version_id=run["skill_version_id"])
            except HTTPException:
                availability = {"available": False, "consult_status": "Skill 已不可用", "pause_reason": "Skill 不存在"}
            values = dict(supplied.get(entry["skill_id"], {}))
            missing = []
            fields = {f["key"]: f for f in definition.get("inputs", [])}
            for key, source in entry.get("from_previous", {}).items():
                value = previous.get(source["skill_id"], {}).get(source["output_key"])
                try:
                    values[key] = validate_input(fields[key], value)
                except (ValueError, TypeError, KeyError):
                    missing.append(key)
            missing.extend(f["key"] for f in fields.values() if f.get("required") and values.get(f["key"]) in (None, "") and f["key"] not in missing)
            unavailable = not availability["available"]
            if unavailable or missing:
                reason = (availability["consult_status"] + "：" + availability["pause_reason"]) if unavailable else "前序没有产出必需值或输入缺失：" + "、".join(missing)
                output = {"status": "不可计算", "outputs": {}, "step_results": [], "calculations": [],
                          "escalation": {"triggered": False, "matched_conditions": [], "reason": ""},
                          "missing_inputs": missing, "summary": reason}
                conn.execute("""UPDATE consult_runs SET status=?,inputs_json=?,output_json=?,validation_json=?,completed_at=? WHERE id=?""",
                             ("skipped" if unavailable else "uncomputable", dumps(values), dumps(output), dumps([reason]), now_iso(), run["id"]))
                previous[entry["skill_id"]] = {}
                continue
            snapshots = load_ref_snapshots(conn, user["organization_id"], run["skill_version_id"])
            evidence = _evidence(conn, user, run["skill_version_id"], definition)
            conn.execute("UPDATE consult_runs SET status='running',inputs_json=?,evidence_json=?,started_at=? WHERE id=?", (dumps(values), dumps(evidence), now_iso(), run["id"]))
        try:
            data, calls = _model(task["id"], "execute", {"skill": definition, "atoms": list(snapshots.values()),
                "inputs": values, "previous_outputs": previous}, lambda d: execution_structure_errors(d, definition))
            output, recompute, validation, reasons = validate_execution(data, definition)
        except ModelFailure as exc:
            with _tx() as conn:
                conn.execute("UPDATE consult_runs SET status='failed',validation_json=?,model_calls_json=?,completed_at=? WHERE id=?", (dumps([str(exc)]), dumps(exc.calls), now_iso(), run["id"]))
            raise
        status = "needs_human" if reasons else ("uncomputable" if output["status"] in ("待补充", "不可计算") else "completed")
        with _tx() as conn:
            conn.execute("""UPDATE consult_runs SET status=?,output_json=?,recompute_json=?,validation_json=?,model_calls_json=?,completed_at=? WHERE id=?""",
                         (status, dumps(output), dumps(recompute), dumps(validation), dumps(calls), now_iso(), run["id"]))
            if reasons:
                _handoff(conn, user, session["id"], "system", reasons)
        previous[entry["skill_id"]] = output["outputs"]
    with _tx() as conn:
        _finish(conn, task["id"])
        return _task(conn, session["id"], "report", user["id"])


def _program_report(detail):
    results, recompute, boundaries, manual, evidence = [], [], [], [], []
    seen = set()
    for run in detail["runs"]:
        output = run["output"]
        results.append({"skill_id": run["skill_id"], "skill_version_id": run["skill_version_id"],
                        "name": run["name"], "version_number": run["version_number"], "status": output.get("status", run["status"]),
                        "outputs": output.get("outputs", {}), "output_fields": run["output_fields"],
                        "step_results": output.get("step_results", []), "summary": output.get("summary", ""),
                        "unverified_outputs": output.get("unverified_outputs", []), "validation": run["validation"]})
        recompute.extend({**row, "skill_id": run["skill_id"], "name": run["name"]} for row in run["recompute"])
        boundaries.append({"skill_id": run["skill_id"], "name": run["name"], "items": run["risk_boundary"]})
        manual.extend(run["validation"])
        manual.extend("未经复算：" + str(row.get("label") or row.get("output_key")) for row in run["recompute"] if row["status"] == "未经复算")
        manual.extend(str(v) for v in output.get("escalation", {}).get("matched_conditions", []))
        if output.get("escalation", {}).get("triggered"):
            manual.append(output["escalation"].get("reason") or "需人工确认")
        for item in run["evidence"]:
            if item["atom_version_id"] not in seen:
                evidence.append(item)
                seen.add(item["atom_version_id"])
    for handoff in detail["handoffs"]:
        manual.extend(handoff["reasons"])
    return {"summary": "", "skill_results": results, "recompute": recompute, "actions": [],
            "risk_boundaries": boundaries, "manual_items": list(dict.fromkeys(manual)), "evidence": evidence,
            "disclaimer": DISCLAIMER, "arithmetic_note": "复算只核对算术，不验证代入数值的口径。", "mode": "skills"}


def _complete_report(conn, task, user, report):
    stamp = now_iso()
    conn.execute("UPDATE consult_sessions SET report_json=?,status='completed',error=NULL,completed_at=?,updated_at=? WHERE id=?", (dumps(report), stamp, stamp, task["session_id"]))
    _finish(conn, task["id"])
    open_handoff = conn.execute("SELECT id FROM consult_handoffs WHERE session_id=? AND status='open'", (task["session_id"],)).fetchone()
    if open_handoff:
        _handoff(conn, user, task["session_id"], "system", [])


def _report(task, session, user):
    with get_db() as conn:
        detail = session_detail(conn, user, session["id"])
    report = _program_report(detail)
    # Only validated outputs and program warnings feed synthesis. No raw model prose or invalid values.
    permitted = [{"name": r["name"], "status": r["status"], "outputs": r["outputs"],
                  "unverified_outputs": r["unverified_outputs"], "validation": r["validation"]} for r in report["skill_results"]]
    data, _ = _model(task["id"], "report", {"skill_results": permitted},
                    lambda d: [] if isinstance(d.get("summary"), str) and isinstance(d.get("actions"), (str, list)) else ["需要 summary 文字与 actions 行动建议"])
    report["summary"], report["actions"] = data["summary"], data["actions"]
    with _tx() as conn:
        _complete_report(conn, task, user, report)


def _fallback(task, session, user):
    with get_db() as conn:
        hits = hybrid_search(conn, user, session["question"], now_iso())
    prefix = "未匹配到可用 Skill，以下为知识条目整理"
    report = {"summary": "没有找到可用知识依据，请补充资料或转人工确认。", "skill_results": [], "recompute": [], "actions": ["可转人工确认"],
              "risk_boundaries": [], "manual_items": [], "evidence": [], "disclaimer": DISCLAIMER,
              "arithmetic_note": "复算只核对算术，不验证代入数值的口径。", "mode": "fallback", "notice": prefix}
    if hits:
        hit_ids = {str(h.get("version_id") or h.get("id") or h.get("knowledge_version_id")) for h in hits}
        data, _ = _model(task["id"], "fallback", {"question": session["question"], "knowledge": hits},
                        lambda d: [] if isinstance(d.get("answer"), str) and isinstance(d.get("refs"), list) else ["需要 answer 文字和 refs 引用列表"])
        refs = [ref for ref in data["refs"] if isinstance(ref, str) and ref in hit_ids]
        if len(refs) != len(data["refs"]):
            report["manual_items"].append("越界引用已剔除")
        if refs:
            report["summary"] = re.sub(r"\[([^\[\]]+)\]", lambda m: m.group(0) if m.group(1) in hit_ids else "", data["answer"])
            with get_db() as conn:
                excerpts = load_evidence(conn, refs)
                for hit in hits:
                    vid = str(hit.get("version_id") or hit.get("id") or hit.get("knowledge_version_id"))
                    if vid in refs:
                        report["evidence"].append({"atom_version_id": vid, "title": hit.get("title", ""),
                            "statement": hit.get("statement") or hit.get("content", ""), "file_name": hit.get("file_name", ""),
                            "version_label": hit.get("document_version_label", ""), "evidence": excerpts.get(vid, hit.get("evidence", []))})
        else:
            report["summary"] = "没有得到可追溯引用的回答，请转人工确认。"
    with _tx() as conn:
        _complete_report(conn, task, user, report)


def run_consult_task(task_id):
    """Claim once, persist every stage, and settle all failures instead of leaving running state."""
    task = None
    next_task = None
    try:
        with _tx() as conn:
            row = conn.execute("SELECT * FROM consult_tasks WHERE id=?", (task_id,)).fetchone()
            if not row or row["status"] != "queued":
                return
            task = dict(row)
            stamp = now_iso()
            conn.execute("UPDATE consult_tasks SET status='running',attempt=attempt+1,started_at=?,updated_at=? WHERE id=?", (stamp, stamp, task_id))
            session = dict(conn.execute("SELECT * FROM consult_sessions WHERE id=?", (task["session_id"],)).fetchone())
            user = load_actor(conn, session["user_id"], session["organization_id"])
            if not user or user["role"] != "admin" or user["account_status"] != "active":
                raise RuntimeError("发起管理员已不可用")
            conn.execute("UPDATE consult_sessions SET started_at=COALESCE(started_at,?),updated_at=? WHERE id=?", (stamp, stamp, session["id"]))
        handlers = {"route": _route, "execute": _execute, "report": _report, "fallback": _fallback}
        next_task = handlers[task["task_type"]](task, session, user)
    except Exception as exc:
        if task:
            message = str(exc) if isinstance(exc, ModelFailure) else "咨询处理出错，可以重试；已停止本次任务"
            with _tx() as conn:
                stamp = now_iso()
                conn.execute("UPDATE consult_tasks SET status='failed',error=?,completed_at=?,updated_at=? WHERE id=?", (_safe(message), stamp, stamp, task_id))
                conn.execute("UPDATE consult_sessions SET status='failed',error=?,completed_at=?,updated_at=? WHERE id=?", (_safe(message), stamp, stamp, task["session_id"]))
                conn.execute("UPDATE consult_runs SET status='failed',completed_at=? WHERE session_id=? AND status='running'", (stamp, task["session_id"]))
    _enqueue(next_task)


def recover_consult_tasks():
    """Resume persisted stages, preserving finished Skill runs and awaiting-input forms."""
    resubmit = []
    with _tx() as conn:
        for row in conn.execute("SELECT * FROM consult_tasks WHERE status IN ('queued','running') ORDER BY created_at").fetchall():
            session = conn.execute("SELECT status FROM consult_sessions WHERE id=?", (row["session_id"],)).fetchone()
            if not session or session["status"] in ("awaiting_input", "completed", "failed"):
                conn.execute("UPDATE consult_tasks SET status='cancelled',completed_at=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), row["id"]))
                continue
            conn.execute("UPDATE consult_tasks SET status='queued',started_at=NULL,updated_at=? WHERE id=?", (now_iso(), row["id"]))
            conn.execute("UPDATE consult_runs SET status='queued' WHERE session_id=? AND status='running'", (row["session_id"],))
            resubmit.append(row["id"])
        orphaned = conn.execute("""SELECT * FROM consult_sessions s WHERE s.status IN ('routing','running')
            AND NOT EXISTS (SELECT 1 FROM consult_tasks t WHERE t.session_id=s.id AND t.status IN ('queued','running'))""").fetchall()
        for session in orphaned:
            route = loads(session["route_json"], {})
            kind = "route" if session["status"] == "routing" else ("execute" if route.get("selected_skills") else "fallback")
            resubmit.append(_task(conn, session["id"], kind, session["user_id"]))
    for task_id in resubmit:
        _enqueue(task_id)
    return resubmit


@router.get("/skills")
def api_skills(user: dict = Depends(require_admin)):
    with get_db() as conn:
        items = list_consult_skills(conn, user)
    return {"items": items, "total": len(items)}


@router.post("/skills/{skill_id}/publish")
def api_publish(skill_id: str, user: dict = Depends(require_admin)):
    with _tx() as conn:
        return publish_skill(conn, user, skill_id)


@router.post("/skills/{skill_id}/unpublish")
def api_unpublish(skill_id: str, payload: dict | None = None, user: dict = Depends(require_admin)):
    with _tx() as conn:
        _skill(conn, user, skill_id)
        reason = str((payload or {}).get("reason") or "")[:1000]
        stamp = now_iso()
        conn.execute("""UPDATE consult_skill_publications SET status='unpublished',unpublished_by=?,unpublished_at=?,unpublish_reason=?
            WHERE organization_id=? AND skill_id=?""", (user["id"], stamp, reason, user["organization_id"], skill_id))
        write_audit(conn, user["organization_id"], user["id"], "consult_unpublish", "skill", skill_id, {"reason": _safe(reason)}, stamp)
        return consult_availability(conn, user, skill_id)


@router.post("/sessions")
def api_create_session(payload: dict, user: dict = Depends(require_admin)):
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip() or len(question) > 10000:
        raise HTTPException(400, "请填写问题，最多 10000 字")
    identifier, stamp = uuid.uuid4().hex, now_iso()
    with _tx() as conn:
        conn.execute("""INSERT INTO consult_sessions (id,organization_id,user_id,question,status,created_at,updated_at)
            VALUES (?,?,?,?,'routing',?,?)""", (identifier, user["organization_id"], user["id"], _safe(question.strip()), stamp, stamp))
        task_id = _task(conn, identifier, "route", user["id"])
    _enqueue(task_id)
    with get_db() as conn:
        return session_detail(conn, user, identifier)


@router.get("/sessions")
def api_sessions(user: dict = Depends(require_admin)):
    with get_db() as conn:
        items = [dict(row) for row in conn.execute("""SELECT id,question,status,error,created_at,updated_at,completed_at
            FROM consult_sessions WHERE organization_id=? ORDER BY created_at DESC""", (user["organization_id"],))]
    return {"items": _safe(items), "total": len(items)}


@router.get("/sessions/{session_id}")
def api_session(session_id: str, user: dict = Depends(require_admin)):
    with get_db() as conn:
        return session_detail(conn, user, session_id)


@router.post("/sessions/{session_id}/inputs")
def api_inputs(session_id: str, payload: dict, user: dict = Depends(require_admin)):
    with _tx() as conn:
        session = _session(conn, user, session_id)
        if session["status"] != "awaiting_input":
            raise HTTPException(409, "这次咨询现在不需要补充信息")
        supplied = payload.get("inputs")
        if not isinstance(supplied, dict):
            raise HTTPException(400, "输入值格式不正确")
        form = loads(session["form_json"], {"fields": []})
        merged = loads(session["inputs_json"], {})
        errors = []
        for field in form["fields"]:
            values = supplied.get(field["skill_id"], {})
            value = values.get(field["key"]) if isinstance(values, dict) else None
            try:
                merged.setdefault(field["skill_id"], {})[field["key"]] = validate_input(field, value)
            except (ValueError, TypeError) as exc:
                errors.append(f"{field['label']}：{exc}")
        if errors:
            raise HTTPException(422, {"message": "请检查补充信息：" + "；".join(errors), "errors": errors})
        conn.execute("UPDATE consult_sessions SET inputs_json=?,status='running',updated_at=? WHERE id=?", (dumps(merged), now_iso(), session_id))
        task_id = _task(conn, session_id, "execute", user["id"])
    _enqueue(task_id)
    with get_db() as conn:
        return session_detail(conn, user, session_id)


@router.post("/sessions/{session_id}/retry")
def api_retry(session_id: str, user: dict = Depends(require_admin)):
    with _tx() as conn:
        session = _session(conn, user, session_id)
        if session["status"] != "failed":
            raise HTTPException(409, "只有未完成的咨询可以重试")
        failed = conn.execute("SELECT task_type FROM consult_tasks WHERE session_id=? AND status='failed' ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()
        kind = failed["task_type"] if failed else "route"
        conn.execute("UPDATE consult_sessions SET status=?,error=NULL,completed_at=NULL,updated_at=? WHERE id=?", ("routing" if kind == "route" else "running", now_iso(), session_id))
        conn.execute("UPDATE consult_runs SET status='queued' WHERE session_id=? AND status='failed'", (session_id,))
        task_id = _task(conn, session_id, kind, user["id"])
    _enqueue(task_id)
    with get_db() as conn:
        return session_detail(conn, user, session_id)


@router.post("/sessions/{session_id}/handoff")
def api_handoff(session_id: str, payload: dict | None = None, user: dict = Depends(require_admin)):
    with _tx() as conn:
        _session(conn, user, session_id)
        identifier = _handoff(conn, user, session_id, "user", [str((payload or {}).get("reason") or "管理员主动转人工")[:2000]])
        return _handoff_data(conn.execute("SELECT * FROM consult_handoffs WHERE id=?", (identifier,)).fetchone())


@router.get("/handoffs")
def api_handoffs(user: dict = Depends(require_admin)):
    with get_db() as conn:
        items = [_handoff_data(row) for row in conn.execute("""SELECT h.*,s.question FROM consult_handoffs h
            JOIN consult_sessions s ON s.id=h.session_id WHERE s.organization_id=? ORDER BY h.created_at DESC""", (user["organization_id"],))]
    return {"items": _safe(items), "total": len(items)}


@router.post("/handoffs/{handoff_id}/resolve")
def api_resolve(handoff_id: str, payload: dict | None = None, user: dict = Depends(require_admin)):
    with _tx() as conn:
        row = conn.execute("""SELECT h.* FROM consult_handoffs h JOIN consult_sessions s ON s.id=h.session_id
            WHERE h.id=? AND s.organization_id=?""", (handoff_id, user["organization_id"])).fetchone()
        if not row:
            raise HTTPException(404, "没有找到这条转人工记录")
        note = str((payload or {}).get("note") or "")[:5000]
        stamp = now_iso()
        conn.execute("UPDATE consult_handoffs SET status='resolved',resolved_by=?,resolved_at=?,note=? WHERE id=?", (user["id"], stamp, _safe(note), handoff_id))
        write_audit(conn, user["organization_id"], user["id"], "consult_handoff_resolve", "consult_handoff", handoff_id, {"note": _safe(note)}, stamp)
        return _handoff_data(conn.execute("SELECT * FROM consult_handoffs WHERE id=?", (handoff_id,)).fetchone())
