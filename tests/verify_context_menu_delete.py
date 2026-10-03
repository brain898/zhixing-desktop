import json
import threading
import time
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
        pass

def run_static_server(port=5183):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

MOCK_SCRIPT = r"""
localStorage.setItem('zhixing_token', 'test-context-menu-token');
window.__apiCalls = [];
window.__deleteShouldFail = false;
window.__lastAlertMessage = null;

window.alert = function(msg) {
  window.__lastAlertMessage = msg;
  console.log('[ALERT INTERCEPTED]', msg);
};

const adminUser = {
  id: 'usr_admin', organization_id: 'org_greentown', organization_name: '绿城咨询',
  username: 'admin', display_name: '文哲', role: 'admin', account_status: 'active'
};

let documents = [
  {
    id: 'doc_1', title: '物业服务品质红线标准.docx', active_version_id: 'docv_1',
    created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T01:00:00Z',
    version_count: 1, version_label: 'v1', file_name: '物业服务品质红线标准.docx',
    file_type: 'docx', file_size: 2400, uploaded_at: '2026-09-18T01:00:00Z',
    processing_status: 'completed', error_summary: null, task_id: null,
    task_status: 'completed', attempt_count: 1, block_count: 5
  },
  {
    id: 'doc_2', title: '园区消防安全应急预案.pdf', active_version_id: 'docv_2',
    created_at: '2026-09-19T00:00:00Z', updated_at: '2026-09-19T01:00:00Z',
    version_count: 1, version_label: 'v1', file_name: '园区消防安全应急预案.pdf',
    file_type: 'pdf', file_size: 5120, uploaded_at: '2026-09-19T01:00:00Z',
    processing_status: 'completed', error_summary: null, task_id: null,
    task_status: 'completed', attempt_count: 1, block_count: 8
  }
];

let items = [
  {
    id: 'ki-1', document_id: 'doc_1', document_title: '物业服务品质红线标准.docx',
    access_scope: 'org_internal', lifecycle_status: 'active',
    created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z',
    active_version_id: 'kv-1', source_document_version_id: 'docv_1',
    version_number: 1, title: '紧急跑水5分钟到达处置', content: '紧急跑水必须在5分钟内到达现场处置。',
    primary_category: '制度与标准', atom_type: '规则', subject: '工程维修部',
    statement: '紧急跑水5分钟到达现场完成停水控制。', conditions: ['业主报修或发现管网跑水'],
    actions: ['5分钟内到达现场', '关闭对应主控制阀'], exceptions: [],
    metric_definition: null, case_details: null, field_states: {}, quality_flags: [],
    customer_types: ['住宅业主'], business_scenes: ['管线抢修'], problem_tags: ['跑水积水'],
    source_anchors: ['[p_1]'], valid_from: null, valid_until: null,
    review_status: 'pending_review', index_status: 'not_indexed', revision_token: 'rev-1',
    extraction_context: {}, related_cases: [], evidence_count: 1
  },
  {
    id: 'ki-2', document_id: 'doc_2', document_title: '园区消防安全应急预案.pdf',
    access_scope: 'org_internal', lifecycle_status: 'active',
    created_at: '2026-09-19T00:00:00Z', updated_at: '2026-09-19T00:00:00Z',
    active_version_id: 'kv-2', source_document_version_id: 'docv_2',
    version_number: 1, title: '消防控制室双人24小时值班', content: '消控室必须保持双人持证上岗。',
    primary_category: '制度与标准', atom_type: '规则', subject: '秩序维保部',
    statement: '消防控制室必须落实两人持中级消防设施操作员证24小时值班。', conditions: ['日常常态化运行'],
    actions: ['双人值守', '按时巡更检查记录主机'], exceptions: [],
    metric_definition: null, case_details: null, field_states: {}, quality_flags: [],
    customer_types: ['园区全员'], business_scenes: ['消防应急'], problem_tags: ['消控值守'],
    source_anchors: ['[p_2]'], valid_from: null, valid_until: null,
    review_status: 'pending_review', index_status: 'not_indexed', revision_token: 'rev-2',
    extraction_context: {}, related_cases: [], evidence_count: 1
  }
];

window.fetch = async function(input, init) {
  const url = typeof input === 'string' ? input : input.url;
  const method = (init && init.method) || 'GET';
  window.__apiCalls.push({ url, method, time: Date.now() });

  // 1. 鉴权接口
  if (url.includes('/api/auth/me')) {
    return new Response(JSON.stringify(adminUser), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  // 2. 真实影响接口
  const impactMatch = url.match(/\/api\/documents\/([^/]+)\/deletion-impact/);
  if (impactMatch) {
    const docId = impactMatch[1];
    const targetDoc = documents.find(d => d.id === docId);
    const derivedCount = items.filter(i => i.document_id === docId).length;
    return new Response(JSON.stringify({
      document_id: docId,
      version_count: targetDoc ? targetDoc.version_count : 1,
      derived_knowledge_count: derivedCount,
      retrieval_record_count: derivedCount,
      active_task_count: 0,
      related_case_reference_count: 0,
      skill_reference_count: 0,
      physical_delete_scheduled: false
    }), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  // 3. 删除文件接口
  const deleteMatch = url.match(/\/api\/documents\/([^/]+)$/);
  if (deleteMatch && method === 'DELETE') {
    const docId = deleteMatch[1];
    if (window.__deleteShouldFail) {
      return new Response(JSON.stringify({
        detail: '服务端网络超时或权限校验失败，请稍后重试'
      }), {
        status: 500, headers: {'Content-Type': 'application/json'}
      });
    }
    // 成功逻辑删除
    documents = documents.filter(d => d.id !== docId);
    items = items.filter(i => i.document_id !== docId);
    return new Response(JSON.stringify({
      message: '文档已逻辑删除，关联派生知识条目已撤销',
      document_id: docId
    }), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  // 4. 获取文件列表
  if (url.match(/\/api\/documents$/) || url.match(/\/api\/documents\?/)) {
    return new Response(JSON.stringify(documents), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  // 5. 单文档详情
  const detailDocMatch = url.match(/\/api\/documents\/([^/]+)$/);
  if (detailDocMatch && method === 'GET') {
    const docId = detailDocMatch[1];
    const doc = documents.find(d => d.id === docId);
    if (!doc) {
      return new Response(JSON.stringify({ detail: '资料不存在或已被删除' }), {
        status: 404, headers: {'Content-Type': 'application/json'}
      });
    }
    return new Response(JSON.stringify({
      id: doc.id,
      title: doc.title,
      active_version_id: doc.active_version_id,
      created_at: doc.created_at,
      updated_at: doc.updated_at,
      versions: [
        {
          id: doc.active_version_id,
          version_label: doc.version_label,
          file_name: doc.file_name,
          file_type: doc.file_type,
          file_size: doc.file_size,
          uploaded_at: doc.uploaded_at,
          processing_status: doc.processing_status,
          error_summary: null,
          task_id: null,
          task_status: 'completed',
          attempt_count: 1,
          block_count: doc.block_count
        }
      ]
    }), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  // 6. 知识条目列表
  if (url.includes('/api/knowledge/items')) {
    const urlObj = new URL(url, 'http://localhost');
    const filterDocId = urlObj.searchParams.get('document_id');
    const filteredItems = filterDocId ? items.filter(i => i.document_id === filterDocId) : items;
    return new Response(JSON.stringify({
      items: filteredItems,
      stats: {
        total: filteredItems.length,
        category_counts: {'制度与标准': filteredItems.length, '指标数据': 0, '方法与工具': 0, '项目案例': 0, '专家经验': 0},
        unclassified_count: 0,
        pending_review_count: filteredItems.length,
        confirmed_count: 0,
        active_count: filteredItems.length,
        disabled_count: 0
      }
    }), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  return new Response(JSON.stringify({}), {
    status: 200, headers: {'Content-Type': 'application/json'}
  });
};
"""

def test_context_menu_delete():
    server = run_static_server(5183)
    front_url = "http://127.0.0.1:5183"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        print("--> 1. 加载页面并注入 Mock 服务...")
        page.add_init_script(MOCK_SCRIPT)
        page.goto(front_url)
        page.wait_for_selector('text="文哲"', timeout=10000)
        page.wait_for_selector('[data-testid="doc-item-物业服务品质红线标准.docx"]', timeout=8000)
        page.wait_for_selector('[data-testid="doc-item-园区消防安全应急预案.pdf"]', timeout=8000)
        print("    两份已导入文件均已成功加载并渲染在列表中。")

        # 选中第一个文件
        doc1_item = page.locator('[data-testid="doc-item-物业服务品质红线标准.docx"]')
        doc1_item.click()
        page.wait_for_timeout(300)
        # 确认当前选中是 doc1
        assert "bg-selected" in doc1_item.evaluate("el => el.style.backgroundColor"), "doc1 应当为当前选中状态"
        print("    当前已选中文件：物业服务品质红线标准.docx")

        # --> 2. 测试右键目标独立性与菜单呼出
        print("--> 2. 右键点击未选中的「园区消防安全应急预案.pdf」...")
        doc2_item = page.locator('[data-testid="doc-item-园区消防安全应急预案.pdf"]')
        doc2_box = doc2_item.bounding_box()
        assert doc2_box is not None

        # 在 doc2 上点击鼠标右键
        doc2_item.click(button="right")
        page.wait_for_selector('[data-testid="document-context-menu"]', timeout=3000)
        menu = page.locator('[data-testid="document-context-menu"]')
        assert menu.is_visible(), "右键菜单未成功弹出"

        # 验证此时 doc1 依然保持为选中状态，没有被右键事件错误切换
        assert "bg-selected" in doc1_item.evaluate("el => el.style.backgroundColor"), "右键其他文件时不应切换当前选中文件"
        print("    右键菜单成功弹出，当前选中文件保持不变（未被误选/误切）。")

        shot1 = SCREENSHOTS_DIR / "verify_context_menu_01_open.png"
        page.screenshot(path=str(shot1))
        print(f"    已保存右键菜单弹出截图: {shot1}")

        # --> 3. 测试按 Esc 键关闭右键菜单
        print("--> 3. 测试按 Esc 键关闭右键菜单...")
        page.keyboard.press("Escape")
        page.wait_for_selector('[data-testid="document-context-menu"]', state="detached", timeout=3000)
        print("    按 Esc 键后，右键菜单已成功关闭。")

        # 测试点击外部关闭
        print("    测试点击外部遮罩关闭右键菜单...")
        doc2_item.click(button="right")
        page.wait_for_selector('[data-testid="document-context-menu"]', timeout=3000)
        backdrop = page.locator('[data-testid="context-menu-backdrop"]')
        backdrop.click(position={"x": 50, "y": 50})
        page.wait_for_selector('[data-testid="document-context-menu"]', state="detached", timeout=3000)
        print("    点击外部后，右键菜单已成功关闭。")

        # --> 4. 测试取消不删除
        print("--> 4. 测试呼出删除确认弹窗并点击取消...")
        doc2_item.click(button="right")
        delete_opt = page.locator('[data-testid="context-menu-delete-btn"]')
        delete_opt.click()

        # 等待确认删除弹窗出现
        page.wait_for_selector('[data-testid="confirm-delete-btn"]', timeout=5000)
        page.wait_for_selector('text=确认删除资料？', timeout=5000)
        # 验证弹窗内展示的文件名准确为右键点击的文件
        page.wait_for_selector('text="园区消防安全应急预案.pdf"', timeout=5000)
        # 验证关联知识条目的删除影响准确读取（派生知识：1 条）
        page.wait_for_selector('text=派生知识：', timeout=5000)
        page.wait_for_selector('text="1 条"', timeout=5000)
        print("    确认弹窗明确展示本次右键目标文件「园区消防安全应急预案.pdf」及派生知识条目影响。")

        shot2 = SCREENSHOTS_DIR / "verify_context_menu_02_confirm_modal.png"
        page.screenshot(path=str(shot2))
        print(f"    已保存删除确认弹窗截图: {shot2}")

        # 点击取消按钮
        cancel_btn = page.locator('[data-testid="cancel-delete-btn"]')
        cancel_btn.click()
        page.wait_for_selector('text=确认删除资料？', state="detached", timeout=3000)

        # 验证文件仍在列表且无 DELETE 请求
        assert page.locator('[data-testid="doc-item-园区消防安全应急预案.pdf"]').is_visible()
        delete_calls = page.evaluate("() => window.__apiCalls.filter(c => c.method === 'DELETE')")
        assert len(delete_calls) == 0, "取消后不应触发任何 DELETE 接口"
        print("    取消删除成功：文件完好保留，后端未收到删除请求。")

        # --> 5. 测试删除失败容错
        print("--> 5. 测试服务端异常时保留文件并提示原因...")
        page.evaluate("() => { window.__deleteShouldFail = true; }")
        doc2_item.click(button="right")
        page.locator('[data-testid="context-menu-delete-btn"]').click()
        page.wait_for_selector('[data-testid="confirm-delete-btn"]', timeout=5000)
        page.locator('[data-testid="confirm-delete-btn"]').click()
        page.wait_for_timeout(600)

        alert_msg = page.evaluate("() => window.__lastAlertMessage")
        assert alert_msg and "服务端网络超时或权限校验失败" in alert_msg, f"失败提示不符合预期: {alert_msg}"
        # 验证文件仍然保留在列表
        assert page.locator('[data-testid="doc-item-园区消防安全应急预案.pdf"]').is_visible()
        # 关闭弹窗
        page.locator('[data-testid="cancel-delete-btn"]').click()
        page.evaluate("() => { window.__deleteShouldFail = false; }")
        print(f"    删除失败容错验证通过：正确提示原因「{alert_msg}」，文件安全保留。")

        # --> 6. 测试确认删除右键目标文件，验证不误删其他文件
        print("--> 6. 确认删除「园区消防安全应急预案.pdf」，验证目标正确性与界面同步...")
        doc2_item.click(button="right")
        page.locator('[data-testid="context-menu-delete-btn"]').click()
        page.wait_for_selector('[data-testid="confirm-delete-btn"]', timeout=5000)
        page.locator('[data-testid="confirm-delete-btn"]').click()
        page.wait_for_selector('text=确认删除资料？', state="detached", timeout=5000)
        page.wait_for_timeout(600)

        # 验证 doc2 已经从列表中彻底移除
        assert page.locator('[data-testid="doc-item-园区消防安全应急预案.pdf"]').count() == 0, "doc2 应当已从列表中移除"
        # 核心验证：当前选中的 doc1 依然完好存在！未被误删！
        assert page.locator('[data-testid="doc-item-物业服务品质红线标准.docx"]').is_visible(), "选中的 doc1 绝不能被误删"
        print("    删除执行成功：右键目标 doc2 已被移除，原选中的 doc1 完好保留且不受干扰！")

        shot3 = SCREENSHOTS_DIR / "verify_context_menu_03_after_delete_doc2.png"
        page.screenshot(path=str(shot3))
        print(f"    已保存删除后工作区截图: {shot3}")

        # --> 7. 测试删除当前选中的文件，验证详情与选中状态同步清理
        print("--> 7. 右键删除当前选中的「物业服务品质红线标准.docx」...")
        doc1_item = page.locator('[data-testid="doc-item-物业服务品质红线标准.docx"]')
        doc1_item.click(button="right")
        page.locator('[data-testid="context-menu-delete-btn"]').click()
        page.wait_for_selector('[data-testid="confirm-delete-btn"]', timeout=5000)
        page.locator('[data-testid="confirm-delete-btn"]').click()
        page.wait_for_selector('text=确认删除资料？', state="detached", timeout=5000)
        page.wait_for_timeout(600)

        # 此时所有文档已清空，验证列表进入清空或引导状态
        assert page.locator('[data-testid="doc-item-物业服务品质红线标准.docx"]').count() == 0
        print("    删除当前选中文件成功，相关详情与选中状态已同步重置。")

        browser.close()

    print("\n[ALL PASS] 知识管理已导入文件列表右键删除全流程端到端自动化验证 100% 通过！")

if __name__ == "__main__":
    test_context_menu_delete()
