"""
M01 知识资产整理模块端到端业务闭环自动化测试
覆盖：
1. 来源证据多字段聚合与段落锚点关联
2. 正式检索隔离：未确认条目不得参与检索
3. 确认启用闭环：建立检索索引，index_status='ready'，正式检索可用
4. 生命周期管理：停用立即退出检索，恢复立即重新参与检索
5. 版本管理与草稿隔离 (FR09)：已生效版本编辑产生新草稿，旧版本持续服务，新版本确认后无缝切换
6. 权限与租户隔离：企业数据隔离、成员访问权限控制
"""

import sys
import json
import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from main import app
from database import get_db, init_db
from tasks import build_index_for_version

def run_tests():
    print("==================================================")
    print("开始执行 M01 知识资产整理模块端到端自动化测试...")
    print("==================================================")

    init_db()
    client = TestClient(app)

    # 1. 登录管理员与普通成员获取 Token
    admin_login_res = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin@Zhixing2026"}
    )
    assert admin_login_res.status_code == 200, f"Admin login failed: {admin_login_res.text}"
    admin_token = admin_login_res.json()["token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    member_login_res = client.post(
        "/api/auth/login",
        json={"username": "李景研", "password": "Member@Zhixing2026"}
    )
    if member_login_res.status_code != 200:
        member_login_res = client.post(
            "/api/auth/login",
            json={"username": "member", "password": "Member@Zhixing2026"}
        )
    assert member_login_res.status_code == 200, f"Member login failed: {member_login_res.text}"
    member_token = member_login_res.json()["token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    other_login_res = client.post(
        "/api/auth/login",
        json={"username": "other_admin", "password": "Other@Zhixing2026"}
    )
    assert other_login_res.status_code == 200, f"Other admin login failed: {other_login_res.text}"
    other_token = other_login_res.json()["token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    print("✅ 1. 账号认证与 Token 获取成功 (Admin, Member, Other Org Admin)")

    # 准备测试资料与结构块
    now_iso = datetime.now(timezone.utc).isoformat()
    test_doc_id = f"doc_test_{uuid.uuid4().hex[:8]}"
    test_ver_id = f"ver_test_{uuid.uuid4().hex[:8]}"
    test_block_id = f"blk_test_{uuid.uuid4().hex[:8]}"

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO documents (id, organization_id, title, access_scope, is_deleted, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (test_doc_id, "org_greentown", "绿城园区高空抛物监控管理规程", "org_internal", now_iso, now_iso)
        )
        conn.execute(
            """
            INSERT INTO document_versions (id, document_id, organization_id, version_label, file_name, file_type, file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
            VALUES (?, ?, ?, 'V1.0', '高空抛物规程.docx', 'docx', 10240, 'hash123', '/storage/test.docx', 'usr_admin_001', ?, 'completed')
            """,
            (test_ver_id, test_doc_id, "org_greentown", now_iso)
        )
        conn.execute(
            "UPDATE documents SET active_version_id = ? WHERE id = ?",
            (test_ver_id, test_doc_id)
        )
        conn.execute(
            """
            INSERT INTO source_blocks (id, document_version_id, organization_id, block_index, block_type, heading_path, page_number, paragraph_anchor, text_content, created_at)
            VALUES (?, ?, 'org_greentown', 0, 'paragraph', '第三章 安全防范/第12条', 3, 'p.3#s2', '对园区高空抛物监控摄像头，工程部安防专员必须每两周进行一次角度校准与镜头擦拭。若遇大风暴雨恶劣天气，应在雨后两小时内完成专项检查。', ?)
            """,
            (test_block_id, test_ver_id, now_iso)
        )

    # 创建一个待核对知识条目
    test_item_id = f"item_{uuid.uuid4().hex[:8]}"
    test_kver_id = f"kver_{uuid.uuid4().hex[:8]}"
    unique_kw = f"高空校准{uuid.uuid4().hex[:6]}"

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope, lifecycle_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'org_internal', 'active', ?, ?)
            """,
            (test_item_id, test_doc_id, "org_greentown", test_kver_id, now_iso, now_iso)
        )
        conn.execute(
            """
            INSERT INTO knowledge_versions (
                id, item_id, organization_id, source_document_version_id, version_number,
                title, statement, content, primary_category, atom_type,
                subject, conditions_json, actions_json, exceptions_json,
                customer_types_json, business_scenes_json, problem_tags_json,
                review_status, index_status, revision_token, quality_flags_json, created_at, created_by
            ) VALUES (
                ?, ?, 'org_greentown', ?, 1,
                ?, ?, ?, '制度与标准', '规则',
                '工程部安防专员', '["园区配备高空抛物监控系统"]', '["每两周进行一次角度校准与镜头擦拭"]', '["若遇大风暴雨，雨后两小时内完成专项检查"]',
                '["业主"]', '["安防巡检", "工程维保"]', '["设备故障"]',
                'pending_review', 'not_indexed', ?, '[]', ?, 'system_extractor'
            )
            """,
            (
                test_kver_id, test_item_id, test_ver_id,
                f"高空监控设备定期巡检要求 {unique_kw}",
                f"工程部安防专员必须每两周进行一次角度校准与镜头擦拭（{unique_kw}）。",
                f"工程部安防专员必须每两周进行一次角度校准与镜头擦拭（{unique_kw}）。",
                uuid.uuid4().hex, now_iso
            )
        )
        # 为 statement, conditions, actions 均绑定同一 source_block_id，验证多字段同 block 证据
        conn.execute(
            """
            INSERT INTO knowledge_evidence (id, knowledge_version_id, source_block_id, organization_id, field_name, excerpt, accuracy_level, created_at)
            VALUES (?, ?, ?, 'org_greentown', 'statement', '每两周进行一次角度校准与镜头擦拭', 'high', ?)
            """,
            (uuid.uuid4().hex, test_kver_id, test_block_id, now_iso)
        )
        conn.execute(
            """
            INSERT INTO knowledge_evidence (id, knowledge_version_id, source_block_id, organization_id, field_name, excerpt, accuracy_level, created_at)
            VALUES (?, ?, ?, 'org_greentown', 'conditions', '对园区高空抛物监控摄像头', 'high', ?)
            """,
            (uuid.uuid4().hex, test_kver_id, test_block_id, now_iso)
        )
        conn.execute(
            """
            INSERT INTO knowledge_evidence (id, knowledge_version_id, source_block_id, organization_id, field_name, excerpt, accuracy_level, created_at)
            VALUES (?, ?, ?, 'org_greentown', 'actions', '雨后两小时内完成专项检查', 'medium', ?)
            """,
            (uuid.uuid4().hex, test_kver_id, test_block_id, now_iso)
        )

    print("✅ 2. 测试资料、结构块、待核对知识与多字段关联证据插入完成")

    # 验证测试点 1：详情接口能返回关联证据，且前端可聚合
    detail_res = client.get(f"/api/knowledge/items/{test_item_id}", headers=admin_headers)
    assert detail_res.status_code == 200, f"Detail failed: {detail_res.text}"
    detail_data = detail_res.json()
    assert len(detail_data["evidence"]) == 3, f"Evidence count expected 3, got {len(detail_data['evidence'])}"
    assert all(e["source_block_id"] == test_block_id for e in detail_data["evidence"]), "Evidence source_block_id mismatch"
    assert detail_data["evidence"][0]["paragraph_anchor"] == "p.3#s2"
    print("✅ 3. 来源证据接口验证成功（3 处不同字段支撑均精确指向同一 source_block_id，提供段落锚点）")

    # 验证测试点 2：未确认条目在正式检索中必须被隔离（查不到）
    search_res_1 = client.get(f"/api/knowledge/search?q={unique_kw}", headers=admin_headers)
    assert search_res_1.status_code == 200, f"Search failed: {search_res_1.text}"
    assert search_res_1.json()["total"] == 0, f"Unconfirmed item should NOT be searchable, got: {search_res_1.json()}"
    print("✅ 4. 正式检索隔离验证成功（待核对条目不可被正式检索查出）")

    # 验证测试点 3：确认知识条目并启用 -> 自动建立检索索引
    revision_token = detail_data["active_version"]["revision_token"]
    confirm_res = client.post(
        f"/api/knowledge/items/{test_item_id}/confirm",
        json={"revision_token": revision_token},
        headers=admin_headers
    )
    assert confirm_res.status_code == 200, f"Confirm failed: {confirm_res.text}"
    assert confirm_res.json()["review_status"] == "confirmed"

    # 检查数据库：index_status 必须为 'ready'，且 retrieval_records 有记录
    with get_db() as conn:
        row = conn.execute("SELECT review_status, index_status FROM knowledge_versions WHERE id = ?", (test_kver_id,)).fetchone()
        assert row["review_status"] == "confirmed"
        assert row["index_status"] == "ready", f"index_status should be ready, got {row['index_status']}"
        rec = conn.execute("SELECT search_text FROM retrieval_records WHERE knowledge_version_id = ?", (test_kver_id,)).fetchone()
        assert rec is not None, "retrieval_records should contain index record"
        assert unique_kw in rec["search_text"], "retrieval_records search_text should contain keyword"

    # 现在正式检索该关键词：必须能够查到！
    search_res_2 = client.get(f"/api/knowledge/search?q={unique_kw}", headers=admin_headers)
    assert search_res_2.status_code == 200
    search_data_2 = search_res_2.json()
    assert search_data_2["total"] == 1, f"Confirmed item should be searchable, got: {search_data_2}"
    item_res = search_data_2["items"][0]
    assert item_res["item_id"] == test_item_id
    assert item_res["version_number"] == 1
    assert item_res["primary_category"] == "制度与标准"
    assert item_res["document_title"] == "绿城园区高空抛物监控管理规程"
    print("✅ 5. 确认启用与索引闭环验证成功（条目状态为 confirmed，索引就绪，正式检索成功命中并提取摘要）")

    # 验证测试点 4：生命周期管理（停用与恢复启用即时生效）
    disable_res = client.put(
        f"/api/knowledge/items/{test_item_id}/lifecycle",
        json={"lifecycle_status": "disabled"},
        headers=admin_headers
    )
    assert disable_res.status_code == 200, f"Disable failed: {disable_res.text}"

    # 停用后检索：必须立即退出检索！
    search_disabled = client.get(f"/api/knowledge/search?q={unique_kw}", headers=admin_headers)
    assert search_disabled.status_code == 200
    assert search_disabled.json()["total"] == 0, "Disabled item should NOT appear in search results"

    # 恢复启用
    enable_res = client.put(
        f"/api/knowledge/items/{test_item_id}/lifecycle",
        json={"lifecycle_status": "active"},
        headers=admin_headers
    )
    assert enable_res.status_code == 200

    # 恢复后检索：必须立即重新查到！
    search_restored = client.get(f"/api/knowledge/search?q={unique_kw}", headers=admin_headers)
    assert search_restored.status_code == 200
    assert search_restored.json()["total"] == 1, "Restored item should appear in search results"
    print("✅ 6. 生命周期状态即时控制验证成功（停用即刻退出检索，恢复即刻恢复服务）")

    # 验证测试点 5：版本管理与草稿隔离 (PRD FR09)
    # 对已生效条目进行编辑保存：必须产生新版本草稿 (v2)，同时旧版本 (v1) 继续对外服务！
    detail_res_3 = client.get(f"/api/knowledge/items/{test_item_id}", headers=admin_headers)
    v1_token = detail_res_3.json()["active_version"]["revision_token"]

    v2_kw = f"激光测距升级{uuid.uuid4().hex[:6]}"
    draft_res = client.put(
        f"/api/knowledge/items/{test_item_id}/draft",
        json={
            "revision_token": v1_token,
            "title": f"高空监控设备定期巡检与激光校准 {v2_kw}",
            "statement": f"工程部安防专员每两周使用激光测距仪完成角度校准（{v2_kw}）。",
            "content": f"工程部安防专员每两周使用激光测距仪完成角度校准（{v2_kw}）。",
            "primary_category": "制度与标准",
            "atom_type": "规则",
            "subject": "工程部安防专员",
            "conditions": ["园区配备高空抛物监控系统"],
            "actions": ["每两周使用激光测距仪校准"],
            "exceptions": ["恶劣天气顺延"],
        },
        headers=admin_headers
    )
    assert draft_res.status_code == 200, f"Draft failed: {draft_res.text}"
    assert draft_res.json()["is_new_version_draft"] is True, "Saving confirmed item should produce new version draft"

    # 检查数据库与检索状态：
    # 1. 知识库条目的 active_version_id 必须依然指向旧的 v1
    with get_db() as conn:
        ki_row = conn.execute("SELECT active_version_id FROM knowledge_items WHERE id = ?", (test_item_id,)).fetchone()
        assert ki_row["active_version_id"] == test_kver_id, "Serving version should remain v1 while v2 is in draft"

    # 2. 检索新草稿关键词 v2_kw：必须搜不到（未确认启用）
    search_v2_before = client.get(f"/api/knowledge/search?q={v2_kw}", headers=admin_headers)
    assert search_v2_before.json()["total"] == 0, "Draft v2 should NOT be searchable"

    # 3. 检索旧版本关键词 unique_kw：必须依然搜得到（线上服务不中断）
    search_v1_during = client.get(f"/api/knowledge/search?q={unique_kw}", headers=admin_headers)
    assert search_v1_during.json()["total"] == 1, "Serving v1 should remain searchable"

    # 4. 详情接口：显示草稿内容，并且提示 is_draft_version=True, serving_version_number=1
    detail_res_draft = client.get(f"/api/knowledge/items/{test_item_id}", headers=admin_headers)
    assert detail_res_draft.json()["is_draft_version"] is True
    assert detail_res_draft.json()["serving_version_number"] == 1
    assert detail_res_draft.json()["active_version"]["version_number"] == 2

    # 5. 管理员确认启用新草稿 v2
    v2_token = draft_res.json()["revision_token"]
    confirm_v2_res = client.post(
        f"/api/knowledge/items/{test_item_id}/confirm",
        json={"revision_token": v2_token},
        headers=admin_headers
    )
    assert confirm_v2_res.status_code == 200

    # 6. 确认后：active_version_id 切换到 v2，新草稿关键词 v2_kw 立即生效可搜！
    search_v2_after = client.get(f"/api/knowledge/search?q={v2_kw}", headers=admin_headers)
    assert search_v2_after.json()["total"] == 1, "Confirmed v2 should now be searchable"
    assert search_v2_after.json()["items"][0]["version_number"] == 2
    print("✅ 7. 版本草稿隔离与切换验证成功（修改已生效知识产生 v2 草稿，v1 持续在线服务，确认后平滑切换到 v2）")

    # 验证测试点 6：企业租户隔离与成员权限
    # 隔离企业管理员检索 org_greentown 的知识：必须完全搜不到
    other_search = client.get(f"/api/knowledge/search?q={v2_kw}", headers=other_headers)
    assert other_search.status_code == 200
    assert other_search.json()["total"] == 0, "Other organization must NOT see this item"

    # 权限控制：若条目被改为 admin_only，普通成员无法搜到
    with get_db() as conn:
        conn.execute("UPDATE knowledge_items SET access_scope = 'admin_only' WHERE id = ?", (test_item_id,))
    
    member_search = client.get(f"/api/knowledge/search?q={v2_kw}", headers=member_headers)
    assert member_search.status_code == 200
    assert member_search.json()["total"] == 0, "Member role should NOT see admin_only knowledge item"

    admin_search_again = client.get(f"/api/knowledge/search?q={v2_kw}", headers=admin_headers)
    assert admin_search_again.status_code == 200
    assert admin_search_again.json()["total"] == 1, "Admin role should see admin_only knowledge item"
    print("✅ 8. 企业隔离与访问权限控制验证成功（跨租户 0 泄漏，admin_only 对成员严格不可见）")

    # 清理测试条目（逻辑删除）
    del_res = client.delete(f"/api/knowledge/items/{test_item_id}?action=delete", headers=admin_headers)
    assert del_res.status_code == 200
    search_after_del = client.get(f"/api/knowledge/search?q={v2_kw}", headers=admin_headers)
    assert search_after_del.json()["total"] == 0, "Deleted item must exit search"
    print("✅ 9. 条目逻辑删除验证成功（彻底退出检索，数据安全归档）")

    print("==================================================")
    print("🎉 全部 9 项端到端业务闭环测试均顺利通过！")
    print("==================================================")

if __name__ == "__main__":
    run_tests()
