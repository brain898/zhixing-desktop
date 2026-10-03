import json
import uuid
import logging
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from threading import Lock
import time

from config import resolve_storage_path
from database import get_db
from parser import parse_file_to_blocks
from indexing import (
    build_index_for_version,
    execute_build_index_task,
    execute_clean_index_task,
    ensure_missing_stage4b_index_tasks,
)

logger = logging.getLogger(__name__)

# 后端持久化任务工作线程池（2个并发处理工作者，足以支撑桌面端轻量并发）
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="zhixing-worker")
_background_futures: set[Future] = set()
_background_futures_lock = Lock()


def _track_future(future: Future) -> None:
    with _background_futures_lock:
        _background_futures.add(future)

    def _remove(completed: Future) -> None:
        with _background_futures_lock:
            _background_futures.discard(completed)

    future.add_done_callback(_remove)


def wait_for_background_tasks(timeout: float = 30.0) -> bool:
    """等待已提交任务（含任务链中新提交的任务）真正退出线程函数。"""
    deadline = time.monotonic() + timeout
    while True:
        with _background_futures_lock:
            pending = list(_background_futures)
        if not pending:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        wait(pending, timeout=min(remaining, 0.25))


def submit_background(func, *args) -> Future:
    """Submit independent work and include its lifetime in cleanup/startup tracking."""
    future = _executor.submit(func, *args)
    _track_future(future)
    return future


def enqueue_jev_evaluation(knowledge_version_id: str, retry_failed: bool = False) -> dict:
    """轻量幂等入队。Jev 关闭或未配置时只记录状态，不提交网络任务。"""
    from jev_evaluator import create_or_get_evaluation, execute_evaluation

    result = create_or_get_evaluation(knowledge_version_id, retry_failed=retry_failed)
    if result["should_submit"]:
        future = _executor.submit(execute_evaluation, result["evaluation_id"])
        _track_future(future)
    return result


def enqueue_scene_merge_suggestion(suggestion_id: str) -> None:
    """M02 FR01 归并建议：独立任务表 scene_merge_suggestions，复用同一线程池与完成跟踪。"""
    from scene_catalog import run_merge_suggestion

    future = _executor.submit(run_merge_suggestion, suggestion_id)
    _track_future(future)


def enqueue_skill_generation_task(task_id: str) -> None:
    """M02-C 生成批次：独立任务表 skill_generation_tasks，复用同一线程池（并发上限与 M01 抽取一致）。"""
    from skill_generation import run_generation_task

    future = _executor.submit(run_generation_task, task_id)
    _track_future(future)


def enqueue_skill_review_task(task_id: str) -> None:
    """M02-D 退回重生成：独立任务表 skill_review_tasks，复用同一线程池与完成跟踪。"""
    from skill_review import run_review_task

    future = _executor.submit(run_review_task, task_id)
    _track_future(future)

def enqueue_consult_task(task_id: str) -> None:
    from consult import run_consult_task
    submit_background(run_consult_task, task_id)


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

    file_path = resolve_storage_path(task["storage_reference"])
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
    1. 调用 DeepSeek API 进行分批抽取；未配置或报错时明确失败并记录异常。
    2. 执行程序语义与来源校验（validate_and_sanitize_atoms）。
    3. 写入 knowledge_items、knowledge_versions 与 knowledge_evidence。
    4. 保证重试幂等：不覆盖已有的人工修改记录，不复活已被删除的知识候选。
    """
    from deepseek_extractor import (
        extract_atoms_via_deepseek_batched,
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

    created_version_ids = []
    try:
        # 调用 DeepSeek API 进行知识抽取；未配置或调用报错时明确抛出异常
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("在线模型未配置，无法执行知识抽取")

        # M01-C1：有场景目录时提供给模型；无目录时不传参数，抽取行为与原来一致。
        from scene_catalog import get_catalog_for_extraction, register_unmatched_scene_tags
        with get_db() as conn:
            scene_catalog = get_catalog_for_extraction(conn, org_id)
        catalog_kwargs = {"scene_catalog": scene_catalog} if scene_catalog else {}

        raw_candidates, extraction_ctx = extract_atoms_via_deepseek_batched(
            source_blocks=source_blocks,
            document_title=doc_title,
            organization_id=org_id,
            document_version_id=version_id,
            **catalog_kwargs,
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
                SELECT ki.id, ki.lifecycle_status, ki.is_excluded, kv.review_status, kv.reviewed_by, kv.title, kv.statement
                FROM knowledge_items ki
                JOIN knowledge_versions kv ON ki.active_version_id = kv.id
                WHERE ki.document_id = ?
                """,
                (doc_id,)
            ).fetchall()

            deleted_titles = {
                r["title"] for r in existing_items
                if r["lifecycle_status"] == "deleted" or (r["is_excluded"] == 1 if "is_excluded" in r.keys() else False)
            }
            manual_titles = {r["title"] for r in existing_items if r["review_status"] == "confirmed" or r["reviewed_by"] is not None}
            manual_statements = {
                (r["statement"] or "").strip()
                for r in existing_items
                if (r["review_status"] == "confirmed" or r["reviewed_by"] is not None) and (r["statement"] or "").strip()
            }

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
                if atom["title"] in manual_titles or (atom.get("statement") or "").strip() in manual_statements:
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
                     extraction_context_json, related_cases_json, business_importance, importance_rationale, created_at, created_by)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', 'not_indexed', ?, ?, ?, ?, ?, ?, 'system_extractor')
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
                        json.dumps({**extraction_ctx, **atom.get("_extraction_context", {})}, ensure_ascii=False),
                        json.dumps(atom.get("related_cases") or [], ensure_ascii=False),
                        atom.get("business_importance", "normal"),
                        atom.get("importance_rationale", "常规业务规程、作业标准或指标要求"),
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
                # M01-C2：目录外的新标签仍保存在原子上，同时进入待归并列表（无目录时不处理）
                register_unmatched_scene_tags(
                    conn, org_id, atom["business_scenes"], item_id, version_id_k,
                    "extraction", title=atom["title"], now_iso=now_iso,
                )
                created_version_ids.append(version_id_k)

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

        # Jev 是独立的后置质检：失败、关闭或缺少 Key 都不得回滚已保存候选。
        for candidate_version_id in created_version_ids:
            try:
                enqueue_jev_evaluation(candidate_version_id)
            except Exception:
                logger.exception("Failed to enqueue Jev evaluation for %s", candidate_version_id)

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
        future = None
        if t_type == "parse_document":
            future = _executor.submit(execute_parse_task, task_id)
        elif t_type == "extract_atoms":
            future = _executor.submit(execute_extract_task, task_id)
        elif t_type == "build_index":
            future = _executor.submit(execute_build_index_task, task_id)
        elif t_type == "clean_index":
            future = _executor.submit(execute_clean_index_task, task_id)
        else:
            logger.info(f"Task type {t_type} handler pending next stage.")
        if future is not None:
            _track_future(future)




def recover_interrupted_tasks():
    """恢复 document/knowledge 两类持久化任务，并补建 Stage 4B 遗留索引。"""
    recover_ids = []
    with get_db() as conn:
        tasks = conn.execute(
            """
            SELECT id, target_type, target_id, task_type, status
            FROM processing_tasks
            WHERE status IN ('queued', 'running')
            ORDER BY created_at
            """
        ).fetchall()

        for task in tasks:
            valid = False
            if task["target_type"] == "document_version":
                row = conn.execute(
                    """
                    SELECT d.is_deleted
                    FROM document_versions dv
                    JOIN documents d ON d.id = dv.document_id
                    WHERE dv.id = ?
                    """,
                    (task["target_id"],),
                ).fetchone()
                valid = bool(row and int(row["is_deleted"] or 0) == 0)
            elif task["target_type"] == "knowledge_version":
                if task["task_type"] == "clean_index":
                    # 清理任务恰恰需要在知识/文件已失效后继续执行并可跨重启恢复。
                    row = conn.execute(
                        "SELECT id FROM knowledge_versions WHERE id = ?",
                        (task["target_id"],),
                    ).fetchone()
                    valid = bool(row)
                else:
                    row = conn.execute(
                        """
                        SELECT kv.review_status, ki.lifecycle_status, ki.is_excluded,
                               ki.active_version_id, ki.pending_version_id, d.is_deleted
                        FROM knowledge_versions kv
                        JOIN knowledge_items ki ON ki.id = kv.item_id
                        JOIN documents d ON d.id = ki.document_id
                        WHERE kv.id = ?
                        """,
                        (task["target_id"],),
                    ).fetchone()
                    valid = bool(
                        row
                        and row["review_status"] == "confirmed"
                        and row["lifecycle_status"] == "active"
                        and int(row["is_excluded"] or 0) == 0
                        and task["target_id"] in (row["active_version_id"], row["pending_version_id"])
                        and int(row["is_deleted"] or 0) == 0
                    )

            if not valid:
                conn.execute(
                    "UPDATE processing_tasks SET status = 'cancelled', error_message = ?, completed_at = ? WHERE id = ?",
                    ("恢复时目标已失效", datetime.now(timezone.utc).isoformat(), task["id"]),
                )
                if task["target_type"] == "knowledge_version":
                    conn.execute(
                        """
                        UPDATE knowledge_versions SET index_status = 'not_indexed'
                        WHERE id = ? AND index_status = 'indexing'
                        """,
                        (task["target_id"],),
                    )
                continue

            if task["status"] == "running":
                conn.execute(
                    """
                    UPDATE processing_tasks
                    SET status = 'queued', started_at = NULL, completed_at = NULL
                    WHERE id = ?
                    """,
                    (task["id"],),
                )
            recover_ids.append(task["id"])

    for task_id in recover_ids:
        logger.info("Recovering persisted task %s on startup.", task_id)
        submit_task(task_id)

    # Stage 4A 的 keyword-v1 记录不再视为 Stage 4B 完成；
    # 仅为未失败的当前确认版本补建当前 config_hash 的真实向量索引。
    for task_id in ensure_missing_stage4b_index_tasks():
        submit_task(task_id)

    # Jev 任务不复用检索任务状态机，避免改变既有 processing_tasks CHECK 约束。
    # 服务重启后仅恢复明确 queued/running 的当前版本评估，不批量扫描既有知识库。
    from jev_evaluator import execute_evaluation
    jev_ids = []
    with get_db() as conn:
        rows = conn.execute(
            """SELECT je.id, je.knowledge_version_id, je.candidate_revision_token,
                      kv.revision_token AS current_revision_token
               FROM jev_evaluations je
               LEFT JOIN knowledge_versions kv ON kv.id = je.knowledge_version_id
               WHERE je.status IN ('queued', 'running') ORDER BY je.created_at"""
        ).fetchall()
        for row in rows:
            if not row["current_revision_token"] or row["current_revision_token"] != row["candidate_revision_token"]:
                conn.execute(
                    "UPDATE jev_evaluations SET status='stale', completed_at=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), row["id"]),
                )
                continue
            if row["id"]:
                conn.execute("UPDATE jev_evaluations SET status='queued', started_at=NULL WHERE id=?", (row["id"],))
                jev_ids.append(row["id"])
    for evaluation_id in jev_ids:
        future = _executor.submit(execute_evaluation, evaluation_id)
        _track_future(future)

    # M02 FR01 归并建议：重启后把中断的 queued/running 建议重新排队执行。
    suggestion_ids = []
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM scene_merge_suggestions WHERE status IN ('queued', 'running') ORDER BY created_at"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE scene_merge_suggestions SET status = 'queued', started_at = NULL WHERE id = ?",
                (row["id"],),
            )
            suggestion_ids.append(row["id"])
    for suggestion_id in suggestion_ids:
        enqueue_scene_merge_suggestion(suggestion_id)

    # M02-C 生成批次：进行中批次的 queued/running 任务重新排队，已结束批次的残留任务取消。
    from skill_generation import recover_generation_tasks
    recover_generation_tasks()

    # M02-D 退回重生成：排队或进行中的任务重新排队；没有任务的「生成中」Skill 恢复到可操作状态。
    from skill_review import recover_review_tasks
    recover_review_tasks()
    from consult import recover_consult_tasks
    recover_consult_tasks()
