"""
M02-C 真实模型联调：生成批次（PRD FR04~FR08）。

- 隔离数据目录（默认 %TEMP%/zhixing_m02c_live），不读写 server/data/zhixing.db。
- 导入 03-素材/Skill工厂测试资料/ 的 01~04 号文件（不导入「00-说明（不导入）.md」），
  走 M01 真实解析与真实 DeepSeek 抽取；为测试目的用确认接口逐条确认原子（被确认门槛拦下的如实记录）。
- 场景目录用真实模型整理并按建议全部确认；然后对可生成的场景各跑 1 个真实批次（至少 2 个）。
- 模型名、Base URL、密钥均取自现有配置；不打印、不保存任何密钥。
- 分阶段可续跑：数据目录已有导入结果时跳过导入与确认（--fresh 重新开始）。
- 结果写入 artifacts/m02c/live_generation_result.json 与 live_sample_candidate.json。

用法：python tests/run_m02c_live_generation.py [--fresh] [--max-scenes 3]
"""

import argparse
import collections
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SERVER_DIR = BASE_DIR / "server"
MATERIAL_DIR = BASE_DIR.parent.parent / "03-素材" / "Skill工厂测试资料"
OUT_DIR = BASE_DIR / "artifacts" / "m02c"

parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", default=str(Path(tempfile.gettempdir()) / "zhixing_m02c_live"))
parser.add_argument("--fresh", action="store_true")
parser.add_argument("--max-scenes", type=int, default=3)
parser.add_argument("--skip-batches", action="store_true")
parser.add_argument("--scenes", default="", help="逗号分隔的场景名称；为空时按可用知识数取前 --max-scenes 个")
ARGS = parser.parse_args()

DATA_DIR = Path(ARGS.data_dir).resolve()
if ARGS.fresh and DATA_DIR.exists():
    shutil.rmtree(DATA_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ["ZHIXING_DATA_DIR"] = str(DATA_DIR)
os.environ["ZHIXING_DB_PATH"] = str(DATA_DIR / "zhixing.db")
os.environ["ZHIXING_STORAGE_DIR"] = str(DATA_DIR / "storage")
os.environ["ZHIXING_DEMO_ADMIN_PASSWORD"] = "Admin@Zhixing2026"
os.environ["ZHIXING_DEMO_MEMBER_PASSWORD"] = "Member@Zhixing2026"
os.environ["ZHIXING_DEMO_OTHER_PASSWORD"] = "Other@Zhixing2026"
sys.path.insert(0, str(SERVER_DIR))

import config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from database import get_db, get_db_path, init_db  # noqa: E402
from seed import seed_data  # noqa: E402
import scene_catalog as sc  # noqa: E402
import skill_generation as sg  # noqa: E402
from main import app  # noqa: E402
from tasks import wait_for_background_tasks  # noqa: E402

assert get_db_path().resolve().parent == DATA_DIR, "数据库必须位于隔离数据目录"
LOG_LINES = []


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    LOG_LINES.append(line)
    print(line, flush=True)


def wait_until(check, label, timeout, interval=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(f"等待超时：{label}")


def main():
    if not config.DEEPSEEK_API_KEY:
        raise SystemExit("未配置在线模型，无法做真实联调")
    fresh_db = not (DATA_DIR / "zhixing.db").exists()
    init_db()
    if fresh_db:
        seed_data(force=True)
    client = TestClient(app)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    with get_db() as conn:
        admin = sg.load_actor(conn, "usr_admin_001", "org_greentown")
    report = {
        "kind": "real_model_call",
        "note": "全部为真实 DeepSeek 调用；测试资料为团队自编，数值与制度均为虚构",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "model_name": config.DEEPSEEK_MODEL,
        "prompt_versions": sg.prompt_versions(),
        "data_dir_isolated": True,
    }

    # ---- 1. 导入与真实抽取 ------------------------------------------------------
    files = sorted(p for p in MATERIAL_DIR.glob("0[1-4]-*.md"))
    assert len(files) == 4 and not any(p.name.startswith("00") for p in files)
    imports = []
    with get_db() as conn:
        existing = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM documents WHERE is_deleted = 0")}
    for path in files:
        started = time.time()
        doc_title = path.stem
        doc_id = next((v for k, v in existing.items() if k.startswith(doc_title[:6])), None)
        if doc_id is None:
            resp = client.post("/api/documents/upload", headers=headers,
                               files={"file": (path.name, path.read_bytes(), "text/markdown")},
                               data={"duplicate_mode": "ask"})
            resp.raise_for_status()
            doc_id = resp.json().get("document_id") or resp.json().get("existing_document_id")
            log(f"已上传 {path.name}，等待解析与真实抽取…")

        def done():
            detail = client.get(f"/api/documents/{doc_id}", headers=headers).json()
            version = detail["versions"][0]
            return version if version["processing_status"] in ("completed", "failed", "partial_failed") else None

        version = wait_until(done, f"{path.name} 抽取", timeout=1800, interval=3)
        items = client.get("/api/knowledge/items", params={"document_id": doc_id}, headers=headers).json()["items"]
        imports.append({"file": path.name, "document_id": doc_id, "processing_status": version["processing_status"],
                        "error_summary": version.get("error_summary"), "atom_count": len(items),
                        "elapsed_seconds": round(time.time() - started, 1)})
        log(f"{path.name}：{version['processing_status']}，抽取 {len(items)} 条")
    report["import"] = imports

    # ---- 2. 测试夹具：逐条确认（走确认门槛） -------------------------------------
    confirmed, blocked = [], []
    items = client.get("/api/knowledge/items", headers=headers).json()["items"]
    for item in items:
        if item["review_status"] == "confirmed":
            confirmed.append(item["active_version_id"])
            continue
        detail = client.get(f"/api/knowledge/items/{item['id']}", headers=headers).json()
        token_ = detail["active_version"]["revision_token"]
        resp = client.post(f"/api/knowledge/items/{item['id']}/confirm", json={"revision_token": token_}, headers=headers)
        if resp.status_code == 200:
            confirmed.append(resp.json()["target_version_id"])
        else:
            blocked.append({"title": item["title"], "reason": resp.json().get("detail")})
    log(f"确认 {len(confirmed)} 条，被确认门槛拦下 {len(blocked)} 条；等待建立检索索引…")

    def indexed():
        with get_db() as conn:
            rows = conn.execute(
                f"SELECT index_status FROM knowledge_versions WHERE id IN ({','.join('?' for _ in confirmed)})",
                confirmed).fetchall()
        statuses = collections.Counter(r["index_status"] for r in rows)
        return statuses if statuses.get("indexing", 0) + statuses.get("not_indexed", 0) == 0 else None

    index_status = wait_until(indexed, "索引", timeout=900, interval=2) if confirmed else {}
    with get_db() as conn:
        category_counts = collections.Counter(
            r["primary_category"] for r in conn.execute(
                "SELECT kv.primary_category FROM knowledge_versions kv JOIN knowledge_items ki ON ki.active_version_id = kv.id "
                "WHERE kv.review_status = 'confirmed' AND kv.index_status = 'ready'").fetchall())
    report["confirm"] = {"extracted_total": len(items), "confirmed": len(confirmed), "blocked": len(blocked),
                         "blocked_details": blocked, "index_status": dict(index_status),
                         "ready_by_category": dict(category_counts)}

    # ---- 3. 场景目录（真实模型整理，按建议全部确认） -----------------------------
    with get_db() as conn:
        has_catalog = sc.catalog_exists(conn, admin["organization_id"])
    if not has_catalog:
        with get_db() as conn:
            suggestion = sc.create_merge_suggestion(conn, admin)
        sc.run_merge_suggestion(suggestion["suggestion_id"])
        with get_db() as conn:
            suggestion = sc.get_suggestion(conn, admin["organization_id"], suggestion["suggestion_id"])
        if suggestion["status"] != "completed":
            raise SystemExit(f"场景整理失败：{suggestion.get('error_message')}")
        groups = [{k: g.get(k) for k in ("name", "description", "typical_problems", "tags")} for g in suggestion["groups"]]
        with get_db() as conn:
            sc.confirm_merge_suggestion(conn, admin["organization_id"], admin["id"], suggestion["suggestion_id"], groups)
        report["catalog_suggestion"] = {"status": suggestion["status"], "attempt_count": suggestion["attempt_count"],
                                        "groups": suggestion["groups"], "unassigned_tags": suggestion["unassigned_tags"]}
    cards = client.get("/api/skill-factory/scenes/cards", headers=headers).json()["cards"]
    report["scenes"] = [{"scene_id": c["scene_id"], "name": c["name"], "aliases": c["aliases"],
                         "available_atom_count": c["available_atom_count"], "category_coverage": c["category_coverage"],
                         "can_generate": c["can_generate"]} for c in cards]
    log("场景：" + "；".join(f"{c['name']}({c['available_atom_count']})" for c in cards))

    if ARGS.skip_batches:
        return finish(report)

    # ---- 4. 真实生成批次 ---------------------------------------------------------
    targets = sorted([c for c in cards if c["can_generate"] and not c.get("running_batch_id")],
                     key=lambda c: -c["available_atom_count"])[:ARGS.max_scenes]
    if ARGS.scenes:
        wanted = [n.strip() for n in ARGS.scenes.split(",") if n.strip()]
        extra = [c for c in cards if c["name"] in wanted and c["can_generate"]]
        targets = targets + [c for c in extra if c["scene_id"] not in {t["scene_id"] for t in targets}]
    with get_db() as conn:
        done_scenes = {r["scene_id"] for r in conn.execute(
            "SELECT scene_id FROM skill_generation_batches WHERE status != 'running'").fetchall()}
    batches = []
    for card in targets:
        if card["scene_id"] in done_scenes:
            log(f"场景 {card['name']} 已有批次，复用")
            with get_db() as conn:
                bid = conn.execute("SELECT id FROM skill_generation_batches WHERE scene_id = ? ORDER BY initiated_at DESC",
                                   (card["scene_id"],)).fetchone()["id"]
            batches.append((card, bid, None))
            continue
        started = time.time()
        resp = client.post(f"/api/skill-factory/scenes/{card['scene_id']}/batches", json={}, headers=headers)
        resp.raise_for_status()
        bid = resp.json()["batch_id"]
        log(f"场景 {card['name']} 发起批次 {bid}")
        wait_until(lambda: client.get(f"/api/skill-factory/batches/{bid}", headers=headers).json()["status"] != "running",
                   f"批次 {bid}", timeout=3600, interval=5)
        batches.append((card, bid, round(time.time() - started, 1)))
        log(f"批次 {bid} 结束")
    wait_for_background_tasks(timeout=60)
    report["batches"] = [summarize_batch(client, headers, card, bid, elapsed) for card, bid, elapsed in batches]
    finish(report)


def summarize_batch(client, headers, card, batch_id, elapsed):
    detail = client.get(f"/api/skill-factory/batches/{batch_id}", headers=headers).json()
    with get_db() as conn:
        tasks = [dict(r) for r in conn.execute(
            "SELECT task_type, task_key, status, attempt_count, result_json, model_calls_json, error_message "
            "FROM skill_generation_tasks WHERE batch_id = ? ORDER BY created_at", (batch_id,)).fetchall()]
    calls = [c for t in tasks for c in json.loads(t["model_calls_json"] or "[]")]
    repair_hard_codes = collections.Counter()
    for t in tasks:
        if t["task_type"] == "validate_store" and t["result_json"]:
            res = json.loads(t["result_json"])
            for issue in res.get("issues", []):
                if issue["level"] == "hard_error":
                    repair_hard_codes[issue["code"]] += 1
    candidates = []
    for cand in detail["candidates"]:
        full = client.get(f"/api/skill-factory/skills/{cand['skill_id']}", headers=headers).json()
        hint_codes = collections.Counter(i["code"] for i in full["issues"] if i["level"] == "hint")
        unsupported = collections.Counter(u["kind"] for u in (full["skill_json"] or {}).get("unsupported_items") or [])
        candidates.append({
            "skill_id": cand["skill_id"], "name": cand["name"], "status": cand["status"],
            "confidence_level": cand["confidence_level"], "ref_count": cand["ref_count"],
            "unsupported_count": cand["unsupported_count"], "unsupported_by_kind": dict(unsupported),
            "hint_codes": dict(hint_codes), "similar_skills": cand["similar_skills"],
            "hard_errors": [i for i in full["issues"] if i["level"] == "hard_error"][:10],
            "repair_count": next((r.get("repair_count") for r in detail["task_results"]
                                  if r.get("skill_id") == cand["skill_id"]), None),
        })
    pool = detail["atom_pool"] or {}
    split = detail["task_split"] or {}
    return {
        "scene": card["name"], "batch_id": batch_id, "status": detail["status"], "status_reason": detail["status_reason"],
        "elapsed_seconds": elapsed,
        "atom_pool": {"size": len(pool.get("atoms") or []), "tag": (pool.get("stats") or {}).get("tag_in_pool"),
                      "semantic": (pool.get("stats") or {}).get("semantic_in_pool"),
                      "semantic_error": pool.get("semantic_error")},
        "split": {
            "task_count": len(split.get("tasks") or []), "generate_count": split.get("generate_count"),
            "no_task_reason": split.get("no_task_reason"),
            "tasks": [{k: t.get(k) for k in ("task_key", "name", "task_type", "status", "skip_reason", "hints",
                                              "possible_duplicate_of", "out_of_pool_ids", "split_reason")}
                      | {"atom_count": len(t.get("atom_version_ids") or [])} for t in split.get("tasks") or []],
            "unused_atoms": split.get("unused_atoms"),
        },
        "task_results": detail["task_results"],
        "model_calls": {"total": len(calls), "failed": sum(1 for c in calls if not c.get("ok")),
                        "by_purpose": dict(collections.Counter(c["purpose"] for c in calls)),
                        "elapsed_seconds": [c.get("elapsed_seconds") for c in calls]},
        "hard_error_codes_seen": dict(repair_hard_codes),
        "candidates": candidates,
    }


def finish(report):
    batches = report.get("batches") or []
    cands = [c for b in batches for c in b["candidates"]]
    report["totals"] = {
        "confirmed_atoms": report["confirm"]["confirmed"],
        "scenes": len(report["scenes"]),
        "batches": len(batches),
        "split_tasks": sum(b["split"]["task_count"] for b in batches),
        "tasks_to_generate": sum(b["split"]["generate_count"] or 0 for b in batches),
        "validation_passed": sum(1 for c in cands if c["status"] == "pending_review"),
        "validation_failed": sum(1 for c in cands if c["status"] == "validation_failed"),
        "call_failed": sum(1 for b in batches for r in b["task_results"] if r.get("status") == "call_failed"),
        "auto_repairs": sum(int(r.get("repair_count") or 0) for b in batches for r in b["task_results"]),
        "hint_codes": dict(sum((collections.Counter(c["hint_codes"]) for c in cands), collections.Counter())),
        "unsupported_by_kind": dict(sum((collections.Counter(c["unsupported_by_kind"]) for c in cands),
                                        collections.Counter())),
        "hard_error_codes_seen": dict(sum((collections.Counter(b["hard_error_codes_seen"]) for b in batches),
                                          collections.Counter())),
    }
    # 密钥不得出现在任何记录中
    key = config.DEEPSEEK_API_KEY
    with get_db() as conn:
        leaked = False
        for table in ("skill_generation_tasks", "skill_generation_batches", "skill_versions", "skills",
                      "scene_merge_suggestions", "audit_logs"):
            for row in conn.execute(f"SELECT * FROM {table}").fetchall():
                if key and key in str(tuple(row)):
                    leaked = True
    report["api_key_found_in_records"] = leaked

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stored = next((c for c in cands if c["status"] == "pending_review"), None)
    if stored:
        with get_db() as conn:
            row = conn.execute("SELECT sv.skill_json FROM skills s JOIN skill_versions sv ON sv.id = s.current_version_id "
                               "WHERE s.id = ?", (stored["skill_id"],)).fetchone()
        sample = json.loads(row["skill_json"])
        (OUT_DIR / "live_sample_candidate.json").write_text(json.dumps(sample, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
        report["sample_candidate_skill_id"] = stored["skill_id"]
    report["log"] = LOG_LINES
    (OUT_DIR / "live_generation_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                         encoding="utf-8")
    log(f"结果已写入 {OUT_DIR / 'live_generation_result.json'}")
    log("汇总：" + json.dumps(report["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
