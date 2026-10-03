# 知行有策 桌面客户端与后台服务 (Zhixing Desktop)

面向物业咨询业务的知识资产与 AI Skill 生产平台桌面端软件。

## M03 Agent 咨询

管理员进入「Agent 咨询」，在「可用 Skill」中上架已通过的当前版本，然后在「咨询」输入问题。系统最多选择 4 个 Skill 顺序执行，缺少必需信息时显示补问表单。文件类输入填写文字说明，不上传文件。报告包含结论、各 Skill 结果、复算、行动建议、原文风险边界、人工确认项、依据和固定提示；展开依据可查看引用快照与原文摘录。历史保留版本、输入和执行记录。

同名 Skill 只能在架一个。待复核、版本更新或引用知识不可用时会暂停；版本更新后需重新上架。可主动转人工，系统也会按升级条件自动记录，管理员在「转人工记录」留下说明并标记已处理。普通成员仍显示原有准备页，全部咨询管理接口拒绝成员访问。计算复算只核对算术，结论需人工确认。

咨询页第 2 栏与第 3 栏之间可拖动调整宽度，两侧保留最小宽度；双击分隔条恢复默认比例，聚焦分隔条后也可用左右方向键调整。

咨询失败会显示所处步骤和具体原因。网络、超时、限流或服务临时异常按原上限尝试 2 次，两次之间短暂等待；认证、权限、额度及请求配置问题直接停止，请按提示处理后重试。重试会继续失败的步骤。旧失败记录不会补造原因，重新执行后会保留新的错误分类和 HTTP 状态，不保存服务商原始错误正文或凭据。

隔离测试与真实演示分别运行，以下命令在代码根目录执行：

```powershell
python tests/test_m03_consult.py
python tests/test_m03_calculator.py
python tests/test_model_errors.py
python tests/verify_m03_consult_ui.py  # 先在 client 执行 npm run build
python tests/run_m03_regression.py --label final
python tests/run_m03_live_demo.py     # 调用真实模型，需已有服务端配置
```

真实演示脚本用 SQLite backup 复制业务库，连同 storage 放入代码目录外的临时目录，三项路径环境变量一起指向副本，上架仅发生在副本。四个真实案例与调用次数、时延、报告、复算和转人工记录保存在 `artifacts/m03/live/`。`--data-dir <副本目录>` 可续用副本，已完成案例不会重复调用；`--case supplement --rerun` 可保留上一轮证据后重测指定案例。脚本不修改 Skill 内容，不输出密钥或认证令牌。

本轮演示副本：`C:/Users/19105/AppData/Local/Temp/zhixing_m03_live_alizbu0p`。查看演示时，完全退出已有桌面端及后端，确认 8766 没有旧服务，再在新的 PowerShell 窗口执行：

```powershell
$env:ZHIXING_DATA_DIR = 'C:/Users/19105/AppData/Local/Temp/zhixing_m03_live_alizbu0p'
$env:ZHIXING_DB_PATH = Join-Path $env:ZHIXING_DATA_DIR 'zhixing.db'
$env:ZHIXING_STORAGE_DIR = Join-Path $env:ZHIXING_DATA_DIR 'storage'
python server/main.py
```

另一个终端进入 `client` 执行 `npx electron .`。关闭这两个终端后，在新终端启动即可回到默认业务目录。正式业务库未在开发中上架；建议去重上架清单及操作步骤见 `../../02-方案/M03阶段报告/M03开发验收报告.md`。

## M02-F 统计与隔离演示

Skill 工厂的「统计」页仅管理员可见，支持按场景与生成批次汇总六项实际指标。比例旁显示分子、分母，没有记录显示「暂无数据」；展开「统计口径」可查看定义。首次生成校验与后续重生成分开统计，人工修改字段数排除模型重写。驳回原因和无依据项处理分布按实际提交事件计数，不设置目标比例。

四份资料全部为团队自编测试材料，不能作为真实企业制度或业务准确率证明。隔离演示准备命令（项目代码目录执行）：

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = 'utf-8'
python tests/prepare_m02f_demo.py
```

默认生成 `%TEMP%/zhixing_m02f_demo/zhixing.db` 及同目录 `storage/`。准备脚本复用 M01 上传、真实 DeepSeek 抽取、确认门槛和真实 BGE 索引流程，再真实整理场景，对全部可用场景发起批次；重跑续用已有演示记录，不删除数据。可提前设置 `ZHIXING_M02F_DEMO_DIR` 指向代码目录之外的新目录。模型名、地址、密钥沿用现有服务端配置，不在命令或证据中输出。不要同时运行两个准备进程。

已准备的演示数据用以下方式启动，默认数据目录配置不变。先完全退出已有桌面端和它启动的后端，确认 8766 没有其他服务监听，避免 Electron 复用仍指向业务库的旧服务。在新的 PowerShell 窗口、项目代码目录中运行：

```powershell
# 本轮已留存可长期使用的演示快照；此处在 zhixing-desktop 目录执行。
$env:ZHIXING_DATA_DIR = (Resolve-Path '../../03-素材/Skill工厂演示数据/M02-F-0930').Path
$env:ZHIXING_DB_PATH = Join-Path $env:ZHIXING_DATA_DIR 'zhixing.db'
$env:ZHIXING_STORAGE_DIR = Join-Path $env:ZHIXING_DATA_DIR 'storage'
$env:ZHIXING_DEMO_ADMIN_PASSWORD = 'Admin@Zhixing2026'
$env:ZHIXING_DEMO_MEMBER_PASSWORD = 'Member@Zhixing2026'
$env:ZHIXING_DEMO_OTHER_PASSWORD = 'Other@Zhixing2026'
python server/main.py
```

后端保持运行，在另一个终端执行 `cd client`、`npx electron .`。Electron 复用此 8766 演示后端，前端使用原有 API 地址；进入「Skill 工厂」查看候选、修改记录、统计并导出。若使用自定义目录，三项路径环境变量须一起指向该目录。环境变量仅影响所在进程及子进程；关闭演示后端与这两个终端后，从新终端正常启动即可回到默认业务目录，无需修改 `.env`。

演示真实结果、Markdown / JSON 差异导出、统计快照与本轮测试证据在 `artifacts/m02f/`；测试截图与演示结果分别标注，模拟界面测试不作为真实模型效果。长期快照在 `../../03-素材/Skill工厂演示数据/M02-F-0930/`，通过 SQLite backup 导出，文档存储位置改为同目录文件名，内容与统计未改动。临时原始演示目录也保留，继续准备或运行审核脚本时仍使用它；移机时整体复制长期快照中的数据库与 `storage/` 并一起调整三项路径变量。没有重制便携包。

## 目录结构

```
06-代码/zhixing-desktop/
├── server/                     # 后端服务 (FastAPI + SQLite + 鉴权/任务机)
│   ├── config.py               # 服务端配置 (端口 8766，数据目录，密钥)
│   ├── database.py             # 五张核心表及原子配套表建表与连接池
│   ├── auth.py                 # 实时鉴权依赖项、角色守卫、会话管理
│   ├── models.py               # Pydantic 校验模型
│   ├── seed.py                 # 受控初始企业与测试用户自举
│   └── main.py                 # FastAPI 路由入口
├── client/                     # 桌面客户端 (Electron + Vite + React + TS)
│   ├── electron/               # Electron 主进程与预加载脚本 (无边框/IPC控制)
│   ├── src/                    # 前端源码
│   │   ├── styles/             # 设计变量 variables.css (白灰墨绿) 与全局样式
│   │   ├── components/         # 统一视觉组件 (Logo, Header, Sidebar, Views)
│   │   ├── context/            # 用户身份与会话上下文
│   │   └── services/           # 服务端 API 契约与网络请求
│   └── package.json            # 依赖与脚本
├── tests/                      # 自动化测试与验证
│   ├── run_tests.py            # 后端身份与权限单元测试 (AC01, AC02, AC33-AC36)
│   └── verify_ui_and_flows.py  # Playwright 端到端界面渲染与视觉截图验证
├── screenshots/                # 阶段性验证截图 (1440x900, 1280x800)
├── ACCEPTANCE_CHECKLIST.md     # 阶段验收清单 (持续跟踪 AC01-AC45, AT01-AT08)
├── start-server.ps1            # 后台服务一键启动脚本
└── README.md
```

## 运行方式

### 1. 启动后端服务
```powershell
cd 06-代码/zhixing-desktop/server
python main.py
```
服务默认监听 `http://127.0.0.1:8766`。初次启动自动创建数据库并自举测试账号。

Jev 为可选的后置分类建议与语义质检，默认关闭。服务端 `.env` 中设置
`JEV_ENABLED=true`、`TYPESAFE_API_KEY=...`，可选覆盖 `JEV_MODEL=jev-latest`、
`JEV_TIMEOUT_SECONDS=30` 和 `JEV_MAX_RETRIES=2`。关闭时 DeepSeek 抽取、人工审核、
确认启用与 BGE 检索保持原流程；密钥不会下发到前端或写入数据库、日志和便携包。

### 2. 本地一键验收

在项目根目录执行：

```powershell
# 快速检查：前端/Electron 类型检查 + 关键业务回归
python tests/acceptance.py quick

# 完整验收：快速检查 + 当前源码生产构建 + 全量隔离业务测试 + 关键 UI/Electron 交互
python tests/acceptance.py full
```

环境要求：Python 3.11+（已安装服务端依赖、`requests`、`playwright`），Node.js 20+，并已在 `client/` 执行 `npm install`。首次运行 UI 验收前执行 `python -m playwright install chromium`。验收不需要 DeepSeek 密钥，不调用真实收费模型。

测试数据库、上传目录、浏览器配置和端口均临时隔离，不连接或修改 `server/data/zhixing.db`。完整验收会先从当前源码重新生成 `client/dist` 与 `client/dist-electron`，构建失败时不会把旧产物当成通过。任一步骤失败都会返回非零退出码，并继续收集可独立执行的后续证据；仅当前源码构建失败时跳过 UI/Electron 检查。

每次运行的日志、请求记录、失败截图和汇总位于 `artifacts/acceptance/<时间>-<模式>/`。成功运行会清理临时资源，失败证据保留在该目录。

### 3. 运行原有后端自动化测试
```powershell
cd 06-代码/zhixing-desktop
python tests/run_tests.py
```

### 4. 运行原有端到端视觉与流转验证 (自动截图)
```powershell
cd 06-代码/zhixing-desktop
python tests/verify_ui_and_flows.py
```

### 5. 启动 Electron 桌面应用窗口
```powershell
cd 06-代码/zhixing-desktop/client
npx electron .
```

## 测试验证账号

| 角色 | 用户名 | 密码 | 姓名 | 所属企业 | 登录预期 |
|---|---|---|---|---|---|
| 管理员 | `admin` | `Admin@Zhixing2026` | 文哲 | 绿城物业运营管理中心 | 进入「知识管理」工作区 (全新库显示空白页) |
| 普通成员 | `member` | `Member@Zhixing2026` | 李景研 | 绿城物业运营管理中心 | 进入「Agent 咨询」准备状态页，知识管理入口完全隐藏 |
