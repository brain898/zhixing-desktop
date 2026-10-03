import json
import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

from deepseek_extractor import validate_and_sanitize_atoms


class TestSourceFidelityRegression(unittest.TestCase):

    def test_unrelated_rules_do_not_create_false_conflict(self):
        blocks = [
            {"id": "sb_a", "block_index": 1, "block_type": "paragraph", "text_content": "责任工程师在设备巡检期间必须记录巡检台账。"},
            {"id": "sb_b", "block_index": 2, "block_type": "paragraph", "text_content": "责任工程师在电梯困人时不得擅自暴力扒门。"},
        ]
        raw = [
            {
                "title": "巡检记录",
                "primary_category": "制度与标准",
                "atom_type": "规则",
                "subject": "责任工程师",
                "statement": blocks[0]["text_content"],
                "conditions": ["设备巡检期间"],
                "actions": ["必须记录巡检台账"],
                "exceptions": [],
                "source_evidence": [{"field_name": "statement", "source_block_id": "sb_a", "excerpt": blocks[0]["text_content"]}],
            },
            {
                "title": "电梯困人",
                "primary_category": "制度与标准",
                "atom_type": "规则",
                "subject": "责任工程师",
                "statement": blocks[1]["text_content"],
                "conditions": ["电梯困人时"],
                "actions": ["不得擅自暴力扒门"],
                "exceptions": [],
                "source_evidence": [{"field_name": "statement", "source_block_id": "sb_b", "excerpt": blocks[1]["text_content"]}],
            },
        ]
        sanitized = validate_and_sanitize_atoms(raw, blocks, "ver_regression")
        for atom in sanitized:
            self.assertFalse(any("疑似规则冲突" in flag for flag in atom["quality_flags"]))

    def test_structured_fields_strip_list_number_but_evidence_keeps_original(self):
        original = "3. 查验动作：机电顾问团队须执行带载切换测试，切换时间控制在3秒以内。"
        blocks = [{
            "id": "sb_numbered",
            "block_index": 10,
            "block_type": "list_item",
            "heading_path": "手册 / 2.1 供配电验收",
            "text_content": original,
        }]
        raw = [{
            "title": "2.1 供配电验收｜查验动作",
            "primary_category": "方法与工具",
            "atom_type": "方法",
            "subject": "机电顾问团队",
            "statement": original,
            "conditions": [],
            "actions": [original],
            "exceptions": [],
            "source_evidence": [{
                "field_name": "statement",
                "source_block_id": "sb_numbered",
                "excerpt": original,
            }],
        }]

        sanitized = validate_and_sanitize_atoms(raw, blocks, "ver_numbered")
        self.assertEqual(sanitized[0]["statement"], "查验动作：机电顾问团队须执行带载切换测试，切换时间控制在3秒以内。")
        self.assertEqual(sanitized[0]["actions"][0], "查验动作：机电顾问团队须执行带载切换测试，切换时间控制在3秒以内。")
        self.assertEqual(sanitized[0]["source_evidence"][0]["excerpt"], original)




if __name__ == "__main__":
    unittest.main()
