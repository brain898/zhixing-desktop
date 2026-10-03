"""从真实留存证据生成 M03 验收报告，不读取凭据或修改业务库。"""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT.parents[1]
REPORT=PROJECT/'02-方案/M03阶段报告/M03开发验收报告.md'
EVIDENCE=ROOT/'artifacts/m03'

def read(path): return json.loads(path.read_text(encoding='utf-8'))
def text(value):
    if isinstance(value,bool): return '是' if value else '否'
    if value is None: return '无'
    if isinstance(value,(dict,list)): return json.dumps(value,ensure_ascii=False)
    return str(value)
def cell(value): return text(value).replace('|','\\|').replace('\n','<br>')
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(cell(v) for v in row)+' |' for row in rows])+'\n'
def bullet(values): return '\n'.join('- '+text(v) for v in values) if values else '无。'

FILES=[
 ('server/consult.py','新增，五表初始化、统一可用范围、11个接口、任务状态机、输出校验、报告和转人工'),
 ('server/consult_calculator.py','新增，AST白名单及三种日期格式、安全复算与误差判定'),
 ('server/prompts/consult_route_v1.md','新增，路由与输入抽取'),
 ('server/prompts/consult_execute_v1.md','新增，单Skill执行；实测后补足日期引号和数字输出约束'),
 ('server/prompts/consult_report_v1.md','新增，仅合成摘要与行动建议'),
 ('server/prompts/consult_fallback_v1.md','新增，知识整理与受控引用'),
 ('server/database.py','增量挂载init_consult_tables'),('server/main.py','挂载管理员咨询路由'),
 ('server/tasks.py','增加enqueue_consult_task与启动恢复钩子'),
 ('server/hybrid_retrieval.py','原摘录读取函数提为load_evidence，保留旧名称别名及行为'),
 ('client/src/App.tsx','替换管理员准备中占位，成员分支保留'),
 ('client/src/services/api.ts','复用request增加11个咨询请求方法'),
 ('client/src/types/index.ts','增加咨询、输入、执行、报告、转人工类型'),
 ('client/src/components/views/consult/ConsultView.tsx','新增，三个标签、历史、进度、上下架、转人工处理'),
 ('client/src/components/views/consult/ConsultForm.tsx','新增，七种输入、文件文字说明、日期时间及期间'),
 ('client/src/components/views/consult/ConsultReport.tsx','新增，八部分报告、中文状态、依据展开及复算标记'),
 ('client/src/components/views/consult/consult.css','新增，白灰墨绿、12px以上、1440/1280布局'),
 ('tests/test_m03_consult.py','新增，26项模拟模型与边界测试'),
 ('tests/test_m03_calculator.py','新增，5项独立复算安全测试'),
 ('tests/verify_m03_consult_ui.py','新增，生产前端、真实接口、隔离模拟模型UI'),
 ('tests/run_m03_live_demo.py','新增，SQLite backup副本与四例真实模型记录'),
 ('tests/run_m03_regression.py','新增，22套M01/M02独立子进程回归与证据'),
 ('tests/build_m03_report.py','新增，汇总已留存证据生成本报告'),
 ('tests/acceptance.py','quick与full登记M03咨询和复算器测试'),
 ('README.md','增加M03使用、隔离演示和启动说明'),
 ('ACCEPTANCE_CHECKLIST.md','增加M03 AC01至AC15及证据'),
]
ACS=[
 ('AC01','§2 D2、§8、§9','成员11端点403、跨企业对象404及列表隔离；UI成员准备页','test_ac01_*；UI成员检查'),
 ('AC02','§6','审核门槛、同名拒绝、上下架审计','test_ac02_approval_gate_duplicate_name_and_audit；UI上下架'),
 ('AC03','§4①、§6','待复核/版本变化/知识不可用暂停，原版本恢复；每个Skill执行前复查','test_ac03_*'),
 ('AC04','§4②','范围外/下架/重复ID剔除，最多4个；超过12个粗排','test_ac04_*'),
 ('AC05','§4③','类型表单、单位与选项、非法值422、日期时间、文件文字说明','test_ac05_*；UI七字段和非法日期'),
 ('AC06','§4②④','前序值传递不补问；缺值后序不可计算且无执行调用','test_ac06_*'),
 ('AC07','§5.1、§5.2','结构/必填修正1次、越界引用及无效步骤剔除、非法输出不进合成','test_ac07_*'),
 ('AC08','§5.3','一致、更正、未经复算；白名单、恶意表达式、日期及误差','test_ac08_*；test_m03_calculator.py；真实补问复算'),
 ('AC09','§5.2、§8','模型升级/失败步骤自动记录，手动记录幂等、处理审计','test_ac09_*；UI处理；真实转人工'),
 ('AC10','§5.4、§9','八部分、风险逐字拼装、快照和原文可追溯','test_ac10_*；UI报告与依据'),
 ('AC11','§4⑥','当前管理员检索、兜底引用剔除；无可用Skill且检索空总调用0次','test_ac11_*；真实岗位职责兜底'),
 ('AC12','§4、§7、§8','沿用M02重试上限、失败后重试、无运行残留、启动恢复钩子、状态CHECK','test_ac12_*'),
 ('AC13','§7、§8','历史固定版本/输入/执行及调用元信息，密钥和令牌不进入记录','test_ac13_*'),
 ('AC14','§11','副本中三个Skill协作、补问、转人工、兜底四个真实案例','artifacts/m03/live/四个JSON与本报告全文'),
 ('AC15','§9、§10','225通过/3既有失败/1跳过；类型和构建通过；UI24项通过','regression_final/、quick证据、build.log、ui/result.json'),
]

def full_report(report):
    out=['#### 1. 结论摘要\n\n'+report['summary']]
    if report.get('notice'): out.append('\n'+report['notice'])
    out.append('#### 2. 各 Skill 结果\n')
    if not report['skill_results']: out.append('本次未执行Skill，使用知识条目整理。')
    for result in report['skill_results']:
        out.append(f"\n##### {result['name']}，第{result.get('version_number')}版，{result['status']}\n")
        fields={f['key']:f for f in result.get('output_fields',[])}
        out.append(table(['输出','结果','单位'],[(fields.get(k,{}).get('label',k),v,fields.get(k,{}).get('unit') or '') for k,v in result['outputs'].items()]))
        out.append(result.get('summary',''))
        out.append('\n'+table(['步骤','状态','结论','引用版本'],[(s['step_id'],s['state'],s['conclusion'],'、'.join(s['refs'])) for s in result['step_results']]))
        if result.get('validation'): out.append('\n程序提示：\n\n'+bullet(result['validation']))
    out.append('#### 3. 计算复算表\n\n复算只核对算术，不验证代入数值的业务口径。\n')
    out.append(table(['Skill与项目','算式','模型结果','程序结果','状态'],[(r.get('name','')+' / '+r['label'],r['expression'],r['model_result'],r['program_result'],r['status']) for r in report['recompute']]) if report['recompute'] else '本例无可用于数字输出的复算表项。')
    out.append('#### 4. 行动建议\n\n'+(bullet(report['actions']) if isinstance(report['actions'],list) else report['actions']))
    out.append('#### 5. 风险边界\n')
    for group in report['risk_boundaries']:
        out.append('\n'+group['name']+'：\n\n'+bullet(group['items']))
    if not report['risk_boundaries']: out.append('未执行Skill，无Skill风险边界；须人工确认知识适用范围。')
    out.append('#### 6. 需人工确认项\n\n'+bullet(report['manual_items']))
    out.append('#### 7. 依据清单\n')
    for evidence in report['evidence']:
        out.append('\n##### '+evidence.get('title','依据')+'\n\n'+evidence.get('statement','')+'\n')
        out.append('版本：`'+evidence['atom_version_id']+'`；来源：'+evidence.get('file_name','')+'，'+evidence.get('version_label','')+'。\n')
        for excerpt in evidence.get('evidence',[]):
            out.append('\n原文摘录（'+str(excerpt.get('field_name',''))+'）：\n\n'+ '\n'.join('> '+line for line in str(excerpt.get('excerpt','')).splitlines()))
            out.append('\n定位：'+text(excerpt.get('source_locator',{}))+'。')
    out.append('#### 8. 固定提示\n\n'+report['disclaimer'])
    return '\n\n'.join(out)

def main():
    base=read(EVIDENCE/'regression_baseline/summary.json');final=read(EVIDENCE/'regression_final/summary.json')
    assert len(final)==22
    assert [(r['file'],r['failed_tests']) for r in base]==[(r['file'],r['failed_tests']) for r in final]
    ui=read(EVIDENCE/'ui/result.json');assert ui['failed']==0
    compatibility=read(EVIDENCE/'compatibility.json');assert compatibility['existing_table_differences']==[]
    publications=read(EVIDENCE/'live/publication_list.json');assert len(publications)==10
    records=[read(EVIDENCE/'live'/f'{name}.json') for name in ('collaboration','supplement','handoff','fallback')]
    assert all(r['detail']['status']=='completed' for r in records)
    assert len(records[0]['detail']['runs'])>=3
    assert records[1]['supplement'] and all(r['status']=='一致' for r in records[1]['detail']['report']['recompute'])
    assert records[2]['detail']['handoffs'] and records[3]['detail']['report']['mode']=='fallback'
    demo=records[0]['demo_directory']
    sha=hashlib.sha256((ROOT/'server/data/zhixing.db').read_bytes()).hexdigest()
    initial=read(Path(demo)/'manifest.json')['source_sha256'];assert sha==initial
    out=['# M03 Agent 咨询开发验收报告\n\n日期：2026-10-02。需求依据：1001-M03-Agent咨询模块方案v0.3。\n\n本轮完成方案§4至§9，AC01至AC15都有隔离测试或真实调用证据。M01/M02回归没有新增失败；3项已知鉴权失败保留。业务库未上架、未写入，也未重启用户现有业务服务。未自动提交Git，未重制便携包。',
         '## 1. 修改与新增文件\n\n'+table(['文件','本轮改动'],FILES),
         '## 2. 数据库与兼容性\n\n新增consult_skill_publications、consult_sessions、consult_runs、consult_tasks、consult_handoffs五表，均使用CREATE TABLE IF NOT EXISTS和状态CHECK。企业范围来自服务端会话；同企业Skill发布唯一、同咨询活动任务唯一、同咨询未处理转人工记录唯一。调用元信息只记录用途、模型名、提示词版本、时延、尝试结果，记录不含认证信息。\n\ninit_db增量挂载初始化，不改变M01/M02已有表结构。对业务库与演示副本逐表比较sqlite_master，已有表定义差异为0；副本仅新增五表，全部skill_versions.skill_json与业务库逐条相同。摘录公共函数保留原查询及旧私有名称别名。\n\n业务库开发前后SHA-256一致：`'+sha+'`。只读业务库中仍无consult_*新表。证据：artifacts/m03/compatibility.json、副本manifest.json。',
         '## 3. 方案与验收对照\n\n'+table(['AC','方案章节','已验证行为','证据'],ACS),
         '## 4. 实际测试命令与结果\n\n### 4.1 模拟模型、隔离库与本地构建\n\n'+table(['命令','真实结果','证据'],[
             ('python tests/test_m03_consult.py','26通过，0失败；其中含3项复算单测','artifacts/m03/backend_final.log'),
             ('python tests/test_m03_calculator.py','5通过，0失败；独立复算白名单与恶意输入','artifacts/m03/calculator.log'),
             ('python tests/verify_m03_consult_ui.py',f"{ui['passed']}通过，0失败；真实API、模拟模型、当前生产构建",'artifacts/m03/ui/result.json及截图'),
             ('python tests/run_m03_regression.py --label baseline','225通过，3失败，1跳过；22套共229项','artifacts/m03/regression_baseline/'),
             ('python tests/run_m03_regression.py --label final','225通过，3既有失败，1跳过；无新增失败，退出码1如实保留','artifacts/m03/regression_final/'),
             ('python tests/acceptance.py quick','PASS；前端与Electron类型检查、5项核心回归、M03 26项及复算5项','artifacts/acceptance/20261002-163731-quick/'),
             ('npm run typecheck:frontend','通过','quick/frontend-typecheck.log'),
             ('npm run build','Vite生产构建与Electron编译通过','artifacts/m03/build.log'),
         ])+'\n所有模拟模型仅用于流程、边界及异常验证，不作为真实模型效果。UI覆盖三个标签页的主要操作、补问7种输入、非法日期、依据展开、历史回看、成员准备页、1440×900与1280×800、字号至少12px。截图已人工查看。构建保留既有electron_mirror配置及大chunk告警，均未导致失败。\n\n### 4.2 既有失败与跳过\n\n'+table(['用例','基线和本轮原因'],[
             ('test_auth_and_permissions.test_06_ac36_realtime_permission_revocation','测试账号状态变更接口被现有生产保护返回403，原测试期望200'),
             ('test_auth_and_permissions.test_07_cross_tenant_forbidden','返回生产环境禁止调用测试状态变更接口，原测试期望越权操作文字'),
             ('test_auth_and_permissions.test_08_reboot_preserves_user_status','前序禁用未执行，成员仍返回200，原测试期望403'),
             ('test_stage4a_eligibility的一项跳过','既有测试保留的跳过，基线与最终相同，具体原因见原日志'),
         ])+'\n未修改上述鉴权或账号代码来改变测试结果。',
         '## 5. AC14 真实模型演示\n\n### 5.1 环境与方法\n\n业务库隔离副本：`'+demo+'`。SQLite backup从只读连接复制zhixing.db，storage一并复制，副本内文档路径改指副本。ZHIXING_DATA_DIR、ZHIXING_DB_PATH、ZHIXING_STORAGE_DIR三项一起隔离。使用副本内管理员会话调用真实API，DeepSeek模型名、地址、密钥均来自config；实际模型为deepseek-flash。上架仅通过副本API完成。没有修改Skill JSON，没有以模拟响应代替真实调用。\n\n复合问题和时间等输入为演示构造，不是现场企业业务记录；真实调用指实际访问模型服务。时延为发起API到报告完成的本机墙钟时间，包含检索初始化、程序复算及补问自动提交，未模拟用户长时间填写等待。\n\n首选「分级→设备异常处置→高空监控」无同义输入输出连接：分级只输出等级、时限、升级和区域判定，设备处置需要异常描述、安全风险及项目类型；高空监控需要记录、期间、校准擦拭及极端天气时间。这些输出不能合法替代上述输入。本次按方案§11换为三个完整输入任务顺序执行并合成报告；from_previous使用独立模拟案例验证，不强造真实业务衔接。\n\n'+table(['案例','实际Skill','调用数','总时延秒','转人工'],[(r['case'],'、'.join(x['name'] for x in r['detail']['runs']) or '知识检索兜底',r['detail']['model_call_count'],r['elapsed_seconds'],bool(r['detail']['handoffs'])) for r in records]),
    ]
    for i,r in enumerate(records,1):
        detail=r['detail']
        out.append(f"### 5.{i+1} {r['case']} 完整记录\n\n问题原文：\n\n{r['question']}\n\n咨询ID：`{detail['id']}`；选中Skill："+'、'.join(x['name'] for x in detail['runs'])+f"；实际调用{detail['model_call_count']}次；总时延{r['elapsed_seconds']}秒；转人工：{text(bool(detail['handoffs']))}。")
        out.append('\n输入记录：\n\n```json\n'+json.dumps(detail['inputs'],ensure_ascii=False,indent=2)+'\n```')
        if r.get('supplement'): out.append('\n补问提交记录：\n\n```json\n'+json.dumps(r['supplement'],ensure_ascii=False,indent=2)+'\n```')
        out.append('\n逐阶段调用：\n\n'+table(['阶段','调用次数','实际模型','提示词版本'],[(t['task_type'],len(t['model_calls']),'、'.join(c.get('model','') for c in t['model_calls']),'、'.join(c.get('prompt_version','') for c in t['model_calls'])) for t in detail['tasks']]))
        if detail['handoffs']:
            out.append('\n转人工原因：\n\n'+bullet([v for h in detail['handoffs'] for v in h['reasons']]))
        out.append('\n'+full_report(detail['report']))
        out.append('\n原始完整证据：`artifacts/m03/live/'+r['case']+'.json`，含每个固定Skill版本、输入、执行、任务、报告及转人工快照。')
    out.append('## 6. 建议上架清单与正式操作\n\n12个已通过Skill中设备异常处置有3个同名，去重后10个。仅建议上架当前已通过且资格仍有效的版本，不表示10个均经过真实模型业务评测。设备异常处置选择较新、包含明确项目类型枚举的sk_07cf8e4cd82d，另外sk_43781746bfa5、sk_6cce48e7a5e9保留在Skill工厂但不上架。\n\n'+table(['名称','Skill ID','建议版本','说明'],[(p['name'],p['id'],p['version_number'],'去重保留；上架时重新校验知识与版本') for p in publications])+'''

正式业务库操作由用户在界面完成：

1. 完全退出演示Electron及后端，在没有三项演示路径变量的新终端启动默认后端与当前客户端。确认8766不是指向演示副本的旧服务。
2. 管理员登录，进入「Agent 咨询」的「可用 Skill」。首次启动会创建五张新表，已有知识与Skill保留。
3. 对照名称和版本逐个上架。同名项只选择上表建议项；若另一个已在架，先下架另一个，再上架目标项。
4. 显示「暂停：待复核」或「暂停：知识不可用」时先处理原有Skill复核与知识资格；显示「暂停：版本已更新」时核对新版本后重新上架。
5. 在「咨询」发起真实问题，补充输入后查看报告与依据；需要人工协助时在「转人工记录」留下处理说明。

开发过程没有执行以上正式业务库操作，也没有重启用户的现有业务服务。
''')
    out.append('## 7. 偏离、限制与未完成事项\n\n实现范围没有未完成项，无需新增产品决策。保留以下实测边界：\n\n- 真实三个任务协作是同一问题下顺序执行与统一报告，不是强行字段串接；原因和换组合已记录。from_previous的成功、缺值、类型校验通过模拟测试。\n- 首次补问实测的日期参数漏引号被安全复算器标为未经复算；补充执行提示词v1.1后重跑，12/14分钟均一致。原始记录保留在artifacts/m03/live/attempts/，没有把第一次错误算作复算成功。\n- 首次协作中的电源Skill没有number输出，模型给出的enum目标计算被剔除并列人工确认项；达标判断属于模型按Skill推理，不能声称该判断已由数值复算验证。v1.1提示词已要求只对number输出提供计算表项，现有原始证据如实保留。\n- 当前上架门槛仍为已通过（待测试），没有实现测试评测、普通成员咨询、计费、外部入口、真实工具调用、流式响应或长期记忆。\n- 业务准确率、10个去重Skill的完整业务测试、正式企业制度适用性均未声称核实。\n- 回归的3项既有鉴权失败和1项跳过仍存在，不属于本次新增退化。没有修改Skill工厂页面、成员导航和MemberHomeView，没有自动Git提交。\n')
    content='\n\n'.join(out)+'\n'
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    if REPORT.exists():
        REPORT.write_text(REPORT.read_text(encoding='utf-8')+'\n## 返工记录\n\n'+content,encoding='utf-8')
    else: REPORT.write_text(content,encoding='utf-8')
    print('报告已生成，'+str(len(content))+'字符；真实案例4个；业务库哈希未改变。')

if __name__=='__main__': main()
