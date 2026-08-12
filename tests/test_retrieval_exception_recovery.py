import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.workflows.chapter_workflow import prepare_human_context
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


class RetrievalState(TypedDict, total=False):
    query: str
    retrieved: bool


class TestRetrievalCheckpoint(unittest.TestCase):
    def test_continue_retries_retrieval_not_durable_query_intent(self):
        calls = {"query": 0, "retrieval": 0}
        fail = {"value": True}
        def query(state):
            calls["query"] += 1
            return {"query": "durable"}
        def retrieval(state):
            calls["retrieval"] += 1
            if fail["value"]:
                raise TimeoutError("timeout")
            return {"retrieved": True}
        builder = StateGraph(RetrievalState)
        builder.add_node("query_intent", query)
        builder.add_node("retrieval", retrieval)
        builder.add_edge(START, "query_intent")
        builder.add_edge("query_intent", "retrieval")
