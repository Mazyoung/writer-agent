import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from langgraph.checkpoint.sqlite import SqliteSaver

from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.workflows.chapter_workflow import (
    build_chapter_workflow, load_chapter_intent, prepare_human_context,
)
from src.workflows.retrieval_service import (
    ChapterRetrievalService, FactRetrievalTrace, RetrievalOutcome,
)


class RetrievalServiceFailureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = get_settings()
        old = settings.data_dir
        settings.data_dir = Path(self.tmp.name)
        self.addCleanup(setattr, settings, "data_dir", old)
        self.settings = settings
        self.fs = FileStore("retrieval", settings.data_dir)

    def service(self):
        service = object.__new__(ChapterRetrievalService)
        service.novel_id = "retrieval"
        service.settings = self.settings
        service.top_k = 5
        service.fs = self.fs
        service.chroma = MagicMock()
        service.author_chroma = MagicMock()
        service.chroma.search.return_value = []
        return service

    def test_runtime_retrieval_exceptions_propagate(self):
        for failure in (TimeoutError("timeout"), RuntimeError("chroma bug")):
            service = self.service()
            service.chroma.search.side_effect = failure
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(type(failure)):
                    service.retrieve(2, "query intent")

    def test_embedding_connection_error_propagates(self):
        self.fs.save_generated_tracking_doc("author_rag", "## Note")
        service = self.service()
        service.author_chroma.ensure_synced.side_effect = ConnectionError("offline")
        with self.assertRaises(ConnectionError):
            service.retrieve(2, "query intent")

    def test_trace_write_is_nonfatal_warning(self):
        service = self.service()
        with patch.object(service, "_save_trace", side_effect=OSError("disk full")):
            outcome = service.retrieve(2, "query intent")
        self.assertTrue(outcome.trace.success)
        self.assertIn("RetrievalTrace persistence failed", outcome.warnings[0])

    def test_trace_warning_does_not_fail_workflow(self):
        outcome = RetrievalOutcome(
            trace=FactRetrievalTrace(query="query", success=True),
            warnings=["RetrievalTrace persistence failed: OSError"],
        )
        with patch(
            "src.agents.author.query_intent_builder.QueryIntentBuilder.build",
            return_value="query",
        ), patch(
            "src.workflows.retrieval_service.ChapterRetrievalService.retrieve",
            return_value=outcome,
        ):
            result = prepare_human_context({
                "novel_id": "retrieval", "chapter_index": 2,
                "chapter_intent": "Inspect door.", "generation_events": [],
            })
        self.assertEqual(result["workflow_status"], "HUMAN_CONTEXT_READY")
        self.assertEqual(result["warnings"], outcome.warnings)


class ProductionRetrievalCheckpointCase(RetrievalServiceFailureCase):
    def _graph_at_intent_boundary(self, mode: str, thread_id: str):
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(connection.close)
        saver = SqliteSaver(connection)
        intent_spy = patch(
            "src.workflows.chapter_workflow.load_chapter_intent",
            wraps=load_chapter_intent,
        ).start()
        self.addCleanup(patch.stopall)
        graph = build_chapter_workflow(checkpointer=saver)
        config = {"configurable": {"thread_id": thread_id}}
        graph.update_state(config, {
            "novel_id": "retrieval",
            "chapter_index": 2,
            "chapter_mode": mode,
            "agent_execution": "supervised",
            "chapter_intent": "Inspect door.",
            "current_state_text": "# Current State",
            "generation_events": [],
            "rag_top_k": 5,
            "workflow_status": "CURRENT_STATE_LOADED",
        }, as_node="load_current_state")
        return graph, config, intent_spy

    @staticmethod
    def _successful_outcome():
        return RetrievalOutcome(
            trace=FactRetrievalTrace(query="query", success=True),
        )

    def test_agent_retry_reexecutes_complete_plan_node_only(self):
        graph, config, intent_spy = self._graph_at_intent_boundary(
            "agent", "agent-retrieval"
        )
        planner = MagicMock()

        def save_plan(*_args, **_kwargs):
            self.fs.save_canonical(
                "outlines", "chapter_plan_ch0002", "# Chapter Plan\n\nPlan."
            )

        planner.plan_chapter.side_effect = save_plan
        with patch(
            "src.agents.author.query_intent_builder.QueryIntentBuilder.build",
            return_value="query",
        ) as query, patch(
            "src.workflows.retrieval_service.ChapterRetrievalService.retrieve",
            side_effect=[TimeoutError("timeout"), self._successful_outcome()],
        ) as retrieval, patch(
            "src.agents.author.chapter_planner.ChapterPlanner",
            return_value=planner,
        ), patch(
            "src.agents.author.plan_reviewer.PlanReviewer.review_plan",
            return_value="## 审阅决策\n- **决策**: PASS\n- **主要问题**: 无",
        ):
            with self.assertRaises(TimeoutError):
                graph.invoke(None, config=config)
            failed = graph.get_state(config)
            self.assertEqual(failed.next, ("plan_chapter",))

            graph.invoke(None, config=config)

        self.assertEqual(intent_spy.call_count, 1)
        self.assertEqual(query.call_count, 2)
        self.assertEqual(retrieval.call_count, 2)
        self.assertEqual(planner.plan_chapter.call_count, 1)

    def test_human_retry_reexecutes_complete_context_node_only(self):
        graph, config, intent_spy = self._graph_at_intent_boundary(
            "human", "human-retrieval"
        )
        with patch(
            "src.agents.author.query_intent_builder.QueryIntentBuilder.build",
            return_value="query",
        ) as query, patch(
            "src.workflows.retrieval_service.ChapterRetrievalService.retrieve",
            side_effect=[TimeoutError("timeout"), self._successful_outcome()],
        ) as retrieval:
            with self.assertRaises(TimeoutError):
                graph.invoke(None, config=config)
            failed = graph.get_state(config)
            self.assertEqual(failed.next, ("prepare_human_context",))

            graph.invoke(None, config=config)

        waiting = graph.get_state(config)
        self.assertEqual(waiting.interrupts[0].value["type"], "human_writing")
        self.assertEqual(intent_spy.call_count, 1)
        self.assertEqual(query.call_count, 2)
        self.assertEqual(retrieval.call_count, 2)
