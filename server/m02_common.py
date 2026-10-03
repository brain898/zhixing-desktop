"""Shared serialization and UTC timestamps for Skill factory modules."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(raw: Any, default: Any) -> Any:
    """Decode stored JSON, preserving an explicit JSON null."""
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def json_value(raw: Any, default: Any) -> Any:
    """Decode atom fields, treating an explicit JSON null as missing."""
    value = loads(raw, default)
    return default if value is None else value


def content_key(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "~" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def stable_item_key(item: Any, id_field=None) -> str:
    ident = item.get(id_field) if id_field and isinstance(item, dict) else None
    return ident.strip() if isinstance(ident, str) and ident.strip() else content_key(item)


def iter_skill_refs(skill):
    """Yield (version_id, stable_field_path, kind) for declarations and uses."""
    if not isinstance(skill, dict):
        return

    def objects(key):
        value = skill.get(key)
        return (entry for entry in value if isinstance(entry, dict)) if isinstance(value, list) else ()

    def text(value):
        return value.strip() if isinstance(value, str) else ""

    for ref in objects("knowledge_refs"):
        vid = text(ref.get("atom_version_id"))
        if vid:
            yield vid, f"knowledge_refs[{vid}]", "declaration"
    for step in objects("steps"):
        refs = step.get("refs")
        for value in refs if isinstance(refs, list) else ():
            vid = text(value)
            if vid:
                yield vid, f"steps[{stable_item_key(step, 'step_id')}].refs", "step"
    for pre in objects("preconditions"):
        vid = text(pre.get("ref"))
        if vid:
            yield vid, f"preconditions[{content_key(pre)}].ref", "precondition"
    for group in ("inputs", "outputs"):
        for entry in objects(group):
            vid = text(entry.get("source_ref"))
            if vid:
                yield vid, f"{group}[{stable_item_key(entry, 'key')}].source_ref", group
