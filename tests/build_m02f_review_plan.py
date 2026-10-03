"""保存已审阅的真实候选修改方案；只产出计划，提交由 review_m02f_demo.py 执行。"""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts/m02f"
snapshot = json.loads((OUT / "review_snapshot.json").read_text(encoding="utf-8"))
by_name = {c["name"]: c for c in snapshot["candidates"] if c["scene_name"] == "物业费收缴与欠费管理"}
by_title = {a["title"]: a for a in snapshot["atoms"]}
plan = []

# T1/T2/T8：保留归属核对与不可计算分支。删除跨任务的清欠率输出；
# 原稿把输入校验列无依据，为它补入已经引用、实际支撑金额与范围校验的原子。
original = by_name["核对指定期间收缴率"]
content = copy.deepcopy(original["content"])
content["not_applies_to"] = ["收缴率下降的原因分析与诊断", "非物业服务费的收费项目核对", "清欠率核算"]
content["outputs"] = [o for o in content["outputs"] if o["key"] != "clear_debt_rate"]
for o in content["outputs"]:
    if o["key"] in {"collection_rate", "diff_from_reported"}:
        o["required"] = False  # 不可计算时不能强制输出数值。
for i in content["inputs"]:
    if i["key"] == "unmatched_received_amount":
        i["required"] = True  # 否则无法判断是否超过已有来源阈值。
for step in content["steps"]:
    if step["step_id"] == "s2":
        step["refs"] = [by_title[t]["version_id"] for t in ("当期收缴率计算口径", "收缴率不可计算情形")]
        step["basis"] = "有原子依据"
        for ref in content["knowledge_refs"]:
            if ref["atom_version_id"] in step["refs"]:
                ref["used_in_steps"] = list(dict.fromkeys(ref["used_in_steps"] + ["s2"]))
    if step["step_id"] == "s5":
        step["action"] = step["action"].replace("，并计算清欠率", "")
content["risk_boundary"][0] = "核对结论只说明口径是否正确，不对收缴率下降的原因下结论。"
content["escalation_conditions"].append("实收归属未核对清楚时转财务确认；不按到账时间归属；统计期内收回的往期欠费和预收未来期间费用不计入当期实收。")
plan.append({"skill_id": original["skill_id"], "action": "approve", "body": {
    "skill_json": content, "resolutions": {},
    "comment": "演示审核：补充输入校验依据并明确跨期归属的升级条件；删除清欠率核算，保持单一核对任务；不可计算时不强制数值输出。",
    "change_reasons": {"steps[s2].refs": "金额与口径校验使用现有正式知识依据。", "outputs[clear_debt_rate]": "清欠率核算属于另一任务。",
                       "inputs[unmatched_received_amount].required": "须取得未核对金额才能判断已有不可计算阈值。"}}})

# T9：原稿给催缴措施但遗漏禁止行为；从真实已确认原子补引用和阻断分支。
original = by_name["判定欠费等级与催缴责任人"]
content = copy.deepcopy(original["content"])
atom = by_title["禁止以停供方式催交物业费"]
assert atom["review_status"] == "confirmed" and atom["index_status"] == "ready"
sid = "s6"
content["knowledge_refs"].append({"atom_item_id": atom["item_id"], "atom_version_id": atom["version_id"],
                                  "role": "例外处理", "used_in_steps": [sid]})
content["steps"].insert(2, {"step_id": sid, "kind": "规则判断", "condition": "拟采取催缴措施时",
                          "action": "不得采取停止供电、供水、供热、供燃气等方式催交物业费；拟议措施触及该禁令时停止该措施并转人工。",
                          "refs": [atom["version_id"]], "basis": "有原子依据", "on_fail": "转人工"})
content["risk_boundary"].append("不得采取停止供电、供水、供热、供燃气等方式催交物业费。")
content["escalation_conditions"].append("拟议催缴措施涉及停止供电、供水、供热、供燃气时，停止该措施并转人工。")
plan.append({"skill_id": original["skill_id"], "action": "approve", "body": {
    "skill_json": content, "resolutions": {}, "comment": "演示审核发现 T9：候选已建议催缴措施，但未引用停供禁令，风险边界也没有该禁令；补正式原子引用、阻断步骤与人工升级条件。",
    "change_reasons": {"steps[s6]": "补入被原稿遗漏的已确认禁令。"}}})

# T3：拆分后的诊断原子集合遗漏新交付例外。退回意见提供系统真实原子，
# 不能把资料缺口猜成制度；模型重写结果仍回到待审核。
original = by_name["诊断收缴率下降原因"]
atom = by_title["新交付项目收缴率评价应结合交付阶段与遗留问题处理情况"]
comment = ("演示审核发现 T3：原稿没有新交付项目的特殊分支，且不适用范围、风险与升级条件有待补充。"
           "同企业已确认知识原子条目 " + atom["item_id"] + "，版本 " + atom["version_id"] + "，内容：" + atom["statement"] +
           " 请补入此引用与特殊分支，提供交付时间、交付率、入住率和遗留问题处理进度输入；资料不全时补问。"
           "不要把新交付项目直接套成熟项目阈值，不编造新的阈值。所有待专家补充必须以现有依据替换；不能替换的明确待补充输入。"
           "保留核对与诊断任务的边界，只给诊断及措施方向，不执行整改。")
plan.append({"skill_id": original["skill_id"], "action": "regenerate", "body": {"comment": comment, "field_groups": ["A", "B", "C", "D", "E"]}})
(OUT / "review_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"saved {len(plan)} explicitly reviewed actions")
