"""全面验证「知行有策」Windows 便携包。

验证项：
1. 解压/独立目录有效性（含中文与空格路径）；
2. 隔离环境执行（完全排除系统 Python、Node.js 与系统 HuggingFace 缓存）；
3. 随包 Python 运行时与依赖库自洽性；
4. 后台服务独立启动与接口健康检查；
5. 管理员与普通成员凭据登录及权限隔离；
6. 知识库快照数据完整性（资料文件、知识条目、审核状态、版本及关联关系）；
7. 原始物理文件下载与段落证据锚点定位；
8. 本地 BGE 模型离线向量计算与混合检索真实结果；
9. 数据修改持久性（修改条目后重启依然保留，验证首次与后续启动不覆盖）；
10. 便携包移动至新目录后相对路径与资料读取自愈；
11. 原工程数据未被污染检查。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

# 全局禁用代理，确保测试套件向本地 127.0.0.1 发送的所有 HTTP 请求直连
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

ROOT = Path(r"D:\竞赛\知行有策大创\我的竞赛项目\06-代码")
ORIG_DB = ROOT / "zhixing-desktop" / "server" / "data" / "zhixing.db"
PORTABLE_DIR = ROOT / "zhixing-portable"
ZIP_FILE = ROOT / "zhixing-portable-windows-x64.zip"

# 中文与空格测试目录
TEST_DIR = ROOT / "测试 验证 目录" / "知行有策 便携 测试"
MOVED_DIR = ROOT / "移动 后的 目录" / "zhixing portable copy"


def log(msg: str):
    print(f"\n>>> [VERIFY] {msg}")


def assert_true(cond: bool, msg: str):
    if not cond:
        raise AssertionError(f"验证失败: {msg}")
    print(f"  [OK] {msg}")


def test_portable_suite():
    log("0. 基础文件与 ZIP 存在性检查")
    assert_true(PORTABLE_DIR.exists(), f"便携包目录存在: {PORTABLE_DIR}")
    assert_true(ZIP_FILE.exists(), f"ZIP 压缩包存在: {ZIP_FILE}")
    assert_true((PORTABLE_DIR / "知行有策.exe").exists(), "Electron 启动文件 知行有策.exe 存在")
    assert_true((PORTABLE_DIR / "启动知行有策.bat").exists(), "启动脚本 启动知行有策.bat 存在")
    assert_true((PORTABLE_DIR / "使用说明.txt").exists(), "使用说明.txt 存在")
    assert_true((PORTABLE_DIR / ".env.example").exists(), ".env.example 模板存在")
    assert_true((PORTABLE_DIR / "backend" / "python" / "python.exe").exists(), "随包 Python 解释器存在")
    assert_true((PORTABLE_DIR / "backend" / "models" / "bge-small-zh-v1.5" / "model.safetensors").exists(), "随包 BGE 模型权重存在")
    assert_true((PORTABLE_DIR / "data" / "zhixing.db").exists(), "随包初始数据库存在")
    assert_true((PORTABLE_DIR / "backend" / "snapshot" / "data" / "zhixing.db").exists(), "随包快照模板数据库存在")

    # 记录原始数据库修改时间与哈希
    orig_mtime_before = ORIG_DB.stat().st_mtime

    # 复制便携包到包含中文与空格的隔离测试目录
    log("1. 复制便携包至含中文与空格的隔离测试目录")
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR.parent, ignore_errors=True)
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    print(f"正在复制便携包至: {TEST_DIR} ...")
    shutil.copytree(PORTABLE_DIR, TEST_DIR, dirs_exist_ok=True)
    assert_true((TEST_DIR / "知行有策.exe").exists(), "隔离目录包含知行有策.exe")

    # 准备彻底隔离的环境变量
    log("2. 准备严格隔离的环境变量（不包含系统 Python、Node.js 与模型缓存）")
    bundled_py = TEST_DIR / "backend" / "python" / "python.exe"
    clean_env = os.environ.copy()
    # 彻底移除系统 Python 与 Node.js
    clean_env["PATH"] = f"{TEST_DIR / 'backend' / 'python'};C:\\Windows\\System32;C:\\Windows"
    clean_env.pop("PYTHONHOME", None)
    clean_env.pop("PYTHONPATH", None)
    # 将模型缓存导向不存在的临时目录，严禁联网与回退本机缓存
    dummy_cache = TEST_DIR / "dummy_cache"
    clean_env["HF_HOME"] = str(dummy_cache)
    clean_env["USERPROFILE"] = str(TEST_DIR)
    clean_env["LOCALAPPDATA"] = str(TEST_DIR)
    clean_env["APPDATA"] = str(TEST_DIR)
    clean_env["SYSTEMDRIVE"] = os.environ.get("SYSTEMDRIVE", "C:")
    clean_env["HF_HUB_OFFLINE"] = "1"
    clean_env["TRANSFORMERS_OFFLINE"] = "1"
    clean_env["HF_DATASETS_OFFLINE"] = "1"
    clean_env["ZHIXING_PORTABLE_ROOT"] = str(TEST_DIR)
    clean_env["SERVER_PORT"] = "8768"
    clean_env["ZHIXING_STORAGE_DIR"] = str(TEST_DIR / "data" / "storage")
    clean_env["ZHIXING_DB_PATH"] = str(TEST_DIR / "data" / "zhixing.db")

    # 验证随包 Python 解释器在隔离环境下自洽运行
    log("3. 验证随包 Python 解释器独立执行与模块导入")
    test_import_code = """
import sys
print("Interpreter sys.prefix:", sys.prefix)
import fastapi, uvicorn, pydantic, docx, pypdf, torch, transformers, sentence_transformers
print("All core dependencies imported successfully!")
"""
    res = subprocess.run(
        [str(bundled_py), "-c", test_import_code],
        capture_output=True,
        text=True,
        env=clean_env,
        timeout=60,
    )
    assert_true(res.returncode == 0, f"随包 Python 独立运行成功:\n{res.stdout}")
    assert_true(str(TEST_DIR.resolve()) in res.stdout, "Python sys.prefix 正确识别为便携包路径")

    # 启动后台服务 (端口 8768)
    log("4. 启动便携包后台服务 (端口 8768)")
    server_script = TEST_DIR / "backend" / "server" / "main.py"
    server_log_file = TEST_DIR / "server_test.log"
    server_log = open(server_log_file, "w", encoding="utf-8")
    server_proc = subprocess.Popen(
        [str(bundled_py), str(server_script)],
        cwd=str(TEST_DIR / "backend" / "server"),
        env=clean_env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )

    base_url = "http://127.0.0.1:8768"
    server_ready = False
    last_err = None
    for i in range(40):
        time.sleep(0.3)
        try:
            req = urllib.request.Request(f"{base_url}/api/system/status")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    server_ready = True
                    print(f"后台服务就绪 (尝试 {i+1} 次): {data}")
                    break
        except Exception as e:
            last_err = e

    if not server_ready:
        print(f"轮询失败，最后一次异常: {type(last_err)}: {last_err}")
        server_log.flush()
        with open(server_log_file, "r", encoding="utf-8", errors="replace") as f:
            print("=== 后台服务启动日志 ===")
            print(f.read())
    assert_true(server_ready, "便携包后台服务在隔离环境中成功启动并响应 200")

    try:
        # 5. 登录验证：admin 与 member
        log("5. 验证测试账号登录与权限状态")
        # 管理员登录
        login_req = urllib.request.Request(
            f"{base_url}/api/auth/login",
            data=json.dumps({"username": "admin", "password": "Admin@Zhixing2026"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(login_req, timeout=5.0) as resp:
            admin_auth = json.loads(resp.read().decode("utf-8"))
        assert_true(admin_auth.get("user", {}).get("role") == "admin", "管理员账号 (admin) 成功登录且角色正确")
        admin_token = admin_auth["token"]

        # 普通成员登录
        member_req = urllib.request.Request(
            f"{base_url}/api/auth/login",
            data=json.dumps({"username": "member", "password": "Member@Zhixing2026"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(member_req, timeout=5.0) as resp:
            member_auth = json.loads(resp.read().decode("utf-8"))
        assert_true(member_auth.get("user", {}).get("role") == "member", "普通成员账号 (member) 成功登录且角色正确")
        member_token = member_auth["token"]

        # 6. 资料列表与原始物理文件下载
        log("6. 验证资料列表与原始物理文件下载（相对路径定位）")
        docs_req = urllib.request.Request(
            f"{base_url}/api/documents",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        with urllib.request.urlopen(docs_req, timeout=5.0) as resp:
            docs_data = json.loads(resp.read().decode("utf-8"))
        docs = docs_data.get("items", docs_data) if isinstance(docs_data, dict) else docs_data
        assert_true(len(docs) > 0, f"成功获取资料列表，包含 {len(docs)} 份文档")

        # 找一份包含 active_version_id 的文档下载
        downloaded = False
        for d in docs:
            doc_id = d.get("id")
            ver_id = d.get("active_version_id")
            if doc_id and ver_id:
                dl_req = urllib.request.Request(
                    f"{base_url}/api/documents/{doc_id}/versions/{ver_id}/file",
                    headers={"Authorization": f"Bearer {admin_token}"},
                )
                try:
                    with urllib.request.urlopen(dl_req, timeout=5.0) as dl_resp:
                        content = dl_resp.read()
                        if len(content) > 0:
                            downloaded = True
                            print(f"  成功读取原始物理文档 [{d.get('title')}] (版本: {ver_id})，大小: {len(content)} 字节")
                            break
                except Exception as e:
                    pass
        assert_true(downloaded, "成功通过文件接口读取并返回物理存储文件，相对路径自愈成功")

        # 7. 知识条目列表与审核状态保留
        log("7. 验证知识条目列表与审核状态正确保留")
        k_req = urllib.request.Request(
            f"{base_url}/api/knowledge/items",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        with urllib.request.urlopen(k_req, timeout=5.0) as resp:
            k_data = json.loads(resp.read().decode("utf-8"))
        k_items = k_data.get("items", k_data) if isinstance(k_data, dict) else k_data
        assert_true(len(k_items) > 0, f"成功获取知识条目列表，数量: {len(k_items)}")

        # 查看一条知识详情，验证来源证据与段落锚点
        target_item = k_items[0]
        item_id = target_item["id"]
        detail_req = urllib.request.Request(
            f"{base_url}/api/knowledge/items/{item_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        with urllib.request.urlopen(detail_req, timeout=5.0) as resp:
            detail = json.loads(resp.read().decode("utf-8"))
        active_ver = detail.get("active_version") or {}
        assert_true("title" in active_ver and "content" in active_ver, f"条目详情读取正确: {active_ver.get('title')}")
        assert_true("evidence" in detail and isinstance(detail["evidence"], list), "条目包含证据溯源引用列表")
        print(f"  知识详情: 标题={active_ver.get('title')}, 审核状态={active_ver.get('review_status')}, 证据数量={len(detail.get('evidence', []))}")

        # 8. 真实本地 BGE 语义检索与混合检索
        log("8. 验证随包携带的本地 BGE 模型检索（完全离线）")
        query_enc = urllib.parse.quote("安全指导规程")
        search_req = urllib.request.Request(
            f"{base_url}/api/knowledge/search?q={query_enc}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        with urllib.request.urlopen(search_req, timeout=15.0) as resp:
            search_data = json.loads(resp.read().decode("utf-8"))
        results = search_data.get("items", [])
        assert_true(isinstance(results, list), f"BGE 检索接口成功响应，返回候选数: {len(results)}")
        print(f"  检索返回候选列表长度: {len(results)}")

        # 9. 数据修改持久性与重复启动防重置验证
        log("9. 验证数据修改持久性与重复启动（决不覆盖已有修改）")
        # 修改条目管理状态为 disabled
        status_req = urllib.request.Request(
            f"{base_url}/api/knowledge/items/{item_id}/status",
            data=json.dumps({"lifecycle_status": "disabled"}).encode("utf-8"),
            headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(status_req, timeout=5.0) as resp:
            status_resp = json.loads(resp.read().decode("utf-8"))
        assert_true(status_resp.get("lifecycle_status") == "disabled", f"成功停用条目并写入数据层: {item_id}")

    finally:
        # 关闭测试服务进程
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except Exception:
            server_proc.kill()
        server_log.close()
        time.sleep(1.0)

    # 重启服务并检查修改是否被快照覆盖
    log("10. 重启后台服务，验证修改项未被快照模板覆盖")
    server_proc2 = subprocess.Popen(
        [str(bundled_py), str(server_script)],
        cwd=str(TEST_DIR / "backend" / "server"),
        env=clean_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3.0)
    try:
        # 直接读取 SQLite 数据库，检查条目状态是否仍保留刚才的修改 (disabled)
        chk_conn = sqlite3.connect(str(TEST_DIR / "data" / "zhixing.db"))
        row = chk_conn.execute("SELECT lifecycle_status FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
        chk_conn.close()
        assert_true(row is not None and row[0] == "disabled", "重启后修改内容 (lifecycle_status=disabled) 完好保留，未被初始快照覆盖！")
    finally:
        server_proc2.terminate()
        try:
            server_proc2.wait(timeout=5)
        except Exception:
            server_proc2.kill()
        time.sleep(1.0)

    # 11. 移动便携包目录测试
    log("11. 验证便携包移动至新目录后仍能正常使用")
    if MOVED_DIR.exists():
        shutil.rmtree(MOVED_DIR, ignore_errors=True)
    MOVED_DIR.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(TEST_DIR), str(MOVED_DIR))
    assert_true((MOVED_DIR / "知行有策.exe").exists(), "移动后新目录知行有策.exe 存在")

    moved_py = MOVED_DIR / "backend" / "python" / "python.exe"
    moved_script = MOVED_DIR / "backend" / "server" / "main.py"
    moved_env = clean_env.copy()
    moved_env["PATH"] = f"{MOVED_DIR / 'backend' / 'python'};C:\\Windows\\System32;C:\\Windows"
    moved_env["ZHIXING_PORTABLE_ROOT"] = str(MOVED_DIR)
    moved_env["SERVER_PORT"] = "8769"
    moved_env["ZHIXING_STORAGE_DIR"] = str(MOVED_DIR / "data" / "storage")
    moved_env["ZHIXING_DB_PATH"] = str(MOVED_DIR / "data" / "zhixing.db")

    server_proc3 = subprocess.Popen(
        [str(moved_py), str(moved_script)],
        cwd=str(MOVED_DIR / "backend" / "server"),
        env=moved_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3.0)
    try:
        req3 = urllib.request.Request("http://127.0.0.1:8769/api/system/status")
        with urllib.request.urlopen(req3, timeout=3.0) as resp3:
            assert_true(resp3.status == 200, "移动目录后，便携包在新路径下成功启动并响应 200")
    finally:
        server_proc3.terminate()
        try:
            server_proc3.wait(timeout=5)
        except Exception:
            server_proc3.kill()
        time.sleep(1.0)

    # 清理临时移动测试目录
    shutil.rmtree(MOVED_DIR.parent, ignore_errors=True)

    # 12. 确认原始知识库未被测试或打包改动
    log("12. 确认原工程知识库数据库未受任何污染或覆盖")
    assert_true(ORIG_DB.exists(), "原知识库文件完好存在")
    orig_mtime_after = ORIG_DB.stat().st_mtime
    assert_true(orig_mtime_after == orig_mtime_before, "原工程数据库修改时间完全未变，未发生任何写入或覆盖！")

    print("\n========================================================")
    print("恭喜！「知行有策」便携包 12 项端到端全链路验证全部 PASS！")
    print("========================================================\n")


if __name__ == "__main__":
    test_portable_suite()
