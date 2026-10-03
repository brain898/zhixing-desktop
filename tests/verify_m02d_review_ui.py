"""
M02-D UI 验证：候选列表筛选、审核工作台双栏与联动、通过门槛提示、修改后通过产生新版本、
差异对照高亮与导出、退回重生成、驳回与恢复、校验未通过候选、并发冲突提示（PRD FR09~FR13、AC11~AC15、AC19）。

- 后端：在本进程内以隔离临时数据库启动真实 FastAPI 服务（端口 8799），不读写 server/data/zhixing.db。
- 前端：直接加载 client/dist 生产构建；页面对 http://127.0.0.1:8766/api 的请求由 Playwright 转发到 8799。
- 模型返回为「模拟返回」（patch skill_generation.post_chat_completion），只用于界面验证，不代表真实模型效果。
- 截图保存到 screenshots/m02d_*.png（1440×900）。

用法：python tests/verify_m02d_review_ui.py（需先 npm run build）
"""

import json
import re
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

TEST_DB = setup_test_db("m02d_ui")

import uvicorn  # noqa: E402
import config  # noqa: E402
import hybrid_retrieval  # noqa: E402
import scene_catalog as sc  # noqa: E402
import skill_generation as sg  # noqa: E402
from database import get_db  # noqa: E402
import main as main_module  # noqa: E402
from main import app  # noqa: E402

CLIENT_DIST = BASE_DIR / "client" / "dist"
SHOTS = BASE_DIR / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
DOWNLOADS = BASE_DIR / "artifacts" / "m02d"
DOWNLOADS.mkdir(parents=True, exist_ok=True)
API_PORT = 8799
STATIC_PORT = 5179
API = f"http://127.0.0.1:{API_PORT}/api"
ORG = "org_greentown"
PREFIX = "m02dui_" + uuid.uuid4().hex[:6]
FAKE_KEY = "sk-ui-fake-key"
ATOMS = {}
RELEASE = threading.Event()
RELEASE.set()
TASK_A = "核对指定期间收缴率"
TASK_B = "欠费分级与催缴建议"
VARIANTS = {TASK_A: {"tbd": True, "unsupported": True, "confidence": "中"},
            TASK_B: {"tbd": False, "unsupported": False, "confidence": "高"}}


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
    """已确认、索引可用的测试知识（自编测试资料），其中一条带原文证据。"""
    now = datetime.now(timezone.utc).isoformat()
    doc_id, ver_id = f"doc_{PREFIX}", f"ver_{PREFIX}"
    specs = [
        ("rate", "收缴率计算口径", "收缴率等于当期实收除以当期应收，按月统计。", ["收缴管理"], "指标数据", "指标",
         ["当期应收为零时不计算收缴率"]),
        ("attr", "实收归属条件", "实收金额只计入与当期应收对应的款项。", ["收缴管理"], "制度与标准", "规则", []),
        ("grade", "欠费分级标准", "欠费超过3个月的业主列为重点欠费户。", ["收缴管理"], "制度与标准", "规则",
         ["存在物业服务争议的欠费另行处理"]),
        ("remind", "催缴方法", "催缴先书面通知，再上门沟通。", ["收缴管理"], "方法与工具", "方法", []),
        ("repair", "报修响应要求", "业主报修后应及时派单。", ["报修响应"], "制度与标准", "规则", []),
        ("iso", "绿化修剪频次", "草坪生长季每月修剪两次。", ["绿化养护"], "方法与工具", "方法", []),
    ]
    with get_db() as conn:
        conn.execute(
            """INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted,
               created_at, updated_at) VALUES (?, ?, '物业费收缴管理办法（自编测试资料）', ?, 'org_internal', 0, ?, ?)""",
            (doc_id, ORG, ver_id, now, now),
        )
        conn.execute(
            """INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name, file_type,
               file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
               VALUES (?, ?, ?, 'v1', 'm02d_ui.txt', 'txt', 100, ?, 'm02d_ui.txt', 'usr_admin_001', ?, 'completed')""",
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
        block_id = f"sb_{PREFIX}"
        conn.execute(
            """INSERT INTO source_blocks (id, document_version_id, organization_id, block_index, block_type, heading_path,
               paragraph_anchor, text_content, created_at) VALUES (?, ?, ?, 3, 'paragraph', '第2章 收缴率口径', 'p4', ?, ?)""",
            (block_id, ver_id, ORG, "2.1 当期收缴率 = 统计期内实收的当期物业费 ÷ 统计期内当期应收物业费。收缴率等于当期实收除以当期应收，按月统计。当期应收为零时不计算收缴率。", now),
        )
        conn.execute(
            """INSERT INTO knowledge_evidence (id, knowledge_version_id, source_block_id, organization_id, field_name,
               excerpt, accuracy_level, created_at) VALUES (?, ?, ?, ?, 'statement', ?, 'exact', ?)""",
            (f"ke_{PREFIX}", ATOMS["rate"]["version_id"], block_id, ORG, "收缴率等于当期实收除以当期应收，按月统计。", now),
        )
        sc.create_scene(conn, ORG, "usr_admin_001", {"name": "物业费收缴管理", "description": "收缴率核对与欠费催缴",
                                                     "aliases": ["收缴管理"], "typical_problems": ["收缴率怎么算"]})


def fake_search(conn, user, query, now_iso=None, **kwargs):
    if "收缴" not in query:
        return []
    a = ATOMS["repair"]
    return [{"item_id": a["item_id"], "version_id": a["version_id"], "title": a["title"],
             "primary_category": "制度与标准", "atom_type": "规则", "statement": "业主报修后应及时派单。",
             "business_scenes": ["报修响应"], "relevance_score": 0.6}]


def fake_formal_search(conn=None, current_user=None, query="", now_iso=None, **kwargs):
    """管理员正式检索（/api/knowledge/search）的模拟返回，只用于界面验证「在全部知识中查找」。"""
    return [{**hit, "atom_type": "规则", "version_number": 1} for hit in fake_search(conn, current_user, query)]


def candidate(payload, variant):
    atoms = payload["atoms"]
    # 与真实返回一致：通用操作步骤不写 refs（Schema 允许省略；曾导致审核页白屏）
    steps = [{"step_id": "s1", "kind": "输入校验", "action": "检查统计期间与台账是否齐全",
              "basis": "通用操作", "on_fail": "补问"}]
    refs = []
    for i, a in enumerate(atoms, start=2):
        steps.append({"step_id": f"s{i}", "kind": "规则判断", "action": a["statement"], "refs": [a["atom_version_id"]],
                      "basis": "有原子依据", "on_fail": "转人工"})
        refs.append({"atom_item_id": a["atom_item_id"], "atom_version_id": a["atom_version_id"],
                     "role": (a.get("default_roles") or ["判断规则"])[0], "used_in_steps": [f"s{i}"]})
    if variant.get("unsupported"):
        steps.append({"step_id": f"s{len(atoms) + 2}", "kind": "生成表达", "action": "30 分钟内回访确认处置结果",
                      "refs": [], "basis": "无依据", "on_fail": "暂停"})
    if variant.get("outsider"):
        g = ATOMS["iso"]
        refs.append({"atom_item_id": g["item_id"], "atom_version_id": g["version_id"], "role": "执行动作",
                     "used_in_steps": ["s2"]})
        steps[1]["refs"].append(g["version_id"])
    exceptions = [e for a in atoms for e in a.get("exceptions") or []]
    tbd = variant.get("tbd")
    return {
        "schema_version": "1.0", "name": payload["task"]["name"][:20], "goal": payload["task"]["goal"],
        "trigger_description": "用户提出：" + payload["task"]["name"], "task_type": payload["task"]["task_type"],
        "scene_id": "x", "applies_to": {"customer_types": ["住宅业主"], "property_types": ["住宅"], "conditions": []},
        "not_applies_to": ["TBD_EXPERT"] if tbd else ["收缴率下降原因分析属于另一任务"], "knowledge_refs": refs,
        "inputs": [{"key": "period", "label": "统计期间", "type": "period", "required": True},
                   {"key": "amount", "label": "当期应收", "type": "number", "unit": "元", "required": True}],
        "preconditions": [], "outputs": [{"key": "result", "label": "核对结果", "type": "text", "required": True}],
        "steps": steps, "risk_boundary": ["TBD_EXPERT"] if tbd else ["不替代财务人员的最终核算"],
        "escalation_conditions": exceptions or ["无法判断时转人工"],
        "generation_confidence": {"level": variant.get("confidence", "中"), "reason": "所引知识覆盖了口径与方法，回访时限在知识中没有出现"},
    }


def fake_model(messages, api_key=None, **kwargs):
    payload = json.loads(messages[1]["content"].split("\n", 1)[1])
    if "atom_pool" in payload:
        ids = {a["title"]: a["atom_version_id"] for a in payload["atom_pool"]}
        return json.dumps({
            "tasks": [
                {"name": TASK_A, "goal": "按口径核对某一期间的收缴率，给出核对结果", "task_type": "计算核对",
                 "atom_version_ids": [ids["收缴率计算口径"], ids["实收归属条件"], ids["催缴方法"]],
                 "split_reason": "收缴率公式与实收归属条件共同决定核对口径"},
                {"name": TASK_B, "goal": "判断欠费等级并给出催缴措施", "task_type": "判断分级",
                 "atom_version_ids": [ids["欠费分级标准"], ids["催缴方法"], ids["报修响应要求"]],
                 "split_reason": "欠费分级标准决定采用哪种催缴方法"},
            ],
            "unused_atoms": [],
        }, ensure_ascii=False), "deepseek-mock"
    if "review_comment" in payload:
        RELEASE.wait(30)  # 停在生成中，便于截取进行中的状态
        c = candidate(payload, {"confidence": "中"})
        c["not_applies_to"] = ["收缴率下降原因分析属于另一任务", "非住宅项目的欠费处理"]
        return json.dumps(c, ensure_ascii=False), "deepseek-mock"
    return json.dumps(candidate(payload, VARIANTS[payload["task"]["name"]]), ensure_ascii=False), "deepseek-mock"


def run_batch(token, scene_id):
    resp = requests.post(f"{API}/skill-factory/scenes/{scene_id}/batches", json={},
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    batch_id = resp.json()["batch_id"]
    for _ in range(300):
        detail = requests.get(f"{API}/skill-factory/batches/{batch_id}", headers={"Authorization": f"Bearer {token}"},
                              timeout=10).json()
        if detail["status"] != "running":
            return detail
        time.sleep(0.1)
    raise RuntimeError("批次超时")


def main():
    patches = [patch.object(config, "DEEPSEEK_API_KEY", FAKE_KEY), patch.object(sg, "post_chat_completion", fake_model),
               patch.object(hybrid_retrieval, "hybrid_search", fake_search),
               patch.object(main_module, "hybrid_search", fake_formal_search)]
    for p in patches:
        p.start()
    static, server = start_servers()
    build_fixture()
    admin_token = login("admin", "Admin@Zhixing2026")
    other_session = login("admin", "Admin@Zhixing2026")
    with get_db() as conn:
        scene_id = sc.list_scenes(conn, ORG)[0]["scene_id"]
    batch1 = run_batch(admin_token, scene_id)
    VARIANTS[TASK_A] = {"outsider": True, "confidence": "中"}
    VARIANTS[TASK_B] = {"tbd": False, "unsupported": False, "confidence": "低"}
    batch2 = run_batch(admin_token, scene_id)
    skill_a = next(c["skill_id"] for c in batch1["candidates"] if c["name"] == TASK_A)
    skill_b1 = next(c["skill_id"] for c in batch1["candidates"] if c["name"] == TASK_B)
    skill_b2 = next(c["skill_id"] for c in batch2["candidates"] if c["name"] == TASK_B)
    skill_failed = next(c["skill_id"] for c in batch2["candidates"] if c["status"] == "validation_failed")
    results = []

    def check(label, cond):
        results.append((label, bool(cond)))
        print(("  [通过] " if cond else "  [失败] ") + label)
        return cond

    jargon = ("原子", "召回", "归并", "别名", "语义", "模型", "DeepSeek", "提示词", "相关度", "mock", "TBD_EXPERT")

    def check_plain(label, text):
        found = [w for w in jargon if w in text]
        return check(f"{label}：页面没有技术术语{('（发现 ' + '、'.join(found) + '）') if found else ''}", not found)

    def api_get(path):
        return requests.get(f"{API}{path}", headers={"Authorization": f"Bearer {admin_token}"}, timeout=10).json()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)

            def forward(route):
                url = route.request.url.replace("http://127.0.0.1:8766/api", API)
                resp = route.fetch(url=url)
                route.fulfill(response=resp)

            context.route("http://127.0.0.1:8766/api/**", forward)
            page = context.new_page()

            def small_fonts(selector):
                return page.evaluate("""(sel) => {
                    const root = document.querySelector(sel); if (!root) return ['root missing'];
                    const bad = [];
                    root.querySelectorAll('*').forEach((el) => {
                      if (!el.childNodes.length) return;
                      const hasText = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim());
                      if (!hasText) return;
                      const size = parseFloat(getComputedStyle(el).fontSize);
                      if (size < 12) bad.push(el.tagName + ':' + size + ':' + el.textContent.trim().slice(0, 12));
                    });
                    return bad.slice(0, 5);
                }""", selector)

            page.goto(f"http://127.0.0.1:{STATIC_PORT}")
            page.evaluate("(t) => localStorage.setItem('zhixing_token', t)", admin_token)
            page.reload()
            page.wait_for_selector('button[aria-label="Skill 工厂"]', timeout=15000)
            page.click('button[aria-label="Skill 工厂"]')
            page.wait_for_selector('[data-testid="tab-skill-review"]', timeout=15000)

            # ---------------- 候选列表（FR09）
            page.click('[data-testid="tab-skill-review"]')
            page.wait_for_selector('[data-testid="skill-list-row"]', timeout=10000)
            rows = page.locator('[data-testid="skill-list-row"]')
            check("列表显示 4 条候选", rows.count() == 4)
            order = [rows.nth(i).get_attribute("data-skill-id") for i in range(rows.count())]
            check("默认排序：待审核按生成把握度从低到高，校验未通过在后",
                  order[:3] == [skill_b2, skill_a, skill_b1] and order[3] == skill_failed)
            head = page.locator('[data-testid="skill-list-table"] thead').inner_text()
            check("列表字段齐全（名称、场景、状态、把握度、无依据项、引用知识、版本、生成记录、更新时间）",
                  all(w in head for w in ("名称", "场景", "状态", "生成把握度", "无依据项", "引用知识", "当前版本", "生成记录", "更新时间")))
            check("第二批候选标记「与已有 Skill 相似」",
                  page.locator(f'[data-testid="skill-list-row"][data-skill-id="{skill_b2}"] [data-testid="mark-similar"]').count() == 1)
            check_plain("候选列表", page.locator('[data-testid="skill-candidate-list"]').inner_text())
            check("候选列表字号不小于 12px", not small_fonts('[data-testid="skill-candidate-list"]'))
            page.screenshot(path=str(SHOTS / "m02d_01_candidate_list_1440x900.png"))

            page.select_option('[data-testid="filter-status"]', "validation_failed")
            page.wait_for_function("() => document.querySelectorAll('[data-testid=\"skill-list-row\"]').length === 1")
            check("按状态筛选：校验未通过 1 条", rows.first.get_attribute("data-skill-id") == skill_failed)
            page.select_option('[data-testid="filter-status"]', "")
            page.select_option('[data-testid="filter-unsupported"]', "yes")
            page.wait_for_selector(f'[data-testid="skill-list-row"][data-skill-id="{skill_a}"]', timeout=5000)
            page.wait_for_function("() => document.querySelectorAll('[data-testid=\"skill-list-row\"]').length === 1")
            check("按「有无依据项」筛选", rows.first.get_attribute("data-skill-id") == skill_a)
            page.select_option('[data-testid="filter-unsupported"]', "")
            page.select_option('[data-testid="filter-batch"]', batch1["batch_id"])
            page.wait_for_function("() => document.querySelectorAll('[data-testid=\"skill-list-row\"]').length === 2")
            check("按生成记录筛选：第一批 2 条", {rows.nth(i).get_attribute("data-skill-id") for i in range(2)} == {skill_a, skill_b1})
            page.screenshot(path=str(SHOTS / "m02d_02_list_filtered_1440x900.png"))
            page.select_option('[data-testid="filter-batch"]', "")
            page.wait_for_function("() => document.querySelectorAll('[data-testid=\"skill-list-row\"]').length === 4")

            # ---------------- 工作台双栏、门槛、联动（FR10 / FR12 / AC11）
            page.click(f'[data-testid="skill-list-row"][data-skill-id="{skill_a}"]')
            page.wait_for_selector('[data-testid="skill-workbench"]', timeout=10000)
            wb = page.locator('[data-testid="skill-workbench"]')
            check("工作台右侧为依据知识（按用途分组）", page.locator('[data-testid="atom-group"]').count() == 3)
            check("左侧按审核顺序分段，默认只读",
                  all(page.locator(f'[data-testid="section-{k}"]').count() == 1 for k in ("identity", "scope", "refs", "io", "steps", "risk"))
                  and wb.locator('[data-testid="section-identity"] input:not([type=checkbox]), [data-testid="section-steps"] textarea').count() == 0)
            check("待专家补充高亮（2 处）", wb.locator('[data-tbd="true"]').count() == 2)
            check("问题集中列出：待专家补充 2 处、无依据项 2 个",
                  page.locator('[data-testid="problem-item"][data-kind="待专家补充"]').count() == 2
                  and page.locator('[data-testid="problem-item"][data-kind="无依据步骤"]').count() == 1
                  and page.locator('[data-testid="problem-item"][data-kind="疑似无依据数值"]').count() == 1)
            check("审核清单不再逐段勾选（集中到「通过」对话框）",
                  wb.locator('input[type=checkbox][data-testid^="check-"]').count() == 0)
            steps_text = page.locator('[data-testid="section-steps"]').inner_text()
            check("步骤显示为「第 N 步」，不出现系统编号",
                  "编号" not in steps_text and not re.search(r"\bs\d+\b", steps_text)
                  and page.locator('[data-testid="step-card"][data-step-id="s1"] [data-testid="step-select"]').get_attribute("aria-label") == "第 1 步")
            check("无依据步骤高亮", page.locator('[data-testid="step-card"][data-step-id="s5"]').get_attribute("data-flagged") == "true")
            check("AC11 通过按钮不可用", page.locator('[data-testid="action-approve"]').is_disabled())
            blockers = page.locator('[data-testid="gate-blockers"]').inner_text()
            check("AC11 说明不可通过的原因（还有几处要处理）", "还有 4 处要处理" in blockers)
            page.click('[data-testid="problem-item"][data-kind="待专家补充"] >> nth=0 >> [data-testid="problem-go"]')
            page.wait_for_selector('[data-testid="section-scope"][data-editing="true"]', timeout=3000)
            check("点「去处理」打开对应段落的编辑", page.locator('[data-testid="field-not-applies"] input[data-tbd="true"]').count() == 1)
            page.click('[data-testid="edit-scope"]')
            check_plain("审核工作台", wb.inner_text())
            check("审核工作台字号不小于 12px", not small_fonts('[data-testid="skill-workbench"]'))
            page.screenshot(path=str(SHOTS / "m02d_03_workbench_gate_1440x900.png"))

            rate, attr = ATOMS["rate"]["version_id"], ATOMS["attr"]["version_id"]
            page.click('[data-testid="step-card"][data-step-id="s2"] [data-testid="step-select"]')
            page.wait_for_selector(f'[data-testid="atom-card"][data-vid="{rate}"][data-highlight="true"]', timeout=3000)
            check("点击步骤 s2，左栏高亮它依据的知识",
                  page.locator('[data-testid="atom-card"][data-highlight="true"]').count() == 1)
            page.click(f'[data-testid="atom-card"][data-vid="{attr}"] [data-testid="atom-select"]')
            page.wait_for_selector('[data-testid="step-card"][data-step-id="s3"][data-highlight="true"]', timeout=3000)
            check("点击知识，右栏高亮使用它的步骤",
                  page.locator('[data-testid="step-card"][data-highlight="true"]').count() == 1)
            page.click(f'[data-testid="atom-card"][data-vid="{rate}"] button[aria-label="展开"]')
            page.wait_for_selector('[data-testid="atom-evidence"]', timeout=5000)
            check("展开知识显示结构字段与原文证据卡片",
                  "当期应收为零时不计算收缴率" in page.locator(f'[data-testid="atom-card"][data-vid="{rate}"]').inner_text()
                  and page.locator('[data-testid="atom-evidence"] mark').count() == 1)
            page.screenshot(path=str(SHOTS / "m02d_04_linking_and_evidence_1440x900.png"))

            # 添加依据：本批次找到的知识 / 在全部知识中查找（M01 正式检索，模拟检索函数）
            check("依据知识默认只看不改", page.locator('[data-testid="atom-add"]').count() == 0)
            page.click('[data-testid="edit-refs"]')
            page.click('[data-testid="atom-add"]')
            page.wait_for_selector('[data-testid="atom-picker"]', timeout=5000)
            picker_rows = page.locator('[data-testid="atom-picker"] [data-testid="picker-row"]')
            check("添加依据：列出本批次找到的知识，已引用的标明",
                  picker_rows.count() == 5 and page.locator('[data-testid="picker-add"]:has-text("已引用")').count() == 3)
            page.click('[data-testid="picker-tab-search"]')
            page.fill('[data-testid="picker-query"]', "收缴")
            page.click('[data-testid="picker-search"]')
            page.wait_for_selector('[data-testid="atom-picker"] [data-testid="picker-row"]:has-text("报修响应要求")', timeout=5000)
            check("添加依据：可在全部知识中查找", picker_rows.count() == 1)
            page.click('[data-testid="atom-picker"] button[aria-label="关闭"]')
            page.click('[data-testid="edit-refs"]')

            # ---------------- 编辑并修改后通过（FR11 / AC12）
            page.click('[data-testid="edit-scope"]')
            page.locator('[data-testid="field-not-applies"] input[data-tbd="true"]').fill("收缴率下降原因分析属于另一任务")
            page.click('[data-testid="edit-risk"]')
            page.locator('[data-testid="field-risk"] input[data-tbd="true"]').fill("不替代财务人员的最终核算")
            page.click('[data-testid="resolve-expert-step"]')
            page.locator('#step-s5 [data-testid="step-expert-reason"]').fill("项目惯例：处置后 30 分钟内回访")
            page.locator('[data-testid="step-card"][data-step-id="s2"] [data-testid="step-action"]').fill("按月统计收缴率：当期实收除以当期应收")
            page.locator('[data-testid="step-card"][data-step-id="s4"] button[aria-label="上移"]').click()
            page.click('[data-testid="step-add"]')
            page.locator('[data-testid="step-card"][data-step-id="s6"] [data-testid="step-action"]').fill("由收费主管确认核对结论")
            check("新增步骤由系统分配编号 s6", page.locator('[data-testid="step-card"][data-step-id="s6"]').count() == 1)
            page.wait_for_function(
                "() => { const b = document.querySelector('[data-testid=\"action-approve\"]'); return b && !b.disabled && b.textContent.includes('修改后通过'); }",
                timeout=10000)
            check("问题处理完后按钮可用，并显示「修改后通过」", True)
            check("问题全部处理，状态提示可以通过",
                  page.locator('[data-testid="problems-card"]').get_attribute("data-count") == "0"
                  and "都处理好了" in page.locator('[data-testid="gate-blockers"]').inner_text())
            page.click('[data-testid="action-save"]')
            page.wait_for_selector('[data-testid="workbench-notice"]:has-text("草稿")', timeout=5000)
            check("保存草稿成功，草稿不产生版本", len(api_get(f"/skill-factory/skills/{skill_a}/workbench")["versions"]) == 1)
            page.screenshot(path=str(SHOTS / "m02d_05_ready_to_approve_1440x900.png"))

            page.click('[data-testid="action-approve"]')
            page.wait_for_selector('[data-testid="approve-dialog"]', timeout=5000)
            check("AC11 审核清单在通过对话框中逐项确认，未勾完不能确认",
                  page.locator('[data-testid="approve-checklist"] input[type=checkbox]').count() == 6
                  and page.locator('[data-testid="approve-confirm"]').is_disabled())
            for key in ("goal", "scope", "refs", "steps", "outputs", "escalation"):
                page.check(f'[data-testid="check-{key}"]')
            check("清单全部勾选后可以确认", not page.locator('[data-testid="approve-confirm"]').is_disabled())
            dialog_text = page.locator('[data-testid="approve-dialog"]').inner_text()
            check("通过对话框列出改动，说明会产生新版本", "专家修订版" in dialog_text and "步骤 s2 · 动作" in dialog_text)
            page.locator('[data-testid="approve-diff"]:has-text("步骤 s2 · 动作") input').fill("口径表述更准确")
            page.screenshot(path=str(SHOTS / "m02d_06_approve_dialog_1440x900.png"))
            page.click('[data-testid="approve-confirm"]')
            page.wait_for_selector('[data-testid="workbench-notice"]:has-text("已跳到下一条")', timeout=10000)
            wb_a = api_get(f"/skill-factory/skills/{skill_a}/workbench")
            check("AC12 修改后通过产生第 2 版专家修订版",
                  wb_a["status"] == "approved" and [v["version_kind"] for v in wb_a["versions"]] == ["ai_original", "expert_revision"])
            check("提交后自动跳到下一条待审核候选",
                  page.locator('[data-testid="workbench-name"]').inner_text() == TASK_B and
                  api_get(f"/skill-factory/skills/{skill_b1}/workbench")["status"] == "pending_review")
            page.screenshot(path=str(SHOTS / "m02d_07_next_candidate_1440x900.png"))

            # ---------------- 无修改通过（AC13）
            page.wait_for_function(
                "() => { const b = document.querySelector('[data-testid=\"action-approve\"]'); return b && !b.disabled && b.textContent.trim().endsWith('通过') && !b.textContent.includes('修改后'); }",
                timeout=10000)
            page.click('[data-testid="action-approve"]')
            for key in ("goal", "scope", "refs", "steps", "outputs", "escalation"):
                page.check(f'[data-testid="check-{key}"]')
            check("AC13 无修改时对话框说明不产生新版本",
                  "不产生新版本" in page.locator('[data-testid="approve-dialog"]').inner_text())
            page.click('[data-testid="approve-confirm"]')
            page.wait_for_selector('[data-testid="workbench-notice"]:has-text("已跳到下一条")', timeout=10000)
            wb_b1 = api_get(f"/skill-factory/skills/{skill_b1}/workbench")
            check("AC13 记录为「通过」且版本数仍为 1",
                  wb_b1["status"] == "approved" and len(wb_b1["versions"]) == 1
                  and wb_b1["review_records"][-1]["action"] == "approve")

            # ---------------- 退回重生成（AC14）
            check("跳到第二批的欠费分级候选", page.locator('[data-testid="skill-workbench"]').count() == 1
                  and api_get(f"/skill-factory/skills/{skill_b2}/workbench")["status"] == "pending_review")
            RELEASE.clear()
            page.click('[data-testid="action-regenerate"]')
            page.wait_for_selector('[data-testid="regenerate-dialog"]', timeout=5000)
            check("AC14 退回意见必填：未填写时不能提交", page.locator('[data-testid="regenerate-confirm"]').is_disabled())
            page.fill('[data-testid="regenerate-comment"]', "不适用范围太笼统，请写明不处理非住宅项目")
            page.check('[data-testid="regenerate-group-A"]')
            page.screenshot(path=str(SHOTS / "m02d_08_regenerate_dialog_1440x900.png"))
            page.click('[data-testid="regenerate-confirm"]')
            page.wait_for_selector('[data-testid="skill-candidate-list"]', timeout=10000)
            page.wait_for_selector(f'[data-testid="skill-list-row"][data-skill-id="{skill_b2}"][data-status="generating"]', timeout=10000)
            page.click(f'[data-testid="skill-list-row"][data-skill-id="{skill_b2}"]')
            page.wait_for_selector('[data-testid="generating-banner"]', timeout=10000)
            check("重生成期间显示「生成中」且不能操作",
                  page.locator('[data-testid="action-approve"]').count() == 0 and page.locator('[data-testid="action-reject"]').count() == 0)
            page.screenshot(path=str(SHOTS / "m02d_09_generating_1440x900.png"))
            RELEASE.set()
            page.wait_for_selector('[data-testid="skill-workbench"][data-status="pending_review"]', timeout=20000)
            check("AC14 新版本回到待审核并显示重生成次数", "已重生成 1 次" in page.locator('[data-testid="regenerate-count"]').inner_text())
            page.click('[data-testid="workbench-tab-history"]')
            page.wait_for_selector('[data-testid="diff-summary"]', timeout=10000)
            page.click('[data-testid="diff-view-compare"]')
            page.wait_for_selector('[data-testid="diff-compare"]', timeout=5000)
            compare_text = page.locator('[data-testid="diff-compare"]').inner_text()
            check("AC14 可比较两个 AI 版本（AI 原稿对 AI 重生成版）", "AI 原稿" in compare_text and "AI 重生成版" in compare_text
                  and page.locator('[data-testid="compare-field"][data-field="not_applies_to"][data-changed="true"]').count() == 1)
            page.screenshot(path=str(SHOTS / "m02d_10_regenerated_compare_1440x900.png"), full_page=True)
            page.click('[data-testid="workbench-tab-review"]')

            # ---------------- 驳回与恢复（AC15）
            page.wait_for_selector('[data-testid="action-reject"]', timeout=5000)
            page.click('[data-testid="action-reject"]')
            page.wait_for_selector('[data-testid="reject-dialog"]', timeout=5000)
            check("AC15 未选原因时不能驳回", page.locator('[data-testid="reject-confirm"]').is_disabled())
            page.check('[data-testid="reject-reason"][value="与已有 Skill 重复"]')
            check("AC15 未填说明时不能驳回", page.locator('[data-testid="reject-confirm"]').is_disabled())
            page.fill('[data-testid="reject-note"]', "与第一批的欠费分级候选重复")
            page.screenshot(path=str(SHOTS / "m02d_11_reject_dialog_1440x900.png"))
            page.click('[data-testid="reject-confirm"]')
            page.wait_for_selector('[data-testid="skill-candidate-list"]', timeout=10000)
            page.click(f'[data-testid="skill-list-row"][data-skill-id="{skill_b2}"]')
            page.wait_for_selector('[data-testid="skill-workbench"][data-status="rejected"]', timeout=10000)
            check("驳回后只能恢复", page.locator('[data-testid="action-restore"]').count() == 1
                  and page.locator('[data-testid="action-approve"]').count() == 0)
            page.screenshot(path=str(SHOTS / "m02d_12_rejected_1440x900.png"))
            page.click('[data-testid="action-restore"]')
            page.locator('[role="dialog"][aria-label="恢复这条候选？"] button').last.click()
            page.wait_for_selector('[data-testid="skill-workbench"][data-status="pending_review"]', timeout=10000)
            check("AC15 恢复后回到待审核", "待审核" in page.locator('[data-testid="workbench-status"]').inner_text())

            # ---------------- 并发冲突（AC19）
            wb_now = requests.get(f"{API}/skill-factory/skills/{skill_b2}/workbench",
                                  headers={"Authorization": f"Bearer {other_session}"}, timeout=10).json()
            saved = requests.put(f"{API}/skill-factory/skills/{skill_b2}/draft",
                                 json={"revision_token": wb_now["revision_token"], "skill_json": wb_now["content"]},
                                 headers={"Authorization": f"Bearer {other_session}"}, timeout=10)
            check("另一个会话先保存了修改", saved.status_code == 200)
            page.click('[data-testid="edit-identity"]')
            page.fill('[data-testid="field-goal"]', "本会话的修改")
            page.click('[data-testid="action-save"]')
            page.wait_for_selector('[data-testid="conflict-banner"]', timeout=5000)
            check("AC19 后提交者收到冲突提示", "其他人修改" in page.locator('[data-testid="conflict-banner"]').inner_text())
            check("AC19 冲突后编辑锁定，不覆盖先提交的内容",
                  page.locator('[data-testid="field-goal"]').count() == 0 and page.locator('[data-testid="edit-identity"]').count() == 0
                  and api_get(f"/skill-factory/skills/{skill_b2}/workbench")["content"]["goal"] != "本会话的修改")
            page.screenshot(path=str(SHOTS / "m02d_13_conflict_1440x900.png"))

            # ---------------- 校验未通过的候选
            page.click('[data-testid="workbench-back"]')
            leave = page.locator('[role="dialog"][aria-label="还有修改没有保存"]')
            leave.wait_for(timeout=5000)
            check("有未保存的修改时离开前提示", leave.count() == 1)
            leave.locator("button").last.click()
            page.wait_for_selector('[data-testid="skill-candidate-list"]', timeout=10000)
            page.click(f'[data-testid="skill-list-row"][data-skill-id="{skill_failed}"]')
            page.wait_for_selector('[data-testid="generation-issues"]', timeout=10000)
            check("校验未通过：说明问题并只能重新生成或放弃",
                  "引用了本次范围以外的知识" in page.locator('[data-testid="generation-issues"]').inner_text()
                  and page.locator('[data-testid="action-regenerate"]').inner_text().strip() == "重新生成"
                  and page.locator('[data-testid="action-abandon"]').count() == 1
                  and page.locator('[data-testid="action-approve"]').count() == 0)
            check_plain("校验未通过工作台", page.locator('[data-testid="skill-workbench"]').inner_text())
            page.screenshot(path=str(SHOTS / "m02d_14_validation_failed_1440x900.png"))

            # ---------------- 差异对照与导出（FR13）
            page.click('[data-testid="workbench-back"]')
            page.wait_for_selector('[data-testid="skill-candidate-list"]', timeout=10000)
            page.click(f'[data-testid="skill-list-row"][data-skill-id="{skill_a}"]')
            page.wait_for_selector('[data-testid="skill-workbench"][data-status="approved"]', timeout=10000)
            page.click('[data-testid="workbench-tab-history"]')
            page.wait_for_selector('[data-testid="diff-summary"]', timeout=10000)
            summary = page.locator('[data-testid="diff-summary"]').inner_text()
            check("修改摘要：修改字段数、新增 1 步、删除 0 步、无依据项专家补充 2 项",
                  page.locator('[data-testid="stat-steps-added"]').inner_text().endswith("1")
                  and page.locator('[data-testid="stat-steps-deleted"]').inner_text().endswith("0")
                  and "专家补充 2 项" in summary and "修改后通过" in summary)
            page.screenshot(path=str(SHOTS / "m02d_15_diff_summary_1440x900.png"))
            page.click('[data-testid="diff-view-compare"]')
            page.wait_for_selector('[data-testid="diff-compare"]', timeout=5000)
            steps_field = page.locator('[data-testid="compare-field"][data-field="steps"]')
            check("字段对照：改动的步骤高亮，未改的步骤不高亮（调整顺序不整段误报）",
                  steps_field.locator('[data-item-id="s2"]').get_attribute("data-changed") == "mod"
                  and steps_field.locator('[data-item-id="s6"]').get_attribute("data-changed") == "add"
                  and steps_field.locator('[data-item-id="s3"]').get_attribute("data-changed") is None
                  and steps_field.locator('[data-item-id="s4"]').get_attribute("data-changed") is None)
            check("字段对照：左为 AI 原稿，右为当前版本", "第 1 版 · AI 原稿" in page.locator('[data-testid="diff-compare"]').inner_text()
                  and "第 2 版 · 专家修订版" in page.locator('[data-testid="diff-compare"]').inner_text())
            page.screenshot(path=str(SHOTS / "m02d_16_diff_compare_1440x900.png"))
            page.click('[data-testid="diff-view-list"]')
            page.wait_for_selector('[data-testid="diff-list"]', timeout=5000)
            check("修改清单按稳定路径列出，含顺序调整与修改理由",
                  page.locator('[data-testid="diff-row"][data-path="steps[s2].action"]').count() == 1
                  and page.locator('[data-testid="diff-row"][data-op="reorder"]').count() == 1
                  and "口径表述更准确" in page.locator('[data-testid="diff-row"][data-path="steps[s2].action"]').inner_text())
            check_plain("修改记录页", page.locator('[data-testid="diff-pane"]').inner_text())
            check("修改记录页字号不小于 12px", not small_fonts('[data-testid="diff-pane"]'))
            page.screenshot(path=str(SHOTS / "m02d_17_diff_list_1440x900.png"))

            with page.expect_download() as dl:
                page.click('[data-testid="export-md"]')
            md_path = DOWNLOADS / "ui_export_diff.md"
            dl.value.save_as(str(md_path))
            md = md_path.read_text(encoding="utf-8")
            with page.expect_download() as dl:
                page.click('[data-testid="export-json"]')
            json_path = DOWNLOADS / "ui_export_diff.json"
            dl.value.save_as(str(json_path))
            exported = json.loads(json_path.read_text(encoding="utf-8"))
            check("导出 Markdown 含原稿、终稿与差异清单", all(s in md for s in ("## 原稿", "## 终稿", "## 差异清单", "`steps[s2].action`")))
            check("导出 JSON 原稿为第 1 版、终稿为第 2 版",
                  exported["original"]["version_number"] == 1 and exported["final"]["version_number"] == 2)
            browser.close()
    finally:
        RELEASE.set()
        server.should_exit = True
        static.shutdown()
        for p in patches:
            p.stop()

    passed = sum(1 for _, ok in results if ok)
    print(f"\nM02-D UI 验证：{passed}/{len(results)} 通过")
    cleanup_test_db(TEST_DB)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
