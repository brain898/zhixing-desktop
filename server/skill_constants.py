"""
知行有策 - M02 Skill 工厂取值常量

数据库状态字段沿用 M01 做法：库内存英文代码，界面与 Skill JSON 使用 PRD 中文名称。
本文件是代码与中文名称的唯一对照，database.py 的 CHECK 约束由这里生成。
"""

SKILL_SCHEMA_VERSION = "1.0"
TBD_EXPERT = "TBD_EXPERT"

# PRD 3.8 / 7.2 候选阶段状态
SKILL_STATUS_LABELS = {
    "generating": "生成中",
    "validation_failed": "校验未通过",
    "pending_review": "待审核",
    "approved": "已通过（待测试）",
    "rejected": "已驳回",
    "needs_recheck": "待复核",
}

# PRD 7.2 / FR08 / FR14 产生版本的来源
SKILL_VERSION_KIND_LABELS = {
    "ai_original": "AI 原稿",
    "ai_regenerated": "AI 重生成版",
    "expert_revision": "专家修订版",
    "recheck_revision": "复核处理版",
}

# PRD FR13 审核动作
REVIEW_ACTION_LABELS = {
    "approve": "通过",
    "approve_with_changes": "修改后通过",
    "regenerate": "退回重生成",
    "reject": "驳回",
    "restore": "恢复",
    "recheck": "复核处理",
}

# PRD FR12 驳回原因
REJECT_REASONS = ("任务不成立", "与已有 Skill 重复", "原子依据不足", "超出本场景", "其他")

# PRD 6.2 批次状态
BATCH_STATUS_LABELS = {
    "running": "进行中",
    "completed": "完成",
    "partial": "部分完成",
    "failed": "失败",
    "no_tasks": "无可生成任务",
}

# PRD 3.4 引用角色
REF_ROLES = ("前置条件", "判断规则", "执行动作", "例外处理", "指标口径", "案例参考")

# PRD R4：可见范围沿用 M01 access_scope 取值
VISIBILITY_VALUES = ("admin_only", "org_internal")

# PRD 5.2 场景状态：启用 / 停用
SCENE_STATUS_LABELS = {
    "active": "启用",
    "disabled": "停用",
}

# PRD FR02 M01-C2：待归并标签的处理状态与来源
PENDING_TAG_STATUS_LABELS = {
    "pending": "待归并",
    "mapped": "已映射到已有场景",
    "created": "已新建场景",
    "ignored": "已忽略",
}
PENDING_TAG_SOURCE_LABELS = {
    "extraction": "M01 抽取",
    "manual": "核对手动输入",
    "catalog_init": "初始化未归入目录",
    "catalog_organize": "整理新标签时未归入",
}

# PRD FR01 归并建议的执行状态（独立任务表，不占用 processing_tasks.task_type）
MERGE_SUGGESTION_STATUS_LABELS = {
    "queued": "排队中",
    "running": "生成中",
    "completed": "待确认",
    "failed": "失败",
    "confirmed": "已确认",
    "discarded": "已放弃",
}

# PRD FR04 / D07：召回参数为初始建议值，开发测试中调整
SCENE_RECALL_POOL_LIMIT = 60        # 原子池上限（2026-09-27 由 40 调为 60，用户确认）
SCENE_SEMANTIC_RESERVED = 10        # 为「内容相关」原子保留的名额；不足时剩余名额还给标签命中（用户确认，调整 FR04「标签优先」）
SCENE_SEMANTIC_TOP_N = 30           # 语义召回取前 N 条
SCENE_MIN_ATOMS_FOR_GENERATION = 3  # FR03：可用原子少于该数时生成按钮不可用

# PRD 6.1 / M02-C：批次后台任务类型（独立任务表 skill_generation_tasks，不占用 processing_tasks）
SKILL_TASK_TYPE_LABELS = {
    "recall_atoms": "召回",
    "split_tasks": "拆分",
    "generate_skill": "生成",
    "validate_store": "校验入库",
}
SKILL_TASK_STATUS_LABELS = {
    "queued": "排队中",
    "running": "进行中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}

# PRD 6.2「各任务生成结果」
TASK_RESULT_LABELS = {
    "pending": "等待生成",
    "generating": "生成中",
    "validating": "校验中",
    "repairing": "自动修复中",
    "stored": "成功",
    "validation_failed": "校验未通过",
    "call_failed": "调用失败",
    "skipped": "未进入生成",
}

# PRD FR05 / FR08 / D07：拆分与入库参数（初始建议值，开发测试中调整）
SPLIT_MAX_TASKS = 5                  # 单批次任务数上限
SPLIT_MIN_ATOMS_FOR_GENERATION = 2   # 所用原子少于该数的任务不进入生成
TASK_OVERLAP_THRESHOLD = 0.8         # 两个任务所用原子重合度超过该值提示可能重复
SKILL_SIMILAR_THRESHOLD = 0.8        # 新候选与已有 Skill 所用原子重合度超过该值标记相似
SKILL_MODEL_CALL_MAX_ATTEMPTS = 2    # 单次模型调用失败或超时的尝试次数，与 M01 抽取一致
SKILL_AUTO_REPAIR_ROUNDS = 1         # FR07：硬性错误自动修复一次
SPLIT_FORMAT_RETRY_ROUNDS = 1        # 拆分返回格式不合格时带错误清单重试一次
FOCUS_NOTE_MAX = 100                 # G1 生成侧重说明长度上限


# PRD FR12 审核清单（依据计划书 4.6.4），通过前须全部勾选
REVIEW_CHECKLIST = (
    ("goal", "业务目标合理，一个 Skill 只做一个任务"),
    ("scope", "适用与不适用范围准确"),
    ("refs", "引用的知识有效，角色正确"),
    ("steps", "执行步骤和分支符合实际业务"),
    ("outputs", "输出结果有使用价值"),
    ("escalation", "需要人工判断的情形已列入升级条件"),
)

# PRD 3.2 字段分组（退回重生成可指定需要重写的字段组，FR12）
FIELD_GROUP_LABELS = {
    "A": "身份与边界",
    "B": "知识依赖",
    "C": "输入输出契约",
    "D": "执行逻辑",
    "E": "风险与人工升级",
    "F": "治理",
    "G": "后续模块预留",
}
REGENERATE_FIELD_GROUPS = ("A", "B", "C", "D", "E")

# PRD FR11 / FR13 无依据项处理结果：删除该内容 / 补充依据 / 专家补充；
# 「已补落点」「修改后消除」是系统根据编辑结果推断的处理方式（例外补了落点、内容改写后不再触发）
UNSUPPORTED_RESOLUTION_LABELS = {
    "delete": "删除",
    "add_ref": "补依据",
    "expert": "专家补充",
    "landed": "已补落点",
    "edited": "修改后消除",
}

# PRD 7.2 / FR12：审核阶段的后台任务（独立任务表 skill_review_tasks，不占用 processing_tasks）
REVIEW_TASK_TYPE_LABELS = {
    "regenerate": "退回重生成",
}
REVIEW_TASK_STATUS_LABELS = SKILL_TASK_STATUS_LABELS

# PRD S13：待沉淀经验清单状态（一键转为 M01 待确认原子列为后续增强，本阶段只保留清单）
EXPERIENCE_STATUS_LABELS = {
    "pending": "待沉淀",
}

# PRD R5 / FR14：原子变更触发复核的事件类型（M02-E）
STALE_TRIGGER_LABELS = {
    "new_version": "已产生新版本并生效",
    "disabled": "已停用",
    "deleted": "已删除",
    "excluded": "已排除（不收录）",
    "scope_tightened": "权限已收紧为管理员专享",
    "expired": "已超过有效期",
    "source_replaced": "来源文件已启用新版本，这条知识不再生效",
}
# 变更对 Skill 的影响：已通过 -> 待复核；待审核 -> 状态不变、显示提示；仅重新推导可见范围
STALE_EFFECT_LABELS = {
    "needs_recheck": "转为待复核",
    "pending_notice": "待审核候选显示变更提示",
}
# PRD FR14 复核动作（逐条受影响知识选择）
STALE_RESOLUTION_LABELS = {
    "update": "更新引用",
    "no_impact": "确认无影响",
    "remove": "移除引用",
}
# 原子被停用、删除、排除（及来源失效）时不能保持旧引用，只能移除（或退回重生成 / 驳回）
STALE_REMOVE_ONLY_TRIGGERS = ("disabled", "deleted", "excluded", "source_replaced")
# 有效期到期没有显式事件：服务启动时与之后每隔该秒数扫描一次（M02-E 选择「启动与定时扫描」）
STALE_SCAN_INTERVAL_SECONDS = 600
STALE_NOTE_MAX = 500

REVIEW_COMMENT_MAX = 1000
REJECT_NOTE_MAX = 500


def sql_in_list(values) -> str:
    """生成 CHECK(... IN (...)) 使用的字面量列表；取值均为本文件内常量。"""
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)
