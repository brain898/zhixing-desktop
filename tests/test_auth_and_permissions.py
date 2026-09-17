import sys
from pathlib import Path
from fastapi.testclient import TestClient

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

from main import app
from database import init_db, get_db
from seed import seed_data

client = TestClient(app)

def setup_module():
    init_db()
    seed_data()

def test_login_success_admin():
    """测试管理员登录返回正确的身份标识与会话"""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "token" in data
    assert data["user"]["role"] == "admin"
    assert data["user"]["display_name"] == "文哲"
    assert data["user"]["organization_id"] == "org_greentown"

def test_login_success_member():
    """测试普通成员登录返回正确的身份标识与会话 (AC33)"""
    resp = client.post("/api/auth/login", json={"username": "member", "password": "Member@Zhixing2026"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "token" in data
    assert data["user"]["role"] == "member"
    assert data["user"]["display_name"] == "李景研"

def test_ac01_empty_knowledge_base():
    """AC01: 全新知识库没有内容时，返回 is_empty=True，支持空白页展现"""
    login_resp = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
    token = login_resp.json()["token"]
    
    resp = client.get("/api/knowledge/overview", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_empty"] is True
    assert data["document_count"] == 0
    assert data["knowledge_count"] == 0

def test_ac02_ac34_member_forbidden_on_admin_endpoint():
    """AC02 & AC34: 普通成员尝试访问管理员专享知识管理接口，被拒绝 403 Forbidden，不可伪造权限"""
    member_login = client.post("/api/auth/login", json={"username": "member", "password": "Member@Zhixing2026"})
    token = member_login.json()["token"]
    
    resp = client.get("/api/knowledge/overview", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert "当前账号无权访问此页面" in resp.json()["detail"]

def test_ac35_login_invalid_after_logout():
    """AC35: 退出登录后 token 立即失效，不能继续请求受保护内容"""
    login_resp = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
    token = login_resp.json()["token"]
    
    # 登出
    logout_resp = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout_resp.status_code == 200
    
    # 再次请求
    follow_resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert follow_resp.status_code == 401
    assert "重新登录" in follow_resp.json()["detail"]

def test_ac36_realtime_permission_revocation():
    """AC36: 账号被禁用或角色变更后，下一个受保护请求立即采用最新授权结果，不沿用旧权限"""
    # 登录管理员
    admin_login = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@Zhixing2026"})
    admin_token = admin_login.json()["token"]
    
    # 登录成员并将其临时变为 admin 再恢复，或者将 member 禁用
    member_login = client.post("/api/auth/login", json={"username": "member", "password": "Member@Zhixing2026"})
    member_token = member_login.json()["token"]
    member_id = member_login.json()["user"]["id"]
    
    # 此时 member 访问 auth/me 是 200
    res1 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {member_token}"})
    assert res1.status_code == 200
    
    # 管理员在后台禁用该成员账号
    mod_resp = client.post(
        f"/api/test/users/{member_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"account_status": "disabled"}
    )
    assert mod_resp.status_code == 200
    
    # 成员使用已有 token 再次请求，立即被拦截 403
    res2 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {member_token}"})
    assert res2.status_code == 403
    assert "账号已被禁用" in res2.json()["detail"]
    
    # 恢复状态以防影响后续
    client.post(
        f"/api/test/users/{member_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"account_status": "active"}
    )
