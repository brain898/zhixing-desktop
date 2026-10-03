"""
M02-B 真实模型联调：场景目录归并建议（PRD FR01）。

- 在临时隔离数据目录中构造少量测试原子（标签取自 PRD FR01 示例，内容为测试资料），
  不读写 server/data/zhixing.db。
- 调用真实 DeepSeek（模型名、Base URL、密钥均取自现有配置），不打印任何密钥。
- 第一步：首次整理（空目录）并按模型建议全部确认；
  第二步：再加入几条带新标签的知识，执行「整理新标签」（归入已有场景或组成新场景）。
- 结果写入 artifacts/m02b/live_merge_suggestion_result.json 与 live_organize_new_tags_result.json。

用法：python tests/run_m02b_live_merge_suggestion.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SERVER_DIR = BASE_DIR / "server"
sys.path.insert(0, str(SERVER_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TMP_DIR = Path(tempfile.mkdtemp(prefix="zhixing_m02b_live_"))
os.environ["ZHIXING_DATA_DIR"] = str(TMP_DIR)
os.environ["ZHIXING_DB_PATH"] = str(TMP_DIR / "zhixing.db")
os.environ["ZHIXING_STORAGE_DIR"] = str(TMP_DIR / "storage")
os.environ["ZHIXING_DEMO_ADMIN_PASSWORD"] = "Admin@Zhixing2026"
os.environ["ZHIXING_DEMO_MEMBER_PASSWORD"] = "Member@Zhixing2026"
os.environ["ZHIXING_DEMO_OTHER_PASSWORD"] = "Other@Zhixing2026"

import config  # noqa: E402
from database import get_db, init_db  # noqa: E402
from seed import seed_data  # noqa: E402
import scene_catalog as sc  # noqa: E402

OUT_DIR = BASE_DIR / "artifacts" / "m02b"

# (原子标题, 业务场景标签, 主分类) —— 测试资料，标签取自 PRD FR01 归并示例
ATOMS = [
    ("新建项目承接查验资料核对", ["承接查验"], "制度与标准"),
    ("公共部位工程查验要点", ["工程查验"], "方法与工具"),
    ("排污泵出口止回装置检查", ["防汛管理", "工程查验"], "方法与工具"),
    ("汛前排水设施检查", ["防汛管理"], "制度与标准"),
    ("供水管道爆裂应急抢修", ["应急抢修"], "方法与工具"),
    ("配电室停电应急抢险", ["应急抢险"], "制度与标准"),
    ("业主报修响应时限", ["报修响应"], "指标数据"),
    ("室内跑水急修处置", ["急修处置"], "方法与工具"),
    ("入户维修工具与防护", ["入户维修"], "方法与工具"),
    ("高空抛物监控摄像覆盖", ["高空抛物监控", "智能安防"], "制度与标准"),
    ("监控告警分级处置", ["告警处置"], "方法与工具"),
    ("夜间安防巡检路线", ["安防巡检"], "方法与工具"),
]

# 目录建立后新进入的知识（测试资料）
NEW_ATOMS = [
    ("排水管道定期疏通", ["排水设施巡查"], "方法与工具"),
    ("消防设施月度检查", ["消防设施检查"], "制度与标准"),
    ("消防演练组织要求", ["消防演练"], "方法与工具"),
    ("电梯困人应急救援", ["电梯困人处置"], "方法与工具"),
]


def add_atoms(org_id, doc_id, ver_id, atoms):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        for title, scenes, category in atoms:
            item_id = f"ki_live_{uuid.uuid4().hex[:6]}"
            version_id = f"kv_live_{uuid.uuid4().hex[:6]}"
            conn.execute(
                """INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope,
                   lifecycle_status, is_excluded, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'org_internal', 'active', 0, ?, ?)""",
                (item_id, doc_id, org_id, version_id, now, now),
            )
            # 联调只需要标签统计：直接构造「已确认 + 索引可用」的测试原子，不跑向量索引
            conn.execute(
                """INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id,
                   version_number, title, content, primary_category, atom_type, statement, business_scenes_json,
                   review_status, index_status, revision_token, created_at, created_by)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, '规则', ?, ?, 'confirmed', 'ready', ?, ?, 'usr_admin_001')""",
                (version_id, item_id, org_id, ver_id, title, title, category, title,
                 json.dumps(scenes, ensure_ascii=False), uuid.uuid4().hex, now),
            )


def build_fixture():
    init_db()
    seed_data(force=True)
    now = datetime.now(timezone.utc).isoformat()
    org_id = "org_greentown"
    doc_id = f"doc_live_{uuid.uuid4().hex[:6]}"
    ver_id = f"ver_live_{uuid.uuid4().hex[:6]}"
    with get_db() as conn:
        conn.execute(
            """INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted,
               created_at, updated_at) VALUES (?, ?, 'M02-B 联调测试资料', ?, 'org_internal', 0, ?, ?)""",
            (doc_id, org_id, ver_id, now, now),
        )
        conn.execute(
            """INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name, file_type,
               file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
               VALUES (?, ?, ?, 'v1', 'm02b_live.txt', 'txt', 100, ?, 'm02b_live.txt', 'usr_admin_001', ?, 'completed')""",
            (ver_id, doc_id, org_id, uuid.uuid4().hex, now),
        )
    add_atoms(org_id, doc_id, ver_id, ATOMS)
    return org_id, doc_id, ver_id


def atom_snapshot():
    with get_db() as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT id, version_number, business_scenes_json, revision_token FROM knowledge_versions ORDER BY id"
        ).fetchall()]


def run_phase(admin, api_key, out_name):
    """发起一次整理并真实调用模型，返回报告。"""
    org_id = admin["organization_id"]
    before = atom_snapshot()
    with get_db() as conn:
        scenes_before = [tuple(r) for r in conn.execute(
            "SELECT id, name, description, aliases_json FROM scenes ORDER BY id").fetchall()]
        suggestion = sc.create_merge_suggestion(conn, admin)
    started = time.time()
    sc.run_merge_suggestion(suggestion["suggestion_id"], api_key=api_key)
    elapsed = round(time.time() - started, 2)
    with get_db() as conn:
        result = sc.get_suggestion(conn, org_id, suggestion["suggestion_id"])
        scenes_after = [tuple(r) for r in conn.execute(
            "SELECT id, name, description, aliases_json FROM scenes ORDER BY id").fetchall()]
    report = {
        "kind": "real_model_call",
        "mode": result["mode"],
        "run_at": datetime.now(timezone.utc).isoformat(),
        "model_name": result["model_name"],
        "prompt_version": result["prompt_version"],
        "status": result["status"],
        "attempt_count": result["attempt_count"],
        "attempts": result["attempts"],
        "elapsed_seconds": elapsed,
        "input_tag_count": len(result["tag_stats"]),
        "input_tags": [{"tag": t["tag"], "count": t["count"]} for t in result["tag_stats"]],
        "groups": result["groups"],
        "unassigned_tags": result["unassigned_tags"],
        "error_message": result["error_message"],
        "atoms_unchanged": before == atom_snapshot(),
        "scenes_unchanged_before_confirm": scenes_before == scenes_after,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / out_name
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{report['mode']}] status={report['status']} model={report['model_name']} "
          f"prompt={report['prompt_version']} attempts={report['attempt_count']} elapsed={elapsed}s")
    for a in report["attempts"]:
        print(f"  attempt {a['attempt']}: ok={a['ok']} errors={a.get('errors')}")
    for g in report["groups"]:
        kind = "归入已有" if g.get("existing") else "新场景"
        print(f"  [{kind}] {g['name']} tags={g['tags']} | {g['description']} | 典型问题={g['typical_problems']}")
        print(f"      理由：{g['reason']}")
    print(f"  unassigned={report['unassigned_tags']}")
    print(f"  atoms_unchanged={report['atoms_unchanged']} "
          f"scenes_unchanged_before_confirm={report['scenes_unchanged_before_confirm']}")
    print(f"  saved: {out.relative_to(BASE_DIR)}")
    return report


def main():
    api_key = os.getenv("DEEPSEEK_API_KEY") or config.DEEPSEEK_API_KEY
    if not api_key:
        print("DEEPSEEK_API_KEY 未配置，无法进行真实调用")
        return 2
    try:
        org_id, doc_id, ver_id = build_fixture()
        admin = {"id": "usr_admin_001", "organization_id": org_id, "role": "admin", "account_status": "active"}

        first = run_phase(admin, api_key, "live_merge_suggestion_result.json")
        if first["status"] != "completed":
            return 1
        # 按模型建议原样确认，建立场景目录
        with get_db() as conn:
            sid = sc.get_latest_suggestion(conn, org_id)["suggestion_id"]
            sc.confirm_merge_suggestion(conn, org_id, admin["id"], sid, first["groups"])
            print(f"  confirmed scenes: {[s['name'] for s in sc.list_scenes(conn, org_id)]}")

        # 目录建立后，新进入带新标签的知识
        add_atoms(org_id, doc_id, ver_id, NEW_ATOMS)
        second = run_phase(admin, api_key, "live_organize_new_tags_result.json")
        return 0 if second["status"] == "completed" else 1
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
