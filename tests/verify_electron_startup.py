"""Electron 启动管理验收，使用独立端口与 user-data-dir。

场景一验证已有外部后端时不创建、不清理该进程；场景二验证启动失败可重试，
快速重复点击只产生一次重试结果。测试不会连接真实业务数据库。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright


ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client"


class StatusHandler(BaseHTTPRequestHandler):
    requests_seen = 0

    def do_GET(self):
        if self.path == "/api/system/status":
            type(self).requests_seen += 1
            payload = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, _format, *_args):
        return


def free_port() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
    port = server.server_port
    server.server_close()
    return port


def electron_binary() -> Path:
    if os.name == "nt":
        return CLIENT / "node_modules" / "electron" / "dist" / "electron.exe"
    candidate = CLIENT / "node_modules" / ".bin" / "electron"
    return candidate


def wait_debug_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
        time.sleep(0.15)
    raise RuntimeError(f"Electron debug port {port} not ready: {last_error}")


def terminate_tree(proc: subprocess.Popen[bytes]) -> None:
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
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def launch(debug_port: int, user_data_dir: Path, backend_url: str, empty_path: Path, log_path: Path):
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(empty_path),
            "ZHIXING_BACKEND_URL": backend_url,
            "ZHIXING_DB_PATH": str(user_data_dir / "must-not-be-created.db"),
            "ZHIXING_STORAGE_DIR": str(user_data_dir / "must-not-be-created-storage"),
            "DEEPSEEK_API_KEY": "",
            "ELECTRON_DISABLE_SECURITY_WARNINGS": "true",
        }
    )
    log_file = log_path.open("wb")
    proc = subprocess.Popen(
        [
            str(electron_binary()),
            ".",
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
        ],
        cwd=CLIENT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    return proc, log_file


def wait_for_page(browser, proc: subprocess.Popen[bytes], predicate, description: str, timeout: float):
    """每轮重新枚举页面，返回正文满足 predicate 的页面。

    Electron 用 loadURL 切换启动页时，CDP 偶发会替换页面目标，旧 Page 对象随之关闭；
    长期持有单个 Page 会把这种替换误报为失败。Electron 进程提前退出则如实报错。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"等待{description}时 Electron 进程已退出，exit code {proc.returncode}")
        for page in [page for context in browser.contexts for page in context.pages]:
            if page.is_closed() or page.url == "about:blank":
                continue
            try:
                text = page.locator("body").inner_text(timeout=500)
            except PlaywrightError:
                continue
            if text.strip() and predicate(text):
                return page
        time.sleep(0.15)
    raise TimeoutError(f"{timeout:.0f}s 内未等到{description}")


def wait_for_text(browser, proc: subprocess.Popen[bytes], text: str, timeout: float):
    return wait_for_page(browser, proc, lambda body: text in body, f"文本「{text}」", timeout)


def run(evidence_dir: Path) -> int:
    binary = electron_binary()
    if not binary.exists():
        print(f"[ENV ERROR] Electron binary missing: {binary}")
        return 2
    if not (CLIENT / "dist" / "index.html").exists() or not (CLIENT / "dist-electron" / "main.js").exists():
        print("[ENV ERROR] current-source production build missing")
        return 2

    evidence_dir.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="zhixing-electron-acceptance-"))
    empty_path = temp_root / "empty-path"
    empty_path.mkdir()
    failures: list[str] = []
    playwright = sync_playwright().start()

    try:
        # 场景一：外部服务已就绪。PATH 中没有 python，仍能进入应用，证明未重复拉起后端。
        StatusHandler.requests_seen = 0
        status_server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
        status_thread = threading.Thread(target=status_server.serve_forever, daemon=True)
        status_thread.start()
        backend_url = f"http://127.0.0.1:{status_server.server_port}"
        debug_port = free_port()
        proc, log_file = launch(
            debug_port,
            temp_root / "external-backend-profile",
            backend_url,
            empty_path,
            evidence_dir / "electron-external-backend.log",
        )
        try:
            wait_debug_port(debug_port)
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
            page = wait_for_page(browser, proc, lambda body: "服务启动中" not in body, "启动页结束", 12)
            if "后端服务启动失败" in page.locator("body").inner_text():
                failures.append("已有后端就绪时 Electron 仍尝试创建后端")
            else:
                print("[PASS] 已有后端时不重复创建后端进程")
            page.screenshot(path=str(evidence_dir / "electron-external-backend.png"))
            browser.close()
        finally:
            terminate_tree(proc)
            log_file.close()

        # 退出 Electron 后外部后端仍应可访问，证明没有误清理由其他进程持有的服务。
        try:
            with urllib.request.urlopen(f"{backend_url}/api/system/status", timeout=2) as response:
                external_alive = response.status == 200
        except Exception:
            external_alive = False
        if external_alive and StatusHandler.requests_seen >= 2:
            print("[PASS] Electron 退出未误清理外部后端")
        else:
            failures.append("Electron 退出误清理或未正确复用外部后端")
        status_server.shutdown()
        status_server.server_close()
        status_thread.join(timeout=3)

        # 场景二：后端不可达且 python 不存在。失败页应可重试，双击只执行一轮重试。
        unavailable_port = free_port()
        debug_port = free_port()
        proc, log_file = launch(
            debug_port,
            temp_root / "retry-profile",
            f"http://127.0.0.1:{unavailable_port}",
            empty_path,
            evidence_dir / "electron-retry.log",
        )
        try:
            wait_debug_port(debug_port)
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
            page = wait_for_text(browser, proc, "后端服务启动失败", 15)
            page.locator("a.retry-btn").evaluate("el => { el.click(); el.click(); }")
            wait_for_text(browser, proc, "知行有策 服务启动中...", 3)
            page = wait_for_text(browser, proc, "后端服务启动失败", 15)
            body_text = page.locator("body").inner_text()
            spawn_error_count = body_text.lower().count("spawn python")
            if spawn_error_count <= 1:
                print("[PASS] 启动失败可重试，快速重复点击未创建重复后端")
            else:
                failures.append(f"一次重试出现 {spawn_error_count} 次后端创建错误，疑似重复启动")
            page.screenshot(path=str(evidence_dir / "electron-retry-failed.png"))
            browser.close()
        finally:
            terminate_tree(proc)
            log_file.close()

        if (temp_root / "retry-profile" / "must-not-be-created.db").exists():
            failures.append("失败启动路径意外创建了业务数据库")
        else:
            print("[PASS] 失败启动与重试未创建业务数据库")
    except Exception as exc:
        failures.append(f"Electron harness exception: {type(exc).__name__}: {exc}")
        print(f"[FAIL] {failures[-1]}")
    finally:
        playwright.stop()
        if failures:
            (evidence_dir / "electron-startup-failures.txt").write_text("\n".join(failures) + "\n", encoding="utf-8")
            # 失败现场（日志和截图）保留，临时 profile 可安全清理，业务证据已复制到 evidence_dir。
        shutil.rmtree(temp_root, ignore_errors=True)

    if failures:
        print(f"[FAIL] Electron startup acceptance: {len(failures)} issue(s)")
        return 1
    print("[PASS] Electron startup acceptance")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    return run(args.evidence_dir)


if __name__ == "__main__":
    raise SystemExit(main())
