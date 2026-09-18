import json
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from database import get_db
from parser import parse_file_to_blocks

logger = logging.getLogger(__name__)

# 后端持久化任务工作线程池（2个并发处理工作者，足以支撑桌面端轻量并发）
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="zhixing-worker")

def execute_parse_task(task_id: str):
    """
    持久化正文解析执行流。
    保证：
    1. 任务及版本状态全程持久化（queued -> running -> completed / failed / cancelled）。
    2. 删除和取消后的迟到结果拒绝写回。
    3. 重试幂等：清除历史生成物，不产生重复块。
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    
    with get_db() as conn:
        task = conn.execute(
            """
            SELECT pt.id, pt.organization_id, pt.target_id, pt.status, pt.attempt_count,
                   dv.document_id, dv.storage_reference, dv.file_type, dv.file_name,
                   d.is_deleted
            FROM processing_tasks pt
            JOIN document_versions dv ON pt.target_id = dv.id
            JOIN documents d ON dv.document_id = d.id
            WHERE pt.id = ?
            """,
            (task_id,)
        ).fetchone()

        if not task:
            logger.warning(f"Task {task_id} not found.")
            return

        # 检查是否已删除或已取消
        if task["is_deleted"] == 1 or task["status"] == "cancelled":
            conn.execute(
                "UPDATE processing_tasks SET status = 'cancelled', completed_at = ? WHERE id = ?",
                (now_iso, task_id)
            )
            return

        # 更新为 running
        conn.execute(
            """
            UPDATE processing_tasks 
            SET status = 'running', started_at = ?, attempt_count = attempt_count + 1, error_message = NULL
            WHERE id = ?
            """,
            (now_iso, task_id)
        )
        conn.execute(
            "UPDATE document_versions SET processing_status = 'parsing', error_summary = NULL WHERE id = ?",
            (task["target_id"],)
        )

    file_path = Path(task["storage_reference"])
    version_id = task["target_id"]
    org_id = task["organization_id"]

    try:
        if not file_path.exists():
            raise FileNotFoundError(f"原始存储文件丢失: {file_path.name}")

        # 解析正文结构块
        blocks = parse_file_to_blocks(file_path, task["file_type"])

        # 写回前再次校验：检查文件是否在解析过程中被删除，或者任务被取消
        with get_db() as conn:
            check_doc = conn.execute(
                """
                SELECT d.is_deleted, pt.status
                FROM documents d
                JOIN document_versions dv ON d.id = dv.document_id
                JOIN processing_tasks pt ON pt.target_id = dv.id
                WHERE dv.id = ? AND pt.id = ?
                """,
                (version_id, task_id)
            ).fetchone()

            if not check_doc or check_doc["is_deleted"] == 1 or check_doc["status"] == "cancelled":
                logger.info(f"Task {task_id} aborted before commit because document was deleted or cancelled.")
                conn.execute(
                    "UPDATE processing_tasks SET status = 'cancelled', completed_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), task_id)
                )
                return

            # 清理该版本旧有的结构块（确保重试幂等性）
            conn.execute("DELETE FROM source_blocks WHERE document_version_id = ?", (version_id,))

            # 批量插入结构块
            insert_rows = [
                (
                    f"sb_{uuid.uuid4().hex[:12]}",
                    version_id,
                    org_id,
                    b["block_index"],
                    b["block_type"],
                    b["heading_path"],
                    b["page_number"],
                    b["paragraph_anchor"],
                    b["text_content"],
                    now_iso,
                )
                for b in blocks
            ]

            conn.executemany(
                """
                INSERT INTO source_blocks 
                (id, document_version_id, organization_id, block_index, block_type, heading_path, page_number, paragraph_anchor, text_content, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                insert_rows
            )

            # 更新正文解析任务为 completed
            completed_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE processing_tasks SET status = 'completed', completed_at = ? WHERE id = ?",
                (completed_iso, task_id)
            )

            # 自动串联阶段三：创建并提交知识原子抽取任务 (extract_atoms)
            extract_task_id = f"task_extract_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """
                INSERT INTO processing_tasks 
                (id, organization_id, target_type, target_id, task_type, status, attempt_count, created_at)
                VALUES (?, ?, 'document_version', ?, 'extract_atoms', 'queued', 0, ?)
                """,
                (extract_task_id, org_id, version_id, completed_iso)
            )
            conn.execute(
                "UPDATE document_versions SET processing_status = 'extracting', error_summary = NULL WHERE id = ?",
                (version_id,)
            )

        logger.info(f"Task {task_id} completed successfully with {len(blocks)} blocks. Enqueued extraction task {extract_task_id}.")
        submit_task(extract_task_id)

    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}", exc_info=True)
        err_msg = str(e)
        failed_iso = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            conn.execute(
                "UPDATE processing_tasks SET status = 'failed', error_message = ?, completed_at = ? WHERE id = ?",
                (err_msg, failed_iso, task_id)
            )
            conn.execute(
                "UPDATE document_versions SET processing_status = 'failed', error_summary = ? WHERE id = ?",
                (err_msg, version_id)
            )


def execute_extract_task(task_id: str):
    """
    持久化知识原子抽取与校验执行流：
    1. 调用 DeepSeek API 或优雅降级至结构化离线提炼引擎。
    2. 执行程序语义与来源校验（validate_and_sanitize_atoms）。
    3. 写入 knowledge_items、knowledge_versions 与 knowledge_evidence。
    4. 保证重试幂等：不覆盖已有的人工修改记录，不复活已被删除的知识候选。
    """
    from deepseek_extractor import (
        extract_atoms_via_deepseek,
        rule_based_extract_atoms,
        validate_and_sanitize_atoms,
    )
    from config import DEEPSEEK_API_KEY

    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        task = conn.execute(
            """
            SELECT pt.id, pt.organization_id, pt.target_id, pt.status, pt.attempt_count,
                   dv.document_id, dv.file_name, dv.version_label, d.title as doc_title, d.is_deleted
            FROM processing_tasks pt
            JOIN document_versions dv ON pt.target_id = dv.id
            JOIN documents d ON dv.document_id = d.id
            WHERE pt.id = ?
            """,
            (task_id,)
        ).fetchone()

        if not task:
            logger.warning(f"Extract task {task_id} not found.")
            return

        if task["is_deleted"] == 1 or task["status"] == "cancelled":
            conn.execute(
                "UPDATE processing_tasks SET status = 'cancelled', completed_at = ? WHERE id = ?",
                (now_iso, task_id)
            )
            return

        conn.execute(
            """
            UPDATE processing_tasks 
            SET status = 'running', started_at = ?, attempt_count = attempt_count + 1, error_message = NULL
            WHERE id = ?
            """,
            (now_iso, task_id)
        )
        conn.execute(
            "UPDATE document_versions SET processing_status = 'extracting', error_summary = NULL WHERE id = ?",
            (task["target_id"],)
        )

        version_id = task["target_id"]
        doc_id = task["document_id"]
        org_id = task["organization_id"]
        doc_title = task["doc_title"] or task["file_name"]

        # 获取该版本所有已入库结构块
        blocks_rows = conn.execute(
            """
            SELECT id, block_index, block_type, heading_path, page_number, paragraph_anchor, text_content
            FROM source_blocks
            WHERE document_version_id = ?
            ORDER BY block_index ASC
            """,
            (version_id,)
        ).fetchall()
        source_blocks = [dict(r) for r in blocks_rows]

    if not source_blocks:
        logger.warning(f"No source blocks found for version {version_id}, cannot extract atoms.")
        with get_db() as conn:
            conn.execute(
                "UPDATE processing_tasks SET status = 'completed', completed_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), task_id)
            )
            conn.execute(
                "UPDATE document_versions SET processing_status = 'completed' WHERE id = ?",
                (version_id,)
            )
        return

    try:
        raw_candidates = []
        extraction_ctx = {}

        # 尝试调用 DeepSeek API；若未配置或调用报错，降级至规则提取器
        if DEEPSEEK_API_KEY:
            try:
                raw_candidates, extraction_ctx = extract_atoms_via_deepseek(
                    source_blocks=source_blocks,
                    document_title=doc_title,
                )
            except Exception as e:
                logger.warning(f"DeepSeek online extraction failed, falling back to rule adapter: {e}")
                raw_candidates, extraction_ctx = rule_based_extract_atoms(
                    source_blocks=source_blocks,
                    document_title=doc_title,
                )
                extraction_ctx["fallback_reason"] = str(e)
        else:
            raw_candidates, extraction_ctx = rule_based_extract_atoms(
                source_blocks=source_blocks,
                document_title=doc_title,
            )

        # 执行程序语义与来源证据严格校验
        sanitized_atoms = validate_and_sanitize_atoms(
            raw_atoms=raw_candidates,
            source_blocks=source_blocks,
            document_version_id=version_id,
        )

        with get_db() as conn:
            # 再次校验是否已在处理中被删除
            chk = conn.execute(
                "SELECT d.is_deleted, pt.status FROM documents d JOIN document_versions dv ON d.id = dv.document_id JOIN processing_tasks pt ON pt.target_id = dv.id WHERE dv.id = ? AND pt.id = ?",
                (version_id, task_id)
            ).fetchone()

            if not chk or chk["is_deleted"] == 1 or chk["status"] == "cancelled":
                logger.info(f"Extract task {task_id} aborted because document was deleted or task cancelled.")
                conn.execute(
                    "UPDATE processing_tasks SET status = 'cancelled', completed_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), task_id)
                )
                return

            # 查询已有条目（避免重试时覆盖已人工修改/已确认项，或复活已删除条目）
            existing_items = conn.execute(
                """
                SELECT ki.id, ki.lifecycle_status, kv.review_status, kv.reviewed_by, kv.title
                FROM knowledge_items ki
                JOIN knowledge_versions kv ON ki.active_version_id = kv.id
                WHERE ki.document_id = ?
                """,
                (doc_id,)
            ).fetchall()

            deleted_titles = {r["title"] for r in existing_items if r["lifecycle_status"] in ("deleted", "excluded")}
            manual_titles = {r["title"] for r in existing_items if r["review_status"] == "confirmed" or r["reviewed_by"] is not None}

            # 清除该版本原有的未校对候选版本，以便更新
            conn.execute(
                """
                DELETE FROM knowledge_evidence
                WHERE knowledge_version_id IN (
                    SELECT kv.id FROM knowledge_versions kv
                    JOIN knowledge_items ki ON kv.item_id = ki.id
                    WHERE kv.source_document_version_id = ? AND kv.review_status = 'pending_review' AND kv.reviewed_by IS NULL
                )
                """,
                (version_id,)
            )
            conn.execute(
                """
                DELETE FROM knowledge_versions
                WHERE source_document_version_id = ? AND review_status = 'pending_review' AND reviewed_by IS NULL
                """,
                (version_id,)
            )

            # 写入知识原子候选
            from deepseek_extractor import is_meaningful_business_text
            for atom in sanitized_atoms:
                # 规则 1：被删除或已排除的候选不自动复活
                if atom["title"] in deleted_titles:
                    logger.info(f"Skipping atom 「{atom['title']}」: previously deleted/excluded by admin.")
                    continue

                # 规则 2：已有且已人工校对的候选不被重试结果覆盖
                if atom["title"] in manual_titles:
                    logger.info(f"Skipping atom 「{atom['title']}」: already reviewed/confirmed by admin.")
                    continue

                # 规则 3：完全无效的片段（无实质业务内容、纯符号/分割线等）留在处理记录中，不塞入人工核对队列
                if not is_meaningful_business_text(atom.get("statement")):
                    logger.info(f"Filtered invalid candidate 「{atom['title']}」: no meaningful business content, preserved in processing log.")
                    continue

                item_id = f"ki_{uuid.uuid4().hex[:12]}"
                version_id_k = f"kv_{uuid.uuid4().hex[:12]}"
                rev_token = uuid.uuid4().hex

                # 1. 插入 knowledge_items
                conn.execute(
                    """
                    INSERT INTO knowledge_items
                    (id, document_id, organization_id, active_version_id, access_scope, lifecycle_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'admin_only', 'active', ?, ?)
                    """,
                    (item_id, doc_id, org_id, version_id_k, now_iso, now_iso)
                )

                # 2. 插入 knowledge_versions
                conn.execute(
                    """
                    INSERT INTO knowledge_versions
                    (id, item_id, organization_id, source_document_version_id, version_number,
                     title, content, primary_category, atom_type, subject, statement,
                     conditions_json, actions_json, exceptions_json, metric_definition_json, case_details_json,
                     field_states_json, quality_flags_json, customer_types_json, business_scenes_json, problem_tags_json,
                     source_anchors_json, valid_from, valid_until, review_status, index_status, revision_token,
                     extraction_context_json, related_cases_json, created_at, created_by)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', 'not_indexed', ?, ?, ?, ?, 'system_extractor')
                    """,
                    (
                        version_id_k,
                        item_id,
                        org_id,
                        version_id,
                        atom["title"],
                        atom["content"],
                        atom["primary_category"],
                        atom["atom_type"],
                        atom["subject"],
                        atom["statement"],
                        json.dumps(atom["conditions"], ensure_ascii=False),
                        json.dumps(atom["actions"], ensure_ascii=False),
                        json.dumps(atom["exceptions"], ensure_ascii=False),
                        json.dumps(atom["metric_definition"], ensure_ascii=False) if atom["metric_definition"] else None,
                        json.dumps(atom["case_details"], ensure_ascii=False) if atom["case_details"] else None,
                        json.dumps(atom["field_states"], ensure_ascii=False),
                        json.dumps(atom["quality_flags"], ensure_ascii=False),
                        json.dumps(atom["customer_types"], ensure_ascii=False),
                        json.dumps(atom["business_scenes"], ensure_ascii=False),
                        json.dumps(atom["problem_tags"], ensure_ascii=False),
                        json.dumps(atom["source_anchors"], ensure_ascii=False),
                        atom.get("valid_from"),
                        atom.get("valid_until"),
                        rev_token,
                        json.dumps(extraction_ctx, ensure_ascii=False),
                        json.dumps(atom.get("related_cases") or [], ensure_ascii=False),
                        now_iso,
                    )
                )

                # 3. 插入 knowledge_evidence
                evidence_rows = [
                    (
                        f"ke_{uuid.uuid4().hex[:12]}",
                        version_id_k,
                        ev["source_block_id"],
                        org_id,
                        ev["field_name"],
                        ev["excerpt"],
                        ev["accuracy_level"],
                        now_iso,
                    )
                    for ev in atom["source_evidence"]
                ]
                if evidence_rows:
                    conn.executemany(
                        """
                        INSERT INTO knowledge_evidence
                        (id, knowledge_version_id, source_block_id, organization_id, field_name, excerpt, accuracy_level, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        evidence_rows
                    )

            # 更新任务与版本状态为 completed
            completed_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE processing_tasks SET status = 'completed', completed_at = ? WHERE id = ?",
                (completed_iso, task_id)
            )
            conn.execute(
                "UPDATE document_versions SET processing_status = 'completed', error_summary = NULL WHERE id = ?",
                (version_id,)
            )

        logger.info(f"Extract task {task_id} completed successfully with {len(sanitized_atoms)} atom candidates.")

    except Exception as e:
        logger.error(f"Extract task {task_id} failed: {e}", exc_info=True)
        err_msg = str(e)
        failed_iso = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            conn.execute(
                "UPDATE processing_tasks SET status = 'failed', error_message = ?, completed_at = ? WHERE id = ?",
                (err_msg, failed_iso, task_id)
            )
            conn.execute(
                "UPDATE document_versions SET processing_status = 'partial_failed', error_summary = ? WHERE id = ?",
                (f"抽取部分异常: {err_msg}", version_id)
            )


def build_index_for_version(conn, version_id: str, org_id: str) -> str:
    """
    为指定知识版本构建检索索引记录（retrieval_records）并置 index_status 为 ready。
    组合标题、陈述、主体、条件、动作、例外、指标与业务标签生成综合检索文本。
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        """
        SELECT kv.*, d.title as doc_title
        FROM knowledge_versions kv
        JOIN knowledge_items ki ON kv.item_id = ki.id
        JOIN documents d ON ki.document_id = d.id
        WHERE kv.id = ? AND kv.organization_id = ? AND ki.lifecycle_status != 'deleted' AND d.is_deleted = 0
        """,
        (version_id, org_id)
    ).fetchone()

    if not row:
        raise ValueError(f"知识版本 {version_id} 不存在或对应资料已被删除")

    parts = []
    if row["title"]:
        parts.append(f"【标题】{row['title']}")
    if row["primary_category"]:
        parts.append(f"【分类】{row['primary_category']}")
    if row["subject"]:
        parts.append(f"【责任主体】{row['subject']}")
    if row["statement"]:
        parts.append(f"【核心结论】{row['statement']}")
    if row["content"] and row["content"] != row["statement"]:
        parts.append(f"【正文详情】{row['content']}")

    conditions = json.loads(row["conditions_json"] or "[]")
    if conditions:
        parts.append(f"【适用条件】{'；'.join(conditions)}")

    actions = json.loads(row["actions_json"] or "[]")
    if actions:
        parts.append(f"【执行动作】{'；'.join(actions)}")

    exceptions = json.loads(row["exceptions_json"] or "[]")
    if exceptions:
        parts.append(f"【例外与禁止】{'；'.join(exceptions)}")

    if row["metric_definition_json"]:
        m = json.loads(row["metric_definition_json"])
        parts.append(f"【指标要求】名称:{m.get('name', '')} 单位:{m.get('unit', '')} 统计期:{m.get('period', '')} 基准:{m.get('criteria', '')}")

    if row["case_details_json"]:
        c = json.loads(row["case_details_json"])
        parts.append(f"【案例实战】背景:{c.get('background', '')} 动作:{c.get('actions', '')} 效果:{c.get('results', '')}")

    customer_types = json.loads(row["customer_types_json"] or "[]")
    if customer_types:
        parts.append(f"【客户类型】{' '.join(customer_types)}")

    business_scenes = json.loads(row["business_scenes_json"] or "[]")
    if business_scenes:
        parts.append(f"【业务场景】{' '.join(business_scenes)}")

    problem_tags = json.loads(row["problem_tags_json"] or "[]")
    if problem_tags:
        parts.append(f"【解决问题】{' '.join(problem_tags)}")

    if row["doc_title"]:
        parts.append(f"【来源资料】{row['doc_title']}")

    search_text = "\n".join(parts)

    # 清除历史旧索引
    conn.execute("DELETE FROM retrieval_records WHERE knowledge_version_id = ?", (version_id,))

    record_id = f"ret_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO retrieval_records 
        (id, knowledge_version_id, organization_id, search_text, vector_json, model_name, index_version, created_at)
        VALUES (?, ?, ?, ?, NULL, 'keyword-v1', 1, ?)
        """,
        (record_id, version_id, org_id, search_text, now_iso)
    )

    conn.execute(
        "UPDATE knowledge_versions SET index_status = 'ready' WHERE id = ?",
        (version_id,)
    )

    return record_id


def execute_build_index_task(task_id: str):
    """
    持久化索引构建执行流：
    将知识条目版本解析并写入 retrieval_records，置 index_status 为 ready。
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        task = conn.execute(
            "SELECT id, organization_id, target_id, status FROM processing_tasks WHERE id = ?",
            (task_id,)
        ).fetchone()
        if not task or task["status"] == "cancelled":
            return

        conn.execute(
            "UPDATE processing_tasks SET status = 'running', started_at = ? WHERE id = ?",
            (now_iso, task_id)
        )
        version_id = task["target_id"]
        org_id = task["organization_id"]
        conn.execute("UPDATE knowledge_versions SET index_status = 'indexing' WHERE id = ?", (version_id,))

    try:
        with get_db() as conn:
            build_index_for_version(conn, version_id, org_id)
            conn.execute(
                "UPDATE processing_tasks SET status = 'completed', completed_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), task_id)
            )
        logger.info(f"Build index task {task_id} completed for version {version_id}.")
    except Exception as e:
        logger.error(f"Build index task {task_id} failed: {e}", exc_info=True)
        err_msg = str(e)
        with get_db() as conn:
            conn.execute(
                "UPDATE processing_tasks SET status = 'failed', error_message = ?, completed_at = ? WHERE id = ?",
                (err_msg, datetime.now(timezone.utc).isoformat(), task_id)
            )
            conn.execute(
                "UPDATE knowledge_versions SET index_status = 'failed' WHERE id = ?",
                (version_id,)
            )


def submit_task(task_id: str):
    """
    根据任务类型智能分发到后台工作线程
    """
    with get_db() as conn:
        task = conn.execute("SELECT task_type FROM processing_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            logger.warning(f"Task {task_id} not found to submit.")
            return

        t_type = task["task_type"]
        if t_type == "parse_document":
            _executor.submit(execute_parse_task, task_id)
        elif t_type == "extract_atoms":
            _executor.submit(execute_extract_task, task_id)
        elif t_type == "build_index":
            _executor.submit(execute_build_index_task, task_id)
        else:
            logger.info(f"Task type {t_type} handler pending next stage.")


def recover_interrupted_tasks():
    """
    服务启动恢复机制：
    查找所有在服务意外停止前处于 'running' 或 'queued' 的任务，恢复排队并重新投递执行。
    """
    with get_db() as conn:
        tasks = conn.execute(
            """
            SELECT pt.id, pt.task_type
            FROM processing_tasks pt
            JOIN documents d ON (SELECT document_id FROM document_versions WHERE id = pt.target_id) = d.id
            WHERE pt.status IN ('queued', 'running') AND d.is_deleted = 0
            """
        ).fetchall()

        for t in tasks:
            logger.info(f"Recovering {t['task_type']} task {t['id']} on startup.")
            submit_task(t["id"])

