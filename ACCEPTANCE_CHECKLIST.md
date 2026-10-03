# 知行有策 M01 知识资产整理验收清单（按 PRD v0.7 校准）

## M03 Agent 咨询（方案 v0.3，2026-10-02）

本轮已完成以下验收。模拟模型测试与真实调用分开留证，完整报告见 `../../02-方案/M03阶段报告/M03开发验收报告.md`。正式业务库没有在开发中上架或写入。

| 编号 | 验收内容 | 状态 | 证据 |
|---|---|---|---|
| AC01 | 成员11个接口403、成员准备页保留、跨企业隔离 | 通过 | `test_m03_consult.py::test_ac01_*`；UI成员检查 |
| AC02 | 已通过上架门槛、同名去重、上下架审计 | 通过 | `test_ac02_*`；UI上下架 |
| AC03 | 待复核、版本变化、原子失去资格暂停；每个执行前复查 | 通过 | `test_ac03_*` |
| AC04 | 范围外和下架ID剔除、最多4个、超过12个粗排 | 通过 | `test_ac04_*` |
| AC05 | 补问按类型带单位/选项、非法值拒绝、提交继续 | 通过 | `test_ac05_*`；UI七字段与非法日期 |
| AC06 | 前序输出自动传值不补问；缺值后序不可计算 | 通过 | `test_ac06_*` |
| AC07 | 越界引用、无效步骤剔除；非法enum/number不进合成 | 通过 | `test_ac07_*` |
| AC08 | 复算一致、更正、未经复算，恶意AST表达式拒绝 | 通过 | `test_ac08_*`；`test_m03_calculator.py`；真实补问12/14分钟 |
| AC09 | 自动与手动转人工、未处理记录幂等、处理审计 | 通过 | `test_ac09_*`；UI处理；真实转人工案例 |
| AC10 | 八部分报告、风险逐字拼装、快照与原文追溯 | 通过 | `test_ac10_*`；UI报告与依据 |
| AC11 | 当前管理员知识兜底、受控引用、检索空0次模型调用 | 通过 | `test_ac11_*`；真实客服主管职责兜底 |
| AC12 | 重试上限、失败后重试、无进行中残留、启动恢复 | 通过 | `test_ac12_*`，调用完整恢复入口验证M03钩子；补充认证/额度直接停止、临时错误重试、本地校验错误不重复请求与失败步骤恢复 |
| AC13 | 历史版本、输入、执行、调用记录；无密钥或令牌泄露 | 通过 | `test_ac13_*`；`test_model_errors.py`验证错误分类与HTTP状态保留、日志不含凭据或服务商原始正文 |
| AC14 | 业务库副本4例真实调用：3 Skill协作、补问、转人工、兜底 | 通过 | `artifacts/m03/live/`；完整案例报告 |
| AC15 | M01/M02不新增失败、类型与构建通过、三个标签UI覆盖 | 通过，既有失败保留 | 回归225通过/3既有失败/1跳过；quick PASS；UI24通过 |

M03模拟后端26项通过，复算器独立5项通过，生产前端隔离UI24项通过。既有鉴权失败的具体用例、原因及基线对比在完整报告与 `artifacts/m03/regression_baseline/`、`regression_final/`，未修改无关鉴权代码。截图在 `artifacts/m03/ui/`。三Skill真实协作采用各自完整输入顺序执行并合成报告，未强行连接不同含义的字段；初次补问表达式未带日期引号的失败复算证据和修正后的真实重测一并保留。

2026-10-02错误处理返工：M03后端29项、错误处理7项、UI38项通过，quick、类型检查与构建通过；M01/M02回归仍为225通过、3既有失败、1跳过，无新增失败。同一「发现异常」在业务库副本中真实重试完成，2次模型调用、6.455秒；旧失败根因不能追溯，未把重试成功作为原故障原因的证明。证据见 `artifacts/m03/error_fix/` 与 `regression_error_fix/`，原报告已追加返工记录。

## M01 原有验收项

| 用例编号 | 验收项 | 验收状态 | 验证方式与证据 |
|---|---|---|---|
| AC01 | 空白页：全新无资料时主工作区只显示引导语与导入按钮，无假数据 | ✅ 通过 | 自动化端到端测试覆盖，截图见 `screenshots/01_stage2_empty_state_1440x900.png` |
| AC02 | 角色限制：普通成员直接访问管理页面或接口被拒绝 | ✅ 通过 | 单元测试 `test_04` 验证服务端返回 403 Forbidden，客户端导航严格隐藏入口，前端路由守卫拦截 |
| AC03 | 企业隔离：企业 A 无法读取、搜索、编辑企业 B 的数据 | ✅ 通过 | 自动化测试 `test_documents.py::test_07` 与 `test_07_cross_tenant_forbidden` 覆盖，跨租户接口严格返回 403/404 |
| AC04 | 正常导入：上传受支持且可读取测试文件，转为两栏并展示状态与结构块 | ✅ 通过 | 支持 Markdown、DOCX、PDF、TXT，提取标题、段落、表格与页码/行号锚点，截图见 `03`、`04` |
| AC05 | 批量部分失败：单批最多10个，非法格式或超限拦截，有效文件继续处理 | ✅ 通过 | 单元测试 `test_04_validation_rejections` 覆盖，前端批量上传面板逐项校验并独立报告 |
| AC06 | 重复导入：相同内容指纹文件重复上传时定位已有记录，不重复新建资产 | ✅ 通过 | 单元测试 `test_05_duplicate_content_detection` 覆盖，基于 SHA-256 指纹检测重复并提示已有资料 |
| AC07 | 同名变更：同名但内容不同文件上传，提供创建新版本或另存独立文件选择 | ✅ 通过 | 单元测试 `test_06_same_name_different_content_ask_and_new_version` 覆盖，前端支持弹窗决策 |
| AC08 | 五类分类与待分类：五类固定主分类，待分类为独立待办入口，数量来自真实数据 | ✅ 通过 | 5类分类（制度与标准、方法与工具、项目案例、指标数据、专家经验）加待分类与待校对 Tab，无假数据，实测见截图 `screenshots/01_stage3_knowledge_cards_1440x900.png` |
| AC09 | 人工校对：支持对照原文修改分类、场景、原子结构要素并保存草稿 | ✅ 通过 | 原文与表单并排双栏校对抽屉，原文摘录绿色高亮，修改后实时保存草稿版本，实测见截图 `screenshots/03_stage3_proofreading_modal_1440x900.png` |
| AC10 | 来源定位：结构块准确定位文件版本及段落/页码/行号锚点，Word 不虚构页码 | ✅ 通过 | 单元测试 `test_01-03` 及端到端实测验证，DOCX 采用 `[p_x]`/`[tbl_x]`，PDF 采用 `[p.x]`，MD/TXT 采用 `[line_x]` |
| AC11 | 确认门槛：主分类不能为待分类、核心陈述不为空、来源与摘录必须真实存在，否则拦截 | ✅ 通过 | 单元测试 `test_04_api_knowledge_lifecycle` 与端到端自动化验证拦截逻辑；违规提交返回 400 Bad Request |
| AC12 | 待确认隔离：未确认或未分类候选知识不得正式返回 | ✅ 通过 | 统一 eligibility 规则与 SQL 刚性过滤，未确认候选绝不进入检索，`tests/test_stage4a_eligibility.py::test_01` 覆盖 |
| AC13 | 可用混合检索：关键词与中文 Dense 语义均可召回当前用户有资格使用的正式知识 | ✅ 通过 (Stage 4B) | `BAAI/bge-small-zh-v1.5` 真实 512 维向量 + cosine；先复用 Stage 4A eligibility 预过滤，再执行 keyword / Dense / RRF，`tests/test_stage4b_hybrid_retrieval.py::test_01`（关键词/同义 Dense/无结果）与 `test_02`（长问题/条件/例外）覆盖 |
| AC14 | 片段去重：同一 knowledge_version 多个 fragment 命中后只返回一个完整知识结果 | ✅ 通过 (Stage 4B) | 检索片段按 `knowledge_version_id` 分通道取最佳排名后 RRF 融合；固定测试验证多 fragment 命中后按 `knowledge_version_id` 只返回 1 个知识版本，`tests/test_stage4b_hybrid_retrieval.py::test_02` 覆盖 |
| AC15 | 正式检索范围与筛选：支持全部资料/当前文件，主分类、客户类型、业务场景、问题均可多选；同组 OR、跨组 AND | ✅ 通过 (Stage 4C) | 后端使用 FastAPI 多值参数并在 eligible 范围内执行同组 OR、跨组 AND；`tests/test_stage4c_admin_search.py::test_01` 验证多选与当前文件/全部资料范围，UI E2E 验证前端重复 GET 参数编码 |
| AC16 | 正式检索状态：未提交输入不触发搜索，零结果与服务失败明确区分，失败可重试，连续查询只保留最新结果 | ✅ 通过 (Stage 4C) | `tests/verify_stage4c_ui.py` 覆盖空查询、未提交输入、loading/empty/error/retry、清除返回维护浏览及 A/B 请求竞争；错误态不再显示“找到 0 条”误导文案 |
| AC17 | 索引失败可控重试：confirmed + failed 不可检索，重试成功后才恢复 ready | ✅ 通过 (Stage 4B) | `POST /api/knowledge/versions/{version_id}/index/retry` 复用同一幂等任务；注入失败后 attempt_count=1、重试真实 BGE 成功后=2，记录无重复，`test_04` 覆盖 |
| AC18 | 可用检索：确认且当前配置索引 ready 后具备正式检索资格 | ✅ 通过 (Stage 4B) | confirm 仅排队持久化 `build_index`，后台真实向量构建成功才置 `ready`；Stage 4A 回归 13/13 与 Stage 4B 固定验收 6/6 均通过 |
| AC19 | 状态与时效拦截：disabled / deleted / expired 知识退出检索 | ✅ 通过 (Stage 4A) | 生命周期停用、逻辑删除、过期由统一资格规则实时拦截，无延迟泄露，`test_02` 覆盖 |
| AC20 | 知识条目删除：删除后不得检索，清理任务失败也不得复活 | ✅ 通过 | 逻辑删除先撤销资格，再异步清理检索记录；`tests/test_version_governance_lifecycle.py::test_04` 覆盖清理失败边界 |
| AC21 | 文件删除：删除资料时显示影响提示，撤销日常访问并取消任务 | ✅ 通过 | 抽屉与原件详情弹窗 (`DocumentDetailModal`) 均已接通删除按钮；`DeleteConfirmModal` 接入真实影响评估接口（版本数、派生知识、检索记录、运行中任务等统计）；单元测试 `test_documents.py::test_11` 覆盖；端到端 UI 验证通过，截图见 `screenshots/verify_doc_delete_01_detail_modal.png`、`02_impact_modal.png`、`03_deleted_result.png` |
| AC22 | 删除竞争：处理期间删除资料，迟到任务产物拒绝写回 | ✅ 通过 | 任务执行器感知文档删除与任务撤回状态，自动中止解析写入，`test_08` 覆盖；Stage 4B `test_05` 验证 build_index 排队后目标停用时任务取消、0 条索引写回且不会重新置 ready |
| AC23 | 新知识版本：支持条目版本递增，保存草稿生成独立 revision_token，支持历史版本追溯 | ✅ 通过 | `knowledge_versions` 表实现版本递增存储与 revision_token 追溯，单元测试与 E2E 均通过 |
| AC24 | 新文件版本生效语义：替换版本上传期间旧版继续服务，全部确认/排除后统一事务切换 | ✅ 通过 (Stage 4A) | 替换版本单条确认不提前切换 active_version_id；启用接口严格门槛校验；启用后旧版本立即退出检索，`test_04` 覆盖 |
| AC25 | 权限收紧与放宽：文件权限为知识权限上限，文件收紧立即生效，放宽不自动放宽知识 | ✅ 通过 (Stage 4A) | 知识权限不得比文件更开放；文件收紧为 admin_only 时派生条目同步级联收紧；核对工作台对专享文件自动锁定知识权限并禁用放宽；草稿保存自愈收敛不卡死；截图见 `screenshots/verify_proofreading_admin_only_scope_fixed.png`；测试 `test_03` 覆盖 |
| AC26 | 并发编辑冲突检测：基于 revision_token 乐观锁，并发修改返回 409 Conflict | ✅ 通过 | 单元测试 `test_04_api_knowledge_lifecycle` 验证并发修改 token 不一致时服务端严格拦截 409 |
| AC27 | 案例关联：关联结果必须受企业与权限约束，不得借关联泄露内容 | ⚠️ 部分通过 | 当前只保存 `related_cases_json`，尚无权威案例实体及受控解析链；不会扩展为未设计的案例模块，正式案例关联验收保留缺口 |
| AC28 | 无 Skill 数据：未建设 Skill 模块时不显示虚构引用或假操作 | ✅ 通过 | 删除影响接口真实返回 `skill_reference_count=0`，未实现模块仅显示准备状态 |
| AC29 | 未保存离开拦截：表单脏数据检测，弹窗提示保存草稿、放弃修改或继续编辑 | ✅ 通过 | 客户端 `UnsavedChangesModal` 完整拦截并保护未保存修改 |
| AC30 | 删除最后资料：全部有效文件删除后，界面安全恢复空白引导页 | ✅ 通过 | 端到端流程实测，文件全部删除后即时恢复空白库占位符 |
| AC31 | 资料中含命令：资料包含 Prompt 注入文本按纯文本正文解析，不触发越权 | ✅ 通过 | 单元测试 `test_07_prompt_injection_safety` 验证包含注入指令的资料仅作为正文解析，不执行任何指令 |
| AC32 | 审计记录 | ✅ 通过 (Stage 4A) | 权限修改、版本启用与条目排除操作均脱敏记录至 `audit_logs` 表，不记全文与敏感密钥，`test_07` 覆盖 |
| AC33 | 登录分流：管理员进入知识工作区，普通成员进入无知识管理入口的准备页 | ✅ 通过 | `tests/test_auth_and_permissions.py` 覆盖两类账号；成员页明确标示知识问答属于 M03，本阶段无真实问答入口 |
| AC34 | 身份伪造：普通成员修改客户端参数不能获得管理员权限 | ✅ 通过 | 服务端基于 Token 实时联查 DB 角色，客户端不能传参提权，`test_04` 验证通过 |
| AC35 | 登录失效：登出或过期后拒绝访问，页面提示重新登录 | ✅ 通过 | 单元测试 `test_05` 验证通过，客户端回调清理 Session |
| AC36 | 账号权限变化：禁用账号或撤销管理员后，下一个请求立即拒绝 | ✅ 通过 | 单元测试 `test_06` 验证通过（实时查库，不依赖静态 Token 缓存） |
| AC37 | 账号切换：管理员退出登录成员账号，不残留上一账号数据 | ✅ 通过 | 端到端流程验证通过，登出时清除 localStorage 与内存状态 |
| AC38 | 演示身份隔离：真实环境无法凭演示开关读取操作真实资产 | ⏳ 需真实身份对接 (已加演示隔离标记) | 登录页测试预填仅在开发环境（Vite DEV）生效且默认密码已清空；测试状态变更接口已受环境变量限制并严格执行企业租户隔离（禁止跨企业改写账号）；正式生产环境需对接企业统一身份鉴权（SSO/OAuth），严禁直接连接真实商业资产 |
| AC39 | 云端持续处理：上传任务被接受后关闭 Electron，任务继续或显示真实失败 | ⚠️ 部分通过 | 本地服务持久任务与重启恢复已通过；真实云端对象存储、独立云端 worker 及关闭桌面后的持续处理未部署，不能用本地恢复替代云端结论 |
| AC40 | 成员 AI 使用 | 不适用（移出 M01） | 按 PRD v0.7 归 M03，本模块不提供成员问答入口、回答生成或联调 |
| AC41 | 模型上下文权限 | 不适用（移出 M01） | 按 PRD v0.7 归 M03；M01 仅保留知识权限与版本数据契约 |
| AC42 | 分类返回校验：DeepSeek 返回空内容、截断 JSON 或错误类别时不得生成已启用知识 | ✅ 通过 | `tests/test_deepseek_batching.py` 覆盖异常返回、失败状态与重试；真实在线联调仅验证正常返回，未强制制造供应商异常 |
| AC43 | 回答期间失效 | 不适用（移出 M01） | 按 PRD v0.7 归 M03，本模块没有回答生成链 |
| AC44 | 版本来源一致：版本严格归属于指定文档与租户，杜绝跨文档/企业串线 | ✅ 通过 | 数据库约束与企业校验共同拦截错配；`tests/test_version_governance_lifecycle.py::test_06` 验证跨来源绑定被拒绝 |
| AC45 | 历史内容保留：新版本确认后旧内容仍可按旧版本追溯 | ✅ 通过 | 历史版本只读，新修改与历史恢复均创建新版本；`tests/test_version_governance_lifecycle.py::test_03` 覆盖历史读取与恢复为新草稿 |
| AT01 | 跨段落条件与例外抽取：能跨段落关联前提条件与停止例外并绑定锚点 | ✅ 通过 | 单元测试 `test_05_cross_paragraph_condition_and_exception` 覆盖 |
| AT02 | 字段状态精准表达：严格区分 supported, not_stated, not_applicable, failed | ✅ 通过 | 单元测试 `test_06_field_states_distinction` 覆盖，不混淆未说明与不适用 |
| AT03 | 伪造 source_block_id 严格拦截：抽取结果引用非正规块 ID 时拒绝写入证据 | ✅ 通过 | 单元测试 `test_02_fake_source_block_rejection` 覆盖，确保来源 100% 真实 |
| AT04 | 表格数据结构化与口径保留：表格提取表头、合格标准与量化数值 | ✅ 通过 | DOCX 表格解析与指标数据原子化抽取验证通过 |
| AT05 | 对立规则冲突检测：识别矛盾对立规则并标记 quality_flags | ✅ 通过 | 单元测试 `test_03_symmetric_conflict_detection` 覆盖对称冲突拦截 |
| AT06 | 完整上下文：任一检索片段命中后，系统可完整回源知识原子及明确绑定 evidence | ✅ 通过 (Stage 4B/4C) | 混合检索与管理员检索界面完整回源 statement/content/subject/conditions/actions/exceptions/metric/case 等完整字段与来源证据 |
| AT07 | 上下文权限：未授权全文/父 source_block 不得先进入语义召回 | ✅ 通过 (Stage 4A/4B) | eligibility 在 retrieval_records 前执行；模型上下文仅含完整 eligible 知识原子和明确绑定的 `knowledge_evidence.excerpt`，不补回父 `source_blocks.text_content` |
| AT08 | 状态边界：confirmed 后经历 not_indexed → indexing → ready/failed，正式检索只接受 ready | ✅ 通过 (Stage 4B) | confirm 不执行 embedding；持久化 build_index 执行时递增 attempt_count，成功置 ready、失败置 failed；`test_04` 验证失败重试，`test_06` 验证服务重启恢复 |

## Stage 4E 综合验证

- [x] 固定人工标注查询集已建立，覆盖专有词、同义表达、长问题、条件、例外、停止条件与无结果；见 `tests/test_stage4e_benchmark_suite.py`。
- [x] A/B/C 对照已可复现：普通切片 / 结构块 / 知识原子+必要 evidence 使用同一 BGE 模型、Dense 阈值、RRF 与近似上下文预算；当前正样本命中率分别为 5/6、5/6、6/6，不预设原子方案必然更优。
- [x] 最终生命周期演示已通过：待确认隔离 → 确认 → 索引 ready → 正式检索 → 来源定位 → 文件/知识权限收紧 → 停用/恢复 → 删除；见 `tests/test_stage4e_full_lifecycle.py`。
- [x] Stage 4A～4C + 4E 综合回归全通过，Stage 4C UI 专项通过，前端 TypeScript/Vite/Electron production build 通过；后续可用 `python tests/run_stage4_validation.py` 一键复现 Stage 4 验证。
- [x] Stage 4E 报告已保存至 `../../02-方案/0918-Stage4E综合验证与对照实验报告.md`；普通成员知识问答归 M03，不是 M01 待办或验收条件。

## 视觉与交互专项检查 (依据 0917-总体软件视觉设计方案 v0.1)

- [x] 三个模块共用品牌、导航、账号区、字体、颜色和组件规范（已落地 CSS 变量系统）。
- [x] 整体采用白灰墨绿克制风格（主背景 #FFFFFF，侧栏 #F7F8F7，品牌强调 #285C49，边框 #E6E8E7）。
- [x] 侧栏底部固定圆形头像、昵称（文哲/李景研）、角色文字与展开菜单。
- [x] 知识管理入口仅管理员可见，普通成员完全隐藏。
- [x] 知识资产整理主工作区支持「全部资料」与单文件范围切换，支持「知识资产与校对」与「原件与结构块」双向透视。
- [x] 校对抽屉提供原文证据对照（左 42%）与结构化表单（右 58%）左右并排，原文摘录提供浅绿底色高亮。
- [x] 真实支持 1440×900 基准尺寸与 1280×800 紧凑尺寸适配（均已实测截屏留证）。
- [x] 无大面积渐变、无装饰机器人、无巨型欢迎标语、无假统计卡片。
