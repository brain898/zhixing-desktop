"""核验本轮落盘证据、系统统计和业务库保护。只输出计数与布尔值。"""
import hashlib
import json
from pathlib import Path
import sqlite3

import review_m02f_demo as demo
import config
from database import get_db
from eligibility import filter_eligible_version_ids
from skill_generation import load_actor

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts/m02f"
proof = {"data_dir": str(demo.DATA), "kind": "actual_records"}
with get_db() as conn:
    user = load_actor(conn, "usr_admin_001", "org_greentown")
    vids = [r[0] for r in conn.execute("SELECT active_version_id FROM knowledge_items WHERE organization_id=?", (user["organization_id"],))]
    eligible = filter_eligible_version_ids(conn, vids, user)
    placeholders = ','.join('?' for _ in eligible)
    categories = {r[0]: r[1] for r in conn.execute(f"SELECT primary_category, COUNT(*) FROM knowledge_versions WHERE id IN ({placeholders}) GROUP BY primary_category", eligible)}
    docs = [dict(r) for r in conn.execute("""SELECT d.id, d.title, COUNT(ki.id) atom_count FROM documents d
        LEFT JOIN knowledge_items ki ON ki.document_id=d.id WHERE d.organization_id=? AND d.is_deleted=0 GROUP BY d.id""", (user["organization_id"],))]
    statuses = {r[0]: r[1] for r in conn.execute("SELECT status,COUNT(*) FROM skills WHERE organization_id=? GROUP BY status", (user["organization_id"],))}
    review_tasks = [{"status": r["status"], "attempt_count": r["attempt_count"],
                    "call_count": len(json.loads(r["model_calls_json"] or "[]")),
                    "failed_call_count": sum(not c.get("ok") for c in json.loads(r["model_calls_json"] or "[]"))}
                   for r in conn.execute("SELECT status,attempt_count,model_calls_json FROM skill_review_tasks WHERE organization_id=?", (user["organization_id"],))]
    running = sum(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ('queued','running')").fetchone()[0]
                  for table in ["processing_tasks", "skill_generation_tasks", "skill_review_tasks"])
    # 真密钥用于相等匹配，不输出，不存哈希，不打印匹配内容。
    key = config.DEEPSEEK_API_KEY
    leaked = False
    if key:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            if table.startswith("sqlite_"):
                continue
            for row in conn.execute(f'SELECT * FROM "{table}"'):
                leaked |= any(key in v for v in row if isinstance(v, str))
proof.update({"source_documents": docs, "extracted_atoms": len(vids), "eligible_atoms": len(eligible),
              "ready_by_category": categories, "material_category_count": len(categories),
              "skill_statuses": statuses, "review_tasks": review_tasks, "running_tasks": running,
              "api_key_found_in_db": leaked})
stats = json.loads((OUT / "statistics.json").read_text(encoding="utf-8"))
assert stats["summary"]["status_counts"] == statuses
assert stats["summary"]["candidate_count"] == sum(statuses.values())
assert len(eligible) == 221 and len(docs) == 4 and len(categories) == 5
assert sum(statuses.values()) >= 10 and running == 0 and not leaked
proof["export_consistency"] = {}
for path in (OUT / "exports").glob("*.json"):
    value = json.loads(path.read_text(encoding="utf-8"))
    with sqlite3.connect(str(demo.DATA / "zhixing.db")) as conn:
        for name in ("original", "final"):
            row = conn.execute("SELECT skill_json FROM skill_versions WHERE id=?", (value[name]["version_id"],)).fetchone()
            assert row and json.loads(row[0]) == value[name]["skill_json"]
    proof["export_consistency"][value["skill_id"]] = True
baseline = json.loads((OUT / "baseline_files.json").read_text(encoding="utf-8"))
proof["protected_files_unchanged"] = {}
for filename, expected in baseline.items():
    path = ROOT / filename
    actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    assert actual == expected, f"受保护文件变化：{filename}"
    proof["protected_files_unchanged"][filename] = True
for path in OUT.rglob("*"):
    if path.is_file() and path.suffix in {".md", ".json", ".txt", ".log"}:
        raw = path.read_bytes()
        # PowerShell 的重定向可能采用 UTF-16，其原始日志保留；JSON/MD 必须有效 UTF-8。
        if path.suffix in {".json", ".md"}:
            raw.decode("utf-8")
        if key:
            assert key.encode("utf-8") not in raw, "证据存在敏感配置"
(OUT / "evidence_check.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"passed": True, "eligible_atoms": len(eligible), "material_categories": len(categories),
                  "candidates": sum(statuses.values()), "exports": len(proof["export_consistency"]),
                  "business_db_unchanged": True, "api_key_leaked": False}, ensure_ascii=False))
