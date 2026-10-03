"""当前 production build 的关键 UI 业务验收。

复用 verify_item_switch_smoothness.py 中的业务夹具，只补充验收所需的延迟、
写请求与删除请求记录。所有网络均由浏览器内 mock 响应，不连接真实后端。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from verify_item_switch_smoothness import MOCK_SCRIPT


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "client" / "dist"


class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def log_message(self, _format, *_args):
        return


ACCEPTANCE_EXTENSION = r"""
window.__acceptanceCalls = [];
window.__detailDelays = {};
window.__mockDocumentDeleted = false;
const acceptanceBaseFetch = window.fetch;
window.fetch = async function(input, init) {
  const url = typeof input === 'string' ? input : input.url;
  const method = ((init && init.method) || 'GET').toUpperCase();
  const body = init && init.body ? JSON.parse(init.body) : null;
  const path = new URL(url, window.location.href).pathname;
  window.__acceptanceCalls.push({url, path, method, body});

  const detailMatch = path.match(/^\/api\/knowledge\/items\/(ki-[123])$/);
  if (detailMatch) {
    const delay = Number(window.__detailDelays[detailMatch[1]] || 0);
    if (delay) await new Promise(resolve => setTimeout(resolve, delay));
    return acceptanceBaseFetch(input, init);
  }

  if (path.match(/^\/api\/knowledge\/items\/ki-[123]\/draft\/validate$/) && method === 'POST') {
    await new Promise(resolve => setTimeout(resolve, 35));
    return new Response(JSON.stringify({
      quality_flags: [], blocking: [], can_confirm: true,
      revision_token: body.revision_token
    }), {status: 200, headers: {'Content-Type': 'application/json'}});
  }

  const draftMatch = path.match(/^\/api\/knowledge\/items\/(ki-[123])\/draft$/);
  if (draftMatch && method === 'PUT') {
    await new Promise(resolve => setTimeout(resolve, 80));
    return new Response(JSON.stringify({
      message: '草稿已保存', revision_token: 'rev-saved-' + draftMatch[1], quality_flags: []
    }), {status: 200, headers: {'Content-Type': 'application/json'}});
  }

  const confirmMatch = path.match(/^\/api\/knowledge\/items\/(ki-[123])\/confirm$/);
  if (confirmMatch && method === 'POST') {
    await new Promise(resolve => setTimeout(resolve, 80));
    return new Response(JSON.stringify({
      message: '知识条目已确认启用', review_status: 'confirmed', index_status: 'indexing',
      revision_token: 'rev-confirmed-' + confirmMatch[1], index_task_id: 'idx-1', index_task_status: 'queued'
    }), {status: 200, headers: {'Content-Type': 'application/json'}});
  }

  if (path === '/api/documents/doc1' && method === 'GET') {
    return new Response(JSON.stringify({
      id: 'doc1', title: '物业服务品质红线标准', active_version_id: 'docv1',
      created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T01:00:00Z',
      versions: [{
        id: 'docv1', version_label: 'v1', file_name: '物业服务品质红线标准.docx',
        file_type: 'docx', file_size: 2400, uploaded_at: '2026-09-18T01:00:00Z',
        processing_status: 'completed', error_summary: null, task_id: null,
        task_status: 'completed', attempt_count: 1, block_count: 5
      }]
    }), {status: 200, headers: {'Content-Type': 'application/json'}});
  }

  if (path.includes('/source-blocks')) {
    return new Response(JSON.stringify([]), {status: 200, headers: {'Content-Type': 'application/json'}});
  }

  if (path === '/api/documents/doc1/deletion-impact' && method === 'GET') {
    return new Response(JSON.stringify({
      document_id: 'doc1', title: '物业服务品质红线标准', version_count: 1,
      derived_knowledge_count: 3, retrieval_record_count: 2, active_task_count: 0,
      related_case_reference_count: 0, skill_reference_count: 0
    }), {status: 200, headers: {'Content-Type': 'application/json'}});
  }

  if (path === '/api/documents/doc1' && method === 'DELETE') {
    await new Promise(resolve => setTimeout(resolve, 180));
    window.__mockDocumentDeleted = true;
    return new Response(JSON.stringify({message: '资料已删除', document_id: 'doc1'}), {
      status: 200, headers: {'Content-Type': 'application/json'}
    });
  }

  if (path === '/api/documents' && method === 'GET' && window.__mockDocumentDeleted) {
    return new Response(JSON.stringify([]), {status: 200, headers: {'Content-Type': 'application/json'}});
  }

  return acceptanceBaseFetch(input, init);
};
"""


def calls(page, *, method: str | None = None, suffix: str | None = None):
    result = page.evaluate("window.__acceptanceCalls")
    if method:
        result = [item for item in result if item["method"] == method]
    if suffix:
        result = [item for item in result if item["path"].endswith(suffix)]
    return result


def check(condition: bool, message: str, failures: list[str]) -> None:
    if condition:
        print(f"[PASS] {message}")
    else:
        failures.append(message)
        print(f"[FAIL] {message}")


def run(evidence_dir: Path) -> int:
    if not (DIST / "index.html").exists():
        print(f"[ENV ERROR] current-source build missing: {DIST / 'index.html'}")
        return 2

    evidence_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), StaticHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    failures: list[str] = []
    page = None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.add_init_script(MOCK_SCRIPT + ACCEPTANCE_EXTENSION)
            page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            page.wait_for_selector('[data-testid="knowledge-item-ki-1"]', timeout=12_000)

            # 乱序详情：ki-2 慢、ki-3 快，最终必须只呈现最后选择的条目。
            page.locator('[data-testid="knowledge-item-ki-1"]').click()
            page.wait_for_selector('[data-testid="confirm-knowledge-btn"]', timeout=8_000)
            page.wait_for_selector('[data-testid="jev-quality-panel"]', timeout=8_000)
            check(page.locator('[data-testid="jev-quality-details"]').count() == 0,
                  'Jev 质检明细默认折叠，只显示摘要', failures)
            page.locator('[data-testid="jev-quality-toggle"]').last.click()
            page.wait_for_selector('[data-testid="jev-quality-details"]', timeout=3_000)
            jev_text = page.locator('[data-testid="jev-quality-panel"]').inner_text()
            check('疑似有问题' in jev_text and '选项概率' in jev_text and '分布置信度' in jev_text,
                  'Jev 质检面板区分问题状态、选项概率与置信度', failures)
            check('该建议不会覆盖人工分类' in jev_text and '阈值尚未经过业务样本校准' in jev_text,
                  'Jev 面板明确人工分类边界与未校准阈值', failures)
            page.evaluate("window.__detailDelays = {'ki-2': 320, 'ki-3': 20}")
            page.locator('button[title^="下一条"]').click()
            page.wait_for_selector('text="第 2 / 3 条"', timeout=2_000)
            page.locator('button[title^="下一条"]').click()
            page.wait_for_selector('text="第 3 / 3 条"', timeout=2_000)
            page.wait_for_timeout(450)
            check(page.locator('text="变配电室每两小时测温巡检"').first.is_visible(), "乱序详情响应不覆盖最后选择条目", failures)
            proofreading_panel = page.locator('[data-testid="close-modal-btn"]').locator(
                'xpath=ancestor::div[contains(@class,"modal-panel-animate")]'
            ).first
            check(proofreading_panel.locator('text="电梯困人救援30分钟到达"').count() == 0, "乱序响应不残留前一条操作目标", failures)

            # 回到 ki-1，修改后确认。必须先 PUT 当前草稿，再用返回的新版本令牌 POST 确认，随后进入 ki-2。
            page.evaluate("window.__detailDelays = {}")
            page.locator('button[title^="上一条"]').click()
            page.wait_for_selector('text="第 2 / 3 条"', timeout=2_000)
            page.locator('button[title^="上一条"]').click()
            page.wait_for_selector('text="第 1 / 3 条"', timeout=2_000)
            page.wait_for_selector('[data-testid="confirm-knowledge-btn"]:not([disabled])', timeout=5_000)
            page.get_by_role("button", name="修改").first.click()
            title_input = page.locator('[data-testid="input-title"]')
            title_input.fill("人工修改后的跑水处置标题")
            page.wait_for_selector('[data-testid="draft-validation-status"]', timeout=2_000)

            # 在弹窗保留未保存编辑时触发父列表重新读取，列表响应不得覆盖表单内容。
            list_reads_before = len([
                item for item in calls(page, method="GET") if item["path"] == "/api/knowledge/items"
            ])
            page.locator('[data-testid="doc-item-物业服务品质红线标准"]').evaluate("el => el.click()")
            page.wait_for_function(
                "before => window.__acceptanceCalls.filter(x => x.method === 'GET' && x.path === '/api/knowledge/items').length > before",
                arg=list_reads_before,
                timeout=4_000,
            )
            check(title_input.input_value() == "人工修改后的跑水处置标题", "后台列表刷新不覆盖未保存修改", failures)

            page.wait_for_function(
                "() => document.querySelector('[data-testid=confirm-knowledge-btn]') && !document.querySelector('[data-testid=confirm-knowledge-btn]').disabled",
                timeout=4_000,
            )
            page.locator('[data-testid="confirm-knowledge-btn"]').click()
            page.wait_for_selector('text="第 2 / 3 条"', timeout=5_000)

            draft_calls = calls(page, method="PUT", suffix="/ki-1/draft")
            confirm_calls = calls(page, method="POST", suffix="/ki-1/confirm")
            wrong_target_writes = [
                item for item in calls(page)
                if item["method"] in ("PUT", "POST")
                and (item["path"].endswith("/draft") or item["path"].endswith("/confirm"))
                and "/ki-1/" not in item["path"]
            ]
            check(len(draft_calls) == 1, "确认修改只保存一次当前条目草稿", failures)
            check(bool(draft_calls) and draft_calls[0]["body"]["revision_token"] == "rev-1", "保存使用当前条目的原版本令牌", failures)
            check(len(confirm_calls) == 1, "确认请求只提交一次且与读取请求区分", failures)
            check(bool(confirm_calls) and confirm_calls[0]["body"]["revision_token"] == "rev-saved-ki-1", "确认使用保存返回的新版本令牌", failures)
            check(not wrong_target_writes, "保存、确认和自动下一条未写入错位目标", failures)
            check(page.locator('text="电梯困人救援30分钟到达"').first.is_visible(), "确认成功后自动进入下一条", failures)

            # 进入批量管理删除确认。取消不能发送 DELETE，确认快速双击也只能发送一次。
            page.keyboard.press("Escape")
            page.wait_for_selector('[data-testid="close-modal-btn"]', state="detached", timeout=3_000)
            page.get_by_role("button", name="管理", exact=True).click()
            page.locator('[data-testid="doc-item-物业服务品质红线标准"]').click()
            page.get_by_role("button", name="删除 (1)").click()
            page.get_by_role("dialog", name="删除所选文件？").wait_for(timeout=5_000)

            # 键盘焦点必须被困在顶层确认弹窗内，且确认弹窗应有 dialog 语义。
            dialog = page.locator('[role="dialog"][aria-modal="true"]')
            has_dialog = dialog.count() == 1
            focus_inside = has_dialog
            if has_dialog:
                for _ in range(6):
                    page.keyboard.press("Tab")
                    focus_inside = focus_inside and page.evaluate(
                        "() => document.querySelector('[role=dialog][aria-modal=true]').contains(document.activeElement)"
                    )
            check(has_dialog and focus_inside, "确认弹窗键盘焦点不进入背景", failures)

            dialog.get_by_role("button", name="取消").click()
            dialog.wait_for(state="detached", timeout=3_000)
            check(len(calls(page, method="DELETE", suffix="/documents/doc1")) == 0, "删除取消不发送删除请求", failures)

            page.get_by_role("button", name="删除 (1)").click()
            dialog.wait_for(timeout=5_000)
            dialog.get_by_role("button", name="删除文件").evaluate("el => { el.click(); el.click(); }")
            page.wait_for_timeout(450)
            delete_calls = calls(page, method="DELETE", suffix="/documents/doc1")
            check(len(delete_calls) == 1, "删除确认快速重复操作只提交一次", failures)
            check(page.locator('[data-testid="doc-item-物业服务品质红线标准"]').count() == 0, "删除成功后资料从业务列表移除", failures)

            page.screenshot(path=str(evidence_dir / "critical-ui-final.png"), full_page=True)
            (evidence_dir / "critical-ui-requests.json").write_text(
                json.dumps(calls(page), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            browser.close()
    except Exception as exc:
        failures.append(f"UI harness exception: {type(exc).__name__}: {exc}")
        print(f"[FAIL] {failures[-1]}")
        if page is not None:
            try:
                page.screenshot(path=str(evidence_dir / "critical-ui-exception.png"), full_page=True)
                (evidence_dir / "critical-ui-requests.json").write_text(
                    json.dumps(calls(page), ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    if failures:
        (evidence_dir / "critical-ui-failures.txt").write_text("\n".join(failures) + "\n", encoding="utf-8")
        print(f"[FAIL] critical UI acceptance: {len(failures)} issue(s)")
        return 1
    print("[PASS] critical UI acceptance")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    return run(args.evidence_dir)


if __name__ == "__main__":
    raise SystemExit(main())
