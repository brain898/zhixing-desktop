"""
M02-B UI 验证：Skill 工厂场景目录（PRD FR01~FR03、AC01、AC04、AC21 入口可见性、M01-C3）。

- 后端：在本进程内以隔离临时数据库启动真实 FastAPI 服务（端口 8799），不读写 server/data/zhixing.db。
- 前端：直接加载 client/dist 生产构建；页面对 http://127.0.0.1:8766/api 的请求由 Playwright 转发到 8799。
- 不调用大模型：归并建议页面使用直接写入隔离库的「构造建议」，仅用于界面验证。
- 截图保存到 screenshots/m02b_*.png。

用法：python tests/verify_m02b_scene_catalog_ui.py（需先 npm run build）
"""

import json
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_env_helper import cleanup_test_db, setup_test_db  # noqa: E402

TEST_DB = setup_test_db("m02b_ui")

import uvicorn  # noqa: E402
import scene_catalog as sc  # noqa: E402
from database import get_db  # noqa: E402
from main import app  # noqa: E402

CLIENT_DIST = BASE_DIR / "client" / "dist"
SHOTS = BASE_DIR / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)
API_PORT = 8799
STATIC_PORT = 5179
API = f"http://127.0.0.1:{API_PORT}/api"
ORG = "org_greentown"
PREFIX = "m02bui_" + uuid.uuid4().hex[:6]


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
    """构造已确认、索引可用的测试原子（界面验证只需标签召回，不建向量）。"""
    now = datetime.now(timezone.utc).isoformat()
    doc_id, ver_id = f"doc_{PREFIX}", f"ver_{PREFIX}"
    specs = [
        ("汛前排水设施检查", ["防汛管理"], "制度与标准", "confirmed"),
        ("排污泵运行检查", ["防汛管理", "工程查验"], "方法与工具", "confirmed"),
        ("地下室积水警戒线", ["防汛管理"], "指标数据", "confirmed"),
        ("承接查验资料核对", ["承接查验"], "制度与标准", "confirmed"),
        ("草坪修剪频次", ["绿化养护"], "方法与工具", "confirmed"),
        ("待核对的巡查要求", ["地下室积水巡查"], "制度与标准", "pending_review"),
    ]
    items = {}
    with get_db() as conn:
        conn.execute(
            """INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted,
               created_at, updated_at) VALUES (?, ?, 'M02-B 界面验证资料', ?, 'org_internal', 0, ?, ?)""",
            (doc_id, ORG, ver_id, now, now),
        )
        conn.execute(
            """INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name, file_type,
               file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
               VALUES (?, ?, ?, 'v1', 'm02b_ui.txt', 'txt', 100, ?, 'm02b_ui.txt', 'usr_admin_001', ?, 'completed')""",
            (ver_id, doc_id, ORG, uuid.uuid4().hex, now),
        )
        block_id = f"blk_{PREFIX}"
        conn.execute(
            """INSERT INTO source_blocks (id, document_version_id, organization_id, block_index, block_type,
               heading_path, paragraph_anchor, text_content, created_at)
               VALUES (?, ?, ?, 1, 'paragraph', '测试章节', '[line_1]', '汛期前应完成排水设施检查并记录结果。', ?)""",
            (block_id, ver_id, ORG, now),
        )
        for idx, (title, scenes, cat, review) in enumerate(specs):
            item_id, version_id = f"ki_{PREFIX}_{idx}", f"kv_{PREFIX}_{idx}"
            conn.execute(
                """INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope,
                   lifecycle_status, is_excluded, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'org_internal', 'active', 0, ?, ?)""",
                (item_id, doc_id, ORG, version_id, now, now),
            )
            conn.execute(
                """INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id,
                   version_number, title, content, primary_category, atom_type, subject, statement, conditions_json,
                   actions_json, exceptions_json, customer_types_json, business_scenes_json, problem_tags_json,
                   source_anchors_json, review_status, index_status, revision_token, created_at, created_by)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, '规则', '物业工程人员', ?, '[]', '["按规程执行"]', '[]', '[]', ?, '[]',
                           '["[line_1]"]', ?, ?, ?, ?, 'usr_admin_001')""",
                (version_id, item_id, ORG, ver_id, title, title + "的要求说明。", cat, title + "的要求说明。",
                 json.dumps(scenes, ensure_ascii=False), review,
                 "ready" if review == "confirmed" else "not_indexed", uuid.uuid4().hex, now),
            )
            conn.execute(
                """INSERT INTO knowledge_evidence (id, knowledge_version_id, source_block_id, organization_id,
                   field_name, excerpt, accuracy_level, created_at) VALUES (?, ?, ?, ?, 'statement', ?, 'exact', ?)""",
                (f"ev_{PREFIX}_{idx}", version_id, block_id, ORG, "汛期前应完成排水设施检查", now),
            )
            items[title] = item_id
    return items


def add_confirmed_atom(idx, title, tags, category):
    """追加一条已确认、索引可用的测试知识（模拟目录建立后新进入的知识）。"""
    now = datetime.now(timezone.utc).isoformat()
    item_id, version_id = f"ki_{PREFIX}_n{idx}", f"kv_{PREFIX}_n{idx}"
    with get_db() as conn:
        conn.execute(
            """INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope,
               lifecycle_status, is_excluded, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'org_internal', 'active', 0, ?, ?)""",
            (item_id, f"doc_{PREFIX}", ORG, version_id, now, now),
        )
        conn.execute(
            """INSERT INTO knowledge_versions (id, item_id, organization_id, source_document_version_id,
               version_number, title, content, primary_category, atom_type, subject, statement, conditions_json,
               actions_json, exceptions_json, customer_types_json, business_scenes_json, problem_tags_json,
               source_anchors_json, review_status, index_status, revision_token, created_at, created_by)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?, '规则', '物业工程人员', ?, '[]', '["按规程执行"]', '[]', '[]', ?, '[]',
                       '["[line_1]"]', 'confirmed', 'ready', ?, ?, 'usr_admin_001')""",
            (version_id, item_id, ORG, f"ver_{PREFIX}", title, title + "的要求说明。", category, title + "的要求说明。",
             json.dumps(tags, ensure_ascii=False), uuid.uuid4().hex, now),
        )


def insert_constructed_incremental_suggestion(flood_scene_id):
    """构造一条已完成的「整理新标签」建议（非模型输出），用于界面验证。"""
    admin = {"id": "usr_admin_001", "organization_id": ORG, "role": "admin", "account_status": "active"}
    groups = [
        {"existing": True, "target_scene_id": flood_scene_id, "name": "防汛管理", "description": "",
         "typical_problems": [], "tags": ["排水设施巡查"], "reason": "构造数据"},
        {"existing": False, "target_scene_id": None, "name": "消防管理", "description": "消防设施检查与消防演练",
         "typical_problems": ["消防演练怎么组织"], "tags": ["消防演练"], "reason": "构造数据"},
    ]
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        stats = sc.collect_unorganized_tags(conn, admin)
        conn.execute(
            """INSERT INTO scene_merge_suggestions (id, organization_id, mode, status, tag_stats_json, groups_json,
               unassigned_tags_json, model_name, prompt_version, attempt_count, requested_by, created_at, completed_at)
               VALUES (?, ?, 'incremental', 'completed', ?, ?, '[]', 'constructed-for-ui', 'scene-merge-incremental-v1',
                       1, 'usr_admin_001', ?, ?)""",
            (f"smg_{PREFIX}_inc", ORG, json.dumps(stats, ensure_ascii=False), json.dumps(groups, ensure_ascii=False),
             now, now),
        )


def insert_constructed_suggestion():
    """构造一条已完成的归并建议（非模型输出），用于界面验证逐组确认。"""
    admin = {"id": "usr_admin_001", "organization_id": ORG, "role": "admin", "account_status": "active"}
    groups = [
        {"name": "防汛与查验", "description": "汛前检查、排水与工程查验", "typical_problems": ["汛前检查项目有哪些"],
         "tags": ["防汛管理", "工程查验", "承接查验"], "reason": "构造数据：故意合并两个领域，供界面拆分"},
        {"name": "绿化养护", "description": "小区绿化修剪与养护", "typical_problems": ["草坪多久修剪一次"],
         "tags": ["绿化养护"], "reason": "构造数据"},
    ]
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        stats = sc.collect_scene_tag_stats(conn, admin)
        conn.execute(
            """INSERT INTO scene_merge_suggestions (id, organization_id, status, tag_stats_json, groups_json,
               unassigned_tags_json, model_name, prompt_version, attempt_count, requested_by, created_at, completed_at)
               VALUES (?, ?, 'completed', ?, ?, '[]', 'constructed-for-ui', 'scene-merge-v1', 1, 'usr_admin_001', ?, ?)""",
            (f"smg_{PREFIX}", ORG, json.dumps(stats, ensure_ascii=False), json.dumps(groups, ensure_ascii=False), now, now),
        )


def main():
    static, server = start_servers()
    admin_token = login("admin", "Admin@Zhixing2026")
    member_token = login("member", "Member@Zhixing2026")
    results = []

    def check(label, cond):
        results.append((label, bool(cond)))
        print(("  [通过] " if cond else "  [失败] ") + label)
        return cond

    jargon = ("原子", "召回", "归并", "别名", "语义", "模型", "DeepSeek", "提示词", "相关度", "constructed")

    def check_plain(label, text):
        found = [w for w in jargon if w in text]
        return check(f"{label}：页面没有技术术语{('（发现 ' + '、'.join(found) + '）') if found else ''}", not found)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})

            def forward(route):
                url = route.request.url.replace("http://127.0.0.1:8766/api", API)
                resp = route.fetch(url=url)
                route.fulfill(response=resp)

            context.route("http://127.0.0.1:8766/api/**", forward)
            page = context.new_page()
            front = f"http://127.0.0.1:{STATIC_PORT}"

            # 1. 管理员：无场景目录时的引导（AC01）
            page.goto(front)
            page.evaluate("(t) => localStorage.setItem('zhixing_token', t)", admin_token)
            page.reload()
            page.wait_for_selector('button[aria-label="Skill 工厂"]', timeout=15000)
            page.click('button[aria-label="Skill 工厂"]')
            page.wait_for_selector('text=先把知识整理成业务场景', timeout=10000)
            check("AC01 空目录显示引导文案", page.locator('.zx-empty-desc').is_visible())
            check("AC01 显示「开始整理场景」按钮", page.locator('[data-testid="init-scene-catalog"]').is_visible())
            check("AC01 空目录页没有场景卡片", page.locator('[data-testid="scene-card"]').count() == 0)
            check_plain("空目录引导", page.locator("main").inner_text())
            page.screenshot(path=str(SHOTS / "m02b_01_empty_catalog_guide_1440x900.png"))

            # 2. 归并建议逐组确认（构造建议，非模型输出）
            items = build_fixture()
            insert_constructed_suggestion()
            page.reload()
            page.click('button[aria-label="Skill 工厂"]')
            page.wait_for_selector('[data-testid="merge-suggestion-review"]', timeout=10000)
            check("归并建议逐组展示", page.locator('[data-testid="merge-group"]').count() == 2)
            check("确认页显示三步操作引导", page.locator('[data-testid="merge-steps"] li').count() == 3)
            check_plain("场景分组确认页", page.locator("main").inner_text())
            # 鼠标按住拖动标签（真实鼠标事件，不是菜单）
            def drag_mouse(chip_tag, to_x, to_y):
                box = page.locator(f'[data-testid="tag-chip"][data-tag="{chip_tag}"]').bounding_box()
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.mouse.down()
                page.mouse.move(box["x"] + box["width"] / 2 + 8, box["y"] + box["height"] / 2 + 8, steps=4)
                page.mouse.move(to_x, to_y, steps=12)
                page.mouse.up()
                page.wait_for_timeout(150)

            def center(locator):
                b = locator.bounding_box()
                return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

            def group_tags(i):
                return page.locator('[data-testid="merge-group"]').nth(i).locator('[data-testid="tag-chip"]').evaluate_all(
                    "els => els.map(e => e.dataset.tag)")

            groups_loc = page.locator('[data-testid="merge-group"]')
            # 1) 拖到另一个场景框：承接查验 -> 绿化养护
            drag_mouse("承接查验", *center(groups_loc.nth(1)))
            check("拖动标签到另一个场景框后移入该场景", "承接查验" in group_tags(1) and "承接查验" not in group_tags(0))
            # 2) 拖到页面空白处：自动新建一个场景框
            vw = page.viewport_size
            drag_mouse("承接查验", vw["width"] - 60, vw["height"] - 40)
            check("拖到空白处自动新建场景框", groups_loc.count() == 3 and group_tags(2) == ["承接查验"])
            page.screenshot(path=str(SHOTS / "m02b_11_drag_to_blank_new_scene_1440x900.png"))
            # 3) 再拖回原场景：临时新建的空框自动消失
            drag_mouse("承接查验", *center(groups_loc.nth(0)))
            check("拖回原场景后临时新框自动移除", groups_loc.count() == 2 and "承接查验" in group_tags(0))
            # 4) 拖到虚线区：防汛管理单独成为新场景
            drag_mouse("防汛管理", *center(page.locator('[data-testid="new-scene-dropzone"]')))
            check("标签上不再有下拉菜单", page.locator('[data-testid="tag-chip"] button').count() == 0)
            first_name = page.locator('[data-testid="merge-group"]').nth(0).locator('input[aria-label="场景名称"]')
            first_name.fill("工程查验")
            check("拆分后分组数为 3", page.locator('[data-testid="merge-group"]').count() == 3)
            page.screenshot(path=str(SHOTS / "m02b_02_merge_review_edit_1440x900.png"))
            page.click('[data-testid="merge-confirm"]')
            page.wait_for_selector('[data-testid="scene-card"]', timeout=20000)

            # 3. 场景卡片（FR03）与原子不足时按钮禁用（AC04）
            cards = page.locator('[data-testid="scene-card"]')
            names = [cards.nth(i).get_attribute("data-scene-name") for i in range(cards.count())]
            check("确认后生成 3 个场景卡片", sorted(names) == sorted(["工程查验", "防汛管理", "绿化养护"]))
            flood = page.locator('[data-testid="scene-card"][data-scene-name="防汛管理"]')
            green = page.locator('[data-testid="scene-card"][data-scene-name="绿化养护"]')
            check("防汛管理可用知识 3 条", flood.locator('[data-testid="scene-available-count"]').inner_text().strip() == "3")
            check("卡片显示知识类型", flood.locator('[data-testid="scene-coverage"]').inner_text() == "制度 1 · 方法 1 · 指标 1")
            check("卡片显示已有 Skill 为暂无", flood.locator('[data-testid="scene-skills"]').inner_text() == "暂无")
            # M02-C 已接通生成：本脚本不配置在线服务，按钮不可用并用大白话说明
            check("未配置生成服务时生成按钮不可用", flood.locator('[data-testid="scene-generate"]').is_disabled())
            check("未配置生成服务时提示原因",
                  flood.locator('[data-testid="scene-generate-hint"]').inner_text() == "生成服务暂未配置，暂时不能生成 Skill")
            check("绿化养护可用知识 1 条", green.locator('[data-testid="scene-available-count"]').inner_text().strip() == "1")
            check("AC04 知识不足时生成按钮不可用", green.locator('[data-testid="scene-generate"]').is_disabled())
            check("AC04 知识不足时提示原因",
                  green.locator('[data-testid="scene-generate-hint"]').inner_text() == "可用知识不足 3 条，请先在知识管理中导入或确认相关资料")
            check_plain("场景卡片页", page.locator("main").inner_text())
            page.screenshot(path=str(SHOTS / "m02b_03_scene_cards_1440x900.png"))
            green.screenshot(path=str(SHOTS / "m02b_04_insufficient_atoms_card.png"))

            # 查看知识弹窗：不显示相关度分数与术语
            flood.locator('button:has-text("查看知识")').click()
            page.wait_for_selector('[data-testid="recall-summary"]', timeout=10000)
            modal_text = page.locator('[role="dialog"]').inner_text()
            check("查看知识弹窗列出 3 条知识", page.locator('[data-testid="recall-atom"]').count() == 3)
            check("查看知识弹窗说明来源", "共 3 条，都带有这个场景的标签" in page.locator('[data-testid="recall-summary"]').inner_text())
            check_plain("查看知识弹窗", modal_text)
            page.screenshot(path=str(SHOTS / "m02b_08_scene_knowledge_modal_1440x900.png"))
            page.click('[role="dialog"] button[aria-label="关闭"]')

            # 4. 场景目录管理：场景列表与待归并标签
            with get_db() as conn:
                sc.register_unmatched_scene_tags(conn, ORG, ["地下室积水巡查"], items["待核对的巡查要求"],
                                                 f"kv_{PREFIX}_5", "extraction", title="待核对的巡查要求")
            page.click('[data-testid="tab-scene-catalog"]')
            page.wait_for_selector('[data-testid="scene-catalog-manager"]', timeout=10000)
            page.wait_for_selector('[data-testid="pending-tag-row"]', timeout=10000)
            check("管理场景列出 3 个场景", page.locator('[data-testid="scene-row"]').count() == 3)
            check("待处理标签可见", "地下室积水巡查" in page.locator('[data-testid="pending-tags"]').inner_text())
            check_plain("管理场景页", page.locator("main").inner_text())
            page.screenshot(path=str(SHOTS / "m02b_05_catalog_manager_1440x900.png"), full_page=True)

            # 4b. 目录建立后又进来新知识：提示条 + 整理新标签
            add_confirmed_atom(1, "排水设施日常巡查", ["排水设施巡查"], "方法与工具")
            add_confirmed_atom(2, "消防演练组织要求", ["消防演练"], "制度与标准")
            page.reload()
            page.click('button[aria-label="Skill 工厂"]')
            page.wait_for_selector('[data-testid="new-tags-bar"]', timeout=10000)
            bar_text = page.locator('[data-testid="new-tags-bar"]').inner_text()
            check("新知识进入后显示「有 2 个新标签」提示条", "有 2 个新标签还没归入场景" in bar_text)
            check_plain("新标签提示条", bar_text)
            page.screenshot(path=str(SHOTS / "m02b_09_new_tags_bar_1440x900.png"))
            # 本脚本不配置模型：点击后应给出大白话提示，而不是技术报错
            page.click('[data-testid="organize-new-tags"]')
            page.wait_for_selector('text=暂时无法自动整理场景，可以先手动新建场景', timeout=10000)
            check("未配置模型时给出大白话提示", True)

            with get_db() as conn:
                flood_id = next(x["scene_id"] for x in sc.list_scenes(conn, ORG) if x["name"] == "防汛管理")
            insert_constructed_incremental_suggestion(flood_id)
            page.reload()
            page.click('button[aria-label="Skill 工厂"]')
            page.wait_for_selector('[data-testid="merge-suggestion-review"][data-mode="incremental"]', timeout=10000)
            check("整理新标签页区分已有场景与新场景",
                  page.locator('[data-testid="merge-group"][data-existing="true"]').count() == 1
                  and page.locator('[data-testid="merge-group"][data-existing="false"]').count() == 1)
            check_plain("整理新标签页", page.locator("main").inner_text())
            # 拖到页面上没有出现的已有场景「绿化养护」，出现对应的已有场景框
            drag_mouse("消防演练", *center(page.locator('[data-testid="other-scene-target"][data-scene-name="绿化养护"]')))
            check("拖到其他已有场景后出现该场景框",
                  page.locator('[data-testid="merge-group"][data-existing="true"]').count() == 2)
            # 再拖回「消防管理」，临时的已有场景框自动消失
            drag_mouse("消防演练", *center(page.locator('[data-testid="merge-group"][data-existing="false"]')))
            check("拖回后临时已有场景框自动移除",
                  page.locator('[data-testid="merge-group"][data-existing="true"]').count() == 1)
            page.screenshot(path=str(SHOTS / "m02b_10_organize_new_tags_1440x900.png"))
            page.click('[data-testid="merge-confirm"]')
            page.wait_for_selector('[data-testid="scene-card"][data-scene-name="消防管理"]', timeout=20000)
            check("确认后新建场景「消防管理」", True)
            flood = page.locator('[data-testid="scene-card"][data-scene-name="防汛管理"]')
            check("已有场景「防汛管理」可用知识增加到 4 条",
                  flood.locator('[data-testid="scene-available-count"]').inner_text().strip() == "4")
            check("整理完成后提示条消失", page.locator('[data-testid="new-tags-bar"]').count() == 0)

            # 5. M01-C3：核对弹窗业务场景可从目录选择，保留手动输入
            page.click('button[aria-label="知识管理"]')
            card = page.locator(f'[data-testid="knowledge-item-{items["待核对的巡查要求"]}"]')
            card.wait_for(timeout=15000)
            card.scroll_into_view_if_needed()
            card.click()
            page.wait_for_selector('text=核对知识', timeout=10000)
            scope_btn = page.locator('button:has-text("修改适用范围与权限")')
            try:
                scope_btn.wait_for(timeout=10000)
                scope_btn.click()
                page.wait_for_selector('[data-testid="scene-catalog-picker"]', timeout=10000)
            except Exception:
                page.screenshot(path=str(SHOTS / "m02b_debug_proofreading_failure.png"))
                raise
            options = page.locator('[data-testid="scene-catalog-picker"] select option').all_inner_texts()
            check("C3 核对弹窗可从目录选择场景", {"工程查验", "防汛管理", "绿化养护"} <= set(options))
            check("C3 保留手动输入框", page.locator('input[placeholder*="没有合适的场景时"]').is_visible())
            page.select_option('select[aria-label="从场景目录选择业务场景"]', "防汛管理")
            check("C3 选择后标签加入业务场景", page.locator('text=场景: 防汛管理').is_visible())
            page.screenshot(path=str(SHOTS / "m02b_06_proofreading_scene_picker_1440x900.png"))

            # 6. 普通成员：入口不可见，接口拒绝（AC21 场景目录部分）
            member_page = context.new_page()
            member_page.goto(front)
            member_page.evaluate("(t) => localStorage.setItem('zhixing_token', t)", member_token)
            member_page.reload()
            member_page.wait_for_selector('button[aria-label="Agent 咨询"]', timeout=15000)
            check("成员侧栏不显示 Skill 工厂", member_page.locator('button[aria-label="Skill 工厂"]').count() == 0)
            check("成员侧栏不显示知识管理", member_page.locator('button[aria-label="知识管理"]').count() == 0)
            api_status = requests.get(f"{API}/skill-factory/scene-catalog",
                                      headers={"Authorization": f"Bearer {member_token}"}, timeout=10).status_code
            check("成员访问场景目录接口返回 403", api_status == 403)
            member_page.screenshot(path=str(SHOTS / "m02b_07_member_no_skill_factory_1440x900.png"))
            browser.close()
    finally:
        server.should_exit = True
        static.shutdown()
        time.sleep(0.5)
        cleanup_test_db(TEST_DB)

    failed = [label for label, ok in results if not ok]
    print(f"\n结果：{len(results) - len(failed)}/{len(results)} 项通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
