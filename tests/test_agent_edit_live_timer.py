from __future__ import annotations

import io
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agents.author.deepseek_writer import DeepSeekWriter
from src.utils.live_timer import LiveStageTimer
from src.workflows.chapter_workflow import (
    _stage_finish,
    _stage_start,
    agent_edit_chapter,
)


class _TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class AgentEditContractTests(unittest.TestCase):
    def test_reviewer_fact_and_conflicting_prose_reach_constrained_prompt(self):
        writer = object.__new__(DeepSeekWriter)
        writer.run = MagicMock(return_value=SimpleNamespace(content="修订正文"))
        issue = "既有正式事实 = A；正文当前写成 B；必须改成 A"

        writer.revise_chapter(
            chapter_plan_text="APPROVED PLAN",
            chapter_index=3,
            chapter_text="当前正文包含 B",
            review_issues=[issue],
        )

        prompt = writer.run.call_args.kwargs["user_message"]
        self.assertIn("既有正式事实 = A", prompt)
        self.assertIn("正文当前写成 B", prompt)
        self.assertIn("MUST FIX", prompt)
        self.assertIn("不得自行发明第三种解释", prompt)
        self.assertIn("只修改", prompt)

    def test_empty_human_feedback_keeps_all_reviewer_issues(self):
        editor = MagicMock()
        editor.revise_chapter.return_value = "修订后正文"
        state = {
            "novel_id": "smoke",
            "chapter_index": 3,
            "chapter_plan_text": "PLAN",
            "styled_text": "正文当前写成 B",
            "human_decision": "agent_edit",
            "agent_execution": "supervised",
            "review_round": 3,
            "t1_issues": ["硬问题"],
            "review_issues": ["既有事实 A 与正文 B 冲突，必须改为 A"],
            "review_reasons": ["连续性冲突"],
            "human_feedback": "",
        }
        with patch(
            "src.agents.author.deepseek_writer.DeepSeekWriter",
            return_value=editor,
        ), patch(
            "src.utils.live_timer.time.perf_counter",
            side_effect=[10.0, 10.2],
        ):
            result = agent_edit_chapter(state)

        kwargs = editor.revise_chapter.call_args.kwargs
        self.assertEqual("", kwargs["human_feedback"])
        self.assertIn("硬问题", kwargs["review_issues"])
        self.assertIn(
            "既有事实 A 与正文 B 冲突，必须改为 A", kwargs["review_issues"]
        )
        self.assertIn("连续性冲突", kwargs["review_issues"])
        self.assertEqual("修订后正文", result["styled_text"])

    def test_agent_edit_event_records_duration(self):
        editor = MagicMock()
        editor.revise_chapter.return_value = "修订后正文"
        with patch(
            "src.agents.author.deepseek_writer.DeepSeekWriter",
            return_value=editor,
        ), patch(
            "src.utils.live_timer.time.perf_counter",
            side_effect=[20.0, 20.1],
        ):
            result = agent_edit_chapter({
                "novel_id": "smoke",
                "chapter_index": 3,
                "styled_text": "正文",
                "human_decision": "agent_edit",
                "review_round": 3,
                "review_issues": ["明确问题"],
            })

        event = result["generation_events"][0]
        self.assertEqual("PROSE_AGENT_EDITED", event["event_type"])
        self.assertGreaterEqual(event["duration_ms"], 0)


class LiveTimerTests(unittest.TestCase):
    def test_non_tty_has_no_control_characters_and_keeps_final_duration(self):
        output = io.StringIO()
        with patch.object(sys, "stdout", output), patch(
            "src.utils.live_timer.time.perf_counter",
            side_effect=[30.0, 30.25],
        ):
            timer = _stage_start({"chapter_index": 3}, "审阅正文")
            print("[review_chapter] Review #3")
            duration = _stage_finish(
                {"chapter_index": 3}, timer, "正文审阅"
            )

        rendered = output.getvalue()
        self.assertNotIn("\r", rendered)
        self.assertNotIn("\033[2K", rendered)
        self.assertIn("[review_chapter] Review #3", rendered)
        self.assertIn("正文审阅完成", rendered)
        self.assertEqual(250.0, duration)

    def test_tty_timer_stops_and_preserves_existing_log(self):
        output = _TTYBuffer()
        with patch.object(sys, "stdout", output):
            timer = LiveStageTimer(
                3, "审阅正文", output=output, refresh_seconds=0.01
            ).start()
            print("[writer] Scene 4 completed")
            time.sleep(0.04)
            duration = timer.finish()
            print("正文审阅完成")

        thread = timer.thread
        self.assertIsNotNone(thread)
        self.assertFalse(thread.is_alive())
        rendered = output.getvalue()
        self.assertIn("[writer] Scene 4 completed", rendered)
        self.assertIn("正文审阅完成", rendered)
        self.assertIn("已用时", rendered)
        self.assertGreaterEqual(duration, 0)


if __name__ == "__main__":
    unittest.main()
