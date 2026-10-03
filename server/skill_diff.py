"""
知行有策 - M02 Skill 字段差异（PRD FR13）

- 字段路径按稳定标识定位，不按数组下标：steps[s4].action、inputs[safety_risk].required、
  knowledge_refs[kv_xxx].role；无稳定 ID 的元素（前置条件、字符串列表）用内容摘要 ~xxxxxxxx 定位，
  与 skill_validation 的问题路径同一口径。
- 调整顺序：有稳定 ID 的步骤、输入输出字段只记一条「调整顺序」，不把整段显示为修改（AC12）；
  字符串列表和引用列表的先后没有业务含义，顺序变化不记差异。
- 只比较审核人可以编辑的内容：A~E 组（不含系统汇总的 unsupported_items）与 G 组 maintainer；
  F 组治理字段与其余 G 组字段由系统或后续模块填写，不计入差异。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from skill_constants import FIELD_GROUP_LABELS
from skill_validation import content_key, load_skill_schema

_MISSING = object()

# 不计入差异的字段（F 组、系统汇总、其余 G 组）
IGNORED_FIELDS = frozenset({
    "schema_version", "skill_id", "version_number", "status", "generation", "review", "stale_reason",
    "visibility", "unsupported_items", "test_set_ref", "tools", "pricing", "license", "published_at",
})

# 有稳定标识的数组：标识字段、是否记录顺序调整
ID_ARRAYS = {
    "knowledge_refs": ("atom_version_id", False),
    "inputs": ("key", True),
    "outputs": ("key", True),
    "steps": ("step_id", True),
}
# 数组元素内部、按集合比较的字段（先后没有含义）
SET_LIKE_FIELDS = frozenset({"refs", "used_in_steps"})

FIELD_LABELS = {
    "name": "名称",
    "goal": "业务目标",
    "trigger_description": "触发描述",
    "task_type": "任务类型",
    "scene_id": "所属场景",
    "applies_to": "适用范围",
    "customer_types": "客户类型",
    "property_types": "业态",
    "conditions": "适用条件",
    "not_applies_to": "不适用范围",
    "knowledge_refs": "知识引用",
    "atom_item_id": "知识条目",
    "atom_version_id": "知识版本",
    "role": "引用角色",
    "role_reason": "角色说明",
    "used_in_steps": "使用步骤",
    "inputs": "输入字段",
    "outputs": "输出字段",
    "key": "字段键",
    "label": "显示名",
    "type": "类型",
    "required": "是否必需",
    "unit": "单位",
    "allowed_values": "可选值",
    "source_ref": "口径依据",
    "preconditions": "前置条件",
    "text": "内容",
    "ref": "依据",
    "output_template": "输出模板",
    "steps": "步骤",
    "step_id": "步骤编号",
    "kind": "步骤类别",
    "condition": "执行条件",
    "action": "动作",
    "refs": "依据知识",
    "basis": "依据状态",
    "expert_reason": "专家补充理由",
    "on_fail": "失败处理",
    "risk_boundary": "风险边界",
    "escalation_conditions": "人工升级条件",
    "generation_confidence": "生成把握度",
    "level": "等级",
    "reason": "理由",
    "maintainer": "维护人",
}

OP_LABELS = {"add": "新增", "delete": "删除", "modify": "修改", "reorder": "调整顺序"}


def field_group(field: str) -> str:
    """顶层字段所属分组（取自 skill_schema_v1.json 的 x-group）。"""
    node = load_skill_schema().get("properties", {}).get(field) or {}
    return node.get("x-group") or "A"


def field_order() -> List[str]:
    return list(load_skill_schema().get("properties", {}).keys())


def path_root(path: str) -> str:
    for sep in ("[", "."):
        idx = path.find(sep)
        if idx != -1:
            path = path[:idx]
    return path


def describe_path(path: str) -> str:
    """把稳定路径翻成中文说明，例如 steps[s4].action -> 步骤 s4 · 动作。"""
    root = path_root(path)
    parts = [FIELD_LABELS.get(root, root)]
    rest = path[len(root):]
    while rest:
        if rest.startswith("["):
            close = rest.index("]")
            key = rest[1:close]
            if not key.startswith("~"):
                parts[-1] = f"{parts[-1]} {key}"
            rest = rest[close + 1:]
        elif rest.startswith("."):
            rest = rest[1:]
            end = min([i for i in (rest.find("."), rest.find("[")) if i != -1] or [len(rest)])
            name = rest[:end]
            parts.append(FIELD_LABELS.get(name, name))
            rest = rest[end:]
        else:
            break
    return " · ".join(parts)


def _is_empty(value: Any) -> bool:
    return value is _MISSING or value is None


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(b, ensure_ascii=False, sort_keys=True)


class _Differ:
    def __init__(self):
        self.diffs: List[Dict[str, Any]] = []

    def add(self, path: str, op: str, before: Any, after: Any) -> None:
        root = path_root(path)
        group = field_group(root)
        self.diffs.append({
            "path": path,
            "field": root,
            "group": group,
            "group_label": FIELD_GROUP_LABELS.get(group, group),
            "op": op,
            "op_label": OP_LABELS[op],
            "label": describe_path(path),
            "before": None if before is _MISSING else before,
            "after": None if after is _MISSING else after,
        })

    def value(self, path: str, before: Any, after: Any, field: str) -> None:
        if _is_empty(before) and _is_empty(after):
            return
        if _is_empty(before):
            if after in ("", [], {}):
                return
            self.add(path, "add", before, after)
            return
        if _is_empty(after):
            if before in ("", [], {}):
                return
            self.add(path, "delete", before, after)
            return
        if field in ID_ARRAYS and isinstance(before, list) and isinstance(after, list):
            self.id_array(path, before, after, *ID_ARRAYS[field])
        elif isinstance(before, list) and isinstance(after, list) and (
            all(isinstance(v, str) for v in before + after) and field not in ("allowed_values",)
            or all(isinstance(v, dict) for v in before + after)
        ):
            if field in SET_LIKE_FIELDS:
                if set(map(str, before)) != set(map(str, after)):
                    self.add(path, "modify", before, after)
            else:
                self.content_array(path, before, after)
        elif isinstance(before, dict) and isinstance(after, dict):
            for key in list(dict.fromkeys(list(before) + list(after))):
                self.value(f"{path}.{key}", before.get(key, _MISSING), after.get(key, _MISSING), key)
        elif not _same(before, after):
            self.add(path, "modify", before, after)

    def content_array(self, path: str, before: List[Any], after: List[Any]) -> None:
        """无稳定 ID 的数组按内容匹配：只记新增与删除，顺序变化不记。"""
        b_keys = [content_key(v) for v in before]
        a_keys = [content_key(v) for v in after]
        remaining = list(a_keys)
        for key, item in zip(b_keys, before):
            if key in remaining:
                remaining.remove(key)
            else:
                self.add(f"{path}[{key}]", "delete", item, _MISSING)
        leftover = list(b_keys)
        for key, item in zip(a_keys, after):
            if key in leftover:
                leftover.remove(key)
            else:
                self.add(f"{path}[{key}]", "add", _MISSING, item)

    def id_array(self, path: str, before: List[Any], after: List[Any], id_field: str, track_order: bool) -> None:
        def index(items):
            out: Dict[str, Any] = {}
            for item in items:
                ident = item.get(id_field) if isinstance(item, dict) else None
                key = ident.strip() if isinstance(ident, str) and ident.strip() else content_key(item)
                out.setdefault(key, item)
            return out

        b_map, a_map = index(before), index(after)
        for key, item in b_map.items():
            if key not in a_map:
                self.add(f"{path}[{key}]", "delete", item, _MISSING)
        for key, item in a_map.items():
            if key not in b_map:
                self.add(f"{path}[{key}]", "add", _MISSING, item)
        for key, b_item in b_map.items():
            a_item = a_map.get(key)
            if a_item is None:
                continue
            if isinstance(b_item, dict) and isinstance(a_item, dict):
                for prop in list(dict.fromkeys(list(b_item) + list(a_item))):
                    if prop == id_field:
                        continue
                    self.value(f"{path}[{key}].{prop}", b_item.get(prop, _MISSING), a_item.get(prop, _MISSING), prop)
            elif not _same(b_item, a_item):
                self.add(f"{path}[{key}]", "modify", b_item, a_item)
        if track_order:
            common_before = [k for k in b_map if k in a_map]
            common_after = [k for k in a_map if k in b_map]
            if common_before != common_after:
                self.add(path, "reorder", common_before, common_after)


def diff_skill(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """两个版本（或草稿）之间的字段差异列表，按 Schema 字段顺序排列。"""
    before = before or {}
    after = after or {}
    differ = _Differ()
    order = field_order()
    keys = [k for k in order if k in before or k in after]
    keys += [k for k in list(before) + list(after) if k not in order and k not in keys]
    for key in dict.fromkeys(keys):
        if key in IGNORED_FIELDS:
            continue
        differ.value(key, before.get(key, _MISSING), after.get(key, _MISSING), key)
    return differ.diffs


def attach_reasons(diffs: Iterable[Dict[str, Any]], reasons: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """修改理由（可选）按字段路径附加。"""
    reasons = reasons if isinstance(reasons, dict) else {}
    out = []
    for d in diffs:
        reason = reasons.get(d["path"])
        out.append({**d, "reason": reason.strip() if isinstance(reason, str) and reason.strip() else None})
    return out


def summarize_diffs(diffs: List[Dict[str, Any]], resolution_counts: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """FR13 修改摘要：修改字段数、新增和删除的步骤数、各组改动数、无依据项处理结果。"""
    steps_added = sum(1 for d in diffs if d["field"] == "steps" and d["op"] == "add" and d["path"].count("[") == 1
                      and "." not in d["path"])
    steps_deleted = sum(1 for d in diffs if d["field"] == "steps" and d["op"] == "delete" and "." not in d["path"])
    by_group: Dict[str, int] = {}
    for d in diffs:
        by_group[d["group"]] = by_group.get(d["group"], 0) + 1
    return {
        "changed_field_count": len(diffs),
        "steps_added": steps_added,
        "steps_deleted": steps_deleted,
        "steps_reordered": any(d["field"] == "steps" and d["op"] == "reorder" for d in diffs),
        "by_group": by_group,
        "unsupported_resolutions": dict(resolution_counts or {}),
    }


def _md_value(value: Any) -> str:
    if value is None:
        return "（空）"
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False)
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= 300 else text[:300] + "…"


def render_export_markdown(payload: Dict[str, Any]) -> str:
    """FR13 导出：原稿、终稿、差异清单（Markdown）。内容与 JSON 导出同源。"""
    orig, final = payload["original"], payload["final"]
    lines = [
        f"# {payload.get('name') or payload['skill_id']}：专家修改前后差异",
        "",
        f"- Skill 标识：{payload['skill_id']}",
        f"- 所属场景：{payload.get('scene_name') or payload.get('scene_id')}",
        f"- 当前状态：{payload.get('status_label')}",
        f"- 原稿：第 {orig['version_number']} 版（{orig['version_kind_label']}，{orig['created_at']}）",
        f"- 终稿：第 {final['version_number']} 版（{final['version_kind_label']}，{final['created_at']}）",
        f"- 导出时间：{payload['exported_at']}",
        "",
        "## 修改摘要",
        "",
    ]
    summary = payload["summary"]
    lines.append(f"- 修改字段数：{summary['changed_field_count']}")
    lines.append(f"- 新增步骤：{summary['steps_added']}；删除步骤：{summary['steps_deleted']}"
                 + ("；步骤顺序有调整" if summary.get("steps_reordered") else ""))
    groups = "；".join(f"{g} {FIELD_GROUP_LABELS.get(g, g)} {n} 处" for g, n in sorted(summary["by_group"].items()))
    lines.append(f"- 分组：{groups or '无'}")
    res = summary.get("unsupported_resolutions") or {}
    if res:
        from skill_constants import UNSUPPORTED_RESOLUTION_LABELS
        lines.append("- 无依据项处理：" + "；".join(
            f"{UNSUPPORTED_RESOLUTION_LABELS.get(k, k)} {v} 项" for k, v in res.items()))
    lines += ["", "## 差异清单", ""]
    if payload["diffs"]:
        lines.append("| 分组 | 字段路径 | 说明 | 操作 | 修改前 | 修改后 | 修改理由 |")
        lines.append("|---|---|---|---|---|---|---|")
        for d in payload["diffs"]:
            lines.append(
                f"| {d['group']} {d['group_label']} | `{d['path']}` | {d['label']} | {d['op_label']} | "
                f"{_md_value(d['before'])} | {_md_value(d['after'])} | {_md_value(d.get('reason')) if d.get('reason') else ''} |"
            )
    else:
        lines.append("两个版本内容相同，没有差异。")
    if payload.get("review_records"):
        lines += ["", "## 审核记录", "", "| 时间 | 动作 | 操作人 | 起始版本 | 结果版本 | 意见 |", "|---|---|---|---|---|---|"]
        for r in payload["review_records"]:
            lines.append(
                f"| {r['created_at']} | {r['action_label']} | {r.get('operator_name') or r['operator_id']} | "
                f"{r.get('from_version_number') or ''} | {r.get('to_version_number') or ''} | {_md_value(r.get('comment')) if r.get('comment') else ''} |"
            )
    lines += [
        "", "## 原稿", "", "```json", json.dumps(orig["skill_json"], ensure_ascii=False, indent=2), "```",
        "", "## 终稿", "", "```json", json.dumps(final["skill_json"], ensure_ascii=False, indent=2), "```", "",
    ]
    return "\n".join(lines)
