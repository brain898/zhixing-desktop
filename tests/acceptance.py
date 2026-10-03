"""知行有策桌面端本地一键验收入口。

quick: 前端/Electron 类型检查 + 关键业务回归。
full: quick + 当前源码生产构建 + 全量隔离业务测试 + 关键 UI/Electron 交互。

每个 Python 测试文件使用独立子进程，避免模块缓存、数据库环境变量和后台线程
跨测试相互污染。日志写入 artifacts/acceptance，失败现场不会被本脚本删除。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client"
ARTIFACT_ROOT = ROOT / "artifacts" / "acceptance"


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]
    cwd: Path
    timeout_seconds: int


def npm_command(*args: str) -> tuple[str, ...]:
    executable = "npm.cmd" if os.name == "nt" else "npm"
    return (executable, *args)


QUICK_STEPS = (
    Step("frontend-typecheck", npm_command("run", "typecheck:frontend"), CLIENT, 120),
    Step("electron-typecheck", npm_command("run", "typecheck:electron"), CLIENT, 120),
    Step(
        "critical-business-regression",
        (
            sys.executable,
            "-m",
            "unittest",
            "-v",
            "test_version_governance_lifecycle.TestVersionGovernanceLifecycle.test_02_stale_admin_save_conflicts",
            "test_deepseek_batching.TestDeepSeekBatching.test_checkpoint_persistence_and_reuse_on_batch_failure_retry",
            "test_deepseek_batching.TestDeepSeekBatching.test_checkpoint_invalidated_when_model_changes",
            "test_stage4a_eligibility.TestStage4AEligibility.test_02_disabled_deleted_expired_blocked_from_retrieval",
            "test_stage4b_hybrid_retrieval.TestStage4BHybridRetrieval.test_05_late_task_cannot_revive_disabled_content",
        ),
        ROOT / "tests",
        240,
    ),
    Step("m03-consult-regression", (sys.executable, str(ROOT / "tests" / "test_m03_consult.py")), ROOT, 240),
    Step("model-request-diagnostics", (sys.executable, str(ROOT / "tests" / "test_model_errors.py")), ROOT, 120),
    Step("m03-calculator-security", (sys.executable, str(ROOT / "tests" / "test_m03_calculator.py")), ROOT, 120),
)

BUSINESS_TEST_FILES = (
    "test_auth_and_permissions.py",
    "test_documents.py",
    "test_knowledge_atoms.py",
    "test_deepseek_batching.py",
    "test_jev_integration.py",
    "test_source_fidelity_regression.py",
    "test_structured_review_quality.py",
    "test_audit_relief_review.py",
    "test_version_governance_lifecycle.py",
    "test_stage4a_eligibility.py",
    "test_stage4b_hybrid_retrieval.py",
    "test_stage4c_admin_search.py",
    "test_stage4e_benchmark_suite.py",
    "test_stage4e_full_lifecycle.py",
    "test_m03_consult.py",
    "test_model_errors.py",
    "test_m03_calculator.py",
)


def full_steps(evidence_dir: Path) -> tuple[Step, ...]:
    isolated_tests = tuple(
        Step(
            f"business-{Path(filename).stem}",
            (sys.executable, str(ROOT / "tests" / filename)),
            ROOT,
            360,
        )
        for filename in BUSINESS_TEST_FILES
    )
    return (
        Step("production-build", npm_command("run", "build"), CLIENT, 240),
        *isolated_tests,
        Step(
            "critical-ui-flows",
            (sys.executable, str(ROOT / "tests" / "verify_acceptance_ui.py"), "--evidence-dir", str(evidence_dir)),
            ROOT,
            180,
        ),
        Step(
            "electron-startup-lifecycle",
            (sys.executable, str(ROOT / "tests" / "verify_electron_startup.py"), "--evidence-dir", str(evidence_dir)),
            ROOT,
            180,
        ),
    )


def terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.kill()


def run_step(step: Step, log_dir: Path, env: dict[str, str]) -> bool:
    log_path = log_dir / f"{step.name}.log"
    started = time.monotonic()
    print(f"\n[RUN] {step.name} (timeout {step.timeout_seconds}s)", flush=True)
    print(f"      {' '.join(step.command)}", flush=True)

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        log.write(f"cwd: {step.cwd}\ncommand: {' '.join(step.command)}\ntimeout: {step.timeout_seconds}s\n\n")
        log.flush()
        try:
            proc = subprocess.Popen(
                step.command,
                cwd=step.cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
            output, _ = proc.communicate(timeout=step.timeout_seconds)
            if output:
                print(output, end="" if output.endswith("\n") else "\n", flush=True)
                log.write(output)
            return_code = proc.returncode
        except subprocess.TimeoutExpired:
            terminate_process_tree(proc)
            message = f"[TIMEOUT] {step.name} exceeded {step.timeout_seconds}s\n"
            print(message, end="", flush=True)
            log.write(message)
            return False
        except FileNotFoundError as exc:
            message = f"[ENV ERROR] executable not found: {exc}\n"
            print(message, end="", flush=True)
            log.write(message)
            return False

    elapsed = time.monotonic() - started
    status = "PASS" if return_code == 0 else "FAIL"
    print(f"[{status}] {step.name} ({elapsed:.1f}s), log: {log_path}", flush=True)
    return return_code == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="知行有策本地一键验收")
    parser.add_argument("mode", choices=("quick", "full"))
    args = parser.parse_args()

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{args.mode}"
    evidence_dir = ARTIFACT_ROOT / run_id
    evidence_dir.mkdir(parents=True, exist_ok=False)

    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "ZHIXING_ACCEPTANCE": "1",
            "ZHIXING_ACCEPTANCE_EVIDENCE_DIR": str(evidence_dir),
            # 测试中即使代码路径误触真实模型，也会因空密钥而在本地明确失败。
            "DEEPSEEK_API_KEY": "",
        }
    )

    steps = list(QUICK_STEPS)
    if args.mode == "full":
        steps.extend(full_steps(evidence_dir))

    failed: list[str] = []
    summary_path = evidence_dir / "summary.txt"
    for step in steps:
        if not run_step(step, evidence_dir, env):
            failed.append(step.name)
            # 构建失败时 UI 产物不是当前源码，禁止继续跑 UI 验收。
            if step.name == "production-build":
                failed.extend(["critical-ui-flows (not run: current-source build failed)", "electron-startup-lifecycle (not run: current-source build failed)"])
                break

    lines = [
        f"mode={args.mode}",
        f"result={'FAIL' if failed else 'PASS'}",
        f"evidence={evidence_dir}",
        f"failed={'; '.join(failed) if failed else 'none'}",
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if failed:
        print("\n[ACCEPTANCE FAIL] " + "; ".join(failed), flush=True)
        print(f"Evidence retained at: {evidence_dir}", flush=True)
        return 1

    print(f"\n[ACCEPTANCE PASS] {args.mode}", flush=True)
    print(f"Evidence: {evidence_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
