import io
import sys
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

def run_static_server(port=5179):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

def test_document_delete_ui():
    app_url = "http://127.0.0.1:8766/api"
    
    # 1. 登录管理员并上传待删除的测试文档
    print("--> 1. 准备端到端测试专用资料...")
    login_res = requests.post(f"{app_url}/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
    assert login_res.status_code == 200, f"登录失败: {login_res.text}"
    token = login_res.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    test_file_name = f"_删除验证_{int(time.time())}.txt"
    test_content = f"这是一份用于端到端验证文件详情弹窗内删除按钮及其真实影响提示的专属测试资料。时间戳：{time.time()}。\n第一条：仅供删除验证使用。"
    upload_res = requests.post(
        f"{app_url}/documents/upload",
        headers=headers,
        files={"file": (test_file_name, io.BytesIO(test_content.encode("utf-8")), "text/plain")}
    )
    assert upload_res.status_code == 200, f"上传失败: {upload_res.text}"
    doc_id = upload_res.json()["document_id"]
    print(f"    测试资料上传成功: ID={doc_id}, 标题={test_file_name}")

    # 等待解析完成
    for _ in range(20):
        time.sleep(0.2)
        doc_info = requests.get(f"{app_url}/documents/{doc_id}", headers=headers).json()
        if doc_info.get("versions") and doc_info["versions"][0]["processing_status"] in ("completed", "failed"):
            break

    # 2. 启动前端静态服务
    server = run_static_server(5179)
    front_url = "http://127.0.0.1:5179"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        print("--> 2. 注入 Token 并打开应用工作台...")
        page.goto(front_url)
        page.evaluate("(token) => localStorage.setItem('zhixing_token', token)", token)
        page.reload()

        page.wait_for_selector('text="文哲"', timeout=10000)
        page.wait_for_selector(f'text="{test_file_name}"', timeout=10000)
        print(f"    工作台加载完成，定位到测试文件: {test_file_name}")

        # 3. 找到该文件的“原文”按钮并点击打开 DocumentDetailModal
        print("--> 3. 打开 DocumentDetailModal 预览弹窗...")
        # 寻找包含该文件名称的卡片内的“原文”按钮
        preview_btn = page.locator(f'div:has-text("{test_file_name}")').locator('button:has-text("原文")').first
        preview_btn.click()

        # 等待详情弹窗打开，并确认删除按钮出现
        page.wait_for_selector('[data-testid="delete-doc-btn"]', timeout=8000)
        delete_btn = page.locator('[data-testid="delete-doc-btn"]')
        assert delete_btn.is_visible(), "未在详情弹窗中找到删除资料按钮 [data-testid='delete-doc-btn']"
        print("    详情弹窗与 [data-testid='delete-doc-btn'] 按钮验证成功")

        shot1 = SCREENSHOTS_DIR / "verify_doc_delete_01_detail_modal.png"
        page.screenshot(path=str(shot1))
        print(f"    已保存详情弹窗截屏: {shot1}")

        # 4. 点击删除资料按钮，触发 DeleteConfirmModal 弹窗
        print("--> 4. 点击删除按钮，弹出 DeleteConfirmModal 确认弹窗...")
        delete_btn.click()

        page.wait_for_selector('text=确认删除资料？', timeout=8000)
        # 验证真实影响读取成功
        page.wait_for_selector('text=历史版本：', timeout=8000)
        page.wait_for_selector('text=真实影响：', timeout=8000)
        assert page.locator('text=本操作采用逻辑删除').is_visible()

        confirm_btn = page.locator('[data-testid="confirm-delete-btn"]')
        # 等待按钮脱离 disabled 状态（impact 已返回）
        confirm_btn.wait_for(state="visible", timeout=5000)
        page.wait_for_function('() => !document.querySelector(\'[data-testid="confirm-delete-btn"]\').disabled', timeout=5000)
        assert confirm_btn.is_enabled(), "确认删除按钮应在真实影响加载后处于启用状态"

        shot2 = SCREENSHOTS_DIR / "verify_doc_delete_02_impact_modal.png"
        page.screenshot(path=str(shot2))
        print(f"    已保存删除影响弹窗截屏: {shot2}")

        # 5. 点击确认删除
        print("--> 5. 点击确认删除，验证文件移出列表...")
        confirm_btn.click()

        # 等待确认弹窗关闭
        page.wait_for_selector('text="确认删除资料？"', state="detached", timeout=8000)
        page.wait_for_timeout(600)

        # 验证侧栏中该测试文件已消失
        assert page.locator(f'text="{test_file_name}"').count() == 0, "删除后文件未从列表中移除"
        print("    资料列表已即时刷新，文件已成功移除")

        shot3 = SCREENSHOTS_DIR / "verify_doc_delete_03_deleted_result.png"
        page.screenshot(path=str(shot3))
        print(f"    已保存删除后工作区截屏: {shot3}")

        browser.close()

    # 6. 验证后端数据已为 404
    print("--> 6. 验证后端接口状态...")
    check_res = requests.get(f"{app_url}/documents/{doc_id}", headers=headers)
    assert check_res.status_code == 404, f"期望返回 404，实际返回 {check_res.status_code}"
    print(f"    后端返回 404 Not Found，逻辑删除确已生效")

    print("\n[ALL PASS] 文件删除按钮端到端 UI 验证及数据流全链路 100% 成功！")

if __name__ == "__main__":
    test_document_delete_ui()
