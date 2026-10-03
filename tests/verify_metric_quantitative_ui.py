"""隔离浏览器核验：旧条目重复定量要求可显式整理；数值冲突不自动覆盖。"""
import time
from playwright.sync_api import sync_playwright
from verify_live_draft_validation_ui import mock as BASE_MOCK, run_static_server

SETUP = """
knowledgeDetail.active_version.primary_category = '指标数据';
knowledgeDetail.active_version.atom_type = '指标';
knowledgeDetail.active_version.statement = '一级紧急报修维修技工到场时限不得超过15分钟。';
knowledgeDetail.active_version.metric_definition = {name:'到场时限', criteria:'',unit:'',period:'',rows:[{
  name:'维修技工到达现场时限',relation:'≤',value:'15',unit:'分钟',
  condition:'室内跑水、总闸跳闸、电梯困人等一级紧急报修',
  period:'',linkage:'',note:'不得超过15分钟'
}]};
"""

def run():
    server = run_static_server(5189)
    time.sleep(.3)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors=[]
            page.on('pageerror',lambda exc: errors.append(str(exc)))
            page.add_init_script(BASE_MOCK.replace('const originalFetch = window.fetch;',SETUP+'\nconst originalFetch = window.fetch;'))
            page.goto('http://127.0.0.1:5189/')
            page.locator('[data-testid="knowledge-item-ki_norm_1"]').click(timeout=12000)
            page.get_by_role('button',name='编辑全部内容').wait_for(timeout=7000)
            page.get_by_text('定性栏包含重复的定量阈值，编辑时可整理。').wait_for(timeout=7000)
            page.get_by_role('button',name='编辑全部内容').click()
            button=page.get_by_role('button',name='整理为定量要求')
            button.wait_for()
            assert page.locator('input[placeholder="定性要求"]').input_value()=='不得超过15分钟'
            assert page.locator('input[placeholder="数值 / 范围"]').input_value()=='15'
            button.click()
            assert page.locator('input[placeholder="定性要求"]').input_value()==''
            assert page.locator('input[placeholder="比较关系"]').input_value()=='≤'
            assert page.locator('input[placeholder="单位"]').input_value()=='分钟'
            assert page.locator('input[placeholder="适用条件"]').input_value().startswith('室内跑水')
            page.get_by_text('已按当前修改重新校验：未发现自动质检问题（未保存）。').wait_for(timeout=7000)
            requests=page.evaluate('window.__auditRequests')
            assert not any(req['method']=='PUT' or '/confirm' in req['url'] for req in requests)
            assert not errors, errors
            browser.close()
            print('PASS: old metric requires explicit split, note cleared, context retained, no auto save/activate')
    finally:
        server.shutdown()

if __name__ == '__main__':
    run()
