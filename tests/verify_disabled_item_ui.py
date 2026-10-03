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

def run_static_server(port=5189):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

MOCK_SCRIPT = r"""
localStorage.setItem('zhixing_token', 'test-token');

const adminUser = {
  id: 'usr_admin', organization_id: 'org_greentown', organization_name: '绿城咨询',
  username: 'admin', display_name: '文哲', role: 'admin', account_status: 'active'
};

const doc1 = {
  id: 'doc1', title: '权限规范文档', active_version_id: 'docv1',
  created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T01:00:00Z',
  version_count: 1, version_label: '第 1 版', file_name: '权限规范文档.docx',
  file_type: 'docx', file_size: 2400, uploaded_at: '2026-09-18T01:00:00Z',
  processing_status: 'completed', error_summary: null, task_id: null,
  task_status: 'completed', attempt_count: 1, block_count: 5
};

const itemActive1 = {
  id: 'ki-act1', document_id: 'doc1', document_title: '权限规范文档',
  access_scope: 'admin_only', lifecycle_status: 'active',
  created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z',
  active_version_id: 'kv-act1', source_document_version_id: 'docv1',
  version_number: 1, title: '正常启用条目A', content: '正常业务条目A',
  primary_category: '制度与标准', atom_type: '规则', subject: '测试主体',
  statement: '正常业务条目A', conditions: [], actions: [], exceptions: [],
  metric_definition: null, case_details: null, field_states: {}, quality_flags: [],
  customer_types: ['通用客户'], business_scenes: ['通用场景'], problem_tags: ['未标记'],
  source_anchors: ['[p_1]'], valid_from: null, valid_until: null,
  review_status: 'confirmed', index_status: 'ready', revision_token: 'rev-act1',
  extraction_context: {}, related_cases: [], evidence_count: 1
};

const itemActive2 = {
  id: 'ki-act2', document_id: 'doc1', document_title: '权限规范文档',
  access_scope: 'admin_only', lifecycle_status: 'active',
  created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z',
  active_version_id: 'kv-act2', source_document_version_id: 'docv1',
  version_number: 1, title: '正常启用条目B', content: '正常业务条目B',
  primary_category: '制度与标准', atom_type: '规则', subject: '测试主体',
  statement: '正常业务条目B', conditions: [], actions: [], exceptions: [],
  metric_definition: null, case_details: null, field_states: {}, quality_flags: [],
  customer_types: ['通用客户'], business_scenes: ['通用场景'], problem_tags: ['未标记'],
  source_anchors: ['[p_2]'], valid_from: null, valid_until: null,
  review_status: 'confirmed', index_status: 'ready', revision_token: 'rev-act2',
  extraction_context: {}, related_cases: [], evidence_count: 1
};

const itemDisabled = {
  id: 'ki-disabled', document_id: 'doc1', document_title: '权限规范文档',
  access_scope: 'admin_only', lifecycle_status: 'disabled',
  created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z',
  active_version_id: 'kv-disabled', source_document_version_id: 'docv1',
  version_number: 1, title: '公开规范条目 (已停用)', content: '核心陈述必须清晰且包含业务事实：权限词_d9f973',
  primary_category: '制度与标准', atom_type: '规则', subject: '测试主体',
  statement: '核心陈述必须清晰且包含业务事实：权限词_d9f973',
  conditions: [], actions: [], exceptions: [],
  metric_definition: null, case_details: null, field_states: {}, quality_flags: [],
  customer_types: ['通用客户'], business_scenes: ['通用场景'], problem_tags: ['未标记'],
  source_anchors: ['[p_3]'], valid_from: null, valid_until: null,
  review_status: 'confirmed', index_status: 'ready', revision_token: 'rev-disabled',
  extraction_context: {}, related_cases: [], evidence_count: 1
};

const detailDisabled = {
  id: 'ki-disabled', document_id: 'doc1', document_title: '权限规范文档',
  document_version_label: '第 1 版', document_file_name: '权限规范文档.docx',
  access_scope: 'admin_only', document_access_scope: 'admin_only',
  lifecycle_status: 'disabled', is_draft_version: false, serving_version_number: 1,
  active_version: {
    id: 'kv-disabled', version_number: 1, title: '公开规范条目 (已停用)',
    primary_category: '制度与标准', atom_type: '规则', subject: '测试主体',
    statement: '核心陈述必须清晰且包含业务事实：权限词_d9f973',
    content: '核心陈述必须清晰且包含业务事实：权限词_d9f973',
    conditions: [], actions: [], exceptions: [],
    metric_definition: null, case_details: null, field_states: {}, quality_flags: [],
    customer_types: ['通用客户'], business_scenes: ['通用场景'], problem_tags: ['未标记'],
    source_anchors: ['[p_3]'], valid_from: null, valid_until: null,
    review_status: 'confirmed', index_status: 'ready', revision_token: 'rev-disabled',
  },
  evidence: [
    {
      id: 'ev-1', knowledge_version_id: 'kv-disabled', source_block_id: 'sb-1',
      field_name: 'statement', excerpt: '测试用来来源段落正文', accuracy_level: 'exact',
      block_index: 2, block_type: 'paragraph', heading_path: null,
      page_number: 1, paragraph_anchor: 'p_3', text_content: '测试用来来源段落正文'
    }
  ],
  source_blocks: [
    {
      id: 'sb-1', document_version_id: 'docv1', block_index: 2, block_type: 'paragraph',
      heading_path: null, page_number: 1, paragraph_anchor: 'p_3', text_content: '测试用来来源段落正文'
    }
  ],
  version_history: [
    {
      id: 'kv-disabled', version_number: 1, title: '公开规范条目 (已停用)',
      statement: '核心陈述必须清晰且包含业务事实：权限词_d9f973',
      review_status: 'confirmed', index_status: 'ready',
      created_at: '2026-09-18T00:00:00Z', reviewed_at: '2026-09-18T01:00:00Z',
      reviewed_by: 'usr_admin'
    }
  ]
};

const originalFetch = window.fetch;
window.fetch = async function(input, init) {
  const url = typeof input === 'string' ? input : input.url;
  if (url.includes('/api/auth/me')) {
    return new Response(JSON.stringify(adminUser), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }
  if (url.includes('/api/documents/doc1')) {
    return new Response(JSON.stringify({
      id: 'doc1', title: '权限规范文档', active_version_id: 'docv1',
      created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T01:00:00Z',
      versions: [
        { id: 'docv1', version_label: '第 1 版', file_name: '权限规范文档.docx', file_type: 'docx', file_size: 2400,
          uploaded_at: '2026-09-18T01:00:00Z', processing_status: 'completed', error_summary: null, task_id: null, task_status: 'completed', attempt_count: 1, block_count: 5 }
      ]
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }
  if (url.includes('/api/documents')) {
    return new Response(JSON.stringify([doc1]), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }
  if (url.includes('/api/knowledge/items?') || url.endsWith('/api/knowledge/items')) {
    // 即使未排好序（模拟停用条目在前），前端与后端契约均保证停用条目自动排在最底
    return new Response(JSON.stringify({
      items: [itemDisabled, itemActive1, itemActive2],
      stats: {
        total: 3,
        category_counts: { '制度与标准': 3, '指标数据': 0, '方法与工具': 0, '项目案例': 0, '专家经验': 0 },
        unclassified_count: 0,
        pending_review_count: 0,
        confirmed_count: 3,
        active_count: 2,
        disabled_count: 1
      }
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }
  if (url.includes('/api/knowledge/items/ki-disabled')) {
    return new Response(JSON.stringify(detailDisabled), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }
  if (url.includes('/api/knowledge/tags')) {
    return new Response(JSON.stringify({ customer_types: ['通用客户'], business_scenes: ['通用场景'], problem_tags: ['未标记'] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }
  return originalFetch.apply(this, arguments);
};
"""

def verify_disabled_item_ui():
    run_static_server(5189)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script(MOCK_SCRIPT)

        page.goto("http://127.0.0.1:5189")
        page.wait_for_selector('text="权限规范文档"', timeout=8000)

        # 切换到全部分类
        tab_all = page.locator('[data-testid="tab-all"]')
        if tab_all.is_visible():
            tab_all.click()

        page.wait_for_selector('[data-testid="knowledge-item-ki-disabled"]', timeout=5000)

        # 验证停用条目自动沉底：列表中的最后一个卡片必须是停用的知识条目
        cards = page.locator('[data-testid^="knowledge-item-"]')
        card_count = cards.count()
        assert card_count == 3, f"预期 3 个卡片，实际为 {card_count}"
        last_card = cards.last
        last_card_id = last_card.get_attribute("data-testid")
        assert last_card_id == "knowledge-item-ki-disabled", f"停用条目必须自动排在列表最底部，当前底部卡片为: {last_card_id}"
        print("[PASS] 停用知识条目成功自动沉底至列表最末端！")

        # 验证已停用卡片显示已停用，不显示已确认启用
        assert "已停用" in last_card.inner_text(), "卡片应显示「已停用」"
        assert "已确认启用" not in last_card.inner_text(), "卡片停用时不应显示「已确认启用」"

        # 验证筛选按钮无数字角标
        filter_btn = page.locator('[data-testid="toggle-filters-btn"]')
        assert filter_btn.is_visible()
        filter_btn_text = filter_btn.inner_text().strip()
        assert filter_btn_text == "筛选", f"筛选按钮不应包含多余数字，当前文本为: '{filter_btn_text}'"
        print(f"[PASS] 筛选按钮纯文本展示正常: '{filter_btn_text}'（多余数字角标已彻底移除）")

        # 点击展开二级筛选抽屉，并截图保存主视图状态
        filter_btn.click()
        page.wait_for_timeout(300)
        shot_list_path = SCREENSHOTS_DIR / "verify_filter_drawer_no_badge.png"
        page.screenshot(path=str(shot_list_path))
        print(f"[PASS] 筛选抽屉展开截图已保存: {shot_list_path}")

        # 点击卡片打开详情弹窗
        last_card.click()
        page.wait_for_selector('text="查看与维护知识"', timeout=5000)

        # 验证弹窗顶栏状态标识
        disabled_badge = page.locator('[data-testid="disabled-status-badge"]')
        assert disabled_badge.is_visible(), "弹窗顶栏必须显示「已停用」状态标签"

        confirmed_badge = page.locator('[data-testid="confirmed-status-badge"]')
        assert not confirmed_badge.is_visible(), "条目已停用时，严禁同时显示「已确认启用」标签！"

        resume_btn = page.locator('[data-testid="toggle-lifecycle-btn"]')
        assert resume_btn.is_visible() and "恢复启用" in resume_btn.inner_text(), "操作按钮应为「恢复启用」"

        # 验证弹窗底栏生效状态
        disabled_footer = page.locator('[data-testid="disabled-footer-badge"]')
        assert disabled_footer.is_visible(), "弹窗底栏应显示「条目已停用（未生效）」"

        active_footer = page.locator('[data-testid="active-version-in-effect-badge"]')
        assert not active_footer.is_visible(), "条目已停用时，底栏严禁显示「当前版本已生效」！"

        # 保存截图确认
        shot_path = SCREENSHOTS_DIR / "verify_disabled_item_modal_fixed.png"
        page.screenshot(path=str(shot_path))
        print(f"[PASS] 停用知识弹窗状态与沉底排序验证全部通过！截图保存至: {shot_path}")
        browser.close()

if __name__ == "__main__":
    verify_disabled_item_ui()
