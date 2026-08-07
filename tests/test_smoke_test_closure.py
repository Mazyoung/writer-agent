"""Focused no-paid-call coverage for the real Smoke Test closure."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main as cli
from src.agents.author.chapter_planner import ChapterPlanner
from src.config.settings import ModelSlot, get_settings
from src.core.token_guard import guard_planning_context
from src.planning.novel_lifecycle import NovelLifecycleService
from src.storage.file_store import FileStore
from src.workflows.chapter_progress import ensure_chapter_can_start
from src.workflows.chapter_runner import ChapterWorkflowRunner
from src.workflows.chapter_workflow import (
    _route_after_chapter_decision,
    _route_after_plan_decision,
    agent_edit_plan,
)
from src.workflows.continuation import NovelContinuationService
from tests.test_e07_9 import VOLUME_DRAFT


BOOK_PLAN = """# 全书规划：《测试》
- **版本**: v1
## 核心目标
目标
## 核心矛盾
矛盾
## 主角长期成长方向
成长
## 战略约束
约束
## 核心梗概
梗概
## 全书主题
主题
## 结局方向
结局
## 卷框架
### 第1卷：开端
- **核心冲突**: 冲突
- **主角弧光**: 弧光
- **关键角色**: 甲
- **章数预估**: 10
## 全局伏笔追踪
| 伏笔描述 | 埋伏章节 | 预计回收卷 | 状态 | 回收章节 |
|---|---|---|---|---|
"""


class SmokeClosureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = get_settings()
        self.old_data_dir = settings.data_dir
        self.old_execution = settings.agent_execution
        self.old_mode = settings.chapter_mode
        settings.data_dir = Path(self.tmp.name)
        settings.chapter_mode = "agent"
        settings.agent_execution = "supervised"
        self.addCleanup(setattr, settings, "data_dir", self.old_data_dir)
        self.addCleanup(setattr, settings, "agent_execution", self.old_execution)
        self.addCleanup(setattr, settings, "chapter_mode", self.old_mode)
        self.settings = settings


class ProposalAndPlanningContextTests(SmokeClosureCase):
    def test_proposal_prompt_forbids_early_chapter_outlines(self):
        service = object.__new__(NovelLifecycleService)
        service.novel_id = "smoke"
        service.world_builder = MagicMock()
        service.world_builder.run.return_value = SimpleNamespace(content="PROPOSAL")

        service.generate_proposal("HINT")

        prompt = service.world_builder.run.call_args.kwargs["user_message"]
        self.assertIn("不得输出或承诺", prompt)
        self.assertIn("前 N 章", prompt)
        self.assertIn("世界观、全书规划和第一卷规划", prompt)

    def _lifecycle(self):
        service = object.__new__(NovelLifecycleService)
        service.novel_id = "smoke"
        service.file_store = FileStore("smoke", self.settings.data_dir)
        service.world_builder = MagicMock()
        service.plot_designer = MagicMock()
        return service

    def test_initialize_resumes_from_existing_formal_artifacts(self):
        cases = (
            (False, False, False, 1, 2),
            (True, False, False, 0, 2),
            (True, True, False, 0, 1),
            (True, True, True, 0, 0),
        )
        for world_exists, book_exists, volume_exists, world_calls, plot_calls in cases:
            with self.subTest(
                world=world_exists, book=book_exists, volume=volume_exists
            ):
                service = self._lifecycle()
                if world_exists:
                    service.file_store.save_canonical(
                        "settings", "world_setting", "WORLD-DISK-RAW"
                    )
                if book_exists:
                    service.file_store.save_tracking_doc(
                        "book_plan", "BOOK-DISK-RAW\n自定义完整正文"
                    )
                if volume_exists:
                    service.file_store.save_tracking_doc(
                        "volume_plan", VOLUME_DRAFT.replace("第2卷", "第1卷")
                    )
                service.world_builder.run.return_value = SimpleNamespace(
                    content="WORLD-GENERATED"
                )
                responses = []
                if not book_exists:
                    responses.append(SimpleNamespace(content=BOOK_PLAN))
                if not volume_exists:
                    responses.append(SimpleNamespace(
                        content=VOLUME_DRAFT.replace("第2卷", "第1卷")
                    ))
                service.plot_designer.run.side_effect = responses

                with patch(
                    "src.storage.current_state_store.CurrentStateStore.ensure_initialized",
                    return_value=(SimpleNamespace(), "# Current State", "hash"),
                ) as ensure:
                    result = service.initialize_novel("PROPOSAL")

                self.assertEqual(service.world_builder.run.call_count, world_calls)
                self.assertEqual(service.plot_designer.run.call_count, plot_calls)
                ensure.assert_called_once_with()
                if book_exists and not volume_exists:
                    volume_prompt = service.plot_designer.run.call_args.kwargs[
                        "user_message"
                    ]
                    self.assertIn("BOOK-DISK-RAW\n自定义完整正文", volume_prompt)
                self.assertTrue(result["world_setting"].strip())
                self.assertTrue(result["book_plan"].strip())
                self.assertTrue(result["volume_plan"].strip())

                for child in service.file_store.root.iterdir():
                    if child.is_dir():
                        import shutil
                        shutil.rmtree(child)
                    else:
                        child.unlink()

    def test_noncanonical_book_markdown_reaches_volume_planner_raw(self):
        service = self._lifecycle()
        service.file_store.save_canonical(
            "settings", "world_setting", "WORLD-DISK"
        )
        raw_book = "任意非 canonical Book Plan 标题\n\n完整作者正文尾部"
        service.file_store.save_tracking_doc("book_plan", raw_book)
        service.plot_designer.run.return_value = SimpleNamespace(
            content=VOLUME_DRAFT.replace("第2卷", "第1卷")
        )

        service.initialize_novel("PROPOSAL")

        self.assertIn(
            raw_book,
            service.plot_designer.run.call_args.kwargs["user_message"],
        )

    def test_confirm_reads_current_proposal_only(self):
        novel = self.settings.data_dir / "novels" / "smoke"
        novel.mkdir(parents=True)
        (novel / "proposal.md").write_text("CURRENT PROPOSAL", encoding="utf-8")
        lifecycle = MagicMock()
        lifecycle.file_store.root = novel
        args = SimpleNamespace(name="smoke", confirm=True)
        with patch.object(cli, "_validate_existing_embedding", return_value=True), patch.object(
            cli, "NovelLifecycleService", return_value=lifecycle
        ):
            cli.cmd_init(args)
        lifecycle.initialize_novel.assert_called_once_with("CURRENT PROPOSAL")

    def test_full_world_and_book_plan_tails_reach_downstream_prompts(self):
        service = object.__new__(NovelLifecycleService)
        service.novel_id = "smoke"
        service.file_store = FileStore("smoke", self.settings.data_dir)
        world_tail = "WORLD-TAIL-MARKER"
        world = "W" * 7000 + world_tail
        book_tail = "BOOK-TAIL-MARKER"
        book = BOOK_PLAN + ("B" * 7000) + book_tail
        service.world_builder = MagicMock()
        service.world_builder.run.return_value = SimpleNamespace(content=world)
        service.plot_designer = MagicMock()
        service.plot_designer.run.side_effect = [
            SimpleNamespace(content=book),
            SimpleNamespace(content=VOLUME_DRAFT.replace("第2卷", "第1卷")),
        ]
        service.initialize_novel("PROPOSAL")
        book_prompt = service.plot_designer.run.call_args_list[0].kwargs["user_message"]
        volume_prompt = service.plot_designer.run.call_args_list[1].kwargs["user_message"]
        self.assertIn(world_tail, book_prompt)
        self.assertIn(world_tail, volume_prompt)
        self.assertIn(book_tail, volume_prompt)

    def test_token_guard_blocks_before_llm_and_lists_documents(self):
        slot = ModelSlot(
            name="architect", provider="deepseek", api_key="x",
            base_url="https://api.deepseek.com", model="m", max_tokens=32768,
        )
        with self.assertRaisesRegex(ValueError, "不会自动截断") as raised:
            guard_planning_context(slot, {
                "proposal.md": "甲" * 300_000,
                "world_setting.md": "乙" * 10,
            })
        self.assertIn("proposal.md", str(raised.exception))
        self.assertIn("world_setting.md", str(raised.exception))

    def test_draft_volume_becomes_active_when_planning_starts(self):
        fs = FileStore("smoke", self.settings.data_dir)
        fs.save_tracking_doc("book_plan", BOOK_PLAN)
        fs.save_tracking_doc(
            "volume_plan", VOLUME_DRAFT.replace("第2卷", "第1卷")
        )
        planner = object.__new__(ChapterPlanner)
        planner.novel_id = "smoke"
        planner.fs = fs
        _book, volume = planner._require_long_term_plans()
        self.assertIn("ACTIVE", volume)
        self.assertIn("ACTIVE", fs.load_tracking_doc("volume_plan"))


class ExecutionPolicyTests(SmokeClosureCase):
    def test_supervised_has_plan_checkpoint_even_on_pass(self):
        self.assertEqual(
            "await_human_plan",
            _route_after_plan_decision({
                "plan_verdict": "PASS",
                "agent_execution": "supervised",
            }),
        )

    def test_autonomous_pass_skips_human_checkpoints(self):
        self.assertEqual(
            "write_draft",
            _route_after_plan_decision({
                "plan_verdict": "PASS",
                "agent_execution": "autonomous",
            }),
        )
        self.assertEqual(
            "commit_canonical_prose",
            _route_after_chapter_decision({
                "verdict": "PASS", "chapter_mode": "agent",
                "agent_execution": "autonomous",
            }),
        )

    def test_autonomous_review_failures_retry_finitely(self):
        self.assertEqual(
            "agent_edit_plan",
            _route_after_plan_decision({
                "plan_verdict": "NEEDS_REVISION",
                "agent_execution": "autonomous",
                "plan_revision_count": 1,
            }),
        )
        self.assertEqual(
            "await_human_plan",
            _route_after_plan_decision({
                "plan_verdict": "NEEDS_REVISION",
                "agent_execution": "autonomous",
                "plan_revision_count": 2,
            }),
        )
        self.assertEqual(
            "agent_edit_chapter",
            _route_after_chapter_decision({
                "verdict": "NEEDS_REVISION", "chapter_mode": "agent",
                "agent_execution": "autonomous", "review_round": 2,
            }),
        )
        self.assertEqual(
            "await_human_chapter",
            _route_after_chapter_decision({
                "verdict": "NEEDS_REVISION", "chapter_mode": "agent",
                "agent_execution": "autonomous", "review_round": 3,
            }),
        )

    def test_plan_agent_edit_receives_review_issues_and_context(self):
        planner = MagicMock()
        planner.revise_plan.return_value = "REVISED"
        with patch(
            "src.agents.author.chapter_planner.ChapterPlanner",
            return_value=planner,
        ):
            result = agent_edit_plan({
                "novel_id": "smoke", "chapter_index": 3,
                "chapter_plan_text": "CURRENT",
                "plan_t1_issues": ["硬问题"],
                "plan_review_reasons": ["因果问题"],
                "historical_evidence": "CONTEXT",
                "chapter_intent": "INTENT",
            })
        kwargs = planner.revise_plan.call_args.kwargs
        self.assertEqual(["硬问题", "因果问题"], kwargs["review_issues"])
        self.assertIn("## World Setting", kwargs["planning_context"])
        self.assertIn("## Book Plan", kwargs["planning_context"])
        self.assertIn("## Volume Plan", kwargs["planning_context"])
        self.assertIn("## Current State", kwargs["planning_context"])
        self.assertIn("CONTEXT", kwargs["planning_context"])
        self.assertEqual("REVISED", result["chapter_plan_text"])


class ProgressAndContinuationTests(SmokeClosureCase):
    def test_previous_derivation_blocks_next_chapter(self):
        fs = FileStore("smoke", self.settings.data_dir)
        fs.commit_canonical_chapter(1, "正文")
        with patch(
            "src.workflows.chapter_progress.is_derived_ready",
            return_value=False,
        ):
            with self.assertRaisesRegex(ValueError, "repair-derivation"):
                ensure_chapter_can_start("smoke", 2)

    def test_completed_canonical_reports_next_chapter(self):
        fs = FileStore("smoke", self.settings.data_dir)
        fs.commit_canonical_chapter(1, "正文")
        with patch(
            "src.workflows.chapter_progress.is_derived_ready",
            return_value=True,
        ):
            with self.assertRaisesRegex(ValueError, "下一章：第 2 章"):
                ensure_chapter_can_start("smoke", 1)

    def test_continue_waiting_human_never_advances(self):
        service = NovelContinuationService("smoke")
        service.fs.save_tracking_doc("volume_plan", VOLUME_DRAFT)
        inspection = {
            "values": {"workflow_status": "WAITING_HUMAN"},
            "next": [],
            "interrupts": [{"id": "i", "value": {
                "type": "plan_review", "reasons": ["具体问题"],
                "allowed_actions": ["agent_edit", "human_edit", "restart"],
            }}],
        }
        with patch.object(
            ChapterWorkflowRunner, "inspect", return_value=inspection
        ), patch.object(ChapterWorkflowRunner, "run") as run:
            result = service.continue_once()
        self.assertEqual("WAITING_HUMAN", result["workflow_status"])
        run.assert_not_called()

    def test_continue_resumes_checkpoint_without_restart(self):
        service = NovelContinuationService("smoke")
        service.fs.save_tracking_doc("volume_plan", VOLUME_DRAFT)
        inspection = {
            "values": {"workflow_status": "WRITING"},
            "next": ["style_chapter"],
            "interrupts": [],
        }
        with patch.object(
            ChapterWorkflowRunner, "inspect", return_value=inspection
        ), patch.object(
            ChapterWorkflowRunner, "run",
            return_value={"workflow_status": "DERIVED_READY", "chapter_index": 1},
        ) as run, patch.object(ChapterWorkflowRunner, "restart") as restart:
            result = service.continue_once()
        self.assertEqual("DERIVED_READY", result["workflow_status"])
        run.assert_called_once()
        restart.assert_not_called()

    def test_continue_reuses_derivation_repair(self):
        service = NovelContinuationService("smoke")
        service.fs.commit_canonical_chapter(1, "正文")
        inspections = [{
            "values": {"workflow_status": "DERIVATION_ERROR"},
            "next": [], "interrupts": [],
        }]
        with patch.object(
            ChapterWorkflowRunner, "inspect", side_effect=inspections
        ), patch.object(
            ChapterWorkflowRunner, "repair_derivation",
            return_value={"workflow_status": "DERIVED_READY", "chapter_index": 1},
        ) as repair:
            result = service.continue_once()
        self.assertEqual("DERIVED_READY", result["workflow_status"])
        repair.assert_called_once()

    def test_continue_after_ready_selects_next_without_overwrite(self):
        service = NovelContinuationService("smoke")
        service.fs.commit_canonical_chapter(1, "正文")
        service.fs.save_tracking_doc("volume_plan", VOLUME_DRAFT)
        inspections = [
            {"values": {"workflow_status": "DERIVED_READY"},
             "next": [], "interrupts": []},
            {"values": {}, "next": [], "interrupts": []},
        ]
        with patch(
            "src.workflows.continuation.is_derived_ready",
            return_value=True,
        ), patch.object(ChapterWorkflowRunner, "inspect", side_effect=inspections):
            decision = service.route()
        self.assertEqual(
            {"action": "start_chapter", "chapter_index": 2}, decision
        )

    def test_continue_stops_at_volume_and_human_boundaries(self):
        service = NovelContinuationService("smoke")
        completed = VOLUME_DRAFT.replace("DRAFT", "COMPLETED")
        service.fs.save_tracking_doc("volume_plan", completed)
        with patch.object(
            ChapterWorkflowRunner, "inspect",
            return_value={"values": {}, "next": [], "interrupts": []},
        ), patch.object(ChapterWorkflowRunner, "run") as run:
            result = service.continue_once()
        self.assertEqual("BLOCKED", result["workflow_status"])
        self.assertIn("下一卷", result["error"])
        run.assert_not_called()

        service.fs.save_tracking_doc("volume_plan", VOLUME_DRAFT)
        self.settings.chapter_mode = "human"
        with patch.object(
            ChapterWorkflowRunner, "inspect",
            return_value={"values": {}, "next": [], "interrupts": []},
        ), patch.object(ChapterWorkflowRunner, "run") as run:
            result = service.continue_once()
        self.assertEqual("BLOCKED", result["workflow_status"])
        self.assertIn("Chapter Intent", result["error"])
        run.assert_not_called()

    def test_run_reuses_continue_router(self):
        self.settings.agent_execution = "autonomous"
        service = NovelContinuationService("smoke")
        results = [
            {"workflow_status": "DERIVED_READY", "chapter_index": 1},
            {"workflow_status": "WAITING_HUMAN", "chapter_index": 2},
        ]
        with patch.object(
            service, "_latest_canonical", side_effect=[0, 1]
        ), patch.object(
            service, "continue_once", side_effect=results
        ) as continue_once:
            result = service.run_to_chapter(3)
        self.assertEqual("WAITING_HUMAN", result["workflow_status"])
        self.assertEqual(2, continue_once.call_count)

    def test_restart_removes_all_precanonical_candidates_but_keeps_intent(self):
        runner = ChapterWorkflowRunner("smoke", 2)
        root = runner.file_store.root
        intent = root / "briefs" / "chapter_intent_ch0002.md"
        intent.parent.mkdir(parents=True, exist_ok=True)
        intent.write_text("保留我", encoding="utf-8")
        candidates = [
            root / "chapters" / "chapter_0002_draft_1.md",
            root / "chapters" / "scene_ch0002_s01.md",
            root / "outlines" / "chapter_plan_ch0002_1.md",
            root / "tracking" / "writing_context_ch0002.md",
            root / "tracking" / "rag_traces" / "retrieval_trace_ch0002_x.json",
        ]
        for path in candidates:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("candidate", encoding="utf-8")
        result = runner.restart()
        self.assertEqual("RESTARTED", result["workflow_status"])
        self.assertTrue(intent.exists())
        self.assertTrue(all(not path.exists() for path in candidates))

    def test_review_cli_prints_all_issues_and_actions(self):
        result = {
            "workflow_status": "WAITING_HUMAN",
            "interrupts": [{"value": {
                "type": "chapter_review", "verdict": "NEEDS_REVISION",
                "t1_issues": ["问题一"], "reasons": ["问题二"],
                "allowed_actions": [
                    "agent_edit", "human_edit", "regenerate_prose", "restart"
                ],
            }}],
        }
        output = io.StringIO()
        with redirect_stdout(output):
            cli._print_chapter_result("smoke", 3, result)
        rendered = output.getvalue()
        for marker in ("问题一", "问题二", "agent edit", "regenerate prose", "restart"):
            self.assertIn(marker, rendered)


if __name__ == "__main__":
    unittest.main()
