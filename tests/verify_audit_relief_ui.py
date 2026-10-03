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


def run_static_server(port=5182):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


MOCK_SCRIPT = r"""
localStorage.setItem('zhixing_token', 'audit-relief-token');

const adminUser = {
  id: 'usr_admin', organization_id: 'org_greentown', organization_name: '绿城咨询',
  username: 'admin', display_name: '文哲', role: 'admin', account_status: 'active'
};

const documentList = [{
  id: 'doc_sop', title: '高压配电与应急操作规程', active_version_id: 'ver_sop_v1',
  created_at: '2026-09-19T00:00:00Z', updated_at: '2026-09-19T01:00:00Z',
  version_count: 1, version_label: 'v1.0', file_name: '高压配电与应急操作规程.docx',
  file_type: 'docx', file_size: 25400, uploaded_at: '2026-09-19T01:00:00Z',
  processing_status: 'completed', error_summary: null, task_id: null,
  task_status: 'completed', attempt_count: 1, block_count: 6
}];

const documentDetail = {
  ...documentList[0],
  versions: [{
    id: 'ver_sop_v1', version_label: 'v1.0', file_name: '高压配电与应急操作规程.docx',
    file_type: 'docx', file_size: 25400, uploaded_at: '2026-09-19T01:00:00Z',
    processing_status: 'completed', error_summary: null, task_id: null,
    task_status: 'completed', attempt_count: 1, block_count: 6
  }]
};

const mockChapterReviewResponse = {
  document_id: 'doc_sop',
  document_title: '高压配电与应急操作规程',
  version_id: 'ver_sop_v1',
  version_label: 'v1.0',
  file_name: '高压配电与应急操作规程.docx',
  summary: {
    total_chapters: 2,
    total_blocks: 6,
    uncovered_blocks: 2,
    associated_blocks: 3,
    admin_ignored_blocks: 0,
    model_suggested_ignore_blocks: 1,
    total_items: 3,
    pending_items: 2,
    confirmed_items: 1,
    critical_items: 1
  },
  chapters: [
    {
      chapter_key: 'chap_1',
      chapter_name: '第四章 变压器带电检修与应急停机',
      heading_path: '第四章 变压器带电检修与应急停机',
      is_derived_group: false,
      group_description: '第四章 变压器带电检修与应急停机',
      blocks: [
        {
          id: 'blk_1',
          block_index: 10,
          block_type: 'paragraph',
          heading_path: '第四章 变压器带电检修与应急停机',
          page_number: 12,
          paragraph_anchor: '[p_10]',
          text_content: '第4.1条 发生绝缘击穿或变压器局部起火时，运维值班人员必须在15秒内按下主控台红色急停按钮切断进线电源，并立即向调度中心通报。',
          coverage_status: 'associated_candidate',
          coverage_note: '已关联候选条目（提示：存在候选知识并不等同于原文已被完整提取）',
          ignore_status: 'normal',
          ignore_reason: null,
          ignored_by: null,
          ignored_at: null,
          associated_items: [{ item_id: 'ki_crit_1', title: '绝缘击穿与起火紧急断电规程', field_name: 'actions' }]
        },
        {
          id: 'blk_2',
          block_index: 11,
          block_type: 'paragraph',
          heading_path: '第四章 变压器带电检修与应急停机',
          page_number: 12,
          paragraph_anchor: '[p_11]',
          text_content: '日常巡检每2小时记录一次主变油温与绕组温度，遇有雷雨大风天气应启动特巡巡视制度。',
          coverage_status: 'associated_candidate',
          coverage_note: '已关联候选条目（提示：存在候选知识并不等同于原文已被完整提取）',
          ignore_status: 'normal',
          ignore_reason: null,
          ignored_by: null,
          ignored_at: null,
          associated_items: [{ item_id: 'ki_norm_1', title: '主变温度巡检与特巡规程', field_name: 'statement' }]
        },
        {
          id: 'blk_3',
          block_index: 12,
          block_type: 'paragraph',
          heading_path: '第四章 变压器带电检修与应急停机',
          page_number: 13,
          paragraph_anchor: '[p_12]',
          text_content: '检修工器具出入库需登记在《设备维保台账附表三》，遗失工器具应在半小时内向工段长报告。',
          coverage_status: 'uncovered',
          coverage_note: '该正文块尚未被任何知识条目覆盖或人工忽略，可能存在遗漏，待检查',
          ignore_status: 'normal',
          ignore_reason: null,
          ignored_by: null,
          ignored_at: null,
          associated_items: []
        },
        {
          id: 'blk_4', block_index: 13, block_type: 'paragraph',
          heading_path: '第四章 变压器带电检修与应急停机', page_number: 13,
          paragraph_anchor: '[p_13]', text_content: '本段为设备沿革背景说明。',
          coverage_status: 'model_suggested_ignore',
          coverage_note: '模型建议无需提取，待管理员确认',
          ignore_status: 'model_suggested_ignore', ignore_reason: '模型判断为背景说明',
          ignored_by: null, ignored_at: null, associated_items: []
        }
      ],
      items: [
        {
          item_id: 'ki_crit_1',
          version_id: 'kv_crit_1',
          title: '绝缘击穿与起火紧急断电规程',
          statement: '变压器起火必须在15秒内按下主控台红色急停按钮切断进线电源。',
          content: '发生绝缘击穿或起火等严重安全事故时，值班人员须执行15秒急停断电程序。',
          primary_category: '制度与标准',
          atom_type: 'process_flow',
          subject: '运维值班人员',
          conditions: ['绝缘击穿', '变压器局部起火'],
          actions: ['15秒内按下主控台红色急停按钮切断进线电源', '立即向调度中心通报'],
          exceptions: [],
          metric_definition: null,
          case_details: null,
          field_states: {},
          quality_flags: [],
          source_anchors: ['[p_10]'],
          review_status: 'pending_review',
          index_status: 'not_indexed',
          revision_token: 'token_rev_crit_1',
          is_draft: true,
          business_importance: 'critical',
          importance_rationale: '命中断电/急停/起火等安全与应急刚性操作，自动标定为重要操作',
          importance_adjusted_by: null,
          issues_summary: {
            deterministic_errors: [],
            model_doubts: [],
            conflict_check_status: '未发现已知规则冲突',
            unimplemented_capabilities: ['跨文档全局矛盾检测尚未实现，当前仅完成单文档规则冲突检查']
          },
          can_confirm: true,
          confirmation_blockers: [],
          batch_review_reasons: ['属于重点审核项目，须逐条人工核对'],
          can_batch_confirm: false,
          batch_block_reason: '包含重要安全/应急操作，禁止参与批量确认，必须人工逐条核验',
          evidence: [],
          associated_block_ids: ['blk_1']
        },
        {
          item_id: 'ki_norm_1',
          version_id: 'kv_norm_1',
          title: '主变温度巡检与特巡规程',
          statement: '日常巡检每2小时记录一次主变油温与绕组温度，遇雷雨大风天气启动特巡。',
          content: '巡视人员按固定频次记录油温与绕组指标，恶劣天气增加特巡。',
          primary_category: '制度与标准',
          atom_type: 'rule_constraint',
          subject: '巡视人员',
          conditions: ['日常运维', '雷雨大风恶劣天气'],
          actions: ['每2小时记录一次主变油温与绕组温度', '启动特巡巡视制度'],
          exceptions: [],
          metric_definition: null,
          case_details: null,
          field_states: {},
          quality_flags: [],
          source_anchors: ['[p_11]'],
          review_status: 'pending_review',
          index_status: 'not_indexed',
          revision_token: 'token_rev_norm_1',
          is_draft: true,
          business_importance: 'normal',
          importance_rationale: '常规日常巡视标准',
          importance_adjusted_by: null,
          issues_summary: {
            deterministic_errors: [],
            model_doubts: [],
            conflict_check_status: '未发现已知规则冲突',
            unimplemented_capabilities: []
          },
          can_confirm: true,
          confirmation_blockers: [],
          batch_review_reasons: [],
          can_batch_confirm: true,
          batch_block_reason: null,
          evidence: [],
          associated_block_ids: ['blk_2']
        }
      ],
      stats: {
        total_blocks: 4,
        uncovered_blocks: 1,
        associated_blocks: 2,
        admin_ignored_blocks: 0,
        model_suggested_ignore_blocks: 1,
        total_items: 2,
        pending_items: 2,
        confirmed_items: 0,
        critical_items: 1,
        can_batch_confirm_count: 1
      },
      attention_points: [
        {
          type: 'critical_operation',
          severity: 'important',
          message: '条目「绝缘击穿与起火紧急断电规程」涉及紧急断电与设备抢险操作，请逐字核实触发时限与动作主体'
        },
        {
          type: 'uncovered_content',
          severity: 'warning',
          message: '存在 1 段正文尚未被抽取或忽略，请核查是否遗漏规范'
        }
      ],
      chapter_status: 'has_uncovered',
      status_label: '存在未覆盖段落 (1段待检查)'
    }
  ]
};

const knowledgeDetail = {
  id: 'ki_norm_1', document_id: 'doc_sop', document_title: '高压配电与应急操作规程',
  document_version_label: 'v1.0', document_file_name: '高压配电与应急操作规程.docx',
  document_access_scope: 'org_internal', access_scope: 'admin_only', lifecycle_status: 'active',
  created_at: '2026-09-19T01:00:00Z', updated_at: '2026-09-19T01:00:00Z',
  is_draft_version: true, serving_version_number: null,
  active_version: {
    id: 'kv_norm_1', version_number: 1, title: '主变温度巡检与特巡规程',
    content: '巡视人员按固定频次记录油温与绕组指标，恶劣天气增加特巡。',
    primary_category: '制度与标准', atom_type: '规则', subject: '巡视人员',
    statement: '日常巡检每2小时记录一次主变油温与绕组温度，遇雷雨大风天气启动特巡。',
    conditions: ['日常运维'], actions: ['每2小时记录一次温度'], exceptions: [],
    metric_definition: null, case_details: null, field_states: {}, quality_flags: [],
    customer_types: [], business_scenes: [], problem_tags: [], source_anchors: ['[p_11]'],
    valid_from: null, valid_until: null, review_status: 'pending_review', reviewed_by: null,
    reviewed_at: null, index_status: 'not_indexed', revision_token: 'token_rev_norm_1',
    extraction_context: {}, related_cases: [], business_importance: 'normal',
    importance_rationale: '常规日常巡视标准', importance_adjusted_by: null
  },
  evidence: [{
    id: 'ev_norm', field_name: 'statement', excerpt: '日常巡检每2小时记录一次主变油温与绕组温度。',
    accuracy_level: 'exact', source_block_id: 'blk_2', block_index: 11, block_type: 'paragraph',
    heading_path: '第四章 变压器带电检修与应急停机', page_number: 12,
    paragraph_anchor: '[p_11]', text_content: '日常巡检每2小时记录一次主变油温与绕组温度，遇有雷雨大风天气应启动特巡巡视制度。'
  }],
  version_history: []
};

window.__auditRequests = [];
window.__chapterReviewRequestCount = 0;

const originalFetch = window.fetch;
window.fetch = async function(url, options = {}) {
  const urlStr = String(url);
  const method = (options.method || 'GET').toUpperCase();
  window.__auditRequests.push({ url: urlStr, method, body: options.body || null });

  if (urlStr.includes('/auth/me')) {
    return new Response(JSON.stringify(adminUser), { status: 200 });
  }
  if (urlStr.includes('/documents/') && urlStr.includes('/chapters/review')) {
    window.__chapterReviewRequestCount += 1;
    return new Response(JSON.stringify(mockChapterReviewResponse), { status: 200 });
  }
  if (urlStr.includes('/documents/doc_sop')) {
    return new Response(JSON.stringify(documentDetail), { status: 200 });
  }
  if (urlStr.includes('/documents') && !urlStr.includes('/chapters/')) {
    return new Response(JSON.stringify(documentList), { status: 200 });
  }
  if (urlStr.includes('/knowledge/items/batch-confirm')) {
    return new Response(JSON.stringify({
      message: '批量确认完成，已记录审计日志',
      confirmed_count: 1,
      skipped_count: 0,
      confirmed_items: [{ item_id: 'ki_norm_1', version_id: 'kv_norm_1', title: '主变温度巡检与特巡规程' }],
      skipped_items: []
    }), { status: 200 });
  }
  if (urlStr.includes('/knowledge/items/ki_crit_1/confirm')) {
    return new Response(JSON.stringify({
      message: '知识条目已确认', review_status: 'confirmed', index_status: 'indexing',
      revision_token: 'token_rev_crit_2', index_task_id: 'task_crit', index_task_status: 'queued'
    }), { status: 200 });
  }
  if (urlStr.includes('/knowledge/items/ki_norm_1/confirm')) {
    return new Response(JSON.stringify({
      message: '知识条目已确认', review_status: 'confirmed', index_status: 'indexing',
      revision_token: 'token_rev_norm_3', index_task_id: 'task_norm', index_task_status: 'queued'
    }), { status: 200 });
  }
  if (urlStr.includes('/knowledge/items/ki_norm_1') && method === 'PUT') {
    const payload = JSON.parse(options.body || '{}');
    knowledgeDetail.active_version.title = payload.title;
    knowledgeDetail.active_version.revision_token = 'token_rev_norm_2';
    const chapterItem = mockChapterReviewResponse.chapters[0].items.find((item) => item.item_id === 'ki_norm_1');
    chapterItem.title = payload.title;
    chapterItem.revision_token = 'token_rev_norm_2';
    return new Response(JSON.stringify({
      message: '知识草稿已保存', revision_token: 'token_rev_norm_2', quality_flags: [],
      version_id: 'kv_norm_1', is_new_version_draft: false
    }), { status: 200 });
  }
  if (urlStr.includes('/knowledge/items/ki_norm_1') && method === 'GET') {
    return new Response(JSON.stringify(knowledgeDetail), { status: 200 });
  }
  if (urlStr.includes('/source-blocks/') && urlStr.includes('/ignore')) {
    return new Response(JSON.stringify({
      message: '正文块忽略状态已更新',
      block_id: 'blk_3',
      ignore_status: 'admin_ignored'
    }), { status: 200 });
  }
  if (urlStr.includes('/knowledge/items') && !urlStr.includes('/batch-confirm')) {
    return new Response(JSON.stringify({
      items: [],
      stats: { total: 2, category_counts: {'制度与标准': 2}, unclassified_count: 0, pending_review_count: 2, confirmed_count: 0 }
    }), { status: 200 });
  }
  if (urlStr.includes('/knowledge/tags')) {
    return new Response(JSON.stringify({ customer_types: [], business_scenes: [], problem_tags: [] }), { status: 200 });
  }
  return originalFetch(url, options);
};
"""


def verify_ui():
    server = run_static_server(5182)
    time.sleep(0.5)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("console", lambda msg: print(f"BROWSER CONSOLE [{msg.type}]: {msg.text}"))
        page.on("pageerror", lambda error: print(f"BROWSER PAGE ERROR: {error}"))

        page.add_init_script(MOCK_SCRIPT)
        page.goto("http://127.0.0.1:5182/")
        page.wait_for_load_state("networkidle")

        # 1. 验证资料列表展示
        doc_item = page.locator("text=高压配电与应急操作规程").first
        doc_item.wait_for(state="visible", timeout=5000)
        doc_item.click()
        page.wait_for_timeout(800)

        # 2. 点击【章节集中核对】模式
        review_mode_btn = page.locator("[data-testid='mode-chapter-review']")
        review_mode_btn.wait_for(state="visible", timeout=5000)
        review_mode_btn.click()
        page.wait_for_timeout(800)

        # 验证章节集中核对界面元素
        page.locator("text=章节集中核对").first.wait_for(state="visible", timeout=5000)
        page.locator("text=第四章 变压器带电检修与应急停机").first.wait_for(state="visible", timeout=5000)
        
        # 验证左栏只读原文流水与真实覆盖状态标签
        page.locator("text=原始文本流水 (只读)").wait_for(state="visible", timeout=5000)
        page.locator("text=尚未覆盖，待检查").wait_for(state="visible", timeout=5000)
        page.locator("text=已关联候选条目").first.wait_for(state="visible", timeout=5000)
        page.get_by_text("模型建议忽略", exact=True).wait_for(state="visible", timeout=5000)
        page.get_by_role("button", name="人工确认忽略").wait_for(state="visible", timeout=5000)

        # 验证右栏解耦展示：重点审核（重要操作）与普通审核
        page.locator("text=重点审核（重要操作 / 模型疑点 / 阻断项）").wait_for(state="visible", timeout=5000)
        page.locator("text=普通审核（常规业务知识）").wait_for(state="visible", timeout=5000)
        page.locator("text=⚠️ 重要操作").wait_for(state="visible", timeout=5000)

        # 详细校对保存后，章节视图必须重新请求并显示新标题、新令牌。
        before_refresh_count = page.evaluate("window.__chapterReviewRequestCount")
        normal_card = page.locator('[data-testid="chapter-item-ki_norm_1"]')
        normal_card.get_by_role("button", name="详细校对").click()
        page.get_by_role("button", name="编辑全部内容").wait_for(state="visible", timeout=5000)
        page.get_by_role("button", name="编辑全部内容").click()
        page.locator('[data-testid="input-title"]').wait_for(state="visible", timeout=5000)
        page.locator('[data-testid="input-title"]').fill("主变温度巡检与特巡规程（校对后）")
        page.locator('[data-testid="save-draft-btn"]').click()
        page.wait_for_function(
            "before => window.__chapterReviewRequestCount > before",
            arg=before_refresh_count,
            timeout=5000,
        )
        page.locator('[data-testid="close-modal-btn"]').click()
        page.locator('[data-testid="chapter-item-ki_norm_1"]').get_by_text("主变温度巡检与特巡规程（校对后）").wait_for(
            state="visible", timeout=5000
        )

        # 刷新后的单条确认必须提交新令牌，不能继续使用保存前令牌。
        page.locator('[data-testid="chapter-item-ki_norm_1"]').get_by_role("button", name="确认通过").click()
        page.wait_for_function(
            "() => window.__auditRequests.some(r => r.url.includes('/knowledge/items/ki_norm_1/confirm'))",
            timeout=5000,
        )
        normal_confirm_request = page.evaluate(
            "window.__auditRequests.find(r => r.url.includes('/knowledge/items/ki_norm_1/confirm'))"
        )
        assert json.loads(normal_confirm_request["body"])["revision_token"] == "token_rev_norm_2"

        # 重点条目按钮必须调用单条确认接口，不能借批量接口绕过保护。
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator('[data-testid="chapter-item-ki_crit_1"]').get_by_role("button", name="重点审核确认").click()
        page.wait_for_function(
            "() => window.__auditRequests.some(r => r.url.includes('/knowledge/items/ki_crit_1/confirm'))",
            timeout=5000,
        )
        critical_confirm_request = page.evaluate(
            "window.__auditRequests.find(r => r.url.includes('/knowledge/items/ki_crit_1/confirm'))"
        )
        assert "/batch-confirm" not in critical_confirm_request["url"]
        assert json.loads(critical_confirm_request["body"])["revision_token"] == "token_rev_crit_1"

        # 保存集中核对工作台截图
        screenshot_path1 = SCREENSHOTS_DIR / "audit_relief_chapter_review.png"
        page.screenshot(path=str(screenshot_path1), full_page=True)
        print(f"Screenshot 1 saved to: {screenshot_path1}")

        # 3. 点击【批量确认普通条目】：验证防误操作二次确认清单弹窗
        batch_btn = page.locator("text=批量确认普通条目").first
        batch_btn.wait_for(state="visible", timeout=5000)
        batch_btn.click()
        page.wait_for_timeout(500)

        page.locator("text=批量确认核对清单（防误操作保护）").wait_for(state="visible", timeout=5000)
        page.locator("text=即将确认的普通条目").wait_for(state="visible", timeout=5000)
        page.locator("text=安全跳过的重点/阻断条目").wait_for(state="visible", timeout=5000)
        page.locator("text=绝缘击穿与起火紧急断电规程").last.wait_for(state="visible", timeout=5000)

        # 保存批量确认二次清单弹窗截图
        screenshot_path2 = SCREENSHOTS_DIR / "audit_relief_batch_modal.png"
        page.screenshot(path=str(screenshot_path2), full_page=True)
        print(f"Screenshot 2 saved to: {screenshot_path2}")

        browser.close()
    server.shutdown()
    print("E2E UI verification completed successfully!")


if __name__ == "__main__":
    verify_ui()
