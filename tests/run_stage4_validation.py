"""Stage 4A～4C + 4E 一键回归入口。

每个测试文件使用独立 Python 子进程，避免测试数据库环境变量与后台任务线程互相污染。
Agent / Stage 4D 不在当前知识资产模块验收范围。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TESTS = [
    "tests/test_stage4a_eligibility.py",
    "tests/test_stage4b_hybrid_retrieval.py",
    "tests/test_stage4c_admin_search.py",
    "tests/test_stage4e_benchmark_suite.py",
    "tests/test_stage4e_full_lifecycle.py",
    "tests/verify_stage4c_ui.py",
]


def run() -> int:
    for relative in TESTS:
        print(f"\n=== {relative} ===", flush=True)
        completed = subprocess.run(
            [sys.executable, str(ROOT / relative)],
            cwd=ROOT,
            check=False,
        )
        if completed.returncode != 0:
            print(f"\n[FAIL] {relative} -> exit {completed.returncode}")
            return completed.returncode

    print("\n[PASS] Stage 4A～4C + 4E 全部验证通过")
    print("[NOTE] 前端 production build 另执行：cd client && npm run build")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
