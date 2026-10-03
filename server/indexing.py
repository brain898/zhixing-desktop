"""Stage 4B 持久化知识索引任务。"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple

from config import EMBEDDING_DIM, EMBEDDING_MODEL_NAME, RETRIEVAL_INDEX_VERSION
from database import get_db
from embedding_service import embed_texts
from hybrid_retrieval import build_retrieval_fragments, get_retrieval_config_hash

logger = logging.getLogger(__name__)


class IndexBuildCancelled(RuntimeError):
    """目标在任务完成前失去索引资格。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_material(conn, version_id: str, org_id: str) -> Tuple[Any, Sequence[Any]]:
    row = conn.execute(
        """
        SELECT kv.*, ki.active_version_id AS item_active_version_id,
               ki.pending_version_id AS item_pending_version_id,
               ki.lifecycle_status, ki.is_excluded,
               d.is_deleted, d.title AS doc_title,
               dv.file_name, dv.version_label AS document_version_label
        FROM knowledge_versions kv
        JOIN knowledge_items ki ON ki.id = kv.item_id
        JOIN documents d ON d.id = ki.document_id
        JOIN document_versions dv ON dv.id = kv.source_document_version_id
        WHERE kv.id = ? AND kv.organization_id = ? AND ki.organization_id = ?
        """,
        (version_id, org_id, org_id),
    ).fetchone()
    if not row:
        raise ValueError(f"知识版本 {version_id} 不存在或不属于当前组织")
    if row["review_status"] != "confirmed":
        raise IndexBuildCancelled("知识版本尚未确认")
    if version_id not in (row["item_active_version_id"], row["item_pending_version_id"]):
        raise IndexBuildCancelled("该知识版本既不是当前服务版本，也不是待切换版本")
    if row["lifecycle_status"] != "active":
        raise IndexBuildCancelled("知识条目已停用或删除")
    if int(row["is_excluded"] or 0) != 0:
        raise IndexBuildCancelled("知识条目已被排除")
    if int(row["is_deleted"] or 0) != 0:
        raise IndexBuildCancelled("来源资料已删除")

    evidence = conn.execute(
        """
        SELECT id, field_name, excerpt, accuracy_level
        FROM knowledge_evidence
        WHERE knowledge_version_id = ? AND organization_id = ?
        ORDER BY created_at, id
        """,
        (version_id, org_id),
    ).fetchall()
    if not evidence:
        raise IndexBuildCancelled("知识版本没有已绑定证据，不能建立正式索引")
    return row, evidence


def _prepare_vectors(row: Any, evidence: Sequence[Any]):
    fragments = build_retrieval_fragments(row, evidence)
    vectors = embed_texts(fragment["search_text"] for fragment in fragments)
    if len(fragments) != len(vectors):
        raise RuntimeError("embedding 返回数量与检索片段数量不一致")
    for vector in vectors:
        if len(vector) != EMBEDDING_DIM:
            raise RuntimeError(
                f"embedding 维度异常：expected={EMBEDDING_DIM}, actual={len(vector)}"
            )
    return fragments, vectors


def _persist_vectors(
    conn,
    version_id: str,
    org_id: str,
    fragments: Sequence[Dict[str, Any]],
    vectors: Sequence[Sequence[float]],
) -> List[str]:
    # 写入前重新读取最新业务状态，防止迟到任务把失效内容重新置为 ready。
    _fetch_material(conn, version_id, org_id)
    config_hash = get_retrieval_config_hash()
    now_iso = _now()

    conn.execute("DELETE FROM retrieval_records WHERE knowledge_version_id = ?", (version_id,))
    record_ids: List[str] = []
    for fragment, vector in zip(fragments, vectors):
        stable_key = f"zhixing:{version_id}:{fragment['fragment_key']}:{config_hash}"
        record_id = "ret_" + uuid.uuid5(uuid.NAMESPACE_URL, stable_key).hex[:20]
        conn.execute(
            """
            INSERT INTO retrieval_records (
                id, knowledge_version_id, organization_id, search_text, vector_json,
                model_name, index_version, fragment_key, fragment_type,
                evidence_ids_json, embedding_dim, content_hash, config_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                version_id,
                org_id,
                fragment["search_text"],
                json.dumps(vector, ensure_ascii=False, separators=(",", ":")),
                EMBEDDING_MODEL_NAME,
                RETRIEVAL_INDEX_VERSION,
                fragment["fragment_key"],
                fragment["fragment_type"],
                json.dumps(fragment["evidence_ids"], ensure_ascii=False),
                EMBEDDING_DIM,
                fragment["content_hash"],
                config_hash,
                now_iso,
            ),
        )
        record_ids.append(record_id)
    conn.execute(
        "UPDATE knowledge_versions SET index_status = 'ready' WHERE id = ? AND review_status = 'confirmed'",
        (version_id,),
    )

    # 新知识版本只有在索引真正 ready 后才能接管服务。这个指针切换与索引 ready
    # 位于同一个 SQLite 事务中，因此不会出现“新正文 + 旧来源”或两个当前版本并存。
    candidate = conn.execute(
        """
        SELECT kv.item_id, kv.reviewed_by, kv.source_document_version_id,
               ki.active_version_id, ki.pending_version_id,
               d.active_version_id AS document_active_version_id
        FROM knowledge_versions kv
        JOIN knowledge_items ki ON ki.id = kv.item_id
        JOIN documents d ON d.id = ki.document_id
        WHERE kv.id = ? AND kv.organization_id = ?
        """,
        (version_id, org_id),
    ).fetchone()
    if (
        candidate
        and candidate["pending_version_id"] == version_id
        and (
            candidate["document_active_version_id"] is None
            or candidate["document_active_version_id"] == candidate["source_document_version_id"]
        )
    ):
        previous_id = candidate["active_version_id"]
        switched = conn.execute(
            """
            UPDATE knowledge_items
            SET active_version_id = ?, pending_version_id = NULL, updated_at = ?
            WHERE id = ? AND organization_id = ? AND pending_version_id = ?
              AND lifecycle_status = 'active' AND (is_excluded = 0 OR is_excluded IS NULL)
            """,
            (version_id, now_iso, candidate["item_id"], org_id, version_id),
        )
        if switched.rowcount == 1:
            conn.execute(
                """
                INSERT INTO audit_logs
                (id, organization_id, user_id, action, target_type, target_id, details, created_at)
                VALUES (?, ?, ?, 'activate_knowledge_version', 'knowledge_item', ?, ?, ?)
                """,
                (
                    f"aud_{uuid.uuid4().hex[:12]}",
                    org_id,
                    candidate["reviewed_by"] or "system_indexer",
                    candidate["item_id"],
                    json.dumps({
                        "previous_active_version_id": previous_id,
                        "new_active_version_id": version_id,
                        "trigger": "index_ready",
                    }, ensure_ascii=False),
                    now_iso,
                ),
            )
            if previous_id and previous_id != version_id:
                # M02-E（R5）：新版本确认生效后，引用旧版本的 Skill 转为待复核或显示变更提示；
                # 钩子在 SAVEPOINT 中执行，失败不影响本次切换。
                from skill_recheck import notify_atom_change
                notify_atom_change(conn, org_id, [candidate["item_id"]], "new_version",
                                   candidate["reviewed_by"] or "system_indexer",
                                   {"previous_version_id": previous_id, "new_version_id": version_id}, now_iso)
    return record_ids


def build_index_for_version(conn, version_id: str, org_id: str) -> List[str]:
    """同步构建器仅供任务内部/维护脚本复用；正式 confirm 不直接调用。"""
    row, evidence = _fetch_material(conn, version_id, org_id)
    fragments, vectors = _prepare_vectors(row, evidence)
    return _persist_vectors(conn, version_id, org_id, fragments, vectors)


def create_or_get_build_index_task(
    conn,
    version_id: str,
    org_id: str,
    retry_failed: bool = False,
) -> Dict[str, Any]:
    config_hash = get_retrieval_config_hash()
    row = conn.execute(
        """
        SELECT kv.id, kv.review_status, kv.index_status,
               ki.active_version_id, ki.pending_version_id, ki.lifecycle_status, ki.is_excluded,
               d.is_deleted
        FROM knowledge_versions kv
        JOIN knowledge_items ki ON ki.id = kv.item_id
        JOIN documents d ON d.id = ki.document_id
        WHERE kv.id = ? AND kv.organization_id = ?
        """,
        (version_id, org_id),
    ).fetchone()
    if not row:
        raise ValueError("知识版本不存在")
    if row["review_status"] != "confirmed":
        raise ValueError("知识版本尚未确认")
    if version_id not in (row["active_version_id"], row["pending_version_id"]):
        raise ValueError("仅能为当前服务版本或待切换版本建立索引")
    if row["lifecycle_status"] != "active" or int(row["is_excluded"] or 0) != 0:
        raise ValueError("知识条目已停用、删除或排除")
    if int(row["is_deleted"] or 0) != 0:
        raise ValueError("来源资料已删除")

    idempotency_key = f"build-index:{version_id}:{config_hash}"
    task = conn.execute(
        """
        SELECT id, status, attempt_count
        FROM processing_tasks
        WHERE task_type = 'build_index' AND target_id = ? AND idempotency_key = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (version_id, idempotency_key),
    ).fetchone()

    should_submit = False
    if task:
        task_id = task["id"]
        status = task["status"]
        attempts = int(task["attempt_count"] or 0)
        record_count = conn.execute(
            "SELECT COUNT(*) FROM retrieval_records WHERE knowledge_version_id = ? AND config_hash = ?",
            (version_id, config_hash),
        ).fetchone()[0]
        if status == "completed" and record_count > 0:
            conn.execute(
                "UPDATE knowledge_versions SET index_status = 'ready' WHERE id = ?",
                (version_id,),
            )
            return {
                "task_id": task_id,
                "task_status": "completed",
                "index_status": "ready",
                "should_submit": False,
                "config_hash": config_hash,
            }
        if status == "failed" and not retry_failed:
            return {
                "task_id": task_id,
                "task_status": "failed",
                "index_status": "failed",
                "should_submit": False,
                "config_hash": config_hash,
            }
        if status == "cancelled" and not retry_failed:
            return {
                "task_id": task_id,
                "task_status": "cancelled",
                "index_status": row["index_status"],
                "should_submit": False,
                "config_hash": config_hash,
            }
        if status in ("failed", "cancelled") and attempts >= MAX_TASK_ATTEMPTS:
            raise ValueError(
                f"索引构建已达到最大重试次数 {MAX_TASK_ATTEMPTS}，请检查最近失败原因后再人工处理"
            )
        if status in ("failed", "cancelled", "completed"):
            conn.execute(
                """
                UPDATE processing_tasks
                SET status = 'queued', error_message = NULL, started_at = NULL, completed_at = NULL
                WHERE id = ?
                """,
                (task_id,),
            )
            status = "queued"
        should_submit = status == "queued"
    else:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO processing_tasks (
                id, organization_id, target_type, target_id, task_type, status,
                attempt_count, idempotency_key, config_hash, created_at
            )
            VALUES (?, ?, 'knowledge_version', ?, 'build_index', 'queued', 0, ?, ?, ?)
            """,
            (task_id, org_id, version_id, idempotency_key, config_hash, _now()),
        )
        status = "queued"
        should_submit = True

    conn.execute(
        "UPDATE knowledge_versions SET index_status = 'indexing' WHERE id = ?",
        (version_id,),
    )
    return {
        "task_id": task_id,
        "task_status": status,
        "index_status": "indexing",
        "should_submit": should_submit,
        "config_hash": config_hash,
    }


def execute_build_index_task(task_id: str):
    """后台执行 queued build_index；attempt_count 每次真实执行时递增。"""
    with get_db() as conn:
        task = conn.execute(
            """
            SELECT id, organization_id, target_id, status, config_hash
            FROM processing_tasks
            WHERE id = ? AND task_type = 'build_index'
            """,
            (task_id,),
        ).fetchone()
        if not task or task["status"] != "queued":
            return

        current_hash = get_retrieval_config_hash()
        if task["config_hash"] != current_hash:
            conn.execute(
                "UPDATE processing_tasks SET status = 'cancelled', error_message = ?, completed_at = ? WHERE id = ?",
                ("索引配置已变化，旧任务取消", _now(), task_id),
            )
            return

        claimed = conn.execute(
            """
            UPDATE processing_tasks
            SET status = 'running', started_at = ?, completed_at = NULL,
                attempt_count = attempt_count + 1, error_message = NULL
            WHERE id = ? AND status = 'queued'
            """,
            (_now(), task_id),
        )
        if claimed.rowcount != 1:
            return
        version_id = task["target_id"]
        org_id = task["organization_id"]
        conn.execute(
            "UPDATE knowledge_versions SET index_status = 'indexing' WHERE id = ? AND review_status = 'confirmed'",
            (version_id,),
        )

    try:
        # embedding 计算阶段不持有 SQLite 写事务。
        with get_db() as conn:
            row, evidence = _fetch_material(conn, version_id, org_id)
        fragments, vectors = _prepare_vectors(row, evidence)

        with get_db() as conn:
            record_ids = _persist_vectors(
                conn, version_id, org_id, fragments, vectors
            )
            conn.execute(
                """
                UPDATE processing_tasks
                SET status = 'completed', error_message = NULL, completed_at = ?
                WHERE id = ?
                """,
                (_now(), task_id),
            )
        logger.info(
            "Build index task %s completed for %s with %d fragments.",
            task_id, version_id, len(record_ids)
        )
    except IndexBuildCancelled as exc:
        with get_db() as conn:
            conn.execute(
                """
                UPDATE processing_tasks
                SET status = 'cancelled', error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (str(exc), _now(), task_id),
            )
            conn.execute(
                """
                UPDATE knowledge_versions
                SET index_status = 'not_indexed'
                WHERE id = ? AND index_status = 'indexing'
                """,
                (version_id,),
            )
        logger.info("Build index task %s cancelled: %s", task_id, exc)
    except Exception as exc:
        with get_db() as conn:
            conn.execute(
                """
                UPDATE processing_tasks
                SET status = 'failed', error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (str(exc), _now(), task_id),
            )
            conn.execute(
                """
                UPDATE knowledge_versions
                SET index_status = 'failed'
                WHERE id = ? AND index_status = 'indexing'
                """,
                (version_id,),
            )
        logger.exception("Build index task %s failed", task_id)



MAX_TASK_ATTEMPTS = 5


def create_or_get_clean_index_task(
    conn,
    version_id: str,
    org_id: str,
    retry_failed: bool = False,
) -> Dict[str, Any]:
    """为失效/删除版本创建幂等索引清理任务；业务资格不依赖清理成功。"""
    version = conn.execute(
        "SELECT id, index_status FROM knowledge_versions WHERE id = ? AND organization_id = ?",
        (version_id, org_id),
    ).fetchone()
    if not version:
        raise ValueError("知识版本不存在")

    idempotency_key = f"clean-index:{version_id}"
    task = conn.execute(
        """
        SELECT id, status, attempt_count
        FROM processing_tasks
        WHERE task_type = 'clean_index' AND target_id = ? AND idempotency_key = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (version_id, idempotency_key),
    ).fetchone()

    should_submit = False
    if task:
        task_id = task["id"]
        task_status = task["status"]
        attempts = int(task["attempt_count"] or 0)
        if task_status == "completed":
            return {
                "task_id": task_id,
                "task_status": "completed",
                "should_submit": False,
            }
        if task_status == "failed" and not retry_failed:
            conn.execute(
                "UPDATE knowledge_versions SET index_status = 'pending_cleanup' WHERE id = ?",
                (version_id,),
            )
            return {
                "task_id": task_id,
                "task_status": "failed",
                "should_submit": False,
            }
        if attempts >= MAX_TASK_ATTEMPTS:
            raise ValueError(f"索引清理已达到最大重试次数 {MAX_TASK_ATTEMPTS}，请检查失败原因后人工处理")
        if task_status in ("failed", "cancelled"):
            conn.execute(
                """
                UPDATE processing_tasks
                SET status = 'queued', started_at = NULL, completed_at = NULL
                WHERE id = ?
                """,
                (task_id,),
            )
            task_status = "queued"
        should_submit = task_status == "queued"
    else:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO processing_tasks (
                id, organization_id, target_type, target_id, task_type, status,
                attempt_count, idempotency_key, created_at
            )
            VALUES (?, ?, 'knowledge_version', ?, 'clean_index', 'queued', 0, ?, ?)
            """,
            (task_id, org_id, version_id, idempotency_key, _now()),
        )
        task_status = "queued"
        should_submit = True

    conn.execute(
        "UPDATE knowledge_versions SET index_status = 'pending_cleanup' WHERE id = ?",
        (version_id,),
    )
    return {
        "task_id": task_id,
        "task_status": task_status,
        "should_submit": should_submit,
    }


def execute_clean_index_task(task_id: str):
    """删除检索派生记录；失败只留下 pending_cleanup，绝不恢复业务资格。"""
    with get_db() as conn:
        task = conn.execute(
            """
            SELECT id, organization_id, target_id, status, attempt_count
            FROM processing_tasks
            WHERE id = ? AND task_type = 'clean_index'
            """,
            (task_id,),
        ).fetchone()
        if not task or task["status"] != "queued":
            return
        if int(task["attempt_count"] or 0) >= MAX_TASK_ATTEMPTS:
            conn.execute(
                "UPDATE processing_tasks SET status = 'failed', error_message = ?, completed_at = ? WHERE id = ?",
                ("索引清理已达到最大重试次数", _now(), task_id),
            )
            conn.execute(
                "UPDATE knowledge_versions SET index_status = 'pending_cleanup' WHERE id = ?",
                (task["target_id"],),
            )
            return

        claimed = conn.execute(
            """
            UPDATE processing_tasks
            SET status = 'running', started_at = ?, completed_at = NULL,
                attempt_count = attempt_count + 1, error_message = NULL
            WHERE id = ? AND status = 'queued'
            """,
            (_now(), task_id),
        )
        if claimed.rowcount != 1:
            return
        version_id = task["target_id"]

    try:
        with get_db() as conn:
            conn.execute("DELETE FROM retrieval_records WHERE knowledge_version_id = ?", (version_id,))
            conn.execute(
                "UPDATE knowledge_versions SET index_status = 'not_indexed' WHERE id = ?",
                (version_id,),
            )
            conn.execute(
                """
                UPDATE processing_tasks
                SET status = 'completed', error_message = NULL, completed_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (_now(), task_id),
            )
    except Exception as exc:
        with get_db() as conn:
            conn.execute(
                """
                UPDATE processing_tasks
                SET status = 'failed', error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (str(exc), _now(), task_id),
            )
            conn.execute(
                "UPDATE knowledge_versions SET index_status = 'pending_cleanup' WHERE id = ?",
                (version_id,),
            )
        logger.exception("Clean index task %s failed", task_id)


def get_cleanup_status(conn, version_ids: Sequence[str]) -> Dict[str, int]:
    if not version_ids:
        return {"queued": 0, "running": 0, "failed": 0, "completed": 0}
    placeholders = ",".join("?" for _ in version_ids)
    rows = conn.execute(
        f"""
        SELECT status, COUNT(*) AS c
        FROM processing_tasks
        WHERE task_type = 'clean_index' AND target_id IN ({placeholders})
        GROUP BY status
        """,
        list(version_ids),
    ).fetchall()
    result = {"queued": 0, "running": 0, "failed": 0, "completed": 0}
    for row in rows:
        if row["status"] in result:
            result[row["status"]] = int(row["c"] or 0)
    return result


def ensure_missing_stage4b_index_tasks() -> List[str]:
    """为 Stage 4A 遗留 keyword-v1/缺失索引创建当前配置任务；失败任务不自动重试。"""
    config_hash = get_retrieval_config_hash()
    queued: List[str] = []
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT kv.id, kv.organization_id
            FROM knowledge_versions kv
            JOIN knowledge_items ki ON ki.id = kv.item_id
            JOIN documents d ON d.id = ki.document_id
            WHERE kv.review_status = 'confirmed'
              AND kv.index_status != 'failed'
              AND ki.active_version_id = kv.id
              AND ki.lifecycle_status = 'active'
              AND (ki.is_excluded = 0 OR ki.is_excluded IS NULL)
              AND d.is_deleted = 0
              AND NOT EXISTS (
                  SELECT 1 FROM retrieval_records rr
                  WHERE rr.knowledge_version_id = kv.id
                    AND rr.config_hash = ?
                    AND rr.vector_json IS NOT NULL
                    AND rr.embedding_dim = ?
              )
            """,
            (config_hash, EMBEDDING_DIM),
        ).fetchall()
        for row in rows:
            result = create_or_get_build_index_task(
                conn, row["id"], row["organization_id"], retry_failed=False
            )
            if result["should_submit"]:
                queued.append(result["task_id"])
    return queued
