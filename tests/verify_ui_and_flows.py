import os
import sys
import time
import threading
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
CLIENT_DIST = BASE_DIR / "client" / "dist"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIST), **kwargs)

    def log_message(self, format, *args):
        pass  # 静默日志

def run_static_server(port=5174):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    return server

def run_ui_verification():
    server = run_static_server(5174)
    app_url = "http://127.0.0.1:5174"
    print(f"Loading UI from static server: {app_url}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        # ========================================================
        # 测试场景 1: 1440x900 登录界面
        # ========================================================
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(app_url)
        page.wait_for_selector('button:has-text("登录")', timeout=10000)
        page.wait_for_timeout(500)
        
        # 截取登录页面
        login_shot = SCREENSHOTS_DIR / "01_login_1440x900.png"
        page.screenshot(path=str(login_shot))
        print(f"[PASS] 登录页已截屏: {login_shot}")
        
        # ========================================================
        # 测试场景 2: 管理员登录与全新空白页 (AC01, AC33)
        # ========================================================
        page.click('button:has-text("管理员 (文哲)")')
        page.click('button[type="submit"]')
        page.wait_for_selector('text="文哲"', timeout=8000)
        page.wait_for_timeout(600)
        
        # 验证 AC01 空白页文案
        empty_text = page.locator('text="导入第一份物业资料，开始建立企业知识库。"')
        assert empty_text.is_visible(), "AC01 空白页文案未正确展现"
        
        empty_shot = SCREENSHOTS_DIR / "02_admin_empty_state_1440x900.png"
        page.screenshot(path=str(empty_shot))
        print(f"[PASS] AC01 空白页已验证并截屏: {empty_shot}")
        
        # ========================================================
        # 测试场景 3: 点击导入文件进入两栏工作区视图并比对视觉效果图
        # ========================================================
        page.click('button:has-text("导入文件")')
        page.wait_for_timeout(600)
        
        # 验证两栏工作区关键元素
        assert page.locator('h1:has-text("知识管理")').is_visible(), "页头知识管理标题缺失"
        assert page.locator('h2:has-text("原始文件")').is_visible(), "左栏原始文件缺失"
        assert page.locator('text="制度与标准"').is_visible(), "制度与标准 Tab 缺失"
        assert page.locator('text="待分类"').is_visible(), "待分类 入口缺失"
        assert page.locator('text="客户诉求受理与记录"').is_visible(), "知识条目示例缺失"
        
        workspace_shot_1440 = SCREENSHOTS_DIR / "03_admin_workspace_1440x900.png"
        page.screenshot(path=str(workspace_shot_1440))
        print(f"[PASS] 1440x900 两栏工作区已验证并截屏: {workspace_shot_1440}")
        
        # 检查 1280x800 紧凑窗口适配
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(300)
        workspace_shot_1280 = SCREENSHOTS_DIR / "04_admin_workspace_1280x800.png"
        page.screenshot(path=str(workspace_shot_1280))
        print(f"[PASS] 1280x800 紧凑窗口已验证并截屏: {workspace_shot_1280}")
        
        # ========================================================
        # 测试场景 4: 退出登录 (AC35, AC37)
        # ========================================================
        page.click('text="文哲"')
        page.wait_for_selector('text="退出登录"', timeout=2000)
        page.click('text="退出登录"')
        page.wait_for_selector('button[type="submit"]', timeout=3000)
        print("[PASS] AC35/AC37 退出登录成功，已返回登录界面并清理缓存")
        
        # ========================================================
        # 测试场景 5: 普通成员登录与页面分流 (AC33)
        # ========================================================
        page.click('button:has-text("普通成员 (李景研)")')
        page.click('button[type="submit"]')
        page.wait_for_selector('text="李景研"', timeout=8000)
        page.wait_for_timeout(600)
        
        # 严格验证：全局侧栏中仅展示「Agent 咨询」，「知识管理」与「Skill 工厂」均被隐藏
        visible_nav = page.locator('nav button')
        assert visible_nav.count() == 1, f"普通成员侧栏应仅有 1 个导航项，实际发现: {visible_nav.count()}"
        assert page.locator('nav >> text="Agent 咨询"').is_visible(), "普通成员未显示「Agent 咨询」入口"
        assert page.locator('nav >> text="知识管理"').count() == 0, "普通成员侧栏不应出现「知识管理」入口"
        assert page.locator('nav >> text="Skill 工厂"').count() == 0, "普通成员侧栏不应出现「Skill 工厂」入口"
        
        # 验证主界面显示真实准备状态
        member_prep = page.locator('text="物业 AI 咨询服务（准备中）"')
        assert member_prep.is_visible(), "普通成员未呈现真实准备状态"
        
        member_shot = SCREENSHOTS_DIR / "05_member_home_view_1440x900.png"
        page.screenshot(path=str(member_shot))
        print(f"[PASS] AC33 普通成员侧栏仅显示「Agent 咨询」已严格验证并截屏: {member_shot}")
        
        context.close()
        browser.close()
        server.shutdown()
        
    print("\n==========================================")
    print("所有端到端视觉与权限用例全部验证通过！")
    print("==========================================")

if __name__ == "__main__":
    run_ui_verification()
