import io
import sys
import json
import uuid
import hashlib
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

# 兼容无控制台环境
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

from fastapi import FastAPI, HTTPException, status, Depends, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from config import (
    SERVER_HOST,
    SERVER_PORT,
    ENVIRONMENT,
    get_storage_dir,
    resolve_storage_path,
    MAX_FILE_SIZE_BYTES,
    ALLOWED_EXTENSIONS,
    ENABLE_DEMO_SEED,
    ALLOWED_ORIGINS,
)
from skill_constants import SCENE_MIN_ATOMS_FOR_GENERATION
from database import init_db, get_db
from seed import seed_data
from tasks import (
    submit_task,
    recover_interrupted_tasks,
    enqueue_jev_evaluation,
    enqueue_scene_merge_suggestion,
    enqueue_skill_generation_task,
    enqueue_skill_review_task,
)
import scene_catalog as sc
import skill_generation as sg
import skill_review as sr
import skill_statistics as ss
import skill_recheck as rc
from scene_catalog import SceneCatalogError, register_unmatched_scene_tags
from jev_evaluator import serialize_evaluation
from indexing import (
    create_or_get_build_index_task,
    create_or_get_clean_index_task,
    get_cleanup_status,
)
from hybrid_retrieval import hybrid_search
from deepseek_extractor import (
    is_meaningful_business_text,
    classify_atom_issues,
    evaluate_business_importance,
)
from auth import (
    verify_password,
    create_session,
    destroy_session,
    get_current_user,
    require_admin,
)
from eligibility import (
    build_eligibility_sql,
    check_knowledge_eligibility,
    check_document_access,
)
from models import (
    LoginRequest,
    LoginResponse,
    UserResponse,
    KnowledgeOverviewResponse,
    SystemStatusResponse,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if ENABLE_DEMO_SEED:
        seed_data(force=False)
    recover_interrupted_tasks()
    # M02-E：有效期到期没有显式事件，服务启动时扫描一次，之后定时扫描（R5 / FR14）
    rc.start_periodic_scan()
    yield

app = FastAPI(
    title="知行有策 服务端",
    description="面向物业咨询业务的知识资产与 AI Skill 生产平台后端服务",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from consult import router as consult_router
app.include_router(consult_router)

VALID_PRIMARY_CATEGORIES = ("制度与标准", "方法与工具", "项目案例", "指标数据", "专家经验")
VALID_ATOM_TYPES = ("规则", "判断", "方法", "案例", "指标", "经验")
VALID_BUSINESS_IMPORTANCE = ("critical", "normal", "informational")


def _json_value(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return fallback


def check_confirmation_blockers(
    title: Optional[str],
    statement: Optional[str],
    primary_category: Optional[str],
    evidence_count: int,
    issues: Dict[str, Any],
    review_status: Optional[str] = "pending_review",
) -> List[str]:
    """统一编辑预览、单条确认和批量确认的公共阻断规则"""
    confirmation_blockers: List[str] = []
    t = (title or "").strip()
    s = (statement or "").strip()

    if not t:
        confirmation_blockers.append("标题不能为空")
    if not s or not is_meaningful_business_text(s):
        confirmation_blockers.append("核心陈述不能为空且须具备实质业务内容")
    if primary_category not in VALID_PRIMARY_CATEGORIES:
        confirmation_blockers.append("主分类仍为待分类或无效，确认前必须明确指定五类主分类之一")
    if evidence_count <= 0:
        confirmation_blockers.append("缺少与当前来源版本绑定的原文证据")

    for issue in issues.get("deterministic_errors", []):
        if issue["message"] not in confirmation_blockers:
            confirmation_blockers.append(issue["message"])
    for issue in issues.get("model_doubts", []):
        if issue.get("blocking") and issue["message"] not in confirmation_blockers:
            confirmation_blockers.append(issue["message"])

    return confirmation_blockers


def _evaluate_review_eligibility(
    version: Any, evidence_count: int, source_evidence: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """统一计算单条确认与普通批量确认资格；旧版字面误报只在证据可核实时消除。"""
    quality_flags = _json_value(version["quality_flags_json"], [])
    if source_evidence is not None and "metric_definition_json" in version.keys():
        from structured_quality import filter_resolved_relation_flags
        quality_flags = filter_resolved_relation_flags(
            quality_flags, _json_value(version["metric_definition_json"], None), source_evidence
        )
    field_states = _json_value(version["field_states_json"], {})
    source_anchors = _json_value(version["source_anchors_json"], [])
    issues = classify_atom_issues(quality_flags, field_states, source_anchors)

    v_status = version["review_status"] if "review_status" in version.keys() else "pending_review"
    is_pending = (v_status == "pending_review")

    confirmation_blockers = check_confirmation_blockers(
        title=version["title"],
        statement=version["statement"],
        primary_category=version["primary_category"],
        evidence_count=evidence_count,
        issues=issues,
        review_status=v_status,
    )

    batch_review_reasons: List[str] = []
    if not is_pending:
        batch_review_reasons.append("该版本已确认或不处于待审核状态")
    if (version["business_importance"] or "normal") == "critical":
        batch_review_reasons.append(
            f"属于重点审核项目（{version['importance_rationale'] or '涉及安全、应急或关键操作'}），须逐条人工核对"
        )
    for issue in issues["model_doubts"]:
        if not issue.get("blocking") and issue["message"] not in batch_review_reasons:
            batch_review_reasons.append(f"抽取疑点尚未通过单条人工复核：{issue['message']}")

    return {
        "issues_summary": issues,
        "confirmation_blockers": confirmation_blockers,
        "batch_review_reasons": batch_review_reasons,
        "can_confirm": (not confirmation_blockers) and is_pending,
        "can_batch_confirm": (not confirmation_blockers) and is_pending and not [
            r for r in batch_review_reasons if r != "该版本已确认或不处于待审核状态"
        ],
    }

@app.get("/api/system/status", response_model=SystemStatusResponse)
def get_system_status():
    return {
        "status": "healthy",
        "version": "0.1.0",
        "environment": ENVIRONMENT,
        "database_connected": True,
        "storage_ready": True,
    }

@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    with get_db() as conn:
        user = conn.execute(
            """
            SELECT u.id, u.organization_id, u.username, u.password_hash, u.display_name, u.role, u.account_status, o.name as organization_name
            FROM users u
            JOIN organizations o ON u.organization_id = o.id
            WHERE u.username = ?
            """,
            (req.username.strip(),)
        ).fetchone()

        if not user or not verify_password(req.password, user["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )

        if user["account_status"] != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="账号已被禁用，无法登录",
            )

        token = create_session(user["id"], user["organization_id"])

        return {
            "token": token,
            "user": {
                "id": user["id"],
                "organization_id": user["organization_id"],
                "organization_name": user["organization_name"],
                "username": user["username"],
                "display_name": user["display_name"],
                "role": user["role"],
                "account_status": user["account_status"],
            }
        }

@app.post("/api/auth/logout")
def logout(user: Dict[str, Any] = Depends(get_current_user)):
    token = user.get("session_token")
    if token:
        destroy_session(token)
    return {"message": "退出成功"}

@app.get("/api/auth/me", response_model=UserResponse)
def get_me(user: Dict[str, Any] = Depends(get_current_user)):
    return {
        "id": user["id"],
        "organization_id": user["organization_id"],
        "organization_name": user["organization_name"],
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "account_status": user["account_status"],
    }

@app.get("/api/knowledge/overview", response_model=KnowledgeOverviewResponse)
def get_knowledge_overview(admin_user: Dict[str, Any] = Depends(require_admin)):
    org_id = admin_user["organization_id"]
    with get_db() as conn:
        doc_count = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE organization_id = ? AND is_deleted = 0",
            (org_id,)
        ).fetchone()["c"]

        k_count = conn.execute(
            "SELECT COUNT(*) as c FROM knowledge_items WHERE organization_id = ? AND lifecycle_status != 'deleted' AND (is_excluded = 0 OR is_excluded IS NULL)",
            (org_id,)
        ).fetchone()["c"]

        # 统计待确认/待分类（排除已明确排除的候选）
        pending_count = conn.execute(
            """
            SELECT COUNT(*) as c FROM knowledge_versions kv
            JOIN knowledge_items ki ON kv.item_id = ki.id
            WHERE kv.organization_id = ? AND ki.lifecycle_status != 'deleted'
              AND (ki.is_excluded = 0 OR ki.is_excluded IS NULL)
              AND (kv.review_status = 'pending_review' OR kv.primary_category IS NULL)
            """,
            (org_id,)
        ).fetchone()["c"]

        # 五类统计（排除已明确排除的条目）
        category_rows = conn.execute(
            """
            SELECT kv.primary_category, COUNT(DISTINCT ki.id) as c
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON ki.active_version_id = kv.id
            WHERE ki.organization_id = ? AND ki.lifecycle_status != 'deleted'
              AND (ki.is_excluded = 0 OR ki.is_excluded IS NULL)
            GROUP BY kv.primary_category
            """,
            (org_id,)
        ).fetchall()

        categories = {
            "制度与标准": 0,
            "方法与工具": 0,
            "项目案例": 0,
            "指标数据": 0,
            "专家经验": 0,
        }
        for row in category_rows:
            if row["primary_category"] in categories:
                categories[row["primary_category"]] = row["c"]

        is_empty = (doc_count == 0 and k_count == 0)

        return {
            "document_count": doc_count,
            "knowledge_count": k_count,
            "pending_count": pending_count,
            "category_counts": categories,
            "is_empty": is_empty,
        }

# ==========================================
# 资料导入与文件管理模块 (Stage 2 核心接口)
# ==========================================

@app.get("/api/documents")
def list_documents(admin: Dict[str, Any] = Depends(require_admin)):
    """
    获取当前企业下所有未删除的文档及最新版本、处理状态
    """
    org_id = admin["organization_id"]
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                d.id as id,
                d.title as title,
                d.active_version_id as active_version_id,
                d.access_scope as access_scope,
                d.created_at as created_at,
                d.updated_at as updated_at,
                (SELECT COUNT(*) FROM document_versions WHERE document_id = d.id) as version_count,
                COALESCE(act_dv.version_label, lat_dv.version_label, 'v1') as version_label,
                COALESCE(lat_dv.file_name, d.title) as file_name,
                COALESCE(lat_dv.file_type, '') as file_type,
                COALESCE(lat_dv.file_size, 0) as file_size,
                lat_dv.uploaded_at as uploaded_at,
                COALESCE(lat_dv.processing_status, 'queued') as processing_status,
                lat_dv.error_summary as error_summary,
                pt.id as task_id,
                COALESCE(pt.status, 'queued') as task_status,
                COALESCE(pt.attempt_count, 0) as attempt_count,
                COALESCE((SELECT COUNT(*) FROM source_blocks WHERE document_version_id = lat_dv.id), 0) as block_count
            FROM documents d
            LEFT JOIN document_versions act_dv ON d.active_version_id = act_dv.id
            LEFT JOIN document_versions lat_dv ON lat_dv.id = (
                SELECT id FROM document_versions WHERE document_id = d.id ORDER BY uploaded_at DESC, id DESC LIMIT 1
            )
            LEFT JOIN processing_tasks pt ON pt.id = (
                SELECT id FROM processing_tasks WHERE target_id = lat_dv.id ORDER BY created_at DESC LIMIT 1
            )
            WHERE d.organization_id = ? AND d.is_deleted = 0
            ORDER BY d.updated_at DESC
            """,
            (org_id,)
        ).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "title": r["title"],
                "active_version_id": r["active_version_id"],
                "access_scope": r["access_scope"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "version_count": r["version_count"],
                "version_label": r["version_label"] or "v1",
                "file_name": r["file_name"] or r["title"],
                "file_type": r["file_type"] or "",
                "file_size": r["file_size"] or 0,
                "uploaded_at": r["uploaded_at"],
                "processing_status": r["processing_status"] or "queued",
                "error_summary": r["error_summary"],
                "task_id": r["task_id"],
                "task_status": r["task_status"] or "queued",
                "attempt_count": r["attempt_count"] or 0,
                "block_count": r["block_count"] or 0,
            })
        return results

@app.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    duplicate_mode: str = Form("ask"),
    target_document_id: Optional[str] = Form(None),
    admin: Dict[str, Any] = Depends(require_admin)
):
    org_id = admin["organization_id"]
    filename = file.filename or "未命名文件"
    ext = Path(filename).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件格式「{ext}」，仅支持 .pdf, .docx, .txt, .md"
        )

    content = await file.read()
    file_size = len(content)

    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件内容为空，无法导入"
        )

    if file_size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件超过 {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB 上限"
        )

    content_hash = hashlib.sha256(content).hexdigest()

    # 文件基础合法性快速校验
    if ext == ".pdf":
        import pypdf
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise HTTPException(status_code=400, detail="PDF 文件已加密，请解除密码保护后重新上传")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"PDF 文件损坏或无法读取: {str(e)}")
    elif ext == ".docx":
        import zipfile
        if not zipfile.is_zipfile(io.BytesIO(content)):
            raise HTTPException(status_code=400, detail="DOCX 文件损坏或非有效 Word 文档")
    elif ext in (".txt", ".md", ".markdown"):
        decoded = False
        for enc in ["utf-8", "utf-8-sig", "gbk", "gb2312"]:
            try:
                content.decode(enc)
                decoded = True
                break
            except UnicodeDecodeError:
                continue
        if not decoded:
            raise HTTPException(status_code=400, detail="文本文件字符编码不受支持（需 UTF-8 或 GBK 编码）")

    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        # 1. 检查企业内部同内容重复上传
        existing_hash = conn.execute(
            """
            SELECT dv.id as version_id, dv.document_id as document_id, d.title as title
            FROM document_versions dv
            JOIN documents d ON dv.document_id = d.id
            WHERE dv.organization_id = ? AND dv.content_hash = ? AND d.is_deleted = 0
            """,
            (org_id, content_hash)
        ).fetchone()

        if existing_hash:
            return {
                "status": "duplicate_content",
                "message": "检测到企业内已有完全相同内容的文件，已自动定位已有资产",
                "existing_document_id": existing_hash["document_id"],
                "existing_version_id": existing_hash["version_id"],
                "file_name": existing_hash["title"],
            }

        # 2. 检查同名但内容不同的情况
        existing_named = conn.execute(
            """
            SELECT id, title FROM documents
            WHERE organization_id = ? AND title = ? AND is_deleted = 0
            """,
            (org_id, filename)
        ).fetchone()

        if existing_named and duplicate_mode == "ask":
            return {
                "status": "conflict_name",
                "message": "检测到已存在同名资料但内容不同",
                "existing_document_id": existing_named["id"],
                "existing_title": existing_named["title"],
            }

        target_doc_id = target_document_id or (existing_named["id"] if existing_named and duplicate_mode == "new_version" else None)

        if duplicate_mode == "new_version" and target_doc_id:
            # 确认该文档属于本企业且未被删除
            doc = conn.execute(
                "SELECT id, title FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
                (target_doc_id, org_id)
            ).fetchone()
            if not doc:
                raise HTTPException(status_code=404, detail="目标版本关联文件不存在或已被删除")

            doc_id = doc["id"]
            title = doc["title"]
            v_count = conn.execute("SELECT COUNT(*) FROM document_versions WHERE document_id = ?", (doc_id,)).fetchone()[0]
            version_label = f"v{v_count + 1}"
        else:
            # 新建独立文件
            doc_id = f"doc_{uuid.uuid4().hex[:12]}"
            title = filename
            if existing_named and duplicate_mode == "new_document":
                stem = Path(filename).stem
                title = f"{stem} (副本{datetime.now().strftime('%m%d%H%M')}){ext}"
            version_label = "v1"
            conn.execute(
                """
                INSERT INTO documents (id, organization_id, title, active_version_id, access_scope, is_deleted, created_at, updated_at)
                VALUES (?, ?, ?, NULL, 'admin_only', 0, ?, ?)
                """,
                (doc_id, org_id, title, now_iso, now_iso)
            )

        # 创建新版本记录
        version_id = f"ver_{uuid.uuid4().hex[:12]}"
        storage_rel = f"{version_id}{ext}"
        storage_dir = get_storage_dir()
        storage_path = storage_dir / storage_rel
        storage_path.write_bytes(content)

        file_type = ext.lstrip(".")
        conn.execute(
            """
            INSERT INTO document_versions
            (id, document_id, organization_id, version_label, file_name, file_type, file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status, error_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL)
            """,
            (
                version_id,
                doc_id,
                org_id,
                version_label,
                filename,
                file_type,
                file_size,
                content_hash,
                str(storage_path),
                admin["id"],
                now_iso,
            )
        )

        # 更新文档 updated_at（首次上传保持 active_version_id=NULL，替换版本保持原有生效版本不变，PRD FR02）
        conn.execute(
            "UPDATE documents SET updated_at = ? WHERE id = ?",
            (now_iso, doc_id)
        )

        # 创建解析任务
        task_id = f"tsk_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO processing_tasks
            (id, organization_id, target_type, target_id, task_type, status, attempt_count, created_at)
            VALUES (?, ?, 'document_version', ?, 'parse_document', 'queued', 0, ?)
            """,
            (task_id, org_id, version_id, now_iso)
        )

    # 提交异步持久化解析任务
    submit_task(task_id)

    return {
        "status": "success",
        "message": "资料已保存，解析任务已排队",
        "document_id": doc_id,
        "version_id": version_id,
        "task_id": task_id,
        "title": title,
        "version_label": version_label,
    }

@app.get("/api/documents/{document_id}")
def get_document_details(document_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    org_id = admin["organization_id"]
    with get_db() as conn:
        doc = conn.execute(
            "SELECT id, title, active_version_id, access_scope, created_at, updated_at FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()

        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        versions_rows = conn.execute(
            """
            SELECT dv.id, dv.version_label, dv.file_name, dv.file_type, dv.file_size, dv.uploaded_at,
                   dv.processing_status, dv.error_summary,
                   pt.id as task_id, pt.status as task_status, pt.attempt_count,
                   (SELECT COUNT(*) FROM source_blocks WHERE document_version_id = dv.id) as block_count
            FROM document_versions dv
            LEFT JOIN processing_tasks pt ON pt.id = (
                SELECT id FROM processing_tasks WHERE target_id = dv.id ORDER BY created_at DESC LIMIT 1
            )
            WHERE dv.document_id = ?
            ORDER BY dv.uploaded_at DESC
            """,
            (document_id,)
        ).fetchall()

        versions = []
        for v in versions_rows:
            versions.append({
                "id": v["id"],
                "version_label": v["version_label"],
                "file_name": v["file_name"],
                "file_type": v["file_type"],
                "file_size": v["file_size"],
                "uploaded_at": v["uploaded_at"],
                "processing_status": v["processing_status"],
                "error_summary": v["error_summary"],
                "task_id": v["task_id"],
                "task_status": v["task_status"],
                "attempt_count": v["attempt_count"] or 0,
                "block_count": v["block_count"] or 0,
            })

        return {
            "id": doc["id"],
            "title": doc["title"],
            "active_version_id": doc["active_version_id"],
            "access_scope": doc["access_scope"],
            "created_at": doc["created_at"],
            "updated_at": doc["updated_at"],
            "versions": versions,
        }

@app.get("/api/documents/{document_id}/versions/{version_id}/source-blocks")
def get_source_blocks(document_id: str, version_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    org_id = admin["organization_id"]
    with get_db() as conn:
        doc = conn.execute(
            "SELECT id FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        blocks = conn.execute(
            """
            SELECT id, block_index, block_type, heading_path, page_number, paragraph_anchor, text_content
            FROM source_blocks
            WHERE document_version_id = ? AND organization_id = ?
            ORDER BY block_index ASC
            """,
            (version_id, org_id)
        ).fetchall()

        return [dict(b) for b in blocks]

@app.get("/api/documents/{document_id}/versions/{version_id}/file")
def get_original_file(
    document_id: str,
    version_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    org_id = current_user["organization_id"]
    with get_db() as conn:
        allowed, err_msg = check_document_access(conn, document_id, current_user, version_id=version_id)
        if not allowed:
            doc = conn.execute("SELECT id, organization_id, is_deleted FROM documents WHERE id = ?", (document_id,)).fetchone()
            if not doc or doc["organization_id"] != org_id or doc["is_deleted"] != 0:
                raise HTTPException(status_code=404, detail="资料不存在或已被删除")
            raise HTTPException(status_code=403, detail=err_msg or "无权访问此文件")

        ver = conn.execute(
            "SELECT file_name, file_type, storage_reference FROM document_versions WHERE id = ? AND document_id = ? AND organization_id = ?",
            (version_id, document_id, org_id)
        ).fetchone()
        if not ver:
            raise HTTPException(status_code=404, detail="版本文件不存在")

    path = resolve_storage_path(ver["storage_reference"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="物理存储文件丢失")

    media_types = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain; charset=utf-8",
        "md": "text/markdown; charset=utf-8",
    }
    media_type = media_types.get(ver["file_type"], "application/octet-stream")

    return FileResponse(
        path=str(path),
        filename=ver["file_name"],
        media_type=media_type,
    )

@app.post("/api/documents/{document_id}/versions/{version_id}/retry")
def retry_parse_task(document_id: str, version_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    org_id = admin["organization_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        doc = conn.execute(
            "SELECT id FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        ver = conn.execute(
            "SELECT id FROM document_versions WHERE id = ? AND document_id = ? AND organization_id = ?",
            (version_id, document_id, org_id)
        ).fetchone()
        if not ver:
            raise HTTPException(status_code=404, detail="版本不存在")

        # 检查是否已成功解析出 source_blocks
        blocks_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM source_blocks WHERE document_version_id = ?",
            (version_id,)
        ).fetchone()["cnt"]

        # 查询该版本关联的历史任务
        tasks = conn.execute(
            "SELECT id, task_type, status FROM processing_tasks WHERE target_id = ? ORDER BY created_at DESC",
            (version_id,)
        ).fetchall()

        extract_task = next((t for t in tasks if t["task_type"] == "extract_atoms"), None)
        parse_task = next((t for t in tasks if t["task_type"] == "parse_document"), None)

        if blocks_count > 0 and extract_task:
            # 结构块已存在，精准重试抽取任务以触发断点检查点复用
            task_id = extract_task["id"]
            conn.execute(
                "UPDATE processing_tasks SET status = 'queued', error_message = NULL WHERE id = ?",
                (task_id,)
            )
        elif parse_task:
            task_id = parse_task["id"]
            conn.execute(
                "UPDATE processing_tasks SET status = 'queued', error_message = NULL WHERE id = ?",
                (task_id,)
            )
        else:
            task_id = f"tsk_{uuid.uuid4().hex[:12]}"
            task_type = "extract_atoms" if blocks_count > 0 else "parse_document"
            conn.execute(
                """
                INSERT INTO processing_tasks (id, organization_id, target_type, target_id, task_type, status, attempt_count, created_at)
                VALUES (?, ?, 'document_version', ?, ?, 'queued', 0, ?)
                """,
                (task_id, org_id, version_id, task_type, now_iso)
            )

        conn.execute(
            "UPDATE document_versions SET processing_status = 'queued', error_summary = NULL WHERE id = ?",
            (version_id,)
        )

    submit_task(task_id)
    return {"message": "解析任务已重新排队", "task_id": task_id}

@app.put("/api/documents/{document_id}/access-scope")
def update_document_access_scope(
    document_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    修改文件访问权限范围（PRD 3.2, Stage 4A）：
    - admin_only -> org_internal：放宽，但不自动把已有 admin_only 知识放宽。
    - org_internal -> admin_only：收紧，所有派生知识对普通成员立即失效，无需等待索引清理。
    - 记录必要审计日志，不记录全文与密钥。
    """
    org_id = admin["organization_id"]
    new_scope = payload.get("access_scope")
    if new_scope not in ("admin_only", "org_internal"):
        raise HTTPException(status_code=400, detail="非法 access_scope 值，仅支持 admin_only 或 org_internal")

    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        doc = conn.execute(
            "SELECT id, organization_id, access_scope FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        old_scope = doc["access_scope"]
        if old_scope != new_scope:
            conn.execute(
                "UPDATE documents SET access_scope = ?, updated_at = ? WHERE id = ?",
                (new_scope, now_iso, document_id)
            )
            if new_scope == "admin_only":
                conn.execute(
                    "UPDATE knowledge_items SET access_scope = 'admin_only', updated_at = ? WHERE document_id = ? AND organization_id = ? AND access_scope = 'org_internal'",
                    (now_iso, document_id, org_id)
                )
                # M02-E（R4 / R5）：引用该文件派生知识的 Skill 重新推导可见范围并按 FR14 处理
                rc.notify_document_change(conn, org_id, document_id, "scope_tightened", admin["id"])
            conn.execute(
                """
                INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
                VALUES (?, ?, ?, 'update_document_access_scope', 'document', ?, ?, ?)
                """,
                (
                    f"aud_{uuid.uuid4().hex[:12]}",
                    org_id,
                    admin["id"],
                    document_id,
                    json.dumps({"from": old_scope, "to": new_scope}, ensure_ascii=False),
                    now_iso,
                )
            )

    return {
        "message": f"文件访问权限已成功更新为「{'企业内部可用' if new_scope == 'org_internal' else '管理员专享'}」",
        "document_id": document_id,
        "access_scope": new_scope,
        "previous_scope": old_scope,
    }

@app.post("/api/documents/{document_id}/versions/{version_id}/activate")
def activate_document_version(
    document_id: str,
    version_id: str,
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    替换文件版本执行“启用此文件版本”（PRD FR02, Stage 4A）：
    前置门槛：
    1. 全部候选须已确认或明确排除（无 pending_review 且未排除候选，且分类明确）。
    2. 拟启用知识全部 index ready。
    3. 至少有一条可用知识。
    在事务中一次性切换 documents.active_version_id，旧版本平滑退出正式检索，记录审计日志。
    """
    org_id = admin["organization_id"]
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        doc = conn.execute(
            "SELECT id, organization_id, active_version_id, is_deleted FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        ver = conn.execute(
            "SELECT id, document_id, version_label, processing_status FROM document_versions WHERE id = ? AND document_id = ? AND organization_id = ?",
            (version_id, document_id, org_id)
        ).fetchone()
        if not ver:
            raise HTTPException(status_code=404, detail="目标文件版本不存在")

        if doc["active_version_id"] == version_id:
            return {
                "message": f"文件版本 {ver['version_label']} 已是当前正式生效版本",
                "document_id": document_id,
                "active_version_id": version_id,
            }

        candidates = conn.execute(
            """
            SELECT
                ki.id as item_id,
                ki.lifecycle_status,
                ki.is_excluded,
                kv.id as version_id,
                kv.review_status,
                kv.index_status,
                kv.primary_category
            FROM knowledge_versions kv
            JOIN knowledge_items ki ON kv.item_id = ki.id
            WHERE kv.source_document_version_id = ? AND kv.organization_id = ?
            """,
            (version_id, org_id)
        ).fetchall()

        if not candidates:
            raise HTTPException(
                status_code=400,
                detail="启用失败：该文件版本尚未提取或生成任何知识候选，无法作为正式版本启用"
            )

        unresolved = []
        for c in candidates:
            is_exc = (c["is_excluded"] == 1)
            if not is_exc:
                if c["review_status"] != "confirmed" or not c["primary_category"]:
                    unresolved.append(c["item_id"])

        if unresolved:
            raise HTTPException(
                status_code=400,
                detail=f"启用失败：存在未完成核对的候选知识（共 {len(unresolved)} 项待确认或待分类），全部候选须已确认或明确排除后方可启用文件版本"
            )

        not_ready = []
        for c in candidates:
            is_exc = (c["is_excluded"] == 1)
            if not is_exc and c["index_status"] != "ready":
                not_ready.append(c["item_id"])

        if not_ready:
            raise HTTPException(
                status_code=400,
                detail=f"启用失败：存在尚未完成索引构建的知识（共 {len(not_ready)} 项），拟启用知识必须全部 index ready 方可启用文件版本"
            )

        available_count = sum(
            1 for c in candidates
            if (c["is_excluded"] != 1)
            and c["lifecycle_status"] == "active"
            and c["review_status"] == "confirmed"
            and c["index_status"] == "ready"
        )
        if available_count == 0:
            raise HTTPException(
                status_code=400,
                detail="启用失败：该文件版本无可用的正式知识（全部被排除或停用），至少须有一条可用知识方可启用文件版本"
            )

        conn.execute(
            "UPDATE documents SET active_version_id = ?, updated_at = ? WHERE id = ?",
            (version_id, now_iso, document_id)
        )
        if doc["active_version_id"]:
            # M02-E（R5）：旧文件版本派生的知识退出正式资格，引用它们的 Skill 按 FR14 处理
            rc.notify_document_change(conn, org_id, document_id, "source_replaced", admin["id"],
                                      {"previous_document_version_id": doc["active_version_id"]})

        conn.execute(
            """
            INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
            VALUES (?, ?, ?, 'activate_document_version', 'document', ?, ?, ?)
            """,
            (
                f"aud_{uuid.uuid4().hex[:12]}",
                org_id,
                admin["id"],
                document_id,
                json.dumps({
                    "previous_active_version_id": doc["active_version_id"],
                    "new_active_version_id": version_id,
                    "version_label": ver["version_label"],
                    "available_count": available_count,
                }, ensure_ascii=False),
                now_iso,
            )
        )

    return {
        "message": f"文件版本 {ver['version_label']} 已成功启用，正式检索已统一切换为新版本",
        "document_id": document_id,
        "active_version_id": version_id,
        "available_knowledge_count": available_count,
    }

@app.get("/api/documents/{document_id}/deletion-impact")
def get_document_deletion_impact(
    document_id: str,
    admin: Dict[str, Any] = Depends(require_admin)
):
    """删除前返回真实影响数据；Skill 引用数来自 Skill 工厂引用关系表（M01-C4 / R7）。"""
    org_id = admin["organization_id"]
    with get_db() as conn:
        doc = conn.execute(
            "SELECT id, title FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        version_count = conn.execute(
            "SELECT COUNT(*) FROM document_versions WHERE document_id = ? AND organization_id = ?",
            (document_id, org_id)
        ).fetchone()[0]
        knowledge_count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE document_id = ? AND organization_id = ? AND lifecycle_status != 'deleted'",
            (document_id, org_id)
        ).fetchone()[0]
        retrieval_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM retrieval_records rr
            JOIN knowledge_versions kv ON kv.id = rr.knowledge_version_id
            JOIN knowledge_items ki ON ki.id = kv.item_id
            WHERE ki.document_id = ? AND ki.organization_id = ?
            """,
            (document_id, org_id)
        ).fetchone()[0]
        active_task_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM processing_tasks pt
            WHERE pt.status IN ('queued', 'running')
              AND (
                pt.target_id IN (
                    SELECT id FROM document_versions
                    WHERE document_id = ? AND organization_id = ?
                )
                OR pt.target_id IN (
                    SELECT kv.id
                    FROM knowledge_versions kv
                    JOIN knowledge_items ki ON ki.id = kv.item_id
                    WHERE ki.document_id = ? AND ki.organization_id = ?
                )
              )
            """,
            (document_id, org_id, document_id, org_id)
        ).fetchone()[0]

        related_case_refs = 0
        rows = conn.execute(
            """
            SELECT kv.related_cases_json
            FROM knowledge_versions kv
            JOIN knowledge_items ki ON ki.id = kv.item_id
            WHERE ki.document_id = ? AND ki.organization_id = ?
            """,
            (document_id, org_id)
        ).fetchall()
        for row in rows:
            try:
                related_case_refs += len(json.loads(row["related_cases_json"] or "[]"))
            except (TypeError, json.JSONDecodeError):
                pass

        # M01-C4 / R7：被多少个 Skill 引用（按各 Skill 当前版本统计）
        skill_refs = rc.count_skill_references(conn, org_id, [
            r["id"] for r in conn.execute(
                "SELECT id FROM knowledge_items WHERE document_id = ? AND organization_id = ?", (document_id, org_id)
            ).fetchall()
        ])

        return {
            "document_id": document_id,
            "title": doc["title"],
            "version_count": int(version_count or 0),
            "derived_knowledge_count": int(knowledge_count or 0),
            "retrieval_record_count": int(retrieval_count or 0),
            "active_task_count": int(active_task_count or 0),
            "related_case_reference_count": related_case_refs,
            "skill_reference_count": skill_refs["count"],
            "skill_reference_status_counts": skill_refs["status_counts"],
            "skill_references": skill_refs["skills"],
            "physical_delete_scheduled": False,
        }


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    org_id = admin["organization_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    cleanup_task_ids: List[str] = []
    with get_db() as conn:
        doc = conn.execute(
            "SELECT id, title FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        document_version_ids = [
            row["id"] for row in conn.execute(
                "SELECT id FROM document_versions WHERE document_id = ? AND organization_id = ?",
                (document_id, org_id)
            ).fetchall()
        ]
        knowledge_version_ids = [
            row["id"] for row in conn.execute(
                """
                SELECT kv.id
                FROM knowledge_versions kv
                JOIN knowledge_items ki ON ki.id = kv.item_id
                WHERE ki.document_id = ? AND ki.organization_id = ?
                """,
                (document_id, org_id)
            ).fetchall()
        ]
        knowledge_item_ids = [
            row["id"] for row in conn.execute(
                "SELECT id FROM knowledge_items WHERE document_id = ? AND organization_id = ? AND lifecycle_status != 'deleted'",
                (document_id, org_id)
            ).fetchall()
        ]
        derived_knowledge_count = len(knowledge_item_ids)

        # 逻辑删除文档
        conn.execute(
            "UPDATE documents SET is_deleted = 1, deleted_at = ?, deleted_by = ?, updated_at = ? WHERE id = ?",
            (now_iso, admin["id"], now_iso, document_id)
        )

        # 级联逻辑删除该文档关联的全部派生知识条目
        if knowledge_item_ids:
            placeholders_ki = ",".join("?" for _ in knowledge_item_ids)
            conn.execute(
                f"""
                UPDATE knowledge_items
                SET lifecycle_status = 'deleted',
                    deleted_at = ?,
                    deleted_by = ?,
                    updated_at = ?
                WHERE id IN ({placeholders_ki})
                """,
                [now_iso, admin["id"], now_iso, *knowledge_item_ids]
            )

        all_targets = document_version_ids + knowledge_version_ids
        if all_targets:
            placeholders = ",".join("?" for _ in all_targets)
            conn.execute(
                f"""
                UPDATE processing_tasks
                SET status = 'cancelled', error_message = COALESCE(error_message, '来源文件已删除'), completed_at = ?
                WHERE target_id IN ({placeholders})
                  AND task_type != 'clean_index'
                  AND status IN ('queued', 'running')
                """,
                [now_iso, *all_targets]
            )

        # M02-E（R5）：引用该文件派生知识的 Skill 按 FR14 处理
        rc.notify_atom_change(conn, org_id, knowledge_item_ids, "deleted", admin["id"],
                              {"source": "document", "document_id": document_id})

        # 检索记录作为派生缓存异步清理；即使清理失败，documents.is_deleted 仍永久阻断检索资格。
        for version_id in knowledge_version_ids:
            result = create_or_get_clean_index_task(conn, version_id, org_id, retry_failed=False)
            if result["should_submit"]:
                cleanup_task_ids.append(result["task_id"])

        conn.execute(
            """
            INSERT INTO audit_logs
            (id, organization_id, user_id, action, target_type, target_id, details, created_at)
            VALUES (?, ?, ?, 'delete_document', 'document', ?, ?, ?)
            """,
            (
                f"aud_{uuid.uuid4().hex[:12]}", org_id, admin["id"], document_id,
                json.dumps({
                    "version_count": len(document_version_ids),
                    "derived_knowledge_count": int(derived_knowledge_count or 0),
                    "cleanup_task_count": len(cleanup_task_ids),
                    "result": "logical_delete_committed",
                }, ensure_ascii=False),
                now_iso,
            )
        )

    for task_id in cleanup_task_ids:
        submit_task(task_id)

    return {
        "message": (
            f"资料「{doc['title']}」已停止使用并完成逻辑删除；"
            + ("检索索引正在后台清理" if cleanup_task_ids else "无待清理检索索引")
        ),
        "document_id": document_id,
        "version_count": len(document_version_ids),
        "derived_knowledge_count": int(derived_knowledge_count or 0),
        "cleanup_task_count": len(cleanup_task_ids),
        "cleanup_status": "pending" if cleanup_task_ids else "not_needed",
    }

# ==========================================
# 知识原子整理与校对模块 (Stage 3 核心接口)
# ==========================================

@app.post("/api/documents/{document_id}/versions/{version_id}/extract")
def trigger_atom_extraction(document_id: str, version_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    """
    手动或重新触发某文件版本的知识原子提炼任务
    """
    org_id = admin["organization_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        doc = conn.execute(
            "SELECT id FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        ver = conn.execute(
            "SELECT id FROM document_versions WHERE id = ? AND document_id = ? AND organization_id = ?",
            (version_id, document_id, org_id)
        ).fetchone()
        if not ver:
            raise HTTPException(status_code=404, detail="版本不存在")

        task_id = f"task_extract_{uuid.uuid4().hex[:10]}"
        conn.execute(
            """
            INSERT INTO processing_tasks (id, organization_id, target_type, target_id, task_type, status, attempt_count, created_at)
            VALUES (?, ?, 'document_version', ?, 'extract_atoms', 'queued', 0, ?)
            """,
            (task_id, org_id, version_id, now_iso)
        )
        conn.execute(
            "UPDATE document_versions SET processing_status = 'extracting', error_summary = NULL WHERE id = ?",
            (version_id,)
        )

    submit_task(task_id)
    return {"message": "知识原子抽取任务已投递排队", "task_id": task_id}

@app.get("/api/knowledge/items")
def list_knowledge_items(
    document_id: Optional[str] = None,
    category: Optional[str] = None,
    review_status: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    search: Optional[str] = None,
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    获取知识原子条目列表及真实分类、审核状态统计数据。
    严格隔离跨企业数据，不显示已删除或已删除文档下的条目。
    支持 lifecycle_status 筛选（active/disabled/all）。
    """
    org_id = admin["organization_id"]

    with get_db() as conn:
        # 1. 基础查询条件
        where_clauses = [
            "ki.organization_id = ?",
            "ki.lifecycle_status != 'deleted'",
            "(ki.is_excluded = 0 OR ki.is_excluded IS NULL)",
            "d.is_deleted = 0"
        ]
        params: List[Any] = [org_id]

        if document_id:
            where_clauses.append("ki.document_id = ?")
            params.append(document_id)

        if category:
            if category == "unclassified":
                where_clauses.append("kv.primary_category IS NULL")
            else:
                where_clauses.append("kv.primary_category = ?")
                params.append(category)

        if review_status:
            where_clauses.append("kv.review_status = ?")
            params.append(review_status)

        if lifecycle_status and lifecycle_status != "all":
            where_clauses.append("ki.lifecycle_status = ?")
            params.append(lifecycle_status)

        if search:
            where_clauses.append("(kv.title LIKE ? OR kv.statement LIKE ? OR kv.content LIKE ?)")
            term = f"%{search.strip()}%"
            params.extend([term, term, term])

        query = f"""
            SELECT
                ki.id, ki.document_id, ki.access_scope, ki.lifecycle_status, ki.created_at, ki.updated_at,
                d.title as document_title,
                kv.id as active_version_id, kv.source_document_version_id, kv.version_number,
                kv.title, kv.content, kv.primary_category, kv.atom_type, kv.subject, kv.statement,
                kv.conditions_json, kv.actions_json, kv.exceptions_json, kv.metric_definition_json, kv.case_details_json,
                kv.field_states_json, kv.quality_flags_json, kv.customer_types_json, kv.business_scenes_json, kv.problem_tags_json,
                kv.source_anchors_json, kv.valid_from, kv.valid_until, kv.review_status, kv.index_status, kv.revision_token,
                kv.extraction_context_json, kv.related_cases_json,
                (SELECT COUNT(*) FROM knowledge_evidence WHERE knowledge_version_id = kv.id) as evidence_count,
                CASE WHEN ki.pending_version_id IS NOT NULL THEN 1 ELSE 0 END as has_draft_version,
                (SELECT version_number FROM knowledge_versions WHERE id = ki.pending_version_id LIMIT 1) as draft_version_number,
                (SELECT review_status FROM knowledge_versions WHERE id = ki.pending_version_id LIMIT 1) as pending_review_status,
                (SELECT index_status FROM knowledge_versions WHERE id = ki.pending_version_id LIMIT 1) as pending_index_status
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON ki.active_version_id = kv.id
            JOIN documents d ON ki.document_id = d.id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY
                CASE WHEN ki.lifecycle_status = 'disabled' THEN 1 ELSE 0 END ASC,
                kv.review_status ASC,
                ki.updated_at DESC
        """
        rows = conn.execute(query, params).fetchall()

        items = []
        for r in rows:
            items.append({
                "id": r["id"],
                "document_id": r["document_id"],
                "document_title": r["document_title"],
                "access_scope": r["access_scope"],
                "lifecycle_status": r["lifecycle_status"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "active_version_id": r["active_version_id"],
                "source_document_version_id": r["source_document_version_id"],
                "version_number": r["version_number"],
                "title": r["title"],
                "content": r["content"],
                "primary_category": r["primary_category"],
                "atom_type": r["atom_type"],
                "subject": r["subject"],
                "statement": r["statement"],
                "conditions": json.loads(r["conditions_json"] or "[]"),
                "actions": json.loads(r["actions_json"] or "[]"),
                "exceptions": json.loads(r["exceptions_json"] or "[]"),
                "metric_definition": json.loads(r["metric_definition_json"]) if r["metric_definition_json"] else None,
                "case_details": json.loads(r["case_details_json"]) if r["case_details_json"] else None,
                "field_states": json.loads(r["field_states_json"] or "{}"),
                "quality_flags": json.loads(r["quality_flags_json"] or "[]"),
                "customer_types": json.loads(r["customer_types_json"] or "[]"),
                "business_scenes": json.loads(r["business_scenes_json"] or "[]"),
                "problem_tags": json.loads(r["problem_tags_json"] or "[]"),
                "source_anchors": json.loads(r["source_anchors_json"] or "[]"),
                "valid_from": r["valid_from"],
                "valid_until": r["valid_until"],
                "review_status": r["review_status"],
                "index_status": r["index_status"],
                "revision_token": r["revision_token"],
                "extraction_context": json.loads(r["extraction_context_json"] or "{}"),
                "related_cases": json.loads(r["related_cases_json"] or "[]"),
                "evidence_count": r["evidence_count"] or 0,
                "has_draft_version": bool(r["has_draft_version"]),
                "draft_version_number": r["draft_version_number"],
                "pending_review_status": r["pending_review_status"],
                "pending_index_status": r["pending_index_status"],
            })

        # 2. 全量统计（基于当前企业下未删除且未排除的有效资料范围）
        scope_where = [
            "ki.organization_id = ?",
            "ki.lifecycle_status != 'deleted'",
            "(ki.is_excluded = 0 OR ki.is_excluded IS NULL)",
            "d.is_deleted = 0"
        ]
        scope_params = [org_id]
        if document_id:
            scope_where.append("ki.document_id = ?")
            scope_params.append(document_id)

        stats_query = f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN kv.primary_category = '制度与标准' THEN 1 ELSE 0 END) as c_rule,
                SUM(CASE WHEN kv.primary_category = '方法与工具' THEN 1 ELSE 0 END) as c_method,
                SUM(CASE WHEN kv.primary_category = '项目案例' THEN 1 ELSE 0 END) as c_case,
                SUM(CASE WHEN kv.primary_category = '指标数据' THEN 1 ELSE 0 END) as c_metric,
                SUM(CASE WHEN kv.primary_category = '专家经验' THEN 1 ELSE 0 END) as c_exp,
                SUM(CASE WHEN kv.primary_category IS NULL THEN 1 ELSE 0 END) as c_unclassified,
                SUM(CASE WHEN kv.review_status = 'pending_review' THEN 1 ELSE 0 END) as c_pending,
                SUM(CASE WHEN kv.review_status = 'confirmed' THEN 1 ELSE 0 END) as c_confirmed,
                SUM(CASE WHEN ki.lifecycle_status = 'active' THEN 1 ELSE 0 END) as c_active,
                SUM(CASE WHEN ki.lifecycle_status = 'disabled' THEN 1 ELSE 0 END) as c_disabled
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON ki.active_version_id = kv.id
            JOIN documents d ON ki.document_id = d.id
            WHERE {' AND '.join(scope_where)}
        """
        stat_row = conn.execute(stats_query, scope_params).fetchone()

        stats = {
            "total": stat_row["total"] or 0,
            "category_counts": {
                "制度与标准": stat_row["c_rule"] or 0,
                "方法与工具": stat_row["c_method"] or 0,
                "项目案例": stat_row["c_case"] or 0,
                "指标数据": stat_row["c_metric"] or 0,
                "专家经验": stat_row["c_exp"] or 0,
            },
            "unclassified_count": stat_row["c_unclassified"] or 0,
            "pending_review_count": stat_row["c_pending"] or 0,
            "confirmed_count": stat_row["c_confirmed"] or 0,
            "active_count": stat_row["c_active"] or 0,
            "disabled_count": stat_row["c_disabled"] or 0,
        }

        return {
            "items": items,
            "stats": stats,
        }

@app.get("/api/knowledge/items/{item_id}")
def get_knowledge_item_detail(item_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    """
    获取单个知识原子的完整详情、原文证据（带对应结构块正文与锚点）及版本记录。
    支持版本草稿隔离：若存在待核对的新版本草稿，优先展示草稿供校对，并标明正在生效的线上版本号。
    """
    org_id = admin["organization_id"]
    with get_db() as conn:
        item_row = conn.execute(
            """
            SELECT ki.id, ki.document_id, ki.active_version_id as serving_version_id, ki.pending_version_id,
                   ki.access_scope, ki.lifecycle_status, ki.created_at, ki.updated_at,
                   d.title as document_title, d.is_deleted as doc_is_deleted, d.access_scope as document_access_scope,
                   kv.id as active_version_id, kv.source_document_version_id, kv.version_number,
                   kv.title, kv.content, kv.primary_category, kv.atom_type, kv.subject, kv.statement,
                   kv.conditions_json, kv.actions_json, kv.exceptions_json, kv.metric_definition_json, kv.case_details_json,
                   kv.field_states_json, kv.quality_flags_json, kv.customer_types_json, kv.business_scenes_json, kv.problem_tags_json,
                   kv.source_anchors_json, kv.valid_from, kv.valid_until, kv.review_status, kv.reviewed_by, kv.reviewed_at,
                   kv.index_status, kv.revision_token, kv.extraction_context_json, kv.related_cases_json,
                   kv.business_importance, kv.importance_rationale, kv.importance_adjusted_by, kv.created_by,
                   dv.version_label as document_version_label, dv.file_name as document_file_name
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON ki.active_version_id = kv.id
            JOIN documents d ON ki.document_id = d.id
            JOIN document_versions dv ON kv.source_document_version_id = dv.id
            WHERE ki.id = ? AND ki.organization_id = ? AND ki.lifecycle_status != 'deleted' AND d.is_deleted = 0
            """,
            (item_id, org_id)
        ).fetchone()

        if not item_row:
            raise HTTPException(status_code=404, detail="知识条目不存在或已被删除")

        # 待切换版本在确认后、索引中或索引失败时仍需可见，不能仅按 pending_review 查找。
        draft_row = None
        if item_row["pending_version_id"]:
            draft_row = conn.execute(
                """
                SELECT kv.*, dv.version_label as document_version_label, dv.file_name as document_file_name
                FROM knowledge_versions kv
                JOIN document_versions dv ON kv.source_document_version_id = dv.id
                WHERE kv.id = ? AND kv.item_id = ? AND kv.organization_id = ?
                """,
                (item_row["pending_version_id"], item_id, org_id)
            ).fetchone()

        target_v = draft_row if draft_row else item_row
        target_version_id = draft_row["id"] if draft_row else item_row["active_version_id"]
        is_draft = bool(draft_row and draft_row["id"] != item_row["active_version_id"])
        serving_version_num = item_row["version_number"] if is_draft else None

        # 查询关联的证据与对应原文结构块正文
        evidence_rows = conn.execute(
            """
            SELECT ke.id, ke.field_name, ke.excerpt, ke.accuracy_level,
                   sb.id as source_block_id, sb.block_index, sb.block_type, sb.heading_path,
                   sb.page_number, sb.paragraph_anchor, sb.text_content
            FROM knowledge_evidence ke
            JOIN source_blocks sb ON ke.source_block_id = sb.id
            WHERE ke.knowledge_version_id = ? AND ke.organization_id = ?
            ORDER BY sb.block_index ASC
            """,
            (target_version_id, org_id)
        ).fetchall()

        if not evidence_rows and is_draft:
            # 草稿未独立复制证据时，读取原版本证据
            evidence_rows = conn.execute(
                """
                SELECT ke.id, ke.field_name, ke.excerpt, ke.accuracy_level,
                       sb.id as source_block_id, sb.block_index, sb.block_type, sb.heading_path,
                       sb.page_number, sb.paragraph_anchor, sb.text_content
                FROM knowledge_evidence ke
                JOIN source_blocks sb ON ke.source_block_id = sb.id
                WHERE ke.knowledge_version_id = ? AND ke.organization_id = ?
                ORDER BY sb.block_index ASC
                """,
                (item_row["active_version_id"], org_id)
            ).fetchall()

        evidence_list = []
        for ev in evidence_rows:
            evidence_list.append({
                "id": ev["id"],
                "field_name": ev["field_name"],
                "excerpt": ev["excerpt"],
                "accuracy_level": ev["accuracy_level"],
                "source_block_id": ev["source_block_id"],
                "block_index": ev["block_index"],
                "block_type": ev["block_type"],
                "heading_path": ev["heading_path"],
                "page_number": ev["page_number"],
                "paragraph_anchor": ev["paragraph_anchor"],
                "text_content": ev["text_content"],
            })

        # 展示旧候选时复核已知的字面误报；不改动原版本、原始质量记录或证据。
        from structured_quality import filter_resolved_relation_flags
        display_quality_flags = filter_resolved_relation_flags(
            json.loads(target_v["quality_flags_json"] or "[]"),
            json.loads(target_v["metric_definition_json"]) if target_v["metric_definition_json"] else None,
            evidence_list,
        )

        # 查询历史版本列表
        version_history_rows = conn.execute(
            """
            SELECT id, version_number, title, primary_category, review_status, index_status, created_at, created_by
            FROM knowledge_versions
            WHERE item_id = ? AND organization_id = ?
            ORDER BY version_number DESC
            """,
            (item_id, org_id)
        ).fetchall()

        history = [dict(v) for v in version_history_rows]
        eligibility = _evaluate_review_eligibility(target_v, len(evidence_list), evidence_list)
        jev_evaluation = serialize_evaluation(conn, target_version_id, target_v["revision_token"])

        return {
            "id": item_row["id"],
            "document_id": item_row["document_id"],
            "document_title": item_row["document_title"],
            "document_version_label": target_v["document_version_label"],
            "document_file_name": target_v["document_file_name"],
            "document_access_scope": item_row["document_access_scope"],
            "access_scope": "admin_only" if item_row["document_access_scope"] == "admin_only" else item_row["access_scope"],
            "lifecycle_status": item_row["lifecycle_status"],
            "created_at": item_row["created_at"],
            "updated_at": item_row["updated_at"],
            "is_draft_version": is_draft,
            "serving_version_number": serving_version_num,
            "active_version": {
                "id": target_version_id,
                "version_number": target_v["version_number"],
                "title": target_v["title"],
                "content": target_v["content"],
                "primary_category": target_v["primary_category"],
                "atom_type": target_v["atom_type"],
                "subject": target_v["subject"],
                "statement": target_v["statement"],
                "conditions": json.loads(target_v["conditions_json"] or "[]"),
                "actions": json.loads(target_v["actions_json"] or "[]"),
                "exceptions": json.loads(target_v["exceptions_json"] or "[]"),
                "metric_definition": json.loads(target_v["metric_definition_json"]) if target_v["metric_definition_json"] else None,
                "case_details": json.loads(target_v["case_details_json"]) if target_v["case_details_json"] else None,
                "field_states": json.loads(target_v["field_states_json"] or "{}"),
                "quality_flags": display_quality_flags,
                "customer_types": json.loads(target_v["customer_types_json"] or "[]"),
                "business_scenes": json.loads(target_v["business_scenes_json"] or "[]"),
                "problem_tags": json.loads(target_v["problem_tags_json"] or "[]"),
                "source_anchors": json.loads(target_v["source_anchors_json"] or "[]"),
                "valid_from": target_v["valid_from"],
                "valid_until": target_v["valid_until"],
                "review_status": target_v["review_status"],
                "reviewed_by": target_v["reviewed_by"],
                "created_by": target_v["created_by"],
                "reviewed_at": target_v["reviewed_at"],
                "index_status": target_v["index_status"],
                "revision_token": target_v["revision_token"],
                "extraction_context": json.loads(target_v["extraction_context_json"] or "{}"),
                "related_cases": json.loads(target_v["related_cases_json"] or "[]"),
                "business_importance": target_v["business_importance"] or "normal",
                "importance_rationale": target_v["importance_rationale"],
                "importance_adjusted_by": target_v["importance_adjusted_by"],
            },
            "evidence": evidence_list,
            "version_history": history,
            "can_confirm": eligibility["can_confirm"],
            "confirmation_blockers": eligibility["confirmation_blockers"],
            "batch_review_reasons": eligibility["batch_review_reasons"],
            "jev_evaluation": jev_evaluation,
        }

@app.post("/api/knowledge/items/{item_id}/structure/retry")
def retry_item_structure(
    item_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin),
):
    """仅管理员手动重试未人工处理的候选；不创建版本、不改已确认内容。"""
    from config import DEEPSEEK_API_KEY
    from deepseek_extractor import extract_atoms_via_deepseek, validate_and_sanitize_atoms
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=503, detail="在线模型未配置；请直接按原文人工核对")
    org_id = admin["organization_id"]
    with get_db() as conn:
        row = conn.execute(
            """SELECT kv.*, ki.document_id, d.title AS document_title
               FROM knowledge_items ki
               JOIN knowledge_versions kv ON kv.id = COALESCE(ki.pending_version_id, ki.active_version_id)
               JOIN documents d ON d.id = ki.document_id
               WHERE ki.id = ? AND ki.organization_id = ? AND kv.organization_id = ?
                 AND d.is_deleted = 0 AND ki.lifecycle_status != 'deleted'""",
            (item_id, org_id, org_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="候选知识不存在")
        if (row["review_status"] != "pending_review" or row["reviewed_by"] is not None
                or row["created_by"] != "system_extractor"):
            raise HTTPException(status_code=409, detail="已人工处理或确认的知识不允许自动覆盖，请手动修改草稿")
        if payload.get("revision_token") != row["revision_token"]:
            raise HTTPException(status_code=409, detail="候选版本已变化，请刷新后操作")
        blocks = [dict(r) for r in conn.execute(
            """SELECT DISTINCT sb.id, sb.block_index, sb.block_type, sb.heading_path,
                      sb.page_number, sb.paragraph_anchor, sb.text_content
               FROM source_blocks sb JOIN knowledge_evidence ke ON ke.source_block_id = sb.id
               WHERE ke.knowledge_version_id = ? AND ke.organization_id = ?
                 AND sb.document_version_id = ?
               ORDER BY sb.block_index""",
            (row["id"], org_id, row["source_document_version_id"]),
        ).fetchall()]
        if not blocks:
            raise HTTPException(status_code=409, detail="原始来源已不可用，禁止重抽")
        original_context = json.loads(row["extraction_context_json"] or "{}")
        version_id, previous_token = row["id"], row["revision_token"]
        old_flags = json.loads(row["quality_flags_json"] or "[]")
        title, doc_title = row["title"], row["document_title"]
    retry_record = {
        "manual_retry_count": int(original_context.get("manual_retry_count") or 0) + 1,
        "manual_retry_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        candidates, _ = extract_atoms_via_deepseek(
            blocks, doc_title,
            batch_context="手动定向重抽原条目「" + title + "」；已知疑点：" +
                          "；".join(old_flags) + "。只返回该条目，保持证据与数字、条件、例外关系。",
        )
        if len(candidates) != 1:
            raise ValueError("模型没有返回唯一的对应条目")
        atoms = validate_and_sanitize_atoms(
            candidates, blocks, document_version_id="source-bound",
        )
        result = atoms[0]
        expected_source_ids = {block["id"] for block in blocks}
        returned_source_ids = {ev["source_block_id"] for ev in result["source_evidence"]
                               if ev["accuracy_level"] in ("exact", "referenced")}
        if expected_source_ids - returned_source_ids:
            raise ValueError("重抽遗漏了原候选的来源段落，保留原始候选")
        if not result["source_evidence"] or any(
            e["accuracy_level"] not in ("exact", "referenced") for e in result["source_evidence"]
        ):
            raise ValueError("重抽来源引用未通过验证")
    except Exception as exc:
        retry_record["manual_retry_result"] = "failed"
        retry_record["manual_retry_reason"] = type(exc).__name__ + "：" + str(exc)[:160]
        with get_db() as conn:
            conn.execute(
                """UPDATE knowledge_versions SET extraction_context_json = ?
                   WHERE id = ? AND organization_id = ? AND revision_token = ?
                     AND review_status = 'pending_review' AND reviewed_by IS NULL
                     AND created_by = 'system_extractor'""",
                (json.dumps({**original_context, **retry_record}, ensure_ascii=False),
                 version_id, org_id, previous_token),
            )
        raise HTTPException(status_code=422, detail="重抽未通过来源校验或模型失败；原候选已保留，请人工核对")
    retry_record["manual_retry_result"] = "needs_review" if result["quality_flags"] else "generated_pending_review"
    new_token = uuid.uuid4().hex
    with get_db() as conn:
        # 重试期间管理员可能已修改/确认；以版本号、审核身份和乐观锁再次阻断覆盖。
        update = conn.execute(
            """UPDATE knowledge_versions SET title=?, content=?, primary_category=?,
               atom_type=?, subject=?, statement=?, conditions_json=?, actions_json=?,
               exceptions_json=?, metric_definition_json=?, case_details_json=?,
               field_states_json=?, quality_flags_json=?, source_anchors_json=?,
               extraction_context_json=?, revision_token=?
               WHERE id=? AND organization_id=? AND revision_token=? AND review_status='pending_review'
                 AND reviewed_by IS NULL AND created_by='system_extractor'""",
            (result["title"], result["content"], result["primary_category"], result["atom_type"],
             result["subject"], result["statement"],
             json.dumps(result["conditions"], ensure_ascii=False),
             json.dumps(result["actions"], ensure_ascii=False),
             json.dumps(result["exceptions"], ensure_ascii=False),
             json.dumps(result["metric_definition"], ensure_ascii=False) if result["metric_definition"] else None,
             json.dumps(result["case_details"], ensure_ascii=False) if result["case_details"] else None,
             json.dumps(result["field_states"], ensure_ascii=False),
             json.dumps(result["quality_flags"], ensure_ascii=False),
             json.dumps(result["source_anchors"], ensure_ascii=False),
             json.dumps({**original_context, **retry_record}, ensure_ascii=False),
             new_token, version_id, org_id, previous_token),
        )
        if update.rowcount != 1:
            raise HTTPException(status_code=409, detail="重试期间候选已被修改，未覆盖用户数据")
        conn.execute("DELETE FROM knowledge_evidence WHERE knowledge_version_id=? AND organization_id=?",
                     (version_id, org_id))
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            """INSERT INTO knowledge_evidence
               (id, knowledge_version_id, source_block_id, organization_id, field_name, excerpt, accuracy_level, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            [(f"ke_{uuid.uuid4().hex[:12]}", version_id, ev["source_block_id"], org_id,
              ev["field_name"], ev["excerpt"], ev["accuracy_level"], now)
             for ev in result["source_evidence"]],
        )
    return {"message": "已重新整理，仍需人工核对；知识未自动启用",
            "quality_flags": result["quality_flags"], "revision_token": new_token}


def _evaluate_edited_draft_flags(
    payload: Dict[str, Any], old_flags: List[str], evidence: List[Dict[str, Any]],
) -> List[str]:
    """编辑预览与正式保存共用同一套无副作用的质量计算，不沿用旧抽取过程的过时疑点。"""
    from structured_quality import structural_quality_flags

    statement = (payload.get("statement") or "").strip()
    category = payload.get("primary_category")
    atom_type = payload.get("atom_type") or "规则"
    actions = payload.get("actions") or []
    metric = payload.get("metric_definition")
    case = payload.get("case_details")
    flags: List[str] = []

    if not statement or len(statement) < 4 or not is_meaningful_business_text(statement):
        flags.append("缺少核心陈述，无法独立理解")
    if category not in VALID_PRIMARY_CATEGORIES:
        flags.append("待管理员确认主分类")
    if category == "指标数据" and not metric:
        flags.append("指标类知识未提供口径、单位或数值范围")
    if category == "项目案例" and not case:
        flags.append("案例类知识未提供背景措施与实际结果")

    # 无法通过编辑正文修复的来源真实性问题继续保留；历史抽取不完整提示不沿用。
    source_errors = ("伪造来源", "来源摘录与原文不匹配", "来源摘录与原文块不匹配",
                     "来源摘录无效", "缺乏有效原文证据")
    for flag in old_flags:
        if any(key in flag for key in source_errors) or "疑似规则冲突" in flag:
            flags.append(flag)

    flags.extend(structural_quality_flags({
        "statement": statement,
        "conditions": payload.get("conditions") or [],
        "actions": actions,
        "exceptions": payload.get("exceptions") or [],
        "primary_category": category,
        "metric_definition": metric,
        "case_details": case,
        "source_evidence": evidence,
    }))
    if any("结构化未完成" in old for old in old_flags):
        if atom_type in ("规则", "方法") and not actions:
            flags.append("结构化未完成：尚未核对并整理执行事项")
        if category == "指标数据" and not (
            metric and (metric.get("rows") or metric.get("criteria"))
        ):
            flags.append("结构化未完成：尚未核对并整理指标要求")
    return list(dict.fromkeys(flags))


@app.post("/api/knowledge/items/{item_id}/draft/validate")
def validate_knowledge_draft_preview(
    item_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin),
):
    """自动质检当前未保存的字段；只读，不创建版本、修改证据或触发模型。"""
    token = payload.get("revision_token")
    if not token:
        raise HTTPException(status_code=400, detail="缺少 revision_token，无法预览校验")
    with get_db() as conn:
        item = conn.execute(
            """SELECT ki.active_version_id, ki.pending_version_id
               FROM knowledge_items ki JOIN documents d ON d.id = ki.document_id
               WHERE ki.id = ? AND ki.organization_id = ? AND ki.lifecycle_status != 'deleted'
                 AND d.is_deleted = 0 AND d.organization_id = ?""",
            (item_id, admin["organization_id"], admin["organization_id"]),
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="知识条目不存在或无权限")
        version_id = item["pending_version_id"] or item["active_version_id"]
        version = conn.execute(
            """SELECT id, title, statement, primary_category, review_status, revision_token, quality_flags_json, source_document_version_id
               FROM knowledge_versions WHERE id = ? AND item_id = ? AND organization_id = ?""",
            (version_id, item_id, admin["organization_id"]),
        ).fetchone()
        if not version or version["review_status"] not in ("pending_review", "confirmed"):
            raise HTTPException(status_code=409, detail="当前知识版本不可编辑，请刷新页面")
        if version["revision_token"] != token:
            raise HTTPException(status_code=409, detail="条目已被修改，请刷新后重新校验")
        evidence = [dict(ev) for ev in conn.execute(
            """SELECT ke.field_name, ke.excerpt, ke.accuracy_level
               FROM knowledge_evidence ke JOIN source_blocks sb ON sb.id = ke.source_block_id
               WHERE ke.knowledge_version_id = ? AND ke.organization_id = ?
                 AND sb.document_version_id = ?""",
            (version_id, admin["organization_id"], version["source_document_version_id"]),
        ).fetchall()]
        flags = _evaluate_edited_draft_flags(
            payload, _json_value(version["quality_flags_json"], []), evidence
        )
        issues = classify_atom_issues(flags, {}, [])
        draft_title = payload.get("title") if "title" in payload else version["title"]
        draft_statement = payload.get("statement") if "statement" in payload else version["statement"]
        draft_category = payload.get("primary_category") if "primary_category" in payload else version["primary_category"]

        blocking = check_confirmation_blockers(
            title=draft_title,
            statement=draft_statement,
            primary_category=draft_category,
            evidence_count=len(evidence),
            issues=issues,
            review_status="pending_review",
        )
        return {
            "quality_flags": flags,
            "blocking": blocking,
            "confirmation_blockers": blocking,
            "can_confirm": not blocking,
            "revision_token": token,
        }


@app.put("/api/knowledge/items/{item_id}/draft")
def save_knowledge_draft(
    item_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    保存草稿：
    1. 乐观锁并发保护：严格校验客户端提交的 revision_token。
    2. 若当前生效版本已确认（confirmed），保存修改自动生成新版本草稿（PRD FR09），
       旧生效版本在未确认新草稿前继续保持可检索服务。
    3. 若条目已有未确认草稿或条目本身为待确认状态，就地更新草稿内容。
    """
    org_id = admin["organization_id"]
    client_token = payload.get("revision_token")
    if not client_token:
        raise HTTPException(status_code=400, detail="缺少 revision_token，无法执行并发安全保存")

    now_iso = datetime.now(timezone.utc).isoformat()
    new_token = uuid.uuid4().hex

    with get_db() as conn:
        curr_item = conn.execute(
            """
            SELECT ki.id, ki.active_version_id, ki.pending_version_id, ki.document_id, ki.access_scope as ki_scope, d.access_scope as doc_scope
            FROM knowledge_items ki
            JOIN documents d ON ki.document_id = d.id
            WHERE ki.id = ? AND ki.organization_id = ? AND ki.lifecycle_status != 'deleted'
            """,
            (item_id, org_id)
        ).fetchone()

        if not curr_item:
            raise HTTPException(status_code=404, detail="知识条目不存在或已被删除")

        new_access_scope = payload.get("access_scope") or curr_item["ki_scope"]
        if curr_item["doc_scope"] == "admin_only":
            # AC25：来源文件权限为管理员专享，知识条目上限自动限制为 admin_only，不因历史旧状态卡死草稿保存
            new_access_scope = "admin_only"

        # 检查是否存在未确认的草稿版本
        draft_row = conn.execute(
            """
            SELECT id, version_number, revision_token, quality_flags_json, field_states_json, source_document_version_id
            FROM knowledge_versions
            WHERE item_id = ? AND organization_id = ? AND review_status = 'pending_review'
              AND (? IS NULL OR id = ?)
            ORDER BY version_number DESC LIMIT 1
            """,
            (item_id, org_id, curr_item["pending_version_id"], curr_item["pending_version_id"])
        ).fetchone()

        # 检查当前生效版本
        active_ver = conn.execute(
            """
            SELECT id, version_number, review_status, revision_token, quality_flags_json, field_states_json, source_document_version_id,
                   valid_from, valid_until, business_importance, importance_rationale, importance_adjusted_by
            FROM knowledge_versions
            WHERE id = ?
            """,
            (curr_item["active_version_id"],)
        ).fetchone()

        target_v_id = None
        is_new_draft_created = False
        old_flags_to_check = []

        if draft_row:
            # 存在未确认草稿，原地更新草稿
            if draft_row["revision_token"] != client_token:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="该知识条目的草稿已被其他操作修改，请刷新并比较最新草稿后再保存（并发冲突保护）"
                )
            target_v_id = draft_row["id"]
            old_flags_to_check = json.loads(draft_row["quality_flags_json"] or "[]")
        elif active_ver and active_ver["review_status"] == "confirmed":
            # 当前版本已生效，修改需产生新版本草稿（PRD FR09），旧版本在未确认前继续服务
            if active_ver["revision_token"] != client_token:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="该知识条目已被其他操作修改，请刷新后再保存（并发冲突保护）"
                )
            target_v_id = f"kv_{uuid.uuid4().hex[:12]}"
            new_ver_num = active_ver["version_number"] + 1
            old_flags_to_check = json.loads(active_ver["quality_flags_json"] or "[]")
            is_new_draft_created = True
        elif active_ver:
            # 当前版本为待核对状态，原地更新
            if active_ver["revision_token"] != client_token:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="该知识条目已被其他操作修改，请刷新后再保存（并发冲突保护）"
                )
            target_v_id = active_ver["id"]
            old_flags_to_check = json.loads(active_ver["quality_flags_json"] or "[]")
        else:
            raise HTTPException(status_code=404, detail="未找到有效知识版本")

        # 处理字段更新
        new_title = (payload.get("title") or "未命名知识条目").strip()
        new_category = payload.get("primary_category")
        if new_category and new_category not in ["制度与标准", "方法与工具", "项目案例", "指标数据", "专家经验"]:
            raise HTTPException(status_code=400, detail=f"非法的主分类「{new_category}」")

        new_atom_type = payload.get("atom_type") or "规则"
        new_subject = payload.get("subject") or "物业责任主体"
        new_statement = (payload.get("statement") or "").strip()
        new_content = payload.get("content") or new_statement

        new_conditions = payload.get("conditions") or []
        new_actions = payload.get("actions") or []
        new_exceptions = payload.get("exceptions") or []
        new_metric = payload.get("metric_definition")
        new_case = payload.get("case_details")

        new_customer_types = payload.get("customer_types") or []
        new_business_scenes = payload.get("business_scenes") or []
        new_problem_tags = payload.get("problem_tags") or []
        if curr_item["doc_scope"] == "admin_only":
            new_access_scope = "admin_only"
        elif "access_scope" in payload:
            new_access_scope = payload.get("access_scope") or new_access_scope
        new_valid_from = payload.get("valid_from")
        new_valid_until = payload.get("valid_until")
        new_related_cases = payload.get("related_cases") or []

        # 有效期属于立即生效的资格控制字段，不等待新正文版本确认/索引。
        # 历史正文仍保持不可变；有效期前后值通过审计日志保留。
        if active_ver and (
            active_ver["valid_from"] != new_valid_from or active_ver["valid_until"] != new_valid_until
        ):
            old_validity = {
                "valid_from": active_ver["valid_from"],
                "valid_until": active_ver["valid_until"],
            }
            conn.execute(
                "UPDATE knowledge_versions SET valid_from = ?, valid_until = ? WHERE id = ?",
                (new_valid_from, new_valid_until, active_ver["id"])
            )
            conn.execute(
                """
                INSERT INTO audit_logs
                (id, organization_id, user_id, action, target_type, target_id, details, created_at)
                VALUES (?, ?, ?, 'update_knowledge_validity', 'knowledge_item', ?, ?, ?)
                """,
                (
                    f"aud_{uuid.uuid4().hex[:12]}", org_id, admin["id"], item_id,
                    json.dumps({
                        "from": old_validity,
                        "to": {"valid_from": new_valid_from, "valid_until": new_valid_until},
                        "serving_version_id": active_ver["id"],
                    }, ensure_ascii=False),
                    now_iso,
                )
            )

        # 保存与自动预览共用当前字段质检，旧版抽取过程提示不继续沿用。
        ev_version = active_ver["id"] if is_new_draft_created else target_v_id
        evidence_for_review = [
            dict(row) for row in conn.execute(
                """SELECT ke.field_name, ke.excerpt, ke.accuracy_level
                   FROM knowledge_evidence ke JOIN source_blocks sb ON sb.id = ke.source_block_id
                   WHERE ke.knowledge_version_id = ? AND ke.organization_id = ?
                     AND sb.document_version_id = ?""",
                (ev_version, org_id, active_ver["source_document_version_id"]),
            ).fetchall()
        ]
        q_flags = _evaluate_edited_draft_flags({
            "statement": new_statement, "primary_category": new_category,
            "atom_type": new_atom_type, "conditions": new_conditions,
            "actions": new_actions, "exceptions": new_exceptions,
            "metric_definition": new_metric, "case_details": new_case,
        }, old_flags_to_check, evidence_for_review)
        # 旧降级条目可通过显式人工修改恢复字段状态；来源不匹配的校验失败不能清除。
        old_source_error = any(
            any(k in flag for k in ("伪造来源", "来源摘录与原文不匹配", "来源摘录与原文块不匹配", "来源摘录无效", "缺乏有效原文证据"))
            for flag in old_flags_to_check
        )
        base_state_row = draft_row if draft_row else active_ver
        previous_states = json.loads(base_state_row["field_states_json"] or "{}")
        new_field_states = dict(previous_states)
        if not old_source_error:
            for key, values in (
                ("statement", new_statement), ("conditions", new_conditions),
                ("actions", new_actions), ("exceptions", new_exceptions),
                ("metric_definition", new_metric), ("case_details", new_case),
            ):
                new_field_states[key] = "supported" if values else (
                    "not_applicable" if key in ("metric_definition", "case_details") and
                    ((key == "metric_definition" and new_category != "指标数据") or
                     (key == "case_details" and new_category != "项目案例"))
                    else "not_stated"
                )

        if is_new_draft_created:
            # 插入新版本草稿记录（review_status='pending_review', index_status='not_indexed'）
            source_doc_ver_id = active_ver["source_document_version_id"]
            conn.execute(
                """
                INSERT INTO knowledge_versions
                (id, item_id, organization_id, source_document_version_id, version_number,
                 title, content, primary_category, atom_type, subject, statement,
                 conditions_json, actions_json, exceptions_json, metric_definition_json, case_details_json,
                 field_states_json, quality_flags_json, customer_types_json, business_scenes_json, problem_tags_json,
                 source_anchors_json, valid_from, valid_until, review_status, index_status, revision_token,
                  extraction_context_json, related_cases_json,
                  business_importance, importance_rationale, importance_adjusted_by,
                  created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, '[]', ?, ?, 'pending_review', 'not_indexed', ?, '{}', ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_v_id,
                    item_id,
                    org_id,
                    source_doc_ver_id,
                    new_ver_num,
                    new_title,
                    new_content,
                    new_category,
                    new_atom_type,
                    new_subject,
                    new_statement,
                    json.dumps(new_conditions, ensure_ascii=False),
                    json.dumps(new_actions, ensure_ascii=False),
                    json.dumps(new_exceptions, ensure_ascii=False),
                    json.dumps(new_metric, ensure_ascii=False) if new_metric else None,
                    json.dumps(new_case, ensure_ascii=False) if new_case else None,
                    json.dumps(q_flags, ensure_ascii=False),
                    json.dumps(new_customer_types, ensure_ascii=False),
                    json.dumps(new_business_scenes, ensure_ascii=False),
                    json.dumps(new_problem_tags, ensure_ascii=False),
                    new_valid_from,
                    new_valid_until,
                    new_token,
                    json.dumps(new_related_cases, ensure_ascii=False),
                    active_ver["business_importance"] or "normal",
                    active_ver["importance_rationale"],
                    active_ver["importance_adjusted_by"],
                    now_iso,
                    admin["id"],
                )
            )
            # 继承已有证据关联至新草稿版本
            old_ev_rows = conn.execute(
                "SELECT source_block_id, field_name, excerpt, accuracy_level FROM knowledge_evidence WHERE knowledge_version_id = ?",
                (active_ver["id"],)
            ).fetchall()
            for oev in old_ev_rows:
                conn.execute(
                    """
                    INSERT INTO knowledge_evidence
                    (id, knowledge_version_id, source_block_id, organization_id, field_name, excerpt, accuracy_level, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"ke_{uuid.uuid4().hex[:12]}",
                        target_v_id,
                        oev["source_block_id"],
                        org_id,
                        oev["field_name"],
                        oev["excerpt"],
                        oev["accuracy_level"],
                        now_iso
                    )
                )

            # 新草稿只占用待切换指针，不改变当前服务版本。
            conn.execute(
                """
                UPDATE knowledge_items
                SET pending_version_id = ?, updated_at = ?
                WHERE id = ? AND organization_id = ? AND active_version_id = ?
                """,
                (target_v_id, now_iso, item_id, org_id, active_ver["id"])
            )
        else:
            # 原地更新已有草稿版本
            conn.execute(
                """
                UPDATE knowledge_versions SET
                    title = ?,
                    content = ?,
                    primary_category = ?,
                    atom_type = ?,
                    subject = ?,
                    statement = ?,
                    conditions_json = ?,
                    actions_json = ?,
                    exceptions_json = ?,
                    metric_definition_json = ?,
                    case_details_json = ?,
                    quality_flags_json = ?,
                    customer_types_json = ?,
                    business_scenes_json = ?,
                    problem_tags_json = ?,
                    valid_from = ?,
                    valid_until = ?,
                    related_cases_json = ?,
                    revision_token = ?,
                    reviewed_by = ?
                WHERE id = ?
                """,
                (
                    new_title,
                    new_content,
                    new_category,
                    new_atom_type,
                    new_subject,
                    new_statement,
                    json.dumps(new_conditions, ensure_ascii=False),
                    json.dumps(new_actions, ensure_ascii=False),
                    json.dumps(new_exceptions, ensure_ascii=False),
                    json.dumps(new_metric, ensure_ascii=False) if new_metric else None,
                    json.dumps(new_case, ensure_ascii=False) if new_case else None,
                    json.dumps(q_flags, ensure_ascii=False),
                    json.dumps(new_customer_types, ensure_ascii=False),
                    json.dumps(new_business_scenes, ensure_ascii=False),
                    json.dumps(new_problem_tags, ensure_ascii=False),
                    new_valid_from,
                    new_valid_until,
                    json.dumps(new_related_cases, ensure_ascii=False),
                    new_token,
                    admin["id"],
                    target_v_id,
                )
            )

        # 将手动校对后的字段状态写回当前草稿，不碰已经服务的历史版本。
        conn.execute(
            "UPDATE knowledge_versions SET field_states_json = ? WHERE id = ? AND organization_id = ? AND review_status = 'pending_review'",
            (json.dumps(new_field_states, ensure_ascii=False), target_v_id, org_id),
        )

        # M01-C3：手动输入的目录外场景标签进入待归并列表（无场景目录时不处理）
        register_unmatched_scene_tags(
            conn, org_id, new_business_scenes, item_id, target_v_id,
            "manual", title=new_title, now_iso=now_iso,
        )

        if curr_item["ki_scope"] != new_access_scope:
            conn.execute(
                "UPDATE knowledge_items SET access_scope = ?, updated_at = ? WHERE id = ?",
                (new_access_scope, now_iso, item_id)
            )
            conn.execute(
                """
                INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
                VALUES (?, ?, ?, 'update_knowledge_access_scope', 'knowledge_item', ?, ?, ?)
                """,
                (
                    f"aud_{uuid.uuid4().hex[:12]}",
                    org_id,
                    admin["id"],
                    item_id,
                    json.dumps({"from": curr_item["ki_scope"], "to": new_access_scope}, ensure_ascii=False),
                    now_iso,
                )
            )
        else:
            conn.execute(
                "UPDATE knowledge_items SET updated_at = ? WHERE id = ?",
                (now_iso, item_id)
            )

    # 事务提交后异步重新评估；版本令牌变化会使旧评估失效，迟到结果也会被拒绝挂接。
    try:
        jev_queue = enqueue_jev_evaluation(target_v_id)
    except Exception:
        jev_queue = {"status": "failed_to_enqueue"}
    msg = "已保存为新版本草稿（旧生效版本在未确认前继续服务）" if is_new_draft_created else "知识草稿已保存"
    return {
        "message": msg,
        "revision_token": new_token,
        "quality_flags": q_flags,
        "version_id": target_v_id,
        "is_new_version_draft": is_new_draft_created,
        "jev_status": jev_queue.get("status"),
    }

def _do_confirm_item(
    conn,
    item_id: str,
    org_id: str,
    admin_id: str,
    client_token: Optional[str],
    now_iso: str,
    batch_mode: bool = False,
) -> Dict[str, Any]:
    item = conn.execute(
        "SELECT id, document_id, active_version_id, pending_version_id FROM knowledge_items WHERE id = ? AND organization_id = ? AND lifecycle_status != 'deleted'",
        (item_id, org_id)
    ).fetchone()

    if not item:
        raise HTTPException(status_code=404, detail="知识条目不存在或已被删除")

    pending_v = None
    if item["pending_version_id"]:
        pending_v = conn.execute(
            """
            SELECT id, version_number, revision_token, title, statement, primary_category,
                   field_states_json, quality_flags_json, metric_definition_json, source_anchors_json, source_document_version_id,
                   review_status, index_status, business_importance, importance_rationale,
                   (SELECT COUNT(*) FROM knowledge_evidence ke
                    JOIN source_blocks sb ON sb.id = ke.source_block_id
                    WHERE ke.knowledge_version_id = kv.id
                      AND ke.organization_id = kv.organization_id
                      AND sb.document_version_id = kv.source_document_version_id) as ev_count
            FROM knowledge_versions kv
            WHERE kv.id = ? AND kv.item_id = ? AND kv.organization_id = ?
            """,
            (item["pending_version_id"], item_id, org_id)
        ).fetchone()

    if pending_v:
        target_v = pending_v
    else:
        target_v = conn.execute(
            """
            SELECT id, version_number, revision_token, title, statement, primary_category,
                   field_states_json, quality_flags_json, metric_definition_json, source_anchors_json, source_document_version_id,
                   review_status, index_status, business_importance, importance_rationale,
                   (SELECT COUNT(*) FROM knowledge_evidence ke
                    JOIN source_blocks sb ON sb.id = ke.source_block_id
                    WHERE ke.knowledge_version_id = kv.id
                      AND ke.organization_id = kv.organization_id
                      AND sb.document_version_id = kv.source_document_version_id) as ev_count
            FROM knowledge_versions kv
            WHERE kv.id = ? AND kv.organization_id = ?
            """,
            (item["active_version_id"], org_id)
        ).fetchone()

    if not target_v:
        raise HTTPException(status_code=404, detail="未找到可确认的知识版本")

    if not client_token:
        raise HTTPException(
            status_code=400,
            detail="缺少 revision_token，无法执行并发安全确认"
        )
    if target_v["revision_token"] != client_token:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该条目已被其他操作修改，请刷新后再确认"
        )
    if target_v["review_status"] != "pending_review":
        raise HTTPException(
            status_code=400,
            detail="确认失败：该版本已确认或不处于待审核状态"
        )
    # 与展示层使用同一份可核验的版本证据，避免旧版字面误报在批量入口继续误拦截。
    confirmation_evidence = [dict(row) for row in conn.execute(
        """SELECT ke.excerpt, ke.accuracy_level FROM knowledge_evidence ke
           JOIN source_blocks sb ON sb.id = ke.source_block_id
           WHERE ke.knowledge_version_id = ? AND ke.organization_id = ?
             AND sb.document_version_id = ?""",
        (target_v["id"], org_id, target_v["source_document_version_id"]),
    ).fetchall()]
    eligibility = _evaluate_review_eligibility(
        target_v, int(target_v["ev_count"] or 0), confirmation_evidence
    )
    # 批量场景优先解释“重点审核/需单条复核”原因，同时保留结构化阻断信息。
    if batch_mode and eligibility["batch_review_reasons"]:
        reasons = eligibility["batch_review_reasons"] + eligibility["confirmation_blockers"]
        raise HTTPException(status_code=400, detail="；".join(reasons))
    if eligibility["confirmation_blockers"]:
        raise HTTPException(
            status_code=400,
            detail=f"确认失败：{'；'.join(eligibility['confirmation_blockers'])}"
        )

    new_token = uuid.uuid4().hex

    conn.execute(
        """
        UPDATE knowledge_versions SET
            review_status = 'confirmed',
            reviewed_by = ?,
            reviewed_at = ?,
            revision_token = ?,
            index_status = 'not_indexed'
        WHERE id = ?
        """,
        (admin_id, now_iso, new_token, target_v["id"])
    )

    conn.execute(
        """
        INSERT INTO audit_logs
        (id, organization_id, user_id, action, target_type, target_id, details, created_at)
        VALUES (?, ?, ?, 'confirm_knowledge_version', 'knowledge_version', ?, ?, ?)
        """,
        (
            f"aud_{uuid.uuid4().hex[:12]}", org_id, admin_id, target_v["id"],
            json.dumps({
                "item_id": item_id,
                "serving_version_id": item["active_version_id"],
                "pending_version_id": item["pending_version_id"],
                "result": "confirmed_index_queued",
            }, ensure_ascii=False),
            now_iso,
        )
    )

    doc = conn.execute(
        "SELECT id, active_version_id FROM documents WHERE id = ?",
        (item["document_id"],)
    ).fetchone()
    if doc and doc["active_version_id"] is None:
        conn.execute(
            "UPDATE documents SET active_version_id = ?, updated_at = ? WHERE id = ? AND active_version_id IS NULL",
            (target_v["source_document_version_id"], now_iso, doc["id"])
        )

    index_task = create_or_get_build_index_task(
        conn, target_v["id"], org_id, retry_failed=False
    )

    return {
        "item_id": item_id,
        "version_id": target_v["id"],
        "title": target_v["title"],
        "revision_token": new_token,
        "active_version_id": item["active_version_id"],
        "pending_version_id": item["pending_version_id"],
        "index_task": index_task,
    }


@app.post("/api/knowledge/items/{item_id}/confirm")
def confirm_knowledge_item(
    item_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    单条确认知识版本并排队建立正式索引（PRD FR07, FR08, FR09）。
    """
    org_id = admin["organization_id"]
    client_token = payload.get("revision_token")
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        res = _do_confirm_item(
            conn=conn,
            item_id=item_id,
            org_id=org_id,
            admin_id=admin["id"],
            client_token=client_token,
            now_iso=now_iso,
            batch_mode=False,
        )

    index_task = res["index_task"]
    if index_task["should_submit"]:
        submit_task(index_task["task_id"])

    return {
        "message": "知识条目已确认，检索索引正在后台构建",
        "review_status": "confirmed",
        "index_status": index_task["index_status"],
        "revision_token": res["revision_token"],
        "active_version_id": res["active_version_id"],
        "target_version_id": res["version_id"],
        "pending_version_id": res["pending_version_id"],
        "index_task_id": index_task["task_id"],
        "index_task_status": index_task["task_status"],
    }


@app.post("/api/knowledge/items/batch-confirm")
def batch_confirm_knowledge_items(
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    批量确认接口（章节集中核对）：
    - 仅普通内容且无阻断性疑点/未解决冲突的条目可被批量确认；
    - 涉及人身安全、应急或重点操作的条目由后端逐条刚性拦截，不能批量误确认；
    - 逐条校验权限、版本匹配与并发 revision_token；
    - 记录批量确认审计日志。
    """
    org_id = admin["organization_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    raw_items = payload.get("items") or []
    if not raw_items:
        raise HTTPException(status_code=400, detail="待确认条目列表不能为空")

    confirmed_list = []
    skipped_list = []
    tasks_to_submit = []

    with get_db() as conn:
        for entry in raw_items:
            item_id = entry.get("item_id")
            token = entry.get("revision_token")
            if not item_id:
                skipped_list.append({"item_id": None, "reason": "缺少 item_id", "status_code": 400})
                continue
            if not token:
                skipped_list.append({"item_id": item_id, "reason": "缺少 revision_token，未执行确认", "status_code": 400})
                continue

            try:
                res = _do_confirm_item(
                    conn=conn,
                    item_id=item_id,
                    org_id=org_id,
                    admin_id=admin["id"],
                    client_token=token,
                    now_iso=now_iso,
                    batch_mode=True,
                )
                confirmed_list.append({
                    "item_id": item_id,
                    "version_id": res["version_id"],
                    "title": res["title"],
                })
                if res["index_task"]["should_submit"]:
                    tasks_to_submit.append(res["index_task"]["task_id"])
            except HTTPException as exc:
                skipped_list.append({
                    "item_id": item_id,
                    "reason": exc.detail,
                    "status_code": exc.status_code,
                })
            except Exception as e:
                skipped_list.append({
                    "item_id": item_id,
                    "reason": str(e),
                    "status_code": 500,
                })

        batch_audit_id = f"aud_batch_{uuid.uuid4().hex[:10]}"
        conn.execute(
            """
            INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
            VALUES (?, ?, ?, 'batch_confirm_knowledge', 'knowledge_batch', ?, ?, ?)
            """,
            (
                batch_audit_id,
                org_id,
                admin["id"],
                batch_audit_id,
                json.dumps({
                    "confirmed_count": len(confirmed_list),
                    "skipped_count": len(skipped_list),
                    "confirmed_item_ids": [c["item_id"] for c in confirmed_list],
                    "skipped_details": skipped_list,
                }, ensure_ascii=False),
                now_iso,
            )
        )

    for tid in tasks_to_submit:
        submit_task(tid)

    return {
        "message": f"批量核对处理完毕：成功确认 {len(confirmed_list)} 条普通条目，跳过 {len(skipped_list)} 条重点或受阻条目",
        "confirmed_count": len(confirmed_list),
        "skipped_count": len(skipped_list),
        "confirmed_items": confirmed_list,
        "skipped_items": skipped_list,
    }


@app.get("/api/documents/{document_id}/versions/{version_id}/chapters/review")
def get_chapter_review(
    document_id: str,
    version_id: str,
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    按章节组织审核数据（PRD 减负改造核心接口）：
    1. 按「来源文件 + 文件版本 + 章节路径」分组；
    2. 无标题内容按原文顺序归组（不编造章节标题）；
    3. 呈现每个章节下的完整原文只读块、覆盖状态（已关联候选/已确认无需提取/未覆盖待检查）；
    4. 呈现各知识条目、业务重要度与抽取疑点（两类解耦）；
    5. 未覆盖段落醒目提示，未覆盖时章节状态不得显示为已核对完成。
    """
    org_id = admin["organization_id"]
    with get_db() as conn:
        doc = conn.execute(
            "SELECT id, title, access_scope, is_deleted FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        ver = conn.execute(
            "SELECT id, version_label, file_name, file_type, processing_status FROM document_versions WHERE id = ? AND document_id = ? AND organization_id = ?",
            (version_id, document_id, org_id)
        ).fetchone()
        if not ver:
            raise HTTPException(status_code=404, detail="文件版本不存在")

        # 1. 查询全部 source_blocks
        blocks_rows = conn.execute(
            """
            SELECT id, block_index, block_type, heading_path, page_number, paragraph_anchor, text_content,
                   COALESCE(ignore_status, 'none') as ignore_status,
                   ignore_reason, ignored_by, ignored_at
            FROM source_blocks
            WHERE document_version_id = ? AND organization_id = ?
            ORDER BY block_index ASC
            """,
            (version_id, org_id)
        ).fetchall()

        if not blocks_rows:
            return {
                "document_id": document_id,
                "document_title": doc["title"],
                "version_id": version_id,
                "version_label": ver["version_label"],
                "file_name": ver["file_name"],
                "chapters": [],
                "summary": {
                    "total_chapters": 0,
                    "total_blocks": 0,
                    "uncovered_blocks": 0,
                    "associated_blocks": 0,
                    "admin_ignored_blocks": 0,
                    "model_suggested_ignore_blocks": 0,
                    "total_items": 0,
                    "pending_items": 0,
                    "confirmed_items": 0,
                    "critical_items": 0,
                }
            }

        # 2. 查询该版本下派生的所有有效知识条目（草稿优先）
        items_rows = conn.execute(
            """
            SELECT
                ki.id as item_id, ki.document_id, ki.access_scope, ki.lifecycle_status,
                kv.id as version_id, kv.source_document_version_id, kv.version_number,
                kv.title, kv.content, kv.primary_category, kv.atom_type, kv.subject, kv.statement,
                kv.conditions_json, kv.actions_json, kv.exceptions_json, kv.metric_definition_json, kv.case_details_json,
                kv.field_states_json, kv.quality_flags_json, kv.source_anchors_json,
                kv.valid_from, kv.valid_until, kv.review_status, kv.index_status, kv.revision_token,
                kv.business_importance, kv.importance_rationale, kv.importance_adjusted_by,
                CASE WHEN ki.pending_version_id IS NOT NULL THEN 1 ELSE 0 END as is_draft
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON (
                CASE WHEN ki.pending_version_id IS NOT NULL THEN ki.pending_version_id ELSE ki.active_version_id END
            ) = kv.id
            WHERE kv.source_document_version_id = ? AND ki.organization_id = ?
              AND ki.lifecycle_status != 'deleted' AND (ki.is_excluded = 0 OR ki.is_excluded IS NULL)
            """,
            (version_id, org_id)
        ).fetchall()

        version_ids = [r["version_id"] for r in items_rows]

        # 3. 查询关联证据
        evidence_rows = []
        if version_ids:
            placeholders = ",".join("?" for _ in version_ids)
            evidence_rows = conn.execute(
                f"""
                SELECT ke.id, ke.knowledge_version_id, ke.source_block_id, ke.field_name, ke.excerpt, ke.accuracy_level
                FROM knowledge_evidence ke
                JOIN knowledge_versions kv ON kv.id = ke.knowledge_version_id
                JOIN source_blocks sb ON sb.id = ke.source_block_id
                WHERE ke.knowledge_version_id IN ({placeholders}) AND ke.organization_id = ?
                  AND sb.document_version_id = kv.source_document_version_id
                """,
                [*version_ids, org_id]
            ).fetchall()
        jev_by_version = {
            row["version_id"]: serialize_evaluation(conn, row["version_id"], row["revision_token"])
            for row in items_rows
        }

    block_associations: Dict[str, List[Dict[str, Any]]] = {}
    item_blocks_map: Dict[str, List[str]] = {}
    item_evidence_map: Dict[str, List[Dict[str, Any]]] = {}

    v_to_item = {r["version_id"]: r for r in items_rows}

    for ev in evidence_rows:
        bid = ev["source_block_id"]
        vid = ev["knowledge_version_id"]
        item = v_to_item.get(vid)
        if not item:
            continue
        item_id = item["item_id"]
        if bid not in block_associations:
            block_associations[bid] = []
        block_associations[bid].append({
            "item_id": item_id,
            "version_id": vid,
            "title": item["title"],
            "field_name": ev["field_name"],
            "excerpt": ev["excerpt"],
        })

        if item_id not in item_blocks_map:
            item_blocks_map[item_id] = []
        if bid not in item_blocks_map[item_id]:
            item_blocks_map[item_id].append(bid)

        if item_id not in item_evidence_map:
            item_evidence_map[item_id] = []
        item_evidence_map[item_id].append({
            "id": ev["id"],
            "source_block_id": bid,
            "field_name": ev["field_name"],
            "excerpt": ev["excerpt"],
            "accuracy_level": ev["accuracy_level"],
        })

    # 构建每个 block 的详细信息及真实覆盖状态
    block_dict_map = {}
    processed_blocks = []
    for b in blocks_rows:
        bid = b["id"]
        assoc_list = block_associations.get(bid, [])
        ignore_status = b["ignore_status"] or "none"
        ignore_reason = b["ignore_reason"]

        if assoc_list:
            coverage_status = "associated_candidate"
            coverage_note = "已关联候选条目（提示：存在候选知识并不等同于原文已被完整提取）"
        elif ignore_status == "admin_ignored":
            coverage_status = "admin_ignored"
            coverage_note = f"管理员已确认无需提取（原因：{ignore_reason or '无需提取'}）"
        elif ignore_status == "model_suggested_ignore":
            coverage_status = "model_suggested_ignore"
            coverage_note = f"模型建议无需提取（原因：{ignore_reason or '说明性内容'}，待管理员确认）"
        else:
            coverage_status = "uncovered"
            coverage_note = "尚未覆盖，待检查"

        b_obj = {
            "id": bid,
            "block_index": b["block_index"],
            "block_type": b["block_type"],
            "heading_path": b["heading_path"],
            "page_number": b["page_number"],
            "paragraph_anchor": b["paragraph_anchor"],
            "text_content": b["text_content"],
            "coverage_status": coverage_status,
            "coverage_note": coverage_note,
            "ignore_status": ignore_status,
            "ignore_reason": ignore_reason,
            "ignored_by": b["ignored_by"],
            "ignored_at": b["ignored_at"],
            "associated_items": [
                {"item_id": a["item_id"], "title": a["title"], "field_name": a["field_name"]}
                for a in assoc_list
            ],
        }
        processed_blocks.append(b_obj)
        block_dict_map[bid] = b_obj

    # 构建知识条目结构化对象
    items_by_id: Dict[str, Dict[str, Any]] = {}
    for r in items_rows:
        item_id = r["item_id"]
        from structured_quality import filter_resolved_relation_flags
        item_source_evidence = item_evidence_map.get(item_id, [])
        q_flags = filter_resolved_relation_flags(
            json.loads(r["quality_flags_json"] or "[]"),
            json.loads(r["metric_definition_json"]) if r["metric_definition_json"] else None,
            item_source_evidence,
        )
        f_states = json.loads(r["field_states_json"] or "{}")
        s_anchors = json.loads(r["source_anchors_json"] or "[]")

        b_importance = r["business_importance"] or "normal"
        imp_rationale = r["importance_rationale"] or "标准业务规范与操作要求"
        eligibility = _evaluate_review_eligibility(r, len(item_source_evidence), item_source_evidence)
        issues_summary = eligibility["issues_summary"]
        batch_reasons = eligibility["confirmation_blockers"] + eligibility["batch_review_reasons"]

        items_by_id[item_id] = {
            "item_id": item_id,
            "version_id": r["version_id"],
            "title": r["title"],
            "content": r["content"],
            "statement": r["statement"],
            "primary_category": r["primary_category"],
            "atom_type": r["atom_type"],
            "subject": r["subject"],
            "conditions": json.loads(r["conditions_json"] or "[]"),
            "actions": json.loads(r["actions_json"] or "[]"),
            "exceptions": json.loads(r["exceptions_json"] or "[]"),
            "metric_definition": json.loads(r["metric_definition_json"]) if r["metric_definition_json"] else None,
            "case_details": json.loads(r["case_details_json"]) if r["case_details_json"] else None,
            "field_states": f_states,
            "quality_flags": q_flags,
            "source_anchors": s_anchors,
            "review_status": r["review_status"],
            "index_status": r["index_status"],
            "revision_token": r["revision_token"],
            "is_draft": bool(r["is_draft"]),
            "business_importance": b_importance,
            "importance_rationale": imp_rationale,
            "importance_adjusted_by": r["importance_adjusted_by"],
            "issues_summary": issues_summary,
            "can_confirm": eligibility["can_confirm"],
            "confirmation_blockers": eligibility["confirmation_blockers"],
            "batch_review_reasons": eligibility["batch_review_reasons"],
            "can_batch_confirm": eligibility["can_batch_confirm"],
            "batch_block_reason": "；".join(batch_reasons) if batch_reasons else None,
            "evidence": item_evidence_map.get(item_id, []),
            "associated_block_ids": item_blocks_map.get(item_id, []),
            "jev_evaluation": jev_by_version.get(r["version_id"], {"status": "not_started", "answers": []}),
        }

    # 按「章节路径」分组
    chapters = []
    current_chapter = None

    for b in processed_blocks:
        h_path = b["heading_path"]
        if h_path:
            if current_chapter is None or current_chapter["heading_path"] != h_path or current_chapter["is_derived_group"]:
                if current_chapter:
                    chapters.append(current_chapter)
                chapter_name = h_path.split(" / ")[-1].strip() if " / " in h_path else h_path.strip()
                current_chapter = {
                    "chapter_key": f"chap_{hashlib.md5(h_path.encode('utf-8')).hexdigest()[:8]}",
                    "chapter_name": chapter_name,
                    "heading_path": h_path,
                    "is_derived_group": False,
                    "group_description": h_path,
                    "blocks": [],
                }
        else:
            if current_chapter is None or not current_chapter["is_derived_group"]:
                if current_chapter:
                    chapters.append(current_chapter)
                current_chapter = {
                    "chapter_key": f"seq_{len(chapters) + 1}",
                    "chapter_name": "正文顺序段落（无章节标题）",
                    "heading_path": None,
                    "is_derived_group": True,
                    "group_description": "原文未设置标题，按出现先后顺序归组",
                    "blocks": [],
                }

        current_chapter["blocks"].append(b)

    if current_chapter:
        chapters.append(current_chapter)

    for chap in chapters:
        if chap["is_derived_group"] and chap["blocks"]:
            start_a = chap["blocks"][0]["paragraph_anchor"]
            end_a = chap["blocks"][-1]["paragraph_anchor"]
            chap["chapter_name"] = f"正文顺序段落（{start_a} ~ {end_a}）"

    block_to_chapter_idx = {}
    for c_idx, chap in enumerate(chapters):
        for b in chap["blocks"]:
            block_to_chapter_idx[b["id"]] = c_idx

    chapter_items: Dict[int, List[Dict[str, Any]]] = {i: [] for i in range(len(chapters))}
    unassigned_items = []

    for item_id, item_obj in items_by_id.items():
        b_ids = item_obj["associated_block_ids"]
        target_c_idx = None
        for bid in b_ids:
            if bid in block_to_chapter_idx:
                target_c_idx = block_to_chapter_idx[bid]
                break
        if target_c_idx is not None:
            chapter_items[target_c_idx].append(item_obj)
        else:
            unassigned_items.append(item_obj)

    total_uncovered_all = 0
    total_critical_all = 0

    for c_idx, chap in enumerate(chapters):
        chap_blocks = chap["blocks"]
        c_items = chapter_items.get(c_idx, [])
        chap["items"] = c_items

        uncovered_cnt = sum(1 for b in chap_blocks if b["coverage_status"] == "uncovered")
        associated_cnt = sum(1 for b in chap_blocks if b["coverage_status"] == "associated_candidate")
        admin_ignored_cnt = sum(1 for b in chap_blocks if b["coverage_status"] == "admin_ignored")
        model_suggested_ignore_cnt = sum(1 for b in chap_blocks if b["coverage_status"] == "model_suggested_ignore")

        pending_cnt = sum(1 for it in c_items if it["review_status"] == "pending_review")
        confirmed_cnt = sum(1 for it in c_items if it["review_status"] == "confirmed")
        critical_cnt = sum(1 for it in c_items if it["business_importance"] == "critical")
        can_batch_cnt = sum(1 for it in c_items if it["can_batch_confirm"])

        total_uncovered_all += uncovered_cnt
        total_critical_all += critical_cnt

        attention_points = []
        if uncovered_cnt > 0:
            attention_points.append({
                "type": "uncovered_blocks",
                "severity": "warning",
                "message": f"本章节有 {uncovered_cnt} 个正文段落尚未覆盖，请核对是否漏抽或标记无需提取",
            })
        if model_suggested_ignore_cnt > 0:
            attention_points.append({
                "type": "model_suggested_ignore",
                "severity": "warning",
                "message": f"本章节有 {model_suggested_ignore_cnt} 个模型建议忽略段落，仍需管理员逐段确认",
            })
        for it in c_items:
            jev = it.get("jev_evaluation") or {}
            if jev.get("review_priority") in ("high", "medium"):
                attention_points.append({
                    "type": "jev_review_priority",
                    "severity": "important" if jev.get("review_priority") == "high" else "warning",
                    "message": f"条目「{it['title']}」：Jev 建议优先核对（{'；'.join(jev.get('priority_reasons') or ['存在模型质检提示'])}）",
                    "item_id": it["item_id"],
                })
            elif jev.get("status") == "failed":
                attention_points.append({
                    "type": "jev_failed",
                    "severity": "warning",
                    "message": f"条目「{it['title']}」：Jev 质检未完成，不能视为检查通过",
                    "item_id": it["item_id"],
                })
            if it["business_importance"] == "critical" and it["review_status"] == "pending_review":
                attention_points.append({
                    "type": "critical_item",
                    "severity": "important",
                    "message": f"条目「{it['title']}」属于重要操作（{it['importance_rationale']}），需人工逐条重点审核",
                    "item_id": it["item_id"],
                })
            for err in it["issues_summary"]["deterministic_errors"]:
                attention_points.append({
                    "type": "deterministic_error",
                    "severity": "blocking",
                    "message": f"条目「{it['title']}」：{err['message']}",
                    "item_id": it["item_id"],
                })
            for doubt in it["issues_summary"]["model_doubts"]:
                if doubt["blocking"]:
                    attention_points.append({
                        "type": "conflict_doubt",
                        "severity": "blocking",
                        "message": f"条目「{it['title']}」：{doubt['message']}",
                        "item_id": it["item_id"],
                    })

        if uncovered_cnt > 0:
            chap_status = "has_uncovered"
            status_label = "存在未覆盖原文"
        elif model_suggested_ignore_cnt > 0:
            chap_status = "pending_source_review"
            status_label = "模型建议待检查"
        elif pending_cnt > 0:
            chap_status = "pending_review"
            status_label = "待核对"
        elif pending_cnt == 0 and len(c_items) > 0:
            chap_status = "fully_reviewed"
            status_label = "已完成核对"
        else:
            chap_status = "empty"
            status_label = "无条目"

        chap["stats"] = {
            "total_blocks": len(chap_blocks),
            "uncovered_blocks": uncovered_cnt,
            "associated_blocks": associated_cnt,
            "admin_ignored_blocks": admin_ignored_cnt,
            "model_suggested_ignore_blocks": model_suggested_ignore_cnt,
            "total_items": len(c_items),
            "pending_items": pending_cnt,
            "confirmed_items": confirmed_cnt,
            "critical_items": critical_cnt,
            "can_batch_confirm_count": can_batch_cnt,
        }
        chap["attention_points"] = attention_points
        chap["chapter_status"] = chap_status
        chap["status_label"] = status_label

    if unassigned_items:
        chapters.append({
            "chapter_key": "unassigned_items_group",
            "chapter_name": "未明确来源段落条目（待检查来源）",
            "heading_path": None,
            "is_derived_group": True,
            "group_description": "缺少对应结构块证据的候选条目",
            "blocks": [],
            "items": unassigned_items,
            "stats": {
                "total_blocks": 0,
                "uncovered_blocks": 0,
                "associated_blocks": 0,
                "admin_ignored_blocks": 0,
                "model_suggested_ignore_blocks": 0,
                "total_items": len(unassigned_items),
                "pending_items": sum(1 for it in unassigned_items if it["review_status"] == "pending_review"),
                "confirmed_items": sum(1 for it in unassigned_items if it["review_status"] == "confirmed"),
                "critical_items": sum(1 for it in unassigned_items if it["business_importance"] == "critical"),
                "can_batch_confirm_count": 0,
            },
            "attention_points": [{
                "type": "missing_source",
                "severity": "blocking",
                "message": f"存在 {len(unassigned_items)} 条条目未关联有效来源块",
            }],
            "chapter_status": "pending_review",
            "status_label": "待检查来源",
        })

    return {
        "document_id": document_id,
        "document_title": doc["title"],
        "version_id": version_id,
        "version_label": ver["version_label"],
        "file_name": ver["file_name"],
        "chapters": chapters,
        "summary": {
            "total_chapters": len(chapters),
            "total_blocks": len(blocks_rows),
            "uncovered_blocks": total_uncovered_all,
            "associated_blocks": sum(1 for b in processed_blocks if b["coverage_status"] == "associated_candidate"),
            "admin_ignored_blocks": sum(1 for b in processed_blocks if b["coverage_status"] == "admin_ignored"),
            "model_suggested_ignore_blocks": sum(1 for b in processed_blocks if b["coverage_status"] == "model_suggested_ignore"),
            "total_items": len(items_rows),
            "pending_items": sum(1 for r in items_rows if r["review_status"] == "pending_review"),
            "confirmed_items": sum(1 for r in items_rows if r["review_status"] == "confirmed"),
            "critical_items": total_critical_all,
        }
    }


@app.post("/api/knowledge/items/{item_id}/jev/retry")
def retry_jev_evaluation(item_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    org_id = admin["organization_id"]
    with get_db() as conn:
        row = conn.execute(
            """SELECT COALESCE(ki.pending_version_id, ki.active_version_id) AS version_id
               FROM knowledge_items ki JOIN documents d ON d.id=ki.document_id
               WHERE ki.id=? AND ki.organization_id=? AND ki.lifecycle_status!='deleted' AND d.is_deleted=0""",
            (item_id, org_id),
        ).fetchone()
    if not row or not row["version_id"]:
        raise HTTPException(status_code=404, detail="知识条目不存在或已删除")
    result = enqueue_jev_evaluation(row["version_id"], retry_failed=True)
    return {"message": "Jev 质检已进入后台队列" if result["status"] == "queued" else "Jev 当前不可执行", **result}


@app.post("/api/knowledge/items/{item_id}/jev/questions/{question_id}/ignore")
def ignore_jev_question(
    item_id: str,
    question_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin),
):
    reason = (payload.get("reason") or "管理员已人工核对并忽略该模型提示").strip()
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        row = conn.execute(
            """SELECT je.id AS evaluation_id
               FROM knowledge_items ki
               JOIN knowledge_versions kv ON kv.id=COALESCE(ki.pending_version_id, ki.active_version_id)
               JOIN jev_evaluations je ON je.knowledge_version_id=kv.id AND je.candidate_revision_token=kv.revision_token
               WHERE ki.id=? AND ki.organization_id=? AND je.status='completed'
               ORDER BY je.created_at DESC LIMIT 1""",
            (item_id, admin["organization_id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="未找到当前版本的已完成 Jev 质检")
        answer = conn.execute(
            "SELECT id FROM jev_evaluation_answers WHERE evaluation_id=? AND question_id=?",
            (row["evaluation_id"], question_id),
        ).fetchone()
        if not answer:
            raise HTTPException(status_code=404, detail="Jev 质检问题不存在")
        conn.execute(
            "UPDATE jev_evaluation_answers SET ignored_by=?, ignored_at=?, ignore_reason=? WHERE id=?",
            (admin["id"], now_iso, reason, answer["id"]),
        )
        conn.execute(
            """INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
               VALUES (?, ?, ?, 'ignore_jev_question', 'jev_evaluation_answer', ?, ?, ?)""",
            (f"aud_{uuid.uuid4().hex[:12]}", admin["organization_id"], admin["id"], answer["id"],
             json.dumps({"item_id": item_id, "question_id": question_id, "reason": reason}, ensure_ascii=False), now_iso),
        )
    return {"message": "该 Jev 提示已忽略", "question_id": question_id}


@app.post("/api/documents/{document_id}/versions/{version_id}/source-blocks/{block_id}/ignore")
def ignore_source_block(
    document_id: str,
    version_id: str,
    block_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    org_id = admin["organization_id"]
    reason = (payload.get("ignore_reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="ignore_reason 不能为空，请填写人工忽略依据")
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        sb = conn.execute(
            "SELECT id, paragraph_anchor, heading_path FROM source_blocks WHERE id = ? AND document_version_id = ? AND organization_id = ?",
            (block_id, version_id, org_id)
        ).fetchone()
        if not sb:
            raise HTTPException(status_code=404, detail="原文正文块不存在")

        conn.execute(
            """
            UPDATE source_blocks
            SET ignore_status = 'admin_ignored',
                ignore_reason = ?,
                ignored_by = ?,
                ignored_at = ?
            WHERE id = ? AND organization_id = ?
            """,
            (reason, admin["id"], now_iso, block_id, org_id)
        )
        conn.execute(
            """
            INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
            VALUES (?, ?, ?, 'ignore_source_block', 'source_block', ?, ?, ?)
            """,
            (
                f"aud_{uuid.uuid4().hex[:12]}", org_id, admin["id"], block_id,
                json.dumps({"reason": reason, "paragraph_anchor": sb["paragraph_anchor"]}, ensure_ascii=False),
                now_iso
            )
        )
    return {"message": "正文块已标记为无需提取", "block_id": block_id, "ignore_status": "admin_ignored", "ignore_reason": reason}


@app.delete("/api/documents/{document_id}/versions/{version_id}/source-blocks/{block_id}/ignore")
def unignore_source_block(
    document_id: str,
    version_id: str,
    block_id: str,
    admin: Dict[str, Any] = Depends(require_admin)
):
    org_id = admin["organization_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        sb = conn.execute(
            "SELECT id, paragraph_anchor FROM source_blocks WHERE id = ? AND document_version_id = ? AND organization_id = ?",
            (block_id, version_id, org_id)
        ).fetchone()
        if not sb:
            raise HTTPException(status_code=404, detail="原文正文块不存在")

        conn.execute(
            """
            UPDATE source_blocks
            SET ignore_status = 'none',
                ignore_reason = NULL,
                ignored_by = NULL,
                ignored_at = NULL
            WHERE id = ? AND organization_id = ?
            """,
            (block_id, org_id)
        )
        conn.execute(
            """
            INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
            VALUES (?, ?, ?, 'unignore_source_block', 'source_block', ?, ?, ?)
            """,
            (
                f"aud_{uuid.uuid4().hex[:12]}", org_id, admin["id"], block_id,
                json.dumps({"action": "restore_to_uncovered", "paragraph_anchor": sb["paragraph_anchor"]}, ensure_ascii=False),
                now_iso
            )
        )
    return {"message": "正文块已恢复为待检查状态", "block_id": block_id, "ignore_status": "none"}


@app.put("/api/knowledge/items/{item_id}/importance")
def update_item_importance(
    item_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    org_id = admin["organization_id"]
    new_imp = payload.get("business_importance")
    rationale = (payload.get("importance_rationale") or "").strip()
    target_version_id = payload.get("version_id")
    client_token = payload.get("revision_token")
    if new_imp not in VALID_BUSINESS_IMPORTANCE:
        raise HTTPException(status_code=400, detail="非法 business_importance 值，仅支持 critical / normal / informational")
    if not rationale:
        raise HTTPException(status_code=400, detail="人工调整重要程度必须填写判断依据")
    if not target_version_id or not client_token:
        raise HTTPException(status_code=400, detail="调整重要程度必须携带 version_id 和 revision_token")
    now_iso = datetime.now(timezone.utc).isoformat()
    new_token = uuid.uuid4().hex

    with get_db() as conn:
        item = conn.execute(
            "SELECT id, active_version_id, pending_version_id FROM knowledge_items WHERE id = ? AND organization_id = ? AND lifecycle_status != 'deleted'",
            (item_id, org_id)
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="知识条目不存在")

        target_vid = item["pending_version_id"] if item["pending_version_id"] else item["active_version_id"]
        if target_vid != target_version_id:
            raise HTTPException(status_code=409, detail="目标版本已变化，请刷新后再调整重要程度")
        target = conn.execute(
            "SELECT business_importance, importance_rationale, revision_token FROM knowledge_versions WHERE id = ? AND organization_id = ?",
            (target_vid, org_id),
        ).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="目标知识版本不存在")
        if target["revision_token"] != client_token:
            raise HTTPException(status_code=409, detail="该知识版本已被其他操作修改，请刷新后再调整")
        conn.execute(
            """
            UPDATE knowledge_versions
            SET business_importance = ?,
                importance_rationale = ?,
                importance_adjusted_by = ?,
                revision_token = ?
            WHERE id = ? AND organization_id = ?
            """,
            (new_imp, rationale, admin["display_name"] or admin["id"], new_token, target_vid, org_id)
        )
        conn.execute(
            """
            INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
            VALUES (?, ?, ?, 'adjust_business_importance', 'knowledge_version', ?, ?, ?)
            """,
            (
                f"aud_{uuid.uuid4().hex[:12]}", org_id, admin["id"], target_vid,
                json.dumps({
                    "from": {"business_importance": target["business_importance"], "rationale": target["importance_rationale"]},
                    "to": {"business_importance": new_imp, "rationale": rationale},
                    "revision_token_rotated": True,
                }, ensure_ascii=False),
                now_iso
            )
        )
    return {
        "message": "业务重要程度已更新",
        "item_id": item_id,
        "version_id": target_vid,
        "business_importance": new_imp,
        "importance_rationale": rationale,
        "revision_token": new_token,
    }


@app.post("/api/documents/{document_id}/versions/{version_id}/source-blocks/{block_id}/supplement")
def supplement_knowledge_from_block(
    document_id: str,
    version_id: str,
    block_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    管理员在未覆盖正文块上一键补充知识条目，真实绑定该块为来源证据。
    """
    org_id = admin["organization_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        sb = conn.execute(
            """
            SELECT sb.id, sb.text_content, sb.heading_path, sb.paragraph_anchor, sb.page_number
            FROM source_blocks sb
            JOIN document_versions dv ON dv.id = sb.document_version_id
            JOIN documents d ON d.id = dv.document_id
            WHERE sb.id = ? AND sb.document_version_id = ? AND sb.organization_id = ?
              AND dv.document_id = ? AND d.organization_id = ? AND d.is_deleted = 0
            """,
            (block_id, version_id, org_id, document_id, org_id)
        ).fetchone()
        if not sb:
            raise HTTPException(status_code=404, detail="正文块不存在")

        text = (sb["text_content"] or "").strip()
        title = (payload.get("title") or "").strip()
        statement = (payload.get("statement") or "").strip()
        content = (payload.get("content") or "").strip()
        category = payload.get("primary_category")
        atom_type = payload.get("atom_type")
        business_importance = payload.get("business_importance")

        if not title:
            raise HTTPException(status_code=400, detail="补充知识标题不能为空")
        if not statement or not is_meaningful_business_text(statement):
            raise HTTPException(status_code=400, detail="核心陈述不能为空且须具备实质业务内容")
        if not content or not is_meaningful_business_text(content):
            raise HTTPException(status_code=400, detail="知识正文不能为空且须具备实质业务内容")
        if category is not None and category not in VALID_PRIMARY_CATEGORIES:
            raise HTTPException(status_code=400, detail="primary_category 不是有效的五类主分类")
        if atom_type not in VALID_ATOM_TYPES:
            raise HTTPException(status_code=400, detail="atom_type 不是有效的知识类型")
        if business_importance not in VALID_BUSINESS_IMPORTANCE:
            raise HTTPException(status_code=400, detail="business_importance 不是有效的重要程度")

        quality_flags = ["管理员补充正文尚未完成逐字段来源支持核对"]
        if not category:
            quality_flags.append("待管理员确认主分类")

        item_id = f"ki_{uuid.uuid4().hex[:12]}"
        version_id_k = f"kv_{uuid.uuid4().hex[:12]}"
        rev_token = uuid.uuid4().hex

        conn.execute(
            """
            INSERT INTO knowledge_items (id, document_id, organization_id, active_version_id, access_scope, lifecycle_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'admin_only', 'active', ?, ?)
            """,
            (item_id, document_id, org_id, version_id_k, now_iso, now_iso)
        )

        conn.execute(
            """
            INSERT INTO knowledge_versions (
                id, item_id, organization_id, source_document_version_id, version_number,
                title, content, primary_category, atom_type, subject, statement,
                conditions_json, actions_json, exceptions_json, metric_definition_json, case_details_json,
                field_states_json, quality_flags_json, customer_types_json, business_scenes_json, problem_tags_json,
                source_anchors_json, review_status, index_status, revision_token,
                business_importance, importance_rationale, importance_adjusted_by, created_at, created_by
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, '物业责任主体', ?, '[]', '[]', '[]', NULL, NULL,
                    ?, ?, '[]', '[]', '[]',
                    ?, 'pending_review', 'not_indexed', ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id_k, item_id, org_id, version_id,
                title, content, category, atom_type, statement,
                json.dumps({"statement": "unverified", "content": "unverified"}, ensure_ascii=False),
                json.dumps(quality_flags, ensure_ascii=False),
                json.dumps([sb["paragraph_anchor"]], ensure_ascii=False),
                rev_token, business_importance,
                "管理员补充时明确指定，仍需单条审核确认",
                admin["display_name"] or admin["id"],
                now_iso, admin["id"]
            )
        )

        conn.execute(
            """
            INSERT INTO knowledge_evidence (id, knowledge_version_id, source_block_id, organization_id, field_name, excerpt, accuracy_level, created_at)
            VALUES (?, ?, ?, ?, 'statement', ?, 'exact', ?)
            """,
            (f"ke_{uuid.uuid4().hex[:12]}", version_id_k, block_id, org_id, text, now_iso)
        )

        conn.execute(
            """
            INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
            VALUES (?, ?, ?, 'supplement_knowledge_from_block', 'knowledge_item', ?, ?, ?)
            """,
            (
                f"aud_{uuid.uuid4().hex[:12]}", org_id, admin["id"], item_id,
                json.dumps({
                    "source_block_id": block_id,
                    "title": title,
                    "primary_category": category,
                    "atom_type": atom_type,
                    "business_importance": business_importance,
                    "source_text_preserved": True,
                    "rewritten_fields_require_review": True,
                }, ensure_ascii=False),
                now_iso
            )
        )

    return {
        "message": "已成功从未覆盖段落补充候选知识条目并建立证据绑定",
        "item_id": item_id,
        "version_id": version_id_k,
        "title": title,
    }


@app.post("/api/knowledge/versions/{version_id}/index/retry")
def retry_knowledge_index(
    version_id: str,
    admin: Dict[str, Any] = Depends(require_admin)
):
    org_id = admin["organization_id"]
    with get_db() as conn:
        version = conn.execute(
            """
            SELECT id, review_status, index_status
            FROM knowledge_versions
            WHERE id = ? AND organization_id = ?
            """,
            (version_id, org_id)
        ).fetchone()
        if not version:
            raise HTTPException(status_code=404, detail="知识版本不存在")
        if version["review_status"] != "confirmed":
            raise HTTPException(status_code=409, detail="知识版本尚未确认，不能建立正式索引")
        if version["index_status"] == "ready":
            raise HTTPException(status_code=409, detail="当前知识版本索引已就绪，无需重试")

        try:
            index_task = create_or_get_build_index_task(
                conn, version_id, org_id, retry_failed=True
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    if index_task["should_submit"]:
        submit_task(index_task["task_id"])

    return {
        "message": "索引重试任务已进入队列" if index_task["should_submit"] else "索引任务已在执行中",
        "version_id": version_id,
        "index_status": index_task["index_status"],
        "index_task_id": index_task["task_id"],
        "index_task_status": index_task["task_status"],
    }

@app.put("/api/knowledge/items/{item_id}/lifecycle")
@app.put("/api/knowledge/items/{item_id}/status")
def update_knowledge_lifecycle(
    item_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    更新知识条目管理状态（停用与重新启用，PRD FR15）：
    - disabled：立即退出正式检索，但保留在维护列表中供管理员查阅与编辑。
    - active：重新启用，若已确认且索引就绪立即恢复检索资格。
    """
    org_id = admin["organization_id"]
    new_status = payload.get("lifecycle_status") or payload.get("status")
    if new_status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="非法 lifecycle_status 值，仅支持 active 或 disabled")

    now_iso = datetime.now(timezone.utc).isoformat()
    blocked_detail: Optional[str] = None
    previous_status = None
    with get_db() as conn:
        item = conn.execute(
            """
            SELECT ki.id, ki.lifecycle_status, ki.active_version_id, ki.is_excluded,
                   ki.access_scope AS item_scope, d.id AS document_id, d.is_deleted,
                   d.active_version_id AS document_active_version_id, d.access_scope AS document_scope,
                   kv.review_status, kv.index_status, kv.source_document_version_id,
                   kv.valid_from, kv.valid_until,
                   (SELECT COUNT(*) FROM knowledge_evidence WHERE knowledge_version_id = kv.id) AS evidence_count
            FROM knowledge_items ki
            JOIN documents d ON d.id = ki.document_id
            LEFT JOIN knowledge_versions kv ON kv.id = ki.active_version_id
            WHERE ki.id = ? AND ki.organization_id = ? AND ki.lifecycle_status != 'deleted'
            """,
            (item_id, org_id)
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="知识条目不存在或已被删除")

        previous_status = item["lifecycle_status"]
        reasons: List[str] = []
        if new_status == "active":
            if int(item["is_excluded"] or 0) != 0:
                reasons.append("条目已被标记为不收录")
            if int(item["is_deleted"] or 0) != 0:
                reasons.append("来源文件已删除")
            if not item["active_version_id"] or item["review_status"] != "confirmed":
                reasons.append("当前服务版本尚未确认")
            if item["index_status"] != "ready":
                reasons.append("当前服务版本索引尚未就绪")
            if item["document_active_version_id"] != item["source_document_version_id"]:
                reasons.append("来源文件版本不是当前生效文件版本")
            if int(item["evidence_count"] or 0) == 0:
                reasons.append("当前服务版本缺少可追溯来源证据")
            if item["valid_from"] and item["valid_from"] > now_iso:
                reasons.append("尚未到生效时间")
            if item["valid_until"] and item["valid_until"] <= now_iso:
                reasons.append("知识已超过有效期")
            if item["document_scope"] == "admin_only" and item["item_scope"] == "org_internal":
                # 文件权限是上限，虽然正式资格仍会安全拒绝普通成员，但重新启用前提示管理员修正语义不一致。
                reasons.append("知识权限范围宽于来源文件，请先收紧知识权限")

        result = "success"
        if reasons:
            blocked_detail = "重新启用失败：" + "；".join(reasons)
            result = "blocked"
        elif previous_status != new_status:
            conn.execute(
                "UPDATE knowledge_items SET lifecycle_status = ?, updated_at = ? WHERE id = ?",
                (new_status, now_iso, item_id)
            )
            if new_status == "disabled":
                # M02-E（R5）：引用该知识的 Skill 按 FR14 处理
                rc.notify_atom_change(conn, org_id, [item_id], "disabled", admin["id"])

        conn.execute(
            """
            INSERT INTO audit_logs
            (id, organization_id, user_id, action, target_type, target_id, details, created_at)
            VALUES (?, ?, ?, 'update_knowledge_lifecycle', 'knowledge_item', ?, ?, ?)
            """,
            (
                f"aud_{uuid.uuid4().hex[:12]}", org_id, admin["id"], item_id,
                json.dumps({
                    "from": previous_status,
                    "to": new_status,
                    "result": result,
                    "reason": blocked_detail,
                }, ensure_ascii=False),
                now_iso,
            )
        )

    if blocked_detail:
        raise HTTPException(status_code=409, detail=blocked_detail)

    msg = (
        "知识已重新启用；正式使用仍由当前时间、权限与来源资格实时校验"
        if new_status == "active"
        else "知识已停用，立即退出正式检索但保留维护能力"
    )
    return {
        "message": msg,
        "item_id": item_id,
        "lifecycle_status": new_status,
        "previous_status": previous_status,
    }

@app.put("/api/knowledge/items/{item_id}/access-scope")
def update_knowledge_access_scope(
    item_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    受控修改知识条目访问权限范围（PRD 3.2, Stage 4A）：
    - 规则 14：文件权限是知识权限上限，知识可以更严格，不能比文件更开放。
    - 来源文件为 admin_only 时，禁止放宽知识条目为 org_internal。
    - 记录审计日志，不记录全文与敏感密钥。
    """
    org_id = admin["organization_id"]
    new_scope = payload.get("access_scope")
    if new_scope not in ("admin_only", "org_internal"):
        raise HTTPException(status_code=400, detail="非法 access_scope 值，仅支持 admin_only 或 org_internal")

    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT ki.id, ki.organization_id, ki.document_id, ki.access_scope as ki_scope,
                   d.access_scope as doc_scope
            FROM knowledge_items ki
            JOIN documents d ON ki.document_id = d.id
            WHERE ki.id = ? AND ki.organization_id = ? AND ki.lifecycle_status != 'deleted'
            """,
            (item_id, org_id)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="知识条目不存在或已被删除")

        if row["doc_scope"] == "admin_only" and new_scope == "org_internal":
            raise HTTPException(
                status_code=400,
                detail="设置失败：来源文件权限为「管理员专享(admin_only)」，知识条目权限不能比文件更开放"
            )

        old_scope = row["ki_scope"]
        if old_scope != new_scope:
            conn.execute(
                "UPDATE knowledge_items SET access_scope = ?, updated_at = ? WHERE id = ?",
                (new_scope, now_iso, item_id)
            )
            conn.execute(
                """
                INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
                VALUES (?, ?, ?, 'update_knowledge_access_scope', 'knowledge_item', ?, ?, ?)
                """,
                (
                    f"aud_{uuid.uuid4().hex[:12]}",
                    org_id,
                    admin["id"],
                    item_id,
                    json.dumps({"from": old_scope, "to": new_scope}, ensure_ascii=False),
                    now_iso,
                )
            )
            if new_scope == "admin_only":
                # M02-E（R4 / R5）：引用该知识的 Skill 重新推导可见范围并按 FR14 处理
                rc.notify_atom_change(conn, org_id, [item_id], "scope_tightened", admin["id"])

    return {
        "message": f"知识条目访问权限已成功更新为「{'企业内部可用' if new_scope == 'org_internal' else '管理员专享'}」",
        "item_id": item_id,
        "access_scope": new_scope,
        "previous_scope": old_scope,
    }

@app.get("/api/knowledge/search")
def search_knowledge(
    q: str,
    document_id: Optional[str] = None,
    category: Optional[List[str]] = Query(default=None),
    customer_type: Optional[List[str]] = Query(default=None),
    business_scene: Optional[List[str]] = Query(default=None),
    problem_tag: Optional[List[str]] = Query(default=None),
    admin: Dict[str, Any] = Depends(require_admin)
):
    """Stage 4B/4C 管理员正式混合检索；未来受控业务入口可在独立模块复用 hybrid_search。"""
    query_text = (q or "").strip()
    if not query_text:
        return {"items": [], "total": 0, "query": "", "retrieval_mode": "hybrid_rrf"}

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as conn:
            results = hybrid_search(
                conn=conn,
                current_user=admin,
                query=query_text,
                now_iso=now_iso,
                document_id=document_id,
                category=category,
                customer_type=customer_type,
                business_scene=business_scene,
                problem_tag=problem_tag,
            )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"语义检索暂不可用：{exc}",
        ) from exc

    return {
        "query": query_text,
        "total": len(results),
        "items": results,
        "retrieval_mode": "hybrid_rrf",
    }


@app.get("/api/knowledge/items/{item_id}/deletion-impact")
def get_knowledge_deletion_impact(
    item_id: str,
    admin: Dict[str, Any] = Depends(require_admin)
):
    """知识删除前返回真实影响；原始文件不随知识条目删除。"""
    org_id = admin["organization_id"]
    with get_db() as conn:
        item = conn.execute(
            """
            SELECT ki.id, kv.title
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON kv.id = ki.active_version_id
            JOIN documents d ON d.id = ki.document_id
            WHERE ki.id = ? AND ki.organization_id = ?
              AND ki.lifecycle_status != 'deleted' AND d.is_deleted = 0
            """,
            (item_id, org_id)
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="知识条目不存在或已被删除")

        version_ids = [
            row["id"] for row in conn.execute(
                "SELECT id FROM knowledge_versions WHERE item_id = ? AND organization_id = ?",
                (item_id, org_id)
            ).fetchall()
        ]
        retrieval_count = 0
        active_task_count = 0
        related_case_refs = 0
        if version_ids:
            placeholders = ",".join("?" for _ in version_ids)
            retrieval_count = conn.execute(
                f"SELECT COUNT(*) FROM retrieval_records WHERE knowledge_version_id IN ({placeholders})",
                version_ids
            ).fetchone()[0]
            active_task_count = conn.execute(
                f"""
                SELECT COUNT(*) FROM processing_tasks
                WHERE target_id IN ({placeholders})
                  AND status IN ('queued', 'running')
                """,
                version_ids
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT related_cases_json FROM knowledge_versions WHERE id IN ({placeholders})",
                version_ids
            ).fetchall()
            for row in rows:
                try:
                    related_case_refs += len(json.loads(row["related_cases_json"] or "[]"))
                except (TypeError, json.JSONDecodeError):
                    pass

        skill_refs = rc.count_skill_references(conn, org_id, [item_id])

        return {
            "item_id": item_id,
            "title": item["title"],
            "version_count": len(version_ids),
            "retrieval_record_count": int(retrieval_count or 0),
            "active_task_count": int(active_task_count or 0),
            "related_case_reference_count": related_case_refs,
            "skill_reference_count": skill_refs["count"],
            "skill_reference_status_counts": skill_refs["status_counts"],
            "skill_references": skill_refs["skills"],
            "source_document_preserved": True,
        }


@app.get("/api/knowledge/items/{item_id}/versions/{version_id}")
def get_knowledge_version_history(
    item_id: str,
    version_id: str,
    admin: Dict[str, Any] = Depends(require_admin)
):
    """读取历史知识版本；历史记录只读，不通过此接口提供修改能力。"""
    org_id = admin["organization_id"]
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT kv.*, ki.active_version_id, ki.pending_version_id,
                   d.title AS document_title,
                   dv.version_label AS document_version_label,
                   dv.file_name AS document_file_name
            FROM knowledge_versions kv
            JOIN knowledge_items ki ON ki.id = kv.item_id
            JOIN documents d ON d.id = ki.document_id
            JOIN document_versions dv ON dv.id = kv.source_document_version_id
            WHERE kv.id = ? AND kv.item_id = ? AND kv.organization_id = ?
              AND ki.organization_id = ? AND ki.lifecycle_status != 'deleted'
              AND d.is_deleted = 0
            """,
            (version_id, item_id, org_id, org_id)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="历史知识版本不存在或不可访问")

        evidence_rows = conn.execute(
            """
            SELECT ke.id, ke.source_block_id, ke.field_name, ke.excerpt, ke.accuracy_level,
                   sb.block_index, sb.block_type, sb.heading_path, sb.page_number,
                   sb.paragraph_anchor, sb.text_content
            FROM knowledge_evidence ke
            LEFT JOIN source_blocks sb ON sb.id = ke.source_block_id
            WHERE ke.knowledge_version_id = ? AND ke.organization_id = ?
            ORDER BY sb.block_index, ke.created_at
            """,
            (version_id, org_id)
        ).fetchall()

        return {
            "readonly": True,
            "is_current": row["active_version_id"] == version_id,
            "is_pending": row["pending_version_id"] == version_id,
            "document_title": row["document_title"],
            "document_version_label": row["document_version_label"],
            "document_file_name": row["document_file_name"],
            "version": {
                "id": row["id"],
                "version_number": row["version_number"],
                "title": row["title"],
                "content": row["content"],
                "primary_category": row["primary_category"],
                "atom_type": row["atom_type"],
                "subject": row["subject"],
                "statement": row["statement"],
                "conditions": json.loads(row["conditions_json"] or "[]"),
                "actions": json.loads(row["actions_json"] or "[]"),
                "exceptions": json.loads(row["exceptions_json"] or "[]"),
                "metric_definition": json.loads(row["metric_definition_json"]) if row["metric_definition_json"] else None,
                "case_details": json.loads(row["case_details_json"]) if row["case_details_json"] else None,
                "customer_types": json.loads(row["customer_types_json"] or "[]"),
                "business_scenes": json.loads(row["business_scenes_json"] or "[]"),
                "problem_tags": json.loads(row["problem_tags_json"] or "[]"),
                "related_cases": json.loads(row["related_cases_json"] or "[]"),
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "review_status": row["review_status"],
                "index_status": row["index_status"],
                "created_at": row["created_at"],
                "created_by": row["created_by"],
                "reviewed_at": row["reviewed_at"],
                "reviewed_by": row["reviewed_by"],
                "source_document_version_id": row["source_document_version_id"],
            },
            "evidence": [dict(ev) for ev in evidence_rows],
        }


@app.post("/api/knowledge/items/{item_id}/versions/{version_id}/restore")
def restore_knowledge_history_as_draft(
    item_id: str,
    version_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    """恢复历史内容时创建新的待确认草稿，不修改任何历史版本。"""
    org_id = admin["organization_id"]
    client_token = payload.get("revision_token")
    if not client_token:
        raise HTTPException(status_code=400, detail="缺少 revision_token，无法执行并发安全恢复")
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        item = conn.execute(
            """
            SELECT ki.id, ki.active_version_id, ki.pending_version_id,
                   kv.revision_token AS active_revision_token,
                   kv.valid_from AS current_valid_from,
                   kv.valid_until AS current_valid_until
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON kv.id = ki.active_version_id
            JOIN documents d ON d.id = ki.document_id
            WHERE ki.id = ? AND ki.organization_id = ?
              AND ki.lifecycle_status != 'deleted' AND d.is_deleted = 0
            """,
            (item_id, org_id)
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="知识条目不存在或已被删除")
        if item["pending_version_id"]:
            raise HTTPException(status_code=409, detail="当前已有待切换草稿，请先处理现有草稿后再恢复历史版本")
        if item["active_revision_token"] != client_token:
            raise HTTPException(status_code=409, detail="当前知识已发生变化，请刷新后再恢复历史版本")
        if item["active_version_id"] == version_id:
            raise HTTPException(status_code=409, detail="所选版本已经是当前服务版本，无需恢复")

        history = conn.execute(
            """
            SELECT *
            FROM knowledge_versions
            WHERE id = ? AND item_id = ? AND organization_id = ?
            """,
            (version_id, item_id, org_id)
        ).fetchone()
        if not history:
            raise HTTPException(status_code=404, detail="要恢复的历史版本不存在")

        next_number = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM knowledge_versions WHERE item_id = ?",
            (item_id,)
        ).fetchone()[0]
        new_id = f"kv_{uuid.uuid4().hex[:12]}"
        new_token = uuid.uuid4().hex

        conn.execute(
            """
            INSERT INTO knowledge_versions (
                id, item_id, organization_id, source_document_version_id, version_number,
                title, content, primary_category, atom_type, subject, statement,
                conditions_json, actions_json, exceptions_json, metric_definition_json, case_details_json,
                field_states_json, quality_flags_json, customer_types_json, business_scenes_json, problem_tags_json,
                source_anchors_json, valid_from, valid_until, review_status, index_status, revision_token,
                extraction_context_json, related_cases_json,
                business_importance, importance_rationale, importance_adjusted_by,
                created_at, created_by
            )
            SELECT ?, item_id, organization_id, source_document_version_id, ?,
                   title, content, primary_category, atom_type, subject, statement,
                   conditions_json, actions_json, exceptions_json, metric_definition_json, case_details_json,
                   field_states_json, quality_flags_json, customer_types_json, business_scenes_json, problem_tags_json,
                   source_anchors_json, ?, ?, 'pending_review', 'not_indexed', ?,
                   extraction_context_json, related_cases_json,
                   business_importance, importance_rationale, importance_adjusted_by,
                   ?, ?
            FROM knowledge_versions WHERE id = ?
            """,
            (
                new_id, next_number,
                item["current_valid_from"], item["current_valid_until"],
                new_token, now_iso, admin["id"], version_id
            )
        )

        evidence = conn.execute(
            """
            SELECT source_block_id, field_name, excerpt, accuracy_level
            FROM knowledge_evidence
            WHERE knowledge_version_id = ? AND organization_id = ?
            """,
            (version_id, org_id)
        ).fetchall()
        for ev in evidence:
            conn.execute(
                """
                INSERT INTO knowledge_evidence
                (id, knowledge_version_id, source_block_id, organization_id,
                 field_name, excerpt, accuracy_level, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"ke_{uuid.uuid4().hex[:12]}", new_id, ev["source_block_id"], org_id,
                    ev["field_name"], ev["excerpt"], ev["accuracy_level"], now_iso
                )
            )

        conn.execute(
            "UPDATE knowledge_items SET pending_version_id = ?, updated_at = ? WHERE id = ?",
            (new_id, now_iso, item_id)
        )
        conn.execute(
            """
            INSERT INTO audit_logs
            (id, organization_id, user_id, action, target_type, target_id, details, created_at)
            VALUES (?, ?, ?, 'restore_knowledge_history', 'knowledge_item', ?, ?, ?)
            """,
            (
                f"aud_{uuid.uuid4().hex[:12]}", org_id, admin["id"], item_id,
                json.dumps({
                    "source_history_version_id": version_id,
                    "new_draft_version_id": new_id,
                    "new_version_number": next_number,
                    "source_document_version_id": history["source_document_version_id"],
                    "result": "draft_created",
                }, ensure_ascii=False),
                now_iso,
            )
        )

        return {
            "message": "历史内容已恢复为新的待确认草稿；历史版本本身未被修改",
            "draft_version_id": new_id,
            "version_number": next_number,
            "revision_token": new_token,
            "source_history_version_id": version_id,
        }


@app.delete("/api/knowledge/items/{item_id}")
def delete_knowledge_item(
    item_id: str,
    action_type: Optional[str] = "delete",
    reason: Optional[str] = None,
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    逻辑删除或排除（不收录）知识条目：
    删除或排除后不再计入日常维护列表与分类统计，撤销后续检索资格，且后续重试不会自动复活。
    """
    org_id = admin["organization_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    if action_type not in ("delete", "exclude"):
        raise HTTPException(status_code=400, detail="非法操作类型")

    cleanup_task_ids: List[str] = []
    with get_db() as conn:
        item = conn.execute(
            """
            SELECT id, active_version_id, pending_version_id
            FROM knowledge_items
            WHERE id = ? AND organization_id = ?
              AND lifecycle_status != 'deleted'
              AND (is_excluded = 0 OR is_excluded IS NULL)
            """,
            (item_id, org_id)
        ).fetchone()

        if not item:
            raise HTTPException(status_code=404, detail="知识条目不存在或已被处理")

        version_ids = [
            row["id"] for row in conn.execute(
                "SELECT id FROM knowledge_versions WHERE item_id = ? AND organization_id = ?",
                (item_id, org_id)
            ).fetchall()
        ]

        if action_type == "exclude":
            conn.execute(
                """
                UPDATE knowledge_items
                SET is_excluded = 1,
                    excluded_at = ?,
                    excluded_by = ?,
                    exclusion_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_iso, admin["id"], reason or "管理员标记不收录", now_iso, item_id)
            )
            audit_action = "exclude_knowledge_item"
            audit_reason = reason or "管理员标记不收录"
        else:
            conn.execute(
                """
                UPDATE knowledge_items
                SET lifecycle_status = 'deleted',
                    deleted_at = ?,
                    deleted_by = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_iso, admin["id"], now_iso, item_id)
            )
            audit_action = "delete_knowledge_item"
            audit_reason = reason or "管理员删除"

        # M02-E（R5）：引用该知识的 Skill 按 FR14 处理（删除或排除后不能保持旧引用）
        rc.notify_atom_change(conn, org_id, [item_id], "excluded" if action_type == "exclude" else "deleted",
                              admin["id"], {"reason": audit_reason})

        if version_ids:
            placeholders = ",".join("?" for _ in version_ids)
            conn.execute(
                f"""
                UPDATE processing_tasks
                SET status = 'cancelled', error_message = COALESCE(error_message, '知识条目已失效'), completed_at = ?
                WHERE target_id IN ({placeholders})
                  AND task_type != 'clean_index'
                  AND status IN ('queued', 'running')
                """,
                [now_iso, *version_ids]
            )
            for version_id in version_ids:
                result = create_or_get_clean_index_task(conn, version_id, org_id, retry_failed=False)
                if result["should_submit"]:
                    cleanup_task_ids.append(result["task_id"])

        conn.execute(
            f"""
            INSERT INTO audit_logs (id, organization_id, user_id, action, target_type, target_id, details, created_at)
            VALUES (?, ?, ?, '{audit_action}', 'knowledge_item', ?, ?, ?)
            """,
            (
                f"aud_{uuid.uuid4().hex[:12]}",
                org_id,
                admin["id"],
                item_id,
                json.dumps({
                    "reason": audit_reason,
                    "affected_version_count": len(version_ids),
                    "cleanup_task_count": len(cleanup_task_ids),
                    "result": "eligibility_revoked",
                }, ensure_ascii=False),
                now_iso,
            )
        )

        if reason and item["active_version_id"]:
            curr_v = conn.execute(
                "SELECT quality_flags_json FROM knowledge_versions WHERE id = ?",
                (item["active_version_id"],)
            ).fetchone()
            q_list = json.loads(curr_v["quality_flags_json"] or "[]") if curr_v else []
            q_list.append(f"管理员操作「{'不收录' if action_type == 'exclude' else '删除'}」: {reason}")
            conn.execute(
                "UPDATE knowledge_versions SET quality_flags_json = ? WHERE id = ?",
                (json.dumps(q_list, ensure_ascii=False), item["active_version_id"])
            )

    for task_id in cleanup_task_ids:
        submit_task(task_id)

    status_str = "excluded" if action_type == "exclude" else "deleted"
    msg = (
        "知识候选已排除（不收录）并保留记录"
        if action_type == "exclude"
        else "知识条目已停止使用并完成逻辑删除"
    )
    return {
        "message": msg,
        "id": item_id,
        "status": status_str,
        "is_excluded": (action_type == "exclude"),
        "cleanup_task_count": len(cleanup_task_ids),
        "cleanup_status": "pending" if cleanup_task_ids else "not_needed",
    }

@app.post("/api/knowledge/items/{item_id}/exclude")
def exclude_knowledge_item(
    item_id: str,
    payload: Optional[Dict[str, Any]] = None,
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    明确排除（不收录）知识候选（PRD FR13, Stage 4A）：
    设置 is_excluded=1，不写入 lifecycle_status，不阻塞新文件版本启用。
    """
    reason = (payload or {}).get("reason") if payload else None
    return delete_knowledge_item(item_id=item_id, action_type="exclude", reason=reason, admin=admin)

@app.get("/api/knowledge/tags")
def list_knowledge_tags(admin: Dict[str, Any] = Depends(require_admin)):
    """
    获取企业内部现存规范化业务标签（客户类型、业务场景、问题标签）
    """
    org_id = admin["organization_id"]
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT kv.customer_types_json, kv.business_scenes_json, kv.problem_tags_json
            FROM knowledge_versions kv
            JOIN knowledge_items ki ON kv.item_id = ki.id
            WHERE ki.organization_id = ? AND ki.lifecycle_status != 'deleted'
            """,
            (org_id,)
        ).fetchall()

        c_types = set()
        b_scenes = set()
        p_tags = set()

        for r in rows:
            for t in json.loads(r["customer_types_json"] or "[]"):
                if t.strip(): c_types.add(t.strip())
            for t in json.loads(r["business_scenes_json"] or "[]"):
                if t.strip(): b_scenes.add(t.strip())
            for t in json.loads(r["problem_tags_json"] or "[]"):
                if t.strip(): p_tags.add(t.strip())

        # 结合常见物业预置标签
        default_customers = ["住宅业主", "商业租户", "政企客户", "外部访客"]
        default_scenes = ["前台接待", "客诉处置", "工程查验", "设备巡检", "保洁作业", "绿化养护", "秩序维护", "应急抢险"]
        default_problems = ["设备异常", "响应超时", "跑冒滴漏", "电梯困人", "噪音扰邻", "违规装修", "收费争议"]

        for d in default_customers: c_types.add(d)
        for d in default_scenes: b_scenes.add(d)
        for d in default_problems: p_tags.add(d)

        return {
            "customer_types": sorted(list(c_types)),
            "business_scenes": sorted(list(b_scenes)),
            "problem_tags": sorted(list(p_tags)),
        }


# ---------------------------------------------------------------------------
# M02 Skill 工厂：场景目录（PRD 第 5 章 FR01~FR03）与原子召回预览（FR04）
# 全部接口仅管理员可用，企业范围取自服务端会话，不信任客户端传入的企业标识。
# ---------------------------------------------------------------------------

def _scene_error(exc: SceneCatalogError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


@app.get("/api/skill-factory/scene-catalog")
def get_scene_catalog(admin: Dict[str, Any] = Depends(require_admin)):
    org_id = admin["organization_id"]
    with get_db() as conn:
        scenes = sc.list_scenes(conn, org_id)
        pending = sc.list_pending_tags(conn, org_id, status="pending")
        latest = sc.get_latest_suggestion(conn, org_id)
        unorganized = sc.collect_unorganized_tags(conn, admin)
    return {
        "has_catalog": bool(scenes),
        "scenes": scenes,
        "pending_tag_count": len(pending),
        "unorganized_tag_count": len(unorganized),
        "latest_suggestion": latest,
    }


@app.get("/api/skill-factory/scene-catalog/tag-stats")
def get_scene_tag_stats(admin: Dict[str, Any] = Depends(require_admin)):
    with get_db() as conn:
        stats = sc.collect_scene_tag_stats(conn, admin)
    return {"tags": stats, "tag_count": len(stats)}


@app.post("/api/skill-factory/scene-catalog/suggestions")
def request_scene_merge_suggestion(admin: Dict[str, Any] = Depends(require_admin)):
    import config
    if not config.DEEPSEEK_API_KEY:
        raise HTTPException(status_code=503, detail="暂时无法自动整理场景，可以先手动新建场景")
    try:
        with get_db() as conn:
            suggestion = sc.create_merge_suggestion(conn, admin)
    except SceneCatalogError as exc:
        raise _scene_error(exc)
    enqueue_scene_merge_suggestion(suggestion["suggestion_id"])
    return suggestion


@app.get("/api/skill-factory/scene-catalog/suggestions/{suggestion_id}")
def get_scene_merge_suggestion(suggestion_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    with get_db() as conn:
        suggestion = sc.get_suggestion(conn, admin["organization_id"], suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="归并建议不存在")
    return suggestion


@app.post("/api/skill-factory/scene-catalog/suggestions/{suggestion_id}/confirm")
def confirm_scene_merge_suggestion(
    suggestion_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        with get_db() as conn:
            result = sc.confirm_merge_suggestion(
                conn, admin["organization_id"], admin["id"], suggestion_id, payload.get("groups") or []
            )
    except SceneCatalogError as exc:
        raise _scene_error(exc)
    return result


@app.post("/api/skill-factory/scene-catalog/suggestions/{suggestion_id}/discard")
def discard_scene_merge_suggestion(suggestion_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    try:
        with get_db() as conn:
            return sc.discard_merge_suggestion(conn, admin["organization_id"], admin["id"], suggestion_id)
    except SceneCatalogError as exc:
        raise _scene_error(exc)


@app.get("/api/skill-factory/scene-catalog/pending-tags")
def list_scene_pending_tags(
    status_filter: Optional[str] = Query("pending", alias="status"),
    admin: Dict[str, Any] = Depends(require_admin),
):
    status_value = None if status_filter in (None, "", "all") else status_filter
    with get_db() as conn:
        items = sc.list_pending_tags(conn, admin["organization_id"], status=status_value)
    return {"items": items, "total": len(items)}


@app.post("/api/skill-factory/scene-catalog/pending-tags/{pending_tag_id}/resolve")
def resolve_scene_pending_tag(
    pending_tag_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        with get_db() as conn:
            return sc.resolve_pending_tag(conn, admin["organization_id"], admin["id"], pending_tag_id, payload)
    except SceneCatalogError as exc:
        raise _scene_error(exc)


@app.post("/api/skill-factory/scenes")
def create_scene_entry(payload: Dict[str, Any], admin: Dict[str, Any] = Depends(require_admin)):
    try:
        with get_db() as conn:
            return sc.create_scene(conn, admin["organization_id"], admin["id"], payload, origin="manual")
    except SceneCatalogError as exc:
        raise _scene_error(exc)


@app.put("/api/skill-factory/scenes/{scene_id}")
def update_scene_entry(scene_id: str, payload: Dict[str, Any], admin: Dict[str, Any] = Depends(require_admin)):
    try:
        with get_db() as conn:
            return sc.update_scene(conn, admin["organization_id"], admin["id"], scene_id, payload)
    except SceneCatalogError as exc:
        raise _scene_error(exc)


@app.post("/api/skill-factory/scenes/{scene_id}/disable")
def disable_scene_entry(scene_id: str, payload: Optional[Dict[str, Any]] = None,
                        admin: Dict[str, Any] = Depends(require_admin)):
    try:
        with get_db() as conn:
            return sc.set_scene_status(conn, admin["organization_id"], admin["id"], scene_id, "disabled", payload)
    except SceneCatalogError as exc:
        raise _scene_error(exc)


@app.post("/api/skill-factory/scenes/{scene_id}/enable")
def enable_scene_entry(scene_id: str, payload: Optional[Dict[str, Any]] = None,
                       admin: Dict[str, Any] = Depends(require_admin)):
    try:
        with get_db() as conn:
            return sc.set_scene_status(conn, admin["organization_id"], admin["id"], scene_id, "active", payload)
    except SceneCatalogError as exc:
        raise _scene_error(exc)


@app.delete("/api/skill-factory/scenes/{scene_id}")
def delete_scene_entry(scene_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    try:
        with get_db() as conn:
            scene = sc.delete_scene(conn, admin["organization_id"], admin["id"], scene_id)
    except SceneCatalogError as exc:
        raise _scene_error(exc)
    return {"message": "场景已删除", "scene_id": scene["scene_id"]}


@app.get("/api/skill-factory/scenes/cards")
def list_scene_cards(force_refresh: bool = Query(False), background_refresh: bool = Query(False),
                     admin: Dict[str, Any] = Depends(require_admin)):
    """FR03 场景入口：只列启用场景；可用原子数以 recall_atoms 结果为准。"""
    import config
    from scene_card_cache import get_scene_cards, get_scene_cards_async
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        has_catalog = sc.catalog_exists(conn, admin["organization_id"])
        scenes = sc.list_scenes(conn, admin["organization_id"], status="active")
        read_cards = get_scene_cards_async if background_refresh else get_scene_cards
        cards = read_cards(conn, admin, scenes, now_iso, force_refresh=force_refresh)
        running = sg.running_batches_by_scene(conn, admin["organization_id"])
        latest = {}
        for batch in sg.list_batches(conn, admin["organization_id"], limit=200):
            latest.setdefault(batch["scene_id"], batch)
    for card in cards:
        card["running_batch_id"] = running.get(card["scene_id"])
        card["latest_batch"] = latest.get(card["scene_id"])
    available = bool(config.DEEPSEEK_API_KEY)
    return {
        "has_catalog": has_catalog,
        "cards": cards,
        "min_atoms_for_generation": SCENE_MIN_ATOMS_FOR_GENERATION,
        "generation_available": available,
        "generation_unavailable_reason": None if available else "生成服务暂未配置，暂时不能生成 Skill",
    }


@app.get("/api/skill-factory/scenes/{scene_id}/recall")
def preview_scene_recall(scene_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    with get_db() as conn:
        scene = sc.get_scene(conn, admin["organization_id"], scene_id)
        if not scene:
            raise HTTPException(status_code=404, detail="场景不存在")
        return sc.recall_atoms(conn, admin, scene)


# ---------------------------------------------------------------------------
# M02 Skill 工厂：生成批次（PRD 第 6 章 FR04~FR08、6.2、6.3）与最小候选查看
# ---------------------------------------------------------------------------

@app.post("/api/skill-factory/scenes/{scene_id}/batches")
def start_skill_generation_batch(
    scene_id: str,
    payload: Optional[Dict[str, Any]] = None,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """G1 发起：同一场景已有进行中批次（AC07）或可用知识不足 3 条（AC04）时拒绝。"""
    import config
    if not config.DEEPSEEK_API_KEY:
        raise HTTPException(status_code=503, detail="生成服务暂未配置，暂时不能生成 Skill")
    try:
        with get_db() as conn:
            batch = sg.start_generation_batch(conn, admin, scene_id, (payload or {}).get("focus_note"))
    except SceneCatalogError as exc:
        raise _scene_error(exc)
    enqueue_skill_generation_task(batch["task_id"])
    return batch


@app.get("/api/skill-factory/statistics")
def get_skill_statistics(
    scene_id: Optional[str] = Query(None),
    batch_id: Optional[str] = Query(None),
    admin: Dict[str, Any] = Depends(require_admin),
):
    with get_db() as conn:
        return ss.get_statistics(conn, admin, scene_id=scene_id, batch_id=batch_id)


@app.get("/api/skill-factory/batches")
def list_skill_generation_batches(
    scene_id: Optional[str] = Query(None),
    admin: Dict[str, Any] = Depends(require_admin),
):
    with get_db() as conn:
        items = sg.list_batches(conn, admin["organization_id"], scene_id=scene_id)
    return {"items": items, "total": len(items)}


@app.get("/api/skill-factory/batches/{batch_id}")
def get_skill_generation_batch(batch_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    with get_db() as conn:
        detail = sg.get_batch_detail(conn, admin["organization_id"], batch_id)
    if not detail:
        raise HTTPException(status_code=404, detail="生成记录不存在")
    return detail


@app.get("/api/skill-factory/skills/{skill_id}")
def get_skill_candidate(skill_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    """最小候选查看：完整 JSON 只读（完整审核工作台在 M02-D）。"""
    with get_db() as conn:
        detail = sg.get_skill_detail(conn, admin["organization_id"], skill_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return detail


# ---------------------------------------------------------------------------
# M02 Skill 工厂：审核（PRD 第 7 章状态流转、第 8 章 FR09~FR13）
# 审核人即管理员（S12）；企业范围取自服务端会话。审核环节只有「退回重生成」调用模型（10.2）。
# ---------------------------------------------------------------------------

def _review_error(exc: SceneCatalogError) -> HTTPException:
    code = getattr(exc, "code", None)
    if code:
        detail: Any = {"message": exc.message, "code": code, **(getattr(exc, "extra", None) or {})}
    else:
        detail = exc.message
    return HTTPException(status_code=exc.status_code, detail=detail)


@app.get("/api/skill-factory/skills")
def list_skill_candidates(
    scene_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    batch_id: Optional[str] = Query(None),
    has_unsupported: Optional[str] = Query(None),
    needs_recheck: bool = Query(False),
    admin: Dict[str, Any] = Depends(require_admin),
):
    """FR09 候选列表：默认待复核优先，其次待审核按生成把握度从低到高。"""
    with get_db() as conn:
        return sr.list_skills(conn, admin, scene_id=scene_id, status=status_filter, batch_id=batch_id,
                              has_unsupported=has_unsupported, needs_recheck=needs_recheck)


@app.get("/api/skill-factory/skills/{skill_id}/workbench")
def get_skill_workbench(skill_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    try:
        with get_db() as conn:
            return sr.get_workbench(conn, admin, skill_id)
    except SceneCatalogError as exc:
        raise _review_error(exc)


@app.post("/api/skill-factory/skills/{skill_id}/review/check")
def check_skill_review(skill_id: str, payload: Dict[str, Any], admin: Dict[str, Any] = Depends(require_admin)):
    """编辑中实时检查通过门槛（不保存）。"""
    try:
        with get_db() as conn:
            return sr.check_draft(conn, admin, skill_id, payload or {})
    except SceneCatalogError as exc:
        raise _review_error(exc)


@app.put("/api/skill-factory/skills/{skill_id}/draft")
def save_skill_review_draft(skill_id: str, payload: Dict[str, Any], admin: Dict[str, Any] = Depends(require_admin)):
    try:
        with get_db() as conn:
            return sr.save_draft(conn, admin, skill_id, payload or {})
    except SceneCatalogError as exc:
        raise _review_error(exc)


@app.post("/api/skill-factory/skills/{skill_id}/draft/discard")
def discard_skill_review_draft(skill_id: str, payload: Dict[str, Any], admin: Dict[str, Any] = Depends(require_admin)):
    try:
        with get_db() as conn:
            return sr.discard_draft(conn, admin, skill_id, payload or {})
    except SceneCatalogError as exc:
        raise _review_error(exc)


@app.post("/api/skill-factory/skills/{skill_id}/review/{action}")
def submit_skill_review_action(skill_id: str, action: str, payload: Optional[Dict[str, Any]] = None,
                               admin: Dict[str, Any] = Depends(require_admin)):
    """审核动作：approve（通过 / 修改后通过）、regenerate、reject、abandon、restore、recheck（复核处理，FR14）。"""
    handlers = {
        "approve": sr.approve,
        "regenerate": sr.request_regenerate,
        "reject": sr.reject,
        "abandon": sr.abandon,
        "restore": sr.restore,
        "recheck": sr.recheck,
    }
    handler = handlers.get(action)
    if handler is None:
        raise HTTPException(status_code=404, detail="不支持的审核动作")
    if action == "regenerate":
        import config
        if not config.DEEPSEEK_API_KEY:
            raise HTTPException(status_code=503, detail="生成服务暂未配置，暂时不能重新生成")
    try:
        with get_db() as conn:
            result = handler(conn, admin, skill_id, payload or {})
    except SceneCatalogError as exc:
        raise _review_error(exc)
    if action == "regenerate":
        enqueue_skill_review_task(result["task_id"])
    return result


@app.get("/api/skill-factory/skills/{skill_id}/compare")
def compare_skill_versions(
    skill_id: str,
    from_version: Optional[str] = Query(None, alias="from"),
    to_version: Optional[str] = Query(None, alias="to"),
    admin: Dict[str, Any] = Depends(require_admin),
):
    """FR13 字段对照：任意两个版本，默认 AI 原稿对最新版本。"""
    try:
        with get_db() as conn:
            return sr.compare_versions(conn, admin, skill_id, from_version, to_version)
    except SceneCatalogError as exc:
        raise _review_error(exc)


@app.get("/api/skill-factory/skills/{skill_id}/export")
def export_skill_diff(
    skill_id: str,
    fmt: str = Query("markdown", alias="format"),
    from_version: Optional[str] = Query(None, alias="from"),
    to_version: Optional[str] = Query(None, alias="to"),
    admin: Dict[str, Any] = Depends(require_admin),
):
    """FR13 导出：原稿、终稿、差异清单（Markdown 或 JSON）。"""
    from urllib.parse import quote
    from fastapi.responses import Response
    if fmt not in ("markdown", "json"):
        raise HTTPException(status_code=400, detail="导出格式只能是 markdown 或 json")
    try:
        with get_db() as conn:
            body, media_type, filename = sr.export_skill(conn, admin, skill_id, fmt, from_version, to_version)
    except SceneCatalogError as exc:
        raise _review_error(exc)
    return Response(
        content=body.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/skill-factory/skills/{skill_id}/versions/{version_id}/refs")
def get_skill_version_ref_snapshots(skill_id: str, version_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    """AC17：任一历史版本引用的知识快照（原子之后被删除也能看到当时依据的内容），附原子当前状态。"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, version_number FROM skill_versions WHERE id = ? AND skill_id = ? AND organization_id = ?",
            (version_id, skill_id, admin["organization_id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="版本不存在")
        return {"skill_id": skill_id, "version_id": version_id, "version_number": row["version_number"],
                "refs": rc.version_ref_snapshots(conn, admin["organization_id"], version_id)}


@app.get("/api/skill-factory/experiences")
def list_skill_expert_experiences(admin: Dict[str, Any] = Depends(require_admin)):
    """S13 待沉淀经验清单（只做清单）。"""
    with get_db() as conn:
        items = sr.list_experiences(conn, admin)
    return {"items": items, "total": len(items)}


@app.post("/api/test/users/{user_id}/status")
def update_user_status_for_testing(
    user_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    受控管理操作：用于测试 AC36 角色变更和禁用立即生效
    限制：
    1. 仅在开发/测试环境中开放，生产环境禁用
    2. 严格遵循企业租户隔离边界，禁止跨企业修改其他企业账号
    """
    if ENVIRONMENT == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="生产环境禁止调用测试状态变更接口"
        )

    with get_db() as conn:
        target_user = conn.execute(
            "SELECT id, organization_id FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()

        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="目标用户不存在"
            )

        if target_user["organization_id"] != admin["organization_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="越权操作：无权修改其他企业的账号状态"
            )

        new_status = payload.get("account_status")
        new_role = payload.get("role")
        now_iso = datetime.now(timezone.utc).isoformat()

        if new_status:
            if new_status not in ("active", "disabled"):
                raise HTTPException(status_code=400, detail="非法 account_status 值")
            conn.execute(
                "UPDATE users SET account_status = ?, updated_at = ? WHERE id = ?",
                (new_status, now_iso, user_id)
            )
        if new_role:
            if new_role not in ("admin", "member"):
                raise HTTPException(status_code=400, detail="非法 role 值")
            conn.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                (new_role, now_iso, user_id)
            )

    return {"message": "用户状态已更新"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, use_colors=False)
