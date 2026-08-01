"""E03 Replanning Foundation 测试：数据模型可序列化 + 持久化接口。

只测试数据结构与其序列化/持久化，不测试尚未实现的自动 repair 行为。
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.planning.models import (
    PlanRevision, PlanningModificationReport, StrategicRepairCase,
    StoryBranch, ChapterCheckpoint,
    PlanType, RevisionStatus, ReportStatus, RepairStrategy, BranchStatus,
)
from src.planning.store import PlanningStore
from src.planning.trigger_policy import ReplanTriggerPolicy, ReplanTrigger


class TestModelSerialization(unittest.TestCase):
    """五个基础模型：to_dict / from_dict round-trip。"""

    def _roundtrip(self, obj):
        return type(obj).from_dict(obj.to_dict())

    def test_plan_revision(self):
        rev = PlanRevision(
            plan_type=PlanType.BOOK_PLAN, base_version="v1", new_version="v2",
            trigger_chapter="第68章", reason="fact_conflict: 身份提前暴露",
            old_content_ref="tracking/revisions/old.md",
            new_content_ref="tracking/book_plan.md",
            affected_nodes=["第4卷", "第5卷"], status=RevisionStatus.APPLIED,
            approved_by="human", decision="accept",
        )
        r2 = self._roundtrip(rev)
        self.assertEqual(r2.revision_id, rev.revision_id)
        self.assertEqual(r2.plan_type, "book_plan")
        self.assertEqual(r2.affected_nodes, ["第4卷", "第5卷"])
        self.assertEqual(r2.approved_by, "human")

    def test_planning_modification_report(self):
        rep = PlanningModificationReport(
            trigger_chapter="第20章", problem="卷规划中角色位置与事实矛盾",
            affected_plan=PlanType.VOLUME_PLAN,
            current_plan="事件5：柯林在交易站",
            conflicting_actual_state="第19章柯林已进入地下",
            evidence="fact_digest_ch0019",
            proposed_change="事件5改为地下遭遇",
            affected_future_nodes=["事件5", "事件6"],
            risk_if_accept="后续两事件需调整",
            risk_if_reject="持续矛盾",
        )
        r2 = self._roundtrip(rep)
        self.assertEqual(r2.report_id, rep.report_id)
        self.assertEqual(r2.status, ReportStatus.PENDING)
        self.assertEqual(r2.affected_future_nodes, ["事件5", "事件6"])

    def test_strategic_repair_case(self):
        case = StrategicRepairCase(
            trigger_chapter="第68章",
            problem_summary="身份提前不可逆暴露，后半卷节点失效",
            affected_scope="both",
            affected_chapters=["69", "70", "71"],
            repair_options=[RepairStrategy.FORWARD_REPAIR,
                            RepairStrategy.ROLLBACK_REWRITE],
            last_safe_checkpoint="ckpt_abc",
        )
        c2 = self._roundtrip(case)
        self.assertEqual(c2.case_id, case.case_id)
        self.assertTrue(c2.requires_human)  # 硬约束
        self.assertIn("FORWARD_REPAIR", c2.repair_options)

    def test_repair_case_requires_human_forced(self):
        """即使显式传 False，也被强制为 True（禁止 Agent 自动修复）。"""
        case = StrategicRepairCase(requires_human=False)
        self.assertTrue(case.requires_human)

    def test_story_branch(self):
        br = StoryBranch(parent_branch="main", fork_checkpoint="ckpt_52",
                         status=BranchStatus.ABANDONED,
                         created_reason="rollback_rewrite")
        b2 = self._roundtrip(br)
        self.assertEqual(b2.branch_id, br.branch_id)
        self.assertEqual(b2.status, "ABANDONED")
        self.assertEqual(b2.fork_checkpoint, "ckpt_52")

    def test_chapter_checkpoint(self):
        ck = ChapterCheckpoint(
            chapter_index=52, active_branch="main",
            book_plan_version="v1", volume_plan_version="v2",
            chapter_plan_version="v1",
            chapter_file="chapters/chapter_0052_styled_20260801.md",
            memory_state_version="fd_0052", tracking_state_version="v3",
            fact_digest_version="fd_0052",
        )
        c2 = self._roundtrip(ck)
        self.assertEqual(c2.checkpoint_id, ck.checkpoint_id)
        self.assertEqual(c2.chapter_index, 52)
        self.assertEqual(c2.volume_plan_version, "v2")


class TestPlanningStore(unittest.TestCase):
    """持久化接口：save / load / list。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = PlanningStore(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_revision_save_load_list(self):
        rev = PlanRevision(plan_type=PlanType.VOLUME_PLAN, reason="test")
        path = self.store.save_revision(rev)
        self.assertTrue(path.exists())
        loaded = self.store.load_revision(rev.revision_id)
        self.assertEqual(loaded.to_dict(), rev.to_dict())
        self.assertEqual(len(self.store.list_revisions()), 1)
        # 文件落在 tracking/revisions/
        self.assertIn(("tracking" + "\\" + "revisions").replace("\\", "/"),
                      str(path).replace("\\", "/"))

    def test_all_kinds_persist(self):
        self.store.save_report(PlanningModificationReport(problem="p"))
        self.store.save_repair_case(StrategicRepairCase(problem_summary="s"))
        self.store.save_branch(StoryBranch(created_reason="r"))
        self.store.save_checkpoint(ChapterCheckpoint(chapter_index=1))
        self.assertEqual(len(self.store.list_reports()), 1)
        self.assertEqual(len(self.store.list_repair_cases()), 1)
        self.assertEqual(len(self.store.list_branches()), 1)
        self.assertEqual(len(self.store.list_checkpoints()), 1)

    def test_load_missing_returns_none(self):
        self.assertIsNone(self.store.load_revision("rev_nonexistent"))


class TestReplanTriggerPolicy(unittest.TestCase):
    def test_allowed(self):
        self.assertTrue(ReplanTriggerPolicy.is_allowed(ReplanTrigger.FACT_CONFLICT))
        self.assertTrue(ReplanTriggerPolicy.is_allowed("user_request"))
        self.assertTrue(ReplanTriggerPolicy.is_allowed("node_preempted"))

    def test_forbidden(self):
        self.assertFalse(ReplanTriggerPolicy.is_allowed("more_exciting"))
        self.assertFalse(ReplanTriggerPolicy.is_allowed("style_change"))
        self.assertTrue(ReplanTriggerPolicy.is_forbidden("writer_preference"))
        self.assertTrue(ReplanTriggerPolicy.is_forbidden("speculation"))


if __name__ == "__main__":
    unittest.main()
