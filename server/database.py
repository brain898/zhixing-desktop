import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from config import DATABASE_URL, DATA_DIR

DB_PATH = DATA_DIR / "zhixing.db"
_local = threading.local()

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn

@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'member')),
            account_status TEXT NOT NULL CHECK(account_status IN ('active', 'disabled')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            active_version_id TEXT,
            access_scope TEXT NOT NULL CHECK(access_scope IN ('admin_only', 'org_internal')),
            is_deleted INTEGER NOT NULL DEFAULT 0,
            deleted_at TEXT,
            deleted_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS document_versions (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            version_label TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            storage_reference TEXT NOT NULL,
            uploaded_by TEXT NOT NULL REFERENCES users(id),
            uploaded_at TEXT NOT NULL,
            processing_status TEXT NOT NULL CHECK(processing_status IN ('uploading', 'queued', 'parsing', 'extracting', 'completed', 'partial_failed', 'failed', 'cancelled')),
            error_summary TEXT
        );

        CREATE TABLE IF NOT EXISTS knowledge_items (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            active_version_id TEXT,
            access_scope TEXT NOT NULL CHECK(access_scope IN ('admin_only', 'org_internal')),
            lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('active', 'disabled', 'deleted')),
            deleted_at TEXT,
            deleted_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS knowledge_versions (
            id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            source_document_version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
            version_number INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            primary_category TEXT CHECK(primary_category IN ('制度与标准', '方法与工具', '项目案例', '指标数据', '专家经验') OR primary_category IS NULL),
            atom_type TEXT,
            subject TEXT,
            statement TEXT,
            conditions_json TEXT,
            actions_json TEXT,
            exceptions_json TEXT,
            metric_definition_json TEXT,
            case_details_json TEXT,
            field_states_json TEXT,
            quality_flags_json TEXT,
            customer_types_json TEXT,
            business_scenes_json TEXT,
            problem_tags_json TEXT,
            source_anchors_json TEXT,
            valid_from TEXT,
            valid_until TEXT,
            review_status TEXT NOT NULL CHECK(review_status IN ('pending_review', 'confirmed')),
            reviewed_by TEXT,
            reviewed_at TEXT,
            index_status TEXT NOT NULL CHECK(index_status IN ('not_indexed', 'indexing', 'ready', 'failed', 'pending_cleanup')),
            revision_token TEXT NOT NULL,
            extraction_context_json TEXT,
            related_cases_json TEXT,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS processing_tasks (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            target_type TEXT NOT NULL CHECK(target_type IN ('document_version', 'knowledge_version')),
            target_id TEXT NOT NULL,
            task_type TEXT NOT NULL CHECK(task_type IN ('parse_document', 'extract_atoms', 'build_index', 'clean_index')),
            status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_blocks (
            id TEXT PRIMARY KEY,
            document_version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            block_index INTEGER NOT NULL,
            block_type TEXT NOT NULL,
            heading_path TEXT,
            page_number INTEGER,
            paragraph_anchor TEXT,
            text_content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS knowledge_evidence (
            id TEXT PRIMARY KEY,
            knowledge_version_id TEXT NOT NULL REFERENCES knowledge_versions(id) ON DELETE CASCADE,
            source_block_id TEXT NOT NULL REFERENCES source_blocks(id) ON DELETE CASCADE,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            field_name TEXT NOT NULL,
            excerpt TEXT NOT NULL,
            accuracy_level TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS retrieval_records (
            id TEXT PRIMARY KEY,
            knowledge_version_id TEXT NOT NULL REFERENCES knowledge_versions(id) ON DELETE CASCADE,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            search_text TEXT NOT NULL,
            vector_json TEXT,
            model_name TEXT NOT NULL,
            index_version INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
        CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
        CREATE INDEX IF NOT EXISTS idx_docs_org ON documents(organization_id, is_deleted);
        CREATE INDEX IF NOT EXISTS idx_kitems_org ON knowledge_items(organization_id, lifecycle_status);
        CREATE INDEX IF NOT EXISTS idx_tasks_target ON processing_tasks(target_id, status);
        """)

        # 动态补齐字段（向后兼容已有 sqlite 数据文件）
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(knowledge_versions)").fetchall()]
        if "extraction_context_json" not in cols:
            conn.execute("ALTER TABLE knowledge_versions ADD COLUMN extraction_context_json TEXT")
        if "related_cases_json" not in cols:
            conn.execute("ALTER TABLE knowledge_versions ADD COLUMN related_cases_json TEXT")

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
