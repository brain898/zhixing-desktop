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

from fastapi import FastAPI, HTTPException, status, Depends, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from config import (
    SERVER_HOST,
    SERVER_PORT,
    ENVIRONMENT,
    STORAGE_DIR,
    MAX_FILE_SIZE_BYTES,
    ALLOWED_EXTENSIONS,
)
from database import init_db, get_db
from seed import seed_data
from tasks import submit_task, recover_interrupted_tasks
from auth import (
    verify_password,
    create_session,
    destroy_session,
    get_current_user,
    require_admin,
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
    if ENVIRONMENT != "production":
        seed_data(force=False)
    recover_interrupted_tasks()
    yield

app = FastAPI(
    title="知行有策 服务端",
    description="面向物业咨询业务的知识资产与 AI Skill 生产平台后端服务",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/system/status", response_model=SystemStatusResponse)
def get_system_status():
    return {
        "status": "healthy",
        "version": "0.1.0",
        "environment": "local_dev",
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
            "SELECT COUNT(*) as c FROM knowledge_items WHERE organization_id = ? AND lifecycle_status != 'deleted'",
            (org_id,)
        ).fetchone()["c"]
        
        # 统计待确认/待分类
        pending_count = conn.execute(
            """
            SELECT COUNT(*) as c FROM knowledge_versions kv
            JOIN knowledge_items ki ON kv.item_id = ki.id
            WHERE kv.organization_id = ? AND ki.lifecycle_status != 'deleted' 
              AND (kv.review_status = 'pending_review' OR kv.primary_category IS NULL)
            """,
            (org_id,)
        ).fetchone()["c"]
        
        # 五类统计
        category_rows = conn.execute(
            """
            SELECT kv.primary_category, COUNT(DISTINCT ki.id) as c
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON ki.active_version_id = kv.id
            WHERE ki.organization_id = ? AND ki.lifecycle_status != 'deleted'
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
                d.created_at as created_at,
                d.updated_at as updated_at,
                (SELECT COUNT(*) FROM document_versions WHERE document_id = d.id) as version_count,
                dv.version_label as version_label,
                dv.file_name as file_name,
                dv.file_type as file_type,
                dv.file_size as file_size,
                dv.uploaded_at as uploaded_at,
                dv.processing_status as processing_status,
                dv.error_summary as error_summary,
                pt.id as task_id,
                pt.status as task_status,
                pt.attempt_count as attempt_count,
                (SELECT COUNT(*) FROM source_blocks WHERE document_version_id = dv.id) as block_count
            FROM documents d
            LEFT JOIN document_versions dv ON d.active_version_id = dv.id
            LEFT JOIN processing_tasks pt ON pt.id = (
                SELECT id FROM processing_tasks WHERE target_id = dv.id ORDER BY created_at DESC LIMIT 1
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
        storage_path = STORAGE_DIR / storage_rel
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

        # 更新文档的 active_version_id 与 updated_at
        conn.execute(
            "UPDATE documents SET active_version_id = ?, updated_at = ? WHERE id = ?",
            (version_id, now_iso, doc_id)
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
            "SELECT id, title, active_version_id, created_at, updated_at FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
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
def get_original_file(document_id: str, version_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    org_id = admin["organization_id"]
    with get_db() as conn:
        doc = conn.execute(
            "SELECT id FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        ver = conn.execute(
            "SELECT file_name, file_type, storage_reference FROM document_versions WHERE id = ? AND document_id = ? AND organization_id = ?",
            (version_id, document_id, org_id)
        ).fetchone()
        if not ver:
            raise HTTPException(status_code=404, detail="版本文件不存在")

    path = Path(ver["storage_reference"])
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

        task = conn.execute(
            "SELECT id FROM processing_tasks WHERE target_id = ?",
            (version_id,)
        ).fetchone()

        if task:
            task_id = task["id"]
            conn.execute(
                "UPDATE processing_tasks SET status = 'queued', error_message = NULL WHERE id = ?",
                (task_id,)
            )
        else:
            task_id = f"tsk_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO processing_tasks (id, organization_id, target_type, target_id, task_type, status, attempt_count, created_at)
                VALUES (?, ?, 'document_version', ?, 'parse_document', 'queued', 0, ?)
                """,
                (task_id, org_id, version_id, now_iso)
            )

        conn.execute(
            "UPDATE document_versions SET processing_status = 'queued', error_summary = NULL WHERE id = ?",
            (version_id,)
        )

    submit_task(task_id)
    return {"message": "解析任务已重新排队", "task_id": task_id}

@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    org_id = admin["organization_id"]
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        doc = conn.execute(
            "SELECT id, title FROM documents WHERE id = ? AND organization_id = ? AND is_deleted = 0",
            (document_id, org_id)
        ).fetchone()
        if not doc:
            raise HTTPException(status_code=404, detail="资料不存在或已被删除")

        # 逻辑删除文档
        conn.execute(
            "UPDATE documents SET is_deleted = 1, deleted_at = ?, deleted_by = ? WHERE id = ?",
            (now_iso, admin["id"], document_id)
        )

        # 取消与该文档各版本关联的正在排队或执行的任务
        conn.execute(
            """
            UPDATE processing_tasks 
            SET status = 'cancelled', completed_at = ?
            WHERE target_id IN (SELECT id FROM document_versions WHERE document_id = ?)
              AND status IN ('queued', 'running')
            """,
            (now_iso, document_id)
        )

    return {
        "message": f"资料「{doc['title']}」已标记为删除（逻辑删除，相关后台任务已取消）",
        "document_id": document_id,
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
    search: Optional[str] = None,
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    获取知识原子条目列表及真实分类、审核状态统计数据。
    严格隔离跨企业数据，不显示已删除或已删除文档下的条目。
    """
    org_id = admin["organization_id"]

    with get_db() as conn:
        # 1. 基础查询条件
        where_clauses = [
            "ki.organization_id = ?",
            "ki.lifecycle_status NOT IN ('deleted', 'excluded')",
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
                (SELECT COUNT(*) FROM knowledge_evidence WHERE knowledge_version_id = kv.id) as evidence_count
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON ki.active_version_id = kv.id
            JOIN documents d ON ki.document_id = d.id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY kv.review_status ASC, ki.updated_at DESC
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
            })

        # 2. 全量统计（基于当前企业下未删除的有效资料范围）
        scope_where = ["ki.organization_id = ?", "ki.lifecycle_status NOT IN ('deleted', 'excluded')", "d.is_deleted = 0"]
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
                SUM(CASE WHEN kv.review_status = 'confirmed' THEN 1 ELSE 0 END) as c_confirmed
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
        }

        return {
            "items": items,
            "stats": stats,
        }

@app.get("/api/knowledge/items/{item_id}")
def get_knowledge_item_detail(item_id: str, admin: Dict[str, Any] = Depends(require_admin)):
    """
    获取单个知识原子的完整详情、原文证据（带对应结构块正文与锚点）及版本记录
    """
    org_id = admin["organization_id"]
    with get_db() as conn:
        item_row = conn.execute(
            """
            SELECT ki.id, ki.document_id, ki.access_scope, ki.lifecycle_status, ki.created_at, ki.updated_at,
                   d.title as document_title, d.is_deleted as doc_is_deleted,
                   kv.id as active_version_id, kv.source_document_version_id, kv.version_number,
                   kv.title, kv.content, kv.primary_category, kv.atom_type, kv.subject, kv.statement,
                   kv.conditions_json, kv.actions_json, kv.exceptions_json, kv.metric_definition_json, kv.case_details_json,
                   kv.field_states_json, kv.quality_flags_json, kv.customer_types_json, kv.business_scenes_json, kv.problem_tags_json,
                   kv.source_anchors_json, kv.valid_from, kv.valid_until, kv.review_status, kv.reviewed_by, kv.reviewed_at,
                   kv.index_status, kv.revision_token, kv.extraction_context_json, kv.related_cases_json,
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

        return {
            "id": item_row["id"],
            "document_id": item_row["document_id"],
            "document_title": item_row["document_title"],
            "document_version_label": item_row["document_version_label"],
            "document_file_name": item_row["document_file_name"],
            "access_scope": item_row["access_scope"],
            "lifecycle_status": item_row["lifecycle_status"],
            "created_at": item_row["created_at"],
            "updated_at": item_row["updated_at"],
            "active_version": {
                "id": item_row["active_version_id"],
                "version_number": item_row["version_number"],
                "title": item_row["title"],
                "content": item_row["content"],
                "primary_category": item_row["primary_category"],
                "atom_type": item_row["atom_type"],
                "subject": item_row["subject"],
                "statement": item_row["statement"],
                "conditions": json.loads(item_row["conditions_json"] or "[]"),
                "actions": json.loads(item_row["actions_json"] or "[]"),
                "exceptions": json.loads(item_row["exceptions_json"] or "[]"),
                "metric_definition": json.loads(item_row["metric_definition_json"]) if item_row["metric_definition_json"] else None,
                "case_details": json.loads(item_row["case_details_json"]) if item_row["case_details_json"] else None,
                "field_states": json.loads(item_row["field_states_json"] or "{}"),
                "quality_flags": json.loads(item_row["quality_flags_json"] or "[]"),
                "customer_types": json.loads(item_row["customer_types_json"] or "[]"),
                "business_scenes": json.loads(item_row["business_scenes_json"] or "[]"),
                "problem_tags": json.loads(item_row["problem_tags_json"] or "[]"),
                "source_anchors": json.loads(item_row["source_anchors_json"] or "[]"),
                "valid_from": item_row["valid_from"],
                "valid_until": item_row["valid_until"],
                "review_status": item_row["review_status"],
                "reviewed_by": item_row["reviewed_by"],
                "reviewed_at": item_row["reviewed_at"],
                "index_status": item_row["index_status"],
                "revision_token": item_row["revision_token"],
                "extraction_context": json.loads(item_row["extraction_context_json"] or "{}"),
                "related_cases": json.loads(item_row["related_cases_json"] or "[]"),
            },
            "evidence": evidence_list,
            "version_history": history,
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
    2. 支持修改分类、标签、正文、主体、条件、动作、例外与指标。
    3. 动态更新质检问题（quality_flags），更新后仍为 pending_review 待校对状态。
    """
    org_id = admin["organization_id"]
    client_token = payload.get("revision_token")
    if not client_token:
        raise HTTPException(status_code=400, detail="缺少 revision_token，无法执行并发安全保存")

    now_iso = datetime.now(timezone.utc).isoformat()
    new_token = uuid.uuid4().hex

    with get_db() as conn:
        curr = conn.execute(
            """
            SELECT ki.id, ki.active_version_id, kv.revision_token, kv.quality_flags_json, kv.review_status
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON ki.active_version_id = kv.id
            WHERE ki.id = ? AND ki.organization_id = ? AND ki.lifecycle_status != 'deleted'
            """,
            (item_id, org_id)
        ).fetchone()

        if not curr:
            raise HTTPException(status_code=404, detail="知识条目不存在或已被删除")

        # 乐观并发检查
        if curr["revision_token"] != client_token:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该知识条目已被其他窗口或管理员保存，请刷新后重试（并发冲突保护）"
            )

        v_id = curr["active_version_id"]

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
        new_access_scope = payload.get("access_scope") or "admin_only"
        new_valid_from = payload.get("valid_from")
        new_valid_until = payload.get("valid_until")
        new_related_cases = payload.get("related_cases") or []

        # 重新评估质检问题
        q_flags = []
        if not new_statement:
            q_flags.append("缺少核心陈述，无法独立理解")
        if not new_category:
            q_flags.append("待管理员确认主分类")
        if new_category == "指标数据" and not new_metric:
            q_flags.append("指标类知识未提供口径、单位或数值范围")
        if new_category == "项目案例" and not new_case:
            q_flags.append("案例类知识未提供背景措施与实际结果")

        # 保留可能存在的互斥冲突与伪造来源标记
        old_flags = json.loads(curr["quality_flags_json"] or "[]")
        for f in old_flags:
            if "疑似规则冲突" in f or "伪造来源" in f or "来源摘录与原文不匹配" in f:
                if f not in q_flags:
                    q_flags.append(f)

        # 更新 knowledge_versions
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
                v_id,
            )
        )

        conn.execute(
            "UPDATE knowledge_items SET access_scope = ?, updated_at = ? WHERE id = ?",
            (new_access_scope, now_iso, item_id)
        )

    return {
        "message": "知识草稿已保存",
        "revision_token": new_token,
        "quality_flags": q_flags,
    }

@app.post("/api/knowledge/items/{item_id}/confirm")
def confirm_knowledge_item(
    item_id: str,
    payload: Dict[str, Any],
    admin: Dict[str, Any] = Depends(require_admin)
):
    """
    确认知识版本：
    门槛校验（AC11）：
    1. 标题与核心陈述必填。
    2. 主分类不能为 null（待分类必须由人工明确指定分类方可确认）。
    3. 严禁存在伪造来源、摘录不匹配或规则冲突等阻塞性质检问题。
    4. 必须具备至少一条有效来源证据。
    确认后状态：review_status='confirmed', index_status='not_indexed'（已确认，索引未建立，不伪造可检索）。
    """
    org_id = admin["organization_id"]
    client_token = payload.get("revision_token")
    now_iso = datetime.now(timezone.utc).isoformat()
    new_token = uuid.uuid4().hex

    with get_db() as conn:
        curr = conn.execute(
            """
            SELECT ki.id, ki.active_version_id, kv.revision_token, kv.title, kv.statement,
                   kv.primary_category, kv.quality_flags_json,
                   (SELECT COUNT(*) FROM knowledge_evidence WHERE knowledge_version_id = kv.id) as ev_count
            FROM knowledge_items ki
            JOIN knowledge_versions kv ON ki.active_version_id = kv.id
            WHERE ki.id = ? AND ki.organization_id = ? AND ki.lifecycle_status != 'deleted'
            """,
            (item_id, org_id)
        ).fetchone()

        if not curr:
            raise HTTPException(status_code=404, detail="知识条目不存在或已被删除")

        if client_token and curr["revision_token"] != client_token:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该条目已被其他操作修改，请刷新后再确认"
            )

        # 门槛 1：核心字段检查
        if not curr["title"] or not curr["statement"]:
            raise HTTPException(status_code=400, detail="确认失败：标题与核心陈述为必填项")

        # 门槛 2：主分类检查
        if not curr["primary_category"]:
            raise HTTPException(status_code=400, detail="确认失败：主分类仍为「待分类」，确认前必须明确指定五类主分类之一")

        # 门槛 3：来源证据检查
        if (curr["ev_count"] or 0) == 0:
            raise HTTPException(status_code=400, detail="确认失败：知识条目必须具备至少一条原文证据支撑")

        # 门槛 4：关键阻塞性质检问题检查
        q_flags = json.loads(curr["quality_flags_json"] or "[]")
        blocking_issues = [
            f for f in q_flags
            if any(k in f for k in ("伪造来源", "不匹配", "冲突", "无效提取", "缺乏有效原文证据", "无实质业务"))
        ]
        if blocking_issues:
            raise HTTPException(
                status_code=400,
                detail=f"确认失败：存在未解决的阻塞性质量问题：{'; '.join(blocking_issues)}"
            )

        # 执行确认
        conn.execute(
            """
            UPDATE knowledge_versions SET
                review_status = 'confirmed',
                index_status = 'not_indexed',
                reviewed_by = ?,
                reviewed_at = ?,
                revision_token = ?
            WHERE id = ?
            """,
            (admin["id"], now_iso, new_token, curr["active_version_id"])
        )
        conn.execute("UPDATE knowledge_items SET updated_at = ? WHERE id = ?", (now_iso, item_id))

    return {
        "message": "知识条目已确认（当前状态：已确认，索引未建立）",
        "review_status": "confirmed",
        "index_status": "not_indexed",
        "revision_token": new_token,
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
    status_to_set = "excluded" if action_type == "exclude" else "deleted"
    with get_db() as conn:
        item = conn.execute(
            "SELECT id, active_version_id FROM knowledge_items WHERE id = ? AND organization_id = ? AND lifecycle_status NOT IN ('deleted', 'excluded')",
            (item_id, org_id)
        ).fetchone()

        if not item:
            raise HTTPException(status_code=404, detail="知识条目不存在或已被处理")

        conn.execute(
            "UPDATE knowledge_items SET lifecycle_status = ?, deleted_at = ?, deleted_by = ? WHERE id = ?",
            (status_to_set, now_iso, admin["id"], item_id)
        )

        if reason and item["active_version_id"]:
            # 记录排除/删除原因至版本说明
            curr_v = conn.execute("SELECT quality_flags_json FROM knowledge_versions WHERE id = ?", (item["active_version_id"],)).fetchone()
            q_list = json.loads(curr_v["quality_flags_json"] or "[]") if curr_v else []
            q_list.append(f"管理员操作「{'不收录' if action_type == 'exclude' else '删除'}」: {reason}")
            conn.execute(
                "UPDATE knowledge_versions SET quality_flags_json = ? WHERE id = ?",
                (json.dumps(q_list, ensure_ascii=False), item["active_version_id"])
            )

    msg = "知识候选已排除（不收录）并保留记录" if action_type == "exclude" else "知识条目已成功删除"
    return {"message": msg, "id": item_id, "status": status_to_set}

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

@app.post("/api/system/shutdown")
async def shutdown_server():
    """桌面端退出时优雅关闭后台服务"""
    import os
    import asyncio
    
    async def delayed_exit():
        await asyncio.sleep(0.3)
        os._exit(0)
        
    asyncio.create_task(delayed_exit())
    return {"message": "服务端正在关闭"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, use_colors=False)
