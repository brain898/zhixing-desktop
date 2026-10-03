"""真实演示库与生产前端核验；不调用模型、不改候选，凭据只在内存中。"""
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import urlsplit

import review_m02f_demo as demo  # 配置已经准备好的独立演示库
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from main import app

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts/m02f/demo_ui"
OUT.mkdir(parents=True, exist_ok=True)


class Static(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "client/dist"), **kwargs)

    def log_message(self, *_args):
        pass


def main():
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"}).json()
    headers = {"Authorization": "Bearer " + login["token"]}
    stats = client.get("/api/skill-factory/statistics", headers=headers).json()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Static)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks, errors = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))

            def forward(route):
                req = route.request
                url = urlsplit(req.url)
                response = client.request(req.method, url.path + ("?" + url.query if url.query else ""),
                                          headers={k: v for k, v in req.headers.items() if k.lower() in {"authorization", "content-type"}},
                                          content=req.post_data)
                route.fulfill(status=response.status_code, content_type=response.headers.get("content-type", "application/json"), body=response.content)
            page.route("**/api/**", forward)
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.evaluate("t => localStorage.setItem('zhixing_token',t)", login["token"])
            page.reload()
            page.get_by_role("button", name="Skill 工厂", exact=True).click()
            page.get_by_test_id("tab-skill-statistics").click()
            page.get_by_test_id("statistics-summary").wait_for()
            pane = page.get_by_test_id("skill-statistics")
            assert f"候选 {stats['summary']['candidate_count']} 个" in pane.inner_text()
            checks.append("候选数量与真实统计一致")
            assert pane.get_by_test_id("statistics-row").count() == len(stats["by_scene"])
            checks.append("七个实际场景汇总")
            page.screenshot(path=str(OUT / "real_statistics_scene_1440x900.png"), full_page=True)
            page.get_by_label("统计汇总方式").select_option("batch")
            assert pane.get_by_test_id("statistics-row").count() == len(stats["by_batch"])
            checks.append("七个实际生成批次汇总")
            page.screenshot(path=str(OUT / "real_statistics_batch_1440x900.png"), full_page=True)
            page.get_by_test_id("tab-skill-review").click()
            page.get_by_test_id("skill-list-row").first.wait_for()
            row = page.locator('[data-skill-id="sk_42abb10a64df"]')
            row.click()
            page.get_by_test_id("skill-workbench").wait_for()
            page.screenshot(path=str(OUT / "real_approved_review_1440x900.png"), full_page=True)
            checks.append("真实已修订候选打开工作台")
            assert not errors, errors
            checks.append("无前端脚本错误")
            browser.close()
        (OUT / "result.json").write_text(json.dumps({"kind": "real_demo_records_no_new_model_calls", "passed": len(checks), "failed": 0,
                                                    "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(checks)} passed, 0 failed")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
