import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

import deepseek_extractor as de


def make_block(idx, path, text=None):
    return {
        "id": f"sb_{idx}",
        "block_index": idx,
        "block_type": "paragraph",
        "heading_path": path,
        "paragraph_anchor": f"line_{idx}",
        "page_number": None,
        "text_content": text or f"第{idx}条：巡检人员须记录第{idx}项检查结果。",
    }


class FakeResponse:
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.body, ensure_ascii=False).encode("utf-8")


class TestDeepSeekBatching(unittest.TestCase):
    def test_semantic_batching_keeps_sections_and_overlaps_only_inside_section(self):
        blocks = [make_block(i, "手册 / A") for i in range(1, 6)]
        blocks += [make_block(6, "手册 / B"), make_block(7, "手册 / B")]

        batches = de.build_semantic_batches(
            blocks,
            max_blocks=3,
            max_chars=99999,
            overlap=1,
        )

        self.assertEqual(len(batches), 3)
        self.assertEqual([b["section_path"] for b in batches], ["手册 / A", "手册 / A", "手册 / B"])
        self.assertEqual([x["id"] for x in batches[0]["blocks"]], ["sb_1", "sb_2", "sb_3"])
        self.assertEqual([x["id"] for x in batches[1]["blocks"]], ["sb_3", "sb_4", "sb_5"])
        self.assertEqual([x["id"] for x in batches[2]["blocks"]], ["sb_6", "sb_7"])
    def test_single_call_explicitly_disables_thinking(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse({
                "choices": [{"message": {"content": '{"items":[]}'}}],
            })

        with patch("deepseek_extractor.urllib.request.urlopen", side_effect=fake_urlopen):
            items, ctx = de.extract_atoms_via_deepseek(
                [make_block(1, "手册 / A")],
                "测试.md",
                api_key="test-key",
                model_name="deepseek-flash",
                batch_context="手册 / A；章节内第 1 批",
            )

        self.assertEqual(items, [])
        self.assertEqual(captured["payload"]["thinking"], {"type": "disabled"})
        self.assertEqual(captured["payload"]["reasoning_effort"], "none")
        self.assertEqual(captured["payload"]["max_tokens"], de.DEEPSEEK_MAX_OUTPUT_TOKENS)
        self.assertIn("章节内第 1 批", captured["payload"]["messages"][1]["content"])
        self.assertEqual(ctx["thinking"], "disabled")

    def test_batched_call_raises_on_failed_batch(self):
        blocks = [
            make_block(1, "手册 / A", "巡检人员须记录巡检结果。"),
            make_block(2, "手册 / B", "电梯困人时中控室须保持双向通话。"),
        ]
        def fake_call(source_blocks, document_title, **kwargs):
            if "手册 / B" in (kwargs.get("batch_context") or ""):
                raise RuntimeError("simulated batch timeout")
            return [{
                "title": "巡检记录",
                "primary_category": "制度与标准",
                "atom_type": "规则",
                "subject": "巡检人员",
                "statement": source_blocks[0]["text_content"],
                "conditions": [],
                "actions": [source_blocks[0]["text_content"]],
                "exceptions": [],
                "source_evidence": [{
                    "field_name": "statement",
                    "source_block_id": source_blocks[0]["id"],
                    "excerpt": source_blocks[0]["text_content"],
                }],
            }], {"provider": "fake"}

        with patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_call):
            with self.assertRaises(RuntimeError) as cm:
                de.extract_atoms_via_deepseek_batched(
                    blocks,
                    "测试.md",
                    api_key="test-key",
                    model_name="deepseek-flash",
                )
            self.assertIn("DeepSeek 批次抽取失败", str(cm.exception))

    def test_compute_batch_key_sensitivity_and_properties(self):
        batch = {
            "section_path": "章1 / 节1",
            "chunk_index": 1,
            "blocks": [make_block(1, "章1 / 节1", "标准检查项")],
        }
        key1 = de.compute_batch_key("org_1", "ver_1", batch, "文档.md", "deepseek-chat")
        key2 = de.compute_batch_key("org_1", "ver_1", batch, "文档.md", "deepseek-chat")
        self.assertEqual(key1, key2)
        self.assertEqual(len(key1), 64)

        # 模型变更加密键不同
        key_diff_model = de.compute_batch_key("org_1", "ver_1", batch, "文档.md", "deepseek-coder")
        self.assertNotEqual(key1, key_diff_model)

        # 版本变更加密键不同
        key_diff_ver = de.compute_batch_key("org_1", "ver_2", batch, "文档.md", "deepseek-chat")
        self.assertNotEqual(key1, key_diff_ver)

        # 内容变更加密键不同
        batch_diff_content = {
            "section_path": "章1 / 节1",
            "chunk_index": 1,
            "blocks": [make_block(1, "章1 / 节1", "已修改的检查项")],
        }
        key_diff_content = de.compute_batch_key("org_1", "ver_1", batch_diff_content, "文档.md", "deepseek-chat")
        self.assertNotEqual(key1, key_diff_content)

    def test_checkpoint_persistence_and_reuse_on_batch_failure_retry(self):
        from test_env_helper import setup_test_db, cleanup_test_db
        import database

        db_path = setup_test_db("test_ckpt")
        try:
            org_id = "org_greentown"
            ver_id = "ver_ckpt_test"

            # 插入前置 document 及 version 记录
            with database.get_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO documents (id, organization_id, title, access_scope, created_at, updated_at) VALUES (?, ?, ?, 'org_internal', '2026-01-01', '2026-01-01')",
                    ("doc_ckpt", org_id, "测试资料")
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO document_versions
                    (id, document_id, organization_id, version_label, file_name, file_type, file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                    VALUES (?, ?, ?, 'v1.0', 'test.docx', 'docx', 100, 'hash', 'ref', 'usr_admin_001', '2026-01-01', 'extracting')
                    """,
                    (ver_id, "doc_ckpt", org_id)
                )

            blocks = [
                make_block(1, "手册 / A", "第一批：巡检人员须记录巡检结果。"),
                make_block(2, "手册 / B", "第二批：电梯困人时中控室须保持双向通话。"),
            ]

            calls = []

            def fake_extract(source_blocks, document_title, **kwargs):
                ctx = kwargs.get("batch_context") or ""
                calls.append(ctx)
                if "手册 / B" in ctx and len(calls) == 2:
                    raise RuntimeError("simulated network drop on batch 2")
                idx = source_blocks[0]["block_index"]
                return [{
                    "title": f"知识条目{idx}",
                    "primary_category": "制度与标准",
                    "atom_type": "规则",
                    "subject": "责任人",
                    "statement": source_blocks[0]["text_content"],
                    "conditions": [],
                    "actions": [source_blocks[0]["text_content"]],
                    "exceptions": [],
                    "source_evidence": [{
                        "field_name": "statement",
                        "source_block_id": source_blocks[0]["id"],
                        "excerpt": source_blocks[0]["text_content"],
                    }],
                }], {"provider": "fake"}

            # 第一次抽取：批次1成功，批次2失败
            with patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_extract):
                with self.assertRaises(RuntimeError) as cm:
                    de.extract_atoms_via_deepseek_batched(
                        source_blocks=blocks,
                        document_title="测试资料",
                        api_key="test-key",
                        model_name="deepseek-flash",
                        organization_id=org_id,
                        document_version_id=ver_id,
                    )
                self.assertIn("DeepSeek 批次抽取失败", str(cm.exception))

            # 验证批次1已被持久化到 extraction_checkpoints
            with database.get_db() as conn:
                ckpts = conn.execute(
                    "SELECT batch_index, section_path, status, payload_hash, candidates_json FROM extraction_checkpoints WHERE document_version_id = ?",
                    (ver_id,)
                ).fetchall()
                self.assertEqual(len(ckpts), 1)
                self.assertEqual(ckpts[0]["batch_index"], 1)
                self.assertEqual(ckpts[0]["section_path"], "手册 / A")
                self.assertEqual(ckpts[0]["status"], "completed")

            # 第二次抽取（模拟任务重试）：批次1直接复用检查点，无需调用模型；只调用批次2
            calls.clear()
            with patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_extract):
                merged, meta = de.extract_atoms_via_deepseek_batched(
                    source_blocks=blocks,
                    document_title="测试资料",
                    api_key="test-key",
                    model_name="deepseek-flash",
                    organization_id=org_id,
                    document_version_id=ver_id,
                )

            # 模型应仅被调用一次（仅批次2）
            self.assertEqual(len(calls), 1)
            self.assertIn("手册 / B", calls[0])

            # 结果中应包含两个批次的候选知识
            self.assertEqual(len(merged), 2)
            self.assertEqual(meta["batches"][0]["checkpoint_reused"], True)
            self.assertEqual(meta["batches"][1]["checkpoint_reused"], False)

            # 批次2现在也已存入检查点
            with database.get_db() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM extraction_checkpoints WHERE document_version_id = ?",
                    (ver_id,)
                ).fetchone()["cnt"]
                self.assertEqual(count, 2)

        finally:
            cleanup_test_db(db_path)

    def test_checkpoint_invalidated_when_model_changes(self):
        from test_env_helper import setup_test_db, cleanup_test_db
        import database

        db_path = setup_test_db("test_ckpt_inval")
        try:
            org_id = "org_greentown"
            ver_id = "ver_ckpt_inval"
            with database.get_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO documents (id, organization_id, title, access_scope, created_at, updated_at) VALUES (?, ?, ?, 'org_internal', '2026-01-01', '2026-01-01')",
                    ("doc_inval", org_id, "测试资料2")
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO document_versions
                    (id, document_id, organization_id, version_label, file_name, file_type, file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                    VALUES (?, ?, ?, 'v1.0', 'test.docx', 'docx', 100, 'hash', 'ref', 'usr_admin_001', '2026-01-01', 'extracting')
                    """,
                    (ver_id, "doc_inval", org_id)
                )

            blocks = [make_block(1, "手册 / A", "第一批：巡检内容。")]

            def fake_extract(source_blocks, document_title, **kwargs):
                return [{
                    "title": "知识1",
                    "primary_category": "制度与标准",
                    "atom_type": "规则",
                    "statement": "巡检内容",
                    "conditions": [],
                    "actions": ["巡检内容"],
                    "exceptions": [],
                    "source_evidence": [{
                        "field_name": "statement",
                        "source_block_id": source_blocks[0]["id"],
                        "excerpt": "巡检内容",
                    }],
                }], {"provider": "fake"}

            # 首次抽取，使用 deepseek-flash
            with patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_extract) as mock_ext:
                de.extract_atoms_via_deepseek_batched(
                    source_blocks=blocks,
                    document_title="测试资料2",
                    api_key="test-key",
                    model_name="deepseek-flash",
                    organization_id=org_id,
                    document_version_id=ver_id,
                )
                self.assertEqual(mock_ext.call_count, 1)

            # 更换模型为 deepseek-chat：检查点不匹配，重新调用模型
            with patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_extract) as mock_ext:
                merged, meta = de.extract_atoms_via_deepseek_batched(
                    source_blocks=blocks,
                    document_title="测试资料2",
                    api_key="test-key",
                    model_name="deepseek-chat",
                    organization_id=org_id,
                    document_version_id=ver_id,
                )
                self.assertEqual(mock_ext.call_count, 1)
                self.assertEqual(meta["batches"][0]["checkpoint_reused"], False)

        finally:
            cleanup_test_db(db_path)

    def test_corrupted_payload_hash_is_discarded(self):
        from test_env_helper import setup_test_db, cleanup_test_db
        import database

        db_path = setup_test_db("test_ckpt_corrupt")
        try:
            org_id = "org_greentown"
            ver_id = "ver_ckpt_corrupt"
            with database.get_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO documents (id, organization_id, title, access_scope, created_at, updated_at) VALUES (?, ?, ?, 'org_internal', '2026-01-01', '2026-01-01')",
                    ("doc_corrupt", org_id, "测试资料3")
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO document_versions
                    (id, document_id, organization_id, version_label, file_name, file_type, file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                    VALUES (?, ?, ?, 'v1.0', 'test.docx', 'docx', 100, 'hash', 'ref', 'usr_admin_001', '2026-01-01', 'extracting')
                    """,
                    (ver_id, "doc_corrupt", org_id)
                )

            blocks = [make_block(1, "手册 / A", "第一批：巡检内容。")]

            def fake_extract(source_blocks, document_title, **kwargs):
                return [{
                    "title": "知识1",
                    "primary_category": "制度与标准",
                    "atom_type": "规则",
                    "statement": "巡检内容",
                    "conditions": [],
                    "actions": ["巡检内容"],
                    "exceptions": [],
                    "source_evidence": [{
                        "field_name": "statement",
                        "source_block_id": source_blocks[0]["id"],
                        "excerpt": "巡检内容",
                    }],
                }], {"provider": "fake"}

            with patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_extract):
                de.extract_atoms_via_deepseek_batched(
                    source_blocks=blocks,
                    document_title="测试资料3",
                    api_key="test-key",
                    model_name="deepseek-flash",
                    organization_id=org_id,
                    document_version_id=ver_id,
                )

            # 篡改检查点 hash
            with database.get_db() as conn:
                conn.execute(
                    "UPDATE extraction_checkpoints SET payload_hash = 'tampered_hash' WHERE document_version_id = ?",
                    (ver_id,)
                )

            # 再次抽取：篡改检查点被安全舍弃，重新调用模型
            with patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_extract) as mock_ext:
                merged, meta = de.extract_atoms_via_deepseek_batched(
                    source_blocks=blocks,
                    document_title="测试资料3",
                    api_key="test-key",
                    model_name="deepseek-flash",
                    organization_id=org_id,
                    document_version_id=ver_id,
                )
                self.assertEqual(mock_ext.call_count, 1)
                self.assertEqual(meta["batches"][0]["checkpoint_reused"], False)

        finally:
            cleanup_test_db(db_path)

    def test_checkpoint_invalidated_when_max_tokens_or_endpoint_or_params_change(self):
        batch = {
            "section_path": "章1 / 节1",
            "chunk_index": 1,
            "blocks": [make_block(1, "章1 / 节1", "检查项A"), make_block(2, "章1 / 节1", "检查项B")],
        }
        key_base = de.compute_batch_key(
            "org_1", "ver_1", batch, "文档.md", "deepseek-chat",
            base_url="https://api.deepseek.com/v1", max_tokens=4096
        )

        # 1. max_tokens 变更后键改变（解决修改 DEEPSEEK_MAX_OUTPUT_TOKENS 后检查点未失效的问题）
        key_diff_tokens = de.compute_batch_key(
            "org_1", "ver_1", batch, "文档.md", "deepseek-chat",
            base_url="https://api.deepseek.com/v1", max_tokens=8192
        )
        self.assertNotEqual(key_base, key_diff_tokens)

        # 2. 端点身份（base_url）变更后键改变
        key_diff_endpoint = de.compute_batch_key(
            "org_1", "ver_1", batch, "文档.md", "deepseek-chat",
            base_url="https://custom-proxy.internal/v1", max_tokens=4096
        )
        self.assertNotEqual(key_base, key_diff_endpoint)

        # 3. 剥离端点携带的凭据：包含 basic auth 的 url 与纯 url 规范化后计算结果相同，且凭据不被写入键
        key_with_auth = de.compute_batch_key(
            "org_1", "ver_1", batch, "文档.md", "deepseek-chat",
            base_url="https://secret_user:secret_pass@api.deepseek.com/v1", max_tokens=4096
        )
        self.assertEqual(key_base, key_with_auth)
        self.assertNotIn("secret_pass", key_with_auth)

        # 4. 生成参数（temperature / thinking）变更后键改变
        key_diff_gen = de.compute_batch_key(
            "org_1", "ver_1", batch, "文档.md", "deepseek-chat",
            base_url="https://api.deepseek.com/v1", max_tokens=4096,
            generation_params={"temperature": 0.7, "thinking": "enabled"}
        )
        self.assertNotEqual(key_base, key_diff_gen)

        # 5. 批次内部 blocks 顺序改变后键改变
        batch_reversed = {
            "section_path": "章1 / 节1",
            "chunk_index": 1,
            "blocks": [make_block(2, "章1 / 节1", "检查项B"), make_block(1, "章1 / 节1", "检查项A")],
        }
        key_diff_order = de.compute_batch_key(
            "org_1", "ver_1", batch_reversed, "文档.md", "deepseek-chat",
            base_url="https://api.deepseek.com/v1", max_tokens=4096
        )
        self.assertNotEqual(key_base, key_diff_order)

        # 6. 批次章节上下文改变后键改变
        key_diff_context = de.compute_batch_key(
            "org_1", "ver_1", batch, "文档.md", "deepseek-chat",
            base_url="https://api.deepseek.com/v1", max_tokens=4096,
            batch_context="特殊修改过的上下文"
        )
        self.assertNotEqual(key_base, key_diff_context)

    def test_checkpoint_invalidated_in_batched_extraction_when_max_tokens_change(self):
        from test_env_helper import setup_test_db, cleanup_test_db
        import database

        db_path = setup_test_db("test_ckpt_tokens")
        try:
            org_id = "org_greentown"
            ver_id = "ver_ckpt_tokens"
            with database.get_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO documents (id, organization_id, title, access_scope, created_at, updated_at) VALUES (?, ?, ?, 'org_internal', '2026-01-01', '2026-01-01')",
                    ("doc_tokens", org_id, "测试资料-Token")
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO document_versions
                    (id, document_id, organization_id, version_label, file_name, file_type, file_size, content_hash, storage_reference, uploaded_by, uploaded_at, processing_status)
                    VALUES (?, ?, ?, 'v1.0', 'test.docx', 'docx', 100, 'hash', 'ref', 'usr_admin_001', '2026-01-01', 'extracting')
                    """,
                    (ver_id, "doc_tokens", org_id)
                )

            blocks = [make_block(1, "手册 / A", "第一批：巡检内容。")]

            def fake_extract(source_blocks, document_title, **kwargs):
                return [{
                    "title": "知识1",
                    "primary_category": "制度与标准",
                    "atom_type": "规则",
                    "statement": "巡检内容",
                    "conditions": [],
                    "actions": ["巡检内容"],
                    "exceptions": [],
                    "source_evidence": [{
                        "field_name": "statement",
                        "source_block_id": source_blocks[0]["id"],
                        "excerpt": "巡检内容",
                    }],
                }], {"provider": "fake"}

            # 首次抽取
            with patch("deepseek_extractor.DEEPSEEK_MAX_OUTPUT_TOKENS", 2048):
                with patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_extract) as mock_ext:
                    de.extract_atoms_via_deepseek_batched(
                        source_blocks=blocks,
                        document_title="测试资料-Token",
                        api_key="test-key-1",
                        model_name="deepseek-flash",
                        organization_id=org_id,
                        document_version_id=ver_id,
                    )
                    self.assertEqual(mock_ext.call_count, 1)

            # 仅更换 API Key（模型、端点与 max_tokens 不变）：复用检查点，不重新调用模型
            with patch("deepseek_extractor.DEEPSEEK_MAX_OUTPUT_TOKENS", 2048):
                with patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_extract) as mock_ext:
                    merged, meta = de.extract_atoms_via_deepseek_batched(
                        source_blocks=blocks,
                        document_title="测试资料-Token",
                        api_key="test-key-2-different",
                        model_name="deepseek-flash",
                        organization_id=org_id,
                        document_version_id=ver_id,
                    )
                    self.assertEqual(mock_ext.call_count, 0)
                    self.assertEqual(meta["batches"][0]["checkpoint_reused"], True)

            # 修改 DEEPSEEK_MAX_OUTPUT_TOKENS：检查点失效，重新调用模型抽取
            with patch("deepseek_extractor.DEEPSEEK_MAX_OUTPUT_TOKENS", 8192):
                with patch("deepseek_extractor.extract_atoms_via_deepseek", side_effect=fake_extract) as mock_ext:
                    merged, meta = de.extract_atoms_via_deepseek_batched(
                        source_blocks=blocks,
                        document_title="测试资料-Token",
                        api_key="test-key-1",
                        model_name="deepseek-flash",
                        organization_id=org_id,
                        document_version_id=ver_id,
                    )
                    self.assertEqual(mock_ext.call_count, 1)
                    self.assertEqual(meta["batches"][0]["checkpoint_reused"], False)

        finally:
            cleanup_test_db(db_path)


if __name__ == "__main__":
    unittest.main()
