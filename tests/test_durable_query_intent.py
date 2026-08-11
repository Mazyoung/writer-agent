"""Focused coverage for durable completion, Query Intent, and context policy."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agents.author.chapter_planner import ChapterPlanner
from src.agents.author.query_intent_builder import QueryIntentBuilder
from src.config.settings import ModelSlot, get_settings
from src.core.text_windows import trailing_complete_paragraphs
from src.core.token_guard import estimate_tokens
from src.storage.chapter_completion import (
    is_derived_ready,
    mark_derived_ready,
)
from src.storage.file_store import FileStore
from src.workflows.chapter_runner import ChapterWorkflowRunner
from src.workflows.chapter_workflow import (
    merge_generation_events,
    _route_after_human_chapter,
    _route_after_human_plan,
    await_human_chapter,
    await_human_plan,
    save_chapter_sources,
    sync_chroma,
)
from src.workflows.continuation import NovelContinuationService
from src.workflows.retrieval_service import ChapterRetrievalService
from tests.test_chapter_plan import _make_plan
from tests.test_planning_hierarchy import BOOK_MD, VOLUME1_MD
from tests.test_e07_7 import ATOMIC_MD


class FocusCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = get_settings()
        self.old_data_dir = settings.data_dir
        self.old_interval = settings.auto_savepoint_every
        settings.data_dir = Path(self.temp.name)
        settings.auto_savepoint_every = 0
        self.addCleanup(setattr, settings, "data_dir", self.old_data_dir)
        self.addCleanup(
            setattr, settings, "auto_savepoint_every", self.old_interval
        )
        self.settings = settings
        self.fs = FileStore("focus", settings.data_dir)


class DurableCompletionTests(FocusCase):
    def test_durable_ready_does_not_require_workflow_checkpoint(self):
        self.fs.commit_canonical_chapter(1, "正式正文")
        self.assertFalse(is_derived_ready(self.fs, 1))
        mark_derived_ready(self.fs, 1)
        self.assertTrue(is_derived_ready(self.fs, 1))
        runner = ChapterWorkflowRunner("focus", 1)
        self.assertEqual("DERIVED_READY", runner.get_workflow_status())
        self.assertFalse(runner.checkpoint_path.exists())

    def test_final_marker_is_written_after_chroma_sync(self):
        self.fs.commit_canonical_chapter(1, "正式正文")
        digest = self.fs.save("states", "fact_digest_ch0001", ATOMIC_MD)
        self.assertFalse(is_derived_ready(self.fs, 1))
        with patch(
            "src.storage.atomic_fact_store.AtomicFactStore.index_facts",
            return_value=2,
        ):
            result = sync_chroma({
                "novel_id": "focus", "chapter_index": 1,
                "fact_digest_path": str(
                    digest.relative_to(self.fs.root)
                ).replace("\\", "/"),
                "canonical_source_path": "chapters/chapter_0001.md",
            })
        self.assertEqual("DERIVED_READY", result["workflow_status"])
        report = (
            self.fs.root / "sources" / "chapter_0001" / "chapter_sources.md"
        ).read_text(encoding="utf-8")
        self.assertIn("- DERIVED_READY: 是", report)

    def test_recovery_finalizes_sources_without_duplicate_events(self):
        self.fs.commit_canonical_chapter(1, "正式正文")
        digest = self.fs.save("states", "fact_digest_ch0001", ATOMIC_MD)
        failure = {
            "event_id": "1:DERIVATION_FAILED:rag",
            "event_type": "DERIVATION_FAILED",
            "chapter_index": 1,
            "discriminator": "rag",
            "details": {"stage": "rag", "message": "provider failed"},
        }
        state = {
            "novel_id": "focus",
            "chapter_index": 1,
            "commit_success": True,
            "fact_digest_path": str(
                digest.relative_to(self.fs.root)
            ).replace("\\", "/"),
            "canonical_source_path": "chapters/chapter_0001.md",
            "workflow_status": "DERIVATION_ERROR",
            "failed_derivation_stage": "rag",
            "derivation_error": "provider failed",
            "active_derivation_errors": {"rag": "provider failed"},
            "derived_state_errors": ["provider failed"],
            "warnings": ["provider failed"],
            "generation_events": [failure],
        }
        with patch(
            "src.storage.atomic_fact_store.AtomicFactStore.index_facts",
            return_value=2,
        ):
            first = sync_chroma(state)
            merged = merge_generation_events(
                state["generation_events"], first["generation_events"]
            )
            second_state = {**state, **first, "generation_events": merged}
            second = sync_chroma(second_state)

        report = (
            self.fs.root / "sources" / "chapter_0001" / "chapter_sources.md"
        ).read_text(encoding="utf-8")
        self.assertEqual("DERIVED_READY", first["workflow_status"])
        self.assertEqual("", first["failed_derivation_stage"])
        self.assertEqual("", first["derivation_error"])
        self.assertEqual({}, first["active_derivation_errors"])
        self.assertIn("`DERIVATION_FAILED`", report)
        self.assertIn("`DERIVATION_RECOVERED`", report)
        self.assertEqual(1, report.count("`DERIVATION_FAILED`"))
        self.assertEqual(1, report.count("`DERIVATION_RECOVERED`"))
        self.assertEqual(1, report.count("`DERIVED_READY`"))
        self.assertIn("- DERIVED_READY: 是", report)
        merged_twice = merge_generation_events(
            merged, second["generation_events"]
        )
        self.assertEqual(
            len(merged_twice), len({event["event_id"] for event in merged_twice})
        )
        self.assertTrue(is_derived_ready(self.fs, 1))

    def test_existing_ready_checkpoint_refreshes_stale_sources_idempotently(self):
        self.fs.commit_canonical_chapter(1, "正式正文")
        mark_derived_ready(self.fs, 1)
        runner = ChapterWorkflowRunner("focus", 1)
        runner.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        runner.checkpoint_path.touch()
        source_path = (
            self.fs.root / "sources" / "chapter_0001" / "chapter_sources.md"
        )
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            "Canonical Commit: 是\nDERIVED_READY: 否\n", encoding="utf-8"
        )
        events = [
            {
                "event_id": "1:DERIVATION_FAILED:rag",
                "event_type": "DERIVATION_FAILED",
                "chapter_index": 1,
                "discriminator": "rag",
                "details": {"stage": "rag", "message": "failed"},
            },
            {
                "event_id": "1:DERIVATION_RECOVERED:rag",
                "event_type": "DERIVATION_RECOVERED",
                "chapter_index": 1,
                "discriminator": "rag",
                "details": {"stage": "rag"},
            },
            {
                "event_id": "1:DERIVED_READY",
                "event_type": "DERIVED_READY",
                "chapter_index": 1,
                "details": {},
            },
        ]
        inspection = {"values": {
            "novel_id": "focus", "chapter_index": 1,
            "commit_success": True, "workflow_status": "DERIVED_READY",
            "failed_derivation_stage": "rag",
            "derivation_error": "failed", "generation_events": events,
        }, "next": [], "interrupts": []}
        with patch.object(runner, "inspect", return_value=inspection):
            first = runner.refresh_derived_ready_sources()
            first_content = source_path.read_text(encoding="utf-8")
            second = runner.refresh_derived_ready_sources()

        self.assertTrue(first["refreshed"])
        self.assertFalse(second["refreshed"])
        self.assertEqual(first_content, source_path.read_text(encoding="utf-8"))
        self.assertIn("`DERIVATION_FAILED`", first_content)
        self.assertIn("`DERIVATION_RECOVERED`", first_content)
        self.assertIn("- DERIVED_READY: 是", first_content)


    def test_continue_uses_markers_and_selects_next_chapter(self):
        for chapter in range(1, 81):
            self.fs.commit_canonical_chapter(chapter, f"第{chapter}章")
            mark_derived_ready(self.fs, chapter)
        self.fs.save_tracking_doc("volume_plan", VOLUME1_MD)
        with patch.object(
            ChapterWorkflowRunner,
            "inspect",
            return_value={"values": {}, "next": [], "interrupts": []},
        ):
            decision = NovelContinuationService("focus").route()
        self.assertEqual(
            {"action": "start_chapter", "chapter_index": 81},
            decision,
        )

    def test_all_legal_intermediate_checkpoints_resume(self):
        statuses = (
            "CANONICAL_COMMITTED",
            "SEMANTICS_DERIVED",
            "CURRENT_STATE_PERSISTED",
            "FACT_DIGEST_PERSISTED",
            "VOLUME_PROGRESS_PERSISTED",
            "CHAPTER_SOURCES_PERSISTED",
        )
        for index, status in enumerate(statuses, 1):
            with self.subTest(status=status):
                fs = FileStore(f"recovery-{index}", self.settings.data_dir)
                fs.commit_canonical_chapter(1, "正文")
                runner = ChapterWorkflowRunner(f"recovery-{index}", 1)
                snapshot = SimpleNamespace(
                    values={
                        "workflow_status": status,
                        "commit_success": True,
                    },
                    next=["next_derivation_stage"],
                    interrupts=[],
                )
                graph = MagicMock()
                graph.get_state.return_value = snapshot
                graph.invoke.return_value = {
                    "workflow_status": "DERIVED_READY"
                }
                connection = MagicMock()
                runner._open_graph = MagicMock(
                    return_value=(connection, MagicMock(), graph)
                )
                result = runner.repair_derivation()
                self.assertEqual("DERIVED_READY", result["workflow_status"])
                graph.invoke.assert_called_once_with(None, config=runner.config)
                graph.update_state.assert_not_called()

    def test_missing_recovery_checkpoint_fails_closed(self):
        self.fs.commit_canonical_chapter(1, "正文")
        runner = ChapterWorkflowRunner("focus", 1)
        graph = MagicMock()
        graph.get_state.return_value = SimpleNamespace(
            values={}, next=[], interrupts=[]
        )
        runner._open_graph = MagicMock(
            return_value=(MagicMock(), MagicMock(), graph)
        )
        with self.assertRaisesRegex(ValueError, "fail-closed"):
            runner.repair_derivation()


class SupervisedPassTests(FocusCase):
    @staticmethod
    def _interrupt(payload, response):
        captured = {}

        def fake(value):
            captured.update(value)
            return response

        return captured, fake

    def test_plan_pass_actions_and_feedback_requirement(self):
        state = {
            "novel_id": "focus", "chapter_index": 2,
            "plan_verdict": "PASS",
        }
        captured, fake = self._interrupt(
            {}, {"action": "agent_edit", "feedback": ""}
        )
        with patch("src.workflows.chapter_workflow.interrupt", side_effect=fake):
            rejected = await_human_plan(state)
        self.assertEqual(
            ["approve", "agent_edit", "human_edit", "restart"],
            captured["allowed_actions"],
        )
        self.assertEqual("error", rejected["workflow_status"])

        with patch(
            "src.workflows.chapter_workflow.interrupt",
            return_value={"action": "agent_edit", "feedback": "强化因果"},
        ):
            accepted = await_human_plan(state)
        self.assertEqual("agent_edit", accepted["human_decision"])
        self.assertEqual("强化因果", accepted["human_feedback"])
        self.assertEqual(
            "agent_edit_plan",
            _route_after_human_plan({**state, **accepted}),
        )

    def test_prose_pass_actions_feedback_and_regenerate_route(self):
        state = {
            "novel_id": "focus", "chapter_index": 2,
            "chapter_mode": "agent", "verdict": "PASS",
            "chapter_plan_text": "APPROVED PLAN",
        }
        captured, fake = self._interrupt(
            {}, {"action": "agent_edit", "feedback": ""}
        )
        with patch("src.workflows.chapter_workflow.interrupt", side_effect=fake):
            rejected = await_human_chapter(state)
        self.assertEqual(
            [
                "approve", "agent_edit", "human_edit",
                "regenerate_prose", "restart",
            ],
            captured["allowed_actions"],
        )
        self.assertEqual("error", rejected["workflow_status"])

        with patch(
            "src.workflows.chapter_workflow.interrupt",
            return_value={"action": "agent_edit", "feedback": "减少解释对白"},
        ):
            accepted = await_human_chapter(state)
        self.assertEqual("agent_edit", accepted["human_decision"])
        self.assertEqual("减少解释对白", accepted["human_feedback"])
        self.assertEqual(
            "agent_edit_chapter",
            _route_after_human_chapter({**state, **accepted}),
        )

        with patch(
            "src.workflows.chapter_workflow.interrupt",
            return_value={"action": "regenerate_prose"},
        ):
            regenerated = await_human_chapter(state)
        merged = {**state, **regenerated}
        self.assertEqual("write_draft", _route_after_human_chapter(merged))
        self.assertEqual("APPROVED PLAN", merged["chapter_plan_text"])


class PlannerContextTests(FocusCase):
    def test_full_formal_context_and_query_intent_reach_planner(self):
        world = "世界" * 2500 + "WORLD-TAIL"
        book = BOOK_MD + ("全书" * 2000) + "BOOK-TAIL"
        volume = VOLUME1_MD + ("本卷" * 2000) + "VOLUME-TAIL"
        current = ("当前" * 2500) + "CURRENT-TAIL"
        self.fs.save_canonical("settings", "world_setting", world)
        self.fs.save_tracking_doc("book_plan", book)
        self.fs.save_tracking_doc("volume_plan", volume)
        self.fs.commit_canonical_chapter(
            1, "短段。\n\n" + ("上一章长段落。" * 180)
        )
        planner = ChapterPlanner("focus")
        planner.run = MagicMock(return_value=SimpleNamespace(
            content=_make_plan(2).to_markdown()
        ))
        planner.plan_chapter(
            2,
            rag_evidence="RAG-EVIDENCE",
            query_intent="QUERY-INTENT",
            chapter_intent="HUMAN-INTENT",
            current_state_text=current,
        )
        prompt = planner.run.call_args.kwargs["user_message"]
        for marker in (
            "WORLD-TAIL", "BOOK-TAIL", "VOLUME-TAIL", "CURRENT-TAIL",
            "QUERY-INTENT", "HUMAN-INTENT", "RAG-EVIDENCE",
        ):
            self.assertIn(marker, prompt)

    def test_previous_end_uses_complete_paragraphs_near_1500_chars(self):
        paragraphs = ["甲" * 700, "乙" * 700, "丙" * 700]
        window = trailing_complete_paragraphs("\n\n".join(paragraphs), 1500)
        self.assertEqual("\n\n".join(paragraphs), window)
        self.assertTrue(window.startswith("甲"))
        self.assertTrue(window.endswith("丙" * 10))


class QueryIntentTests(FocusCase):
    @staticmethod
    def _builder(outputs):
        builder = object.__new__(QueryIntentBuilder)
        builder.slot = ModelSlot(
            "query_intent", "deepseek", "key",
            "https://example.test", "model", 100,
        )
        builder._call = MagicMock(side_effect=outputs)
        return builder

    def test_builder_receives_full_inputs_and_human_priority(self):
        builder = self._builder(["精炼检索意图"])
        result = builder.build(
            volume_plan="VOLUME-TAIL",
            recent_chapter_end="RECENT-TAIL",
            current_state="STATE-TAIL",
            human_intent="HUMAN-TAIL",
        )
        self.assertEqual("精炼检索意图", result)
        prompt = builder._call.call_args.args[0]
        for marker in (
            "VOLUME-TAIL", "RECENT-TAIL", "STATE-TAIL", "HUMAN-TAIL",
            "最高优先级",
        ):
            self.assertIn(marker, prompt)

    def test_builder_accepts_under_10000_without_truncation(self):
        output = "检" * 9999
        builder = self._builder([output])
        self.assertEqual(
            output,
            builder.build(
                volume_plan="卷", recent_chapter_end="末",
                current_state="状态",
            ),
        )
        self.assertEqual(1, builder._call.call_count)

    def test_builder_retries_severe_length_once(self):
        builder = self._builder(["长" * 10000, "短意图"])
        result = builder.build(
            volume_plan="卷", recent_chapter_end="末", current_state="状态"
        )
        self.assertEqual("短意图", result)
        self.assertEqual(2, builder._call.call_count)
        self.assertIn("严重超长", builder._call.call_args_list[1].args[0])

    def test_builder_second_severe_length_fails_closed(self):
        builder = self._builder(["长" * 10000, "仍" * 10000])
        with self.assertRaisesRegex(ValueError, "连续生成"):
            builder.build(
                volume_plan="卷", recent_chapter_end="末",
                current_state="状态",
            )

    def test_embedding_search_uses_only_query_intent(self):
        self.fs.save_tracking_doc("volume_plan", VOLUME1_MD)
        self.fs.save_generated_tracking_doc("current_state", "FULL STATE")
        self.fs.commit_canonical_chapter(1, "前章段落")
        service = ChapterRetrievalService("focus")
        service.chroma = MagicMock()
        service.chroma.search.return_value = []
        service.author_chroma = MagicMock()
        outcome = service.retrieve(2, "ONLY QUERY INTENT")
        self.assertTrue(outcome.trace.success)
        self.assertEqual("ONLY QUERY INTENT", outcome.trace.query)
        self.assertEqual(
            "ONLY QUERY INTENT",
            service.chroma.search.call_args.kwargs["query"],
        )

    def test_stylist_receives_complete_chapter_plan(self):
        from src.agents.author.claude_stylist import ClaudeStylist

        stylist = object.__new__(ClaudeStylist)
        stylist.model_slot = self.settings.get_model_slot("write")
        stylist._call_write_slot = MagicMock(return_value="STYLED")
        stylist._prompt = ""
        plan = "PLAN-HEAD\n" + "P" * 4000 + "PLAN-TAIL"
        stylist.edit_chapter("DRAFT", 1, scene_plan_text=plan)
        user_msg = stylist._call_write_slot.call_args.args[0]
        self.assertIn("PLAN-TAIL", user_msg)

    def test_sources_record_actual_query_intent(self):
        result = save_chapter_sources({
            "novel_id": "focus", "chapter_index": 1,
            "chapter_mode": "human", "chapter_intent": "作者要求",
            "query_intent": "ACTUAL EMBEDDING QUERY",
            "retrieved_facts": [], "expanded_sources": [],
        })
        report = (
            self.fs.root / result["chapter_sources_path"]
        ).read_text(encoding="utf-8")
        self.assertIn("## 1. 本章创作意图", report)
        self.assertIn("ACTUAL EMBEDDING QUERY", report)
        self.assertIn("## 2. 历史内容来源", report)


class TokenEstimateTests(unittest.TestCase):
    def test_chinese_is_not_divided_by_four(self):
        text = "中" * 1000
        self.assertGreaterEqual(estimate_tokens(text), 1000)


if __name__ == "__main__":
    unittest.main()
