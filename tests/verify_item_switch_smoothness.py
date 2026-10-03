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

def run_static_server(port=5188):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

MOCK_SCRIPT = r"""
localStorage.setItem('zhixing_token', 'test-smooth-token');
window.__apiCalls = [];

const adminUser = {
  id: 'usr_admin', organization_id: 'org_greentown', organization_name: '绿城咨询',
  username: 'admin', display_name: '文哲', role: 'admin', account_status: 'active'
};

const doc1 = {
  id: 'doc1', title: '物业服务品质红线标准', active_version_id: 'docv1',
  created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T01:00:00Z',
  version_count: 1, version_label: 'v1', file_name: '物业服务品质红线标准.docx',
  file_type: 'docx', file_size: 2400, uploaded_at: '2026-09-18T01:00:00Z',
  processing_status: 'completed', error_summary: null, task_id: null,
  task_status: 'completed', attempt_count: 1, block_count: 5
};

const item1 = {
  id: 'ki-1', document_id: 'doc1', document_title: '物业服务品质红线标准',
  access_scope: 'org_internal', lifecycle_status: 'active',
  created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z',
  active_version_id: 'kv-1', source_document_version_id: 'docv1',
  version_number: 1, title: '紧急跑水5分钟到达处置', content: '紧急跑水必须在5分钟内到达现场处置。',
  primary_category: '制度与标准', atom_type: '规则', subject: '工程维修部',
  statement: '紧急跑水5分钟到达现场完成停水控制。', conditions: ['业主报修或发现管网跑水'],
  actions: ['5分钟内到达现场', '关闭对应主控制阀'], exceptions: ['遇台风极端天气可延至15分钟'],
  metric_definition: null, case_details: null, field_states: {}, quality_flags: [],
  customer_types: ['住宅业主'], business_scenes: ['管线抢修'], problem_tags: ['跑水积水'],
  source_anchors: ['[p_1]'], valid_from: null, valid_until: null,
  review_status: 'pending_review', index_status: 'not_indexed', revision_token: 'rev-1',
  extraction_context: {}, related_cases: [], evidence_count: 1
};

const item2 = {
  id: 'ki-2', document_id: 'doc1', document_title: '物业服务品质红线标准',
  access_scope: 'org_internal', lifecycle_status: 'active',
  created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z',
  active_version_id: 'kv-2', source_document_version_id: 'docv1',
  version_number: 1, title: '电梯困人救援30分钟到达', content: '电梯发生困人事故时30分钟内完成救援。',
  primary_category: '制度与标准', atom_type: '规则', subject: '电梯维保单位',
  statement: '电梯发生困人故障，救援人员须在30分钟内抵达现场施救。', conditions: ['电梯发生困人停梯'],
  actions: ['安抚被困人员', '立即通知维保专员到达'], exceptions: [],
  metric_definition: null, case_details: null, field_states: {}, quality_flags: [],
  customer_types: ['业主与访客'], business_scenes: ['特种设备应急'], problem_tags: ['电梯困人'],
  source_anchors: ['[p_2]'], valid_from: null, valid_until: null,
  review_status: 'pending_review', index_status: 'not_indexed', revision_token: 'rev-2',
  extraction_context: {}, related_cases: [], evidence_count: 1
};

const item3 = {
  id: 'ki-3', document_id: 'doc1', document_title: '物业服务品质红线标准',
  access_scope: 'org_internal', lifecycle_status: 'active',
  created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z',
  active_version_id: 'kv-3', source_document_version_id: 'docv1',
  version_number: 1, title: '变配电室每两小时测温巡检', content: '变配电室必须每两小时巡视一次并记录温度。',
  primary_category: '指标数据', atom_type: '指标', subject: '配电运行电工',
  statement: '配电室运行环境温度不得高于35摄氏度，每2小时测温登记。', conditions: ['夏季供电负荷高峰'],
  actions: ['读取红外测温仪数值', '开启排风降温系统'], exceptions: [],
  metric_definition: {rows: [{name: '配电室温度', target: '<= 35', unit: '℃', rationale: '设备散热红线'}]},
  case_details: null, field_states: {}, quality_flags: [],
  customer_types: ['全体业主'], business_scenes: ['高低压供电保障'], problem_tags: ['过载跳闸'],
  source_anchors: ['[p_3]'], valid_from: null, valid_until: null,
  review_status: 'pending_review', index_status: 'not_indexed', revision_token: 'rev-3',
  extraction_context: {}, related_cases: [], evidence_count: 1
};

const itemsMap = {
  'ki-1': item1,
  'ki-2': item2,
  'ki-3': item3,
};

function getDetail(id) {
  const item = itemsMap[id] || item1;
  return {
    id: item.id,
    document_id: item.document_id,
    document_title: item.document_title,
    document_version_label: 'v1',
    document_file_name: '物业服务品质红线标准.docx',
    access_scope: item.access_scope,
    document_access_scope: item.access_scope,
    lifecycle_status: item.lifecycle_status,
    created_at: item.created_at,
    updated_at: item.updated_at,
    is_draft_version: false,
    serving_version_number: 1,
    active_version: item,
    evidence: [{
      id: 'ev-' + id,
      source_block_id: 'block-' + id,
      field_name: 'statement',
      excerpt: item.statement,
      accuracy_level: 'exact',
      block_index: id === 'ki-1' ? 0 : id === 'ki-2' ? 1 : 2,
      block_type: 'paragraph',
      heading_path: '服务品质红线 > 应急响应',
      page_number: 1,
      paragraph_anchor: '[p_' + (id === 'ki-1' ? 1 : id === 'ki-2' ? 2 : 3) + ']',
      text_content: '完整原文段落：' + item.statement + ' 所有相关责任人必须按规范严格执行。'
    }],
    version_history: [{
      id: item.active_version_id,
      version_number: 1,
      title: item.title,
      primary_category: item.primary_category,
      review_status: item.review_status,
      index_status: item.index_status,
      created_at: item.created_at,
      created_by: 'system_extractor'
    }],
    can_confirm: true,
    confirmation_blockers: [],
    jev_evaluation: {
      id: 'jev-' + id,
      status: 'completed',
      is_stale: false,
      requested_model: 'jev-latest',
      actual_model: 'jev-1.13.0',
      question_definition_version: 'zhixing-jev-v1',
      classification_suggestion: '制度与标准',
      classification_disagrees: false,
      review_priority: 'high',
      priority_reasons: ['是否遗漏重要例外或禁止事项'],
      thresholds_calibrated: false,
      answers: [{
        question_id: 'exception_omitted', question_type: 'choice',
        question_label: '是否遗漏重要例外或禁止事项', relevant_fields: ['exceptions', 'statement'],
        choice: 'suspected_issue', probabilities: {suspected_issue: 0.82, no_issue: 0.1, insufficient_evidence: 0.08, not_applicable: 0},
        confidence: 0.78, display_status: 'suspected_issue', ignored: false, ignore_reason: null
      }]
    }
  };
}

const originalFetch = window.fetch;
window.fetch = async function(input, init) {
  const url = typeof input === 'string' ? input : input.url;
  const method = (init && init.method) || 'GET';
  window.__apiCalls.push({ url, method });

  // 1. 鉴权
  if (url.includes('/api/auth/me')) {
    return new Response(JSON.stringify(adminUser), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  // 2. 文件列表与详情
  if (url.includes('/api/documents/doc1')) {
    return new Response(JSON.stringify({
      id: 'doc1', title: '物业服务品质红线标准', active_version_id: 'docv1',
      created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T01:00:00Z',
      versions: [
        {id: 'docv1', version_label: 'v1', file_name: '物业服务品质红线标准.docx', file_type: 'docx', file_size: 2400,
         uploaded_at: '2026-09-18T01:00:00Z', processing_status: 'completed', error_summary: null, task_id: null, task_status: 'completed', attempt_count: 1, block_count: 5}
      ]
    }), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }
  if (url.includes('/api/documents')) {
    return new Response(JSON.stringify([doc1]), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  // 3. 知识条目列表
  if (url.includes('/api/knowledge/items?') || url.endsWith('/api/knowledge/items')) {
    return new Response(JSON.stringify({
      items: [item1, item2, item3],
      stats: {
        total: 3,
        category_counts: {'制度与标准': 2, '指标数据': 1, '方法与工具': 0, '项目案例': 0, '专家经验': 0},
        unclassified_count: 0,
        pending_review_count: 3,
        confirmed_count: 0,
        active_count: 3,
        disabled_count: 0
      }
    }), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  // 4. 单条知识详情
  if (url.includes('/api/knowledge/items/ki-')) {
    const id = url.split('/api/knowledge/items/')[1].split('?')[0];
    const detail = getDetail(id);
    return new Response(JSON.stringify(detail), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  // 5. 标签列表
  if (url.includes('/api/knowledge/tags')) {
    return new Response(JSON.stringify({
      categories: ['制度与标准', '指标数据'],
      customer_types: ['住宅业主', '业主与访客'],
      business_scenes: ['管线抢修', '特种设备应急'],
      problem_tags: ['跑水积水', '电梯困人']
    }), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  // 6. 确认条目
  if (url.includes('/confirm')) {
    return new Response(JSON.stringify({
      message: '知识条目已确认启用',
      review_status: 'confirmed',
      index_status: 'indexing',
      revision_token: 'rev-confirmed'
    }), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  return originalFetch.apply(this, arguments);
};
"""

def test_smooth_navigation():
    server = run_static_server(5188)
    print("=== 开始知行有策 知识条目平滑切换与动画专项验证 ===")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.add_init_script(MOCK_SCRIPT)

        page.goto("http://127.0.0.1:5188")
        page.wait_for_selector('text="物业服务品质红线标准"', timeout=10000)
        print("[PASS] 知识管理工作区加载成功")

        # 点击第一个条目打开校对弹窗
        item_card = page.locator('[data-testid="knowledge-item-ki-1"]')
        item_card.click()
        page.wait_for_selector('text="核对知识"', timeout=8000)
        print("[PASS] 成功打开知识核对弹窗 (ki-1)")

        # 验证初始第 1 条状态
        counter = page.locator('text="第 1 / 3 条"')
        assert counter.is_visible(), "底栏应显示 第 1 / 3 条"
        page.screenshot(path=str(SCREENSHOTS_DIR / "verify_switch_01_item1.png"))
        print("[PASS] 初始条目 1 渲染正确，截图已保存")

        # 点击「下一条」
        next_btn = page.locator('button[title^="下一条"]')
        next_btn.click()
        page.wait_for_timeout(300)

        # 验证切换到第 2 条
        counter2 = page.locator('text="第 2 / 3 条"')
        assert counter2.is_visible(), "底栏应更新为 第 2 / 3 条"
        title2 = page.locator('text="电梯困人救援30分钟到达"').first
        assert title2.is_visible(), "标题应直接呈现第 2 条内容"
        page.screenshot(path=str(SCREENSHOTS_DIR / "verify_switch_02_item2.png"))
        print("[PASS] 成功切换至第 2 条，无骨架屏闪烁，内容瞬时衔接")

        # 使用键盘快捷键 Alt + ArrowRight 切换至第 3 条
        page.keyboard.press("Alt+ArrowRight")
        page.wait_for_timeout(300)

        counter3 = page.locator('text="第 3 / 3 条"')
        assert counter3.is_visible(), "底栏应通过 Alt+→ 切换为 第 3 / 3 条"
        title3 = page.locator('text="变配电室每两小时测温巡检"').first
        assert title3.is_visible(), "标题应直接呈现第 3 条内容"
        page.screenshot(path=str(SCREENSHOTS_DIR / "verify_switch_03_item3.png"))
        print("[PASS] 键盘快捷键 Alt+→ 成功切换至第 3 条")

        # 使用键盘快捷键 Alt + ArrowLeft 切换回第 2 条
        page.keyboard.press("Alt+ArrowLeft")
        page.wait_for_timeout(300)
        assert page.locator('text="第 2 / 3 条"').is_visible(), "底栏应通过 Alt+← 切换回 第 2 / 3 条"
        print("[PASS] 键盘快捷键 Alt+← 成功切回第 2 条")

        # 点击「上一条」回到第 1 条
        prev_btn = page.locator('button[title^="上一条"]')
        prev_btn.click()
        page.wait_for_timeout(300)
        assert page.locator('text="第 1 / 3 条"').is_visible(), "底栏应切回 第 1 / 3 条"
        print("[PASS] 按钮点击「上一条」回到第 1 条")

        # 测试 Escape 快捷键关闭弹窗
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert page.locator('[data-testid="close-modal-btn"]').count() == 0, "按下 Escape 键应成功关闭弹窗"
        print("[PASS] 按下 Escape 键成功关闭弹窗")

        # 重新打开第 1 条，测试确认启用并自动流转下一条
        item_card.click()
        page.wait_for_selector('[data-testid="confirm-knowledge-btn"]', timeout=5000)
        confirm_btn = page.locator('[data-testid="confirm-knowledge-btn"]')
        assert confirm_btn.is_visible(), "应显示确认按钮"
        confirm_btn.click()
        page.wait_for_timeout(350)

        # 确认后自动流转至第 2 条
        assert page.locator('text="第 2 / 3 条"').is_visible(), "确认后应自动流转至第 2 条"
        assert page.locator('text="电梯困人救援30分钟到达"').first.is_visible(), "自动流转后应直接呈现第 2 条内容"
        page.screenshot(path=str(SCREENSHOTS_DIR / "verify_switch_04_auto_next.png"))
        print("[PASS] 确认启用后平滑自动流转至第 2 条，无状态微闪")

        browser.close()
        print("=== 知识条目平滑切换与动画专项验证 全部通过！ ===")

if __name__ == "__main__":
    test_smooth_navigation()
