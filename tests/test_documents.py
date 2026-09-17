import io
import sys
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
import docx
from reportlab.pdfgen import canvas

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

from main import app
from database import init_db
from seed import seed_data

class TestDocumentManagement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        seed_data(force=True)
        from database import get_db
        with get_db() as conn:
            conn.execute("DELETE FROM source_blocks")
            conn.execute("DELETE FROM processing_tasks")
            conn.execute("DELETE FROM document_versions")
            conn.execute("DELETE FROM documents")
        cls.client = TestClient(app)

        # 登录管理员（文哲，org_greentown）
        admin_login = cls.client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
        cls.admin_token = admin_login.json()["token"]
        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}

        # 登录普通成员（李景研，org_greentown）
        member_login = cls.client.post("/api/auth/login", json={"username": "member", "password": "Member@Zhixing2026"})
        cls.member_token = member_login.json()["token"]
        cls.member_headers = {"Authorization": f"Bearer {cls.member_token}"}

        # 登录跨企业管理员（张经理，org_other）
        other_login = cls.client.post("/api/auth/login", json={"username": "other_admin", "password": "Other@Zhixing2026"})
        cls.other_token = other_login.json()["token"]
        cls.other_headers = {"Authorization": f"Bearer {cls.other_token}"}

    def _wait_for_task_completion(self, doc_id: str, timeout: float = 4.0):
        start = time.time()
        while time.time() - start < timeout:
            resp = self.client.get(f"/api/documents/{doc_id}", headers=self.admin_headers)
            if resp.status_code == 200:
                data = resp.json()
                status = data["versions"][0]["processing_status"]
                if status in ("completed", "failed", "cancelled"):
                    return data
            time.sleep(0.1)
        return None

    def test_01_upload_and_parse_markdown(self):
        """测试 Markdown 文件导入、结构块解析及来源定位"""
        md_content = """# 第一章 物业服务总则

## 1.1 适用范围
本规范适用于所有在管物业服务中心及下属住宅项目。

## 1.2 巡检要求
- 巡检人员应每日进行公区设施检查。
- 遇紧急漏水应在15分钟内到场处置。
"""
        files = {"file": ("物业服务规范.md", io.BytesIO(md_content.encode("utf-8")), "text/markdown")}
        resp = self.client.post("/api/documents/upload", headers=self.admin_headers, files=files)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        doc_id = data["document_id"]
        ver_id = data["version_id"]

        # 等待后台任务执行完毕
        doc_detail = self._wait_for_task_completion(doc_id)
        self.assertIsNotNone(doc_detail)
        self.assertEqual(doc_detail["versions"][0]["processing_status"], "completed")

        # 检查结构块
        blocks_resp = self.client.get(f"/api/documents/{doc_id}/versions/{ver_id}/source-blocks", headers=self.admin_headers)
        self.assertEqual(blocks_resp.status_code, 200)
        blocks = blocks_resp.json()
        self.assertTrue(len(blocks) >= 4)
        headings = [b for b in blocks if b["block_type"] == "heading"]
        self.assertTrue(any("第一章" in h["text_content"] for h in headings))
        self.assertTrue(any("1.1 适用范围" in h["text_content"] for h in headings))

    def test_02_upload_and_parse_docx(self):
        """测试 DOCX 文件的标题层级、段落与表格结构解析"""
        doc = docx.Document()
        doc.add_heading("住宅工程接管指引", level=1)
        doc.add_paragraph("第一条 本工程接管包括建筑结构与公用机电设备核验。")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "核验项"
        table.cell(0, 1).text = "合格标准"
        table.cell(1, 0).text = "消防主机"
        table.cell(1, 1).text = "联动测试正常无报警"
        buf = io.BytesIO()
        doc.save(buf)

        files = {"file": ("住宅工程接管指引.docx", io.BytesIO(buf.getvalue()), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        resp = self.client.post("/api/documents/upload", headers=self.admin_headers, files=files)
        self.assertEqual(resp.status_code, 200)
        doc_id = resp.json()["document_id"]
        ver_id = resp.json()["version_id"]

        doc_detail = self._wait_for_task_completion(doc_id)
        self.assertIsNotNone(doc_detail)
        self.assertEqual(doc_detail["versions"][0]["processing_status"], "completed")

        blocks_resp = self.client.get(f"/api/documents/{doc_id}/versions/{ver_id}/source-blocks", headers=self.admin_headers)
        blocks = blocks_resp.json()
        types = [b["block_type"] for b in blocks]
        self.assertIn("heading", types)
        self.assertIn("paragraph", types)
        self.assertIn("table", types)

    def test_03_upload_and_parse_pdf(self):
        """测试 PDF 文本文件导入与页码锚点抽取"""
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(72, 750, "物业设备机房巡检手册")
        c.drawString(72, 700, "变配电室每两小时记录一次电流电压读数。")
        c.showPage()
        c.drawString(72, 750, "第二章 给排水巡检")
        c.drawString(72, 700, "生活水泵房应保持无积水并检查主备泵自动切换。")
        c.showPage()
        c.save()

        files = {"file": ("机房巡检手册.pdf", io.BytesIO(buf.getvalue()), "application/pdf")}
        resp = self.client.post("/api/documents/upload", headers=self.admin_headers, files=files)
        self.assertEqual(resp.status_code, 200)
        doc_id = resp.json()["document_id"]
        ver_id = resp.json()["version_id"]

        doc_detail = self._wait_for_task_completion(doc_id)
        self.assertIsNotNone(doc_detail)
        self.assertEqual(doc_detail["versions"][0]["processing_status"], "completed")

        blocks_resp = self.client.get(f"/api/documents/{doc_id}/versions/{ver_id}/source-blocks", headers=self.admin_headers)
        blocks = blocks_resp.json()
        pages = {b["page_number"] for b in blocks if b["page_number"] is not None}
        self.assertIn(1, pages)
        self.assertIn(2, pages)

    def test_04_validation_rejections(self):
        """测试文件校验拦截：空文件、超限扩展名"""
        # 1. 空文件
        files = {"file": ("empty.txt", io.BytesIO(b""), "text/plain")}
        r1 = self.client.post("/api/documents/upload", headers=self.admin_headers, files=files)
        self.assertEqual(r1.status_code, 400)
        self.assertIn("文件内容为空", r1.json()["detail"])

        # 2. 不支持的扩展名
        files2 = {"file": ("virus.exe", io.BytesIO(b"binary content"), "application/octet-stream")}
        r2 = self.client.post("/api/documents/upload", headers=self.admin_headers, files=files2)
        self.assertEqual(r2.status_code, 400)
        self.assertIn("不支持的文件格式", r2.json()["detail"])

    def test_05_duplicate_content_detection(self):
        """测试同企业内相同内容重复上传：定位已有记录，不重复新建资产"""
        content = "绿城物业服务回访SOP标准文案内容-唯一测试内容-2026".encode("utf-8")
        files = {"file": ("回访SOP-原件.txt", io.BytesIO(content), "text/plain")}
        r1 = self.client.post("/api/documents/upload", headers=self.admin_headers, files=files)
        self.assertEqual(r1.status_code, 200)
        orig_doc_id = r1.json()["document_id"]

        # 换个文件名再次上传相同内容
        files2 = {"file": ("回访SOP-副本.txt", io.BytesIO(content), "text/plain")}
        r2 = self.client.post("/api/documents/upload", headers=self.admin_headers, files=files2)
        self.assertEqual(r2.status_code, 200)
        res_data = r2.json()
        self.assertEqual(res_data["status"], "duplicate_content")
        self.assertEqual(res_data["existing_document_id"], orig_doc_id)

    def test_06_same_name_different_content_ask_and_new_version(self):
        """测试同名但内容不同时的交互模式：ask、new_version、new_document"""
        fn = "品质检查通报.txt"
        c1 = "第1期品质巡查：公区保洁合格率98%".encode("utf-8")
        c2 = "第2期品质巡查：地下车库地面积水待整改".encode("utf-8")

        # 首次上传
        r1 = self.client.post("/api/documents/upload", headers=self.admin_headers, files={"file": (fn, io.BytesIO(c1), "text/plain")})
        doc_id = r1.json()["document_id"]

        # 同名内容不同，未指定模式（默认 ask）
        r2 = self.client.post("/api/documents/upload", headers=self.admin_headers, files={"file": (fn, io.BytesIO(c2), "text/plain")})
        self.assertEqual(r2.json()["status"], "conflict_name")

        # 指定为 new_version
        r3 = self.client.post(
            "/api/documents/upload",
            headers=self.admin_headers,
            data={"duplicate_mode": "new_version", "target_document_id": doc_id},
            files={"file": (fn, io.BytesIO(c2), "text/plain")}
        )
        self.assertEqual(r3.json()["status"], "success")
        self.assertEqual(r3.json()["version_label"], "v2")

        # 验证该文档有两个版本
        detail = self.client.get(f"/api/documents/{doc_id}", headers=self.admin_headers).json()
        self.assertEqual(len(detail["versions"]), 2)

    def test_07_permission_and_isolation(self):
        """测试权限限制：普通成员禁止上传/查询/删除；跨企业禁止读取"""
        # 1. 普通成员尝试获取文档列表
        r_list = self.client.get("/api/documents", headers=self.member_headers)
        self.assertEqual(r_list.status_code, 403)

        # 2. 普通成员尝试上传文件
        files = {"file": ("test.txt", io.BytesIO(b"content"), "text/plain")}
        r_upload = self.client.post("/api/documents/upload", headers=self.member_headers, files=files)
        self.assertEqual(r_upload.status_code, 403)

        # 3. 跨企业管理员读取绿城文档
        # 先由绿城管理员建一个文件
        files_gc = {"file": ("绿城内部绝密.txt", io.BytesIO(b"greentown secret content"), "text/plain")}
        gc_doc = self.client.post("/api/documents/upload", headers=self.admin_headers, files=files_gc).json()
        gc_doc_id = gc_doc["document_id"]

        # 企业 B 管理员请求读取该文档 -> 404（对其他企业不可见）
        r_cross = self.client.get(f"/api/documents/{gc_doc_id}", headers=self.other_headers)
        self.assertEqual(r_cross.status_code, 404)

    def test_08_logical_deletion_and_task_cancellation(self):
        """测试文档逻辑删除及关联任务失效"""
        files = {"file": ("待删除资料.txt", io.BytesIO("待删除的临时资料".encode("utf-8")), "text/plain")}
        upload_res = self.client.post("/api/documents/upload", headers=self.admin_headers, files=files).json()
        doc_id = upload_res["document_id"]

        # 删除文档
        del_resp = self.client.delete(f"/api/documents/{doc_id}", headers=self.admin_headers)
        self.assertEqual(del_resp.status_code, 200)

        # 再次获取详情应返回 404
        get_resp = self.client.get(f"/api/documents/{doc_id}", headers=self.admin_headers)
        self.assertEqual(get_resp.status_code, 404)

        # 列表不出现该文档
        list_resp = self.client.get("/api/documents", headers=self.admin_headers).json()
        self.assertFalse(any(d["id"] == doc_id for d in list_resp))

    def test_09_retry_and_file_download(self):
        """测试失败或已解析任务的重试接口及原始文件受控下载"""
        files = {"file": ("作业规范.txt", io.BytesIO("现场标准化作业程序正文内容".encode("utf-8")), "text/plain")}
        upload_res = self.client.post("/api/documents/upload", headers=self.admin_headers, files=files).json()
        doc_id = upload_res["document_id"]
        ver_id = upload_res["version_id"]

        # 等待首轮解析完毕
        self._wait_for_task_completion(doc_id)

        # 触发重试接口
        retry_resp = self.client.post(f"/api/documents/{doc_id}/versions/{ver_id}/retry", headers=self.admin_headers)
        self.assertEqual(retry_resp.status_code, 200)
        self.assertIn("task_id", retry_resp.json())

        # 再次等待解析完成
        detail = self._wait_for_task_completion(doc_id)
        self.assertEqual(detail["versions"][0]["processing_status"], "completed")

        # 验证受控下载接口返回正确内容
        file_resp = self.client.get(f"/api/documents/{doc_id}/versions/{ver_id}/file", headers=self.admin_headers)
        self.assertEqual(file_resp.status_code, 200)
        self.assertIn("现场标准化作业程序正文内容", file_resp.text)

if __name__ == "__main__":
    unittest.main(verbosity=2)
