from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main as cli
from src.config.runtime_policy import (
    NovelRuntimePolicy,
    create_novel_runtime_env,
    load_novel_runtime_policy,
)
from src.workflows.chapter_runner import ChapterWorkflowRunner
from src.workflows.chapter_workflow import style_edit


class NovelRuntimePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name)
        self.settings = SimpleNamespace(
            data_dir=self.data_dir,
            chapter_mode="agent",
            agent_execution="supervised",
            auto_savepoint_every=0,
            rag_top_k=10,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _write_env(self, novel_id: str, content: str) -> None:
        root = self.data_dir / "novels" / novel_id
        root.mkdir(parents=True)
        (root / ".env").write_text(content, encoding="utf-8")

    def test_two_novels_are_isolated_without_environment_mutation(self):
        self._write_env(
            "auto",
            "CHAPTER_MODE=agent\nAGENT_EXECUTION=autonomous\nRAG_TOP_K=7\n",
        )
        self._write_env("human", "CHAPTER_MODE=human\n")
        before = dict(os.environ)

        automatic = load_novel_runtime_policy("auto", self.settings)
        human = load_novel_runtime_policy("human", self.settings)

        self.assertEqual(("agent", "autonomous", 7), (
            automatic.chapter_mode,
            automatic.agent_execution,
            automatic.rag_top_k,
        ))
        self.assertEqual(("human", "supervised", 10), (
            human.chapter_mode,
            human.agent_execution,
            human.rag_top_k,
        ))
        self.assertEqual(before, dict(os.environ))

    def test_new_novel_env_materializes_effective_root_policy(self):
        path = create_novel_runtime_env("new", self.settings)
        self.assertEqual(
            NovelRuntimePolicy("agent", "supervised", 0, 10),
            load_novel_runtime_policy("new", self.settings),
        )
        self.assertIn("RAG_TOP_K=10", path.read_text(encoding="utf-8"))

    def test_new_chapter_freezes_command_policy_into_initial_state(self):
        policy = NovelRuntimePolicy("agent", "autonomous", 3, 8)
        settings = SimpleNamespace(data_dir=self.data_dir)
        snapshot = SimpleNamespace(interrupts=[], values={}, next=[])
        graph = MagicMock()
        graph.get_state.side_effect = [snapshot, snapshot]
        graph.invoke.side_effect = lambda state, config: state
        connection = MagicMock()
        runner = None
        with patch(
            "src.workflows.chapter_runner.get_settings", return_value=settings
        ):
            runner = ChapterWorkflowRunner(
                "fresh", 1, runtime_policy=policy
            )
        with patch.object(
            runner, "_open_graph", return_value=(connection, MagicMock(), graph)
        ), patch(
            "src.workflows.chapter_progress.ensure_chapter_can_start"
        ):
            result = runner.run()

        self.assertEqual("autonomous", result["agent_execution"])
        self.assertEqual(8, result["rag_top_k"])
        self.assertEqual(3, result["auto_savepoint_every"])

    def test_existing_checkpoint_keeps_frozen_mode(self):
        latest = NovelRuntimePolicy("agent", "autonomous", 0, 10)
        settings = SimpleNamespace(data_dir=self.data_dir)
        frozen = {
            "chapter_mode": "agent",
            "agent_execution": "supervised",
            "workflow_status": "PLAN_APPROVED",
        }
        snapshot = SimpleNamespace(interrupts=[], values=frozen, next=["write_draft"])
        graph = MagicMock()
        graph.get_state.side_effect = [snapshot, snapshot]
        graph.invoke.return_value = frozen
        connection = MagicMock()
        with patch(
            "src.workflows.chapter_runner.get_settings", return_value=settings
        ):
            runner = ChapterWorkflowRunner(
                "frozen", 4, runtime_policy=latest
            )
        with patch.object(
            runner, "_open_graph", return_value=(connection, MagicMock(), graph)
        ), patch(
            "src.workflows.chapter_progress.ensure_chapter_can_start"
        ):
            result = runner.run()

        self.assertEqual("supervised", result["agent_execution"])
        self.assertIsNone(graph.invoke.call_args.args[0])


class StageTimingTests(unittest.TestCase):
    def test_style_node_records_monotonic_duration_and_summary_uses_only_nodes(self):
        stylist = MagicMock()
        stylist.edit_chapter.return_value = "润色后正文"
        with patch(
            "src.agents.author.claude_stylist.ClaudeStylist",
            return_value=stylist,
        ), patch(
            "src.utils.live_timer.time.perf_counter",
            side_effect=[10.0, 10.25],
        ):
            result = style_edit({
                "novel_id": "timed",
                "chapter_index": 3,
                "draft_text": "正文",
                "chapter_plan_text": "规划",
            })

        event = result["generation_events"][0]
        self.assertEqual("STYLE_COMPLETED", event["event_type"])
        self.assertGreaterEqual(event["duration_ms"], 0)
        self.assertEqual(250.0, event["duration_ms"])

        output = io.StringIO()
        with redirect_stdout(output):
            cli._print_timing_summary({"generation_events": [event]})
        rendered = output.getvalue()
        self.assertIn("不含人工等待", rendered)
        self.assertIn("0.2s", rendered)


if __name__ == "__main__":
    unittest.main()
