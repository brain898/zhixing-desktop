# 知行有策 桌面客户端与后台服务 (Zhixing Desktop)

面向物业咨询业务的知识资产与 AI Skill 生产平台桌面端软件。

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

### 2. 运行自动化测试
```powershell
cd 06-代码/zhixing-desktop
python tests/run_tests.py
```

### 3. 运行端到端视觉与流转验证 (自动截图)
```powershell
cd 06-代码/zhixing-desktop
python tests/verify_ui_and_flows.py
```

### 4. 启动 Electron 桌面应用窗口
```powershell
cd 06-代码/zhixing-desktop/client
npx electron .
```

## 测试验证账号

| 角色 | 用户名 | 密码 | 姓名 | 所属企业 | 登录预期 |
|---|---|---|---|---|---|
| 管理员 | `admin` | `Admin@Zhixing2026` | 文哲 | 绿城物业运营管理中心 | 进入「知识管理」工作区 (全新库显示空白页) |
| 普通成员 | `member` | `Member@Zhixing2026` | 李景研 | 绿城物业运营管理中心 | 进入「Agent 咨询」准备状态页，知识管理入口完全隐藏 |
