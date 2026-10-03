import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
CLIENT_DIST = BASE_DIR / "client" / "dist"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIST), **kwargs)

    def log_message(self, format, *args):
        pass


def run_static_server(port=5178):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


MOCK_SCRIPT = r"""
localStorage.setItem('zhixing_token', 'stage4c-ui-token');
window.__apiRequests = [];
window.__searchRequests = [];
window.__failAttempts = 0;

const adminUser = {
  id: 'usr_admin', organization_id: 'org_greentown', organization_name: '绿城咨询',
  username: 'admin', display_name: '文哲', role: 'admin', account_status: 'active'
};
const documentList = [{
  id: 'doc1', title: '客户投诉处理制度', active_version_id: 'docv-new',
  created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T01:00:00Z',
  version_count: 2, version_label: 'v2', file_name: '客户投诉处理制度_v2.txt',
  file_type: 'txt', file_size: 1200, uploaded_at: '2026-09-18T01:00:00Z',
  processing_status: 'completed', error_summary: null, task_id: null,
  task_status: 'completed', attempt_count: 1, block_count: 3
}];

const maintenanceItem = {
  id: 'ki-pending', document_id: 'doc1', document_title: '客户投诉处理制度',
  access_scope: 'org_internal', lifecycle_status: 'active',
  created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z',
  active_version_id: 'kv-pending', source_document_version_id: 'docv-new',
  version_number: 1, title: '待核对维护知识', content: '维护浏览内容',
  primary_category: '制度与标准', atom_type: '规则', subject: '客服人员',
  statement: '这是维护浏览中的待核对内容。', conditions: [], actions: [], exceptions: [],
  metric_definition: null, case_details: null, field_states: {}, quality_flags: [],
  customer_types: ['住宅物业'], business_scenes: ['客诉处理'], problem_tags: ['投诉未闭环'],
  source_anchors: ['[line_2]'], valid_from: null, valid_until: null,
  review_status: 'pending_review', index_status: 'not_indexed', revision_token: 'rev',
  extraction_context: {}, related_cases: [], evidence_count: 1
};

const maintenanceResponse = {
  items: [maintenanceItem],
  stats: {
    total: 1, category_counts: {'制度与标准': 1, '方法与工具': 0, '项目案例': 0, '指标数据': 0, '专家经验': 0},
    unclassified_count: 0, pending_review_count: 1, confirmed_count: 0, active_count: 1, disabled_count: 0
  }
};
function makeResult(q, versionId, title) {
  return {
    item_id: 'ki-' + versionId, version_id: versionId, version_number: 3,
    title, primary_category: '制度与标准', atom_type: '规则', subject: '客服人员',
    statement: q + ' 的核心陈述', content: q + ' 的完整知识内容',
    document_id: 'doc1', document_title: '客户投诉处理制度', document_version_label: 'v1',
    conditions: ['收到客户投诉'], actions: ['登记工单', '在约定时限内反馈'], exceptions: ['恶意重复投诉转人工复核'],
    metric_definition: null, case_details: null, customer_types: ['住宅物业'],
    business_scenes: ['客诉处理'], problem_tags: ['投诉未闭环'],
    valid_from: null, valid_until: null, access_scope: 'org_internal', lifecycle_status: 'active',
    source: {
      document_id: 'doc1', document_title: '客户投诉处理制度',
      document_version_id: 'docv-old', document_version_label: 'v1',
      file_name: '客户投诉处理制度_v1.txt', source_anchors: ['[line_12]']
    },
    evidence: [{
      id: 'ev-' + versionId, source_block_id: 'block-old-12', field_name: 'statement',
      excerpt: '投诉事项应登记并形成闭环。',
      accuracy_level: 'high',
      source_locator: {block_index: 12, block_type: 'paragraph', heading_path: '第三章 > 投诉处理', page_number: null, paragraph_anchor: '[line_12]'}
    }],
    evidence_count: 1,
    matched_snippets: [q + ' 命中的投诉处理片段'],
    matched_fragments: [{
      fragment_key: 'statement', fragment_type: 'statement', channels: ['keyword', 'dense'],
      rank: 1, dense_similarity: 0.82, snippet: q + ' 命中的投诉处理片段',
      evidence_ids: ['ev-' + versionId]
    }],
    score: 0.031
  };
}

function jsonResponse(data, status=200, delay=0) {
  return new Promise(resolve => setTimeout(() => resolve(new Response(JSON.stringify(data), {
    status,
    headers: {'Content-Type': 'application/json'}
  })), delay));
}
window.fetch = (input, init={}) => {
  const raw = typeof input === 'string' ? input : input.url;
  const url = new URL(raw, location.href);
  if (!url.href.startsWith('http://127.0.0.1:8766/api')) {
    return Promise.reject(new Error('Unexpected external request: ' + url.href));
  }
  window.__apiRequests.push(url.pathname + url.search);

  if (url.pathname === '/api/auth/me') return jsonResponse(adminUser);
  if (url.pathname === '/api/documents') return jsonResponse(documentList);
  if (url.pathname === '/api/knowledge/tags') {
    return jsonResponse({
      customer_types: ['住宅物业', '商业物业'],
      business_scenes: ['客诉处理', '工程巡检'],
      problem_tags: ['投诉未闭环', '响应超时']
    });
  }
  if (url.pathname === '/api/knowledge/items') return jsonResponse(maintenanceResponse);

  if (url.pathname === '/api/knowledge/search') {
    const snapshot = {};
    for (const [key, value] of url.searchParams.entries()) {
      if (!snapshot[key]) snapshot[key] = [];
      snapshot[key].push(value);
    }
    window.__searchRequests.push(snapshot);
    const q = url.searchParams.get('q') || '';
    if (q === 'A') return jsonResponse({query: q, total: 1, items: [makeResult(q, 'kv-a', 'A 晚返回结果')]}, 200, 650);
    if (q === 'B') return jsonResponse({query: q, total: 1, items: [makeResult(q, 'kv-b', 'B 最新结果')]}, 200, 60);
    if (q === 'NONE') return jsonResponse({query: q, total: 0, items: []}, 200, 40);
    if (q === 'FAIL') {
      window.__failAttempts += 1;
      if (window.__failAttempts === 1) return jsonResponse({detail: 'mock unavailable'}, 503, 40);
      return jsonResponse({query: q, total: 1, items: [makeResult(q, 'kv-retry', '重试后结果')]}, 200, 40);
    }
    return jsonResponse({query: q, total: 1, items: [makeResult(q, 'kv-default', '默认检索结果')]}, 200, 40);
  }
  if (url.pathname === '/api/documents/doc1') {
    return jsonResponse({
      id: 'doc1', title: '客户投诉处理制度', active_version_id: 'docv-new',
      created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T01:00:00Z',
      versions: [
        {id: 'docv-new', version_label: 'v2', file_name: '客户投诉处理制度_v2.txt', file_type: 'txt', file_size: 1200,
         uploaded_at: '2026-09-18T01:00:00Z', processing_status: 'completed', error_summary: null, task_id: null, task_status: 'completed', attempt_count: 1, block_count: 1},
        {id: 'docv-old', version_label: 'v1', file_name: '客户投诉处理制度_v1.txt', file_type: 'txt', file_size: 1000,
         uploaded_at: '2026-09-17T01:00:00Z', processing_status: 'completed', error_summary: null, task_id: null, task_status: 'completed', attempt_count: 1, block_count: 1}
      ]
    });
  }
  if (url.pathname === '/api/documents/doc1/versions/docv-old/source-blocks') {
    return jsonResponse([{
      id: 'block-old-12', block_index: 12, block_type: 'paragraph',
      heading_path: '第三章 > 投诉处理', page_number: null,
      paragraph_anchor: '[line_12]', text_content: '投诉事项应登记并形成闭环。'
    }]);
  }
  if (url.pathname.includes('/source-blocks')) return jsonResponse([]);
  return jsonResponse({detail: 'mock route missing: ' + url.pathname}, 404);
};
"""
def run_ui_verification():
    server = run_static_server()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.add_init_script(script=MOCK_SCRIPT)
            page.goto("http://127.0.0.1:5178")
            page.wait_for_selector('h1:has-text("知识管理")', timeout=10000)
            page.wait_for_selector('[data-testid="knowledge-item-ki-pending"]')

            search_input = page.locator('[data-testid="formal-search-input"]')

            # 空查询/未提交输入不触发正式检索，也不改变维护浏览。
            search_input.fill("   ")
            search_input.press("Enter")
            page.wait_for_timeout(120)
            assert page.evaluate("window.__searchRequests.length") == 0
            search_input.fill("只是输入，还没提交")
            page.wait_for_timeout(120)
            assert page.evaluate("window.__searchRequests.length") == 0
            assert page.locator('[data-testid="knowledge-item-ki-pending"]').is_visible()

            # 维护筛选先设为待核对，之后正式检索再清除，必须恢复该维护筛选。
            page.locator('[data-testid="status-pending"]').click()
            page.wait_for_timeout(80)

            # 正式筛选支持同项多选；提交时应编码成重复 GET 参数。
            page.locator('[data-testid="formal-search-filter-toggle"]').click()
            drawer = page.locator('[data-testid="formal-search-filters"]')
            for label in ["制度与标准", "方法与工具", "住宅物业", "商业物业", "客诉处理", "投诉未闭环", "响应超时"]:
                drawer.get_by_role("button", name=label, exact=True).click()

            search_input.fill("B")
            page.locator('[data-testid="formal-search-submit"]').click()
            page.wait_for_selector('text="B 最新结果"', timeout=3000)
            assert page.locator('[data-testid="formal-search-mode"]').is_visible()
            assert page.locator('[data-testid="status-pending"]').count() == 0
            last_request = page.evaluate("window.__searchRequests[window.__searchRequests.length - 1]")
            assert last_request["category"] == ["制度与标准", "方法与工具"]
            assert last_request["customer_type"] == ["住宅物业", "商业物业"]
            assert last_request["business_scene"] == ["客诉处理"]
            assert last_request["problem_tag"] == ["投诉未闭环", "响应超时"]
            assert "document_id" not in last_request

            shot = SCREENSHOTS_DIR / "stage4c_formal_search_results_1440x900.png"
            page.screenshot(path=str(shot))

            # 点击结果展示固定命中版本的完整知识原子。
            page.locator('[data-testid="formal-search-result-kv-b"]').click()
            page.wait_for_selector('[data-testid="formal-result-detail"]')
            detail_text = page.locator('[data-testid="formal-result-detail"]').inner_text()
            for expected in ["知识版本 v3", "条件", "动作", "例外", "证据", "收到客户投诉", "登记工单"]:
                assert expected in detail_text
            page.get_by_role("button", name="关闭正式检索结果详情").click()

            # 来源必须打开结果绑定的 docv-old；即使文档当前 active_version_id 已是 docv-new。
            page.locator('[data-testid="formal-source-kv-b"]').click()
            page.wait_for_selector('text="版本：第 1 版"', timeout=3000)
            page.wait_for_timeout(160)
            api_requests = page.evaluate("window.__apiRequests")
            assert any("/versions/docv-old/source-blocks" in item for item in api_requests)
            assert not any("/versions/docv-new/source-blocks" in item for item in api_requests)
            page.locator('[data-testid="close-detail-modal-btn"]').click()

            # 清除查询回维护浏览，并恢复进入检索前的维护筛选。
            page.locator('[data-testid="clear-formal-search"]').click()
            page.wait_for_selector('[data-testid="status-pending"]')
            page.wait_for_selector('[data-testid="knowledge-item-ki-pending"]', timeout=3000)
            style_attr = page.locator('[data-testid="status-pending"]').get_attribute("style") or ""
            assert "font-weight: 600" in style_attr

            # 零结果与服务失败必须不同，失败可重试。
            search_input.fill("NONE")
            search_input.press("Enter")
            page.wait_for_selector('[data-testid="formal-search-empty"]')
            assert "没有找到符合条件的可用知识，可调整关键词或筛选条件。" in page.content()
            page.locator('[data-testid="clear-formal-search"]').click()
            search_input.fill("FAIL")
            search_input.press("Enter")
            page.wait_for_selector('[data-testid="formal-search-error"]')
            assert "检索暂时不可用，请重试。" in page.content()
            assert "共找到 0 条正式服务中的可用知识" not in page.content()
            page.locator('[data-testid="formal-search-retry"]').click()
            page.wait_for_selector('text="重试后结果"', timeout=3000)
            # 请求竞争：A 先发、B 后发，A 更晚返回；最终只能保留 B。
            page.locator('[data-testid="clear-formal-search"]').click()
            search_input.fill("A")
            search_input.press("Enter")
            page.wait_for_timeout(20)
            search_input.fill("B")
            search_input.press("Enter")
            page.wait_for_selector('text="B 最新结果"', timeout=3000)
            page.wait_for_timeout(800)
            assert page.locator('text="B 最新结果"').is_visible()
            assert page.locator('text="A 晚返回结果"').count() == 0

            race_shot = SCREENSHOTS_DIR / "stage4c_request_race_latest_wins_1440x900.png"
            page.screenshot(path=str(race_shot))

            print("[PASS] Stage 4C UI: 空查询、维护/正式模式、多选参数、零结果、失败重试、request race、来源版本定位全部通过")
            print(f"[PASS] Screenshot: {shot}")
            print(f"[PASS] Screenshot: {race_shot}")
            browser.close()
    finally:
        server.shutdown()


if __name__ == "__main__":
    run_ui_verification()
