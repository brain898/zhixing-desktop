"""统计页生产构建 UI 验证。隔离合成记录，真实 API，不调用模型。"""
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright
from test_m02f_statistics import TestStatisticsAPI

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts/m02f/ui"
OUT.mkdir(parents=True, exist_ok=True)


class Static(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "client/dist"), **kwargs)

    def log_message(self, *_args):
        pass


def main():
    TestStatisticsAPI.setUpClass()
    fixture = TestStatisticsAPI
    server = ThreadingHTTPServer(("127.0.0.1", 0), Static)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    errors = []
    failed_stats = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda err: errors.append(str(err)))

            def forward(route):
                if failed_stats and "/statistics" in route.request.url:
                    route.fulfill(status=503, content_type="application/json", body='{"detail":"测试服务暂不可用"}')
                    return
                from urllib.parse import urlsplit
                req = route.request
                url = urlsplit(req.url)
                result = fixture.client.request(req.method, url.path + ("?" + url.query if url.query else ""),
                                                headers={k: v for k, v in req.headers.items() if k.lower() in {"authorization", "content-type"}},
                                                content=req.post_data)
                route.fulfill(status=result.status_code, headers={"content-type": result.headers.get("content-type", "application/json")},
                              body=result.content)
            page.route("**/api/**", forward)

            def open_as(username, password):
                login = fixture.client.post("/api/auth/login", json={"username": username, "password": password}).json()
                page.goto(f"http://127.0.0.1:{server.server_port}")
                page.evaluate("data => {localStorage.setItem('zhixing_token', data.token); localStorage.setItem('zhixing_user', JSON.stringify(data.user));}", login)
                page.reload()

            def check(name, condition):
                assert condition, name
                checks.append(name)

            open_as("admin", "Admin@Zhixing2026")
            page.get_by_text("Skill 工厂", exact=True).first.click()
            page.get_by_test_id("tab-skill-statistics").click()
            pane = page.get_by_test_id("skill-statistics")
            pane.get_by_test_id("statistics-summary").wait_for()
            check("实际候选数", "候选 2 个" in pane.inner_text())
            check("校验分子分母", "100.0%（2/2）" in pane.inner_text())
            check("没有审核记录不显示0%", "审核通过率 暂无数据" in pane.inner_text())
            check("没有修改记录不显示0", "平均修改字段 暂无数据" in pane.inner_text())
            check("按场景一行", pane.get_by_test_id("statistics-row").count() == 1)
            page.get_by_label("统计汇总方式").select_option("batch")
            check("按批次两行", pane.get_by_test_id("statistics-row").count() == 2)
            page.get_by_label("统计场景").select_option(fixture.scene)
            check("场景筛选保留两批", pane.get_by_test_id("statistics-row").count() == 2)
            pane.get_by_text("统计口径", exact=True).click()
            check("口径解释可展开", "排除模型重写" in pane.inner_text())
            page.screenshot(path=str(OUT / "statistics_1440x900.png"), full_page=True)
            page.set_viewport_size({"width": 1280, "height": 800})
            page.screenshot(path=str(OUT / "statistics_1280x800.png"), full_page=True)
            sizes = pane.locator("*").evaluate_all("els => els.filter(e => e.getClientRects().length && e.textContent.trim()).map(e => parseFloat(getComputedStyle(e).fontSize))")
            check("字号不小于12px", min(sizes) >= 12)
            failed_stats = True
            pane.get_by_role("button", name="刷新").click()
            pane.get_by_text("测试服务暂不可用", exact=False).wait_for()
            check("错误状态清楚可见", "测试服务暂不可用" in pane.inner_text())
            failed_stats = False
            pane.get_by_role("button", name="刷新").click()
            pane.get_by_test_id("statistics-summary").wait_for()
            check("重试恢复", "候选 2 个" in pane.inner_text())
            open_as("other_admin", "Other@Zhixing2026")
            page.get_by_text("Skill 工厂", exact=True).first.click()
            page.get_by_test_id("tab-skill-statistics").click()
            page.get_by_test_id("statistics-summary").wait_for()
            text = page.get_by_test_id("skill-statistics").inner_text()
            check("其他企业不见候选", "候选 0 个" in text and "本企业场景" not in text)
            check("空分母显示暂无数据", "校验通过率 暂无数据" in text)
            page.screenshot(path=str(OUT / "statistics_empty.png"), full_page=True)
            open_as("member", "Member@Zhixing2026")
            check("成员入口隐藏", page.get_by_text("Skill 工厂", exact=True).count() == 0)
            check("无脚本异常", not errors)
            browser.close()
        (OUT / "result.json").write_text(json.dumps({"passed": len(checks), "failed": 0, "checks": checks,
                                                    "kind": "synthetic_records_real_api_no_model"}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(checks)} passed, 0 failed")
    finally:
        server.shutdown()
        server.server_close()
        fixture.tearDownClass()


if __name__ == "__main__":
    main()
