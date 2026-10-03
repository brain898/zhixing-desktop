import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from config import DATABASE_URL, DATA_DIR, get_db_path
from skill_constants import (
    BATCH_STATUS_LABELS,
    MERGE_SUGGESTION_STATUS_LABELS,
    PENDING_TAG_STATUS_LABELS,
    REF_ROLES,
    REJECT_REASONS,
    REVIEW_ACTION_LABELS,
    REVIEW_TASK_STATUS_LABELS,
    REVIEW_TASK_TYPE_LABELS,
    EXPERIENCE_STATUS_LABELS,
    SCENE_STATUS_LABELS,
    SKILL_STATUS_LABELS,
    SKILL_TASK_STATUS_LABELS,
    SKILL_TASK_TYPE_LABELS,
    SKILL_VERSION_KIND_LABELS,
    STALE_EFFECT_LABELS,
    STALE_TRIGGER_LABELS,
    VISIBILITY_VALUES,
    sql_in_list,
)

DB_PATH = get_db_path()
_local = threading.local()

def get_connection():
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=20.0, check_same_thread=False)
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
            pending_version_id TEXT,
            access_scope TEXT NOT NULL CHECK(access_scope IN ('admin_only', 'org_internal')),
            lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('active', 'disabled', 'deleted')),
            is_excluded INTEGER NOT NULL DEFAULT 0,
            excluded_at TEXT,
            excluded_by TEXT,
            exclusion_reason TEXT,
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
            business_importance TEXT DEFAULT 'normal' CHECK(business_importance IN ('critical', 'normal', 'informational')),
            importance_rationale TEXT,
            importance_adjusted_by TEXT,
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
            idempotency_key TEXT,
            config_hash TEXT,
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
            ignore_status TEXT DEFAULT 'none' CHECK(ignore_status IN ('none', 'model_suggested_ignore', 'admin_ignored')),
            ignore_reason TEXT,
            ignored_by TEXT,
            ignored_at TEXT,
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
            fragment_key TEXT,
            fragment_type TEXT,
            evidence_ids_json TEXT,
            embedding_dim INTEGER,
            content_hash TEXT,
            config_hash TEXT,
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

        CREATE TABLE IF NOT EXISTS extraction_checkpoints (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            document_version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
            batch_key TEXT NOT NULL,
            batch_index INTEGER NOT NULL,
            section_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            block_ids_json TEXT NOT NULL,
            candidates_json TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jev_evaluations (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            knowledge_version_id TEXT NOT NULL REFERENCES knowledge_versions(id) ON DELETE CASCADE,
            candidate_revision_token TEXT NOT NULL,
            source_document_version_id TEXT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
            requested_model TEXT NOT NULL,
            actual_model TEXT,
            question_definition_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed', 'not_configured', 'disabled', 'stale')),
            idempotency_key TEXT NOT NULL UNIQUE,
            classification_suggestion TEXT,
            classification_disagrees INTEGER NOT NULL DEFAULT 0,
            review_priority TEXT NOT NULL DEFAULT 'not_available' CHECK(review_priority IN ('high', 'medium', 'normal', 'not_available')),
            priority_reasons_json TEXT,
            usage_json TEXT,
            elapsed_ms INTEGER,
            error_code TEXT,
            error_message TEXT,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jev_evaluation_answers (
            id TEXT PRIMARY KEY,
            evaluation_id TEXT NOT NULL REFERENCES jev_evaluations(id) ON DELETE CASCADE,
            question_id TEXT NOT NULL,
            question_type TEXT NOT NULL,
            question_label TEXT NOT NULL,
            relevant_fields_json TEXT NOT NULL,
            answer_value_json TEXT NOT NULL,
            probabilities_json TEXT,
            confidence REAL,
            display_status TEXT NOT NULL,
            ignored_by TEXT,
            ignored_at TEXT,
            ignore_reason TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(evaluation_id, question_id)
        );

        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
        CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
        CREATE INDEX IF NOT EXISTS idx_docs_org ON documents(organization_id, is_deleted);
        CREATE INDEX IF NOT EXISTS idx_kitems_org ON knowledge_items(organization_id, lifecycle_status);
        CREATE INDEX IF NOT EXISTS idx_tasks_target ON processing_tasks(target_id, status);
        CREATE INDEX IF NOT EXISTS idx_extraction_checkpoints_lookup ON extraction_checkpoints(document_version_id, batch_key);
        CREATE INDEX IF NOT EXISTS idx_extraction_checkpoints_org ON extraction_checkpoints(organization_id, document_version_id);
        CREATE INDEX IF NOT EXISTS idx_jev_evaluations_version ON jev_evaluations(knowledge_version_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_jev_evaluations_status ON jev_evaluations(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_jev_answers_evaluation ON jev_evaluation_answers(evaluation_id);
        """)

        # 动态补齐字段（向后兼容已有 sqlite 数据文件）
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(knowledge_versions)").fetchall()]
        if "extraction_context_json" not in cols:
            conn.execute("ALTER TABLE knowledge_versions ADD COLUMN extraction_context_json TEXT")
        if "related_cases_json" not in cols:
            conn.execute("ALTER TABLE knowledge_versions ADD COLUMN related_cases_json TEXT")

        ki_cols = [r["name"] for r in conn.execute("PRAGMA table_info(knowledge_items)").fetchall()]
        if "is_excluded" not in ki_cols:
            conn.execute("ALTER TABLE knowledge_items ADD COLUMN is_excluded INTEGER NOT NULL DEFAULT 0")
        if "excluded_at" not in ki_cols:
            conn.execute("ALTER TABLE knowledge_items ADD COLUMN excluded_at TEXT")
        if "excluded_by" not in ki_cols:
            conn.execute("ALTER TABLE knowledge_items ADD COLUMN excluded_by TEXT")
        if "exclusion_reason" not in ki_cols:
            conn.execute("ALTER TABLE knowledge_items ADD COLUMN exclusion_reason TEXT")
        if "pending_version_id" not in ki_cols:
            conn.execute("ALTER TABLE knowledge_items ADD COLUMN pending_version_id TEXT")

        # 审核减负：正文块覆盖状态与忽略记录
        sb_cols = [r["name"] for r in conn.execute("PRAGMA table_info(source_blocks)").fetchall()]
        if "ignore_status" not in sb_cols:
            conn.execute("ALTER TABLE source_blocks ADD COLUMN ignore_status TEXT DEFAULT 'none'")
        if "ignore_reason" not in sb_cols:
            conn.execute("ALTER TABLE source_blocks ADD COLUMN ignore_reason TEXT")
        if "ignored_by" not in sb_cols:
            conn.execute("ALTER TABLE source_blocks ADD COLUMN ignored_by TEXT")
        if "ignored_at" not in sb_cols:
            conn.execute("ALTER TABLE source_blocks ADD COLUMN ignored_at TEXT")

        # 审核减负：业务重要程度与判断依据（与抽取疑点解耦）
        if "business_importance" not in cols:
            conn.execute("ALTER TABLE knowledge_versions ADD COLUMN business_importance TEXT DEFAULT 'normal'")
        if "importance_rationale" not in cols:
            conn.execute("ALTER TABLE knowledge_versions ADD COLUMN importance_rationale TEXT")
        if "importance_adjusted_by" not in cols:
            conn.execute("ALTER TABLE knowledge_versions ADD COLUMN importance_adjusted_by TEXT")

        # 版本治理：服务指针与待切换指针分离，active_version_id 始终只指向当前服务版本。
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kitems_pending_version ON knowledge_items(pending_version_id)"
        )

        # Stage 4B：补齐持久化索引与幂等任务字段。
        task_cols = [r["name"] for r in conn.execute("PRAGMA table_info(processing_tasks)").fetchall()]
        if "idempotency_key" not in task_cols:
            conn.execute("ALTER TABLE processing_tasks ADD COLUMN idempotency_key TEXT")
        if "config_hash" not in task_cols:
            conn.execute("ALTER TABLE processing_tasks ADD COLUMN config_hash TEXT")

        rr_cols = [r["name"] for r in conn.execute("PRAGMA table_info(retrieval_records)").fetchall()]
        rr_migrations = {
            "fragment_key": "TEXT",
            "fragment_type": "TEXT",
            "evidence_ids_json": "TEXT",
            "embedding_dim": "INTEGER",
            "content_hash": "TEXT",
            "config_hash": "TEXT",
        }
        for column, column_type in rr_migrations.items():
            if column not in rr_cols:
                conn.execute(f"ALTER TABLE retrieval_records ADD COLUMN {column} {column_type}")

        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_build_idempotency
            ON processing_tasks(task_type, target_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_retrieval_version_fragment_config
            ON retrieval_records(knowledge_version_id, fragment_key, config_hash)
            WHERE fragment_key IS NOT NULL AND config_hash IS NOT NULL
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrieval_org_config ON retrieval_records(organization_id, config_hash)"
        )

        # AC44：来源绑定必须在数据库写入层成立，不能仅依赖上层 API。
        # 知识版本只能绑定同企业、同一 knowledge_item 所属文件的 document_version。
        conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS trg_kv_source_consistency_insert
        BEFORE INSERT ON knowledge_versions
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM knowledge_items ki
            JOIN document_versions dv ON dv.id = NEW.source_document_version_id
            WHERE ki.id = NEW.item_id
              AND ki.organization_id = NEW.organization_id
              AND dv.organization_id = NEW.organization_id
              AND dv.document_id = ki.document_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'knowledge source binding mismatch');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_kv_source_consistency_update
        BEFORE UPDATE OF item_id, organization_id, source_document_version_id ON knowledge_versions
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM knowledge_items ki
            JOIN document_versions dv ON dv.id = NEW.source_document_version_id
            WHERE ki.id = NEW.item_id
              AND ki.organization_id = NEW.organization_id
              AND dv.organization_id = NEW.organization_id
              AND dv.document_id = ki.document_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'knowledge source binding mismatch');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_evidence_source_consistency_insert
        BEFORE INSERT ON knowledge_evidence
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM knowledge_versions kv
            JOIN source_blocks sb ON sb.id = NEW.source_block_id
            WHERE kv.id = NEW.knowledge_version_id
              AND kv.organization_id = NEW.organization_id
              AND sb.organization_id = NEW.organization_id
              AND sb.document_version_id = kv.source_document_version_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'knowledge evidence source mismatch');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_evidence_source_consistency_update
        BEFORE UPDATE OF knowledge_version_id, source_block_id, organization_id ON knowledge_evidence
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM knowledge_versions kv
            JOIN source_blocks sb ON sb.id = NEW.source_block_id
            WHERE kv.id = NEW.knowledge_version_id
              AND kv.organization_id = NEW.organization_id
              AND sb.organization_id = NEW.organization_id
              AND sb.document_version_id = kv.source_document_version_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'knowledge evidence source mismatch');
        END;
        """)

        # AC25 自愈保证：文件权限是知识权限上限，如果来源文件为 admin_only，自动纠正下属知识条目为 admin_only
        conn.execute("""
            UPDATE knowledge_items
            SET access_scope = 'admin_only'
            WHERE access_scope = 'org_internal'
              AND document_id IN (
                  SELECT id FROM documents WHERE access_scope = 'admin_only'
              )
        """)

        # 文档与派生知识生命周期一致性自愈保证：若来源文件已被逻辑删除，下属知识条目必须同步标记为已删除
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE knowledge_items
            SET lifecycle_status = 'deleted',
                deleted_at = COALESCE(deleted_at, ?),
                updated_at = ?
            WHERE lifecycle_status != 'deleted'
              AND document_id IN (
                  SELECT id FROM documents WHERE is_deleted = 1
              )
        """, (now_iso, now_iso))

        init_skill_tables(conn)
        init_scene_catalog_tables(conn)
        init_scene_card_cache_table(conn)
        init_skill_generation_tables(conn)
        init_skill_review_tables(conn)
        init_skill_recheck_tables(conn)
        from consult import init_consult_tables
        init_consult_tables(conn)


def init_scene_card_cache_table(conn):
    """Persist recall summaries; live Skill/batch data never belongs in this cache."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scene_card_cache (
            organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('admin', 'member')),
            scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
            input_signature TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            retry_after TEXT,
            PRIMARY KEY (organization_id, role, scene_id)
        )
    """)


def init_skill_recheck_tables(conn):
    """
    M02-E 原子变更复核（PRD R4、R5、R7、FR14）。只做增量变更，不改动 M01 核心表与 processing_tasks。
    - skill_stale_events：M01 原子变更（新版本生效、停用、删除、排除、权限收紧、有效期到期、来源文件换版）
      对引用它的 Skill 当前版本产生的影响记录；复核处理后写 resolved_*。同一 Skill 版本、同一原子版本、
      同一触发类型只保留一条未处理记录。
    - skill_review_drafts.stale_resolutions_json：草稿中对受影响知识的复核选择（更新引用 / 确认无影响 / 移除引用）。
    """
    trigger = sql_in_list(STALE_TRIGGER_LABELS)
    effect = sql_in_list(STALE_EFFECT_LABELS)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS skill_stale_events (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        skill_version_id TEXT NOT NULL,
        atom_item_id TEXT NOT NULL,
        atom_version_id TEXT NOT NULL,
        new_atom_version_id TEXT,
        trigger_type TEXT NOT NULL CHECK(trigger_type IN ({trigger})),
        skill_status TEXT NOT NULL,
        effect TEXT NOT NULL CHECK(effect IN ({effect})),
        detail_json TEXT,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        resolved_by TEXT,
        resolved_record_id TEXT,
        resolution TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_skill_stale_events_skill ON skill_stale_events(skill_id, resolved_at);
    CREATE INDEX IF NOT EXISTS idx_skill_stale_events_org ON skill_stale_events(organization_id, created_at);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_stale_events_open
        ON skill_stale_events(skill_id, skill_version_id, atom_version_id, trigger_type) WHERE resolved_at IS NULL;
    """)
    draft_cols = [r["name"] for r in conn.execute("PRAGMA table_info(skill_review_drafts)").fetchall()]
    if "stale_resolutions_json" not in draft_cols:
        conn.execute("ALTER TABLE skill_review_drafts ADD COLUMN stale_resolutions_json TEXT")


def init_skill_review_tables(conn):
    """
    M02-D 审核（PRD 第 7、8 章 FR09~FR13）。只做增量变更，不改动 M01 核心表与 processing_tasks。
    - skills.revision_token：审核乐观锁（沿用 M01 revision_token 做法）；草稿保存与每个审核动作都校验并轮换。
    - skill_review_drafts：审核中的编辑草稿，一个 Skill 一份；草稿不是版本，审核动作提交时才产生版本。
    - skill_review_tasks：退回重生成、校验未通过后重新生成的后台任务，复用 tasks.py 线程池与启动恢复。
    - skill_expert_experiences：审核中标为「专家补充」的内容，形成待沉淀经验清单（S13，只做清单）。
    """
    task_type = sql_in_list(REVIEW_TASK_TYPE_LABELS)
    task_status = sql_in_list(REVIEW_TASK_STATUS_LABELS)
    experience_status = sql_in_list(EXPERIENCE_STATUS_LABELS)

    skill_cols = [r["name"] for r in conn.execute("PRAGMA table_info(skills)").fetchall()]
    if "revision_token" not in skill_cols:
        conn.execute("ALTER TABLE skills ADD COLUMN revision_token TEXT")
    # 只为新增列补初值，不改动已有业务字段
    conn.execute("UPDATE skills SET revision_token = lower(hex(randomblob(16))) WHERE revision_token IS NULL")

    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS skill_review_drafts (
        skill_id TEXT PRIMARY KEY REFERENCES skills(id) ON DELETE CASCADE,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        base_version_id TEXT NOT NULL,
        content_json TEXT NOT NULL,
        resolutions_json TEXT,
        checklist_json TEXT,
        change_reasons_json TEXT,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS skill_review_tasks (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        review_record_id TEXT,
        task_type TEXT NOT NULL CHECK(task_type IN ({task_type})),
        source_status TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ({task_status})),
        attempt_count INTEGER NOT NULL DEFAULT 0,
        payload_json TEXT,
        result_json TEXT,
        model_calls_json TEXT,
        error_message TEXT,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS skill_expert_experiences (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        skill_version_id TEXT NOT NULL,
        review_record_id TEXT,
        source_kind TEXT NOT NULL,
        source_path TEXT NOT NULL,
        content TEXT NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ({experience_status})),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(skill_id, source_path, content)
    );

    CREATE INDEX IF NOT EXISTS idx_skill_review_tasks_skill ON skill_review_tasks(skill_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_skill_review_tasks_status ON skill_review_tasks(status, created_at);
    CREATE INDEX IF NOT EXISTS idx_skill_experiences_org ON skill_expert_experiences(organization_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_skill_versions_skill_kind ON skill_versions(skill_id, version_kind);
    """)


def init_skill_generation_tables(conn):
    """
    M02-C 生成批次（PRD 第 6 章）。只做增量变更，不改动 M01 核心表与 processing_tasks。
    - skill_generation_tasks：批次内的后台任务（召回、拆分、生成、校验入库），
      复用 tasks.py 线程池与启动恢复；每次模型调用的原始返回保存在 model_calls_json（不含密钥）。
    - skills 补列：所属批次与拆分任务、与已有 Skill 相似标记、校验未通过时的错误与候选。
    """
    task_type = sql_in_list(SKILL_TASK_TYPE_LABELS)
    task_status = sql_in_list(SKILL_TASK_STATUS_LABELS)

    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS skill_generation_tasks (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        batch_id TEXT NOT NULL REFERENCES skill_generation_batches(id) ON DELETE CASCADE,
        task_type TEXT NOT NULL CHECK(task_type IN ({task_type})),
        task_key TEXT,
        status TEXT NOT NULL CHECK(status IN ({task_status})),
        attempt_count INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 1,
        payload_json TEXT,
        result_json TEXT,
        model_calls_json TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_skill_gen_tasks_batch ON skill_generation_tasks(batch_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_skill_gen_tasks_status ON skill_generation_tasks(status, created_at);
    CREATE INDEX IF NOT EXISTS idx_skill_gen_tasks_org ON skill_generation_tasks(organization_id, batch_id);
    """)

    skill_cols = [r["name"] for r in conn.execute("PRAGMA table_info(skills)").fetchall()]
    skill_migrations = {
        "batch_id": "TEXT",
        "task_key": "TEXT",
        "similar_skills_json": "TEXT",
        "generation_error_json": "TEXT",
    }
    for column, column_type in skill_migrations.items():
        if column not in skill_cols:
            conn.execute(f"ALTER TABLE skills ADD COLUMN {column} {column_type}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_org_batch ON skills(organization_id, batch_id)")


def init_scene_catalog_tables(conn):
    """
    M02-B 场景目录（PRD 第 5 章）。只做增量新建，不改动 M01 核心表与原子数据。
    - scenes：受控场景目录，名称按规范化形式在企业内唯一。
    - scene_pending_tags：目录外标签的待归并列表（M01-C2/C3），每个企业每个规范化标签一行。
    - scene_merge_suggestions：FR01 归并建议，同时充当该模型调用的独立任务表。
    """
    scene_status = sql_in_list(SCENE_STATUS_LABELS)
    pending_status = sql_in_list(PENDING_TAG_STATUS_LABELS)
    suggestion_status = sql_in_list(MERGE_SUGGESTION_STATUS_LABELS)

    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS scenes (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        name_normalized TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        aliases_json TEXT NOT NULL DEFAULT '[]',
        typical_problems_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ({scene_status})),
        origin TEXT,
        revision_token TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_by TEXT,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS scene_pending_tags (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        tag TEXT NOT NULL,
        tag_normalized TEXT NOT NULL,
        source_atoms_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ({pending_status})),
        resolved_scene_id TEXT,
        resolution_note TEXT,
        resolved_by TEXT,
        resolved_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS scene_merge_suggestions (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        mode TEXT NOT NULL DEFAULT 'initial',
        status TEXT NOT NULL CHECK(status IN ({suggestion_status})),
        tag_stats_json TEXT NOT NULL,
        groups_json TEXT,
        unassigned_tags_json TEXT,
        model_name TEXT,
        prompt_version TEXT NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        attempts_json TEXT,
        error_message TEXT,
        requested_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        confirmed_by TEXT,
        confirmed_at TEXT,
        confirmed_groups_json TEXT
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_scenes_org_name ON scenes(organization_id, name_normalized);
    CREATE INDEX IF NOT EXISTS idx_scenes_org_status ON scenes(organization_id, status);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_scene_pending_tags_org_tag ON scene_pending_tags(organization_id, tag_normalized);
    CREATE INDEX IF NOT EXISTS idx_scene_pending_tags_org_status ON scene_pending_tags(organization_id, status);
    CREATE INDEX IF NOT EXISTS idx_scene_merge_suggestions_org ON scene_merge_suggestions(organization_id, created_at);
    -- 同一企业同一时间只允许一个排队或生成中的归并建议
    CREATE UNIQUE INDEX IF NOT EXISTS idx_scene_merge_suggestions_one_running
        ON scene_merge_suggestions(organization_id) WHERE status IN ('queued', 'running');
    """)

    # 整理模式：首次整理（initial）/ 目录建立后整理新标签（incremental）
    sms_cols = [r["name"] for r in conn.execute("PRAGMA table_info(scene_merge_suggestions)").fetchall()]
    if "mode" not in sms_cols:
        conn.execute("ALTER TABLE scene_merge_suggestions ADD COLUMN mode TEXT NOT NULL DEFAULT 'initial'")


def init_skill_tables(conn):
    """
    M02 Skill 工厂数据表（PRD 4.2、6.2）。只做增量新建，不改动 M01 核心表。
    状态字段存英文代码，中文名称对照见 skill_constants.py。
    引用关系不对 knowledge_* 建外键：原子被删除后，Skill 历史仍需通过快照可读（R7）。
    """
    skill_status = sql_in_list(SKILL_STATUS_LABELS)
    version_kind = sql_in_list(SKILL_VERSION_KIND_LABELS)
    review_action = sql_in_list(REVIEW_ACTION_LABELS)
    reject_reason = sql_in_list(REJECT_REASONS)
    batch_status = sql_in_list(BATCH_STATUS_LABELS)
    ref_role = sql_in_list(REF_ROLES)
    visibility = sql_in_list(VISIBILITY_VALUES)

    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS skills (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        scene_id TEXT NOT NULL,
        current_version_id TEXT,
        status TEXT NOT NULL CHECK(status IN ({skill_status})),
        visibility TEXT NOT NULL DEFAULT 'admin_only' CHECK(visibility IN ({visibility})),
        stale_reason TEXT,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS skill_versions (
        id TEXT PRIMARY KEY,
        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        version_number INTEGER NOT NULL,
        schema_version TEXT NOT NULL,
        skill_json TEXT NOT NULL,
        version_kind TEXT NOT NULL CHECK(version_kind IN ({version_kind})),
        based_on_version_id TEXT,
        batch_id TEXT,
        generation_json TEXT,
        validation_report_json TEXT,
        review_action TEXT CHECK(review_action IS NULL OR review_action IN ({review_action})),
        reviewed_by TEXT,
        reviewed_at TEXT,
        review_comment TEXT,
        revision_token TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(skill_id, version_number)
    );

    CREATE TABLE IF NOT EXISTS skill_atom_refs (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        skill_version_id TEXT NOT NULL REFERENCES skill_versions(id) ON DELETE CASCADE,
        atom_item_id TEXT NOT NULL,
        atom_version_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ({ref_role})),
        snapshot_title TEXT,
        snapshot_statement TEXT,
        snapshot_conditions_json TEXT,
        snapshot_actions_json TEXT,
        snapshot_exceptions_json TEXT,
        snapshot_primary_category TEXT,
        snapshot_atom_type TEXT,
        snapshot_metric_definition_json TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(skill_version_id, atom_version_id)
    );

    CREATE TABLE IF NOT EXISTS skill_review_records (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        from_version_id TEXT,
        to_version_id TEXT,
        action TEXT NOT NULL CHECK(action IN ({review_action})),
        operator_id TEXT NOT NULL,
        comment TEXT,
        checklist_json TEXT,
        field_diffs_json TEXT,
        reject_reason TEXT CHECK(reject_reason IS NULL OR reject_reason IN ({reject_reason})),
        regenerate_groups_json TEXT,
        detail_json TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS skill_generation_batches (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        scene_id TEXT NOT NULL,
        initiated_by TEXT NOT NULL,
        initiated_at TEXT NOT NULL,
        focus_note TEXT,
        atom_pool_json TEXT,
        task_split_json TEXT,
        model_name TEXT,
        prompt_version TEXT,
        task_results_json TEXT,
        status TEXT NOT NULL CHECK(status IN ({batch_status})),
        status_reason TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_skills_org_status ON skills(organization_id, status);
    CREATE INDEX IF NOT EXISTS idx_skills_org_scene ON skills(organization_id, scene_id);
    CREATE INDEX IF NOT EXISTS idx_skill_versions_skill ON skill_versions(skill_id, version_number);
    CREATE INDEX IF NOT EXISTS idx_skill_versions_batch ON skill_versions(organization_id, batch_id);
    CREATE INDEX IF NOT EXISTS idx_skill_refs_version ON skill_atom_refs(skill_version_id);
    CREATE INDEX IF NOT EXISTS idx_skill_refs_atom_version ON skill_atom_refs(organization_id, atom_version_id);
    CREATE INDEX IF NOT EXISTS idx_skill_refs_atom_item ON skill_atom_refs(organization_id, atom_item_id);
    CREATE INDEX IF NOT EXISTS idx_skill_reviews_skill ON skill_review_records(skill_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_skill_batches_org_scene ON skill_generation_batches(organization_id, scene_id, initiated_at);
    -- PRD 6.3：同一场景同一时间只允许一个进行中的批次
    CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_batches_one_running
        ON skill_generation_batches(organization_id, scene_id) WHERE status = 'running';

    -- 企业隔离：版本、引用、审核记录必须与所属 Skill 同企业
    CREATE TRIGGER IF NOT EXISTS trg_skill_versions_org_insert
    BEFORE INSERT ON skill_versions
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1 FROM skills s WHERE s.id = NEW.skill_id AND s.organization_id = NEW.organization_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'skill version organization mismatch');
    END;

    -- 版本内容不可修改（含 AI 原稿）；修改一律产生新版本。审核信息与 revision_token 仍可更新。
    CREATE TRIGGER IF NOT EXISTS trg_skill_versions_content_immutable
    BEFORE UPDATE OF skill_id, organization_id, version_number, schema_version, skill_json,
        version_kind, based_on_version_id, batch_id, generation_json, created_by, created_at
    ON skill_versions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'skill version content is immutable');
    END;

    CREATE TRIGGER IF NOT EXISTS trg_skill_versions_original_no_delete
    BEFORE DELETE ON skill_versions
    FOR EACH ROW
    WHEN OLD.version_kind = 'ai_original'
    BEGIN
        SELECT RAISE(ABORT, 'skill original version cannot be deleted');
    END;

    -- 当前版本指针只能指向自己的版本
    CREATE TRIGGER IF NOT EXISTS trg_skills_current_version_update
    BEFORE UPDATE OF current_version_id ON skills
    FOR EACH ROW
    WHEN NEW.current_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM skill_versions sv
        WHERE sv.id = NEW.current_version_id AND sv.skill_id = NEW.id
          AND sv.organization_id = NEW.organization_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'skill current version mismatch');
    END;

    -- 引用写入时：Skill 版本同企业同 Skill；原子版本属于所填条目且同企业
    CREATE TRIGGER IF NOT EXISTS trg_skill_atom_refs_consistency_insert
    BEFORE INSERT ON skill_atom_refs
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1 FROM skill_versions sv
        WHERE sv.id = NEW.skill_version_id AND sv.skill_id = NEW.skill_id
          AND sv.organization_id = NEW.organization_id
    ) OR NOT EXISTS (
        SELECT 1 FROM knowledge_versions kv
        WHERE kv.id = NEW.atom_version_id AND kv.item_id = NEW.atom_item_id
          AND kv.organization_id = NEW.organization_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'skill atom reference mismatch');
    END;

    -- 引用快照随版本锁定（R2），不原地修改
    CREATE TRIGGER IF NOT EXISTS trg_skill_atom_refs_immutable
    BEFORE UPDATE ON skill_atom_refs
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'skill atom reference snapshot is immutable');
    END;

    CREATE TRIGGER IF NOT EXISTS trg_skill_review_records_org_insert
    BEFORE INSERT ON skill_review_records
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1 FROM skills s WHERE s.id = NEW.skill_id AND s.organization_id = NEW.organization_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'skill review record organization mismatch');
    END;
    """)

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
