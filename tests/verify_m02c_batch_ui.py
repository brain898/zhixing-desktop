"""
M02-C UI 验证：场景卡片生成按钮、生成侧重说明、生成记录（批次详情）与最小候选列表（PRD 10.1 批次详情、FR04~FR08）。

- 后端：在本进程内以隔离临时数据库启动真实 FastAPI 服务（端口 8798），不读写 server/data/zhixing.db。
- 前端：直接加载 client/dist 生产构建；页面对 http://127.0.0.1:8766/api 的请求由 Playwright 转发到 8798。
- 模型返回为「模拟返回」（patch skill_generation.post_chat_completion），只用于界面验证；
  其中一个任务故意引用范围外的知识，用来展示「校验未通过」；真实调用见 tests/run_m02c_live_generation.py。
- 截图保存到 screenshots/m02c_*.png。

用法：python tests/verify_m02c_batch_ui.py（需先 npm run build）
"""

import json
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import requests
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_env_helper import cleanup_test_db, setup_test_db  # noqa: E402

TEST_DB = setup_test_db("m02c_ui")

import uvicorn  # noqa: E402
import config  # noqa: E402
import hybrid_retrieval  # noqa: E402
import scene_catalog as sc  # noqa: E402
import skill_generation as sg  # noqa: E402
from database import get_db  # noqa: E402
from main import app  # noqa: E402

CLIENT_DIST = BASE_DIR / "client" / "dist"
SHOTS = BASE_DIR / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
API_PORT = 8798
STATIC_PORT = 5178
API = f"http://127.0.0.1:{API_PORT}/api"
ORG = "org_greentown"
PREFIX = "m02cui_" + uuid.uuid4().hex[:6]
FAKE_KEY = "sk-ui-fake-key"
ATOMS = {}
RELEASE = threading.Event()


class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIST), **kwargs)

    def log_message(self, format, *args):
        pass


def start_servers():
    static = ThreadingHTTPServer(("127.0.0.1", STATIC_PORT), StaticHandler)
    threading.Thread(target=static.serve_forever, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=API_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if requests.get(f"{API}/system/status", timeout=1).status_code == 200:
                return static, server
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise RuntimeError("测试后端未启动")


def login(username, password):
    resp = requests.post(f"{API}/auth/login", json={"username": username, "password": password}, timeout=10)
    resp.raise_for_status()
    return resp.json()["token"]


def build_fixture():
    """已确认、索引可用的测试知识（界面验证不建向量），内容为自编测试资料。"""
    now = datetime.now(timezone.utc).isoformat()
    doc_id, ver_id = f"doc_{PREFIX}", f"ver_{PREFIX}"
    specs = [
        ("rate", "收缴率计算口径", "收缴率等于当期实收除以当期应收，按月统计。", ["收缴管理"], "指标数据", "指标",
         ["当期应收为零时不计算收缴率"]),
        ("attr", "实收归属条件", "实收金额只计入与当期应收对应的款项。", ["收缴管理"], "制度与标准", "规则", []),
        ("grade", "欠费分级标准", "欠费超过3个月的业主列为重点欠费户。", ["收缴管理"], "制度与标准", "规则",
         ["存在物业服务争议的欠费另行处理"]),
        ("remind", "催缴方法", "催缴先书面通知，再上门沟通。", ["收缴管理"], "方法与工具", "方法", []),
        ("case", "分级催缴案例", "某项目通过分级催缴提升了收缴率。", ["收缴管理"], "项目案例", "案例", []),
        ("repair", "报修响应要求", "业主报修后应及时派单。", ["报修响应"], "制度与标准", "规则", []),
        ("green", "草坪修剪频次", "草坪生长季每月修剪两次。", ["绿化养护"], "方法与工具", "方法", []),
    ]
    with get_db() as conn:
        conn.execute(
            """INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted,
               created_at, updated_at) VALUES (?, ?, 'M02-C 界面验证资料', ?, 'org_internal', 0, ?, ?)""",
            (doc_id, ORG, ver_id, now, now),
        )
        conn.execute(
            """INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name, file_type,
               file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
               VALUES (?, ?, ?, 'v1', 'm02c_ui.txt', 'txt', 100, ?, 'm02c_ui.txt', 'usr_admin_001', ?, 'completed')""",
            (ver_id, doc_id, ORG, uuid.uuid4().hex, now),
        )
        for key, title, statement, scenes, cat, atom_type, exceptions in specs:
            item_id, version_id = f"ki_{PREFIX}_{key}", f"kv_{PREFIX}_{key}"
            conn.execute(
                """INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope,
                   lifecycle_status, is_excluded, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'org_internal', 'active', 0, ?, ?)""",
                (item_id, doc_id, ORG, version_id, now, now),
            )
            conn.execute(
                """INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id,
                   version_number, title, content, primary_category, atom_type, subject, statement, conditions_json,
                   actions_json, exceptions_json, customer_types_json, business_scenes_json, review_status,
                   index_status, revision_token, created_at, created_by)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, '物业管理处', ?, '[]', ?, ?, '["住宅业主"]', ?, 'confirmed', 'ready',
                           ?, ?, 'usr_admin_001')""",
                (version_id, item_id, ORG, ver_id, title, statement, cat, atom_type, statement,
                 json.dumps([statement], ensure_ascii=False), json.dumps(exceptions, ensure_ascii=False),
                 json.dumps(scenes, ensure_ascii=False), uuid.uuid4().hex, now),
            )
            ATOMS[key] = {"item_id": item_id, "version_id": version_id, "title": title}
        sc.create_scene(conn, ORG, "usr_admin_001", {"name": "物业费收缴管理", "description": "收缴率核对与欠费催缴",
                                                     "aliases": ["收缴管理"], "typical_problems": ["收缴率怎么算"]})
        sc.create_scene(conn, ORG, "usr_admin_001", {"name": "绿化养护", "description": "小区绿化修剪与养护",
                                                     "aliases": ["绿化养护"]})


def fake_search(conn, user, query, now_iso=None, **kwargs):
    if "收缴" not in query:
        return []
    a = ATOMS["repair"]
    return [{"item_id": a["item_id"], "version_id": a["version_id"], "title": a["title"],
             "primary_category": "制度与标准", "atom_type": "规则", "statement": "业主报修后应及时派单。",
             "business_scenes": ["报修响应"], "relevance_score": 0.6}]


def candidate(payload, outsider=False):
    atoms = payload["atoms"]
    steps = [{"step_id": "s1", "kind": "输入校验", "action": "检查统计期间与应收实收数据是否齐全", "refs": [],
              "basis": "通用操作", "on_fail": "补问"}]
    refs = []
    for i, a in enumerate(atoms, start=2):
        steps.append({"step_id": f"s{i}", "kind": "规则判断", "action": a["statement"], "refs": [a["atom_version_id"]],
                      "basis": "有原子依据", "on_fail": "转人工"})
        refs.append({"atom_item_id": a["atom_item_id"], "atom_version_id": a["atom_version_id"],
                     "role": (a.get("default_roles") or ["判断规则"])[0], "used_in_steps": [f"s{i}"]})
    steps.append({"step_id": f"s{len(atoms) + 2}", "kind": "生成表达", "action": "3 个工作日内反馈核对结果",
                  "refs": [], "basis": "无依据", "on_fail": "暂停"})
    if outsider:
        g = ATOMS["green"]
        refs.append({"atom_item_id": g["item_id"], "atom_version_id": g["version_id"], "role": "执行动作",
                     "used_in_steps": ["s2"]})
        steps[1]["refs"].append(g["version_id"])
    return {
        "schema_version": "1.0", "name": payload["task"]["name"][:20], "goal": payload["task"]["goal"],
        "trigger_description": "用户提出：" + payload["task"]["name"], "task_type": payload["task"]["task_type"],
        "scene_id": "x", "applies_to": {"customer_types": ["住宅业主"], "property_types": ["住宅"], "conditions": []},
        "not_applies_to": ["收缴率下降原因分析属于另一任务"], "knowledge_refs": refs,
        "inputs": [{"key": "period", "label": "统计期间", "type": "period", "required": True}],
        "preconditions": [], "outputs": [{"key": "result", "label": "核对结果", "type": "text", "required": True}],
        "steps": steps, "risk_boundary": ["TBD_EXPERT"],
        "escalation_conditions": ["存在物业服务争议的欠费另行处理"],
        "generation_confidence": {"level": "中", "reason": "反馈时限在所引知识中没有出现"},
    }


def fake_model(messages, api_key=None, **kwargs):
    payload = json.loads(messages[1]["content"].split("\n", 1)[1])
    if "atom_pool" in payload:
        ids = {a["title"]: a["atom_version_id"] for a in payload["atom_pool"]}
        RELEASE.wait(20)  # 停在拆分阶段，便于截取进行中的进度
        return json.dumps({
            "tasks": [
                {"name": "核对指定期间收缴率", "goal": "按口径核对某一期间的收缴率，给出核对结果", "task_type": "计算核对",
                 "atom_version_ids": [ids["收缴率计算口径"], ids["实收归属条件"], ids["分级催缴案例"]],
                 "split_reason": "收缴率公式与实收归属条件共同决定核对口径"},
                {"name": "欠费分级与催缴建议", "goal": "判断欠费等级并给出催缴措施", "task_type": "判断分级",
                 "atom_version_ids": [ids["欠费分级标准"], ids["催缴方法"], ids["实收归属条件"]],
                 "split_reason": "欠费分级标准决定采用哪种催缴方法"},
                {"name": "报修派单", "goal": "报修后派单", "task_type": "流程指引",
                 "atom_version_ids": [ids["报修响应要求"]], "split_reason": "报修相关知识"},
            ],
            "unused_atoms": [{"atom_version_id": ids["报修响应要求"], "reason": "属于报修场景，与收缴无关"}],
        }, ensure_ascii=False), "deepseek-mock"
    outsider = payload["task"]["name"] == "欠费分级与催缴建议"
    return json.dumps(candidate(payload, outsider=outsider), ensure_ascii=False), "deepseek-mock"


def main():
    patches = [patch.object(config, "DEEPSEEK_API_KEY", FAKE_KEY), patch.object(sg, "post_chat_completion", fake_model),
               patch.object(hybrid_retrieval, "hybrid_search", fake_search)]
    for p in patches:
        p.start()
    static, server = start_servers()
    # 服务启动时会为缺少向量的已确认知识补建索引，因此构造数据放在启动之后（同 M02-B 界面脚本）
    build_fixture()
    admin_token = login("admin", "Admin@Zhixing2026")
    results = []

    def check(label, cond):
        results.append((label, bool(cond)))
        print(("  [通过] " if cond else "  [失败] ") + label)
        return cond

    jargon = ("原子", "召回", "归并", "别名", "语义", "模型", "DeepSeek", "提示词", "相关度", "mock")

    def check_plain(label, text):
        found = [w for w in jargon if w in text]
        return check(f"{label}：页面没有技术术语{('（发现 ' + '、'.join(found) + '）') if found else ''}", not found)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})

            def forward(route):
                url = route.request.url.replace("http://127.0.0.1:8766/api", API)
                resp = route.fetch(url=url)
                route.fulfill(response=resp)

            context.route("http://127.0.0.1:8766/api/**", forward)
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{STATIC_PORT}")
            page.evaluate("(t) => localStorage.setItem('zhixing_token', t)", admin_token)
            page.reload()
            page.wait_for_selector('button[aria-label="Skill 工厂"]', timeout=15000)
            page.click('button[aria-label="Skill 工厂"]')
            page.wait_for_selector('[data-testid="scene-card"]', timeout=15000)

            fee = page.locator('[data-testid="scene-card"][data-scene-name="物业费收缴管理"]')
            green = page.locator('[data-testid="scene-card"][data-scene-name="绿化养护"]')
            check("知识足够时生成按钮可用", fee.locator('[data-testid="scene-generate"]').is_enabled())
            check("AC04 知识不足时生成按钮不可用", green.locator('[data-testid="scene-generate"]').is_disabled())
            check("AC04 知识不足时提示原因", "不足 3 条" in green.locator('[data-testid="scene-generate-hint"]').inner_text())

            # G1：生成侧重说明
            fee.locator('[data-testid="scene-generate"]').click()
            page.wait_for_selector('[data-testid="generate-dialog"]', timeout=5000)
            page.fill('[data-testid="generate-focus"]', "侧重收缴率核对")
            check_plain("发起生成对话框", page.locator('[data-testid="generate-dialog"]').inner_text())
            page.screenshot(path=str(SHOTS / "m02c_01_generate_dialog_1440x900.png"))
            page.click('[data-testid="generate-confirm"]')

            # 进行中：四段进度
            page.wait_for_selector('[data-testid="batch-detail"]', timeout=10000)
            page.wait_for_selector('[data-testid="stage-split"][data-status="running"]', timeout=10000)
            check("进入生成记录页并显示四段进度", page.locator('[data-testid="batch-stages"] > div').count() == 4)
            check("查找知识已完成、拆分任务进行中",
                  page.locator('[data-testid="stage-recall"]').get_attribute("data-status") == "done")
            check("进行中显示本次找到的知识", page.locator('[data-testid="pool-atom"]').count() == 6)
            page.screenshot(path=str(SHOTS / "m02c_02_batch_running_1440x900.png"))

            # AC07：同一场景进行中时再次发起被拒绝（接口）
            with get_db() as conn:
                scene_id = next(s["scene_id"] for s in sc.list_scenes(conn, ORG) if s["name"] == "物业费收缴管理")
            again = requests.post(f"{API}/skill-factory/scenes/{scene_id}/batches", json={},
                                  headers={"Authorization": f"Bearer {admin_token}"}, timeout=10)
            check("AC07 进行中再次发起返回 409", again.status_code == 409)

            RELEASE.set()
            page.wait_for_selector('[data-testid="batch-status"]:has-text("部分完成")', timeout=30000)
            stages = [page.locator(f'[data-testid="stage-{k}"]').get_attribute("data-status")
                      for k in ("recall", "split", "generate", "validate")]
            check("四段进度全部完成", stages == ["done", "done", "done", "done"])
            rows = page.locator('[data-testid="candidate-row"]')
            check("候选列表 2 条（1 条待审核、1 条校验未通过）",
                  rows.count() == 2
                  and page.locator('[data-testid="candidate-row"][data-status="pending_review"]').count() == 1
                  and page.locator('[data-testid="candidate-row"][data-status="validation_failed"]').count() == 1)
            ok_row = page.locator('[data-testid="candidate-row"][data-status="pending_review"]')
            cells = ok_row.locator("td").all_inner_texts()
            check("候选行显示名称、状态、把握度、无依据项数、引用知识数",
                  "核对指定期间收缴率" in cells[0] and cells[1] == "待审核" and cells[2] == "中"
                  and cells[3] == "3" and cells[4] == "3")
            check("拆分结果列出 3 个任务", page.locator('[data-testid="split-task"]').count() == 3)
            check("知识不足的任务标为未进入生成",
                  page.locator('[data-testid="split-task"][data-status="skipped"]').count() == 1)
            check("拆分结果列出没有用上的知识", page.locator('[data-testid="unused-atoms"]').count() == 1)
            check("本次找到的知识标注来源",
                  "1 条内容相关" in page.locator('[data-testid="pool-summary"]').inner_text()
                  and page.locator('[data-testid="pool-atom"] .zx-tag.warning:has-text("内容相关")').count() == 1)
            check_plain("生成记录页", page.locator('[data-testid="batch-detail"]').inner_text())
            page.screenshot(path=str(SHOTS / "m02c_03_batch_detail_done_1440x900.png"), full_page=True)

            # 最小候选列表：完整 JSON（只读）
            ok_row.locator('[data-testid="candidate-view"]').click()
            page.wait_for_selector('[data-testid="candidate-json"]', timeout=10000)
            text = page.locator('[data-testid="candidate-json"]').inner_text()
            parsed = json.loads(text)
            check("查看内容显示完整 JSON", parsed.get("schema_version") == "1.0" and parsed.get("status") == "待审核"
                  and len(parsed.get("unsupported_items") or []) == 3)
            check("JSON 只读展示（非输入框）", page.locator('[data-testid="candidate-json-modal"] textarea, '
                                                       '[data-testid="candidate-json-modal"] input').count() == 0)
            page.screenshot(path=str(SHOTS / "m02c_04_candidate_json_1440x900.png"))
            page.click('[data-testid="candidate-json-modal"] button[aria-label="关闭"]')

            bad_row = page.locator('[data-testid="candidate-row"][data-status="validation_failed"]')
            check("校验未通过的候选在列表中显示原因", "引用了本次范围以外的知识" in bad_row.inner_text())
            bad_row.locator('[data-testid="candidate-view"]').click()
            page.wait_for_selector('[data-testid="candidate-failed-issues"]', timeout=10000)
            check("校验未通过的候选说明修正次数与问题",
                  "已自动修正 1 次" in page.locator('[data-testid="candidate-failed-issues"]').inner_text())
            check_plain("校验未通过说明", page.locator('[data-testid="candidate-failed-issues"]').inner_text())
            page.screenshot(path=str(SHOTS / "m02c_05_failed_candidate_1440x900.png"))
            page.click('[data-testid="candidate-json-modal"] button[aria-label="关闭"]')

            # 返回场景：卡片显示已有 Skill 与上次记录
            page.click('[data-testid="batch-back"]')
            page.wait_for_selector('[data-testid="scene-card"]', timeout=10000)
            fee = page.locator('[data-testid="scene-card"][data-scene-name="物业费收缴管理"]')
            check("卡片已有 Skill 显示待审核 1", "待审核 1" in fee.locator('[data-testid="scene-skills"]').inner_text())
            check("卡片显示上次生成结果", "上次生成：部分完成" in fee.locator('[data-testid="scene-generate-hint"]').inner_text())
            check_plain("场景卡片页", page.locator("main").inner_text())
            page.screenshot(path=str(SHOTS / "m02c_06_scene_cards_after_batch_1440x900.png"))
            fee.locator('[data-testid="scene-latest-batch"]').click()
            page.wait_for_selector('[data-testid="batch-detail"]', timeout=10000)
            check("「上次记录」可重新打开生成记录", page.locator('[data-testid="candidate-row"]').count() == 2)
            browser.close()
    finally:
        RELEASE.set()
        server.should_exit = True
        static.shutdown()
        time.sleep(0.5)
        for p in patches:
            p.stop()
        cleanup_test_db(TEST_DB)

    failed = [label for label, ok in results if not ok]
    print(f"\n结果：{len(results) - len(failed)}/{len(results)} 项通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
