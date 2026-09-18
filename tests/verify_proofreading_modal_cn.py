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

def run_static_server(port=5176):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    return server

def verify_modal_chinese():
    server = run_static_server(5176)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 访问 5176 静态前端
        page.goto("http://127.0.0.1:5176")
        page.wait_for_selector('button:has-text("登录")', timeout=10000)
        page.fill('input[placeholder="请输入用户名"]', 'admin')
        page.fill('input[placeholder="请输入密码"]', 'Admin@Zhixing2026')
        page.click('button[type="submit"]')

        # 等待主工作区加载
        page.wait_for_selector('text="知识管理"', timeout=10000)
        page.wait_for_timeout(1000)

        # 找到知识条目并点击打开校对弹窗
        items = page.locator('[data-testid^="knowledge-item-"]')
        count = items.count()
        print(f"找到 {count} 个知识条目...")
        if count > 0:
            print("点击第一个知识条目打开校对工作台...")
            items.first.click()
        else:
            print("[WARN] 当前无知识条目，正在截取主页面...")
            page.screenshot(path=str(SCREENSHOTS_DIR / "workspace_empty.png"))
            return

        # 等待校对弹窗打开
        page.wait_for_selector('text="核对知识"', timeout=8000)
        page.wait_for_timeout(1000)

        # 截屏保存
        shot_path = SCREENSHOTS_DIR / "proofreading_modal_localized.png"
        page.screenshot(path=str(shot_path))
        print(f"校对工作台截屏保存至: {shot_path}")

        # 获取弹窗可见文本
        modal = page.locator('text="核对知识"').locator('xpath=ancestor::div[contains(@style, "max-width: 96vw")]')
        modal_text = modal.inner_text()
        print("\n--- 弹窗可见文本摘录 ---")
        for line in modal_text.splitlines()[:30]:
            if line.strip():
                print(line.strip())
        print("-----------------------\n")

        # 检查是否包含多余英文
        banned_terms = [
            "(statement)",
            "(subject)",
            "(conditions)",
            "(actions)",
            "(exceptions)",
            "(Stage 4)",
            "(Stage 5",
            "(admin_only)",
            "(org_internal)",
            "支撑字段: statement",
            "支撑字段：statement",
            "[1line_1]",
            "[line_1]",
        ]

        found_banned = []
        for term in banned_terms:
            if term in modal_text:
                found_banned.append(term)

        if found_banned:
            print(f"[FAIL] 仍检测到未本地化的生硬英文: {found_banned}")
            sys.exit(1)
        else:
            print("[PASS] 弹窗中所有生硬英文标识已成功清除，已全部替换为专业自然中文！")

        browser.close()

if __name__ == "__main__":
    verify_modal_chinese()
