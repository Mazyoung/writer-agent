"""Focused E07.9 lifecycle and derivation-repair tests; no paid calls."""

import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agents.author.chapter_planner import ChapterPlanner
from src.agents.state_manager.state_manager import StateManager
from src.config.settings import get_settings
from src.planning.novel_lifecycle import NovelLifecycleService
from src.storage.document_formats import CurrentState, VolumePlan
from src.storage.file_store import FileStore
from src.workflows.chapter_runner import ChapterWorkflowRunner
from src.workflows.retrieval_service import ChapterRetrievalService
from src.workflows.chapter_workflow import (
    _parse_volume_progress,
    build_chapter_workflow,
    parse_chapter_decision,
)


VOLUME_DRAFT = """# 第2卷规划：《远路》
- **版本**: v1
- **状态**: DRAFT

## 起始状态
众人离开旧城。

## 本卷目标
抵达北境。

## 主要冲突
追兵与严冬。

## 故事阶段/路径
- 离城后的失序
- 重建队伍信任
- 穿越边境

## 关键转折
- 队伍发现内鬼

## 限制条件
不能推翻既有事实。

## 目标结束状态
众人在北境建立落脚点。

## 作者备注
这段必须保留。
"""


class E079Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = get_settings()
        old = settings.data_dir
        settings.data_dir = Path(self.tmp.name)
        self.addCleanup(setattr, settings, "data_dir", old)
        self.fs = FileStore("e079", settings.data_dir)


class TestContracts(E079Case):
    def test_prose_halt_is_unknown_fail_closed(self):
        result = parse_chapter_decision({
            "raw_analysis": "## 审阅决策\n- **决策**: HALT\n- **严重性**: MAJOR",
        })
        self.assertEqual(result["verdict"], "UNKNOWN")
        self.assertEqual(result["workflow_status"], "error")

    def test_volume_progress_is_advisory_enum(self):
        self.assertEqual(_parse_volume_progress(
            "## Volume Progress\n- **Recommendation**: READY_TO_CLOSE"),
            "READY_TO_CLOSE")
        self.assertEqual(_parse_volume_progress("malformed"), "UNKNOWN")

    def test_graph_has_checkpointed_derivation_stages(self):
        nodes = set(build_chapter_workflow().get_graph().nodes)
        self.assertTrue({
            "derive_semantics", "persist_current_state", "persist_fact_digest",
            "persist_volume_progress", "persist_chapter_sources", "sync_chroma",
        }.issubset(nodes))
        self.assertNotIn("derive_chapter", nodes)

    def test_chapter_planning_has_no_per_chapter_volume_extractor(self):
        self.assertFalse(hasattr(ChapterPlanner, "_extract_chapter_from_volume"))
        planner_source = inspect.getsource(ChapterPlanner.plan_chapter)
        retrieval_source = inspect.getsource(ChapterRetrievalService._build_query)
        self.assertNotIn("_extract_chapter_from_volume", planner_source)
        self.assertNotIn("_extract_chapter_from_volume", retrieval_source)
        self.assertIn("volume_plan[:3000]", planner_source)
    def test_top_level_markdown_ignores_edited_shadow(self):
        cases = [
            ("settings", "world_setting"), ("tracking", "book_plan"),
            ("tracking", "volume_plan"), ("tracking", "author_rag"),
        ]
        for category, name in cases:
            self.fs.save_canonical(category, name, "OFFICIAL")
            (self.fs.root / category / f"{name}_edited.md").write_text(
                "SHADOW", encoding="utf-8")
            self.assertEqual(self.fs.load_canonical(category, name), "OFFICIAL")


class TestCurrentStateRetry(E079Case):
    def test_already_applied_current_state_is_idempotent(self):
        state = CurrentState(through_chapter=1)
        state.chapter.chapter_index = 1
        state.chapter.canonical_source_path = "chapters/chapter_0001.md"
        with patch(
            "src.storage.current_state_store.CurrentStateStore.ensure_initialized",
            return_value=(state, state.to_markdown(), "new-hash"),
        ):
            result = StateManager("e079", MagicMock()).update_tracking_docs(
                1, "canonical", "malformed but must not be reparsed",
                expected_state_sha256="old-hash",
                canonical_source_path="chapters/chapter_0001.md",
            )
        self.assertTrue(result["_commit_result"].success)

class TestRepair(E079Case):
    def test_repair_resumes_first_incomplete_checkpoint_stage(self):
        runner = ChapterWorkflowRunner("e079", 1)
        self.fs.commit_canonical_chapter(1, "canonical")
        values = {
            "novel_id": "e079", "chapter_index": 1,
            "commit_success": True, "workflow_status": "DERIVATION_ERROR",
            "derivation_raw_analysis": "DERIVED",
            "current_state_persisted": True,
            "fact_digest_generated": False,
        }
        snapshot = SimpleNamespace(values=values, interrupts=[])
        connection = MagicMock()
        graph = MagicMock()
        graph.get_state.return_value = snapshot
        graph.invoke.return_value = {**values, "workflow_status": "DERIVED_READY"}
        runner._open_graph = MagicMock(return_value=(connection, MagicMock(), graph))

        result = runner.repair_derivation()

        self.assertEqual(result["workflow_status"], "DERIVED_READY")
        self.assertEqual(graph.update_state.call_args.kwargs["as_node"],
                         "persist_current_state")
        graph.invoke.assert_called_once_with(None, config=runner.config)


class TestVolumeLifecycle(E079Case):
    def _service(self):
        service = NovelLifecycleService("e079")
        service.plot_designer = MagicMock()
        return service

    def test_close_is_manual_and_approve_preserves_custom_notes(self):
        active = VOLUME_DRAFT.replace("DRAFT", "ACTIVE")
        self.fs.save_tracking_doc("volume_plan", active)
        self.fs.save_generated_tracking_doc(
            "volume_progress", "# Volume Progress\n- **Recommendation**: CONTINUE\n")
        service = self._service()
        closed = service.close_volume()
        self.assertIn("**状态**: COMPLETED", closed)
        self.assertIn("## 作者备注", closed)
        self.assertTrue((self.fs.root / "tracking" / "volumes" / "volume_02.md").exists())

        self.fs.save_tracking_doc("volume_plan", VOLUME_DRAFT)
        approved = service.approve_volume()
        self.assertIn("**状态**: ACTIVE", approved)
        self.assertIn("## 作者备注\n这段必须保留。", approved)

    def test_next_volume_uses_required_inputs_and_stays_draft(self):
        previous = VOLUME_DRAFT.replace("第2卷", "第1卷").replace(
            "DRAFT", "COMPLETED")
        self.fs.save_tracking_doc("volume_plan", previous)
        self.fs.save_tracking_doc("book_plan", "BOOK INPUT")
        self.fs.save_canonical("settings", "world_setting", "WORLD INPUT")
        self.fs.save_generated_tracking_doc("current_state", "STATE INPUT")
        service = self._service()
        service.plot_designer.run.return_value = SimpleNamespace(content=VOLUME_DRAFT)

        result = service.start_new_volume(notes="USER INTENT")

        prompt = service.plot_designer.run.call_args.kwargs["user_message"]
        for marker in ("WORLD INPUT", "BOOK INPUT", "Previous Volume Plan",
                       "STATE INPUT", "USER INTENT"):
            self.assertIn(marker, prompt)
        self.assertIn("**状态**: DRAFT", result)
        self.assertNotIn("对应章节", result)
        self.assertEqual(VolumePlan.from_markdown(result).story_path[0],
                         "离城后的失序")


if __name__ == "__main__":
    unittest.main()
