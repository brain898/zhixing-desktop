"""AC01/AC05/AC10/AC15：生产前端+真实API+隔离库，模型全部为模拟返回。"""
import json
import os
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from test_m03_consult import TestM03Consult

ROOT=Path(__file__).resolve().parents[1]
OUT=Path(os.environ.get('ZHIXING_M03_UI_EVIDENCE_DIR', str(ROOT/'artifacts/m03/ui')))
OUT.mkdir(parents=True,exist_ok=True)

class Static(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT/'client/dist'),**kw)
    def log_message(self,*a): pass

def main():
    TestM03Consult.setUpClass()
    fixture=TestM03Consult()
    fixture.setUp()
    checks=[]; errors=[]
    server=ThreadingHTTPServer(('127.0.0.1',0),Static)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    def check(name,condition):
        assert condition,name
        checks.append(name)
    simple=fixture._skill(name='处置设备异常',publish=False)
    fields=[{'key':'desc','label':'现场描述','type':'text','required':True},
            {'key':'amount','label':'用时','type':'number','unit':'分钟','required':True},
            {'key':'level','label':'问题等级','type':'enum','allowed_values':['普通','紧急'],'required':True},
            {'key':'safe','label':'已确认安全','type':'boolean','required':True},
            {'key':'at','label':'发生时间','type':'date','required':True},
            {'key':'period','label':'统计期间','type':'period','required':True},
            {'key':'file','label':'原始记录说明','type':'file','required':True}]
    form=fixture._skill(name='核对完整巡检记录',inputs=fields,publish=False)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.on('pageerror',lambda err:errors.append(str(err)))
            def forward(route):
                req=route.request; url=urlsplit(req.url)
                response=fixture.client.request(req.method,url.path+('?' + url.query if url.query else ''),
                    headers={k:v for k,v in req.headers.items() if k.lower() in ('authorization','content-type')},content=req.post_data)
                # 与单元测试一致：持久化入队后显式驱动真实状态机，避免测试线程异步竞争。
                if req.method=='POST' and response.status_code==200 and re.fullmatch(r'/api/consult/sessions(?:/[a-z0-9]+/(?:inputs|retry))?',url.path):
                    fixture._finish(response.json()['id'])
                route.fulfill(status=response.status_code,content_type=response.headers.get('content-type','application/json'),body=response.content)
            page.route('**/api/**',forward)
            def open_as(name,password):
                login=fixture.client.post('/api/auth/login',json={'username':name,'password':password}).json()
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.evaluate("d=>{localStorage.setItem('zhixing_token',d.token);localStorage.setItem('zhixing_user',JSON.stringify(d.user));}",login)
                page.reload()
            open_as('admin','Admin@Zhixing2026')
            page.get_by_text('Agent 咨询',exact=True).first.click()
            view=page.get_by_test_id('consult-view'); view.wait_for()
            page.get_by_test_id('tab-consult-skills').click()
            simple_row=view.get_by_test_id('consult-skill-item').filter(has_text='处置设备异常')
            simple_row.get_by_role('button',name='上架',exact=True).click()
            simple_row.get_by_text('咨询中',exact=False).wait_for()
            check('可用Skill上架', '咨询中' in simple_row.inner_text())
            page.screenshot(path=str(OUT/'skills_1440x900.png'))
            simple_row.get_by_role('button',name='下架',exact=True).click()
            simple_row.get_by_role('button',name='上架',exact=True).wait_for()
            check('可用Skill下架', '已下架' in simple_row.inner_text())
            simple_row.get_by_role('button',name='上架',exact=True).click()
            simple_row.get_by_text('咨询中',exact=False).wait_for()
            page.get_by_test_id('tab-consult').click()
            check('初始示例来自真实触发描述','现场发现设备异常应如何处理' in view.inner_text())
            fixture.model.add(fixture._route(simple),fixture._execution(),{'summary':'根据核对结果登记并确认','actions':['交由责任人确认']})
            page.get_by_label('你的问题',exact=True).fill('设备异常，请核对如何处理')
            page.get_by_role('button',name='开始咨询',exact=True).click()
            report=page.get_by_test_id('consult-report'); report.wait_for(timeout=20000)
            check('提问到报告', '根据核对结果登记并确认' in report.inner_text())
            check('八部分报告', report.locator('section').count()==8)
            check('逐字风险边界','不替代现场人员对安全风险的判断。' in report.inner_text())
            check('输出字段使用中文','处置结果' in report.inner_text())
            separator=page.get_by_role('separator',name='调整咨询与报告宽度')
            dialog=view.locator('.zx-consult-dialog')
            report_pane=view.locator('.zx-consult-report-pane')
            history_pane=view.locator('.zx-consult-history')
            def widths():
                return [el.bounding_box()['width'] for el in (dialog,report_pane,history_pane)]
            def drag_by(delta):
                box=separator.bounding_box()
                page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
                page.mouse.down()
                page.mouse.move(box['x']+box['width']/2+delta,box['y']+box['height']/2,steps=10)
                page.mouse.up()
            original=widths()
            drag_by(110)
            page.wait_for_function("w=>document.querySelector('.zx-consult-dialog').getBoundingClientRect().width>w+90",arg=original[0])
            resized=widths()
            check('分隔条拖动同步调整第2和第3栏',resized[0]>original[0]+90 and resized[1]<original[1]-90)
            check('拖动不改变历史栏',abs(resized[2]-original[2])<1)
            check('拖动结束释放鼠标及恢复选中',not view.locator('.zx-consult-resizing').count())
            page.screenshot(path=str(OUT/'splitter_dragged_1440x900.png'))
            drag_by(-1200)
            check('左侧最小宽度280px',abs(widths()[0]-280)<1)
            drag_by(1200)
            check('右侧最小宽度340px',abs(widths()[1]-340)<1)
            separator.focus()
            before=widths()[0]
            separator.press('ArrowLeft')
            check('方向键可调整宽度',abs(widths()[0]-(before-16))<1)
            separator.press('Home')
            check('键盘调整遵守最小宽度',abs(widths()[0]-280)<1)
            separator.dblclick()
            check('双击恢复默认比例',abs(widths()[0]-original[0])<1)
            page.screenshot(path=str(OUT/'report_1440x900.png'))
            evidence=page.get_by_test_id('consult-evidence').first
            evidence.locator('summary').click()
            check('依据可展开原子快照和原文','m03-fixture.txt' in evidence.inner_text() and fixture.atom['statement'] in evidence.inner_text())
            page.screenshot(path=str(OUT/'report_evidence_1440x900.png'))
            page.set_viewport_size({'width':1280,'height':800})
            page.screenshot(path=str(OUT/'report_1280x800.png'))
            check('1280布局没有横向溢出',view.evaluate('e=>e.scrollWidth<=e.clientWidth+1'))
            sizes=view.locator('*').evaluate_all('els=>els.filter(e=>e.getClientRects().length&&e.textContent.trim()).map(e=>parseFloat(getComputedStyle(e).fontSize))')
            check('字号至少12px',min(sizes)>=12)
            drag_by(1200)
            page.set_viewport_size({'width':1100,'height':800})
            page.wait_for_function("()=>document.querySelector('.zx-consult-report-pane').getBoundingClientRect().width>=339")
            check('窄窗口拖动后重新限制两栏宽度',widths()[0]>=279 and widths()[1]>=339 and view.evaluate('e=>e.scrollWidth<=e.clientWidth+1'))
            page.screenshot(path=str(OUT/'splitter_narrow_1100x800.png'))
            separator.dblclick()
            page.set_viewport_size({'width':1440,'height':900})
            page.get_by_label('转人工原因',exact=True).fill('现场需要人工确认')
            page.get_by_role('button',name='转人工',exact=True).click()
            page.get_by_text('已记录转人工',exact=False).wait_for()
            check('主动转人工',len(fixture._detail(fixture.client.get('/api/consult/sessions',headers=fixture.admin).json()['items'][0]['id'])['handoffs'])==1)
            page.get_by_test_id('tab-consult-handoffs').click()
            page.get_by_test_id('consult-handoff-item').first.click()
            page.get_by_label('处理说明',exact=True).fill('责任人已现场确认')
            page.get_by_role('button',name='标记已处理',exact=True).click()
            page.get_by_text('责任人已现场确认',exact=True).wait_for()
            check('转人工记录标记处理','已处理' in page.get_by_test_id('consult-handoffs').inner_text())
            page.screenshot(path=str(OUT/'handoff_resolved_1440x900.png'))
            page.get_by_test_id('tab-consult-skills').click()
            form_row=view.get_by_test_id('consult-skill-item').filter(has_text='核对完整巡检记录')
            form_row.get_by_role('button',name='上架',exact=True).click()
            form_row.get_by_text('咨询中',exact=False).wait_for()
            page.get_by_test_id('tab-consult').click()
            check('切换标签后分隔条仍可操作',separator.count()==1)
            page.get_by_role('button',name='新咨询',exact=True).click()
            route=fixture._route(form); route['selected_skills'][0]['inputs']={}
            fixture.model.add(route,fixture._execution(),{'summary':'完整记录核对完成','actions':['请人工复核']})
            page.get_by_label('你的问题',exact=True).fill('请核对完整巡检记录，信息随后补充')
            page.get_by_role('button',name='开始咨询',exact=True).click()
            form_el=page.get_by_test_id('consult-input-form');form_el.wait_for(timeout=20000)
            check('必需输入补问七字段',form_el.locator('.zx-consult-field').count()==7)
            check('数字带单位',form_el.get_by_label('用时（分钟）',exact=True).get_attribute('type')=='number')
            check('枚举提供选项',form_el.get_by_label('问题等级',exact=True).locator('option').count()==3)
            check('布尔提供是与否',form_el.get_by_label('已确认安全',exact=True).locator('option').count()==3)
            page.get_by_label('现场描述',exact=True).fill('设备异常')
            page.get_by_label('用时（分钟）',exact=True).fill('12')
            page.get_by_label('问题等级',exact=True).select_option('普通')
            page.get_by_label('已确认安全',exact=True).select_option('true')
            page.get_by_label('发生时间',exact=True).fill('2026-02-31')
            page.get_by_label('统计期间开始日期',exact=True).fill('2026-10-01')
            page.get_by_label('统计期间结束日期',exact=True).fill('2026-10-02')
            page.get_by_label('原始记录说明',exact=True).fill('现场记录已核对')
            check('文件降级文字说明',page.get_by_label('原始记录说明',exact=True).get_attribute('type')=='text')
            page.get_by_role('button',name='提交并继续',exact=True).click()
            form_el.get_by_role('alert').wait_for()
            check('非法日期拒绝且指出字段','发生时间' in form_el.get_by_role('alert').inner_text())
            page.get_by_label('发生时间',exact=True).fill('2026-10-01 09:30')
            page.screenshot(path=str(OUT/'input_form_1440x900.png'))
            page.get_by_role('button',name='提交并继续',exact=True).click()
            page.get_by_test_id('consult-report').wait_for(timeout=20000)
            check('补问提交后完成', '完整记录核对完成' in page.get_by_test_id('consult-report').inner_text())
            check('历史保留两次咨询',page.get_by_test_id('consult-history-item').count()==2)
            page.get_by_test_id('consult-history-item').last.click()
            page.get_by_text('根据核对结果登记并确认',exact=True).wait_for()
            check('历史可回看报告',page.get_by_test_id('consult-report').count()==1)
            from model_errors import ModelRequestError
            page.get_by_role('button',name='新咨询',exact=True).click()
            fixture.model.add(fixture._route(simple),ModelRequestError('authentication','模型服务认证失败（HTTP 401），请检查服务端密钥配置后再重试',http_status=401))
            page.get_by_label('你的问题',exact=True).fill('验证模型认证失败提示')
            page.get_by_role('button',name='开始咨询',exact=True).click()
            failure=page.get_by_test_id('consult-failure');failure.wait_for(timeout=20000)
            check('失败提示指出执行阶段','执行咨询内容时失败' in failure.inner_text())
            check('认证失败提示具体原因','HTTP 401' in failure.inner_text() and '密钥配置' in failure.inner_text())
            check('永久错误提示处理后重试',failure.get_by_role('button',name='处理问题后重试本次咨询',exact=True).count()==1)
            page.screenshot(path=str(OUT/'model_authentication_failure_1440x900.png'))
            fixture.model.add(fixture._execution(),{'summary':'模型连接恢复后完成','actions':[]})
            failure.get_by_role('button',name='处理问题后重试本次咨询',exact=True).click()
            page.get_by_test_id('consult-report').wait_for(timeout=20000)
            check('修复配置后可继续原咨询','模型连接恢复后完成' in page.get_by_test_id('consult-report').inner_text())
            open_as('member','Member@Zhixing2026')
            page.get_by_text('准备',exact=False).first.wait_for()
            check('成员看不到新页面',page.get_by_test_id('consult-view').count()==0)
            check('成员看不到三个管理标签',page.get_by_test_id('tab-consult-skills').count()==0)
            page.screenshot(path=str(OUT/'member_unchanged_1440x900.png'))
            check('无页面脚本异常',not errors)
            browser.close()
        (OUT/'result.json').write_text(json.dumps({'passed':len(checks),'failed':0,'checks':checks,'kind':'isolated_db_mock_model_real_api_production_build'},ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'{len(checks)} passed, 0 failed')
    except Exception:
        if 'page' in locals():
            try: page.screenshot(path=str(OUT/'failure.png'))
            except Exception: pass  # Playwright may already have closed its event loop.
        (OUT/'result.json').write_text(json.dumps({'passed':len(checks),'failed':1,'checks':checks,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
        raise
    finally:
        fixture.doCleanups()
        server.shutdown();server.server_close()
        TestM03Consult.tearDownClass()

if __name__=='__main__': main()
