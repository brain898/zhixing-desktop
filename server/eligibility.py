"""
知行有策 - 统一检索资格与访问权限服务模块 (Stage 4A)

实现 PRD FR08, FR10, FR11, FR12, FR14, FR15 以及 Stage 4A 的 14 项正式检索资格基线规则。
管理员正式检索、未来普通成员 AI 咨询、以及来源访问均必须复用本模块，禁止各写一套。
"""

import json
import sqlite3
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from pydantic import BaseModel

class EligibilityResult(BaseModel):
    is_eligible: bool
    code: str
    reason: str
    item_id: Optional[str] = None
    version_id: Optional[str] = None
    document_id: Optional[str] = None
    document_version_id: Optional[str] = None

def get_current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def build_eligibility_sql(
    user: Dict[str, Any],
    now_iso: Optional[str] = None,
    table_aliases: Optional[Dict[str, str]] = None,
) -> Tuple[List[str], List[Any]]:
    """
    构造统一的 SQL 过滤条件（WHERE clauses 及对应的参数列表）。
    
    14 项正式知识准入规则：
    1. 用户已登录且账号 active。
    2. 用户所属 organization 与知识、文件一致。
    3. knowledge_items.lifecycle_status = 'active'。
    4. knowledge_items 未被删除或排除 (lifecycle_status != 'deleted', is_excluded = 0, deleted_at IS NULL)。
    5. knowledge_versions.review_status = 'confirmed'。
    6. knowledge_versions.index_status = 'ready'。
    7. valid_from / valid_until 在当前有效区间内；空值表示未设置边界。
    8. knowledge_items.active_version_id = 当前知识版本 (kv.id)。
    9. documents.is_deleted = 0。
    10. documents.active_version_id = knowledge_versions.source_document_version_id。
    11. source_document_version 必须真实属于该 knowledge_item.document_id 和同一 organization。
    12. 文件权限允许当前用户。
    13. 知识权限允许当前用户。
    14. 文件权限是知识权限上限：知识可以更严格，不能比文件更开放。
    
    管理员与成员共用同一规则：
    - 管理员 (role='admin')：允许访问 admin_only 与 org_internal。
    - 普通成员 (role='member')：必须满足 documents.access_scope = 'org_internal' 且 knowledge_items.access_scope = 'org_internal'。
    """
    aliases = {
        "ki": "ki",
        "kv": "kv",
        "d": "d",
        "dv": "dv",
    }
    if table_aliases:
        aliases.update(table_aliases)

    ki = aliases["ki"]
    kv = aliases["kv"]
    d = aliases["d"]
    dv = aliases["dv"]

    # 规则 1：用户已登录且账号 active
    if not user or user.get("account_status") != "active":
        return ["1 = 0"], []

    org_id = user.get("organization_id")
    if not org_id:
        return ["1 = 0"], []

    role = user.get("role")
    if role not in ("admin", "member"):
        return ["1 = 0"], []

    if not now_iso:
        now_iso = get_current_utc_iso()

    clauses = [
        # 规则 2: 租户隔离
        f"{ki}.organization_id = ?",
        f"{d}.organization_id = ?",
        f"{kv}.organization_id = ?",
        f"{dv}.organization_id = ?",
        # 规则 3 & 4: lifecycle_status = 'active' 且未删除未排除
        f"{ki}.lifecycle_status = 'active'",
        f"({ki}.is_excluded = 0 OR {ki}.is_excluded IS NULL)",
        f"{ki}.deleted_at IS NULL",
        # 规则 5: review_status = 'confirmed'
        f"{kv}.review_status = 'confirmed'",
        # 规则 6: index_status = 'ready'
        f"{kv}.index_status = 'ready'",
        # 规则 7: 有效期边界
        f"({kv}.valid_from IS NULL OR {kv}.valid_from <= ?)",
        f"({kv}.valid_until IS NULL OR {kv}.valid_until >= ?)",
        # 规则 8: 条目当前生效版本
        f"{ki}.active_version_id = {kv}.id",
        # 规则 9: 文档未删除
        f"{d}.is_deleted = 0",
        # 规则 10: 文档生效版本与知识来源版本必须一致
        f"{d}.active_version_id IS NOT NULL",
        f"{d}.active_version_id = {kv}.source_document_version_id",
        # 规则 11: 来源文件版本真实属于该 document 和同一 organization
        f"{dv}.id = {kv}.source_document_version_id",
        f"{dv}.document_id = {ki}.document_id",
        f"{dv}.organization_id = {ki}.organization_id",
    ]

    params = [org_id, org_id, org_id, org_id, now_iso, now_iso]

    # 规则 12, 13, 14: 权限边界与上限
    if role == "member":
        # 普通成员只有在文件与知识均为 org_internal 时才允许使用
        clauses.append(f"{d}.access_scope = 'org_internal'")
        clauses.append(f"{ki}.access_scope = 'org_internal'")
    elif role == "admin":
        # 管理员具备全量受控访问权限，且文件权限为知识权限上限规则天然成立
        pass

    return clauses, params

def check_knowledge_eligibility(
    conn: sqlite3.Connection,
    version_id: str,
    user: Dict[str, Any],
    now_iso: Optional[str] = None
) -> EligibilityResult:
    """
    单条知识版本回查校验函数：针对指定知识版本 ID，严格检查全部 14 项资格规则。
    任何一项不满足，均返回明确的 code 与原因。
    """
    if not user or user.get("account_status") != "active":
        return EligibilityResult(
            is_eligible=False,
            code="USER_NOT_ACTIVE",
            reason="用户未登录或账号处于禁用状态",
            version_id=version_id,
        )

    role = user.get("role")
    if role not in ("admin", "member"):
        return EligibilityResult(
            is_eligible=False,
            code="ROLE_UNAUTHORIZED",
            reason="非法或未授权的用户角色",
            version_id=version_id,
        )

    user_org = user.get("organization_id")
    if not now_iso:
        now_iso = get_current_utc_iso()

    row = conn.execute(
        """
        SELECT 
            kv.id as version_id,
            kv.item_id as item_id,
            kv.organization_id as kv_org,
            kv.source_document_version_id,
            kv.review_status,
            kv.index_status,
            kv.valid_from,
            kv.valid_until,
            ki.organization_id as ki_org,
            ki.document_id as ki_doc_id,
            ki.active_version_id as ki_active_version_id,
            ki.access_scope as ki_access_scope,
            ki.lifecycle_status as ki_lifecycle_status,
            ki.is_excluded as ki_is_excluded,
            ki.deleted_at as ki_deleted_at,
            d.organization_id as d_org,
            d.active_version_id as d_active_version_id,
            d.access_scope as d_access_scope,
            d.is_deleted as d_is_deleted,
            dv.organization_id as dv_org,
            dv.document_id as dv_doc_id
        FROM knowledge_versions kv
        JOIN knowledge_items ki ON kv.item_id = ki.id
        JOIN documents d ON ki.document_id = d.id
        LEFT JOIN document_versions dv ON kv.source_document_version_id = dv.id
        WHERE kv.id = ?
        """,
        (version_id,)
    ).fetchone()

    if not row:
        return EligibilityResult(
            is_eligible=False,
            code="VERSION_NOT_FOUND",
            reason="知识版本不存在或关联业务对象已丢失",
            version_id=version_id,
        )

    # 规则 2: 租户匹配
    if not (row["kv_org"] == user_org and row["ki_org"] == user_org and row["d_org"] == user_org and row["dv_org"] == user_org):
        return EligibilityResult(
            is_eligible=False,
            code="TENANT_MISMATCH",
            reason="知识资产不属于当前用户所在企业",
            version_id=version_id,
            item_id=row["item_id"],
            document_id=row["ki_doc_id"],
        )

    # 规则 3: lifecycle_status = 'active'
    if row["ki_lifecycle_status"] != "active":
        return EligibilityResult(
            is_eligible=False,
            code="ITEM_LIFECYCLE_NOT_ACTIVE",
            reason=f"知识条目生命周期状态不为 active（当前为 {row['ki_lifecycle_status']}）",
            version_id=version_id,
            item_id=row["item_id"],
        )

    # 规则 4: 未被删除或排除
    if row["ki_is_excluded"] == 1 or row["ki_deleted_at"] is not None:
        return EligibilityResult(
            is_eligible=False,
            code="ITEM_EXCLUDED_OR_DELETED",
            reason="知识条目已被管理员标记排除或已逻辑删除",
            version_id=version_id,
            item_id=row["item_id"],
        )

    # 规则 5: review_status = 'confirmed'
    if row["review_status"] != "confirmed":
        return EligibilityResult(
            is_eligible=False,
            code="REVIEW_NOT_CONFIRMED",
            reason=f"知识版本尚未确认（当前为 {row['review_status']}）",
            version_id=version_id,
            item_id=row["item_id"],
        )

    # 规则 6: index_status = 'ready'
    if row["index_status"] != "ready":
        return EligibilityResult(
            is_eligible=False,
            code="INDEX_NOT_READY",
            reason=f"知识版本检索索引尚未就绪（当前为 {row['index_status']}）",
            version_id=version_id,
            item_id=row["item_id"],
        )

    # 规则 7: 有效期校验
    if row["valid_from"] and row["valid_from"] > now_iso:
        return EligibilityResult(
            is_eligible=False,
            code="NOT_YET_VALID",
            reason=f"知识版本尚未到达生效时间 (valid_from={row['valid_from']})",
            version_id=version_id,
            item_id=row["item_id"],
        )
    if row["valid_until"] and row["valid_until"] < now_iso:
        return EligibilityResult(
            is_eligible=False,
            code="EXPIRED",
            reason=f"知识版本已超过有效期限 (valid_until={row['valid_until']})",
            version_id=version_id,
            item_id=row["item_id"],
        )

    # 规则 8: 条目当前生效版本
    if row["ki_active_version_id"] != version_id:
        return EligibilityResult(
            is_eligible=False,
            code="NOT_ACTIVE_ITEM_VERSION",
            reason=f"该版本不是条目的当前生效版本（当前生效为 {row['ki_active_version_id']}）",
            version_id=version_id,
            item_id=row["item_id"],
        )

    # 规则 9: 文件未删除
    if row["d_is_deleted"] != 0:
        return EligibilityResult(
            is_eligible=False,
            code="DOCUMENT_DELETED",
            reason="来源文件已被逻辑删除",
            version_id=version_id,
            item_id=row["item_id"],
            document_id=row["ki_doc_id"],
        )

    # 规则 10: 文件生效版本与知识来源版本必须一致
    if not row["d_active_version_id"]:
        return EligibilityResult(
            is_eligible=False,
            code="DOCUMENT_NO_ACTIVE_VERSION",
            reason="来源文件尚未建立或启用正式生效版本",
            version_id=version_id,
            item_id=row["item_id"],
            document_id=row["ki_doc_id"],
        )

    if row["d_active_version_id"] != row["source_document_version_id"]:
        return EligibilityResult(
            is_eligible=False,
            code="SOURCE_DOC_VERSION_INACTIVE",
            reason=f"知识来源文件版本（{row['source_document_version_id']}）非文档当前生效版本（{row['d_active_version_id']}）",
            version_id=version_id,
            item_id=row["item_id"],
            document_id=row["ki_doc_id"],
        )

    # 规则 11: 来源文件版本归属
    if not row["dv_doc_id"] or row["dv_doc_id"] != row["ki_doc_id"]:
        return EligibilityResult(
            is_eligible=False,
            code="SOURCE_BLOCK_MISMATCH",
            reason="来源文件版本不属于该条目关联的文档",
            version_id=version_id,
            item_id=row["item_id"],
            document_id=row["ki_doc_id"],
        )

    # 规则 12, 13, 14: 权限校验及上限原则
    if role == "member":
        if row["d_access_scope"] != "org_internal":
            return EligibilityResult(
                is_eligible=False,
                code="DOCUMENT_SCOPE_RESTRICTED",
                reason="来源文件仅限管理员专享 (admin_only)，普通成员无权检索",
                version_id=version_id,
                item_id=row["item_id"],
                document_id=row["ki_doc_id"],
            )
        if row["ki_access_scope"] != "org_internal":
            return EligibilityResult(
                is_eligible=False,
                code="ITEM_SCOPE_RESTRICTED",
                reason="知识条目仅限管理员专享 (admin_only)，普通成员无权检索",
                version_id=version_id,
                item_id=row["item_id"],
                document_id=row["ki_doc_id"],
            )

    return EligibilityResult(
        is_eligible=True,
        code="ELIGIBLE",
        reason="符合全部 14 项正式检索资格准入规则",
        version_id=version_id,
        item_id=row["item_id"],
        document_id=row["ki_doc_id"],
        document_version_id=row["source_document_version_id"],
    )

def filter_eligible_version_ids(
    conn: sqlite3.Connection,
    version_ids: List[str],
    user: Dict[str, Any],
    now_iso: Optional[str] = None
) -> List[str]:
    """
    批量从业务数据库严格回查，过滤出具备正式检索资格的版本 ID 列表。
    杜绝仅凭 retrieval_records 索引存在即放行，保证脏旧/失效索引被 100% 拦截。
    """
    if not version_ids:
        return []

    clauses, params = build_eligibility_sql(user, now_iso)
    placeholders = ",".join("?" for _ in version_ids)
    clauses.append(f"kv.id IN ({placeholders})")
    params.extend(version_ids)

    sql = f"""
    SELECT kv.id
    FROM knowledge_versions kv
    JOIN knowledge_items ki ON kv.item_id = ki.id
    JOIN documents d ON ki.document_id = d.id
    JOIN document_versions dv ON kv.source_document_version_id = dv.id
    WHERE {' AND '.join(clauses)}
    """
    rows = conn.execute(sql, params).fetchall()
    eligible_set = {r["id"] for r in rows}
    # 保持原输入顺序
    return [vid for vid in version_ids if vid in eligible_set]

def check_document_access(
    conn: sqlite3.Connection,
    document_id: str,
    user: Dict[str, Any],
    version_id: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    文件访问权限受控校验（原件下载、预览、来源追溯）：
    - 管理员：所属企业一致且未删除即可访问，可追溯历史版本。
    - 普通成员：必须未删除、属于当前企业、access_scope = 'org_internal' 且具有已生效版本；
      当指定 version_id 时，只允许访问 documents.active_version_id，禁止绕过正式版本读取历史/未启用原件。
    """
    if not user or user.get("account_status") != "active":
        return False, "用户未登录或账号已被禁用"

    role = user.get("role")
    org_id = user.get("organization_id")

    doc = conn.execute(
        "SELECT id, organization_id, active_version_id, access_scope, is_deleted FROM documents WHERE id = ?",
        (document_id,)
    ).fetchone()

    if not doc or doc["organization_id"] != org_id or doc["is_deleted"] != 0:
        return False, "文档不存在或已被删除"

    if role == "admin":
        return True, None

    if role == "member":
        if doc["access_scope"] != "org_internal":
            return False, "该文件设为管理员专享，普通成员无权访问"
        if not doc["active_version_id"]:
            return False, "该文件尚未启用正式生效版本，暂不可供成员使用"
        if version_id is not None and version_id != doc["active_version_id"]:
            return False, "普通成员只能访问当前正式生效的文件版本"
        return True, None

    return False, "未知用户角色"
