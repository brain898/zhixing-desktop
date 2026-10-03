"""隔离 mock 浏览器验证：指标行、段落级证据定位、默认阅读和点击编辑。"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from verify_audit_relief_ui import MOCK_SCRIPT, run_static_server

BASE=Path(__file__).resolve().parents[1]
SOURCE='承压不低于0.8 MPa，并保持30分钟。'
EXTRA=r"""
const sourceText = '承压不低于0.8 MPa，并保持30分钟。';
const sampleMetric = {name:'承压验收', unit:'', period:'', criteria:'', rows:[
  {name:'压力', relation:'不低于', value:'0.8', unit:'MPa', condition:'', period:'', linkage:'', note:''},
  {name:'持续时间', relation:'', value:'30', unit:'分钟', condition:'', period:'', linkage:'在上述压力条件下保持', note:''}
]};
const sample = mockChapterReviewResponse.chapters[0].items.find(i => i.item_id==='ki_norm_1');
sample.title='承压验收';
sample.statement='承压验收要求';
sample.primary_category='指标数据';
sample.atom_type='指标';
sample.actions=[];
sample.conditions=[];
sample.exceptions=[];
sample.metric_definition=sampleMetric;
knowledgeDetail.active_version.title='承压验收';
knowledgeDetail.active_version.statement='承压验收要求';
knowledgeDetail.active_version.primary_category='指标数据';
knowledgeDetail.active_version.atom_type='指标';
knowledgeDetail.active_version.metric_definition=sampleMetric;
knowledgeDetail.active_version.actions=[];
knowledgeDetail.active_version.conditions=[];
knowledgeDetail.active_version.exceptions=[];
knowledgeDetail.evidence=[{id:'metric-ev', field_name:'metric_definition', excerpt:sourceText, accuracy_level:'exact',
  source_block_id:'blk_2', block_index:11, block_type:'paragraph',
  heading_path:'验收', page_number:12, paragraph_anchor:'p_11', text_content:sourceText}];
"""

def run():
    server=run_static_server(5184)
    time.sleep(.35)
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            page=browser.new_page(viewport={'width':1440,'height':900})
            errs=[]
            page.on('pageerror',lambda e: errs.append(str(e)))
            page.add_init_script(MOCK_SCRIPT+'\n'+EXTRA)
            page.goto('http://127.0.0.1:5184/')
            page.wait_for_load_state('networkidle')
            page.get_by_text('高压配电与应急操作规程').first.click()
            page.locator('[data-testid="mode-chapter-review"]').click()
            card=page.locator('[data-testid="chapter-item-ki_norm_1"]')
            card.locator('[data-testid="structured-review"]').wait_for()
            assert card.get_by_text('0.8',exact=False).count()>0
            assert card.get_by_text('在上述压力条件下保持').count()>0
            card.get_by_role('button',name='详细校对').click()
            page.get_by_role('button',name='编辑全部内容').wait_for()
            table=page.locator('[data-testid="structured-review"]').last
            assert table.get_by_text('压力',exact=True).count()>0
            assert table.get_by_text('持续时间',exact=True).count()>0
            assert table.get_by_text('在上述压力条件下保持').count()>0
            assert page.get_by_text('逐项核对指标').count()==0, '默认不得展示编辑表单'
            table.get_by_role('button',name='定位↗').first.click()
            page.get_by_text('仅有段落级证据，不能精确定位字段').wait_for()
            target=page.locator('#proofread-source-blk_2')
            assert target.count()==1
            assert 'brand-accent' in (target.get_attribute('style') or '')
            screenshot=BASE/'screenshots'/'structured_review_metric.png'
            page.screenshot(path=str(screenshot),full_page=True)
            table.get_by_role('button',name='修改').first.click()
            page.get_by_text('逐项核对指标').wait_for()
            editor=page.get_by_text('逐项核对指标').locator('xpath=..')
            assert page.get_by_text('检查项 1').count()>0
            assert page.get_by_text('检查项 2').count()>0
            page.get_by_role('button',name='＋ 新增检查项').click()
            page.get_by_text('检查项 3').wait_for()
            assert not errs, errs
            browser.close()
            print('PASS: chapter/detail share metric table, no default inputs, source locator, row edit; screenshot:',screenshot)
    finally:
        server.shutdown()

if __name__=='__main__':
    run()

