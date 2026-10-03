from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
sys.path.insert(0, str(SERVER_DIR))
from auth import create_session

SHOT = ROOT / "screenshots" / "electron_version_governance_1440x900.png"
SHOT.parent.mkdir(parents=True, exist_ok=True)

admin_token = create_session("usr_admin_001", "org_greentown")
member_token = create_session("usr_member_001", "org_greentown")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    pages = [page for ctx in browser.contexts for page in ctx.pages]
    assert pages, "未找到 Electron BrowserWindow 页面"
    page = next((x for x in pages if x.url != "about:blank"), pages[0])
    page.evaluate("(token) => localStorage.setItem('zhixing_token', token)", admin_token)
    page.reload()
    page.wait_for_selector('h1:has-text("知识管理")', timeout=10000)
    page.wait_for_selector('[data-testid^="knowledge-item-"]', timeout=10000)

    items = page.locator('[data-testid^="knowledge-item-"]')
    assert items.count() > 0, "真实知识库没有可打开的知识条目"
    items.first.click()
    page.wait_for_selector('text="核对知识"', timeout=8000)

    version_button = page.locator('button:has-text("版本历史")')
    assert version_button.is_visible(), "知识详情缺少版本历史入口"
    version_button.click()
    panel = page.locator('[data-testid="version-history-panel"]')
    panel.wait_for(state="visible", timeout=5000)
    assert "历史版本不能直接修改" in panel.inner_text()

    history_buttons = panel.locator("button")
    assert history_buttons.count() > 0, "版本历史列表为空"
    history_buttons.first.click()
    page.wait_for_timeout(500)
    panel_text = panel.inner_text()
    assert "此视图只读" in panel_text
    page.screenshot(path=str(SHOT))

    # AC37：切换到成员会话后，管理员知识 UI 不得残留。
    page.evaluate("(token) => localStorage.setItem('zhixing_token', token)", member_token)
    page.reload()
    page.wait_for_selector('text="李景研"', timeout=8000)
    page.wait_for_timeout(300)
    assert page.locator('nav >> text="知识管理"').count() == 0
    assert page.locator('[data-testid^="knowledge-item-"]').count() == 0
    assert page.locator('nav >> text="Agent 咨询"').is_visible()

    # 验收后清空本地身份，不改变用户下次启动默认账号。
    page.evaluate("localStorage.removeItem('zhixing_token')")
    page.reload()
    page.wait_for_selector('button:has-text("登录")', timeout=5000)

    print("[PASS] Electron real UI: knowledge detail -> readonly version history")
    print("[PASS] Electron real UI: account switch clears admin knowledge UI (AC37)")
    print(f"[PASS] Screenshot: {SHOT}")
    browser.close()
