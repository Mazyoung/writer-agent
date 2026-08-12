"""E07.9.1-B Human Author Mode 闭环测试；全部为 mocked/no-paid-call。"""

import contextlib
import inspect
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main as cli
from src.agents.state_manager.state_manager import StateManager
from src.config.settings import get_settings
from src.storage.atomic_fact_store import AtomicFactStore
from src.storage.document_formats import AtomicFact, ChapterPlan, FactDigest
from src.storage.file_store import FileStore
from src.workflows.chapter_runner import ChapterWorkflowRunner
from src.workflows.chapter_workflow import (
    _is_canonical_authorized,
    parse_consistency_decision,
    save_chapter_sources,
)
from src.workflows.retrieval_service import (
    ChapterRetrievalService,
    FactRetrievalTrace,
    RetrievalOutcome,
)
from tests.test_architecture_closure import PLAN_TEXT, pass_decision
from tests.test_e07_9_1 import EVIDENCE, E0791Case


CLEAN = """## 连续性问题
- 无

## 支持依据
- 无

## 一致性结论
- **结论**: CLEAN
- **主要问题**: 无
"""

WARN = """## 连续性问题
- 林默在 Current State 中位于旧宅，但正文称他一直在北境。

## 支持依据
- Current State：林默位于旧宅。

## 一致性结论
- **结论**: WARN
- **主要问题**: 人物位置冲突
"""

NEEDS_REVISION = """## 一致性检查
### T1（硬错误）
- 门锁状态与历史事实冲突。
### T2（软问题）
- 无
### T3（观察项）
- 无

## 质量审阅
- **情节逻辑**: MAJOR — 存在冲突

## 审阅决策
- **决策**: NEEDS_REVISION
- **严重性**: MAJOR
- **主要问题**: 门锁状态与历史事实冲突
- **规划级别**: L1
"""


class TestAClosure(E0791Case):
    def test_real_retrieval_construction_never_instantiates_planner(self):
        with patch(
            "src.workflows.retrieval_service.AtomicFactStore"
        ), patch(
            "src.workflows.retrieval_service.AuthorRAGStore"
        ), patch(
            "src.agents.author.chapter_planner.ChapterPlanner"
        ) as planner:
            service = ChapterRetrievalService("human-mode")
        self.assertEqual(service.novel_id, "human-mode")
        planner.assert_not_called()
        source = inspect.getsource(ChapterRetrievalService)
        self.assertNotIn("ChapterPlanner", source)
        self.assertNotIn("self.planner", source)

    def test_human_writing_cli_only_shows_context_and_submit(self):
        waiting = {
            "workflow_status": "WAITING_HUMAN",
            "interrupts": [{
                "id": "i1",
                "value": {
                    "type": "human_writing",
                    "writing_context_path": "tracking/writing_context_ch0002.md",
                    "allowed_actions": ["submit", "restart"],
                },
            }],
        }
        args = SimpleNamespace(
            name="human-mode", chapter=2, action=None, stop=False,
            resume=None, outline="", chapter_intent="", candidate_file="",
        )
        output = io.StringIO()
        with patch.object(cli, "_get_novel_dir", return_value=True), patch.object(
            cli, "run_chapter_workflow", return_value=waiting
        ), contextlib.redirect_stdout(output):
            cli.cmd_write(args)
        rendered = output.getvalue()
        self.assertIn("【人工创作模式】", rendered)
        self.assertIn("tracking/writing_context_ch0002.md", rendered)
        self.assertIn("--action submit --file <正文文件>", rendered)
        for invalid in ("Planning level", "agent_edit", "regenerate", "Review reasons"):
            self.assertNotIn(invalid, rendered)


class HumanFlowCase(E0791Case):
    def setUp(self):
        super().setUp()
        self.settings.chapter_mode = "human"
        self.planner = patch(
            "src.agents.author.chapter_planner.ChapterPlanner"
        ).start()
        self.plan_reviewer = patch(
            "src.agents.author.plan_reviewer.PlanReviewer"
        ).start()
        self.writer = patch(
            "src.agents.author.deepseek_writer.DeepSeekWriter"
        ).start()
        self.stylist = patch(
            "src.agents.author.claude_stylist.ClaudeStylist"
        ).start()
        self.retrieval = patch(
            "src.workflows.retrieval_service.ChapterRetrievalService"
        ).start()
        self.manager_class = patch(
            "src.agents.state_manager.state_manager.StateManager"
        ).start()
        self.current_state = patch(
            "src.storage.current_state_store.CurrentStateStore.ensure_raw_initialized",
            return_value=("# Current State\n林默在旧宅。", ""),
        ).start()
        self.query_intent = patch(
            "src.agents.author.query_intent_builder.QueryIntentBuilder.build",
            return_value="intent",
        ).start()
        self.addCleanup(patch.stopall)
        self.retrieval.return_value.retrieve.return_value = self.retrieval_outcome()
        self.runner = ChapterWorkflowRunner("human-mode", 2)

    def _start(self, consistency=CLEAN):
        self.manager_class.return_value.review_consistency.return_value = {
            "raw_analysis": consistency,
            "filepath": None,
        }
        waiting = self.runner.run(chapter_intent="调查门锁，不揭露幕后人。")
        self.assertEqual(waiting["interrupts"][0]["value"]["type"], "human_writing")
        return waiting

    def _candidate_file(self, text="人工正文 Candidate。") -> Path:
        path = Path(self.tmp.name) / "candidate.md"
        path.write_text(text, encoding="utf-8")
        return path

    def _submit(self, path: Path):
        return self.runner.resume({
            "action": "submit",
            "candidate_file": str(path),
        })

    def test_candidate_submission_runs_consistency_not_agents_or_canonical(self):
        self._start(CLEAN)
        waiting = self._submit(self._candidate_file())
        self.assertEqual(waiting["workflow_status"], "WAITING_HUMAN")
        self.assertEqual(waiting["consistency_verdict"], "CLEAN")
        self.assertEqual(waiting["interrupts"][0]["value"]["type"],
                         "human_final_approval")
        self.assertEqual(waiting["candidate_text"], "人工正文 Candidate。")
        self.assertTrue((self.fs.root / waiting["candidate_path"]).is_file())
        self.manager_class.return_value.review_consistency.assert_called_once()
        self.assertEqual(self.retrieval.return_value.retrieve.call_count, 1)
        supplied = self.manager_class.return_value.review_consistency.call_args
        self.assertEqual(supplied.args[0], "人工正文 Candidate。")
        self.assertIn("Writing Context", supplied.kwargs["writing_context_text"])
        for agent in (self.planner, self.plan_reviewer, self.writer, self.stylist):
            agent.assert_not_called()
        self.assertFalse(self.fs.canonical_chapter_path(2).exists())

    def test_empty_missing_other_novel_and_wrong_chapter_are_rejected(self):
        self._start(CLEAN)
        empty = self._candidate_file("")
        with self.assertRaisesRegex(ValueError, "文件为空"):
            self._submit(empty)
        with self.assertRaisesRegex(ValueError, "文件不存在"):
            self._submit(Path(self.tmp.name) / "missing.md")
        other = self.settings.data_dir / "novels" / "other" / "candidate.md"
        other.parent.mkdir(parents=True)
        other.write_text("错误小说正文", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "其他 novel"):
            self._submit(other)
        wrong_chapter = self.fs.root / "chapters" / "chapter_0001_candidate.md"
        wrong_chapter.write_text("错误章节正文", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "指向第 1 章"):
            self._submit(wrong_chapter)
        with self.assertRaisesRegex(ValueError, "没有可 resume"):
            ChapterWorkflowRunner("human-mode", 3).resume({
                "action": "submit", "candidate_file": str(self._candidate_file())
            })
        self.assertFalse(self.fs.canonical_chapter_path(2).exists())

    def test_canonical_path_is_rejected_before_human_resume(self):
        self._start(CLEAN)
        canonical = self.fs.canonical_chapter_path(2)
        canonical.write_text("不得作为 Candidate。", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Canonical chapter path"):
            self._submit(canonical)

        waiting = self.runner.inspect()
        self.assertEqual(waiting["next"], ["await_human_writing"])
        self.assertEqual(waiting["interrupts"][0]["value"]["type"], "human_writing")
        self.manager_class.return_value.review_consistency.assert_not_called()
        self.assertNotIn("candidate_text", waiting["values"])

        canonical.unlink()
        resumed = self._submit(self._candidate_file("合法人工正文。"))
        self.assertEqual(
            resumed["interrupts"][0]["value"]["type"],
            "human_final_approval",
        )
        self.assertEqual(resumed["candidate_text"], "合法人工正文。")

    def test_commit_collision_preserves_commit_checkpoint_and_override(self):
        self._start(WARN)
        self._submit(self._candidate_file())
        self.runner.resume({"action": "approve"})
        canonical = self.fs.canonical_chapter_path(2)
        canonical.write_text("外部竞争写入。", encoding="utf-8")

        failed = self.runner.resume({"action": "confirm_override"})
        self.assertEqual(failed["workflow_status"], "error")
        self.assertEqual(failed["failed_runtime_stage"], "commit_canonical_prose")
        self.assertIn("FileExistsError", failed["error"])
        checkpoint = self.runner.inspect()
        self.assertEqual(checkpoint["next"], ["commit_canonical_prose"])
        self.assertEqual(checkpoint["values"]["consistency_verdict"], "WARN")
        self.assertTrue(checkpoint["values"]["review_override_confirmed"])
        self.assertEqual(
            self.manager_class.return_value.review_consistency.call_count, 1
        )

        canonical.unlink()
        self.manager_class.return_value.update_current_state.side_effect = RuntimeError(
            "down"
        )
        result = self.runner.run()
        self.assertEqual(result["workflow_status"], "DERIVATION_ERROR")
        self.assertEqual(self.fs.load_canonical_chapter(2), "人工正文 Candidate。")
        self.assertEqual(result["consistency_verdict"], "WARN")
        self.assertTrue(result["review_override_confirmed"])
        self.assertEqual(
            self.manager_class.return_value.review_consistency.call_count, 1
        )

    def test_warn_approve_requires_confirmation_and_keeps_verdict(self):
        self._start(WARN)
        final_wait = self._submit(self._candidate_file())
        self.assertEqual(final_wait["consistency_verdict"], "WARN")
        self.assertFalse(self.fs.canonical_chapter_path(2).exists())

        override_wait = self.runner.resume({"action": "approve"})
        self.assertEqual(override_wait["workflow_status"], "WAITING_HUMAN")
        payload = override_wait["interrupts"][0]["value"]
        self.assertEqual(payload["type"], "review_override_confirmation")
        self.assertEqual(payload["verdict"], "WARN")
        self.assertFalse(self.fs.canonical_chapter_path(2).exists())
        self.assertFalse(override_wait["review_override_confirmed"])
        self.assertEqual(
            self.manager_class.return_value.review_consistency.call_count, 1
        )

        self.manager_class.return_value.update_current_state.side_effect = RuntimeError("down")
        result = self.runner.resume({"action": "confirm_override"})
        self.assertEqual(result["workflow_status"], "DERIVATION_ERROR")
        self.assertEqual(result["consistency_verdict"], "WARN")
        self.assertTrue(result["review_override_confirmed"])
        self.assertEqual(self.fs.load_canonical_chapter(2), "人工正文 Candidate。")
        self.assertEqual(
            self.manager_class.return_value.review_consistency.call_count, 1
        )

    def test_warn_human_edit_forces_new_consistency_check(self):
        self.manager_class.return_value.review_consistency.side_effect = [
            {"raw_analysis": WARN, "filepath": None},
            {"raw_analysis": CLEAN, "filepath": None},
        ]
        self._start(WARN)
        first = self._submit(self._candidate_file())
        edit_path = self.fs.root / first["interrupts"][0]["value"]["edit_path"]
        edit_path.write_text("修正后的人工正文。", encoding="utf-8")

        second = self.runner.resume({"action": "human_edit"})
        self.assertEqual(second["workflow_status"], "WAITING_HUMAN")
        self.assertEqual(second["candidate_text"], "修正后的人工正文。")
        self.assertEqual(second["consistency_verdict"], "CLEAN")
        self.assertEqual(
            self.manager_class.return_value.review_consistency.call_count, 2
        )
        self.assertFalse(self.fs.canonical_chapter_path(2).exists())

    def test_clean_approve_uses_existing_complete_derivation(self):
        self._start(CLEAN)
        self._submit(self._candidate_file("最终人工正文。"))
        manager = self.manager_class.return_value
        manager.update_current_state.return_value = {
            "updated_current_state": "# Current State\n林默检查了门锁。"
        }
        manager.derive_atomic_facts.return_value = {
            "raw_analysis": "## Atomic Facts\n\n- [P1-P1] 林默检查门锁。"
        }
        manager.verify_atomic_facts.return_value = {
            "raw_analysis": "FACT 1\nDecision: VERIFIED\nReason: 原文明示。"
        }
        with patch.object(AtomicFactStore, "index_facts", return_value=1) as index:
            result = self.runner.resume({"action": "approve"})

        self.assertEqual(result["workflow_status"], "DERIVED_READY")
        self.assertEqual(result["consistency_verdict"], "CLEAN")
        self.assertFalse(result["review_override_confirmed"])
        self.assertEqual(self.fs.load_canonical_chapter(2), "最终人工正文。")
        manager.update_current_state.assert_called_once()
        manager.derive_atomic_facts.assert_called_once()
        manager.verify_atomic_facts.assert_called_once()
        index.assert_called_once()
        self.assertTrue((self.fs.root / "tracking" / "volume_progress.md").is_file())
        sources = self.fs.root / result["chapter_sources_path"]
        report = sources.read_text(encoding="utf-8")
        self.assertIn("## 2. 历史内容来源", report)
        self.assertIn("## 4. 关键生成过程", report)
        self.assertNotIn("adopted", report)
        self.assertNotIn("candidate-only", report)


class TestConsistencyInfrastructure(E0791Case):
    def test_parser_and_prompt_are_consistency_only(self):
        self.assertEqual(parse_consistency_decision({
            "consistency_raw_analysis": CLEAN
        })["consistency_verdict"], "CLEAN")
        parsed = parse_consistency_decision({"consistency_raw_analysis": WARN})
        self.assertEqual(parsed["consistency_verdict"], "WARN")
        self.assertIn("旧宅", parsed["consistency_warnings"][0])
        malformed = parse_consistency_decision({
            "consistency_raw_analysis": "看起来没问题"
        })
        self.assertEqual(malformed["consistency_verdict"], "UNKNOWN")
        self.assertEqual(malformed["workflow_status"], "error")

        prompt = (self.settings.prompts_dir / "consistency_reviewer.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("只检查", prompt)
        self.assertIn("禁止评价", prompt)
        self.assertIn("CLEAN", prompt)
        self.assertIn("WARN", prompt)
        source = inspect.getsource(StateManager.review_consistency)
        self.assertIn('load_prompt("consistency_reviewer.txt")', source)
        self.assertNotIn("review_chapter(", source)

    def test_canonical_guard_requires_normal_result_or_confirmed_override(self):
        self.assertTrue(_is_canonical_authorized({
            "verdict": "PASS", "final_author_approved": True
        }))
        self.assertTrue(_is_canonical_authorized({
            "chapter_mode": "human", "consistency_verdict": "CLEAN",
            "final_author_approved": True,
        }))
        for state in (
            {"verdict": "NEEDS_REVISION", "final_author_approved": True},
            {"chapter_mode": "human", "consistency_verdict": "WARN",
             "final_author_approved": True},
        ):
            self.assertFalse(_is_canonical_authorized(state))
            self.assertTrue(_is_canonical_authorized({
                **state, "review_override_confirmed": True
            }))

    def test_human_sources_do_not_require_or_fabricate_plan(self):
        result = save_chapter_sources({
            "novel_id": "human-mode",
            "chapter_index": 2,
            "chapter_mode": "human",
            "chapter_intent": "调查门锁",
            "writing_context_path": "tracking/writing_context_ch0002.md",
            "retrieval_trace_path": "tracking/rag_traces/trace.json",
            "retrieved_facts": [{
                "fact_id": "FACT-0001-001", "chapter_index": 1,
                "fact_type": "event", "text": "门锁被破坏。",
            }],
            "expanded_sources": [{
                "fact_id": "FACT-0001-001",
                "source_path": "chapters/chapter_0001.md",
                "paragraph_start": 2, "paragraph_end": 4,
            }],
        })
        report = (self.fs.root / result["chapter_sources_path"]).read_text(
            encoding="utf-8"
        )
        self.assertIn("## 2. 历史内容来源", report)
        self.assertIn("FACT-0001-001", report)
        self.assertIn("chapters/chapter_0001.md", report)
        self.assertNotIn("adopted", report)
        self.assertFalse(any((self.fs.root / "outlines").glob("chapter_plan*")))


class TestAgentReviewActions(E0791Case):
    def test_non_pass_exposes_specific_actions_without_approve(self):
        self.settings.chapter_mode = "agent"
        self.fs.save_canonical("settings", "world_setting", "# 世界观")
        retrieval = patch(
            "src.workflows.retrieval_service.ChapterRetrievalService"
        ).start()
        planner = patch("src.agents.author.chapter_planner.ChapterPlanner").start()
        plan_review = patch("src.agents.author.plan_reviewer.PlanReviewer").start()
        writer = patch("src.agents.author.deepseek_writer.DeepSeekWriter").start()
        stylist = patch("src.agents.author.claude_stylist.ClaudeStylist").start()
        manager_class = patch(
            "src.agents.state_manager.state_manager.StateManager"
        ).start()
        current = patch(
            "src.storage.current_state_store.CurrentStateStore.ensure_initialized",
            return_value=(SimpleNamespace(), "# Current State", "hash"),
        ).start()
        query_intent = patch(
            "src.agents.author.query_intent_builder.QueryIntentBuilder.build",
            return_value="intent",
        ).start()
        self.addCleanup(patch.stopall)
        retrieval.return_value.retrieve.return_value = RetrievalOutcome(
            trace=FactRetrievalTrace(chapter_index=1, success=True)
        )
        plan = ChapterPlan.from_markdown(PLAN_TEXT)

        def save_plan(*_args, **_kwargs):
            self.fs.save_canonical("outlines", "chapter_plan_ch0001", PLAN_TEXT)
            return plan

        planner.return_value.plan_chapter.side_effect = save_plan
        plan_review.return_value.review_plan.return_value = pass_decision()
        writer.return_value.write_chapter.return_value = "draft"
        stylist.return_value.edit_chapter.return_value = "agent candidate"
        manager = manager_class.return_value
        manager.review_chapter.return_value = {
            "raw_analysis": NEEDS_REVISION, "filepath": None
        }
        manager.derive_chapter.side_effect = RuntimeError("down")

        runner = ChapterWorkflowRunner("human-mode", 1)
        first = runner.run()
        self.assertEqual(
            first["interrupts"][0]["value"]["type"], "plan_review"
        )
        first = runner.resume({"action": "approve"})
        self.assertEqual(first["verdict"], "NEEDS_REVISION")
        payload = first["interrupts"][0]["value"]
        self.assertEqual(payload["type"], "chapter_review")
        self.assertEqual(
            payload["allowed_actions"],
            ["agent_edit", "human_edit", "regenerate_prose", "restart"],
        )
        self.assertIn("门锁状态与历史事实冲突", payload["reasons"])
        self.assertNotIn("approve", payload["allowed_actions"])
        self.assertFalse(self.fs.canonical_chapter_path(1).exists())
        self.assertEqual(manager.review_chapter.call_count, 1)


if __name__ == "__main__":
    unittest.main()
