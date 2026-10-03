"""隔离浏览器验证：修改后防抖质检、旧结果隐藏、乱序响应忽略、不自动保存。"""
import json
import time
from playwright.sync_api import sync_playwright
from verify_audit_relief_ui import MOCK_SCRIPT, run_static_server

extra_list = """items: [{
        ...knowledgeDetail.active_version, id:'ki_norm_1', active_version_id:'kv_norm_1',
        document_id:'doc_sop', document_title:'高压配电与应急操作规程',
        lifecycle_status:'active', quality_flags:['结构化未完成：定向重抽仍不合格']
      }],
      stats:"""
mock = MOCK_SCRIPT.replace("items: [],\n      stats:", extra_list)
mock = mock.replace("window.__auditRequests = [];",
    "knowledgeDetail.active_version.quality_flags = ['结构化未完成：定向重抽仍不合格'];\nwindow.__auditRequests = [];")
mock = mock.replace(
    "if (urlStr.includes('/knowledge/items/ki_norm_1') && method === 'PUT') {",
    """if (urlStr.includes('/knowledge/items/ki_norm_1/draft/validate') && method === 'POST') {
      const payload=JSON.parse(options.body || '{}');
      const first=payload.title.includes('首轮修改');
      return new Promise(resolve => setTimeout(() => resolve(
        new Response(JSON.stringify({
          quality_flags:first?['过期请求的误报']:[],
          blocking:[], can_confirm:true, revision_token:payload.revision_token
        }), {status:200})
      ), first?1800:90));
    }
    if (urlStr.includes('/knowledge/items/ki_norm_1') && method === 'PUT') {"""
)

def run():
    server=run_static_server(5188)
    time.sleep(.3)
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':900})
            errors=[]
            page.on('pageerror', lambda err: errors.append(str(err)))
            page.add_init_script(mock)
            page.goto('http://127.0.0.1:5188/')
            page.locator('[data-testid="knowledge-item-ki_norm_1"]').click(timeout=12000)
            page.get_by_role('button',name='编辑全部内容').click()
            title=page.locator('[data-testid="input-title"]')
            title.fill('首轮修改')
            page.wait_for_function("""() => window.__auditRequests.some(
              req => req.url.includes('/draft/validate') &&
              req.body && JSON.parse(req.body).title === '首轮修改')""",timeout=6000)
            title.fill('再次修改')
            page.get_by_text('已按当前修改重新校验：未发现自动质检问题（未保存）。').wait_for(timeout=6000)
            assert page.get_by_text('结构化未完成：定向重抽仍不合格').count()==0
            page.wait_for_timeout(1250)
            assert page.get_by_text('过期请求的误报').count()==0, '旧响应覆盖了新结果'
            requests=page.evaluate('window.__auditRequests')
            assert not any(x['method']=='PUT' for x in requests), '自动校验不得写入草稿'
            assert not any('/confirm' in x['url'] for x in requests), '自动校验不得启用知识'
            assert len([x for x in requests if '/draft/validate' in x['url']])>=2
            assert not errors, errors
            browser.close()
            print('PASS: debounce preview, newest result wins, stale warning gone, zero writes')
    finally:
        server.shutdown()

if __name__=='__main__':
    run()
