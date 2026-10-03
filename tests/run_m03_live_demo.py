"""AC14：业务库 SQLite backup 副本上的真实 DeepSeek 演示，不修改 Skill 内容。

三项路径在导入任何服务模块前一起隔离；认证只在副本创建，不输出令牌。
可用 --data-dir 续用副本，已完成案例不重复付费调用。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'server/data/zhixing.db'
OUT = ROOT/'artifacts/m03/live'
CASES = [
    ('collaboration', '住宅项目品质巡检发现一处灯具不亮，属于C类（一般品质问题），同类问题只出现1次，无客户投诉、无重点人群安全风险、影响范围未扩大、无网络传播风险，同一区域只有1项问题。该异常不涉及人身安全风险。另外在园区常规工作时段实测主备电源自动切换时间为0.3秒。请依次完成「判定巡检问题等级与整改时限」「处置巡检发现的设备异常」「核对主备电源切换时间」三个任务，汇总各自结论和处置建议。不要把设备处置建议当作已完成的现场动作。'),
    ('supplement', '请核对业主端管家平台生成的报修工单BX-001的派发与首次电话联系是否符合15分钟响应时限。时间记录我随后补充。'),
    ('handoff', '住宅项目设备巡检发现裸露带电线缆并有儿童接近，明确涉及人身安全风险，属于重大设备异常。请提供设备异常处置指引，并转工程主管人工处理。'),
    ('fallback', '客服主管日常岗位职责有哪些？我没有报修工单，也不需要响应时限核对，请仅整理岗位职责的知识依据。'),
]

def prepare_copy(directory):
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory/'zhixing.db').exists():
        with sqlite3.connect(SOURCE.resolve().as_uri()+'?mode=ro',uri=True) as src:
            with sqlite3.connect(directory/'zhixing.db') as dst:
                src.backup(dst)
        shutil.copytree(SOURCE.parent/'storage',directory/'storage',dirs_exist_ok=True)
    for name,value in {'ZHIXING_DATA_DIR':directory,'ZHIXING_DB_PATH':directory/'zhixing.db',
                       'ZHIXING_STORAGE_DIR':directory/'storage'}.items():
        os.environ[name] = str(value)
    sys.path.insert(0,str(ROOT/'server'))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--case',choices=[c[0] for c in CASES])
    parser.add_argument('--rerun',action='store_true',help='保留上一轮证据后重新实测指定案例')
    args=parser.parse_args()
    directory=(args.data_dir or Path(tempfile.mkdtemp(prefix='zhixing_m03_live_'))).resolve()
    if directory==ROOT or ROOT in directory.parents:
        raise SystemExit('演示目录必须在代码目录外')
    prepare_copy(directory)
    from database import init_db,get_db
    from auth import create_session,destroy_session
    from main import app
    from tasks import wait_for_background_tasks
    from fastapi.testclient import TestClient
    import config
    if not config.DEEPSEEK_API_KEY:
        raise SystemExit('真实模型未配置，未发起模型调用')
    init_db()
    OUT.mkdir(parents=True,exist_ok=True)
    with get_db() as conn:
        user=dict(conn.execute("SELECT id,organization_id FROM users WHERE role='admin' AND account_status='active' ORDER BY created_at LIMIT 1").fetchone())
        # 仅修正副本内原始文件存储路径，确保整个演示的文件读取指向副本。
        for row in conn.execute('SELECT id,storage_reference FROM document_versions').fetchall():
            candidate=directory/'storage'/Path(row['storage_reference']).name
            if candidate.exists():
                conn.execute('UPDATE document_versions SET storage_reference=? WHERE id=?',(str(candidate),row['id']))
    token=create_session(user['id'],user['organization_id'])
    headers={'Authorization':'Bearer '+token}
    client=TestClient(app)
    def call(method,path,body=None):
        response=client.request(method,'/api/consult'+path,headers=headers,json=body)
        if response.status_code!=200:
            raise RuntimeError(f'{method} {path}: HTTP {response.status_code}: {response.text}')
        return response.json()
    try:
        candidates=call('GET','/skills')['items']
        # 同名优先选择最后创建的已通过项；使用API门槛确认原子仍有资格。
        groups={}
        for row in candidates:
            if row.get('skill_status',row.get('status'))=='approved':
                groups.setdefault(row['name'],[]).append(row)
        selected=[]
        # 明确选择需求核对过的设备异常版本，另外九种任务保留唯一候选。
        for name,rows in groups.items():
            chosen=next((r for r in rows if r['skill_id']=='sk_07cf8e4cd82d'),rows[0])
            for row in rows:
                if row['skill_id']!=chosen['skill_id'] and row.get('publication_status')=='published':
                    call('POST',f"/skills/{row['skill_id']}/unpublish",{'reason':'演示同名去重'})
            call('POST',f"/skills/{chosen['skill_id']}/publish",{})
            selected.append(chosen)
        (OUT/'publication_list.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
        for name,question in CASES:
            if args.case and args.case!=name: continue
            file=OUT/(name+'.json')
            if file.exists() and args.rerun:
                attempts=OUT/'attempts';attempts.mkdir(exist_ok=True)
                shutil.copy2(file,attempts/(name+'_'+str(time.time_ns())+'.json'))
            if file.exists() and not args.rerun and json.loads(file.read_text(encoding='utf-8')).get('detail',{}).get('status')=='completed':
                print(name+': 已有完成证据，跳过重复调用',flush=True);continue
            started=time.monotonic();result=call('POST','/sessions',{'question':question});sid=result.get('id') or result['session_id']
            supplement=None
            deadline=time.monotonic()+1500
            while time.monotonic()<deadline:
                detail=call('GET','/sessions/'+sid)
                if detail['status']=='awaiting_input':
                    if name!='supplement': break
                    values={'repair_order_no':'BX-001','order_created_at':'2026-10-01 09:00',
                            'dispatch_at':'2026-10-01 09:12','first_contact_at':'2026-10-01 09:14',
                            'response_limit_minutes':15}
                    supplement={}
                    for field in detail['form']['fields']:
                        if field['key'] not in values:
                            raise RuntimeError('出现未预设的演示补问字段：'+field['label'])
                        supplement.setdefault(field['skill_id'],{})[field['key']]=values[field['key']]
                    call('POST','/sessions/'+sid+'/inputs',{'inputs':supplement})
                if detail['status'] in ('completed','failed'): break
                time.sleep(.3)
            record={'case':name,'question':question,'supplement':supplement,'elapsed_seconds':round(time.monotonic()-started,2),
                    'demo_directory':str(directory),'detail':detail,'real_model':True}
            file.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'case':name,'status':detail['status'],'seconds':record['elapsed_seconds'],
                              'calls':detail.get('model_call_count'),'skills':[r.get('skill_name',r.get('name')) for r in detail.get('runs',[])],
                              'handoff':bool(detail.get('handoffs'))},ensure_ascii=False),flush=True)
            if detail['status']!='completed':
                print('未完成证据已保存，继续其他独立案例',flush=True)
        wait_for_background_tasks(timeout=30)
        manifest={'demo_directory':str(directory),'source':str(SOURCE.resolve()),
                  'source_sha256_after':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'real_model':True}
        (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        destroy_session(token)
        client.close()

if __name__=='__main__':
    main()
