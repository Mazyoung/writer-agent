"""Focused no-paid-call verification for E07.7/E07.8 architecture closure."""

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config.settings import get_settings
from src.config.runtime_policy import NovelRuntimePolicy
from src.storage.atomic_fact_store import FactSearchResult
from src.storage.file_store import FileStore
from src.workflows.chapter_runner import ChapterWorkflowRunner
from src.workflows.chapter_workflow import (
    _load_prev_chapter_end,
    _route_after_chapter_decision,
    _route_after_human_chapter,
    commit_canonical_prose,
)
from src.workflows.retrieval_service import ChapterRetrievalService
from src.workflows.retrieval_service import FactRetrievalTrace, RetrievalOutcome


PLAN_TEXT = """# 第1章规划：《闭环》

## 一、章节信息
- **章大纲**: 测试
- **章节类型**: 延续型
- **总场景数**: 1

## 二、写作上下文包
### 角色关系图
暂无
### 物品/装备追踪
暂无
### 修炼/力量体系现状
暂无
### 关键伏笔节点
暂无
### 情感调色板
紧张
### 禁止清单
无

## 三、场景级写作计划
### 场景 1：测试 [状态：待规划]
- **发生什么**：甲进入房间。
- **本场景的戏剧功能**：推进
- **对话必须达成的信息增量**：确认异常
- **角色微时刻**：甲停步
- **涉及角色**：甲
- **情绪曲线**：平静 → 紧张
- **字数预估**：800
- **与前后衔接**：开篇
"""


def pass_decision():
    return """## 一致性检查
### T1（硬错误）
无
### T2（软问题）
无
### T3（观察项）
无
## 质量审阅
- **情节逻辑**: PASS — ok
## 审阅决策
- **决策**: PASS
- **严重性**: PASS
- **主要问题**: 无
- **规划级别**: L1
"""


class ClosureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = get_settings()
        old = settings.data_dir
        settings.data_dir = Path(self.tmp.name)
        self.addCleanup(setattr, settings, "data_dir", old)
        self.fs = FileStore("closure", settings.data_dir)


class TestCanonicalIdentity(ClosureCase):
    def test_commit_requires_pass_and_final_approval_and_never_overwrites(self):
        base = {
            "novel_id": "closure", "chapter_index": 1,
            "styled_text": "正式正文", "verdict": "PASS",
        }
        blocked = commit_canonical_prose(base)
        self.assertEqual(blocked["workflow_status"], "error")
        committed = commit_canonical_prose({
            **base, "final_author_approved": True,
        })
        self.assertEqual(committed["workflow_status"], "CANONICAL_COMMITTED")
        self.assertEqual(
            self.fs.load_canonical_chapter(1), "正式正文")
        self.assertFalse(
            (self.fs.root / "states" / "chapter_0001_derived").exists())
        with self.assertRaises(FileExistsError):
            commit_canonical_prose({
                **base, "final_author_approved": True,
                "styled_text": "覆盖尝试",
            })
        self.assertEqual(self.fs.load_canonical_chapter(1), "正式正文")

    def test_formal_history_ignores_styled_candidates(self):
        self.fs.commit_canonical_chapter(1, "CANONICAL END")
        self.fs.save("chapters", "chapter_0001_styled", "CANDIDATE END")
        self.assertEqual(_load_prev_chapter_end(self.fs, 2), "CANONICAL END")
        service = object.__new__(ChapterRetrievalService)
        service.fs = self.fs
        result = FactSearchResult(
            chapter_index=1,
            source_path="chapters/chapter_0001_styled_old.md",
        )
        self.assertEqual(
            service._resolve_source_path(result),
            self.fs.canonical_chapter_path(1),
        )
        self.assertEqual(len(self.fs.list_chapters()), 1)


class TestSemanticSeparation(ClosureCase):
    def test_reviewer_and_deriver_have_distinct_output_contracts(self):
        prompts = get_settings().prompts_dir
        reviewer = (prompts / "prose_reviewer.txt").read_text(encoding="utf-8")
        deriver = (prompts / "chapter_deriver.txt").read_text(encoding="utf-8")
        self.assertNotIn("## 状态变更（State Delta）", reviewer)
        self.assertNotIn("## 事实摘要", reviewer)
        self.assertIn("## 状态变更（State Delta）", deriver)
        self.assertIn("## 事实摘要", deriver)

    def test_author_rag_sync_reads_only_official_markdown(self):
        official = self.fs.root / "tracking" / "author_rag.md"
        edited = self.fs.root / "tracking" / "author_rag_edited.md"
        official.write_text("OFFICIAL", encoding="utf-8")
        edited.write_text("RETIRED OVERRIDE", encoding="utf-8")
        self.assertEqual(
            self.fs.load_generated_tracking_doc("author_rag"), "OFFICIAL")
        source = inspect.getsource(ChapterRetrievalService.retrieve)
        self.assertIn('load_generated_tracking_doc("author_rag")', source)
        self.assertNotIn('load_tracking_doc("author_rag")', source)

    def test_derivation_failure_does_not_revoke_canonical_prose(self):
        from src.storage.document_formats import ChapterPlan

        self.fs.save_canonical("settings", "world_setting", "# 世界观")
        self.fs.save_tracking_doc("book_plan", "# Book Plan")
        self.fs.save_tracking_doc("volume_plan", "# Volume Plan")
        targets = {
            "retrieval": "src.workflows.retrieval_service.ChapterRetrievalService",
            "planner": "src.agents.author.chapter_planner.ChapterPlanner",
            "plan_review": "src.agents.author.plan_reviewer.PlanReviewer",
            "writer": "src.agents.author.deepseek_writer.DeepSeekWriter",
            "stylist": "src.agents.author.claude_stylist.ClaudeStylist",
            "manager": "src.agents.state_manager.state_manager.StateManager",
            "query_intent": "src.agents.author.query_intent_builder.QueryIntentBuilder",
        }
        mocks = {}
        for name, target in targets.items():
            patcher = patch(target)
            mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        mocks["query_intent"].return_value.build.return_value = "intent"
        mocks["retrieval"].return_value.retrieve.return_value = RetrievalOutcome(
            trace=FactRetrievalTrace(chapter_index=1, success=True))
        plan = ChapterPlan.from_markdown(PLAN_TEXT)

        def save_plan(*_args, **_kwargs):
            self.fs.save_canonical("outlines", "chapter_plan_ch0001", PLAN_TEXT)
            return plan

        mocks["planner"].return_value.plan_chapter.side_effect = save_plan
        mocks["plan_review"].return_value.review_plan.return_value = pass_decision()
        mocks["writer"].return_value.write_chapter.return_value = "draft"
        mocks["stylist"].return_value.edit_chapter.return_value = "canonical prose"
        mocks["manager"].return_value.review_chapter.return_value = {
            "raw_analysis": pass_decision(), "filepath": None,
        }
        mocks["manager"].return_value.update_current_state.side_effect = RuntimeError(
            "current-state updater unavailable")

        runner = ChapterWorkflowRunner(
            "closure", 1,
            runtime_policy=NovelRuntimePolicy("agent", "supervised", 0, 5),
        )
        waiting = runner.run()
        self.assertEqual(waiting["workflow_status"], "WAITING_HUMAN")
        payload = waiting["interrupts"][0]["value"]
        self.assertEqual(payload["type"], "plan_review")
        waiting = runner.resume({"action": "approve"})
        payload = waiting["interrupts"][0]["value"]
        self.assertEqual(payload["type"], "final_author_approval")
        self.assertIn("approve", payload["allowed_actions"])
        self.assertFalse(self.fs.canonical_chapter_path(1).exists())
        mocks["manager"].return_value.update_tracking_docs.assert_not_called()

        result = runner.resume({"action": "approve"})
        self.assertEqual(result["workflow_status"], "DERIVATION_ERROR")
        self.assertEqual(self.fs.load_canonical_chapter(1), "canonical prose")
        mocks["manager"].return_value.update_tracking_docs.assert_not_called()


class TestRestart(ClosureCase):
    def test_restart_discards_candidates_and_preserves_intent(self):
        runner = ChapterWorkflowRunner("closure", 1)
        pending = MagicMock()
        pending.id = "interrupt-1"
        pending.value = {
            "type": "chapter_review",
            "allowed_actions": ["restart"],
        }
        snapshot = MagicMock(
            interrupts=[pending], values={"verdict": "NEEDS_REVISION"})
        connection = MagicMock()
        checkpointer = MagicMock()
        graph = MagicMock()
        graph.get_state.return_value = snapshot
        runner._open_graph = MagicMock(
            return_value=(connection, checkpointer, graph))
        self.fs.save_canonical("briefs", "chapter_intent_ch0001", "preserve me")
        self.fs.save("chapters", "chapter_0001_styled", "candidate")
        self.fs.save_canonical("outlines", "chapter_plan_ch0001", PLAN_TEXT)

        restarted = runner.resume({"action": "restart"})
        self.assertEqual(restarted["workflow_status"], "RESTARTED")
        self.assertEqual(
            self.fs.load_canonical("briefs", "chapter_intent_ch0001"),
            "preserve me",
        )
        self.assertFalse(any(
            (self.fs.root / "chapters").glob("chapter_0001_styled*.md")))
        checkpointer.delete_thread.assert_called_with(runner.thread_id)
