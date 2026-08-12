from __future__ import annotations

import inspect
import unittest
from unittest.mock import MagicMock

from src.agents.author.claude_stylist import ClaudeStylist


class StylistScopeTests(unittest.TestCase):
    def test_stylist_has_no_human_feedback_contract_or_prompt_section(self):
        signature = inspect.signature(ClaudeStylist.edit_chapter)
        self.assertNotIn("style_feedback", signature.parameters)
        self.assertNotIn("human_feedback", signature.parameters)

        stylist = object.__new__(ClaudeStylist)
        stylist.model_slot = MagicMock()
        stylist.model_slot.max_tokens = 100_000
        stylist._call_write_slot = MagicMock(return_value="STYLED")
        result = stylist.edit_chapter("WRITER DRAFT", 4, "APPROVED PLAN")

        self.assertEqual("STYLED", result)
        prompt = stylist._call_write_slot.call_args.args[0]
        self.assertIn("WRITER DRAFT", prompt)
        self.assertIn("APPROVED PLAN", prompt)
        self.assertNotIn("人工反馈", prompt)
        self.assertNotIn("human_feedback", prompt)


if __name__ == "__main__":
    unittest.main()
