"""知行有策桌面端 Windows 便携包可重复构建与打包脚本。

功能：
1. 编译前端与 Electron 主进程生产代码；
2. 组装独立便携包目录结构（包含免安装 Electron、免安装 Python 运行时、本地 BGE 模型）；
3. 制作完整一致性知识库快照（包含 SQLite 数据一致性备份、原始与解析文件拷贝、路径相对化治理、任务状态安全兜底、清理会话凭据）；
4. 输出队友使用说明与 DeepSeek 配置模板；
5. 打包生成可分发的 ZIP 压缩包。
"""
from __future__ import annotations

import datetime as dt
import importlib.metadata
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "zhixing-portable"
ZIP_FILE = PROJECT_ROOT / "zhixing-portable-windows-x64.zip"

CLIENT_DIR = ROOT_DIR / "client"
SERVER_DIR = ROOT_DIR / "server"
ELECTRON_DIST = CLIENT_DIR / "node_modules" / "electron" / "dist"
PYTHON_BASE = Path(r"D:\python")

# 真实本地 BGE 模型缓存快照目录
BGE_MODEL_SOURCE = (
    Path(os.path.expanduser(r"~/.cache/huggingface/hub/models--BAAI--bge-small-zh-v1.5/snapshots/7999e1d3359715c523056ef9478215996d62a620"))
)

# 后端运行时所必需的顶层分发包名称
REQUIRED_DIST_ROOTS = [
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic-settings",
    "python-multipart",
    "annotated-doc",
    "annotated-types",
    "python-dotenv",
    "python-docx",
    "docx2python",
    "pypdf",
    "pypdfium2",
    "sentence-transformers",
    "torch",
    "torchgen",
    "functorch",
    "transformers",
    "tokenizers",
    "huggingface-hub",
    "safetensors",
    "accelerate",
    "requests",
    "httptools",
    "pyyaml",
    "scipy",
    "scikit-learn",
    "jinja2",
    "markupsafe",
    "sympy",
    "mpmath",
    "networkx",
    "filelock",
    "fsspec",
    "typing-extensions",
    "typing-inspection",
]


def log_step(name: str):
    print(f"\n{'='*20} [STEP] {name} {'='*20}")


def run_cmd(args: list[str], cwd: Path, timeout: int = 300):
    cmd_str = " ".join(args)
    print(f"Executing: {cmd_str} (cwd: {cwd})")
    res = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if res.returncode != 0:
        print("STDOUT:", res.stdout)
        print("STDERR:", res.stderr)
        raise RuntimeError(f"命令执行失败 (code {res.returncode}): {cmd_str}")
    return res.stdout


def copy_folder_pruned(src: Path, dst: Path, exclude_exts: set[str] | None = None):
    """递归复制文件夹，排除不必要的构建期文件（如 .lib, .pdb, __pycache__）。"""
    if exclude_exts is None:
        exclude_exts = {".lib", ".pdb"}
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        # 忽略 __pycache__
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel = Path(root).relative_to(src)
        cur_dst = dst / rel
        cur_dst.mkdir(parents=True, exist_ok=True)
        for f in files:
            suffix = Path(f).suffix.lower()
            if suffix in exclude_exts:
                continue
            src_file = Path(root) / f
            dst_file = cur_dst / f
            if not dst_file.exists():
                shutil.copy2(src_file, dst_file)


def build_client_artifacts():
    log_step("1. 编译客户端前端与 Electron 产物")
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    run_cmd([npm_cmd, "run", "build"], CLIENT_DIR, timeout=180)
    print("Vite 与 Electron main 生产构建完成。")


def apply_exe_branding(exe_path: Path):
    """用 rcedit 把应用图标与版本信息写入改名后的 Electron 可执行文件。"""
    rcedit = CLIENT_DIR / "node_modules" / "rcedit" / "bin" / "rcedit-x64.exe"
    icon = CLIENT_DIR / "build" / "icon.ico"
    if not (rcedit.exists() and icon.exists() and exe_path.exists()):
        print("未找到 rcedit 或图标文件，可执行文件保持 Electron 默认图标。")
        return
    run_cmd(
        [
            str(rcedit),
            str(exe_path),
            "--set-icon", str(icon),
            "--set-version-string", "FileDescription", "知行有策",
            "--set-version-string", "ProductName", "知行有策",
            "--set-version-string", "CompanyName", "知行有策团队",
            "--set-version-string", "OriginalFilename", "知行有策.exe",
        ],
        CLIENT_DIR,
        timeout=60,
    )
    print("已写入应用图标与版本信息。")


def assemble_electron_shell():
    log_step("2. 组装免安装 Electron 外壳")
    if not ELECTRON_DIST.exists():
        raise RuntimeError(f"未找到 Electron 预编译目录: {ELECTRON_DIST}")

    print("复制 Electron 原生二进制与资源...")
    copy_folder_pruned(ELECTRON_DIST, OUTPUT_DIR)

    # 重命名可执行程序
    orig_exe = OUTPUT_DIR / "electron.exe"
    target_exe = OUTPUT_DIR / "知行有策.exe"
    if orig_exe.exists():
        if target_exe.exists():
            target_exe.unlink()
        orig_exe.rename(target_exe)
    apply_exe_branding(target_exe)

    # 移除默认 asar，准备专用 app 目录
    default_asar = OUTPUT_DIR / "resources" / "default_app.asar"
    if default_asar.exists():
        default_asar.unlink()

    app_dir = OUTPUT_DIR / "resources" / "app"
    app_dir.mkdir(parents=True, exist_ok=True)

    # 写入 app package.json
    app_pkg_json = app_dir / "package.json"
    app_pkg_json.write_text(
        '{\n  "name": "zhixing-desktop",\n  "version": "1.0.0",\n  "main": "dist-electron/main.js"\n}\n',
        encoding="utf-8",
    )

    # 复制 dist 与 dist-electron
    copy_folder_pruned(CLIENT_DIR / "dist", app_dir / "dist")
    copy_folder_pruned(CLIENT_DIR / "dist-electron", app_dir / "dist-electron")
    print("Electron 应用资源组装完成。")


def assemble_python_runtime():
    log_step("3. 组装独立免安装 Python 运行时")
    py_target = OUTPUT_DIR / "backend" / "python"
    py_target.mkdir(parents=True, exist_ok=True)

    # 复制核心 exe 和 dll
    core_files = [
        "python.exe",
        "pythonw.exe",
        "python3.dll",
        "python313.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ]
    for cf in core_files:
        src = PYTHON_BASE / cf
        if src.exists():
            shutil.copy2(src, py_target / cf)

    # 复制 DLLs 目录
    print("复制 CPython DLLs...")
    copy_folder_pruned(PYTHON_BASE / "DLLs", py_target / "DLLs")

    # 复制标准库 Lib (排除 site-packages)
    print("复制 CPython 标准库...")
    lib_src = PYTHON_BASE / "Lib"
    lib_dst = py_target / "Lib"
    lib_dst.mkdir(parents=True, exist_ok=True)
    for item in lib_src.iterdir():
        if item.name == "site-packages":
            continue
        dst_item = lib_dst / item.name
        if item.is_dir():
            copy_folder_pruned(item, dst_item)
        else:
            shutil.copy2(item, dst_item)

    # 解析所有依赖的分发包
    print("解析依赖包元数据...")
    all_dists = set()
    queue = list(REQUIRED_DIST_ROOTS)
    while queue:
        curr = queue.pop(0).lower().replace("_", "-")
        if curr in all_dists:
            continue
        all_dists.add(curr)
        try:
            reqs = importlib.metadata.requires(curr)
        except importlib.metadata.PackageNotFoundError:
            continue
        if not reqs:
            continue
        for req in reqs:
            if "extra ==" in req:
                continue
            m = re.match(r"^([a-zA-Z0-9_\-\.]+)", req.strip())
            if m:
                dep = m.group(1).lower().replace("_", "-")
                if dep not in all_dists:
                    queue.append(dep)

    print(f"解析到 {len(all_dists)} 个运行时包分发项，正在复制到 site-packages...")
    sp_dst = lib_dst / "site-packages"
    sp_dst.mkdir(parents=True, exist_ok=True)
    sp_src = PYTHON_BASE / "Lib" / "site-packages"

    for dist_name in all_dists:
        try:
            files = importlib.metadata.files(dist_name)
        except importlib.metadata.PackageNotFoundError:
            continue
        if not files:
            continue
        for f in files:
            src_path = Path(f.locate())
            if not src_path.exists() or src_path.is_dir():
                continue
            if src_path.suffix.lower() in [".lib", ".pdb"]:
                continue
            try:
                rel = src_path.relative_to(sp_src)
            except ValueError:
                continue
            dst_path = sp_dst / rel
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            if not dst_path.exists():
                shutil.copy2(src_path, dst_path)

    # 复制 *.libs 目录（如 numpy.libs, scipy.libs 等 C 扩展依赖）
    for libs_dir in sp_src.glob("*.libs"):
        dst_libs = sp_dst / libs_dir.name
        if not dst_libs.exists():
            copy_folder_pruned(libs_dir, dst_libs)

    print("Python 独立运行时组装完成。")


def assemble_backend_server():
    log_step("4. 复制后端服务代码")
    server_target = OUTPUT_DIR / "backend" / "server"
    server_target.mkdir(parents=True, exist_ok=True)

    for py_file in SERVER_DIR.glob("*.py"):
        shutil.copy2(py_file, server_target / py_file.name)

    # M02 Skill Schema 是运行时读取的结构定义，随代码一起分发
    for schema_file in SERVER_DIR.glob("skill_schema_v*.json"):
        shutil.copy2(schema_file, server_target / schema_file.name)

    # M02-C 拆分与生成提示词文件，运行时按版本号读取
    prompts_target = server_target / "prompts"
    prompts_target.mkdir(parents=True, exist_ok=True)
    for prompt_file in (SERVER_DIR / "prompts").glob("*.md"):
        shutil.copy2(prompt_file, prompts_target / prompt_file.name)

    print("后端业务代码复制完成（不包含私人 .env 凭据）。")


def assemble_bge_model():
    log_step("5. 随包携带本地 BGE 检索模型")
    if not BGE_MODEL_SOURCE.exists():
        raise RuntimeError(f"本地 BGE 模型不存在: {BGE_MODEL_SOURCE}")

    model_target = OUTPUT_DIR / "backend" / "models" / "bge-small-zh-v1.5"
    model_target.mkdir(parents=True, exist_ok=True)
    copy_folder_pruned(BGE_MODEL_SOURCE, model_target)
    print(f"BGE 模型成功存入 {model_target}")


def generate_knowledge_snapshot():
    log_step("6. 生成一致性知识库快照与数据初始化")
    snapshot_time = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    src_db_path = SERVER_DIR / "data" / "zhixing.db"
    if not src_db_path.exists():
        raise RuntimeError(f"原知识库数据库不存在: {src_db_path}")

    # 快照模板目录（用于首次初始化或恢复）
    tmpl_data = OUTPUT_DIR / "backend" / "snapshot" / "data"
    tmpl_storage = tmpl_data / "storage"
    tmpl_storage.mkdir(parents=True, exist_ok=True)
    tmpl_db_path = tmpl_data / "zhixing.db"

    # 执行一致性在线备份（自动处理 SQLite WAL）
    print("通过 SQLite Backup API 创建 point-in-time 一致性数据库副本...")
    src_conn = sqlite3.connect(str(src_db_path), timeout=20.0)
    dest_conn = sqlite3.connect(str(tmpl_db_path), timeout=20.0)
    src_conn.backup(dest_conn)
    src_conn.close()

    dest_conn.row_factory = sqlite3.Row
    cur = dest_conn.cursor()

    # 1. 清理已有活动 session，队友使用标准账号登录
    cur.execute("DELETE FROM sessions")

    # 2. 安全处理任务状态：将任何 queued / running 任务重置为 cancelled，防止跨机孤儿任务跑飞
    cur.execute("UPDATE processing_tasks SET status = 'cancelled' WHERE status IN ('queued', 'running')")
    # Jev 评估不会随便携快照恢复并触发付费调用；已有已完成结果保留供审阅。
    jev_table = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jev_evaluations'"
    ).fetchone()
    if jev_table:
        cur.execute(
            "UPDATE jev_evaluations SET status = 'stale', completed_at = ? WHERE status IN ('queued', 'running')",
            (snapshot_time,),
        )

    # 3. 治理本机绝对路径：排查并规范化 storage_reference 为相对路径文件名
    rows = cur.execute("SELECT id, storage_reference FROM document_versions").fetchall()
    updated_paths = 0
    for r in rows:
        old_ref = r["storage_reference"]
        norm_ref = Path(old_ref).name
        if old_ref != norm_ref:
            cur.execute("UPDATE document_versions SET storage_reference = ? WHERE id = ?", (norm_ref, r["id"]))
            updated_paths += 1
    dest_conn.commit()

    # 4. 核对统计指标
    doc_count = cur.execute("SELECT count(*) FROM documents WHERE is_deleted = 0").fetchone()[0]
    total_docs = cur.execute("SELECT count(*) FROM documents").fetchone()[0]
    doc_ver_count = cur.execute("SELECT count(*) FROM document_versions").fetchone()[0]
    ki_active = cur.execute("SELECT count(*) FROM knowledge_items WHERE lifecycle_status = 'active' AND is_excluded = 0").fetchone()[0]
    ki_total = cur.execute("SELECT count(*) FROM knowledge_items").fetchone()[0]
    kv_ready_indexed = cur.execute("SELECT count(*) FROM knowledge_versions WHERE index_status = 'ready'").fetchone()[0]
    kv_total = cur.execute("SELECT count(*) FROM knowledge_versions").fetchone()[0]
    retrieval_count = cur.execute("SELECT count(*) FROM retrieval_records").fetchone()[0]
    user_records = [dict(u) for u in cur.execute("SELECT username, display_name, role, account_status FROM users").fetchall()]

    dest_conn.close()

    # 5. 复制 storage 原始与解析文件
    src_storage = SERVER_DIR / "data" / "storage"
    if src_storage.exists():
        print("复制上传资料物理文件与解析产物...")
        copy_folder_pruned(src_storage, tmpl_storage)

    # 6. 同时预先拷贝一份至 <portable_root>/data/，使队友解压后立即可直接使用
    runtime_data = OUTPUT_DIR / "data"
    runtime_data.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tmpl_db_path, runtime_data / "zhixing.db")
    copy_folder_pruned(tmpl_storage, runtime_data / "storage")

    stats = {
        "snapshot_time": snapshot_time,
        "total_docs": total_docs,
        "active_docs": doc_count,
        "doc_versions": doc_ver_count,
        "total_knowledge_items": ki_total,
        "active_knowledge_items": ki_active,
        "total_knowledge_versions": kv_total,
        "indexed_knowledge_versions": kv_ready_indexed,
        "retrieval_records": retrieval_count,
        "updated_paths": updated_paths,
        "users": user_records,
    }

    print("知识库快照生成完成。统计结果：", stats)
    return stats


def assemble_launchers_and_docs(stats: dict):
    log_step("7. 生成便携启动脚本、配置模板与使用说明")

    # 启动批处理脚本
    bat_content = """@echo off
chcp 65001 >nul
title 知行有策 知识资产平台
cd /d "%~dp0"

echo 正在启动 知行有策 桌面端...
start "" "%~dp0知行有策.exe"
exit
"""
    (OUTPUT_DIR / "启动知行有策.bat").write_text(bat_content, encoding="utf-8")

    # .env.example 配置模板
    env_example = """# 知行有策 环境变量配置
# 1. 基础使用（浏览知识库、查看与下载来源文件、执行本地 BGE 语义与关键词混合检索）无需任何配置，解压即用。
# 2. 若需在线使用大模型知识抽取功能，请将本文件重命名为 .env 并填入 DeepSeek API Key：
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# Jev 分类建议与语义质检（可选，默认关闭）
JEV_ENABLED=false
TYPESAFE_API_KEY=
JEV_API_URL=https://api.typesafe.ai/v1/systemone
JEV_MODEL=jev-latest
JEV_TIMEOUT_SECONDS=30
JEV_MAX_RETRIES=2

# 服务端口（默认 8766，如端口被占用可在此修改）
SERVER_PORT=8766
"""
    (OUTPUT_DIR / ".env.example").write_text(env_example, encoding="utf-8")

    # 详细中文使用说明
    readme_content = f"""# 知行有策 桌面知识资产平台 (Windows 便携版)

面向物业咨询业务的知识资产生产平台桌面端软件。
本便携包已包含完整的独立运行环境（Electron、Python 3.13 运行时、本地 BGE 混合检索模型）以及打包时的完整知识库数据快照。

队友在未安装 Python、Node.js 或任何开发工具的 Windows 电脑上，解压即可直接双击运行。

---

## 一、启动方式

1. **直接启动**：双击文件夹根目录下的 `知行有策.exe`（或双击 `启动知行有策.bat`）。
2. 首次启动会自动拉起随包附带的后台服务并载入本地知识库，稍候片刻即可进入界面。

---

## 二、测试验证登录账号

随包数据库已包含经过验证的测试账号：

| 角色 | 用户名 | 密码 | 姓名 | 所属企业 | 登录权限范围 |
|---|---|---|---|---|---|
| **管理员** | `admin` | `Admin@Zhixing2026` | 文哲 | 绿城物业服务集团 | 拥有全部管理权限：资料导入、知识核对、全量混合检索 |
| **普通成员** | `member` | `Member@Zhixing2026` | 李景研 | 绿城物业服务集团 | 普通成员视图 |

---

## 三、知识库快照信息

- **快照打包时间**：{stats['snapshot_time']}
- **资料文件数量**：{stats['total_docs']} 份（其中有效资料 {stats['active_docs']} 份，版本记录 {stats['doc_versions']} 条）
- **知识条目数量**：{stats['total_knowledge_items']} 条（其中有效启用 {stats['active_knowledge_items']} 条）
- **知识版本记录**：{stats['total_knowledge_versions']} 条（其中就绪索引版本 {stats['indexed_knowledge_versions']} 条）
- **混合检索索引**：{stats['retrieval_records']} 条持久化向量与关键词索引
- **物理原始资料**：随包携带完整 `data/storage/` 原始上传文件与解析产物
- **本地语义模型**：随包携带 BAAI/bge-small-zh-v1.5 512 维稠密向量模型，本地离线计算

---

## 四、DeepSeek API Key 配置（可选）

- **无需 Key**：浏览已有知识库、查看正文与段落证据锚点、打开/下载原文件、执行本地 BGE 语义检索与混合检索，均完全在本地离线运行，**不需要**配置 API Key。
- **配置 Key**：如需导入新文件并触发 DeepSeek 自动抽取知识候选，请按以下步骤操作：
  1. 将软件根目录下的 `.env.example` 复制一份并重命名为 `.env`；
  2. 用记事本打开 `.env`，填入：`DEEPSEEK_API_KEY=你的真实key`；
  3. 保存并重启软件即可生效。

---

## 五、数据保存与备份

1. **数据目录位置**：所有运行期修改（新导入文件、新修改条目、审核状态、新增索引）均实时保存于便携包根目录下的 `data/` 目录中。
2. **安全隔离机制**：软件严格区分首次初始化与后续启动，启动时决不使用初始快照覆盖队友已修改的数据。
3. **一键备份方法**：直接复制保存 `data/` 文件夹即可完成全部知识与配置的备份。
4. **移动与便携**：支持将整个便携包文件夹移动到任意磁盘、路径（支持中文、空格路径），数据与检索功能持续有效。
"""
    (OUTPUT_DIR / "使用说明.txt").write_text(readme_content, encoding="utf-8")
    print("启动脚本、配置文件与使用说明生成完成。")


def create_zip_archive():
    log_step("8. 生成分发 ZIP 压缩包")
    if ZIP_FILE.exists():
        ZIP_FILE.unlink()

    print(f"正在压缩便携包至: {ZIP_FILE} ...")
    total_files = 0
    with zipfile.ZipFile(str(ZIP_FILE), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(OUTPUT_DIR):
            for f in files:
                full_path = Path(root) / f
                rel_path = full_path.relative_to(PROJECT_ROOT)
                zf.write(full_path, arcname=str(rel_path))
                total_files += 1
                if total_files % 200 == 0:
                    print(f"已压缩 {total_files} 个文件...")

    zip_size_mb = ZIP_FILE.stat().st_size / (1024 * 1024)
    dir_size_mb = sum(f.stat().st_size for f in OUTPUT_DIR.rglob('*') if f.is_file()) / (1024 * 1024)

    print(f"\n便携包构建成功！")
    print(f"- 便携包目录: {OUTPUT_DIR}")
    print(f"- 便携包解压体积: {dir_size_mb:.2f} MB")
    print(f"- ZIP 压缩包路径: {ZIP_FILE}")
    print(f"- ZIP 文件体积: {zip_size_mb:.2f} MB")
    print(f"- 目标架构: Windows x64 (AMD64)")


def main():
    print(f"开始制作「知行有策」Windows 便携包...")
    start_time = dt.datetime.now()

    build_client_artifacts()
    assemble_electron_shell()
    assemble_python_runtime()
    assemble_backend_server()
    assemble_bge_model()
    stats = generate_knowledge_snapshot()
    assemble_launchers_and_docs(stats)
    create_zip_archive()

    duration = (dt.datetime.now() - start_time).total_seconds()
    print(f"\n全部流程完成，耗时 {duration:.1f} 秒。")


if __name__ == "__main__":
    main()
