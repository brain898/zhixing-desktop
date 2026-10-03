"""
M02-E UI 验证：待复核标记、原子新旧差异视图与受影响步骤高亮、复核动作、待审核候选的变更提示、
删除确认弹窗中的 Skill 引用数量（PRD FR14、R4、R5、R7、M01-C4；AC16~AC18）。

- 后端：在本进程内以隔离临时数据库启动真实 FastAPI 服务（端口 8799），不读写 server/data/zhixing.db。
- 前端：直接加载 client/dist 生产构建；页面对 http://127.0.0.1:8766/api 的请求由 Playwright 转发到 8799。
- M01 变更一律通过 M01 接口触发。索引向量为模拟向量（只为让索引任务完成切换），不代表检索效果；本脚本不调用模型。
- 截图保存到 screenshots/m02e_*.png（1440×900）。

用法：python tests/verify_m02e_stale_ui.py（需先 npm run build）
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

TEST_DB = setup_test_db("m02e_ui")

import uvicorn  # noqa: E402
import config  # noqa: E402
import indexing  # noqa: E402
import scene_catalog as sc  # noqa: E402
import skill_generation as sg  # noqa: E402
from database import get_db  # noqa: E402
from main import app  # noqa: E402
from skill_constants import REVIEW_CHECKLIST  # noqa: E402
from skill_validation import validate_skill  # noqa: E402

CLIENT_DIST = BASE_DIR / "client" / "dist"
SHOTS = BASE_DIR / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
API_PORT = 8799
STATIC_PORT = 5179
API = f"http://127.0.0.1:{API_PORT}/api"
ORG = "org_greentown"
ADMIN_ID = "usr_admin_001"
ALL_CHECKED = {key: True for key, _ in REVIEW_CHECKLIST}


def fake_embed(texts):
    return [[0.01] * config.EMBEDDING_DIM for _ in list(texts)]


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


class Fixture:
    def __init__(self, token):
        self.h = {"Authorization": f"Bearer {token}"}
        with get_db() as conn:
            self.actor = sg.load_actor(conn, ADMIN_ID, ORG)
            self.scene = sc.create_scene(conn, ORG, ADMIN_ID, {
                "name": "设备巡检", "description": "巡检异常处置", "aliases": ["设备巡检"],
                "typical_problems": ["巡检发现异常怎么办"]})

    def document(self, title):
        now = datetime.now(timezone.utc).isoformat()
        doc_id, ver_id = f"doc_{uuid.uuid4().hex[:10]}", f"ver_{uuid.uuid4().hex[:10]}"
        blocks = [f"blk_{uuid.uuid4().hex[:10]}" for _ in range(2)]
        with get_db() as conn:
            conn.execute("""INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted,
                            created_at, updated_at) VALUES (?, ?, ?, NULL, 'org_internal', 0, ?, ?)""",
                         (doc_id, ORG, title, now, now))
            conn.execute("""INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name,
                            file_type, file_size, content_hash, storage_reference, uploaded_by, uploaded_at,
                            processing_status) VALUES (?, ?, ?, 'v1', ?, 'txt', 100, ?, 'x.txt', ?, ?, 'completed')""",
                         (ver_id, doc_id, ORG, title + ".txt", uuid.uuid4().hex, ADMIN_ID, now))
            for i, block in enumerate(blocks, start=1):
                conn.execute("""INSERT INTO source_blocks (id, document_version_id, organization_id, block_index,
                                block_type, heading_path, paragraph_anchor, text_content, created_at)
                                VALUES (?, ?, ?, ?, 'paragraph', '巡检', ?, '巡检原文', ?)""",
                             (block, ver_id, ORG, i, f"[line_{i}]", now))
        return {"doc_id": doc_id, "ver_id": ver_id, "blocks": blocks, "title": title}

    def atom(self, doc, title, statement):
        now = datetime.now(timezone.utc).isoformat()
        item_id, vid = f"ki_{uuid.uuid4().hex[:10]}", f"kv_{uuid.uuid4().hex[:10]}"
        with get_db() as conn:
            conn.execute("""INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope,
                            lifecycle_status, is_excluded, created_at, updated_at)
                            VALUES (?, ?, ?, ?, 'org_internal', 'active', 0, ?, ?)""",
                         (item_id, doc["doc_id"], ORG, vid, now, now))
            conn.execute("""INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id,
                            version_number, title, content, primary_category, atom_type, subject, statement,
                            conditions_json, actions_json, exceptions_json, customer_types_json, business_scenes_json,
                            problem_tags_json, source_anchors_json, review_status, index_status, revision_token,
                            created_at, created_by)
                            VALUES (?, ?, ?, ?, 1, ?, ?, '制度与标准', '规则', '物业服务人员', ?, '["巡检中发现异常"]', ?, '[]',
                                    '["住宅业主"]', '["设备巡检"]', '[]', '["[line_1]"]', 'pending_review',
                                    'not_indexed', ?, ?, ?)""",
                         (vid, item_id, ORG, doc["ver_id"], title, statement, statement,
                          json.dumps([statement], ensure_ascii=False), uuid.uuid4().hex, now, ADMIN_ID))
            for field, block in (("statement", doc["blocks"][0]), ("actions", doc["blocks"][1])):
                conn.execute("""INSERT INTO knowledge_evidence (id, knowledge_version_id, source_block_id, organization_id,
                                field_name, excerpt, accuracy_level, created_at) VALUES (?, ?, ?, ?, ?, ?, 'high', ?)""",
                             (f"ev_{uuid.uuid4().hex[:10]}", vid, block, ORG, field, statement, now))
        self.confirm(item_id, vid)
        return {"item_id": item_id, "version_id": vid, "title": title}

    def confirm(self, item_id, vid):
        detail = requests.get(f"{API}/knowledge/items/{item_id}", headers=self.h, timeout=10).json()
        target = detail.get("pending_version") or detail["active_version"]
        r = requests.post(f"{API}/knowledge/items/{item_id}/confirm", json={"revision_token": target["revision_token"]},
                          headers=self.h, timeout=10)
        r.raise_for_status()
        deadline = time.time() + 30
        while time.time() < deadline:
            with get_db() as conn:
                row = conn.execute("SELECT active_version_id FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
                st = conn.execute("SELECT index_status FROM knowledge_versions WHERE id = ?", (vid,)).fetchone()
            if st["index_status"] == "ready" and row["active_version_id"] == vid:
                return
            time.sleep(0.1)
        raise RuntimeError("索引等待超时")

    def new_version(self, item_id, statement):
        detail = requests.get(f"{API}/knowledge/items/{item_id}", headers=self.h, timeout=10).json()
        av = detail["active_version"]
        payload = {k: av[k] for k in ("title", "primary_category", "atom_type", "subject", "conditions", "actions",
                                      "exceptions", "metric_definition", "case_details", "customer_types",
                                      "business_scenes", "problem_tags", "valid_from", "valid_until")}
        payload.update({"revision_token": av["revision_token"], "statement": statement, "content": statement,
                        "access_scope": detail["access_scope"],
                        "actions": [statement]})
        r = requests.put(f"{API}/knowledge/items/{item_id}/draft", json=payload, headers=self.h, timeout=10)
        r.raise_for_status()
        new_vid = r.json()["version_id"]
        self.confirm(item_id, new_vid)
        return new_vid

    def skill(self, atoms, name, approve=True):
        steps = [{"step_id": "s1", "kind": "输入校验", "action": "检查现场情况描述是否齐全", "refs": [],
                  "basis": "通用操作", "on_fail": "补问"}]
        refs = []
        for i, a in enumerate(atoms, start=2):
            steps.append({"step_id": f"s{i}", "kind": "规则判断", "action": f"按「{a['title']}」判断处理方式",
                          "refs": [a["version_id"]], "basis": "有原子依据", "on_fail": "转人工"})
            refs.append({"atom_item_id": a["item_id"], "atom_version_id": a["version_id"], "role": "判断规则",
                         "used_in_steps": [f"s{i}"]})
        cand = {
            "schema_version": "1.0", "name": name, "goal": "判断巡检发现的异常应如何处置",
            "trigger_description": "用户描述巡检中发现的异常，询问如何处理", "task_type": "判断分级", "scene_id": "x",
            "applies_to": {"customer_types": ["住宅业主"], "property_types": [], "conditions": []},
            "not_applies_to": ["收费争议属于另一任务"], "knowledge_refs": refs,
            "inputs": [{"key": "situation", "label": "现场情况", "type": "text", "required": True}],
            "preconditions": [{"text": "异常已由巡检人员现场确认", "ref": atoms[0]["version_id"]}],
            "outputs": [{"key": "route", "label": "处置方式", "type": "text", "required": True}],
            "steps": steps, "risk_boundary": ["不替代现场人员对安全风险的判断"],
            "escalation_conditions": ["无法判断时转人工"],
            "generation_confidence": {"level": "高", "reason": "测试固定候选"},
        }
        with get_db() as conn:
            cand, _ = sg.normalize_candidate(cand, self.scene["scene_id"])
            report = validate_skill(conn, cand, self.actor, pool_ids=None)
            stored = sg.store_candidate(conn, org_id=ORG, scene_id=self.scene["scene_id"], batch_id=None, task_key=None,
                                        candidate=cand, report=report,
                                        generation={"model_name": "fixture", "prompt_version": "fixture"},
                                        created_by=ADMIN_ID)
        sid = stored["skill_id"]
        if approve:
            wb = requests.get(f"{API}/skill-factory/skills/{sid}/workbench", headers=self.h, timeout=10).json()
            r = requests.post(f"{API}/skill-factory/skills/{sid}/review/approve", headers=self.h, timeout=10,
                              json={"revision_token": wb["revision_token"], "skill_json": wb["content"],
                                    "checklist": ALL_CHECKED})
            r.raise_for_status()
        return sid


def main():
    patches = [patch.object(indexing, "embed_texts", fake_embed)]
    for p in patches:
        p.start()
    static, server = start_servers()
    token = requests.post(f"{API}/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"},
                          timeout=10).json()["token"]
    fx = Fixture(token)
    doc1 = fx.document("设备巡检作业规程（自编测试资料）")
    a = fx.atom(doc1, "异常分级标准", "涉及人身安全的异常转入应急流程。")
    b = fx.atom(doc1, "工单登记要求", "一般异常登记工单并通知责任人。")
    c = fx.atom(doc1, "巡检记录要求", "巡检结束后当日提交巡检记录。")
    doc2 = fx.document("配电房巡检指引（自编测试资料）")
    d = fx.atom(doc2, "配电房巡检要点", "配电房巡检须两人同行。")
    e = fx.atom(doc2, "配电异常处置", "配电异常先断电再报修。")

    s_new = fx.skill([a, b], "巡检异常分级处置")
    s_del = fx.skill([b, c], "巡检记录与工单登记")
    s_pending = fx.skill([c, a], "巡检异常上报", approve=False)
    s_doc2 = fx.skill([d, e], "配电房异常处置")

    # M01 变更（全部走 M01 接口）
    new_vid = fx.new_version(a["item_id"], "涉及人身安全或设备停运的异常转入应急流程。")
    requests.delete(f"{API}/knowledge/items/{c['item_id']}", headers=fx.h, timeout=10).raise_for_status()
    requests.put(f"{API}/knowledge/items/{a['item_id']}/access-scope", json={"access_scope": "admin_only"},
                 headers=fx.h, timeout=10).raise_for_status()

    results = []

    def check(label, cond):
        results.append((label, bool(cond)))
        print(("  [通过] " if cond else "  [失败] ") + label)
        return cond

    jargon = ("原子", "召回", "模型", "DeepSeek", "提示词", "TBD_EXPERT", "stale", "atom", "kv_", "needs_recheck")

    def check_plain(label, text):
        found = [w for w in jargon if w in text]
        return check(f"{label}：页面没有技术术语{('（发现 ' + '、'.join(found) + '）') if found else ''}", not found)

    def api_get(path):
        return requests.get(f"{API}{path}", headers=fx.h, timeout=10).json()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})

            def forward(route):
                route.fulfill(response=route.fetch(url=route.request.url.replace("http://127.0.0.1:8766/api", API)))

            context.route("http://127.0.0.1:8766/api/**", forward)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))

            def small_fonts(selector):
                return page.evaluate("""(sel) => {
                    const root = document.querySelector(sel); if (!root) return ['root missing'];
                    const bad = [];
                    root.querySelectorAll('*').forEach((el) => {
                      const hasText = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim());
                      if (!hasText) return;
                      const size = parseFloat(getComputedStyle(el).fontSize);
                      if (size < 12) bad.push(el.tagName + ':' + size + ':' + el.textContent.trim().slice(0, 12));
                    });
                    return bad.slice(0, 5);
                }""", selector)

            page.goto(f"http://127.0.0.1:{STATIC_PORT}")
            page.evaluate("(t) => localStorage.setItem('zhixing_token', t)", token)
            page.reload()
            page.wait_for_selector('button[aria-label="Skill 工厂"]', timeout=15000)
            page.click('button[aria-label="Skill 工厂"]')
            page.wait_for_selector('[data-testid="tab-skill-review"]', timeout=15000)
            page.click('[data-testid="tab-skill-review"]')
            page.wait_for_selector('[data-testid="skill-list-row"]', timeout=10000)

            # ---------------- 列表：待复核标记（R5）
            rows = page.locator('[data-testid="skill-list-row"]')
            order = [rows.nth(i).get_attribute("data-skill-id") for i in range(rows.count())]
            check("待复核的 Skill 排在列表最前", set(order[:2]) == {s_new, s_del})
            row_new = page.locator(f'[data-testid="skill-list-row"][data-skill-id="{s_new}"]')
            check("状态显示「待复核」", "待复核" in row_new.inner_text())
            check("待复核行显示原因（引用知识已产生新版本）",
                  "新版本" in row_new.locator('[data-testid="stale-reason"]').inner_text())
            check("待复核行标记「知识已变更」", row_new.locator('[data-testid="mark-atom-changed"]').count() == 1)
            row_pending = page.locator(f'[data-testid="skill-list-row"][data-skill-id="{s_pending}"]')
            check("待审核候选状态不变，但标记「知识已变更」",
                  "待审核" in row_pending.inner_text() and row_pending.locator('[data-testid="mark-atom-changed"]').count() == 1)
            check_plain("候选列表", page.locator('[data-testid="skill-candidate-list"]').inner_text())
            page.screenshot(path=str(SHOTS / "m02e_01_list_needs_recheck_1440x900.png"))

            # ---------------- 复核视图：新旧差异、受影响步骤（AC16）
            row_new.click()
            page.wait_for_selector('[data-testid="atom-changes-card"]', timeout=10000)
            check("工作台状态为待复核", page.get_attribute('[data-testid="skill-workbench"]', "data-status") == "needs_recheck")
            banner = page.locator('[data-testid="atom-changed-banner"]').inner_text()
            check("顶部说明需要复核的原因", "需要复核" in banner and "新版本" in banner)
            check("顶部说明可见范围已随权限收紧", "仅管理员" in banner)
            item_a = page.locator(f'[data-testid="atom-change-item"][data-atom="{a["version_id"]}"]')
            diff_rows = item_a.locator('[data-testid="atom-diff-row"]')
            fields = [diff_rows.nth(i).get_attribute("data-field") for i in range(diff_rows.count())]
            check("差异视图列出核心陈述与动作的变化", "statement" in fields and "actions" in fields)
            stmt = item_a.locator('[data-testid="atom-diff-row"][data-field="statement"]').inner_text()
            check("旧版本与新版本内容并列显示", "涉及人身安全的异常转入应急流程" in stmt and "设备停运" in stmt)
            triggers = item_a.locator('[data-testid="atom-change-trigger"]').all_inner_texts()
            check("标出触发原因：新版本生效与权限收紧", any("新版本" in t for t in triggers) and any("权限" in t for t in triggers))
            affected = item_a.locator('[data-testid="atom-change-affected"]').inner_text()
            check("列出用到它的步骤与前置条件", "步骤 s2" in affected and "前置条件" in affected)
            item_a.locator('[data-testid="atom-change-affected"] button', has_text="步骤 s2").click()
            page.wait_for_timeout(400)
            highlighted = page.evaluate("""() => { const el = document.getElementById('step-s2');
                if (!el) return false; const s = getComputedStyle(el); return s.backgroundColor !== 'rgba(0, 0, 0, 0)' || s.borderColor !== ''; }""")
            check("点击「步骤 s2」后右栏定位并高亮该步骤", highlighted)
            check("处理前「提交复核」不可用", page.locator('[data-testid="action-recheck"]').is_disabled())
            check("没有「通过」按钮（待复核只能提交复核、退回重生成或驳回）", page.locator('[data-testid="action-approve"]').count() == 0)
            check_plain("知识变更卡片", page.locator('[data-testid="atom-changes-card"]').inner_text())
            check("知识变更卡片字号不小于 12px", not small_fonts('[data-testid="atom-changes-card"]'))
            item_a.scroll_into_view_if_needed()
            page.locator('[data-testid="atom-changes-card"]').scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / "m02e_02_recheck_atom_diff_1440x900.png"))

            # 更新引用 -> 提交复核
            item_a.locator('[data-testid="stale-update"]').click()
            page.wait_for_function(f"""() => document.querySelector('[data-testid="atom-change-item"][data-atom="{a['version_id']}"]')?.dataset.handled === 'true'""", timeout=10000)
            check("选择「更新引用」后该条变为已处理", True)
            page.wait_for_function("() => !document.querySelector('[data-testid=\"action-recheck\"]').disabled", timeout=10000)
            page.click('[data-testid="action-recheck"]')
            page.wait_for_selector('[data-testid="recheck-dialog"]')
            check("提交复核对话框列出处理方式", "更新引用" in page.locator('[data-testid="recheck-dialog"]').inner_text())
            page.screenshot(path=str(SHOTS / "m02e_03_recheck_dialog_1440x900.png"))
            page.click('[data-testid="recheck-confirm"]')
            page.wait_for_function("() => document.querySelector('[data-testid=\"skill-workbench\"]')?.dataset.status === 'approved'", timeout=10000)
            wb = api_get(f"/skill-factory/skills/{s_new}/workbench")
            check("复核后回到已通过，产生复核处理版并改指新版本",
                  wb["status"] == "approved" and wb["current_version_kind"] == "recheck_revision"
                  and new_vid in {r["atom_version_id"] for r in wb["content"]["knowledge_refs"]})
            check("可见范围保持「仅管理员」", wb["visibility"] == "admin_only")
            page.click('[data-testid="workbench-back"]')

            # ---------------- 删除的知识：只能移除，显示引用时保存的内容（AC17）
            page.wait_for_selector(f'[data-testid="skill-list-row"][data-skill-id="{s_del}"]')
            page.click(f'[data-testid="skill-list-row"][data-skill-id="{s_del}"]')
            page.wait_for_selector('[data-testid="atom-changes-card"]', timeout=10000)
            item_c = page.locator(f'[data-testid="atom-change-item"][data-atom="{c["version_id"]}"]')
            check("已删除的知识只提供「移除引用」", item_c.locator('[data-testid="stale-update"]').count() == 0
                  and item_c.locator('[data-testid="stale-no-impact"]').count() == 0)
            snap = item_c.locator('[data-testid="atom-old-snapshot"]').inner_text()
            check("已删除的知识显示引用时保存的内容", "已删除" in snap and "巡检结束后当日提交巡检记录" in snap)
            page.locator('[data-testid="atom-changes-card"]').scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / "m02e_04_deleted_atom_remove_only_1440x900.png"))
            item_c.locator('[data-testid="stale-remove"]').click()
            page.wait_for_function("() => [...document.querySelectorAll('[data-testid=\"problem-item\"]')].some((e) => e.dataset.kind === '无依据步骤')", timeout=10000)
            check("移除引用后只依据它的步骤变为「无依据」，按 FR11 处理", True)
            page.screenshot(path=str(SHOTS / "m02e_05_removed_then_fr11_1440x900.png"))
            page.click('[data-testid="workbench-back"]')
            if page.locator('text=仍然离开').count():
                page.click('text=仍然离开')

            # ---------------- 待审核候选：状态不变、处理后才能通过
            page.wait_for_selector(f'[data-testid="skill-list-row"][data-skill-id="{s_pending}"]')
            page.click(f'[data-testid="skill-list-row"][data-skill-id="{s_pending}"]')
            page.wait_for_selector('[data-testid="atom-changes-card"]', timeout=10000)
            check("待审核候选显示变更提示", "处理后才能通过" in page.locator('[data-testid="atom-changed-banner"]').inner_text())
            check("待审核候选的通过按钮不可用", page.locator('[data-testid="action-approve"]').is_disabled())
            check("问题清单包含「知识变更」",
                  page.locator('[data-testid="problem-item"][data-kind="知识变更"]').count() >= 1)
            page.screenshot(path=str(SHOTS / "m02e_06_pending_notice_1440x900.png"))
            page.click('[data-testid="workbench-back"]')

            # ---------------- 删除确认弹窗中的 Skill 引用数（M01-C4）
            impact = api_get(f"/documents/{doc2['doc_id']}/deletion-impact")
            check("文件删除影响返回真实 Skill 引用数", impact["skill_reference_count"] == 1)
            page.click('button[aria-label="知识管理"]')
            page.wait_for_selector(f'text="{doc2["title"]}"', timeout=15000)
            page.locator(f'div:has-text("{doc2["title"]}")').locator('button:has-text("原文")').first.click()
            page.wait_for_selector('[data-testid="delete-doc-btn"]', timeout=10000)
            page.click('[data-testid="delete-doc-btn"]')
            page.wait_for_selector('[data-testid="delete-skill-refs"]', timeout=10000)
            text = page.locator('[data-testid="delete-skill-refs"]').inner_text()
            check("删除确认弹窗显示被 Skill 引用数量", "被 Skill 引用" in text and "1 个" in text and "复核" in text)
            page.screenshot(path=str(SHOTS / "m02e_07_delete_modal_skill_refs_1440x900.png"))
            page.click('[data-testid="confirm-delete-btn"]')
            page.wait_for_timeout(800)
            check("删除文件后引用它的 Skill 转为待复核",
                  api_get(f"/skill-factory/skills/{s_doc2}/workbench")["status"] == "needs_recheck")
            check("页面没有脚本错误", not errors)
            browser.close()
    finally:
        server.should_exit = True
        static.shutdown()
        for p in patches:
            p.stop()

    passed = sum(1 for _, ok in results if ok)
    print(f"\nM02-E UI 验证：{passed}/{len(results)} 通过")
    cleanup_test_db(TEST_DB)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
