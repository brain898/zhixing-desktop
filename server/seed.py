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

    # 3. 受控处理历史已有异常数据 (如纯符号/分割线无效提取)
    sanitize_existing_anomalies()

    print(f"Seed data loaded successfully (force={force}).")

def sanitize_existing_anomalies():
    """
    受控处理历史数据库中已存在的异常数据：
    要求：
    - 不静默删除已有人工确认内容；
    - 已有异常记录要明确标识并按受控方式处理；
    - 未经人工核对的纯符号/无效提取（如陈述为 ---），受控转为 excluded（不收录），移出待核对队列并保留记录；
    - 已有人工确认的异常数据，保留并打上复核质检标记。
    """
    import json
    import re
    from deepseek_extractor import is_meaningful_business_text
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT ki.id as item_id, ki.lifecycle_status, kv.id as version_id, kv.statement,
                   kv.review_status, kv.reviewed_by, kv.quality_flags_json
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON ki.active_version_id = kv.id
            WHERE ki.lifecycle_status != 'deleted'
            """
        ).fetchall()

        for r in rows:
            stmt = r["statement"] or ""
            if not is_meaningful_business_text(stmt):
                q_flags = json.loads(r["quality_flags_json"] or "[]")
                flag_msg = "核心陈述缺乏有效业务内容，属于无效提取"
                if flag_msg not in q_flags:
                    q_flags.append(flag_msg)

                if r["review_status"] == "confirmed" or r["reviewed_by"] is not None:
                    # 已有人工确认，保留并提示管理员复核
                    conn.execute(
                        "UPDATE knowledge_versions SET quality_flags_json = ? WHERE id = ?",
                        (json.dumps(q_flags, ensure_ascii=False), r["version_id"])
                    )
                else:
                    # 未经人工确认的无效候选，受控排除，移出人工待核对队列并保留处理记录
                    exclude_flag = "已受控排除：无效提取片段不塞入人工核对队列"
                    if exclude_flag not in q_flags:
                        q_flags.append(exclude_flag)
                    conn.execute(
                        "UPDATE knowledge_items SET lifecycle_status = 'excluded', updated_at = ? WHERE id = ?",
                        (now_iso, r["item_id"])
                    )
                    conn.execute(
                        "UPDATE knowledge_versions SET quality_flags_json = ? WHERE id = ?",
                        (json.dumps(q_flags, ensure_ascii=False), r["version_id"])
                    )

if __name__ == "__main__":
    seed_data(force=True)
