from datetime import datetime, timezone
from database import get_db, init_db
from auth import hash_password

def seed_data(force: bool = False):
    """
    初始化演示/测试数据。
    默认采用 INSERT OR IGNORE 确保幂等：已存在账号的状态、禁用标记与修改后的密码/角色在服务重启时得到持久化保留。
    当 force=True 时，才覆盖重置为初始演示数据。
    """
    init_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    
    with get_db() as conn:
        # 1. 预置企业 (Organizations)
        conn.execute(
            "INSERT OR IGNORE INTO organizations (id, name, created_at) VALUES (?, ?, ?)",
            ("org_greentown", "绿城物业服务集团 · 运营咨询中心", now_iso)
        )
        conn.execute(
            "INSERT OR IGNORE INTO organizations (id, name, created_at) VALUES (?, ?, ?)",
            ("org_other", "第三方物业管理公司", now_iso)
        )
        
        # 2. 预置测试账号 (Users)
        admin_pass = hash_password("Admin@Zhixing2026")
        member_pass = hash_password("Member@Zhixing2026")
        other_pass = hash_password("Other@Zhixing2026")

        action = "INSERT OR REPLACE" if force else "INSERT OR IGNORE"
        
        # 管理员：文哲 (admin)
        conn.execute(
            f"""
            {action} INTO users (id, organization_id, username, password_hash, display_name, role, account_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("usr_admin_001", "org_greentown", "admin", admin_pass, "文哲", "admin", "active", now_iso, now_iso)
        )
        
        # 普通成员：李景研 (member)
        conn.execute(
            f"""
            {action} INTO users (id, organization_id, username, password_hash, display_name, role, account_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("usr_member_001", "org_greentown", "member", member_pass, "李景研", "member", "active", now_iso, now_iso)
        )
        
        # 隔离企业管理员 (other_admin)
        conn.execute(
            f"""
            {action} INTO users (id, organization_id, username, password_hash, display_name, role, account_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("usr_other_001", "org_other", "other_admin", other_pass, "张经理", "admin", "active", now_iso, now_iso)
        )

    print(f"Seed data loaded successfully (force={force}).")

if __name__ == "__main__":
    seed_data(force=True)
