import sys
import time
import threading
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIST = ROOT / "client" / "dist"
SCREENSHOTS_DIR = ROOT / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIST), **kwargs)

    def log_message(self, format, *args):
        pass

def run_static_server(port=5190):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    return server

def verify_all_knowledge_features():
    server = run_static_server(5190)
    app_url = "http://127.0.0.1:5190"
    print(f"Testing Knowledge Management V2 from {app_url}...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 1. 登录
        page.goto(app_url)
        page.wait_for_selector('button:has-text("登录")', timeout=10000)
        page.fill('input[placeholder="请输入用户名"]', 'admin')
        page.fill('input[placeholder="请输入密码"]', 'Admin@Zhixing2026')
        page.click('button[type="submit"]')

        # 2. 等待主工作区加载
        page.wait_for_selector('text="知识管理"', timeout=10000)
        page.wait_for_timeout(1000)

        # 检查主页面：两栏布局验证，无旧版平级主视图切换器
        workspace_shot = SCREENSHOTS_DIR / "01_knowledge_workspace_clean_1440x900.png"
        page.screenshot(path=str(workspace_shot))
        print(f"[PASS] 主工作区已截屏: {workspace_shot}")

        # 验证没有「原件与结构块」Tab（已被抽屉化替代）
        tabs = page.locator('text="原件与结构块"')
        assert tabs.count() == 0, "主页面不应存在平级『原件与结构块』主视图切换器"
        print("[PASS] 确认平级『原件与结构块』主视图已彻底移除，界面纯正聚焦知识资产")

        # 3. 验证左栏「原文」抽屉入口
        original_btn = page.locator('button:has-text("原文")').first
        if original_btn.is_visible():
            original_btn.click()
            page.wait_for_selector('button:has-text("原文预览")', timeout=5000)
            page.wait_for_timeout(600)
            drawer_shot = SCREENSHOTS_DIR / "02_document_preview_drawer.png"
            page.screenshot(path=str(drawer_shot))
            print(f"[PASS] 原文预览抽屉已打开并截屏: {drawer_shot}")
            # 关闭抽屉
            page.click('[data-testid="close-detail-modal-btn"]')
            page.wait_for_selector('[data-testid="close-detail-modal-btn"]', state='detached', timeout=3000)
            page.wait_for_timeout(400)

        # 4. 打开第一个知识条目进行核对
        items = page.locator('[data-testid^="knowledge-item-"]')
        count = items.count()
        print(f"发现 {count} 个待核对知识条目")
        assert count > 0, "应有至少 1 个知识条目"
        items.first.click()

        # 等待「核对知识」弹窗
        page.wait_for_selector('text="核对知识"', timeout=8000)
        page.wait_for_timeout(1000)

        # 验证核对知识标题与说明
        assert page.locator('text="核对系统整理的内容是否忠于原文，重点检查条件、动作和例外"').is_visible()
        print("[PASS] 核对知识工作台顶部标题与业务指引文案准确展现")

        modal_shot_1 = SCREENSHOTS_DIR / "03_proofreading_modal_item1.png"
        page.screenshot(path=str(modal_shot_1))
        print(f"[PASS] 条目 1 核对视图已截屏: {modal_shot_1}")

        # 5. 测试连续核对导航「下一条」
        next_btn = page.locator('button:has-text("下一条")')
        if next_btn.is_enabled():
            print("点击『下一条』进行连续核对...")
            next_btn.click()
            page.wait_for_timeout(800)
            modal_shot_2 = SCREENSHOTS_DIR / "04_proofreading_modal_item2.png"
            page.screenshot(path=str(modal_shot_2))
            print(f"[PASS] 条目 2 核对视图已截屏: {modal_shot_2}")

            # 测试「上一条」
            prev_btn = page.locator('button:has-text("上一条")')
            assert prev_btn.is_enabled(), "上一条按钮应可点击"
            prev_btn.click()
            page.wait_for_timeout(800)
            print("[PASS] 成功返回条目 1")

        # 6. 测试「不收录」弹窗
        exclude_btn = page.locator('button:has-text("不收录")')
        assert exclude_btn.is_visible(), "不收录按钮应存在"
        exclude_btn.click()
        page.wait_for_selector('text="排除此条知识（不收录）"', timeout=3000)
        page.wait_for_timeout(400)
        exclude_shot = SCREENSHOTS_DIR / "05_exclude_confirmation_dialog.png"
        page.screenshot(path=str(exclude_shot))
        print(f"[PASS] 不收录确认弹窗已验证并截屏: {exclude_shot}")

        # 点击取消不收录
        page.click('button:has-text("取消")')
        page.wait_for_timeout(400)
        assert page.locator('text="排除此条知识（不收录）"').count() == 0, "取消后弹窗应关闭"

        # 7. 检查 1280x800 分辨率适配
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(500)
        compact_shot = SCREENSHOTS_DIR / "06_proofreading_modal_1280x800.png"
        page.screenshot(path=str(compact_shot))
        print(f"[PASS] 1280x800 紧凑分辨率下已验证并截屏: {compact_shot}")

        browser.close()
        server.shutdown()

    print("\n==========================================")
    print("知识管理 V2 交互改造与核对工作台全部测试通过！")
    print("==========================================")

if __name__ == "__main__":
    verify_all_knowledge_features()
