"""
M02-D 真实模型联调：退回重生成（PRD FR12 / AC14）。审核环节只有这一个动作调用模型（10.2）。

- 在 M02-C 联调库（默认 %TEMP%/zhixing_m02c_live）的副本上执行，副本位于 %TEMP%/zhixing_m02d_live；
  不读写 server/data/zhixing.db，也不改动 M02-C 联调库本身。
- 模型名、Base URL、密钥取自现有配置；不打印、不保存任何密钥（结束时检查任务表中没有密钥）。
- 选取 2 条待审核候选，按审核意见退回重生成，等待后台任务完成，记录新版本与字段差异。
- 结果写入 artifacts/m02d/live_regenerate_result.json。

用法：python tests/run_m02d_live_regenerate.py
"""

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
OUT_DIR = BASE_DIR / "artifacts" / "m02d"
SOURCE_DIR = Path(tempfile.gettempdir()) / "zhixing_m02c_live"
DATA_DIR = (Path(tempfile.gettempdir()) / "zhixing_m02d_live").resolve()

if not (SOURCE_DIR / "zhixing.db").exists():
    sys.exit("找不到 M02-C 联调库，请先运行 tests/run_m02c_live_generation.py")
if DATA_DIR.exists():
    shutil.rmtree(DATA_DIR)
DATA_DIR.mkdir(parents=True)
shutil.copy2(SOURCE_DIR / "zhixing.db", DATA_DIR / "zhixing.db")
os.environ["ZHIXING_DATA_DIR"] = str(DATA_DIR)
os.environ["ZHIXING_DB_PATH"] = str(DATA_DIR / "zhixing.db")
os.environ["ZHIXING_STORAGE_DIR"] = str(DATA_DIR / "storage")
os.environ["ZHIXING_DEMO_ADMIN_PASSWORD"] = "Admin@Zhixing2026"
sys.path.insert(0, str(SERVER_DIR))

import config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from database import get_db, get_db_path, init_db  # noqa: E402
from main import app  # noqa: E402
from tasks import wait_for_background_tasks  # noqa: E402

assert Path(get_db_path()).resolve().parent == DATA_DIR, "数据库必须位于隔离目录"
if not config.DEEPSEEK_API_KEY:
    sys.exit("在线模型未配置，无法进行真实调用")
init_db()

# 退回意见按联调库中真实候选的问题编写（M02-C 报告 5.2 典型问题 7：不可计算情形只落了一种）
PLAN = [
    ("核对指定期间收缴率",
     "不可计算的情形只写了「台账不一致」一种。请把统计期不一致、收费范围不一致也写进分支步骤或人工升级条件；"
     "不适用范围补充「非物业服务费的收费项目」。", ["D", "E"]),
    (None, "人工升级条件写得太笼统，请逐条写明哪些情形必须转人工，并与所引知识中的例外对应。", ["E"]),
]


def main():
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
    resp.raise_for_status()
    headers = {"Authorization": f"Bearer {resp.json()['token']}"}
    listing = client.get("/api/skill-factory/skills", params={"status": "pending_review"}, headers=headers).json()
    pending = listing["items"]
    print(f"待审核候选 {len(pending)} 条（模型：{config.DEEPSEEK_MODEL}）")
    used = set()
    runs = []
    for name, comment, groups in PLAN:
        target = next((i for i in pending if (name is None or i["name"] == name) and i["skill_id"] not in used), None)
        if not target:
            print(f"跳过：没有找到候选 {name}")
            continue
        used.add(target["skill_id"])
        wb = client.get(f"/api/skill-factory/skills/{target['skill_id']}/workbench", headers=headers).json()
        started = time.monotonic()
        resp = client.post(f"/api/skill-factory/skills/{target['skill_id']}/review/regenerate",
                           json={"revision_token": wb["revision_token"], "comment": comment, "field_groups": groups},
                           headers=headers)
        resp.raise_for_status()
        status = "generating"
        while status == "generating" and time.monotonic() - started < 600:
            time.sleep(1)
            status = client.get(f"/api/skill-factory/skills/{target['skill_id']}/workbench", headers=headers).json()["status"]
        elapsed = round(time.monotonic() - started, 1)
        after = client.get(f"/api/skill-factory/skills/{target['skill_id']}/workbench", headers=headers).json()
        with get_db() as conn:
            task = conn.execute("SELECT * FROM skill_review_tasks WHERE id = ?", (resp.json()["task_id"],)).fetchone()
        calls = json.loads(task["model_calls_json"] or "[]")
        result = json.loads(task["result_json"] or "{}")
        record = next(r for r in reversed(after["review_records"]) if r["action"] == "regenerate")
        entry = {
            "skill_id": target["skill_id"],
            "name": target["name"],
            "scene": target["scene_name"],
            "comment": comment,
            "field_groups": groups,
            "outcome": result.get("outcome"),
            "message": result.get("message"),
            "status_after": status,
            "elapsed_seconds": elapsed,
            "model_calls": [{"round": c.get("round"), "attempt": c.get("attempt"), "ok": c.get("ok"),
                             "elapsed_seconds": c.get("elapsed_seconds"), "prompt_version": c.get("prompt_version"),
                             "error": c.get("error")} for c in calls],
            "versions": [(v["version_number"], v["version_kind"]) for v in after["versions"]],
            "regenerate_count": after["regenerate_count"],
            "record_state": record["detail"].get("state"),
            "diff_summary": record["detail"].get("summary"),
            "diffs": [{"path": d["path"], "op": d["op"], "before": d["before"], "after": d["after"]} for d in record["field_diffs"]],
            "evaluation_after": {"blockers": [b["code"] for b in after["evaluation"]["blockers"]],
                                 "unsupported": len(after["evaluation"]["unsupported_items"]),
                                 "hints": after["evaluation"]["hint_count"]},
        }
        if status == "pending_review" and len(after["versions"]) >= 2:
            cmp = client.get(f"/api/skill-factory/skills/{target['skill_id']}/compare", headers=headers).json()
            entry["compare_default"] = {"from": cmp["from"]["version_number"], "to": cmp["to"]["version_number"],
                                        "diff_count": len(cmp["diffs"])}
            entry["new_version_json"] = cmp["to"]["skill_json"]
        runs.append(entry)
        print(f"- {target['name']}：{entry['outcome']}，{elapsed} 秒，调用 {len(calls)} 次，版本 {entry['versions']}，"
              f"差异 {len(entry['diffs'])} 处")
    wait_for_background_tasks(timeout=60)
    with get_db() as conn:
        dump = "\n".join((r["model_calls_json"] or "") + (r["result_json"] or "")
                         for r in conn.execute("SELECT model_calls_json, result_json FROM skill_review_tasks").fetchall())
    key_found = bool(config.DEEPSEEK_API_KEY) and config.DEEPSEEK_API_KEY in dump
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "真实模型调用（非模拟）；数据为团队自编测试资料，数值与制度均为虚构；在联调库副本上执行",
        "data_dir": str(DATA_DIR),
        "model_name": config.DEEPSEEK_MODEL,
        "api_key_found_in_records": key_found,
        "runs": runs,
    }
    (OUT_DIR / "live_regenerate_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"密钥是否出现在记录中：{key_found}")
    print(f"结果：{OUT_DIR / 'live_regenerate_result.json'}")


if __name__ == "__main__":
    main()
