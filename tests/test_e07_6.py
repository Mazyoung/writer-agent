"""Focused E07.6 chapter-loop invariants.

These tests mock all paid LLM calls. They exercise the production graph, SQLite
checkpoint resume, plan re-review, and deterministic prose revision limits.
"""

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config.settings import get_settings
from src.storage.document_formats import ChapterPlan, FactDigest, StateCommitResult
from src.storage.file_store import FileStore
from src.workflows.chapter_runner import ChapterWorkflowRunner
from src.workflows.chapter_workflow import (
    _route_after_chapter_decision,
    build_chapter_workflow,
    load_chapter_intent,
    write_draft,
)
from src.workflows.retrieval_service import RetrievalOutcome
from src.storage.chroma_store import RetrievalTrace


PLAN_TEXT = """# 第1章规划：《测试》

## 一、章节信息
- **章大纲**: 测试事件
- **章节类型**: 延续型
- **总场景数**: 1

## 二、写作上下文包
### 角色关系图
甲与乙互相信任。
### 物品/装备追踪
暂无
### 修炼/力量体系现状
暂无
### 关键伏笔节点
不得提前揭露真相。
### 情感调色板
紧张
### 禁止清单
未来卷终局

## 三、场景级写作计划
### 场景 1：开场 [状态：待规划]
- **发生什么**：甲进入仓库并与乙交谈。
- **本场景的戏剧功能**：推进主线
- **对话必须达成的信息增量**：确认仓库异常
- **角色微时刻**：甲握紧手电
- **涉及角色**：甲、乙
- **情绪曲线**：平静 → 紧张
- **字数预估**：800
- **与前后衔接**：承接上一章
"""


def decision(verdict: str, level: str = "L1", reason: str = "问题") -> str:
    severity = "PASS" if verdict == "PASS" else "MAJOR"
    t1 = "无" if verdict == "PASS" else f"- {reason}"
    quality = "PASS" if verdict == "PASS" else "MAJOR"
    return f"""## 一致性检查
### T1（硬错误）
{t1}
### T2（软问题）
无
### T3（观察项）
无
## 质量审阅
- **情节逻辑**: {quality} — test
## 审阅决策
- **决策**: {verdict}
- **严重性**: {severity}
- **主要问题**: {reason if verdict != 'PASS' else '无'}
- **规划级别**: {level}
"""


class E076Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = get_settings()
        self.original_data_dir = self.settings.data_dir
        self.settings.data_dir = Path(self.tmp.name)
        self.addCleanup(setattr, self.settings, "data_dir", self.original_data_dir)
        self.novel_id = "e076_test"
        self.fs = FileStore(self.novel_id, self.settings.data_dir)
        self.fs.save_canonical("settings", "world_setting", "# 世界观\n不得穿越时间。")
        self.fs.save_tracking_doc("book_plan", "# Book Plan\n终局信息仅供规划层。")
        self.fs.save_tracking_doc("volume_plan", "# Volume Plan\n本章进入仓库。")

    def _patch_runtime(self):
        patchers = {
            "retrieval": patch("src.workflows.retrieval_service.ChapterRetrievalService"),
            "planner": patch("src.agents.author.chapter_planner.ChapterPlanner"),
            "plan_reviewer": patch("src.agents.author.plan_reviewer.PlanReviewer"),
            "writer": patch("src.agents.author.deepseek_writer.DeepSeekWriter"),
            "stylist": patch("src.agents.author.claude_stylist.ClaudeStylist"),
            "checker": patch("src.agents.author.style_checker.StyleChecker"),
            "state_manager": patch("src.agents.state_manager.state_manager.StateManager"),
            "chroma": patch("src.storage.atomic_fact_store.AtomicFactStore"),
        }
        mocks = {name: patcher.start() for name, patcher in patchers.items()}
        for patcher in patchers.values():
            self.addCleanup(patcher.stop)

        mocks["retrieval"].return_value.retrieve.return_value = RetrievalOutcome(
            evidence="第0章历史事实",
            trace=RetrievalTrace(chapter_index=1, success=True),
        )

        fake_plan = ChapterPlan.from_markdown(PLAN_TEXT)

        def save_plan(*args, **kwargs):
            self.fs.save_canonical("outlines", "chapter_plan_ch0001", PLAN_TEXT)
            return fake_plan

        mocks["planner"].return_value.plan_chapter.side_effect = save_plan
        mocks["writer"].return_value.write_chapter.return_value = "初稿正文"
        mocks["writer"].return_value.revise_chapter.return_value = "自动修订正文"
        mocks["stylist"].return_value.edit_chapter.return_value = "风格正文"
        report = MagicMock(errors=0, warnings=0)
        report.summary.return_value = "OK"
        mocks["checker"].return_value.check_all.return_value = report
        mocks["state_manager"].return_value.extract_fact_digest_from_analysis.return_value = (
            FactDigest(chapter_index=1, confirmed_events="事件发生")
        )
        mocks["chroma"].return_value.index_facts.return_value = 1
        return mocks

    def _successful_commit(self, mock_state_manager):
        marker = self.fs.root / "states" / "chapter_0001_completed"

        def commit(*args, **kwargs):
            marker.write_text("PASS", encoding="utf-8")
            return {
                "_commit_result": StateCommitResult(
                    success=True,
                    changed_files=["states/chapter_0001_completed"],
                )
            }

        mock_state_manager.return_value.update_tracking_docs.side_effect = commit


class TestChapterIntent(E076Case):
    def test_supplied_intent_is_canonical_and_reloadable(self):
        state = {
            "novel_id": self.novel_id,
            "chapter_index": 1,
            "chapter_intent": "推进人物和解，但不能揭露终局。",
        }
        first = load_chapter_intent(state)
        second = load_chapter_intent({
            "novel_id": self.novel_id,
            "chapter_index": 1,
        })

        self.assertEqual(second["chapter_intent"], first["chapter_intent"])
        self.assertEqual(
            (self.fs.root / "briefs" / "chapter_intent_ch0001.md").read_text(
                encoding="utf-8"
            ),
            first["chapter_intent"],
        )

    def test_writer_requires_latest_plan_pass(self):
        result = write_draft({
            "novel_id": self.novel_id,
            "chapter_index": 1,
            "chapter_plan_text": PLAN_TEXT,
            "plan_verdict": "NEEDS_REVISION",
        })
        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("Plan Review", result["error"])

    def test_completed_preflight_blocks_intent_and_all_later_work(self):
        marker = self.fs.root / "states" / "chapter_0001_completed"
        marker.write_text("PASS", encoding="utf-8")
        intent_path = self.fs.root / "briefs" / "chapter_intent_ch0001.md"

        with patch(
            "src.workflows.chapter_workflow.load_chapter_intent",
            wraps=load_chapter_intent,
        ) as mock_intent:
            result = build_chapter_workflow().invoke({
                "novel_id": self.novel_id,
                "chapter_index": 1,
                "chapter_intent": "不得被保存",
            })

        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("ERROR_ALREADY_EXISTS", result["error"])
        mock_intent.assert_not_called()
        self.assertFalse(intent_path.exists())


class TestDeterministicRevisionRouting(unittest.TestCase):
    def test_writer_nodes_do_not_load_future_plans(self):
        from src.workflows.chapter_workflow import auto_revise_chapter

        for node in (write_draft, auto_revise_chapter):
            source = inspect.getsource(node.__wrapped__)
            self.assertNotIn('load_tracking_doc("book_plan")', source)
            self.assertNotIn('load_tracking_doc("volume_plan")', source)

    def test_only_review_one_l1_needs_revision_can_auto_revise(self):
        base = {
            "workflow_status": "DECISION_NEEDS_REVISION",
            "verdict": "NEEDS_REVISION",
            "planning_level": "L1",
        }
        self.assertEqual(
            _route_after_chapter_decision({
                **base, "review_round": 1, "revision_used": False,
            }),
            "auto_revise_chapter",
        )
        self.assertEqual(
            _route_after_chapter_decision({
                **base, "review_round": 2, "revision_used": True,
            }),
            "await_human_chapter",
        )
        self.assertEqual(
            _route_after_chapter_decision({
                **base, "review_round": 1, "revision_used": False,
                "planning_level": "L2",
            }),
            "await_human_chapter",
        )

    def test_unknown_and_runtime_error_end(self):
        self.assertEqual(_route_after_chapter_decision({
            "workflow_status": "error", "verdict": "NEEDS_REVISION",
        }), "__end__")
        self.assertEqual(_route_after_chapter_decision({
            "workflow_status": "DECISION_UNKNOWN", "verdict": "UNKNOWN",
        }), "__end__")


class TestCheckpointedPlanReview(E076Case):
    def test_human_plan_edit_is_re_reviewed_without_replanning(self):
        mocks = self._patch_runtime()
        mocks["plan_reviewer"].return_value.review_plan.side_effect = [
            decision("NEEDS_REVISION", reason="场景因果缺失"),
            decision("PASS", reason="无"),
        ]
        mocks["state_manager"].return_value.review_chapter.return_value = {
            "raw_analysis": decision("PASS"), "filepath": None,
        }
        self._successful_commit(mocks["state_manager"])

        runner = ChapterWorkflowRunner(self.novel_id, 1)
        waiting = runner.run(chapter_intent="不能提前揭露终局")

        self.assertEqual(waiting["workflow_status"], "WAITING_HUMAN")
        payload = waiting["interrupts"][0]["value"]
        self.assertEqual(payload["type"], "plan_review")
        self.assertEqual(payload["edit_path"], "outlines/chapter_plan_ch0001_edited.md")
        mocks["writer"].return_value.write_chapter.assert_not_called()

        with self.assertRaisesRegex(ValueError, "does not exist"):
            runner.resume({"action": "edit", "feedback": "先修规划"})
        still_waiting = runner.run()
        self.assertEqual(still_waiting["workflow_status"], "WAITING_HUMAN")

        (self.fs.root / payload["edit_path"]).write_text(PLAN_TEXT, encoding="utf-8")
        result = runner.resume({"action": "edit", "feedback": "已修规划"})

        self.assertEqual(result["workflow_status"], "completed")
        self.assertEqual(mocks["planner"].return_value.plan_chapter.call_count, 1)
        self.assertEqual(
            mocks["plan_reviewer"].return_value.review_plan.call_count, 2
        )
        mocks["writer"].return_value.write_chapter.assert_called_once()


class TestCheckpointedProseCycle(E076Case):
    def test_human_prose_starts_new_review_one_with_fresh_allowance(self):
        mocks = self._patch_runtime()
        mocks["plan_reviewer"].return_value.review_plan.return_value = decision("PASS")
        mocks["state_manager"].return_value.review_chapter.side_effect = [
            {"raw_analysis": decision("NEEDS_REVISION", reason="首轮问题"), "filepath": None},
            {"raw_analysis": decision("NEEDS_REVISION", reason="二审仍有问题"), "filepath": None},
            {"raw_analysis": decision("NEEDS_REVISION", reason="人工稿首轮问题"), "filepath": None},
            {"raw_analysis": decision("PASS"), "filepath": None},
        ]
        self._successful_commit(mocks["state_manager"])

        runner = ChapterWorkflowRunner(self.novel_id, 1)
        waiting = runner.run()

        self.assertEqual(waiting["workflow_status"], "WAITING_HUMAN")
        self.assertEqual(waiting["review_round"], 2)
        self.assertTrue(waiting["revision_used"])
        payload = waiting["interrupts"][0]["value"]
        self.assertEqual(payload["type"], "chapter_review")
        self.assertEqual(
            mocks["writer"].return_value.revise_chapter.call_count, 1
        )

        (self.fs.root / payload["edit_path"]).write_text(
            "人工修改后的完整正文", encoding="utf-8"
        )
        result = runner.resume({"action": "edit", "feedback": "已人工修改"})

        self.assertEqual(result["workflow_status"], "completed")
        self.assertEqual(result["review_round"], 2)
        self.assertTrue(result["revision_used"])
        self.assertEqual(
            mocks["writer"].return_value.revise_chapter.call_count, 2,
            "Each human prose edit must renew exactly one revision allowance",
        )
        self.assertEqual(
            mocks["state_manager"].return_value.review_chapter.call_count, 4
        )
        mocks["state_manager"].return_value.update_tracking_docs.assert_called_once()



class TestTerminalCheckpointClosure(E076Case):
    def _passing_prose_review(self, mocks):
        mocks["state_manager"].return_value.review_chapter.return_value = {
            "raw_analysis": decision("PASS"), "filepath": None,
        }
        self._successful_commit(mocks["state_manager"])

    def test_error_terminal_without_marker_starts_new_generate(self):
        mocks = self._patch_runtime()
        mocks["plan_reviewer"].return_value.review_plan.side_effect = [
            decision("UNKNOWN"),
            decision("PASS"),
        ]
        self._passing_prose_review(mocks)
        runner = ChapterWorkflowRunner(self.novel_id, 1)

        failed = runner.run()
        self.assertEqual(failed["workflow_status"], "error")
        self.assertFalse(
            (self.fs.root / "states" / "chapter_0001_completed").exists())

        completed = runner.run(chapter_intent="第二次执行")
        self.assertEqual(completed["workflow_status"], "completed")
        self.assertEqual(
            mocks["planner"].return_value.plan_chapter.call_count, 2)

    def test_stopped_terminal_without_marker_starts_new_generate(self):
        mocks = self._patch_runtime()
        mocks["plan_reviewer"].return_value.review_plan.side_effect = [
            decision("NEEDS_REVISION"),
            decision("PASS"),
        ]
        self._passing_prose_review(mocks)
        runner = ChapterWorkflowRunner(self.novel_id, 1)

        waiting = runner.run()
        self.assertEqual(waiting["workflow_status"], "WAITING_HUMAN")
        stopped = runner.resume({"action": "stop"})
        self.assertEqual(stopped["workflow_status"], "STOPPED_NON_PASS")

        completed = runner.run()
        self.assertEqual(completed["workflow_status"], "completed")
        self.assertEqual(
            mocks["planner"].return_value.plan_chapter.call_count, 2)

    def test_completed_marker_blocks_new_generate_after_terminal(self):
        mocks = self._patch_runtime()
        mocks["plan_reviewer"].return_value.review_plan.return_value = decision("PASS")
        self._passing_prose_review(mocks)
        runner = ChapterWorkflowRunner(self.novel_id, 1)

        completed = runner.run()
        self.assertEqual(completed["workflow_status"], "completed")
        calls = mocks["planner"].return_value.plan_chapter.call_count

        blocked = runner.run(chapter_intent="不得覆盖")
        self.assertEqual(blocked["workflow_status"], "error")
        self.assertIn("ERROR_ALREADY_EXISTS", blocked["error"])
        self.assertEqual(
            mocks["planner"].return_value.plan_chapter.call_count, calls)

if __name__ == "__main__":
    unittest.main()
