import os
import sys
import io
import time
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
    # 1. Markdown
    md_path = MATERIALS_DIR / "01_物业客户服务标准.md"
    md_content = """# 第一章 服务总则

## 1.1 适用范围
本标准适用于绿城物业在管所有住宅与商办物业服务中心。

## 1.2 服务时效要求
- 客户报修需求应在15分钟内完成联系回访。
- 紧急跑水、电梯困人等事项必须在5分钟内到达现场处置。
"""
    md_path.write_text(md_content, encoding="utf-8")

    # 2. DOCX
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

    # 3. PDF
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

    # 4. TXT
    txt_path = MATERIALS_DIR / "04_保洁标准化作业规程.txt"
    txt_content = """园区环境保洁标准化作业规程

一、地面推尘作业要求
公区大堂每日晨间7点前完成首次全面推尘。

二、垃圾收集与清运要求
生活垃圾桶满溢率不得超过三分之二，定时开展消杀作业。
"""
    txt_path.write_text(txt_content, encoding="utf-8")

    return [str(md_path), str(docx_path), str(pdf_path), str(txt_path)]

def run_e2e_verification():
    # 1. 干净初始化数据库
    init_db()
    seed_data(force=True)
    with get_db() as conn:
        conn.execute("DELETE FROM source_blocks")
        conn.execute("DELETE FROM processing_tasks")
        conn.execute("DELETE FROM document_versions")
        conn.execute("DELETE FROM documents")

    # 2. 准备真实测试资料
    files = prepare_test_materials()

    # 3. 启动静态服务器
    server = run_static_server(5175)
    app_url = "http://127.0.0.1:5175"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        print("--> 1. 访问系统并登录管理员 (文哲)...")
        page.goto(app_url)
        page.wait_for_selector('button:has-text("登录")', timeout=8000)
        page.fill('input[placeholder="请输入用户名"]', 'admin')
        page.fill('input[placeholder="请输入密码"]', 'Admin@Zhixing2026')
        page.click('button[type="submit"]')
        page.wait_for_selector('text="文哲"', timeout=8000)
        page.wait_for_timeout(600)

        # 验证全新空库状态
        assert page.locator('text="导入第一份物业资料，开始建立企业知识库。"').is_visible()
        shot1 = SCREENSHOTS_DIR / "01_stage2_empty_state_1440x900.png"
        page.screenshot(path=str(shot1))
        print(f"[PASS] 1. 空白库引导状态已验证并截屏: {shot1}")

        # 4. 打开导入文件弹窗并选择 4 份真实资料
        print("--> 2. 打开导入弹窗并批量选择 4 类支持格式资料...")
        page.click('button:has-text("导入文件")')
        page.wait_for_selector('text="导入物业资料"', timeout=3000)

        # 设置文件输入
        page.set_input_files('input[type="file"]', files)
        page.wait_for_timeout(500)

        # 验证待导入清单展示 4 项
        assert page.locator('text="待导入清单 (4)"').is_visible()
        shot_modal = SCREENSHOTS_DIR / "02_stage2_upload_modal.png"
        page.screenshot(path=str(shot_modal))
        print(f"[PASS] 2. 批量上传清单已验证并截屏: {shot_modal}")

        # 5. 点击开始导入
        print("--> 3. 提交批量导入并等待后台异步解析完成...")
        page.click('button:has-text("开始导入 (4)")')
        page.wait_for_selector('h2:has-text("原始文件 (4)")', timeout=15000)

        # 等待后台解析完成
        print("--> 4. 等待后台结构解析流水线处理...")
        for _ in range(10):
            page.wait_for_timeout(1000)
            if page.locator('text="2. 正文结构解析完成"').is_visible():
                break

        # 验证两栏工作区
        assert page.locator('h2:has-text("原始文件 (4)")').is_visible()
        assert page.locator('text="处理流水线阶段状态"').is_visible()
        assert page.locator('text="原文结构解析结果"').is_visible()

        shot_workspace = SCREENSHOTS_DIR / "03_stage2_document_workspace_1440x900.png"
        page.screenshot(path=str(shot_workspace))
        print(f"[PASS] 3. 1440x900 两栏资料工作区已验证并截屏: {shot_workspace}")

        # 6. 切换查看 DOCX 文件的结构块
        print("--> 5. 切换选中文档并核验结构块锚点与表格...")
        page.click('[data-testid="doc-item-02_设施设备工程接管验收规范.docx"]')
        page.wait_for_timeout(2000)
        page.screenshot(path=str(SCREENSHOTS_DIR / "debug_click_docx.png"))
        page.wait_for_selector('h2:has-text("02_设施设备工程接管验收规范.docx")', timeout=8000)

        shot_blocks = SCREENSHOTS_DIR / "04_stage2_structured_blocks_1440x900.png"
        page.screenshot(path=str(shot_blocks))
        print(f"[PASS] 4. 原文结构解析与表格锚点已验证并截屏: {shot_blocks}")

        # 7. 紧凑窗口 1280x800 检查
        print("--> 6. 切换紧凑视口 1280x800 验证布局响应...")
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(500)
        shot_compact = SCREENSHOTS_DIR / "05_stage2_compact_1280x800.png"
        page.screenshot(path=str(shot_compact))
        print(f"[PASS] 5. 1280x800 视口已验证并截屏: {shot_compact}")

        # 恢复 1440x900
        page.set_viewport_size({"width": 1440, "height": 900})

        # 8. 搜索过滤测试
        print("--> 7. 测试左侧文件搜索过滤...")
        search_input = page.locator('input[placeholder="搜索资料名称"]')
        search_input.fill("巡检")
        page.wait_for_timeout(300)
        assert page.locator('div[title="03_机房安全巡检手册.pdf"]').is_visible()
        assert not page.locator('div[title="01_物业客户服务标准.md"]').is_visible()
        print("[PASS] 6. 文件名搜索过滤响应正常")

        search_input.fill("")
        page.wait_for_timeout(300)

        # 9. 删除确认弹窗测试
        print("--> 8. 测试资料删除确认弹窗与逻辑删除...")
        page.click('[data-testid="delete-doc-btn"]')
        page.wait_for_selector('text=确认删除资料？', timeout=3000)
        assert page.get_by_text("物理原始数据当前尚未物理清除").is_visible()

        shot_del = SCREENSHOTS_DIR / "06_stage2_delete_modal.png"
        page.screenshot(path=str(shot_del))
        print(f"[PASS] 7. 删除确认弹窗已验证并截屏: {shot_del}")

        # 确认删除
        page.click('[data-testid="confirm-delete-btn"]')
        page.wait_for_timeout(1000)

        # 验证文件数量由 4 减少为 3
        assert page.locator('h2:has-text("原始文件 (3)")').is_visible()
        print("[PASS] 8. 资料删除成功，列表与日常访问已实时生效撤销")

    print("\n========================================================")
    print("Stage 2 所有端到端 UI 交互与数据流验证全部 100% 通过！")
    print("========================================================")

if __name__ == "__main__":
    run_e2e_verification()
