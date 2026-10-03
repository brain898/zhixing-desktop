"""M02-F 真实演示审核驱动。显式审核计划走正式 API，不绕过校验门槛。

--snapshot 保存实际候选和来源供审阅；--plan FILE 执行审阅后的编辑/退回/驳回；
--export 导出有审核记录的候选原稿、终稿、差异及系统统计。不会输出登录凭据。
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts/m02f"
DATA = Path(os.getenv("ZHIXING_M02F_DEMO_DIR", str(Path(tempfile.gettempdir()) / "zhixing_m02f_demo"))).resolve()
if ROOT == DATA or ROOT in DATA.parents or not (DATA / "zhixing.db").is_file():
    raise SystemExit("仅允许操作已准备的隔离演示库")
os.environ["ZHIXING_DATA_DIR"] = str(DATA)
os.environ["ZHIXING_DB_PATH"] = str(DATA / "zhixing.db")
os.environ["ZHIXING_STORAGE_DIR"] = str(DATA / "storage")
sys.path.insert(0, str(ROOT / "server"))

from fastapi.testclient import TestClient
from database import get_db
from main import app
from skill_constants import REVIEW_CHECKLIST
from tasks import wait_for_background_tasks


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
    login.raise_for_status()
    headers = {"Authorization": "Bearer " + login.json()["token"]}

    def get(url):
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

    if args.snapshot:
        candidates = get("/api/skill-factory/skills")["items"]
        workbenches = []
        for candidate in candidates:
            wb = get(f"/api/skill-factory/skills/{candidate['skill_id']}/workbench")
            workbenches.append({k: wb[k] for k in ("skill_id", "name", "scene_name", "status", "content", "base_json",
                                                  "evaluation", "atoms", "versions", "review_records")})
        with get_db() as conn:
            atoms = [dict(r) for r in conn.execute("""SELECT ki.id AS item_id, kv.id AS version_id, kv.title, kv.statement,
                kv.primary_category, kv.conditions_json, kv.actions_json, kv.exceptions_json, kv.metric_definition_json,
                kv.case_details_json, kv.field_states_json, kv.quality_flags_json, kv.review_status, kv.index_status,
                dv.file_name FROM knowledge_items ki JOIN knowledge_versions kv ON kv.id=ki.active_version_id
                JOIN document_versions dv ON dv.id=kv.source_document_version_id WHERE ki.organization_id=?""",
                                                 (login.json()["user"]["organization_id"],))]
        save(OUT / "review_snapshot.json", {"kind": "real_model_candidates_for_demonstration_review",
                                            "candidates": workbenches, "atoms": atoms})
        print(f"snapshot: {len(workbenches)} candidates, {len(atoms)} atoms")

    if args.plan:
        history = OUT / "review_action_results.json"
        results = json.loads(history.read_text(encoding="utf-8")) if history.exists() else []
        for entry in json.loads(args.plan.read_text(encoding="utf-8")):
            sid, action = entry["skill_id"], entry["action"]
            wb = get(f"/api/skill-factory/skills/{sid}/workbench")
            body = {**entry["body"], "revision_token": wb["revision_token"]}
            if action == "approve":
                body["checklist"] = {k: True for k, _ in REVIEW_CHECKLIST}
                check = client.post(f"/api/skill-factory/skills/{sid}/review/check", headers=headers, json=body)
                check.raise_for_status()
                if not check.json()["evaluation"]["can_approve"]:
                    save(OUT / "review_blocked.json", {"skill_id": sid, "evaluation": check.json()})
                    raise SystemExit("审核门槛未通过，已保存原因；未提交此动作")
            response = client.post(f"/api/skill-factory/skills/{sid}/review/{action}", headers=headers, json=body)
            if response.status_code != 200:
                save(OUT / "review_blocked.json", {"skill_id": sid, "status": response.status_code, "detail": response.json()})
                raise SystemExit("审核动作失败，已保存原因")
            safe = {k: v for k, v in response.json().items() if k not in {"revision_token"}}
            results.append({"skill_id": sid, "requested_action": action, "result": safe})
            save(OUT / "review_action_results.json", results)
            print(f"{sid}: {action} accepted", flush=True)
            if action == "regenerate":
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    current = get(f"/api/skill-factory/skills/{sid}/workbench")
                    if current["status"] != "generating":
                        task = current.get("regenerate_task") or {}
                        results[-1]["completed_status"] = current["status"]
                        results[-1]["regenerate_task_status"] = task.get("status")
                        results[-1]["version_count"] = len(current["versions"])
                        save(OUT / "review_action_results.json", results)
                        if task.get("status") != "completed":
                            raise SystemExit("真实重生成失败，已保留记录")
                        break
                    time.sleep(2)
                else:
                    raise SystemExit("真实重生成等待超时，后台记录保留")
        wait_for_background_tasks(timeout=30)

    if args.export:
        out = OUT / "exports"
        out.mkdir(parents=True, exist_ok=True)
        with get_db() as conn:
            ids = [r[0] for r in conn.execute("SELECT DISTINCT skill_id FROM skill_review_records WHERE organization_id=?",
                                             (login.json()["user"]["organization_id"],))]
            records = [dict(r) for r in conn.execute("""SELECT id, skill_id, action, comment, reject_reason, from_version_id,
                to_version_id, created_at, detail_json FROM skill_review_records WHERE organization_id=? ORDER BY created_at, rowid""",
                                                    (login.json()["user"]["organization_id"],))]
        save(OUT / "review_records.json", records)
        for sid in ids:
            for fmt, ext in [("markdown", "md"), ("json", "json")]:
                response = client.get(f"/api/skill-factory/skills/{sid}/export", headers=headers, params={"format": fmt})
                response.raise_for_status()
                (out / f"{sid}.{ext}").write_bytes(response.content)
        save(OUT / "statistics.json", get("/api/skill-factory/statistics"))
        print(f"exported {len(ids)} Skills in Markdown and JSON")


if __name__ == "__main__":
    main()
