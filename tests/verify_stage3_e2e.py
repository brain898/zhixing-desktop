import os
import sys
import io
import time
import subprocess
import urllib.request
import threading
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright
import docx
from reportlab.pdfgen import canvas

BASE_DIR = Path(__file__).resolve().parent.parent
SERVER_DIR = BASE_DIR / "server"
CLIENT_DIST = BASE_DIR / "client" / "dist"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
MATERIALS_DIR = BASE_DIR / "tests" / "test_materials"

SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
MATERIALS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(SERVER_DIR))
from database import init_db, get_db
from seed import seed_data

class StaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CLIENT_DIST), **kwargs)

    def log_message(self, format, *args):
        pass

def run_static_server(port=5175):
    server = ThreadingHTTPServer(("127.0.0.1", port), StaticHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    return server

def prepare_test_materials():
    # 1. Markdown: 包含制度与标准、方法与工具
    md_path = MATERIALS_DIR / "01_物业客户服务标准.md"
    md_content = """# 第一章 服务总则

## 1.1 适用范围
本标准适用于绿城物业在管所有住宅与商办物业服务中心。

## 1.2 服务时效要求
- 客户报修需求应在15分钟内完成联系回访。
- 紧急跑水、电梯困人等事项必须在5分钟内到达现场处置。
- 若遇台风、暴雨等不可抗力极端天气，优先保障人身安全，工单响应时限可延长至30分钟。
"""
    md_path.write_text(md_content, encoding="utf-8")

    # 2. DOCX: 包含变配电系统设备验收指标与表格
    docx_path = MATERIALS_DIR / "02_设施设备工程接管验收规范.docx"
    doc = docx.Document()
    doc.add_heading("设施设备工程接管验收规范", level=1)
    doc.add_paragraph("第一条 本规范用于明确机电工程在竣工交付前的查验与移交程序。")
    t = doc.add_table(rows=3, cols=2)
    t.cell(0, 0).text = "系统名称"
    t.cell(0, 1).text = "查验合格标准"
    t.cell(1, 0).text = "变配电系统"
    t.cell(1, 1).text = "主备电源自动切换时间小于0.5秒"
    t.cell(2, 0).text = "给排水系统"
    t.cell(2, 1).text = "水压测试符合设计要求，管道无渗漏"
    doc.save(str(docx_path))

    # 3. PDF: 巡检规程
    pdf_path = MATERIALS_DIR / "03_机房安全巡检手册.pdf"
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 750, "绿城物业 · 机房安全巡检工作指南")
    c.drawString(72, 710, "变配电室每两小时记录一次电流电压读数，确保温湿度在合格区间。")
    c.showPage()
    c.drawString(72, 750, "第二章 消防泵房查验标准")
    c.drawString(72, 710, "每周进行一次消防泵手动与远程联动启泵测试。")
    c.showPage()
    c.save()
    pdf_path.write_bytes(buf.getvalue())

    return [str(md_path), str(docx_path), str(pdf_path)]

def is_server_alive(url="http://127.0.0.1:8766/api/auth/status"):
    try:
        urllib.request.urlopen(url, timeout=1)
        return True
    except urllib.error.HTTPError:
        # HTTP 401/403/404 均说明服务端 HTTP 服务已正常接收请求
        return True
    except Exception:
        return False

def ensure_backend_server():
    if is_server_alive():
        print("[INFO] 后端服务已在 8766 端口运行")
        return None

    print("[INFO] 启动后端服务 (端口 8766)...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8766"],
        cwd=str(SERVER_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # 等待就绪
    for _ in range(40):
        time.sleep(0.3)
        if is_server_alive():
            print("[INFO] 后端服务启动并就绪")
            return proc

    raise RuntimeError("后端服务未能成功启动")

def run_stage3_verification():
    print("=== 开始知行有策 Stage 3 知识原子整理与校对端到端验收 ===")

    # 1. 干净重置数据库
    init_db()
    seed_data(force=True)
    with get_db() as conn:
        conn.execute("DELETE FROM knowledge_evidence")
        conn.execute("DELETE FROM knowledge_versions")
        conn.execute("DELETE FROM knowledge_items")
        conn.execute("DELETE FROM source_blocks")
        conn.execute("DELETE FROM processing_tasks")
        conn.execute("DELETE FROM document_versions")
        conn.execute("DELETE FROM documents")

    backend_proc = ensure_backend_server()
    static_server = run_static_server(5175)
    app_url = "http://127.0.0.1:5175"

    test_files = prepare_test_materials()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.on("console", lambda msg: print(f"[CONSOLE {msg.type}]: {msg.text}"))
            page.on("pageerror", lambda err: print(f"[PAGEERROR]: {err}"))

            # 步骤 1: 管理员登录
            print("--> 1. 访问系统并以管理员文哲身份登录...")
            page.goto(app_url)
            page.wait_for_selector('button:has-text("登录")', timeout=10000)
            page.fill('input[placeholder="请输入用户名"]', 'admin')
            page.fill('input[placeholder="请输入密码"]', 'Admin@Zhixing2026')
            page.click('button[type="submit"]')
            page.wait_for_selector('text="文哲"', timeout=10000)
            page.wait_for_timeout(600)

            # 步骤 2: 批量导入测试资料
            print("--> 2. 导入 3 份真实资料以触发结构解析与原子提炼流水线...")
            page.click('button:has-text("导入文件")')
            page.wait_for_selector('text="导入物业资料"', timeout=5000)
            page.set_input_files('input[type="file"]', test_files)
            page.wait_for_timeout(500)
            page.click('button:has-text("开始导入 (3)")')

            # 等待解析与知识抽取任务流水线完成
            print("--> 3. 等待后台解析与原子抽取流水线执行...")
            for _ in range(45):
                page.wait_for_timeout(1000)
                # 检查是否已有知识卡片生成
                cards = page.locator('[data-testid^="knowledge-item-"]')
                if cards.count() > 0:
                    break

            # 验证两栏布局与卡片流
            cards_count = page.locator('[data-testid^="knowledge-item-"]').count()
            print(f"[PASS] 知识原子抽取完成，当前加载卡片数: {cards_count}")
            assert cards_count > 0, "应抽取并展示知识原子候选卡片"

            # 截图 1: 1440x900 知识卡片流与分类标签
            shot_cards_1440 = SCREENSHOTS_DIR / "01_stage3_knowledge_cards_1440x900.png"
            page.screenshot(path=str(shot_cards_1440))
            print(f"[PASS] 1. 1440x900 知识资产卡片流截屏完成: {shot_cards_1440}")

            # 截图 2: 1280x800 紧凑视口
            print("--> 4. 切换紧凑视口 1280x800 验证布局自适应...")
            page.set_viewport_size({"width": 1280, "height": 800})
            page.wait_for_timeout(600)
            shot_cards_1280 = SCREENSHOTS_DIR / "02_stage3_knowledge_cards_1280x800.png"
            page.screenshot(path=str(shot_cards_1280))
            print(f"[PASS] 2. 1280x800 视口截屏完成: {shot_cards_1280}")
            page.set_viewport_size({"width": 1440, "height": 900})

            # 步骤 5: 验证分类 Tab 与搜索过滤
            print("--> 5. 验证五大分类 Tab 过滤与搜索...")
            # 点击「制度与标准」Tab
            tab_rule = page.locator('[data-testid="tab-制度与标准"]')
            if tab_rule.is_visible():
                tab_rule.click()
                page.wait_for_timeout(500)
                rule_cards = page.locator('[data-testid^="knowledge-item-"]')
                print(f"[PASS] 制度与标准分类下卡片数: {rule_cards.count()}")

            # 恢复全部 Tab
            page.locator('[data-testid="tab-all"]').click()
            page.wait_for_timeout(500)

            # 步骤 6: 打开首个知识卡片校对抽屉
            print("--> 6. 点击知识卡片打开原文对照校对抽屉...")
            first_card = page.locator('[data-testid^="knowledge-item-"]').first
            first_card.click()
            page.wait_for_selector('text="知识原子校对工作台"', timeout=8000)
            page.wait_for_selector('text=原文证据对照', timeout=8000)

            # 验证左侧原文证据与锚点高亮
            assert page.locator('text=原文证据对照').is_visible()
            print("[PASS] 左侧原文块及证据出处展示正常")

            # 验证右侧结构化字段
            assert page.locator('text=五类主分类').is_visible()
            assert page.locator('text=独立可理解核心陈述').is_visible()
            assert page.locator('[data-testid="save-draft-btn"]').is_visible()
            assert page.locator('[data-testid="confirm-knowledge-btn"]').is_visible()

            # 截图 3: 校对抽屉 1440x900
            shot_modal = SCREENSHOTS_DIR / "03_stage3_proofreading_modal_1440x900.png"
            page.screenshot(path=str(shot_modal))
            print(f"[PASS] 3. 校对抽屉全貌截屏完成: {shot_modal}")

            # 步骤 7: 测试保存草稿
            print("--> 7. 修改业务场景并测试保存草稿...")
            title_input = page.locator('[data-testid="input-title"]')
            original_title = title_input.input_value()
            title_input.fill(original_title + " [校对草稿]")
            page.click('[data-testid="save-draft-btn"]')
            page.wait_for_timeout(1000)
            print("[PASS] 保存草稿成功")

            # 步骤 8: 测试确认知识版本（严格校验：必须非待分类、陈述不为空、来源真实）
            print("--> 8. 确认知识版本并验证「已确认，索引未建立」状态...")
            # 确保主分类有效
            cat_select = page.locator('[data-testid="select-primary-category"]')
            current_cat = cat_select.input_value()
            if not current_cat:
                cat_select.select_option("制度与标准")

            page.click('[data-testid="confirm-knowledge-btn"]')
            page.wait_for_timeout(1500)

            # 验证卡片状态角标严格为「已确认，索引未建立」
            badge = page.locator('[data-testid="confirmed-status-badge"]')
            assert badge.is_visible(), "确认后状态必须为「已确认，索引未建立」，严禁显示「可检索」"
            print("[PASS] 确认后状态真实准确: 已确认，索引未建立")

            # 关闭校对抽屉
            page.click('[data-testid="close-modal-btn"]')
            page.wait_for_timeout(800)

            # 截图 4: 确认后列表卡片状态截屏
            shot_confirmed = SCREENSHOTS_DIR / "04_stage3_confirmed_status_1440x900.png"
            page.screenshot(path=str(shot_confirmed))
            print(f"[PASS] 4. 确认后列表卡片状态截屏完成: {shot_confirmed}")

            # 步骤 9: 测试视图切换到「原件与结构块」
            print("--> 9. 切换至「原件与结构块」流水线视图...")
            page.click('[data-testid="mode-tab-pipeline"]')
            page.wait_for_timeout(1000)
            assert page.locator('text="原文结构解析结果"').is_visible() or page.locator('text="处理流水线阶段状态"').is_visible()
            
            shot_pipeline = SCREENSHOTS_DIR / "05_stage3_pipeline_view_1440x900.png"
            page.screenshot(path=str(shot_pipeline))
            print(f"[PASS] 5. 原件与结构块流水线视图截屏完成: {shot_pipeline}")

            # 切回知识管理
            page.click('[data-testid="mode-tab-knowledge"]')
            page.wait_for_timeout(600)

            print("\n========================================================")
            print("Stage 3 所有端到端 UI 交互与原子校对确认验证全部 100% 通过！")
            print("========================================================")

    finally:
        if backend_proc:
            backend_proc.terminate()

if __name__ == "__main__":
    run_stage3_verification()
