"""新生成候选的保守结构化质检；只标记疑点，不据此擅自改写来源。"""
import re
from typing import Any, Dict, List

def _norm(value: Any) -> str:
    return re.sub(r"[\s，。；：:、,.!?！？（）()【】\[\]“”\"']", "", str(value or ""))

def _evidence_text(item: Dict[str, Any]) -> str:
    return "；".join(e.get("excerpt") or "" for e in item.get("source_evidence") or []
                    if e.get("accuracy_level", "exact") in ("exact", "referenced"))

def _relation_supported(relation: str, row: Dict[str, Any], source: str) -> bool:
    """仅在同一数值/单位附近识别比较关系的常见等价表述；不靠全段关键词拼接推断。"""
    equivalent = {
        "不超过": ("不得超过", "不可超过", "不超过", "不高于", "不大于", "至多", "最多", "以内", "之内", "以下", "小于等于", "≤", "≦", "<="),
        "≤": ("不得超过", "不可超过", "不超过", "不高于", "不大于", "至多", "最多", "以内", "之内", "以下", "小于等于", "≤", "≦", "<="),
        "不低于": ("不低于", "不少于", "至少", "以上", "不小于", "大于等于", "≥", "≧"),
        "≥": ("不低于", "不少于", "至少", "以上", "不小于", "大于等于", "≥", "≧", ">="),
    }
    tokens = equivalent.get(relation)
    if not tokens:
        return _norm(relation) in _norm(source)
    value = str(row.get("value") or "").strip()
    unit = str(row.get("unit") or "").strip()
    if not value:
        return _norm(relation) in _norm(source)
    # 只接受本行数值附近的文字，避免另一指标行中的“不超过”被错误借用。
    measurement = re.escape(value) + r"\s*" + (re.escape(unit) if unit else "")
    for clause in re.split(r"[，。；;,]|但是|但|而|另外", source):
        for match in re.finditer(measurement, clause, re.I):
            nearby = clause[max(0, match.start() - 14): min(len(clause), match.end() + 8)]
            if any(token in nearby for token in tokens):
                return True
    return False


def filter_resolved_relation_flags(
    flags: List[str], metric_definition: Any, source_evidence: List[Dict[str, Any]]
) -> List[str]:
    """读取旧候选时消除已证实为字面误报的比较关系提示，不修改存量版本。"""
    rows = (metric_definition or {}).get("rows") or [] if isinstance(metric_definition, dict) else []
    source = _evidence_text({"source_evidence": source_evidence})
    if not rows or not source:
        return flags
    result = []
    for flag in flags:
        match = re.fullmatch(r"指标关联需核对：第(\d+)行的比较关系「([^」]+)」未在证据摘录中直接对应", flag)
        index = int(match.group(1)) - 1 if match else -1
        if 0 <= index < len(rows) and isinstance(rows[index], dict) and (
            str(rows[index].get("relation") or "").strip() == match.group(2)
            and _relation_supported(match.group(2), rows[index], source)
        ):
            continue
        result.append(flag)
    return result


def structural_quality_flags(item: Dict[str, Any]) -> List[str]:
    """确定性重复和疑似语义丢失分开；短字段相同不视作问题。"""
    flags: List[str] = []
    source = _evidence_text(item)
    fields = {"核心陈述": item.get("statement") or ""}
    case = item.get("case_details") or {}
    if item.get("primary_category") == "项目案例" and isinstance(case, dict):
        for key, label in (("background", "背景"), ("actions", "措施"), ("results", "结果"),
                           ("limitations", "适用限制")):
            fields[label] = case.get(key) or ""
    for name, key in (("条件", "conditions"), ("动作", "actions"), ("例外", "exceptions")):
        values = item.get(key) or []
        if isinstance(values, str):
            values = [values]
        fields[name] = "；".join(str(v) for v in values if v)
    groups: Dict[str, List[str]] = {}
    for name, value in fields.items():
        normalized = _norm(value)
        if len(normalized) >= 28:
            groups.setdefault(normalized, []).append(name)
    duplicates = ["/".join(names) for names in groups.values() if len(names) > 1]
    if duplicates:
        flags.append("结构化重复：同一长段落出现在" + "、".join(duplicates) + "，请分别核对职责")
    metric = item.get("metric_definition") or {}
    if isinstance(metric, dict):
        rows = metric.get("rows") or []
        criteria = str(metric.get("criteria") or "")
        source_count = len(set(re.findall(
            r"\d+(?:\.\d+)?\s*(?:MPa|kPa|分钟|小时|秒|%|℃|毫米|厘米|米|次|元)",
            criteria, re.I)))
        if ((len(_norm(criteria)) >= 35 and source and _norm(criteria) in _norm(source)) or
                (not rows and source_count >= 2)):
            flags.append("指标堆积：多个检查项或整段原文堆入达标基准，请逐项核对并保留关联")
        if rows:
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    flags.append(f"指标结构错误：第{index + 1}行不是有效检查项")
                    continue
                name = str(row.get("name") or "").strip()
                value = str(row.get("value") or "").strip()
                note = str(row.get("note") or "").strip()
                if not name or not (value or note):
                    flags.append(f"指标结构错误：第{index + 1}行缺少检查项或要求")
                # note 只能承载无法用阈值表达的性质，不能把明确数值的同一时限再填入 note。
                from metric_semantics import parse_quantitative_note, quantity_matches_row
                parsed_note = parse_quantitative_note(note)
                if parsed_note:
                    if quantity_matches_row(parsed_note, row):
                        flags.append(
                            f"指标拆分需核对：第{index + 1}行「定性要求」实际上是定量阈值"
                            f"（{note}），请将比较关系、数值与单位分别填写，定性要求留空"
                        )
                    else:
                        flags.append(
                            f"指标关联需核对：第{index + 1}行定性要求中的阈值「{note}」"
                            "与本行比较关系、数值或单位不一致，请对照原文核实，不能直接清空"
                        )
                elif note and re.search(
                    r"\d+(?:\.\d+)?\s*(?:MPa|kPa|分钟|小时|秒|天|次|%|℃|毫米|厘米|米|元)", note, re.I
                ):
                    flags.append(
                        f"指标拆分需核对：第{index + 1}行定性要求同时包含数值和其他描述，"
                        "请分别整理定量阈值与真正的定性条件"
                    )
                # 持续时长/响应时限不得误写成“统计周期”。
                row_period = str(row.get("period") or "").strip()
                if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:秒|分钟|小时|天)", row_period):
                    pattern = r"(?:每|每隔|按).{0,6}" + re.escape(row_period) + r".{0,8}(?:统计|汇总|计算|考核|记录)"
                    if not re.search(pattern, source):
                        flags.append(
                            f"统计口径需核对：第{index + 1}行的「{row_period}」可能是响应时限或持续时间，并非统计周期"
                        )
                # 数值与单位要在原文相邻，不能仅因各自在不同检查项出现就判为对应。
                unit = str(row.get("unit") or "").strip()
                if value and unit and source and not re.search(
                    re.escape(value) + r"\s*" + re.escape(unit), source, re.I
                ):
                    flags.append(
                        f"指标关联需核对：第{index + 1}行的数值「{value}」与单位「{unit}」未在原文相邻出现"
                    )
                # 只能检测是否回源，不能将字符串存在当作语义已正确配对。
                for key, label in (("value", "数值"), ("unit", "单位"), ("relation", "比较关系")):
                    token = str(row.get(key) or "").strip()
                    if token and source:
                        accepted = (_relation_supported(token, row, source) if key == "relation"
                                    else _norm(token) in _norm(source))
                        if not accepted:
                            flags.append(f"指标关联需核对：第{index + 1}行的{label}「{token}」未在证据摘录中直接对应")
        elif not criteria and item.get("primary_category") == "指标数据":
            flags.append("结构化未完成：指标缺少可核对的要求")
    # 数值+单位必须仍能在整理结果中逐项找到；只做保守的表面完整性校验。
    if item.get("primary_category") == "指标数据" and source and isinstance(metric, dict):
        source_measurements = set(re.findall(
            r"\d+(?:\.\d+)?\s*(?:MPa|kPa|分钟|小时|秒|%|℃|毫米|厘米|米|次|元)",
            source, re.I))
        structured = _norm("；".join(
            " ".join(str(row.get(key) or "") for key in ("relation", "value", "unit", "period", "note", "linkage"))
            for row in metric.get("rows") or [] if isinstance(row, dict)
        ) or str(metric.get("criteria") or ""))
        missing = [measure for measure in source_measurements if _norm(measure).lower() not in structured.lower()]
        if missing:
            flags.append("指标关联需核对：来源数值或单位在指标行中未找到：" + "、".join(sorted(missing)))
    # 对所有类别检查明确的数字与单位是否仍保留（仅校验表面覆盖，语义关联仍须人工核对）。
    if source and item.get("_extraction_mode") != "offline-fallback":
        source_numbers = set(re.findall(
            r"\d+(?:\.\d+)?\s*(?:MPa|kPa|分钟|小时|秒|%|℃|毫米|厘米|米|次|元|天)",
            source, re.I))
        structured_parts = [str(item.get("statement") or ""), str(item.get("subject") or "")]
        for key in ("conditions", "actions", "exceptions"):
            values = item.get(key) or []
            structured_parts.extend([values] if isinstance(values, str) else values)
        if isinstance(metric, dict):
            structured_parts.extend([str(metric.get("criteria") or ""), str(metric.get("period") or "")])
            for row in metric.get("rows") or []:
                if isinstance(row, dict):
                    structured_parts.extend(str(v or "") for v in row.values())
        case = item.get("case_details") or {}
        if isinstance(case, dict):
            structured_parts.extend(str(v or "") for v in case.values())
        structured_text = _norm("；".join(str(part) for part in structured_parts)).lower()
        missing_numbers = [v for v in source_numbers if _norm(v).lower() not in structured_text]
        if missing_numbers:
            flags.append("信息遗漏需核对：来源中的数值与单位未完整保留：" + "、".join(sorted(missing_numbers)))
    # 条件与禁止关系可能失配：这里是语义疑点，不视为自动判定的原文错误。
    if source and item.get("_extraction_mode") != "offline-fallback":
        metric_rows = metric.get("rows") or [] if isinstance(metric, dict) else []
        row_conditions = any(r.get("condition") for r in metric_rows if isinstance(r, dict))
        row_prohibitions = any(
            re.search(r"(?:不得|严禁|禁止|停止)", str(r.get("note") or "") + str(r.get("linkage") or ""))
            for r in metric_rows if isinstance(r, dict)
        )
        if (re.search(r"(?:当|如果|若|发现).{0,24}(?:时|则)", source)
                and not item.get("conditions") and not row_conditions):
            flags.append("条件对应需核对：原文包含触发前提，但整理结果未列出适用条件")
        # “不得超过15分钟”是比较关系，不等于“不得进入现场”一类禁止/停止要求。
        if (re.search(r"(?:不得(?!超过|高于|大于|少于|低于)|严禁|禁止|不允许|不能进入|停止操作)", source)
                and not item.get("exceptions") and not row_prohibitions):
            flags.append("例外对应需核对：原文包含禁止或停止要求，但整理结果未列出限制")
    mode = item.get("_extraction_mode")
    if mode == "offline-fallback":
        flags.append("结构化未完成：在线模型不可用，只保留原文及来源，请人工整理后确认")
    return list(dict.fromkeys(flags))

def requires_targeted_retry(flags: List[str]) -> bool:
    return any(
        f.startswith(("结构化重复", "指标堆积", "指标结构错误", "指标关联需核对",
                      "条件对应需核对", "例外对应需核对", "信息遗漏需核对", "统计口径需核对"))
        or (f.startswith("指标拆分需核对") and "同时包含数值" in f)
        for f in flags
    )

