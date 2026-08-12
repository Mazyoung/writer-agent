import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from src.config.runtime_policy import NovelRuntimePolicy
from src.config.settings import ModelSlot
from src.core.model_provider import (
    EmptyModelResponseError,
    GenerationLimitExceeded,
    ModelProviderClient,
)
from src.workflows.chapter_runner import ChapterWorkflowRunner
from src.workflows.chapter_workflow import _guard_node


def _slot(provider="openai_compatible"):
    return ModelSlot(
        name="plan",
        provider=provider,
        api_key="secret",
        base_url="https://provider.example",
        model="deepseek-v4-flash",
        max_tokens=4096,
    )


def _response(content, *, finish_reason="stop", reasoning_content=None):
    message = SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
    )
    return SimpleNamespace(choices=[
        SimpleNamespace(message=message, finish_reason=finish_reason)
    ])


class TestBoundedEmptyResponseRetry(unittest.TestCase):
    def test_first_empty_second_valid_reuses_same_request(self):
        raw_client = MagicMock()
        raw_client.chat.completions.create.side_effect = [
            _response(""),
            _response("PASS"),
        ]
        messages = [{"role": "user", "content": "review plan"}]
        with patch("src.core.model_provider.OpenAI", return_value=raw_client):
            result = ModelProviderClient(_slot()).complete(messages)
        self.assertEqual(result, "PASS")
        self.assertEqual(raw_client.chat.completions.create.call_count, 2)
        first, second = raw_client.chat.completions.create.call_args_list
        self.assertEqual(first.kwargs, second.kwargs)
        self.assertIs(first.kwargs["messages"], messages)

    def test_two_empty_responses_fail_and_reasoning_is_not_final_content(self):
        raw_client = MagicMock()
        raw_client.chat.completions.create.return_value = _response(
            "", reasoning_content="internal reasoning must not be parsed"
        )
        with patch("src.core.model_provider.OpenAI", return_value=raw_client):
            with self.assertRaises(EmptyModelResponseError):
                ModelProviderClient(_slot()).complete([
                    {"role": "user", "content": "review plan"}
                ])
        self.assertEqual(raw_client.chat.completions.create.call_count, 2)

    def test_generation_limit_keeps_original_single_call_behavior(self):
        raw_client = MagicMock()
        raw_client.chat.completions.create.return_value = _response(
            "", finish_reason="length"
        )
        with patch("src.core.model_provider.OpenAI", return_value=raw_client):
            with self.assertRaises(GenerationLimitExceeded):
                ModelProviderClient(_slot()).complete([])
        raw_client.chat.completions.create.assert_called_once()

    def test_empty_content_never_reaches_review_parser(self):
        parser = Mock()
        provider = Mock(side_effect=EmptyModelResponseError("empty twice"))

        @_guard_node
        def review_plan(state):
            raw = provider()
            return parser(raw)

        with self.assertRaises(EmptyModelResponseError):
            review_plan({"chapter_index": 2})
        parser.assert_not_called()


class TestCheckpointPreservingProviderFailure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = SimpleNamespace(data_dir=Path(self.tmp.name))
        self.policy = NovelRuntimePolicy("agent", "autonomous", 0, 10)

    def _runner(self, execution="autonomous"):
        policy = NovelRuntimePolicy("agent", execution, 0, 10)
        with patch(
            "src.workflows.chapter_runner.get_settings",
            return_value=self.settings,
        ):
            return ChapterWorkflowRunner(
                "smoke_auto", 2, runtime_policy=policy
            )

    @staticmethod
    def _snapshot():
        return SimpleNamespace(
            interrupts=[],
            values={
                "novel_id": "smoke_auto",
                "chapter_index": 2,
                "agent_execution": "autonomous",
                "workflow_status": "PLAN_CREATED",
                "generation_events": [{"event_type": "PLAN_CREATED"}],
            },
            next=["review_plan"],
        )

    def test_both_empty_returns_command_error_and_preserves_review_checkpoint(self):
        for execution in ("autonomous", "supervised"):
            with self.subTest(execution=execution):
                runner = self._runner(execution)
                snapshot = self._snapshot()
                snapshot.values["agent_execution"] = execution
                graph = MagicMock()
                graph.get_state.return_value = snapshot
                graph.invoke.side_effect = EmptyModelResponseError("empty twice")
                checkpointer = MagicMock()
                connection = MagicMock()
                with patch.object(
                    runner, "_open_graph",
                    return_value=(connection, checkpointer, graph),
                ), patch(
                    "src.workflows.chapter_progress.ensure_chapter_can_start"
                ):
                    result = runner.run()

                self.assertEqual(result["workflow_status"], "error")
                self.assertEqual(result["failed_runtime_stage"], "review_plan")
                self.assertEqual(snapshot.next, ["review_plan"])
                self.assertEqual(
                    snapshot.values["generation_events"],
                    [{"event_type": "PLAN_CREATED"}],
                )
                graph.update_state.assert_not_called()
                checkpointer.delete_thread.assert_not_called()
                graph.invoke.assert_called_once_with(None, config=runner.config)

    def test_continue_after_failure_invokes_only_pending_review_plan(self):
        runner = self._runner()
        snapshot = self._snapshot()
        graph = MagicMock()
        graph.get_state.return_value = snapshot
        graph.invoke.side_effect = [
            EmptyModelResponseError("empty twice"),
            {
                **snapshot.values,
                "workflow_status": "PLAN_REVIEWED",
                "review_verdict": "PASS",
            },
        ]
        checkpointer = MagicMock()
        connection = MagicMock()
        with patch.object(
            runner, "_open_graph",
            return_value=(connection, checkpointer, graph),
        ), patch(
            "src.workflows.chapter_progress.ensure_chapter_can_start"
        ):
            first = runner.run()
            second = runner.run()

        self.assertEqual(first["failed_runtime_stage"], "review_plan")
        self.assertEqual(second["workflow_status"], "PLAN_REVIEWED")
        self.assertEqual(
            [call.args[0] for call in graph.invoke.call_args_list],
            [None, None],
        )
        checkpointer.delete_thread.assert_not_called()
        graph.update_state.assert_not_called()

    def test_unknown_and_runtime_exceptions_preserve_pending_node(self):
        for failure in (AttributeError("review bug"), TimeoutError("provider timeout")):
            with self.subTest(failure=type(failure).__name__):
                runner = self._runner()
                snapshot = self._snapshot()
                graph = MagicMock()
                graph.get_state.return_value = snapshot
                graph.invoke.side_effect = failure
                checkpointer = MagicMock()
                with patch.object(
                    runner, "_open_graph",
                    return_value=(MagicMock(), checkpointer, graph),
                ), patch(
                    "src.workflows.chapter_progress.ensure_chapter_can_start"
                ):
                    result = runner.run()

                self.assertEqual(result["workflow_status"], "error")
                self.assertEqual(result["failed_runtime_stage"], "review_plan")
                self.assertIn(type(failure).__name__, result["error"])
                self.assertEqual(snapshot.next, ["review_plan"])
                graph.update_state.assert_not_called()
                checkpointer.delete_thread.assert_not_called()

    def test_keyboard_interrupt_is_not_converted_to_workflow_error(self):
        runner = self._runner()
        snapshot = self._snapshot()
        graph = MagicMock()
        graph.get_state.return_value = snapshot
        graph.invoke.side_effect = KeyboardInterrupt()
        with patch.object(
            runner, "_open_graph",
            return_value=(MagicMock(), MagicMock(), graph),
        ), patch(
            "src.workflows.chapter_progress.ensure_chapter_can_start"
        ):
            with self.assertRaises(KeyboardInterrupt):
                runner.run()

    def test_guard_keeps_explicit_domain_error_terminal(self):
        @_guard_node
        def explicit_failure(state):
            return {"workflow_status": "error", "error": "invalid chapter state"}

        result = explicit_failure({"chapter_index": 2})
        self.assertEqual(result["workflow_status"], "error")
        self.assertEqual(result["error"], "invalid chapter state")

    def test_guard_propagates_unknown_exception(self):
        @_guard_node
        def broken_review(state):

            raise AttributeError("missing verdict field")

        with self.assertRaisesRegex(AttributeError, "missing verdict field"):
            broken_review({"chapter_index": 2})
class _MiniState(TypedDict, total=False):
    completed: list[str]
    review_ok: bool


class TestRealLangGraphCheckpointSemantics(unittest.TestCase):
    def test_failed_review_is_uncommitted_and_continue_skips_upstream(self):
        calls = {"query": 0, "retrieval": 0, "plan": 0, "review": 0}
        fail_review = {"value": True}

        def query(state):
            calls["query"] += 1
            return {"completed": [*state.get("completed", []), "query"]}

        def retrieval(state):
            calls["retrieval"] += 1
            return {"completed": [*state["completed"], "retrieval"]}

        def plan(state):
            calls["plan"] += 1
            return {"completed": [*state["completed"], "plan"]}

        def review(state):
            calls["review"] += 1
            if fail_review["value"]:
                raise AttributeError("review parser bug")
            return {"completed": [*state["completed"], "review"], "review_ok": True}

        builder = StateGraph(_MiniState)
        builder.add_node("query", query)
        builder.add_node("retrieval", retrieval)
        builder.add_node("plan", plan)
        builder.add_node("review_plan", review)
        builder.add_edge(START, "query")
        builder.add_edge("query", "retrieval")
        builder.add_edge("retrieval", "plan")
        builder.add_edge("plan", "review_plan")
        builder.add_edge("review_plan", END)

        connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(connection.close)
        graph = builder.compile(checkpointer=SqliteSaver(connection))
        config = {"configurable": {"thread_id": "chapter:smoke_auto:0002"}}

        with self.assertRaisesRegex(AttributeError, "review parser bug"):
            graph.invoke({"completed": []}, config=config)
        failed = graph.get_state(config)
        self.assertEqual(tuple(failed.next), ("review_plan",))
        self.assertEqual(failed.values["completed"], ["query", "retrieval", "plan"])
        self.assertEqual(calls, {"query": 1, "retrieval": 1, "plan": 1, "review": 1})

        fail_review["value"] = False
        result = graph.invoke(None, config=config)
        self.assertTrue(result["review_ok"])
        self.assertEqual(calls, {"query": 1, "retrieval": 1, "plan": 1, "review": 2})


if __name__ == "__main__":
    unittest.main()
