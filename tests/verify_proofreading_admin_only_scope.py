import time
import threading
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
CLIENT_DIST = BASE_DIR / "client" / "dist"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIST), **kwargs)

    def log_message(self, format, *args):
        pass

def run_static_server(port=5180):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

def test_proofreading_scope_fix():
    app_url = "http://127.0.0.1:8766/api"

    # 1. 登录管理员
    login_res = requests.post(f"{app_url}/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
    assert login_res.status_code == 200
    token = login_res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. 检查审计条目
    item_id = "ki_s4a_8f92c639_ae64e5"
    item_detail = requests.get(f"{app_url}/knowledge/items/{item_id}", headers=headers).json()
    assert item_detail.get("document_access_scope") == "admin_only"
    print(f"--> 1. 目标知识条目校验正常: {item_detail['active_version']['title']}, 母文档={item_detail['document_title']}")

    # 3. 启动前端静态服务
    server = run_static_server(5180)
    front_url = "http://127.0.0.1:5180"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 监听 dialog，确保没有意外的保存失败 alert 弹出
        dialog_messages = []
        page.on("dialog", lambda d: [dialog_messages.append(d.message), d.accept()])

        print("--> 2. 注入 Token 并打开应用工作台...")
        page.goto(front_url)
        page.evaluate("(token) => localStorage.setItem('zhixing_token', token)", token)
        page.reload()

        page.wait_for_selector('text="文哲"', timeout=10000)
        page.wait_for_selector('text="[s4a_8f92c639] 审计条目"', timeout=10000)
        print("    工作台加载完成，定位到条目: [s4a_8f92c639] 审计条目")

        # 4. 点击打开核对抽屉/弹窗
        print("--> 3. 打开核对弹窗...")
        card = page.locator('[data-testid="knowledge-item-ki_s4a_8f92c639_ae64e5"]')
        card.scroll_into_view_if_needed()
        card.click()

        page.wait_for_selector('text=核对知识', timeout=8000)
        page.wait_for_selector('text=审计测试文件', timeout=8000)
        print("    核对弹窗加载成功")

        # 5. 展开业务标签与权限管理
        print("--> 4. 检查业务标签与权限管理组件...")
        scope_btn = page.locator('button:has-text("修改适用范围与权限")')
        if scope_btn.is_visible():
            scope_btn.click()
            page.wait_for_timeout(300)

        # 验证下拉框受控且 disabled
        select = page.locator('select:has(option[value="admin_only"])')
        assert select.is_visible()
        assert select.is_disabled(), "母文档为 admin_only 时，知识条目权限下拉框应处于 disabled 状态"
        assert page.locator('text="来源文件为管理员专享，知识条目权限不能比文件更开放"').is_visible()
        print("    权限选择器禁用与边界提示校验通过")

        # 6. 编辑标题并保存草稿
        print("--> 5. 编辑标题并测试保存草稿...")
        edit_btn = page.locator('button:has-text("修改")').first
        if edit_btn.is_visible():
            edit_btn.click()
            page.wait_for_timeout(200)

        # 触发 dirty 状态
        title_input = page.locator('input[value*="审计条目"]')
        if title_input.is_visible():
            title_input.fill("[s4a_8f92c639] 审计条目 - 已修复")
        else:
            # 或者通过全量编辑模式
            page.locator('button:has-text("编辑全部内容")').click()
            page.wait_for_timeout(200)
            page.locator('input').first.fill("[s4a_8f92c639] 审计条目 - 已修复")

        # 点击保存草稿
        save_draft_btn = page.locator('button:has-text("保存草稿")')
        save_draft_btn.click()
        page.wait_for_timeout(1000)

        # 验证没有抛出保存失败弹窗
        failed_alerts = [m for m in dialog_messages if "保存草稿失败" in m]
        assert len(failed_alerts) == 0, f"意外捕获到草稿保存失败弹窗: {failed_alerts}"
        print("    草稿保存成功，未触发任何报错拦截！")

        shot = SCREENSHOTS_DIR / "verify_proofreading_admin_only_scope_fixed.png"
        page.screenshot(path=str(shot))
        print(f"    已保存修复后界面截图: {shot}")

        browser.close()

    # 7. 恢复标题
    requests.put(
        f"{app_url}/knowledge/items/{item_id}/draft",
        headers=headers,
        json={
            "title": "[s4a_8f92c639] 审计条目",
            "statement": "核心陈述必须清晰且包含业务事实：审计关键词",
            "primary_category": "制度与标准",
            "access_scope": "admin_only",
            "revision_token": requests.get(f"{app_url}/knowledge/items/{item_id}", headers=headers).json()["active_version"]["revision_token"]
        }
    )
    print("--> 6. 恢复标题为基准状态")
    print("\n[ALL PASS] 权限边界与草稿保存 Bug 修复验证 100% 通过！")

if __name__ == "__main__":
    test_proofreading_scope_fix()
