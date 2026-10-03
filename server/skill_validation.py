"""
知行有策 - M02 Skill 结构与引用校验器（M02-A）

依据 0926-Skill工厂模块PRD：第 3 章 Skill Schema v1、第 4 章 R1/R3/R4/R6 与 4.3、FR07 校验表。

- 结构校验读取 skill_schema_v1.json（唯一结构定义）。便携包运行时未打包 jsonschema，
  因此这里用轻量解释器执行 Schema 中实际用到的关键字子集：
  type / const / enum / required / properties / additionalProperties / items /
  minItems / maxItems / minLength / maxLength / pattern / allOf / anyOf / if-then-else / $ref。
- 引用资格（R1）一律调用 eligibility.check_knowledge_eligibility，不另写判断。
- 本模块只读数据库，不调用大模型，不写入任何数据。

问题列表每项含 level（hard_error / hint）、code、path、message；path 使用稳定标识，
例如 steps[s4].action、inputs[safety_risk].required、knowledge_refs[kv_xxx].role，
不使用数组下标。无稳定 ID 的数组元素（如 preconditions、字符串列表）用内容摘要
~xxxxxxxx 定位，调整顺序后不变。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from eligibility import check_knowledge_eligibility
from m02_common import content_key, iter_skill_refs, json_value as _json_value, stable_item_key
from skill_constants import SKILL_SCHEMA_VERSION, TBD_EXPERT

SCHEMA_PATH = Path(__file__).resolve().with_name("skill_schema_v1.json")

HARD_ERROR = "hard_error"
HINT = "hint"

# PRD 4.3：建议引用原子数区间（超出只提示，不阻断）
REF_COUNT_MIN = 3
REF_COUNT_MAX = 8

# 例外落点文本相似规则（可替换）：
# 文本先去掉空白、标点与 ASCII 字母（分支条件里的变量名、布尔值不参与比较），再切成相邻两字组。
# 重合系数 = 共有两字组数 / min(例外两字组数, 落点两字组数)。
# 同时满足「重合系数 >= 0.5」与「共有两字组数 >= min(3, 两侧两字组数)」视为已落点。
EXCEPTION_MATCH_THRESHOLD = 0.5
EXCEPTION_MIN_SHARED_BIGRAMS = 3

# PRD 4.3 五类主分类到默认引用角色
CATEGORY_DEFAULT_ROLES = {
    "制度与标准": ("判断规则", "前置条件"),
    "方法与工具": ("执行动作",),
    "指标数据": ("指标口径",),
    "项目案例": ("案例参考",),
    "专家经验": ("例外处理",),
}
# atom_type 与主分类不一致时，以 atom_type 为更细参考（PRD 4.3 末段）
ATOM_TYPE_DEFAULT_ROLES = {
    "规则": ("判断规则", "前置条件"),
    "判断": ("判断规则",),
    "方法": ("执行动作",),
    "案例": ("案例参考",),
    "指标": ("指标口径",),
    "经验": ("例外处理",),
}
ATOM_TYPE_NATURAL_CATEGORY = {
    "规则": "制度与标准",
    "方法": "方法与工具",
    "案例": "项目案例",
    "指标": "指标数据",
    "经验": "专家经验",
    # 「判断」没有唯一对应的主分类，始终按 atom_type 取默认角色
}

GENERIC_BASIS_KINDS = ("输入校验", "人工确认")

STAGE_GENERATION = "generation"
STAGE_REVIEW = "review"

UNSUPPORTED_STEP = "无依据步骤"
UNSUPPORTED_NUMBER = "疑似无依据数值"
UNSUPPORTED_EXCEPTION = "例外未落点"


# ---------------------------------------------------------------------------
# 问题与报告
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    level: str
    code: str
    path: str
    message: str
    detail: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {"level": self.level, "code": self.code, "path": self.path, "message": self.message}
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass
class SkillValidationReport:
    issues: List[ValidationIssue] = field(default_factory=list)
    unsupported_items: List[Dict[str, Any]] = field(default_factory=list)
    visibility: str = "admin_only"
    atom_snapshots: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def hard_errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == HARD_ERROR]

    @property
    def hints(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == HINT]

    @property
    def passed(self) -> bool:
        """没有硬性错误即可进入审核；提示项不阻断（FR07）。"""
        return not self.hard_errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SKILL_SCHEMA_VERSION,
            "passed": self.passed,
            "hard_error_count": len(self.hard_errors),
            "hint_count": len(self.hints),
            "issues": [i.to_dict() for i in self.issues],
            "unsupported_items": list(self.unsupported_items),
            "visibility": self.visibility,
        }


# ---------------------------------------------------------------------------
# 稳定路径
# ---------------------------------------------------------------------------

def _join(path: str, prop: str) -> str:
    return f"{path}.{prop}" if path else prop


def _item(path: str, key: str) -> str:
    return f"{path}[{key}]"


# ---------------------------------------------------------------------------
# 结构校验：轻量 JSON Schema 解释器
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_skill_schema() -> Dict[str, Any]:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}

_TYPE_LABELS = {
    "string": "字符串", "integer": "整数", "number": "数值", "boolean": "布尔值",
    "object": "对象", "array": "数组", "null": "空值",
}

_CODE_MESSAGES = {
    "APPLIES_TO_EMPTY": "适用范围 customer_types、property_types、conditions 至少一项非空",
    "NUMBER_UNIT_MISSING": "number 类字段必须填写单位 unit",
    "ENUM_ALLOWED_VALUES_MISSING": "enum 类字段必须填写可选值 allowed_values",
    "USED_IN_STEPS_EMPTY": "除「案例参考」外，引用必须注明使用步骤 used_in_steps",
}


def _same_value(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


class _SchemaWalker:
    def __init__(self, root: Dict[str, Any]):
        self.root = root

    def resolve(self, ref: str) -> Dict[str, Any]:
        if not ref.startswith("#/"):
            raise ValueError(f"不支持的 $ref：{ref}")
        node: Any = self.root
        for part in ref[2:].split("/"):
            node = node[part]
        return node

    def check(self, value: Any, node: Dict[str, Any], path: str, allow_tbd: bool = False) -> List[Tuple[str, str, str]]:
        errors: List[Tuple[str, str, str]] = []
        self._walk(value, node, path, allow_tbd, errors)
        return errors

    def _walk(self, value, node, path, allow_tbd, errors):
        local: List[Tuple[str, str, str]] = []
        allow_tbd = allow_tbd or bool(node.get("x-allow-tbd"))

        if "$ref" in node:
            self._walk(value, self.resolve(node["$ref"]), path, allow_tbd, local)

        type_ok = True
        if "type" in node:
            types = node["type"] if isinstance(node["type"], list) else [node["type"]]
            if not any(_TYPE_CHECKS[t](value) for t in types):
                type_ok = False
                expected = "或".join(_TYPE_LABELS.get(t, t) for t in types)
                local.append(("SCHEMA_TYPE", path, f"类型应为{expected}"))

        if type_ok:
            if "const" in node and not _same_value(value, node["const"]):
                local.append(("SCHEMA_CONST", path, f"取值必须为 {node['const']!r}"))
            if "enum" in node and not any(_same_value(value, e) for e in node["enum"]):
                allowed = "、".join(str(e) for e in node["enum"] if e is not None)
                local.append(("SCHEMA_ENUM", path, f"取值「{value}」不在允许范围：{allowed}"))

            if isinstance(value, str):
                self._walk_string(value, node, path, allow_tbd, local)
            elif isinstance(value, dict):
                self._walk_object(value, node, path, allow_tbd, local)
            elif isinstance(value, list):
                self._walk_array(value, node, path, allow_tbd, local)

            for sub in node.get("allOf", []):
                self._walk(value, sub, path, allow_tbd, local)

            if "anyOf" in node:
                if not any(not self.check(value, sub, path, True) for sub in node["anyOf"]):
                    local.append(("SCHEMA_ANY_OF", path, "不满足任一可选结构"))

            if "if" in node:
                if not self.check(value, node["if"], path, True):
                    if "then" in node:
                        self._walk(value, node["then"], path, allow_tbd, local)
                elif "else" in node:
                    self._walk(value, node["else"], path, allow_tbd, local)

        override = node.get("x-error-code")
        if override:
            message = _CODE_MESSAGES.get(override)
            local = [(override, p, message or m) for (_c, p, m) in local]
        errors.extend(local)

    def _walk_string(self, value, node, path, allow_tbd, errors):
        if value == TBD_EXPERT and "enum" not in node and "const" not in node and not allow_tbd:
            errors.append(("TBD_NOT_ALLOWED", path, "该字段不允许填写占位值 TBD_EXPERT"))
            return
        if "minLength" in node and len(value) < node["minLength"]:
            errors.append(("SCHEMA_MIN_LENGTH", path, "不能为空"))
        if "maxLength" in node and len(value) > node["maxLength"]:
            errors.append(("SCHEMA_MAX_LENGTH", path, f"长度不能超过 {node['maxLength']} 字（当前 {len(value)} 字）"))
        if "pattern" in node and value and not re.search(node["pattern"], value):
            errors.append(("SCHEMA_PATTERN", path, "不能只包含空白字符"))

    def _walk_object(self, value, node, path, allow_tbd, errors):
        for req in node.get("required", []):
            if req not in value:
                errors.append(("SCHEMA_REQUIRED", _join(path, req), "缺少必填字段"))
        props = node.get("properties", {})
        for key, sub in props.items():
            if key in value:
                self._walk(value[key], sub, _join(path, key), allow_tbd, errors)
        if node.get("additionalProperties") is False and "properties" in node:
            for key in value:
                if key not in props:
                    errors.append(("SCHEMA_ADDITIONAL_PROPERTY", _join(path, key), "Schema 未定义该字段"))

    def _walk_array(self, value, node, path, allow_tbd, errors):
        if "minItems" in node and len(value) < node["minItems"]:
            errors.append(("SCHEMA_MIN_ITEMS", path, f"至少需要 {node['minItems']} 项（当前 {len(value)} 项）"))
        if "maxItems" in node and len(value) > node["maxItems"]:
            errors.append(("SCHEMA_MAX_ITEMS", path, f"最多 {node['maxItems']} 项（当前 {len(value)} 项）"))
        id_field = node.get("x-id-field")
        if id_field is None and "$ref" in node:
            id_field = self.resolve(node["$ref"]).get("x-id-field")
        seen = set()
        for item in value:
            key = stable_item_key(item, id_field)
            item_path = _item(path, key)
            if id_field and isinstance(item, dict) and key in seen and not key.startswith("~"):
                errors.append(("DUPLICATE_ID", item_path, f"{id_field}「{key}」重复"))
            seen.add(key)
            if "items" in node:
                self._walk(item, node["items"], item_path, allow_tbd, errors)


def validate_structure(skill: Any) -> List[ValidationIssue]:
    """按 skill_schema_v1.json 检查必填、类型、枚举与条件必填；全部为硬性错误（FR07）。"""
    schema = load_skill_schema()
    if not isinstance(skill, dict):
        return [ValidationIssue(HARD_ERROR, "SCHEMA_TYPE", "", "Skill 必须是 JSON 对象")]
    walker = _SchemaWalker(schema)
    issues: List[ValidationIssue] = []
    seen = set()
    for code, path, message in walker.check(skill, schema, ""):
        # $ref 与引用处的同一约束可能各报一次，按 (code, path) 去重
        if (code, path) in seen:
            continue
        seen.add((code, path))
        issues.append(ValidationIssue(HARD_ERROR, code, path, message))
    return issues


# ---------------------------------------------------------------------------
# 原子读取与快照
# ---------------------------------------------------------------------------

def _text_list(raw: Any) -> List[str]:
    value = _json_value(raw, [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    texts = []
    for entry in value:
        if isinstance(entry, str):
            if entry.strip():
                texts.append(entry.strip())
        elif isinstance(entry, dict):
            text = entry.get("text") or entry.get("content") or " ".join(str(v) for v in entry.values() if v)
            if str(text).strip():
                texts.append(str(text).strip())
    return texts


def load_atom_snapshots(conn: sqlite3.Connection, organization_id: str, version_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """读取当前企业内的原子版本内容，形成引用快照（PRD 4.2 skill_atom_refs 所需字段）。"""
    ids = sorted({v for v in version_ids if isinstance(v, str) and v})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT kv.id AS atom_version_id, kv.item_id AS atom_item_id, kv.title, kv.statement,
               kv.conditions_json, kv.actions_json, kv.exceptions_json, kv.metric_definition_json,
               kv.primary_category, kv.atom_type,
               ki.access_scope AS item_access_scope, d.access_scope AS document_access_scope
        FROM knowledge_versions kv
        JOIN knowledge_items ki ON ki.id = kv.item_id
        JOIN documents d ON d.id = ki.document_id
        WHERE kv.organization_id = ? AND ki.organization_id = ? AND d.organization_id = ?
          AND kv.id IN ({placeholders})
        """,
        [organization_id, organization_id, organization_id, *ids],
    ).fetchall()
    snapshots = {}
    for row in rows:
        metric = _json_value(row["metric_definition_json"], None)
        snapshots[row["atom_version_id"]] = {
            "atom_item_id": row["atom_item_id"],
            "atom_version_id": row["atom_version_id"],
            "title": row["title"],
            "statement": row["statement"] or "",
            "conditions": _text_list(row["conditions_json"]),
            "actions": _text_list(row["actions_json"]),
            "exceptions": _text_list(row["exceptions_json"]),
            "metric_definition": metric if isinstance(metric, (dict, list, str)) else None,
            "primary_category": row["primary_category"],
            "atom_type": row["atom_type"],
            "item_access_scope": row["item_access_scope"],
            "document_access_scope": row["document_access_scope"],
        }
    return snapshots


# ---------------------------------------------------------------------------
# 数值抽取（R6）
# ---------------------------------------------------------------------------

_ARABIC_NUMBER = re.compile(
    r"(?<![A-Za-z_\d.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(%|％|‰)?"
)
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_NUMERAL = "零〇一二两三四五六七八九十百千万"
# 中文数字只在后接数量单位时识别，避免「统一」「一键」等普通词误报
_CN_QUANTITY = re.compile(
    rf"([{_CN_NUMERAL}]+)\s*(个工作日|工作日|分钟|小时|个月|天|周|年|秒|万元|元|倍|公里|平方米|米|人|户)"
)
_CN_PERCENT = re.compile(rf"百分之([{_CN_NUMERAL}]+)")


def _cn_to_number(text: str) -> Optional[int]:
    total, section, num = 0, 0, 0
    seen = False
    for ch in text:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
            seen = True
        elif ch in _CN_UNITS:
            if num == 0:
                num = 1
            section += num * _CN_UNITS[ch]
            num = 0
            seen = True
        elif ch == "万":
            section += num
            total += (section or 1) * 10000
            section, num = 0, 0
            seen = True
    return total + section + num if seen else None


def extract_numbers(text: str) -> List[Tuple[str, float, bool]]:
    """返回 (原文片段, 数值, 是否百分数)。"""
    if not isinstance(text, str) or not text:
        return []
    found: List[Tuple[str, float, bool]] = []
    for m in _ARABIC_NUMBER.finditer(text):
        value = float(m.group(1).replace(",", ""))
        suffix = m.group(2)
        if suffix == "‰":
            found.append((m.group(0).strip(), value / 10, True))
        else:
            found.append((m.group(0).strip(), value, bool(suffix)))
    for m in _CN_PERCENT.finditer(text):
        value = _cn_to_number(m.group(1))
        if value is not None:
            found.append((m.group(0), float(value), True))
    for m in _CN_QUANTITY.finditer(text):
        if text[max(0, m.start() - 3):m.start()].endswith("百分之"):
            continue
        value = _cn_to_number(m.group(1))
        if value is not None:
            found.append((m.group(0), float(value), False))
    return found


def _flatten_text(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out = []
        for v in value.values():
            out.extend(_flatten_text(v))
        return out
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_flatten_text(v))
        return out
    return [str(value)]


def _atom_number_values(atoms: Iterable[Dict[str, Any]]) -> set:
    """所引原子 statement、conditions、actions、exceptions、metric_definition 中的数值集合。"""
    values = set()
    for atom in atoms:
        texts = [atom.get("statement") or ""]
        texts += atom.get("conditions") or []
        texts += atom.get("actions") or []
        texts += atom.get("exceptions") or []
        texts += _flatten_text(atom.get("metric_definition"))
        for text in texts:
            for _raw, value, is_pct in extract_numbers(text):
                values.add(round(value, 6))
                if is_pct:
                    values.add(round(value / 100, 6))
    return values


def _number_supported(value: float, is_pct: bool, atom_values: set) -> bool:
    if round(value, 6) in atom_values:
        return True
    if is_pct and round(value / 100, 6) in atom_values:
        return True
    return False


# ---------------------------------------------------------------------------
# 例外落点（文本相似规则）
# ---------------------------------------------------------------------------

def _normalize_for_match(text: str) -> str:
    text = re.sub(r"[A-Za-z]+", "", text or "")
    return re.sub(r"[\s\W_]+", "", text)


def _bigrams(text: str) -> set:
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def exception_similarity(exception_text: str, target_text: str) -> Tuple[float, int]:
    a = _bigrams(_normalize_for_match(exception_text))
    b = _bigrams(_normalize_for_match(target_text))
    if not a or not b:
        return 0.0, 0
    shared = len(a & b)
    return shared / min(len(a), len(b)), shared


def exception_landed(exception_text: str, targets: Sequence[str]) -> bool:
    return _exception_landed_bigrams(_bigrams(_normalize_for_match(exception_text)),
                                     [_bigrams(_normalize_for_match(t)) for t in targets])


def _exception_landed_bigrams(a: set, targets: Sequence[set]) -> bool:
    if not a:
        return False
    for b in targets:
        if not b:
            continue
        shared = len(a & b)
        score = shared / min(len(a), len(b))
        if score >= EXCEPTION_MATCH_THRESHOLD and shared >= min(EXCEPTION_MIN_SHARED_BIGRAMS, len(a), len(b)):
            return True
    return False


# ---------------------------------------------------------------------------
# 引用校验
# ---------------------------------------------------------------------------

def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> List[Dict[str, Any]]:
    return [v for v in _list(value) if isinstance(v, dict)]


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _default_roles(atom: Dict[str, Any]) -> Tuple[str, ...]:
    category = atom.get("primary_category")
    atom_type = atom.get("atom_type")
    if atom_type in ATOM_TYPE_DEFAULT_ROLES and ATOM_TYPE_NATURAL_CATEGORY.get(atom_type) != category:
        return ATOM_TYPE_DEFAULT_ROLES[atom_type]
    return CATEGORY_DEFAULT_ROLES.get(category, ())


def derive_visibility(atoms: Iterable[Dict[str, Any]], has_unresolved: bool = False) -> str:
    """R4：取所引原子最严格的权限，同时以来源文件权限为上限；无法判断时按管理员专享处理。"""
    atoms = list(atoms)
    if has_unresolved or not atoms:
        return "admin_only"
    for atom in atoms:
        if atom.get("item_access_scope") != "org_internal" or atom.get("document_access_scope") != "org_internal":
            return "admin_only"
    return "org_internal"


def validate_skill(
    conn: sqlite3.Connection,
    skill: Any,
    user: Dict[str, Any],
    *,
    pool_ids: Optional[Iterable[str]] = None,
    stage: str = STAGE_GENERATION,
    now_iso: Optional[str] = None,
) -> SkillValidationReport:
    """
    完整校验：结构（Schema）+ 引用（R1、R3、used_in_steps 一致性）+ 提示（R6、例外落点、4.3）
    + 系统推导（R4 可见范围、unsupported_items）。

    pool_ids：原子池内的原子版本 ID；提供时引用必须在池内。
    stage：generation 表示模型生成后的 G5 校验，此时不允许出现「专家补充」；
           review 表示审核编辑后的复验，「专家补充」须有理由。
    """
    report = SkillValidationReport()
    issues = report.issues
    issues.extend(validate_structure(skill))
    if not isinstance(skill, dict):
        return report

    organization_id = (user or {}).get("organization_id")
    steps = _dicts(skill.get("steps"))
    refs = _dicts(skill.get("knowledge_refs"))
    step_by_id = {_str(s.get("step_id")): s for s in steps if _str(s.get("step_id"))}
    refs_by_step = {stable_item_key(s, "step_id"): {_str(v) for v in _list(s.get("refs")) if _str(v)} for s in steps}

    declared: Dict[str, Dict[str, Any]] = {}
    for ref in refs:
        vid = _str(ref.get("atom_version_id"))
        if vid and vid not in declared:
            declared[vid] = ref
    used_by_ref = {vid: {_str(s) for s in _list(ref.get("used_in_steps")) if _str(s)}
                   for vid, ref in declared.items()}

    # 所有出现的原子版本引用；已声明的定位到 knowledge_refs[vid]，
    # 未声明的取字典序最小的出现位置，保证调整顺序后路径不变
    usages = list(iter_skill_refs(skill))
    usage_paths: Dict[str, str] = {vid: f"knowledge_refs[{vid}]" for vid in declared}
    for vid, path, _kind in usages:
        if vid not in declared:
            usage_paths[vid] = min(path, usage_paths.get(vid, path))

    atoms =load_atom_snapshots(conn, organization_id, usage_paths.keys()) if organization_id else {}
    report.atom_snapshots = atoms
    pool = {p for p in pool_ids} if pool_ids is not None else None

    # R1：存在、正式资格、原子池
    unresolved = False
    for vid, path in usage_paths.items():
        atom = atoms.get(vid)
        if atom is None:
            unresolved = True
            issues.append(ValidationIssue(HARD_ERROR, "REF_ATOM_NOT_FOUND", path, f"引用的原子版本 {vid} 不存在"))
            continue
        result = check_knowledge_eligibility(conn, vid, user, now_iso)
        if not result.is_eligible:
            issues.append(ValidationIssue(
                HARD_ERROR, "REF_NOT_ELIGIBLE", path,
                f"原子版本 {vid} 不满足正式引用资格：{result.reason}",
                {"eligibility_code": result.code},
            ))
        if pool is not None and vid not in pool:
            issues.append(ValidationIssue(HARD_ERROR, "REF_NOT_IN_POOL", path, f"原子版本 {vid} 不在本批次原子池内"))

    for vid, ref in declared.items():
        item_id = _str(ref.get("atom_item_id"))
        atom = atoms.get(vid)
        if atom and item_id and item_id != atom["atom_item_id"]:
            issues.append(ValidationIssue(
                HARD_ERROR, "REF_ITEM_MISMATCH", f"knowledge_refs[{vid}].atom_item_id",
                f"原子条目 {item_id} 与版本 {vid} 所属条目 {atom['atom_item_id']} 不一致",
            ))

    # 前置条件与口径依据须来自 knowledge_refs
    for vid, path, kind in usages:
        if kind in ("precondition", "inputs", "outputs") and vid not in declared:
            label = "前置条件" if kind == "precondition" else "口径依据"
            issues.append(ValidationIssue(HARD_ERROR, "REF_NOT_DECLARED", path,
                                         f"{label}引用的原子版本 {vid} 未列入 knowledge_refs"))

    # R3：步骤依据
    for step in steps:
        sid = stable_item_key(step, "step_id")
        basis = step.get("basis")
        kind = step.get("kind")
        step_refs = [_str(v) for v in _list(step.get("refs")) if _str(v)]
        base = f"steps[{sid}]"
        if basis == "有原子依据" and not step_refs:
            issues.append(ValidationIssue(HARD_ERROR, "R3_REFS_MISSING", f"{base}.refs", "依据状态为「有原子依据」时必须填写依据原子"))
        elif basis == "通用操作" and kind not in GENERIC_BASIS_KINDS:
            if kind in ("规则判断", "计算"):
                message = f"「{kind}」步骤不能标为「通用操作」，须有原子依据或标为「无依据」"
            else:
                message = "「通用操作」仅限输入校验、人工确认步骤"
            issues.append(ValidationIssue(HARD_ERROR, "R3_GENERIC_KIND_INVALID", f"{base}.basis", message))
        elif basis == "专家补充":
            if stage == STAGE_GENERATION:
                issues.append(ValidationIssue(
                    HARD_ERROR, "R3_EXPERT_BASIS_NOT_ALLOWED", f"{base}.basis",
                    "「专家补充」只能由审核人设置，生成阶段不能出现",
                ))
            elif not _str(step.get("expert_reason")):
                issues.append(ValidationIssue(HARD_ERROR, "R3_EXPERT_REASON_MISSING", f"{base}.expert_reason", "「专家补充」必须填写理由"))
        elif basis == "无依据":
            issues.append(ValidationIssue(HINT, "STEP_WITHOUT_BASIS", base, "该步骤没有原子依据，为模型自行补充"))
            report.unsupported_items.append({
                "kind": UNSUPPORTED_STEP,
                "path": base,
                "item_key": f"step:{sid}",
                "message": f"步骤 {sid} 没有原子依据：{_str(step.get('action'))}",
            })

        # 4.3：项目案例不能单独支撑规则判断
        if kind == "规则判断" and step_refs and all(atoms.get(v, {}).get("primary_category") == "项目案例" for v in step_refs):
            issues.append(ValidationIssue(HINT, "CASE_ONLY_RULE_STEP", f"{base}.refs", "规则判断步骤只引用了项目案例，案例不能单独作为判断规则"))

    # knowledge_refs.used_in_steps 与 steps.refs 互相一致
    for vid, ref in declared.items():
        used = [_str(s) for s in _list(ref.get("used_in_steps")) if _str(s)]
        for sid in used:
            if sid not in step_by_id:
                issues.append(ValidationIssue(
                    HARD_ERROR, "USED_IN_UNKNOWN_STEP", f"knowledge_refs[{vid}].used_in_steps",
                    f"使用步骤 {sid} 不存在",
                ))
            elif vid not in refs_by_step[sid]:
                issues.append(ValidationIssue(
                    HARD_ERROR, "USED_IN_STEPS_MISMATCH", f"knowledge_refs[{vid}].used_in_steps",
                    f"引用声明用于步骤 {sid}，但该步骤的 refs 未包含 {vid}",
                ))
    for step in steps:
        sid = stable_item_key(step, "step_id")
        for vid in [_str(v) for v in _list(step.get("refs")) if _str(v)]:
            ref = declared.get(vid)
            if ref is None:
                issues.append(ValidationIssue(
                    HARD_ERROR, "USED_IN_STEPS_MISMATCH", f"steps[{sid}].refs",
                    f"步骤引用的原子版本 {vid} 未列入 knowledge_refs",
                ))
            elif sid not in used_by_ref[vid]:
                issues.append(ValidationIssue(
                    HARD_ERROR, "USED_IN_STEPS_MISMATCH", f"steps[{sid}].refs",
                    f"步骤引用了 {vid}，但该原子的 used_in_steps 未包含 {sid}",
                ))

    declared_atoms = [atoms[v] for v in declared if v in atoms]

    # R6：疑似无依据数值
    atom_values = _atom_number_values(declared_atoms)
    number_targets: List[Tuple[str, str]] = []
    for step in steps:
        if step.get("basis") == "专家补充":
            continue  # 专家已确认并给出理由的内容不再按原子追溯
        sid = stable_item_key(step, "step_id")
        number_targets.append((f"steps[{sid}].condition", step.get("condition")))
        number_targets.append((f"steps[{sid}].action", step.get("action")))
    for pre in _dicts(skill.get("preconditions")):
        number_targets.append((f"preconditions[{content_key(pre)}].text", pre.get("text")))
    for out in _dicts(skill.get("outputs")):
        key = stable_item_key(out, "key")
        number_targets.append((f"outputs[{key}].label", out.get("label")))
        for value in _list(out.get("allowed_values")):
            if isinstance(value, str):
                number_targets.append((f"outputs[{key}].allowed_values", value))
    number_targets.append(("output_template", skill.get("output_template")))

    seen_numbers = set()
    for path, text in number_targets:
        for raw, value, is_pct in extract_numbers(text if isinstance(text, str) else ""):
            if _number_supported(value, is_pct, atom_values):
                continue
            marker = (path, raw)
            if marker in seen_numbers:
                continue
            seen_numbers.add(marker)
            message = f"数值「{raw}」未在所引原子的陈述、条件、动作、例外或指标口径中找到，疑似无依据"
            issues.append(ValidationIssue(HINT, "R6_UNSUPPORTED_NUMBER", path, message, {"value": raw}))
            report.unsupported_items.append({
                "kind": UNSUPPORTED_NUMBER,
                "path": path,
                "value": raw,
                "item_key": f"number:{path}:{raw}",
                "message": message,
            })

    # 例外落点：分支条件（有 condition 的步骤）、escalation_conditions、not_applies_to
    targets: List[str] = []
    for step in steps:
        if _str(step.get("condition")):
            targets.append(f"{_str(step.get('condition'))} {_str(step.get('action'))}")
    for group in ("escalation_conditions", "not_applies_to"):
        targets.extend(t for t in _list(skill.get(group)) if isinstance(t, str) and t.strip() and t.strip() != TBD_EXPERT)
    target_bigrams = [_bigrams(_normalize_for_match(t)) for t in targets]
    for vid in declared:
        atom = atoms.get(vid)
        if not atom:
            continue
        for exc in atom["exceptions"]:
            if _exception_landed_bigrams(_bigrams(_normalize_for_match(exc)), target_bigrams):
                continue
            message = f"所引原子的例外「{exc}」在分支条件、人工升级条件或不适用范围中没有对应"
            issues.append(ValidationIssue(HINT, "EXCEPTION_NOT_LANDED", f"knowledge_refs[{vid}]", message, {"exception": exc}))
            report.unsupported_items.append({
                "kind": UNSUPPORTED_EXCEPTION,
                "path": f"knowledge_refs[{vid}]",
                "atom_version_id": vid,
                "value": exc,
                "item_key": f"exception:{vid}:{content_key(exc)[1:]}",
                "message": message,
            })

    # 4.3 完整性提示
    if declared and not (REF_COUNT_MIN <= len(declared) <= REF_COUNT_MAX):
        issues.append(ValidationIssue(
            HINT, "REF_COUNT_OUT_OF_RANGE", "knowledge_refs",
            f"引用原子 {len(declared)} 条，建议 {REF_COUNT_MIN} 到 {REF_COUNT_MAX} 条",
        ))
    categories = {a.get("primary_category") for a in declared_atoms}
    if declared_atoms and len(categories) == 1:
        category = next(iter(categories)) or "待分类"
        issues.append(ValidationIssue(
            HINT, "SINGLE_CATEGORY", "knowledge_refs",
            f"引用原子全部来自「{category}」，可能缺少口径、方法或例外",
        ))
    for vid, ref in declared.items():
        atom = atoms.get(vid)
        role = ref.get("role")
        if not atom or not isinstance(role, str):
            continue
        expected = _default_roles(atom)
        if expected and role not in expected:
            issues.append(ValidationIssue(
                HINT, "ROLE_DEFAULT_MISMATCH", f"knowledge_refs[{vid}].role",
                f"角色「{role}」与默认对应（{'/'.join(expected)}）不一致",
                {"expected_roles": list(expected)},
            ))

    # R4
    report.visibility = derive_visibility(declared_atoms, has_unresolved=unresolved or len(declared_atoms) < len(declared))
    return report


def apply_derived_fields(skill: Dict[str, Any], report: SkillValidationReport) -> Dict[str, Any]:
    """把系统推导结果写入副本：E 组 unsupported_items、F 组 visibility（FR07 系统推导）。"""
    result = json.loads(json.dumps(skill, ensure_ascii=False))
    result["unsupported_items"] = list(report.unsupported_items)
    result["visibility"] = report.visibility
    return result
