import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "client" / "dist"

class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", 5181), StaticHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()

base_item = {
    "id": "ki_ui_activation", "document_id": "doc_ui", "document_title": "状态演示资料",
    "access_scope": "org_internal", "lifecycle_status": "active",
    "created_at": "2026-09-19T00:00:00+00:00", "updated_at": "2026-09-19T00:00:00+00:00",
    "active_version_id": "kv_old", "source_document_version_id": "dv_ui", "version_number": 1,
    "title": "电梯巡检规范", "content": "测试内容", "primary_category": "制度与标准",
    "atom_type": "规则", "subject": "物业项目", "statement": "每日完成电梯巡检。",
    "conditions": [], "actions": ["执行巡检"], "exceptions": [], "metric_definition": None,
    "case_details": None, "field_states": {}, "quality_flags": [], "customer_types": [],
    "business_scenes": [], "problem_tags": [], "source_anchors": [], "valid_from": None,
    "valid_until": None, "review_status": "confirmed", "index_status": "ready",
    "revision_token": "rev_old", "extraction_context": {}, "related_cases": [], "evidence_count": 1,
}

stats = {
    "total": 1, "category_counts": {"制度与标准": 1, "方法与工具": 0, "项目案例": 0, "指标数据": 0, "专家经验": 0},
    "unclassified_count": 0, "pending_review_count": 0, "confirmed_count": 1,
    "active_count": 1, "disabled_count": 0,
}

doc = {
    "id": "doc_ui", "title": "状态演示资料", "active_version_id": "dv_ui",
    "created_at": "2026-09-19T00:00:00+00:00", "updated_at": "2026-09-19T00:00:00+00:00",
    "version_count": 1, "version_label": "v1", "file_name": "demo.md", "file_type": "md",
    "file_size": 100, "uploaded_at": "2026-09-19T00:00:00+00:00", "processing_status": "completed",
    "error_summary": None, "task_id": None, "task_status": None, "attempt_count": 1, "block_count": 1,
}

list_calls = {"count": 0}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('zhixing_token', 'ui-test-token')")
    page = context.new_page()

    def api_route(route):
        url = urlparse(route.request.url)
        path = url.path
        if path == "/api/auth/me":
            route.fulfill(json={
                "id": "usr_admin_001", "organization_id": "org_greentown",
                "organization_name": "绿城咨询", "username": "admin",
                "display_name": "文哲", "role": "admin", "account_status": "active",
            })
        elif path == "/api/documents":
            route.fulfill(json=[doc])
        elif path == "/api/knowledge/tags":
            route.fulfill(json={"customer_types": [], "business_scenes": [], "problem_tags": []})
        elif path == "/api/knowledge/items":
            list_calls["count"] += 1
            if list_calls["count"] <= 2:
                item = dict(base_item, has_draft_version=True, draft_version_number=2,
                            pending_review_status="confirmed", pending_index_status="indexing")
            else:
                item = dict(base_item, active_version_id="kv_new", version_number=2,
                            has_draft_version=False, draft_version_number=None,
                            pending_review_status=None, pending_index_status=None)
            route.fulfill(json={"items": [item], "stats": stats})
        else:
            route.fulfill(status=404, json={"detail": f"unmocked {path}"})

    page.route("http://127.0.0.1:8766/api/**", api_route)
    page.goto("http://127.0.0.1:5181")
    page.wait_for_selector('[data-testid="activating-status-badge"]', timeout=5000)
    activating_text = page.locator('[data-testid="activating-status-badge"]').inner_text()
    assert "正在启用" in activating_text and "建立索引" in activating_text

    page.wait_for_selector('[data-testid="activating-status-badge"]', state="detached", timeout=5000)
    page.wait_for_selector('text="已确认启用"', timeout=5000)
    assert list_calls["count"] >= 3, f"未发生自动轮询: {list_calls['count']}"

    print(f"[PASS] activating intermediate state shown: {activating_text}")
    print(f"[PASS] polling transitioned automatically to enabled; list calls={list_calls['count']}")
    browser.close()

server.shutdown()
