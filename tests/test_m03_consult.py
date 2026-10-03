"""M03 v0.3 AC01–AC13：隔离库、模拟模型的流程/边界/异常测试。

模型返回全部由 patch 提供，不代表真实模型效果；不读取或写入业务库。
AC14 的真实模型案例另由演示核验记录，AC15 由回归与 UI 脚本证明。
"""
import copy
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
_IMPORT_DIR = Path(tempfile.mkdtemp(prefix="zhixing_m03_import_"))
os.environ.update(ZHIXING_DATA_DIR=str(_IMPORT_DIR), ZHIXING_DB_PATH=str(_IMPORT_DIR / "zhixing.db"),
                  ZHIXING_STORAGE_DIR=str(_IMPORT_DIR / "storage"))
sys.path.insert(0, str(SERVER_DIR))
from test_env_helper import cleanup_test_db, setup_test_db
from fastapi.testclient import TestClient
import config
import consult
import consult_calculator as calculator
import skill_generation as sg
import tasks
from database import get_db, init_db
from main import app
from skill_validation import validate_skill


class ModelQueue:
    """严格队列：意外多调用立即失败，避免以宽松桩掩盖调用次数错误。"""
    def __init__(self):
        self.responses = []
        self.messages = []

    def add(self, *responses):
        self.responses.extend(responses)

    def __call__(self, messages, **kwargs):
        self.messages.append(copy.deepcopy(messages))
        if not self.responses:
            raise AssertionError("模拟模型收到未预期调用")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value(messages)
        return (json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value), "deepseek-mock"


class TestM03Consult(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = setup_test_db("m03_consult")
        # 三项路径均保持隔离；业务路径从未作为连接目标。
        cls.old_data_dir = os.environ.get("ZHIXING_DATA_DIR")
        os.environ["ZHIXING_DATA_DIR"] = str(cls.db_path.parent)
        cls.client = TestClient(app)
        cls.admin, cls.user = cls._login("admin", "Admin@Zhixing2026")
        cls.member, _ = cls._login("member", "Member@Zhixing2026")
        cls.other, cls.other_user = cls._login("other_admin", "Other@Zhixing2026")
        cls.org = cls.user["organization_id"]

    @classmethod
    def tearDownClass(cls):
        cleanup_test_db(cls.db_path)
        if cls.old_data_dir is None:
            os.environ.pop("ZHIXING_DATA_DIR", None)
        else:
            os.environ["ZHIXING_DATA_DIR"] = cls.old_data_dir

    @classmethod
    def _login(cls, username, password):
        r = cls.client.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["user"]

    def setUp(self):
        self.prefix = uuid.uuid4().hex[:8]
        self.model = ModelQueue()
        for p in (patch.object(consult, "post_chat_completion", self.model),
                  patch.object(tasks, "enqueue_consult_task", return_value=None),
                  patch.object(config, "DEEPSEEK_API_KEY", "unit-test-placeholder")):
            p.start()
            self.addCleanup(p.stop)
        with get_db() as conn:
            conn.execute("UPDATE consult_skill_publications SET status='unpublished'")
        self.atom = self._atom()

    def _atom(self):
        now = datetime.now(timezone.utc).isoformat()
        d, dv, ki, kv, b = [f"m03_{kind}_{self.prefix}" for kind in ("doc", "dv", "ki", "kv", "block")]
        statement = "发现设备异常先登记，涉及现场安全时交由责任人确认。"
        with get_db() as c:
            c.execute("""INSERT INTO documents(id,organization_id,title,active_version_id,access_scope,is_deleted,created_at,updated_at)
                VALUES(?,?,'M03隔离测试资料',?,'admin_only',0,?,?)""", (d,self.org,dv,now,now))
            c.execute("""INSERT INTO document_versions(id,document_id,organization_id,version_label,file_name,file_type,file_size,
                content_hash,storage_reference,uploaded_by,uploaded_at,processing_status)
                VALUES(?,?,?,'v1','m03-fixture.txt','txt',100,?,'m03-fixture.txt',?,?,'completed')""",
                (dv,d,self.org,uuid.uuid4().hex,self.user["id"],now))
            c.execute("""INSERT INTO knowledge_items(id,document_id,organization_id,active_version_id,access_scope,lifecycle_status,
                is_excluded,created_at,updated_at) VALUES(?,?,?,?,'admin_only','active',0,?,?)""", (ki,d,self.org,kv,now,now))
            c.execute("""INSERT INTO knowledge_versions(id,item_id,organization_id,source_document_version_id,version_number,title,
                content,primary_category,atom_type,subject,statement,conditions_json,actions_json,exceptions_json,customer_types_json,
                business_scenes_json,review_status,index_status,revision_token,created_at,created_by)
                VALUES(?,?,?,?,1,'设备异常处置依据',?,'制度与标准','规则','物业人员',?,'[]','[]','[]','["住宅业主"]',
                '["设备巡检"]','confirmed','ready',?,?,?)""", (kv,ki,self.org,dv,statement,statement,uuid.uuid4().hex,now,self.user["id"]))
            c.execute("""INSERT INTO source_blocks(id,document_version_id,organization_id,block_index,block_type,text_content,created_at)
                VALUES(?,?,?,1,'paragraph',?,?)""", (b,dv,self.org,statement,now))
            c.execute("""INSERT INTO knowledge_evidence(id,knowledge_version_id,source_block_id,organization_id,field_name,excerpt,
                accuracy_level,created_at) VALUES(?,?,?,?, 'statement',?,'high',?)""", ("ev_"+self.prefix,kv,b,self.org,statement,now))
        return {"item_id":ki,"version_id":kv,"doc_id":d,"doc_version_id":dv,"statement":statement}

    def _skill(self, name=None, inputs=None, outputs=None, status="approved", publish=True, on_fail="转人工"):
        name = name or "核对设备异常" + uuid.uuid4().hex[:4]
        v = self.atom["version_id"]
        candidate = {"schema_version":"1.0","name":name,"goal":"核对设备异常并给出处理结果",
            "trigger_description":"现场发现设备异常应如何处理","task_type":"判断分级","scene_id":"m03_fixture",
            "applies_to":{"customer_types":["住宅业主"],"property_types":[],"conditions":[]},
            "not_applies_to":["不处理收费争议"],"knowledge_refs":[{"atom_item_id":self.atom["item_id"],
            "atom_version_id":v,"role":"判断规则","used_in_steps":["s2"]}],
            "inputs":inputs or [{"key":"situation","label":"现场情况","type":"text","required":True}],
            "preconditions":[],"outputs":outputs or [{"key":"result","label":"处置结果","type":"text","required":True}],
            "steps":[{"step_id":"s1","kind":"输入校验","action":"检查现场情况是否齐全","basis":"通用操作","on_fail":"补问"},
                {"step_id":"s2","kind":"规则判断","action":self.atom["statement"],"basis":"有原子依据","refs":[v],"on_fail":on_fail}],
            "risk_boundary":["不替代现场人员对安全风险的判断。", "须由责任人确认处置条件。"],
            "escalation_conditions":["涉及现场安全时交由责任人确认"],
            "generation_confidence":{"level":"高","reason":"隔离测试固定样本"}}
        with get_db() as c:
            actor = sg.load_actor(c,self.user["id"],self.org)
            report = validate_skill(c,candidate,actor,pool_ids=None)
            self.assertEqual([x.to_dict() for x in report.issues if x.level=="hard_error"], [])
            stored = sg.store_candidate(c,org_id=self.org,scene_id="m03_fixture",batch_id=None,task_key=None,
                candidate=candidate,report=report,generation={"model_name":"fixture","prompt_version":"fixture"},created_by=self.user["id"])
            c.execute("UPDATE skills SET status=? WHERE id=?", (status,stored["skill_id"]))
        obj={**stored,"content":candidate}
        if publish:
            self.assertEqual(self._post(f"skills/{obj['skill_id']}/publish",{}).status_code,200)
        return obj

    def _post(self, suffix, body, headers=None):
        return self.client.post("/api/consult/"+suffix,json=body,headers=headers or self.admin)

    def _detail(self,sid):
        r=self.client.get("/api/consult/sessions/"+sid,headers=self.admin)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def _finish(self, sid):
        for _ in range(30):
            with get_db() as c:
                queued=[r[0] for r in c.execute("SELECT id FROM consult_tasks WHERE session_id=? AND status='queued' ORDER BY created_at,id",(sid,))]
            if not queued:
                return self._detail(sid)
            for tid in queued:
                consult.run_consult_task(tid)
        self.fail("咨询任务未收敛")

    def _session(self):
        r=self._post("sessions",{"question":"设备异常，请核对如何处理","organization_id":self.other_user["organization_id"]})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()["id"]

    @staticmethod
    def _route(*skills):
        return {"selected_skills":[{"skill_id":s["skill_id"],"reason":"匹配现场问题","inputs":{"situation":"设备异常"},"from_previous":{}} for s in skills]}

    def _execution(self, outputs=None, **changes):
        result={"status":"完成","outputs":outputs if outputs is not None else {"result":"登记并确认"},
            "step_results":[{"step_id":"s2","conclusion":"按处置依据登记","refs":[self.atom["version_id"]],"state":"完成"}],
            "calculations":[],"escalation":{"triggered":False,"matched_conditions":[],"reason":""},
            "missing_inputs":[],"summary":"已按依据核对"}
        result.update(changes)
        return result

    def _success(self, *skills, executions=None):
        self.model.add(self._route(*skills),*(executions or [self._execution() for s in skills]),{"summary":"根据合规输出登记并确认","actions":["交由责任人确认"]})
        sid=self._session()
        d=self._finish(sid)
        self.assertEqual(d["status"],"completed",d)
        return sid,d

    def test_ac01_all_11_endpoints_member_forbidden(self):
        """AC01：所有路径鉴权均由 require_admin 执行。"""
        endpoints=[("get","skills"),("post","skills/missing/publish"),("post","skills/missing/unpublish"),
            ("post","sessions"),("get","sessions"),("get","sessions/missing"),("post","sessions/missing/inputs"),
            ("post","sessions/missing/retry"),("post","sessions/missing/handoff"),("get","handoffs"),("post","handoffs/missing/resolve")]
        for method,path in endpoints:
            with self.subTest(path=path):
                args={"json":{"question":"测试"}} if method=="post" else {}
                r=getattr(self.client,method)("/api/consult/"+path,headers=self.member,**args)
                self.assertEqual(r.status_code,403,r.text)

    def test_ac01_cross_enterprise_and_server_session_scope(self):
        """AC01：忽略客户端企业字段，其他企业所有对象接口均为 404。"""
        s=self._skill(); sid,d=self._success(s)
        hand=self._post(f"sessions/{sid}/handoff",{"reason":"管理员确认"}).json()
        hid=hand.get("id") or hand.get("handoff_id")
        for method,path in [("post",f"skills/{s['skill_id']}/publish"),("post",f"skills/{s['skill_id']}/unpublish"),
            ("get",f"sessions/{sid}"),("post",f"sessions/{sid}/inputs"),("post",f"sessions/{sid}/retry"),
            ("post",f"sessions/{sid}/handoff"),("post",f"handoffs/{hid}/resolve")]:
            args={"json":{}} if method=="post" else {}
            self.assertEqual(getattr(self.client,method)("/api/consult/"+path,headers=self.other,**args).status_code,404,path)
        for kind, key, identity in [("skills","skill_id",s["skill_id"]),("sessions","id",sid),("handoffs","id",hid)]:
            items=self.client.get("/api/consult/"+kind,headers=self.other).json()["items"]
            self.assertNotIn(identity,{x[key] for x in items})
        with get_db() as c:
            self.assertEqual(c.execute("SELECT organization_id FROM consult_sessions WHERE id=?",(sid,)).fetchone()[0],self.org)

    def test_ac02_approval_gate_duplicate_name_and_audit(self):
        """AC02：审核门槛、同名去重、上下架审计。"""
        for status in ("pending_review","rejected","needs_recheck","validation_failed","generating"):
            s=self._skill(status=status,publish=False)
            self.assertIn(self._post(f"skills/{s['skill_id']}/publish",{}).status_code,(400,409,422))
        a=self._skill(name="核对设备异常去重")
        b=self._skill(name="核对设备异常去重",publish=False)
        r=self._post(f"skills/{b['skill_id']}/publish",{})
        self.assertIn(r.status_code,(409,422)); self.assertIn("同名",r.text)
        self.assertEqual(self._post(f"skills/{a['skill_id']}/unpublish",{"reason":"替换同名版本"}).status_code,200)
        self.assertEqual(self._post(f"skills/{b['skill_id']}/publish",{}).status_code,200)
        with get_db() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM audit_logs WHERE target_id IN (?,?)",(a["skill_id"],b["skill_id"]))]
        self.assertGreaterEqual(len(rows),3)
        self.assertIn("替换同名版本",json.dumps(rows,ensure_ascii=False))

    def test_ac03_three_pause_causes_and_automatic_restore(self):
        """AC03：待复核、版本更新、原子不具资格不进入路由；原版本复核通过自动恢复。"""
        s=self._skill(); sid=s["skill_id"]
        def listing():
            return next(x for x in self.client.get("/api/consult/skills",headers=self.admin).json()["items"] if x["skill_id"]==sid)
        with get_db() as c: c.execute("UPDATE skills SET status='needs_recheck' WHERE id=?",(sid,))
        self.assertIn("待复核",json.dumps(listing(),ensure_ascii=False))
        with get_db() as c: c.execute("UPDATE skills SET status='approved' WHERE id=?",(sid,))
        self.assertIn("咨询中",json.dumps(listing(),ensure_ascii=False))
        with get_db() as c: c.execute("UPDATE skills SET current_version_id=NULL WHERE id=?",(sid,))
        self.assertIn("版本已更新",json.dumps(listing(),ensure_ascii=False))
        with get_db() as c:
            c.execute("UPDATE skills SET current_version_id=? WHERE id=?",(s["version_id"],sid))
            c.execute("UPDATE knowledge_items SET lifecycle_status='disabled' WHERE id=?",(self.atom["item_id"],))
        self.assertIn("知识不可用",json.dumps(listing(),ensure_ascii=False))
        with patch.object(consult,"hybrid_search",return_value=[]):
            ident=self._session(); done=self._finish(ident)
        self.assertEqual(done["status"],"completed")
        self.assertEqual(len(self.model.messages),0)

    def test_ac04_out_of_scope_unpublished_and_limit_four(self):
        """AC04：范围外与下架 ID 剔除并留下记录；执行最多四个。"""
        ss=[self._skill() for _ in range(5)]
        off=self._skill(publish=False)
        route=self._route(*ss)
        route["selected_skills"].insert(0,{"skill_id":"other-enterprise-id","inputs":{},"reason":"越界"})
        route["selected_skills"].insert(1,{"skill_id":off["skill_id"],"inputs":{},"reason":"已下架"})
        self.model.add(route,*[self._execution() for _ in range(4)],{"summary":"核对完成","actions":[]})
        d=self._finish(self._session())
        self.assertEqual(d["status"],"completed",d)
        self.assertEqual([r["skill_id"] for r in d["runs"]],[s["skill_id"] for s in ss[:4]])
        route_text=json.dumps(d["route"],ensure_ascii=False)
        self.assertIn("other-enterprise-id",route_text)
        self.assertIn(off["skill_id"],route_text)
        self.assertEqual(len(self.model.messages),6)

    def test_ac03_rechecks_before_each_skill(self):
        """AC03：路由后、前序执行期间失去资格的后序不得执行。"""
        a=self._skill(); b=self._skill()
        def change_during_execution(messages):
            with get_db() as c:
                c.execute("UPDATE skills SET status='needs_recheck' WHERE id=?",(b['skill_id'],))
            return self._execution()
        self.model.add(self._route(a,b),change_during_execution,{"summary":"后序已暂停","actions":[]})
        d=self._finish(self._session())
        self.assertEqual(d['status'],'completed')
        self.assertEqual(d['runs'][1]['status'],'skipped')
        self.assertEqual(len(self.model.messages),3)
        self.assertIn('待复核',json.dumps(d['report'],ensure_ascii=False))

    def test_ac04_coarse_ranking_limits_prompt_to_twelve(self):
        """§4/AC04：超过十二个可用项时粗排后只交模型十二个。"""
        for _ in range(13): self._skill()
        self.model.add({'selected_skills':[]})
        with patch.object(consult,'hybrid_search',return_value=[]):
            d=self._finish(self._session())
        self.assertEqual(d['status'],'completed')
        payload=json.loads(self.model.messages[0][1]['content'])
        self.assertEqual(len(payload['skills']),12)

    def test_ac05_all_form_types_invalid_values_and_continue(self):
        """AC05：七种输入类型含文件降级文字；单位、选项、非法值和补问继续。"""
        fields=[{"key":"desc","label":"描述","type":"text","required":True},
            {"key":"money","label":"金额","type":"number","unit":"元","required":True},
            {"key":"grade","label":"等级","type":"enum","allowed_values":["普通","紧急"],"required":True},
            {"key":"safe","label":"已确认安全","type":"boolean","required":True},
            {"key":"date","label":"日期","type":"date","required":True},
            {"key":"period","label":"期间","type":"period","required":True},
            {"key":"file","label":"文件说明","type":"file","required":True},
            {"key":"optional","label":"补充说明","type":"text","required":False}]
        s=self._skill(inputs=fields)
        route=self._route(s); route["selected_skills"][0]["inputs"]={}
        self.model.add(route)
        sid=self._session(); d=self._finish(sid)
        self.assertEqual(d["status"],"awaiting_input")
        self.assertEqual({f["key"] for f in d["form"]["fields"]},{f["key"] for f in fields if f["required"]})
        fs={f["key"]:f for f in d["form"]["fields"]}
        self.assertEqual(fs["money"]["unit"],"元"); self.assertEqual(fs["grade"]["allowed_values"],["普通","紧急"])
        self.assertEqual(fs["file"]["type"],"text")
        valid={"desc":"已检查","money":12.5,"grade":"普通","safe":True,"date":"2026-10-01", "period":"2026-10","file":"已说明原始记录"}
        for key,bad in [("desc",{}),("money","许多"),("money",True),("grade","不存在"),("safe","不确定"),
                         ("date","2026-02-31"),("period",{}),("file",[])]:
            with self.subTest(key=key,bad=bad):
                r=self._post(f"sessions/{sid}/inputs",{"inputs":{s["skill_id"]:{**valid,key:bad}}})
                self.assertEqual(r.status_code,422,r.text)
        self.assertEqual(len(self.model.messages),1)
        self.model.add(self._execution(),{"summary":"已完成核对","actions":[]})
        self.assertEqual(self._post(f"sessions/{sid}/inputs",{"inputs":{s["skill_id"]:valid}}).status_code,200)
        self.assertEqual(self._finish(sid)["status"],"completed")

    def test_ac06_from_previous_transfer_without_form(self):
        """AC06：前序输出自动传入后序，不生成补问字段。"""
        a=self._skill(); b=self._skill()
        route=self._route(a,b); route["selected_skills"][1].update(inputs={},from_previous={"situation":{"skill_id":a["skill_id"],"output_key":"result"}})
        self.model.add(route,self._execution({"result":"前序确定值"}),self._execution(),{"summary":"协作核对完成","actions":[]})
        d=self._finish(self._session())
        self.assertEqual(d["status"],"completed",d)
        self.assertEqual(d["runs"][1]["inputs"]["situation"],"前序确定值")
        self.assertEqual(d["form"]["fields"],[])
        self.assertEqual(len(self.model.messages),4)

    def test_ac06_previous_missing_skips_second_model_call(self):
        """AC06：前序没有所需值，后序不可计算且不调用执行模型。"""
        a=self._skill(outputs=[{"key":"result","label":"可选结果","type":"text","required":False}]); b=self._skill()
        route=self._route(a,b); route["selected_skills"][1].update(inputs={},from_previous={"situation":{"skill_id":a["skill_id"],"output_key":"result"}})
        self.model.add(route,self._execution({}),{"summary":"后序不可计算","actions":["人工补充前序结果"]})
        d=self._finish(self._session())
        self.assertEqual(d["status"],"completed",d)
        self.assertIn("不可计算",json.dumps(d["runs"][1],ensure_ascii=False))
        self.assertEqual(len(self.model.messages),3)

    def test_ac07_refs_steps_and_invalid_output_filtered(self):
        """AC07：引用越界/无效步骤删除，非法 enum/number 不参与报告合成。"""
        s=self._skill(outputs=[{"key":"grade","label":"等级","type":"enum","allowed_values":["普通","紧急"],"required":True},
            {"key":"amount","label":"金额","type":"number","unit":"元","required":True}])
        out=self._execution({"grade":"幻想等级","amount":"很多"},step_results=[
            {"step_id":"s2","conclusion":"按依据","refs":[self.atom["version_id"],"alien-ref"],"state":"完成"},
            {"step_id":"s999","conclusion":"越界步骤","refs":[self.atom["version_id"]],"state":"完成"}])
        sid,d=self._success(s,executions=[out]); run=d["runs"][0]
        self.assertEqual(run["output"]["outputs"],{})
        self.assertEqual(len(run["output"]["step_results"]),1)
        self.assertEqual(run["output"]["step_results"][0]["refs"],[self.atom["version_id"]])
        self.assertIn("越界引用已剔除",json.dumps(run["validation"],ensure_ascii=False))
        self.assertIn("取值不合规",json.dumps(d["report"]["manual_items"],ensure_ascii=False))
        report_prompt=json.dumps(self.model.messages[-1],ensure_ascii=False)
        self.assertNotIn("幻想等级",report_prompt); self.assertNotIn('"amount": "很多"',report_prompt)

    def test_ac07_required_missing_retries_once_then_skill_failed(self):
        """AC07/§5.2：必填输出缺失一次结构重试，仍失败记为失败。"""
        s=self._skill(); self.model.add(self._route(s),self._execution({}),self._execution({}),{"summary":"该核对失败","actions":[]})
        d=self._finish(self._session())
        self.assertIn(d["status"],("completed","failed"),d)
        self.assertEqual(d["runs"][0]["status"],"failed")
        self.assertIn("result",json.dumps(self.model.messages[2],ensure_ascii=False))

    def test_ac08_recompute_consistent_corrected_and_unverified(self):
        """AC08：一致、不一致覆盖、无法解析三种状态出现在实际报告。"""
        s=self._skill(outputs=[{"key":k,"label":k,"type":"number","unit":"分钟","required":True} for k in ("ok","wrong","unknown")])
        calculations=[{"step_id":"s2","label":"一致","expression":"6 + 6","model_result":12,"unit":"分钟","output_key":"ok"},
            {"step_id":"s2","label":"修正","expression":"10 + 2","model_result":9,"unit":"分钟","output_key":"wrong"},
            {"step_id":"s2","label":"不可解析","expression":"missing(10)","model_result":10,"unit":"分钟","output_key":"unknown"}]
        _,d=self._success(s,executions=[self._execution({"ok":12,"wrong":9,"unknown":10},calculations=calculations)])
        self.assertEqual(d["runs"][0]["output"]["outputs"]["wrong"],12)
        table=json.dumps(d["report"]["recompute"],ensure_ascii=False)
        self.assertIn("一致",table); self.assertIn("已按程序复算更正",table); self.assertIn("未经复算",table)
        self.assertIn("未经复算",json.dumps(d["report"]["manual_items"],ensure_ascii=False))

    def test_ac09_model_escalation_handoff_idempotent_and_resolve_audit(self):
        """AC09：命中条件自动转人工，重复手动转人工复用未处理记录，处理写审计。"""
        s=self._skill(); _,d=self._success(s,executions=[self._execution(escalation={"triggered":True,"matched_conditions":["涉及现场安全时交由责任人确认"],"reason":"现场安全待确认"})])
        self.assertEqual(len(d["handoffs"]),1); self.assertEqual(d["handoffs"][0]["source"],"system")
        hid=d["handoffs"][0]["id"]
        for _ in range(2): self.assertEqual(self._post(f"sessions/{d['id']}/handoff",{"reason":"管理员再次确认"}).status_code,200)
        self.assertEqual(len(self._detail(d["id"])["handoffs"]),1)
        r=self._post(f"handoffs/{hid}/resolve",{"note":"责任人已现场确认"})
        self.assertEqual(r.status_code,200,r.text)
        updated=self._detail(d["id"])["handoffs"][0]
        self.assertEqual(updated["status"],"resolved")
        self.assertIn("责任人已现场确认",json.dumps(updated,ensure_ascii=False))
        with get_db() as c:
            audit=[dict(r) for r in c.execute("SELECT * FROM audit_logs WHERE target_id=?",(hid,))]
        self.assertTrue(audit)

    def test_ac09_failed_escalation_step_and_manual_only(self):
        """AC09：步骤 on_fail=转人工 且未通过；管理员主动转人工。"""
        s=self._skill()
        _,d=self._success(s,executions=[self._execution(step_results=[{"step_id":"s2","conclusion":"条件未通过","refs":[self.atom["version_id"]],"state":"未通过"}])])
        self.assertEqual(len(d["handoffs"]),1)
        self.assertIn("需人工",json.dumps(d["runs"][0],ensure_ascii=False))
        sid,plain=self._success(s)
        self.assertEqual(plain["handoffs"],[])
        self.assertEqual(self._post(f"sessions/{sid}/handoff",{"reason":"负责人希望人工复核"}).status_code,200)
        self.assertEqual(self._detail(sid)["handoffs"][0]["source"],"user")

    def test_ac10_eight_report_parts_exact_boundary_and_frozen_snapshot(self):
        """AC10：报告八部分、逐字边界、快照及原文摘录追溯。"""
        s=self._skill()
        # 快照应保留引用时原文，不把当前原子内容冒充历史快照。
        with get_db() as c: c.execute("UPDATE knowledge_versions SET title='变化后的标题' WHERE id=?",(self.atom["version_id"],))
        _,d=self._success(s); report=d["report"]
        for key in ("summary","skill_results","recompute","actions","risk_boundaries","manual_items","evidence","disclaimer"):
            self.assertIn(key,report)
        self.assertEqual(report["risk_boundaries"][0]["items"],s["content"]["risk_boundary"])
        self.assertEqual(report["disclaimer"],"本报告由 AI 按已审核 Skill 生成，结论需人工确认。")
        ev=json.dumps(report["evidence"],ensure_ascii=False)
        for expected in ("设备异常处置依据",self.atom["statement"],"m03-fixture.txt","v1",self.atom["version_id"]): self.assertIn(expected,ev)
        self.assertNotIn("变化后的标题",ev)

    def test_ac11_empty_retrieval_zero_calls(self):
        """AC11：没有可用 Skill 且检索为空，总模型调用严格为 0。"""
        with patch.object(consult,"hybrid_search",return_value=[]) as search:
            d=self._finish(self._session())
        self.assertEqual(d["status"],"completed",d); self.assertEqual(len(self.model.messages),0)
        self.assertIn("没有",json.dumps(d["report"],ensure_ascii=False)); self.assertTrue(search.called)
        self.assertEqual(self._post(f"sessions/{d['id']}/handoff",{"reason":"缺少依据"}).status_code,200)

    def test_ac11_fallback_filters_refs_and_uses_current_admin(self):
        """AC11：兜底引用仅来自检索结果，hybrid_search 接收当前管理员。"""
        result={"item_id":self.atom["item_id"],"version_id":self.atom["version_id"],"title":"设备异常处置依据", "statement":self.atom["statement"],"relevance_score":0.8}
        self.model.add({"answer":"按已检索依据登记并由责任人确认[outside-ref]","refs":[self.atom["version_id"],"outside-ref"]})
        with patch.object(consult,"hybrid_search",return_value=[result]) as search:
            d=self._finish(self._session())
        self.assertEqual(d["status"],"completed",d)
        self.assertEqual(len(self.model.messages),1)
        self.assertNotIn("outside-ref",json.dumps(d["report"]["evidence"],ensure_ascii=False))
        self.assertNotIn("outside-ref",d["report"]["summary"])
        self.assertIn(self.atom["version_id"],json.dumps(d["report"]["evidence"]))
        self.assertIn("未匹配到可用 Skill，以下为知识条目整理",json.dumps(d["report"],ensure_ascii=False))
        args=search.call_args.args
        self.assertEqual(args[1]["id"],self.user["id"]); self.assertEqual(args[1]["role"],"admin")

    def test_ac11_router_zero_selection_then_fallback(self):
        """AC11：存在可用 Skill 但选择零个时检索兜底，只调用路由与兜底。"""
        self._skill(); self.model.add({"selected_skills":[]},{"answer":"仅整理知识依据","refs":[self.atom["version_id"]]})
        with patch.object(consult,"hybrid_search",return_value=[{"version_id":self.atom["version_id"],"item_id":self.atom["item_id"],"statement":self.atom["statement"]}]):
            d=self._finish(self._session())
        self.assertEqual(d["status"],"completed",d); self.assertEqual(len(self.model.messages),2)
        self.assertEqual(d["runs"],[])

    def test_ac12_transport_retry_success_and_exhaustion_retry_api(self):
        """AC12：模型失败按 M02 上限重试，失败后无残留运行中并能重试成功。"""
        s=self._skill()
        self.model.add(RuntimeError("模拟连接失败"),self._route(s),self._execution(),{"summary":"重试后完成","actions":[]})
        d=self._finish(self._session()); self.assertEqual(d["status"],"completed",d)
        self.assertEqual(len(self.model.messages),4)
        self.model.messages=[]
        from skill_constants import SKILL_MODEL_CALL_MAX_ATTEMPTS
        self.model.add(*[RuntimeError("模拟连续失败") for _ in range(SKILL_MODEL_CALL_MAX_ATTEMPTS)])
        sid=self._session(); d=self._finish(sid)
        self.assertEqual(d["status"],"failed",d)
        self.assertEqual(len(self.model.messages),SKILL_MODEL_CALL_MAX_ATTEMPTS)
        self.assertTrue(all(t["status"] not in ("queued","running") for t in d["tasks"]))
        self.model.add(self._route(s),self._execution(),{"summary":"重试恢复","actions":[]})
        self.assertEqual(self._post(f"sessions/{sid}/retry",{}).status_code,200)
        self.assertEqual(self._finish(sid)["status"],"completed")

    def test_ac12_restart_recovers_interrupted_tasks(self):
        """AC12：启动恢复中断任务，重新入队后正常结束。"""
        s=self._skill(); sid=self._session()
        with get_db() as c:
            c.execute("UPDATE consult_tasks SET status='running' WHERE session_id=?",(sid,))
        self.model.add(self._route(s),self._execution(),{"summary":"恢复完成","actions":[]})
        with patch.object(tasks,'ensure_missing_stage4b_index_tasks',return_value=[]), \
             patch.object(consult,'recover_consult_tasks',wraps=consult.recover_consult_tasks) as recover:
            tasks.recover_interrupted_tasks()
            recover.assert_called_once()
        self.assertEqual(self._finish(sid)["status"],"completed")

    def test_ac12_permanent_model_error_records_cause_without_retry(self):
        """AC12/AC13：认证、权限、额度、请求错误只调用一次，保留分类，不含密钥。"""
        from model_errors import ModelRequestError
        self._skill()
        for status, code in ((401,'authentication'),(403,'permission'),(402,'quota'),(400,'invalid_request')):
            with self.subTest(status=status):
                self.model.messages=[]
                self.model.add(ModelRequestError(code,f'模型服务未接受请求（HTTP {status}），请检查配置',http_status=status))
                d=self._finish(self._session())
                self.assertEqual(d['status'],'failed'); self.assertEqual(len(self.model.messages),1)
                self.assertEqual(d['failure']['http_status'],status)
                self.assertFalse(d['failure']['retryable'])
                call=d['tasks'][-1]['model_calls'][-1]
                self.assertEqual(call['error_code'],code)
                self.assertIn(str(status),d['error'])
                self.assertNotIn(config.DEEPSEEK_API_KEY,json.dumps(d))

    def test_ac12_transient_model_errors_retry_and_report_failure_stage(self):
        """AC12：临时错误按原上限重试；执行失败与路由失败可区分，恢复不重做路由。"""
        from model_errors import ModelRequestError
        s=self._skill()
        self.model.add(self._route(s),*[ModelRequestError('service_unavailable','模型服务暂时异常（HTTP 503），请稍后重试',http_status=503,retryable=True) for _ in range(2)])
        with patch.object(consult.time,'sleep') as sleep:
            sid=self._session(); d=self._finish(sid)
        self.assertEqual(d['failure']['stage'],'执行咨询内容')
        self.assertEqual(d['failure']['http_status'],503)
        self.assertTrue(d['failure']['retryable'])
        self.assertEqual(len(self.model.messages),3);sleep.assert_called_once_with(1)
        self.model.add(self._execution(),{'summary':'故障恢复后完成','actions':[]})
        self._post(f'sessions/{sid}/retry',{})
        d=self._finish(sid)
        self.assertEqual(d['status'],'completed');self.assertNotIn('failure',d)
        self.assertEqual(len(self.model.messages),5)

    def test_ac12_processing_error_does_not_retry_model_request(self):
        """AC12：本地校验异常不会误判成模型服务故障并重复请求。"""
        s=self._skill();self.model.add(self._route(s),self._execution())
        with patch.object(consult,'execution_structure_errors',side_effect=RuntimeError('内部校验错误')):
            d=self._finish(self._session())
        self.assertEqual(d['status'],'failed');self.assertEqual(len(self.model.messages),2)
        self.assertEqual(d['failure']['error_code'],'processing_error')
        self.assertNotIn('内部校验错误',json.dumps(d,ensure_ascii=False))

    def test_ac13_history_version_inputs_execution_and_no_credentials(self):
        """AC13：历史记录保留固定版本、输入与调用元信息，记录没有密钥或令牌。"""
        s=self._skill(); sid,d=self._success(s)
        history=self.client.get("/api/consult/sessions",headers=self.admin).json()["items"]
        self.assertIn(sid,{x["id"] for x in history})
        self.assertEqual(d["runs"][0]["skill_version_id"],s["version_id"])
        self.assertEqual(d["runs"][0]["inputs"]["situation"],"设备异常")
        self.assertTrue(d["runs"][0]["model_calls"])
        with get_db() as c:
            records={table:[dict(r) for r in c.execute("SELECT * FROM "+table)] for table in ("consult_sessions","consult_runs","consult_tasks","consult_handoffs","audit_logs")}
        serial=json.dumps(records,ensure_ascii=False)
        self.assertNotIn(config.DEEPSEEK_API_KEY,serial)
        self.assertNotIn(self.admin["Authorization"].split(" ")[1],serial)
        self.assertNotIn('"api_key"',serial.lower())

    def test_ac12_database_constraints_and_idempotent_init(self):
        """§7/AC12：五张表状态 CHECK 生效，重复 init 不破坏已有咨询。"""
        s=self._skill(); sid,_=self._success(s)
        init_db(); self.assertEqual(self._detail(sid)["status"],"completed")
        self._post(f'sessions/{sid}/handoff', {'reason':'验证状态约束'})
        for table in ("consult_skill_publications","consult_sessions","consult_runs","consult_tasks","consult_handoffs"):
            with self.subTest(table=table),get_db() as c:
                with self.assertRaises(sqlite3.IntegrityError): c.execute("UPDATE "+table+" SET status='bad-state'")


class TestM03Calculator(unittest.TestCase):
    def test_ac08_arithmetic_and_three_datetime_formats(self):
        """AC08/§5.3：四则运算、分钟/小时/天、三种日期格式。"""
        examples={"(12 + 3) * 2 / 5 - 1":5,
            "minutes_between('2026-10-01 09:00','2026-10-01 09:12')":12,
            "hours_between('2026-10-01 09:00:00','2026-10-01 11:30:00')":2.5,
            "days_between('2026-10-01','2026-10-03')":2}
        for expression,result in examples.items():
            with self.subTest(expression=expression): self.assertAlmostEqual(calculator.evaluate_expression(expression),result)

    def test_ac08_ast_malicious_inputs_rejected(self):
        """AC08：__import__、属性、陌生函数和所有非白名单语法拒绝。"""
        for expression in ("__import__('os').system('echo unsafe')","(1).__class__","abs(-1)","sum([1,2])",
            "2 ** 999999","[x for x in (1,2)]","True+1","lambda:1","open('x')","minutes_between.__call__('x','y')",
            "1/0","1e999","minutes_between('2026-02-31','2026-10-01')"):
            with self.subTest(expression=expression),self.assertRaises((ValueError,TypeError,ZeroDivisionError,OverflowError)):
                calculator.evaluate_expression(expression)

    def test_ac08_absolute_and_relative_tolerance(self):
        """AC08：0.01绝对误差或0.5%相对误差范围视为一致。"""
        calculations=[{"step_id":"s2","label":"绝对误差","expression":"1","model_result":1.009,"output_key":"a"},
            {"step_id":"s2","label":"相对误差","expression":"1000","model_result":1004,"output_key":"b"},
            {"step_id":"s2","label":"超出误差","expression":"1000","model_result":1010,"output_key":"c"}]
        rows,outputs=calculator.recompute_calculations(calculations,{"a":1.009,"b":1004,"c":1010})
        self.assertEqual(outputs["c"],1000)
        self.assertIn("已按程序复算更正",json.dumps(rows[2],ensure_ascii=False))
        self.assertNotIn("已按程序复算更正",json.dumps(rows[:2],ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
