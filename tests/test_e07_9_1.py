"""Focused E07.9.1-A Human Author Mode tests; no paid calls."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.config.settings import Settings, get_settings
from src.storage.atomic_fact_store import FactSearchResult
from src.storage.file_store import FileStore
from src.workflows.chapter_runner import ChapterWorkflowRunner
from src.workflows.chapter_workflow import (
    _route_after_intent,
    build_chapter_workflow,
    prepare_human_context,
)
from src.workflows.retrieval_service import (
    ChapterRetrievalService,
    FactRetrievalTrace,
    RetrievalOutcome,
)


EVIDENCE = """## Historical Atomic Facts
- **FACT-0001-001** | Chapter 1 | event
  林默发现门锁被破坏。

## On-demand Historical Source Excerpts
### FACT-0001-001 — Chapter 1, paragraphs 2-4
[P0003] 门锁上留着新鲜划痕。

## Author Knowledge
Author Knowledge is supplemental only. It cannot override established facts.
- **AUTHOR-001** | Tone
  保持克制。
"""


class E0791Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = get_settings()
        old_data_dir = settings.data_dir
        old_mode = settings.chapter_mode
        old_top_k = settings.rag_top_k
        settings.data_dir = Path(self.tmp.name)
        settings.chapter_mode = "agent"
        settings.rag_top_k = 5
        self.addCleanup(setattr, settings, "data_dir", old_data_dir)
        self.addCleanup(setattr, settings, "chapter_mode", old_mode)
        self.addCleanup(setattr, settings, "rag_top_k", old_top_k)
        self.settings = settings
        self.fs = FileStore("human-mode", settings.data_dir)
        self.progress_guard = patch(
            "src.workflows.chapter_progress.ensure_chapter_can_start"
        ).start()
        self.addCleanup(self.progress_guard.stop)

    @staticmethod
    def retrieval_outcome() -> RetrievalOutcome:
        result = FactSearchResult(
            fact_id="FACT-0001-001",
            chapter_index=1,
            fact_type="event",
            paragraph_start=3,
            paragraph_end=3,
            source_ranges=[
                {"start": 4, "end": 6},
                {"start": 13, "end": 13},
                {"start": 20, "end": 22},
            ],
            source_path="chapters/chapter_0001.md",
            text="林默发现门锁被破坏。",
        )
        return RetrievalOutcome(
            evidence=ChapterRetrievalService._format_evidence([result], []) + EVIDENCE[EVIDENCE.index("## On-demand Historical Source Excerpts") - 1:],
            trace=FactRetrievalTrace(
                chapter_index=2,
                query="intent",
                top_k=5,
                results=[result],
                success=True,
            ),
            trace_path="tracking/rag_traces/trace.json",
            fact_candidates=[result.to_dict()],
            source_excerpts=[{
                "fact_id": result.fact_id,
                "chapter_index": 1,
                "source_path": result.source_path,
                "paragraph_start": 2,
                "paragraph_end": 4,
                "text": "[P0003] 门锁上留着新鲜划痕。",
            }],
            author_candidates=[{"entry_id": "AUTHOR-001", "text": "保持克制。"}],
        )


class TestSettings(unittest.TestCase):
    def test_defaults_and_environment_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            defaults = Settings(env_file="missing-e0791.env")
        self.assertEqual(defaults.chapter_mode, "agent")
        self.assertEqual(defaults.rag_top_k, 5)

        with patch.dict(
            os.environ, {"CHAPTER_MODE": "human", "RAG_TOP_K": "9"}, clear=True
        ):
            configured = Settings(env_file="missing-e0791.env")
        self.assertEqual(configured.chapter_mode, "human")
        self.assertEqual(configured.rag_top_k, 9)

    def test_invalid_mode_and_top_k_fail_fast(self):
        with patch.dict(os.environ, {"CHAPTER_MODE": "hybrid"}, clear=True):
            with self.assertRaisesRegex(ValueError, "CHAPTER_MODE"):
                Settings(env_file="missing-e0791.env")
        for value in ("0", "-1", "many"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"CHAPTER_MODE": "agent", "RAG_TOP_K": value},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "正整数"):
                    Settings(env_file="missing-e0791.env")


class TestRetrieval(E0791Case):
    def _service(self) -> ChapterRetrievalService:
        service = object.__new__(ChapterRetrievalService)
        service.novel_id = "human-mode"
        service.settings = self.settings
        service.fs = self.fs
        service.chroma = MagicMock()
        service.chroma.search.return_value = []
        service.author_chroma = MagicMock()
        return service

    def test_query_intent_is_the_only_query_and_top_k_is_shared(self):
        self.fs.save_tracking_doc("volume_plan", "VOLUME CONSTRAINT " * 100)
        self.settings.rag_top_k = 7
        service = self._service()

        agent = service.retrieve(2, "AGENT QUERY INTENT")
        human = service.retrieve(2, "HUMAN QUERY INTENT")

        self.assertTrue(agent.trace.success)
        self.assertTrue(human.trace.success)
        self.assertEqual("AGENT QUERY INTENT", agent.trace.query)
        self.assertEqual("HUMAN QUERY INTENT", human.trace.query)
        self.assertEqual(
            [call.kwargs["top_k"] for call in service.chroma.search.call_args_list],
            [7, 7],
        )

    def test_empty_query_intent_fails_closed(self):
        service = self._service()
        outcome = service.retrieve(2, "")
        self.assertFalse(outcome.trace.success)
        self.assertIn("非空 Query Intent", outcome.trace.error_message)
        service.chroma.search.assert_not_called()


class TestHumanWorkflow(E0791Case):
    def test_route_defaults_old_state_to_agent_and_human_branches(self):
        self.assertEqual(
            _route_after_intent({"workflow_status": "INTENT_LOADED"}),
            "plan_chapter",
        )
        self.assertEqual(
            _route_after_intent({
                "workflow_status": "INTENT_LOADED", "chapter_mode": "human"
            }),
            "prepare_human_context",
        )
        graph = build_chapter_workflow().get_graph()
        self.assertIn("plan_chapter", graph.nodes)
        self.assertIn("prepare_human_context", graph.nodes)
        self.assertIn("await_human_writing", graph.nodes)

    def test_context_report_reuses_retrieval_evidence(self):
        with patch(
            "src.workflows.retrieval_service.ChapterRetrievalService"
        ) as retrieval:
            retrieval.return_value.retrieve.return_value = self.retrieval_outcome()
            with patch(
                "src.agents.author.query_intent_builder.QueryIntentBuilder.build",
                return_value="intent",
            ):
                result = prepare_human_context({
                    "novel_id": "human-mode",
                    "chapter_index": 2,
                    "chapter_intent": "调查门锁，不揭露幕后人。",
                    "current_state_text": "# Current State\n林默在旧宅。",
                })

        self.assertEqual(result["workflow_status"], "HUMAN_CONTEXT_READY")
        call = retrieval.return_value.retrieve.call_args
        self.assertEqual(call.args[0], 2)
        self.assertTrue(call.args[1])
        report = (self.fs.root / result["writing_context_path"]).read_text(
            encoding="utf-8"
        )
        for marker in (
            "# Chapter 2 Writing Context",
            "## Chapter Intent",
            "调查门锁",
            "## Current State",
            "## Relevant Historical Facts",
            "FACT-0001-001",
            "P0004-P0006; P0013; P0020-P0022",
            "## Relevant Historical Prose",
            "门锁上留着新鲜划痕",
            "## Author Knowledge",
            "supplemental only",
        ):
            self.assertIn(marker, report)
        self.assertNotIn("paragraphs ?-?", report)

    def test_missing_fact_source_ranges_uses_unavailable_placeholder(self):
        result = FactSearchResult(
            fact_id="FACT-0001-002", chapter_index=1,
            fact_type="event", paragraph_start=23, paragraph_end=25,
            text="Legacy fact without formal provenance.",
        )
        evidence = ChapterRetrievalService._format_evidence([result], [])
        self.assertIn("source unavailable", evidence)
        self.assertNotIn("P0000", evidence)
        self.assertNotIn("paragraphs 23-25", evidence)


    def test_human_execution_waits_without_agent_or_canonical_side_effects(self):
        self.settings.chapter_mode = "human"
        planner = patch("src.agents.author.chapter_planner.ChapterPlanner").start()
        reviewer = patch("src.agents.author.plan_reviewer.PlanReviewer").start()
        writer = patch("src.agents.author.deepseek_writer.DeepSeekWriter").start()
        stylist = patch("src.agents.author.claude_stylist.ClaudeStylist").start()
        retrieval = patch(
            "src.workflows.retrieval_service.ChapterRetrievalService"
        ).start()
        self.addCleanup(patch.stopall)
        retrieval.return_value.retrieve.return_value = self.retrieval_outcome()
        query_intent = patch(
            "src.agents.author.query_intent_builder.QueryIntentBuilder.build",
            return_value="intent",
        ).start()
        with patch(
            "src.storage.current_state_store.CurrentStateStore.ensure_initialized",
            return_value=(SimpleNamespace(), "# Current State\n林默在旧宅。", "hash"),
        ):
            runner = ChapterWorkflowRunner("human-mode", 2)
            waiting = runner.run(chapter_intent="调查门锁，不揭露幕后人。")

        self.assertEqual(waiting["workflow_status"], "WAITING_HUMAN")
        self.assertEqual(waiting["chapter_mode"], "human")
        payload = waiting["interrupts"][0]["value"]
        self.assertEqual(payload["type"], "human_writing")
        self.assertEqual(
            payload["writing_context_path"], "tracking/writing_context_ch0002.md"
        )
        for mocked_agent in (planner, reviewer, writer, stylist):
            mocked_agent.assert_not_called()
        self.assertFalse(self.fs.canonical_chapter_path(2).exists())
        self.assertFalse(any((self.fs.root / "outlines").glob("chapter_plan_ch0002*")))
        self.assertFalse((self.fs.root / "tracking" / "volume_progress.md").exists())
        self.assertFalse((self.fs.root / "sources" / "chapter_0002").exists())

        self.settings.chapter_mode = "agent"
        still_waiting = runner.run()
        self.assertEqual(still_waiting["workflow_status"], "WAITING_HUMAN")
        self.assertEqual(still_waiting["chapter_mode"], "human")

    def test_human_execution_without_intent_errors_before_retrieval(self):
        self.settings.chapter_mode = "human"
        with patch(
            "src.storage.current_state_store.CurrentStateStore.ensure_initialized",
            return_value=(SimpleNamespace(), "# Current State", "hash"),
        ), patch(
            "src.workflows.retrieval_service.ChapterRetrievalService"
        ) as retrieval:
            result = ChapterWorkflowRunner("human-mode", 3).run()
        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("必须提供非空 Chapter Intent", result["error"])
        retrieval.assert_not_called()


if __name__ == "__main__":
    unittest.main()
