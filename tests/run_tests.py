import sys
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

from main import app
from database import init_db
from seed import seed_data

class TestAuthAndPermissions(unittest.TestCase):
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

    def test_01_login_success_admin(self):
        """测试管理员登录返回正确的身份标识与会话"""
        resp = self.client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("token", data)
        self.assertEqual(data["user"]["role"], "admin")
        self.assertEqual(data["user"]["display_name"], "文哲")
        self.assertEqual(data["user"]["organization_id"], "org_greentown")

    def test_02_login_success_member(self):
        """测试普通成员登录返回正确的身份标识与会话 (AC33)"""
        resp = self.client.post("/api/auth/login", json={"username": "member", "password": "Member@Zhixing2026"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("token", data)
        self.assertEqual(data["user"]["role"], "member")
        self.assertEqual(data["user"]["display_name"], "李景研")

    def test_03_ac01_empty_knowledge_base(self):
        """AC01: 全新知识库没有内容时，返回 is_empty=True，支持空白页展现"""
        login_resp = self.client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
        token = login_resp.json()["token"]
        
        resp = self.client.get("/api/knowledge/overview", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["is_empty"])
        self.assertEqual(data["document_count"], 0)
        self.assertEqual(data["knowledge_count"], 0)

    def test_04_ac02_ac34_member_forbidden_on_admin_endpoint(self):
        """AC02 & AC34: 普通成员尝试访问管理员专享知识管理接口，被拒绝 403 Forbidden，不可伪造权限"""
        member_login = self.client.post("/api/auth/login", json={"username": "member", "password": "Member@Zhixing2026"})
        token = member_login.json()["token"]
        
        resp = self.client.get("/api/knowledge/overview", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 403)
        self.assertIn("当前账号无权访问此页面", resp.json()["detail"])

    def test_05_ac35_login_invalid_after_logout(self):
        """AC35: 退出登录后 token 立即失效，不能继续请求受保护内容"""
        login_resp = self.client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
        token = login_resp.json()["token"]
        
        # 登出
        logout_resp = self.client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(logout_resp.status_code, 200)
        
        # 再次请求
        follow_resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(follow_resp.status_code, 401)
        self.assertIn("重新登录", follow_resp.json()["detail"])

    def test_06_ac36_realtime_permission_revocation(self):
        """AC36: 账号被禁用后，下一个受保护请求立即采用最新授权结果，不沿用旧权限"""
        admin_login = self.client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
        admin_token = admin_login.json()["token"]
        
        member_login = self.client.post("/api/auth/login", json={"username": "member", "password": "Member@Zhixing2026"})
        member_token = member_login.json()["token"]
        member_id = member_login.json()["user"]["id"]
        
        res1 = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {member_token}"})
        self.assertEqual(res1.status_code, 200)
        
        # 禁用账号
        mod_resp = self.client.post(
            f"/api/test/users/{member_id}/status",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"account_status": "disabled"}
        )
        self.assertEqual(mod_resp.status_code, 200)
        
        # 再次请求，立即被拦截 403
        res2 = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {member_token}"})
        self.assertEqual(res2.status_code, 403)
        self.assertIn("账号已被禁用", res2.json()["detail"])
        
        # 恢复状态
        self.client.post(
            f"/api/test/users/{member_id}/status",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"account_status": "active"}
        )

    def test_07_cross_tenant_forbidden(self):
        """AC03 & 安全加固：企业 A 管理员尝试修改企业 B 用户状态，必须被拦截 403 Forbidden"""
        admin_login = self.client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
        admin_token = admin_login.json()["token"]
        
        # usr_other_001 归属于 org_other
        cross_resp = self.client.post(
            "/api/test/users/usr_other_001/status",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"account_status": "disabled"}
        )
        self.assertEqual(cross_resp.status_code, 403)
        self.assertIn("越权操作", cross_resp.json()["detail"])

    def test_08_reboot_preserves_user_status(self):
        """服务重启幂等性：被禁用的账号在服务重启或再次加载初始化数据时，状态不得被重置为 active"""
        admin_login = self.client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
        admin_token = admin_login.json()["token"]
        
        # 1. 禁用 member 账号
        self.client.post(
            "/api/test/users/usr_member_001/status",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"account_status": "disabled"}
        )
        
        # 2. 模拟服务重启 (调用常规 lifespan/启动所用的 seed_data(force=False))
        seed_data(force=False)
        
        # 3. 验证 member 尝试登录应被拒绝 (证明 status 仍为 disabled，未被覆盖)
        member_resp = self.client.post("/api/auth/login", json={"username": "member", "password": "Member@Zhixing2026"})
        self.assertEqual(member_resp.status_code, 403)
        self.assertIn("账号已被禁用", member_resp.json()["detail"])
        
        # 4. 恢复 member 为 active 供后续用例使用
        self.client.post(
            "/api/test/users/usr_member_001/status",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"account_status": "active"}
        )

if __name__ == "__main__":
    unittest.main(verbosity=2)
