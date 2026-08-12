from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main as cli
from src.workflows.chapter_runner import ChapterWorkflowRunner


class SupervisedReviewFeedbackGuardTests(unittest.TestCase):
    def setUp(self):
        lock = patch("src.workflows.chapter_runner.NovelOperationLock")
        lock.start()
        self.addCleanup(lock.stop)

    @staticmethod
    def _runner_with_pending(kind: str, verdict: str):
        runner = object.__new__(ChapterWorkflowRunner)
        runner.novel_id = "guard"
        runner.chapter_index = 4
        runner.file_store = SimpleNamespace(root=MagicMock())

        pending = MagicMock()
        pending.id = "interrupt-1"
        pending.value = {
            "type": kind,
            "novel_id": "guard",
            "chapter_index": 4,
            "verdict": verdict,
            "reasons": ["T3 观察项均为非阻断建议"],
            "allowed_actions": ["agent_edit"],
        }
        snapshot = SimpleNamespace(interrupts=[pending], values={})
        connection = MagicMock()
        graph = MagicMock()
        graph.get_state.return_value = snapshot
        runner._open_graph = MagicMock(
            return_value=(connection, MagicMock(), graph)
        )
        return runner, graph, connection, snapshot
        runner._result_or_interrupt = MagicMock(side_effect=lambda _graph, result: result)

    def test_pass_empty_feedback_is_rejected_before_resume_for_plan_and_prose(self):
        for kind in ("plan_review", "final_author_approval"):
            with self.subTest(kind=kind):
                runner, graph, connection, snapshot = self._runner_with_pending(
                    kind, "PASS"
                )
                with self.assertRaisesRegex(
                    ValueError, "Review 已通过，Agent 自动修改需要提供修改意见"
                ):
                    runner.resume({"action": "agent_edit", "feedback": ""})

                graph.invoke.assert_not_called()
                self.assertIs(graph.get_state.return_value, snapshot)
                connection.close.assert_called_once()

    def test_needs_revision_empty_feedback_resumes_for_plan_and_prose(self):
        for kind in ("plan_review", "chapter_review"):
            with self.subTest(kind=kind):
                runner, graph, _connection, _snapshot = self._runner_with_pending(
                    kind, "NEEDS_REVISION"
                )
                graph.invoke.return_value = {"workflow_status": "RESUMED"}
                runner.resume({"action": "agent_edit", "feedback": ""})

                command = graph.invoke.call_args.args[0]
                self.assertEqual("", command.resume["feedback"])

    def test_pass_nonempty_feedback_resumes_for_plan_and_prose(self):
        for kind in ("plan_review", "final_author_approval"):
            with self.subTest(kind=kind):
                runner, graph, _connection, _snapshot = self._runner_with_pending(
                    kind, "PASS"
                )
                graph.invoke.return_value = {"workflow_status": "RESUMED"}
                runner.resume({
                    "action": "agent_edit",
                    "feedback": "收紧这一处措辞",
                })

                command = graph.invoke.call_args.args[0]
                self.assertEqual("收紧这一处措辞", command.resume["feedback"])

    def test_cli_pass_empty_feedback_returns_without_resume_value(self):
        payload = {
            "type": "plan_review",
            "verdict": "PASS",
            "reasons": ["T3 观察项均为非阻断建议"],
            "allowed_actions": ["agent_edit"],
        }
        output = io.StringIO()
        with patch("builtins.input", side_effect=["1", ""]) as user_input, redirect_stdout(output):
            value = cli._interactive_resume_value("guard", 4, payload)

        self.assertIsNone(value)
        rendered = output.getvalue()
        self.assertIn("此处不能为空", user_input.call_args_list[1].args[0])
        self.assertIn("Review 已通过，Agent 自动修改需要提供修改意见", rendered)

    def test_cli_needs_revision_empty_feedback_is_preserved(self):
        payload = {
            "type": "chapter_review",
            "verdict": "NEEDS_REVISION",
            "reasons": ["修复已指出的问题"],
            "allowed_actions": ["agent_edit"],
        }
        output = io.StringIO()
        with patch("builtins.input", side_effect=["1", ""]) as user_input, redirect_stdout(output):
            value = cli._interactive_resume_value("guard", 4, payload)

        self.assertEqual({"action": "agent_edit", "feedback": ""}, value)
        self.assertIn(
            "直接回车则仅使用 Reviewer 已给出的修改问题",
            user_input.call_args_list[1].args[0],
        )


if __name__ == "__main__":
    unittest.main()
