"""
测试环境隔离辅助工具：
确保所有单元测试与自动化测试套件在独立的临时 SQLite 数据库中运行，
绝不污染或重置真实业务数据库 server/data/zhixing.db。
"""

import os
import sys
import uuid
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

def setup_test_db(suite_name: str = "test") -> Path:
    """
    为测试套件建立独立的临时数据库与临时 storage，并注入对应环境变量。
    同时执行 init_db() 与 seed_data(force=True) 初始化干净环境。
    """
    temp_dir = Path(tempfile.gettempdir()) / "zhixing_test_isolated_dbs"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    unique_id = uuid.uuid4().hex[:8]
    temp_db_path = (temp_dir / f"{suite_name}_{unique_id}.db").resolve()
    temp_storage_dir = (temp_dir / f"{suite_name}_{unique_id}_storage").resolve()
    temp_storage_dir.mkdir(parents=True, exist_ok=True)

    # 同时隔离数据库与上传文件存储目录，避免测试写入真实 server/data。
    os.environ["ZHIXING_DB_PATH"] = str(temp_db_path)
    os.environ["ZHIXING_STORAGE_DIR"] = str(temp_storage_dir)
    os.environ["ZHIXING_DEMO_ADMIN_PASSWORD"] = "Admin@Zhixing2026"
    os.environ["ZHIXING_DEMO_MEMBER_PASSWORD"] = "Member@Zhixing2026"
    os.environ["ZHIXING_DEMO_OTHER_PASSWORD"] = "Other@Zhixing2026"

    import config
    config.DEEPSEEK_API_KEY = ""  # 避免测试期间触发外部模型网络调用
    
    from database import init_db
    from seed import seed_data
    
    init_db()
    seed_data(force=True)
    
    return temp_db_path

def cleanup_test_db(db_path: Path):
    """清理测试数据库、WAL/SHM 与同套件临时 storage，并恢复环境变量。"""
    if not db_path:
        return

    # 先等待线程函数真正退出，再确认持久化状态收敛。只看数据库 completed 不足以
    # 证明 worker 已退出，提前删库会让迟到线程访问已清理的数据库。
    from tasks import wait_for_background_tasks
    if not wait_for_background_tasks(timeout=30.0):
        raise RuntimeError(f"隔离测试后台任务在 30 秒内未退出，保留现场不清理：{db_path}")

    deadline = time.time() + 30.0
    while db_path.exists() and time.time() < deadline:
        try:
            conn = sqlite3.connect(str(db_path), timeout=0.2)
            row = conn.execute(
                "SELECT COUNT(*) FROM processing_tasks WHERE status IN ('queued', 'running')"
            ).fetchone()
            conn.close()
            if not row or int(row[0] or 0) == 0:
                break
        except sqlite3.Error:
            break
        time.sleep(0.05)

    if db_path.exists():
        conn = sqlite3.connect(str(db_path), timeout=0.2)
        remaining = conn.execute(
            "SELECT COUNT(*) FROM processing_tasks WHERE status IN ('queued', 'running')"
        ).fetchone()[0]
        conn.close()
        if int(remaining or 0) != 0:
            raise RuntimeError(f"隔离测试仍有 {remaining} 个持久化后台任务，保留现场不清理：{db_path}")
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db_path) + suffix)
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass

    storage_dir = db_path.parent / f"{db_path.stem}_storage"
    if storage_dir.exists():
        shutil.rmtree(storage_dir, ignore_errors=True)

    if os.environ.get("ZHIXING_DB_PATH") == str(db_path):
        os.environ.pop("ZHIXING_DB_PATH", None)
    if os.environ.get("ZHIXING_STORAGE_DIR") == str(storage_dir):
        os.environ.pop("ZHIXING_STORAGE_DIR", None)
