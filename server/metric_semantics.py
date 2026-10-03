"""保守的指标定量/定性拆分：仅处理能完整解析的简单单项指标，绝不猜测关系或覆盖人工内容。"""
import re
from typing import Any, Dict, List, Optional

_NUMBER = r"(?P<value>\d+(?:\.\d+)?)"
_UNIT = r"(?P<unit>MPa|kPa|分钟|小时|秒|天|次|%|℃|毫米|厘米|米|元)"
_BEFORE = (
    "不得超过", "不可超过", "不超过", "不高于", "不大于", "至多", "最多", "小于等于",
    "不低于", "不少于", "不小于", "至少", "大于等于",
    "小于", "低于", "大于", "高于", "等于", "≤", "≦", "≥", "≧", "<=", ">=", "<", ">", "=",
)
_NOTE_PATTERN = re.compile(
    r"^\s*(?:必须|应当|应|须|需|严格)?\s*"
    r"(?P<relation>" + "|".join(map(re.escape, _BEFORE)) + r")?\s*"
    + _NUMBER + r"\s*" + _UNIT
    + r"\s*(?P<after>以内|以下|以上|之内)?\s*[。；！!]?\s*$",
    re.I,
)
_RELATION_KIND = {
    **{key: "≤" for key in ("不得超过", "不可超过", "不超过", "不高于", "不大于", "至多", "最多", "小于等于", "≤", "≦", "<=")},
    **{key: "≥" for key in ("不低于", "不少于", "不小于", "至少", "大于等于", "≥", "≧", ">=")},
    **{key: "<" for key in ("小于", "低于", "<")},
    **{key: ">" for key in ("大于", "高于", ">")},
    **{key: "=" for key in ("等于", "=")},
}
_AFTER_KIND = {"以内": "≤", "以下": "≤", "之内": "≤", "以上": "≥"}


def parse_quantitative_note(note: Any) -> Optional[Dict[str, str]]:
    """只解析整段恰好等于一个量化要求的 note；混合条件、枚举及区间不冒险拆分。"""
    match = _NOTE_PATTERN.fullmatch(str(note or ""))
    if not match:
        return None
    before = _RELATION_KIND.get(match.group("relation") or "")
    after = _AFTER_KIND.get(match.group("after") or "")
    if before and after and before != after:
        return None
    relation = before or after
    if not relation:
        return None
    return {"relation": relation, "value": match.group("value"), "unit": match.group("unit")}


def same_quantity(a: str, b: str) -> bool:
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


def quantity_matches_row(parsed: Dict[str, str], row: Dict[str, Any]) -> bool:
    value, unit, relation = (
        str(row.get(key) or "").strip() for key in ("value", "unit", "relation")
    )
    return (
        (not value or same_quantity(value, parsed["value"]))
        and (not unit or unit.lower() == parsed["unit"].lower())
        and (not relation or _RELATION_KIND.get(relation) == parsed["relation"])
    )


def normalize_generated_metric_rows(metric: Any, source: str) -> Any:
    """仅对新模型候选修复冗余 note；已保存的条目及人工草稿绝不在读取/校验时改写。"""
    if not isinstance(metric, dict) or not isinstance(metric.get("rows"), list):
        return metric
    from structured_quality import _relation_supported
    updated = dict(metric)
    updated_rows: List[Any] = []
    for raw in metric["rows"]:
        if not isinstance(raw, dict):
            updated_rows.append(raw)
            continue
        row = dict(raw)
        parsed = parse_quantitative_note(row.get("note"))
        if parsed and quantity_matches_row(parsed, row):
            probe = {"value": parsed["value"], "unit": parsed["unit"]}
            if re.search(re.escape(parsed["value"]) + r"\s*" + re.escape(parsed["unit"]), source, re.I) and (
                _relation_supported(parsed["relation"], probe, source)
            ):
                row["value"] = str(row.get("value") or parsed["value"])
                row["unit"] = str(row.get("unit") or parsed["unit"])
                row["relation"] = str(row.get("relation") or parsed["relation"])
                row["note"] = ""  # 原文依据及其他行字段均不变；无剩余定性描述时留空。
        updated_rows.append(row)
    updated["rows"] = updated_rows
    return updated
