"""M02-F / PRD FR13：只读统计，首次生成结果与人工审核记录分开计数。"""
from collections import Counter, defaultdict
import json

from skill_constants import SKILL_STATUS_LABELS, UNSUPPORTED_RESOLUTION_LABELS

MANUAL_ACTIONS = {"approve", "approve_with_changes", "recheck"}


def _json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (ValueError, TypeError):
        return default


def _rate(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def _validation_outcomes(batches):
    # 一个生成任务只取落盘后的最终校验结果；调用失败、跳过与运行中不算校验样本。
    outcomes = {}
    for b in batches:
        results = _json(b["task_results_json"], {})
        for r in (results.values() if isinstance(results, dict) else results):
            if isinstance(r, dict) and r.get("skill_id") and r.get("status") in {"stored", "validation_failed"}:
                outcomes[r["skill_id"]] = r["status"]
    return outcomes


def _review_totals(records):
    """按 Skill 汇总有序审核事件，JSON 只解析一次，供所有统计分组复用。"""
    totals = defaultdict(lambda: {
        "latest": None, "field_counts": [], "rejection_reasons": Counter(), "unsupported_resolutions": Counter(),
    })
    for r in records:
        total = totals[r["skill_id"]]
        if r["action"] in MANUAL_ACTIONS | {"reject", "restore", "regenerate"}:
            total["latest"] = r["action"]
        if r["action"] == "reject":
            total["rejection_reasons"][r["reject_reason"] or "未记录原因"] += 1
        if r["action"] not in MANUAL_ACTIONS:
            continue
        # 统计人工提交记录中的唯一字段路径；不把模型重生成的差异算作专家修改。
        if r["field_diffs_json"] is not None:
            total["field_counts"].append(len({d["path"] for d in _json(r["field_diffs_json"], [])
                                            if isinstance(d, dict) and d.get("path")}))
        seen = set()
        for item in _json(r["detail_json"], {}).get("unsupported_items", []):
            key = item.get("item_key")
            action = item.get("resolution")
            if key and key not in seen and action in UNSUPPORTED_RESOLUTION_LABELS:
                total["unsupported_resolutions"][action] += 1
                seen.add(key)
    return totals


def _summarize(skills, outcomes, reviews):
    outcomes = {s["id"]: outcomes[s["id"]] for s in skills if s["id"] in outcomes}
    totals = [reviews[s["id"]] for s in skills if s["id"] in reviews]
    decisions = [t["latest"] for t in totals if t["latest"] in MANUAL_ACTIONS | {"reject"}]
    field_counts = [n for t in totals for n in t["field_counts"]]
    reasons, resolutions = Counter(), Counter()
    for total in totals:
        reasons.update(total["rejection_reasons"])
        resolutions.update(total["unsupported_resolutions"])
    return {
        "candidate_count": len(skills),
        "status_counts": dict(Counter(s["status"] for s in skills)),
        "validation_pass_rate": _rate(sum(v == "stored" for v in outcomes.values()), len(outcomes)),
        "validation_unrecorded_count": len(skills) - len(outcomes),
        "review_pass_rate": _rate(sum(a in MANUAL_ACTIONS for a in decisions), len(decisions)),
        "review_pending_count": len(skills) - len(decisions),
        "average_modified_fields": sum(field_counts) / len(field_counts) if field_counts else None,
        "manual_review_record_count": len(field_counts),
        "modified_field_total": sum(field_counts),
        "rejection_reasons": dict(reasons),
        "unsupported_resolutions": dict(resolutions),
    }


def _aggregate(skills, batches, records):
    ids = {s["id"] for s in skills}
    return _summarize(skills, _validation_outcomes(batches), _review_totals(r for r in records if r["skill_id"] in ids))


def get_statistics(conn, user, scene_id=None, batch_id=None):
    org = user["organization_id"]
    # 生成/审核在后台继续写入时，各分组使用同一读取快照。
    if not conn.in_transaction:
        conn.execute("BEGIN")
    # 所有表分别限定企业；历史版本、引用、草稿的行数均不充当候选数。
    skills = [dict(r) for r in conn.execute("SELECT id, status, scene_id, batch_id FROM skills WHERE organization_id = ?", (org,))]
    batches = [dict(r) for r in conn.execute(
        "SELECT id, scene_id, initiated_at, status, task_results_json FROM skill_generation_batches "
        "WHERE organization_id = ? ORDER BY initiated_at, id", (org,))]
    scenes = [dict(r) for r in conn.execute("SELECT id, name, status FROM scenes WHERE organization_id = ? ORDER BY name, id", (org,))]
    records = [dict(r) for r in conn.execute(
        "SELECT skill_id, action, field_diffs_json, detail_json, reject_reason FROM skill_review_records "
        "WHERE organization_id = ? ORDER BY created_at, rowid", (org,))]
    if scene_id is not None:
        skills = [s for s in skills if s["scene_id"] == scene_id]
        batches = [b for b in batches if b["scene_id"] == scene_id]
        scenes = [s for s in scenes if s["id"] == scene_id]
    if batch_id is not None:
        skills = [s for s in skills if s["batch_id"] == batch_id]
        batches = [b for b in batches if b["id"] == batch_id]
        scene_ids = {b["scene_id"] for b in batches} | {s["scene_id"] for s in skills}
        scenes = [s for s in scenes if s["id"] in scene_ids]
    names = {s["id"]: s["name"] for s in scenes}
    outcomes, scene_outcomes, batch_outcomes = {}, defaultdict(dict), {}
    for batch in batches:
        result = _validation_outcomes([batch])
        outcomes.update(result)
        scene_outcomes[batch["scene_id"]].update(result)
        batch_outcomes[batch["id"]] = result
    ids = {s["id"] for s in skills}
    reviews = _review_totals(r for r in records if r["skill_id"] in ids)
    by_scene, by_batch = defaultdict(list), defaultdict(list)
    for skill in skills:
        by_scene[skill["scene_id"]].append(skill)
        by_batch[skill["batch_id"]].append(skill)
    return {
        "summary": _summarize(skills, outcomes, reviews),
        "by_scene": [{"scene_id": s["id"], "name": s["name"], "status": s["status"],
                      **_summarize(by_scene[s["id"]], scene_outcomes[s["id"]], reviews)} for s in scenes],
        "by_batch": [{"batch_id": b["id"], "scene_id": b["scene_id"], "scene_name": names.get(b["scene_id"], "场景已删除"),
                      "initiated_at": b["initiated_at"], "status": b["status"],
                      **_summarize(by_batch[b["id"]], batch_outcomes[b["id"]], reviews)} for b in batches],
        "status_labels": SKILL_STATUS_LABELS,
        "resolution_labels": UNSUPPORTED_RESOLUTION_LABELS,
        "definitions": {
            "candidate_count": "按候选 ID 去重，包括校验未通过；历史版本和重生成不增加候选数。",
            "validation_pass_rate": "首次生成最终校验通过数 / 有最终校验记录的候选数（含自动修复）；调用失败、跳过和运行中不计。审核与重生成不改此数。",
            "review_pass_rate": "最新有效审核决定为通过、修改后通过或复核处理的候选数 / 最新决定为通过或驳回的候选数。恢复、退回重生成后尚未再决定的候选不计；待复核保留此前决定。",
            "average_modified_fields": "人工通过、修改后通过、复核处理记录的唯一修改字段路径总数 / 有差异记录的人工提交次数，含无修改通过的 0；排除模型重写与未提交草稿。",
            "rejection_reasons": "历次驳回事件数，恢复后仍保留历史；放弃校验失败候选没有原因时列为未记录原因。",
            "unsupported_resolutions": "已提交人工通过或复核记录中，每条无依据项的实际处理结果；同一记录内按问题 ID 去重，跨记录保留处理事件。",
        },
    }
