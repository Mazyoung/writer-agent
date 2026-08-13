"""Focused E07.8 current-state persistence tests; no paid calls."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agents.state_manager.state_manager import StateManager
from src.config.settings import get_settings
from src.storage.current_state_store import CurrentStateStore
from src.storage.document_formats import (
    CurrentCharacterState,
    CurrentForeshadowState,
    CurrentItemState,
    CurrentRelationshipState,
    CurrentCultivationState,
    CurrentState,
    StateDelta,
)
from src.storage.file_store import FileStore
from src.storage.sqlite_store import SQLiteStore
from src.workflows.chapter_workflow import load_current_state, review_plan


NO_CHANGE_ANALYSIS = """## 事实摘要
### FACT-0001-001
- **Chapter**: 1
- **Fact Type**: event
- **Entities**: 林默
- **Paragraph Range**: 1
- **Fact Text**: 林默进入医院。

## 状态变更（State Delta）
### 角色关系当前状态
- 无
### 角色物品状态
#### 获得
- 无
#### 消耗
- 无
#### 失去
- 无
### 角色修炼状态
- 无
### 角色当前状态
- 无
### 伏笔状态
- 无

## 审阅决策
- **决策**: PASS
- **严重性**: PASS
- **主要问题**: 无
- **规划级别**: L1
"""

FULL_DELTA_ANALYSIS = """## 状态变更（State Delta）
### 角色关系当前状态
- 赵诚 ↔ 林默: 关系类型=对手, 当前状态=正面冲突, 态度=戒备 [依据: P0002]
### 角色物品状态
#### 获得
- 黑色芯片: 持有者=赵诚, 来源=医院, 状态=可用 [依据: P0003]
#### 消耗
- 无
#### 失去
- 无
### 角色修炼状态
- 林默: 当前境界=二阶, 距下一阶=尚远, 特殊能力=感知, 限制=头痛 [依据: P0004]
### 角色当前状态
- 林默: 存活=存活, 位置=旧城区, 身体状态=左臂受伤, 身份=调查员 [依据: P0001]
### 伏笔状态
- NEW: 描述=芯片来源, 状态=OPEN, 预计回收=第2卷 [依据: P0003]
"""


class E078Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = get_settings()
        self.original_data_dir = self.settings.data_dir
        self.settings.data_dir = Path(self.tmp.name)
        self.addCleanup(setattr, self.settings, "data_dir", self.original_data_dir)
        self.novel_id = "e078"
        self.fs = FileStore(self.novel_id, self.settings.data_dir)
        self.sqlite = SQLiteStore(self.fs.root / "state.db")
        self.addCleanup(self.sqlite.close)
        self.store = CurrentStateStore(self.novel_id, self.fs, self.sqlite)


class TestCurrentStateFormat(E078Case):
    def test_round_trip_is_deterministic_and_escapes_cells(self):
        state = CurrentState(
            through_chapter=2,
            characters=[CurrentCharacterState(
                name="林|默", alive_status="存活", location="旧城\\医院",
                physical_state="轻伤\n稳定", identity_status="调查员",
                updated_chapter=2,
            )],
        )
        state.chapter.chapter_index = 2
        text = state.to_markdown()
        parsed = CurrentState.from_markdown(text)
        self.assertEqual(parsed.characters[0].name, "林|默")
        self.assertEqual(parsed.characters[0].location, "旧城\\医院")
        self.assertEqual(parsed.characters[0].physical_state, "轻伤\n稳定")
        self.assertEqual(parsed.to_markdown(), text)

    def test_missing_section_and_duplicate_key_fail_closed(self):
        text = CurrentState().to_markdown().replace("## Items", "## Missing")
        with self.assertRaisesRegex(ValueError, "section missing"):
            CurrentState.from_markdown(text)
        state = CurrentState(items=[
            CurrentItemState(name="芯片"), CurrentItemState(name="芯片")])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            state.to_markdown()

    def test_all_chapter_index_fields_accept_zero_and_round_trip(self):
        state = CurrentState(
            through_chapter=0,
            characters=[CurrentCharacterState(name="林默", updated_chapter=0)],
            relationships=[CurrentRelationshipState(
                character_a="林默", character_b="赵诚",
                last_interaction_chapter=0,
            )],
            items=[CurrentItemState(
                name="旧钥匙", acquired_chapter=0, updated_chapter=0
            )],
            cultivation=[CurrentCultivationState(name="林默", updated_chapter=0)],
            foreshadows=[CurrentForeshadowState(
                foreshadow_id="F0001", description="旧事", planted_chapter=0,
                last_progress_chapter=0, resolved_chapter=0,
            )],
        )
        text = state.to_markdown()
        parsed = CurrentState.from_markdown(text)
        self.assertEqual(parsed.through_chapter, 0)
        self.assertEqual(parsed.relationships[0].last_interaction_chapter, 0)
        self.assertEqual(parsed.items[0].acquired_chapter, 0)
        self.assertEqual(parsed.cultivation[0].updated_chapter, 0)
        self.assertEqual(parsed.foreshadows[0].planted_chapter, 0)
        self.assertEqual(parsed.to_markdown(), text)

    def test_legacy_prestory_maps_only_to_zero(self):
        text = CurrentState(items=[CurrentItemState(name="旧钥匙")]).to_markdown()
        legacy = text.replace("| 旧钥匙 |  |  |  | 0 |", "| 旧钥匙 |  |  |  | 前史 |")
        parsed = CurrentState.from_markdown(legacy)
        self.assertEqual(parsed.items[0].acquired_chapter, 0)

    def test_unknown_natural_language_chapters_fail_closed(self):
        text = CurrentState(items=[CurrentItemState(name="旧钥匙")]).to_markdown()
        for invalid in ("正文前", "故事开始前", "开篇前", "未知"):
            with self.subTest(invalid=invalid):
                malformed = text.replace(
                    "| 旧钥匙 |  |  |  | 0 |",
                    f"| 旧钥匙 |  |  |  | {invalid} |",
                )
                with self.assertRaisesRegex(ValueError, "Invalid Acquired Chapter"):
                    CurrentState.from_markdown(malformed)

    def test_generated_current_state_ignores_edited_override(self):
        original = CurrentState().to_markdown()
        self.fs.save_generated_tracking_doc("current_state", original)
        (self.fs.root / "tracking" / "current_state_edited.md").write_text(
            "# malicious override", encoding="utf-8")
        self.assertEqual(
            self.fs.load_generated_tracking_doc("current_state"), original)


class TestStateDeltaParsing(E078Case):
    def test_all_domains_parse_and_apply(self):
        base, _text, _digest = self.store.initialize_empty()
        delta = StateDelta.from_analysis(FULL_DELTA_ANALYSIS)
        candidate = self.store.apply_delta(
            base, delta, 1, "旧城追踪", 1234,
            "chapters/chapter_0001.md")
        self.assertEqual(candidate.characters[0].location, "旧城区")
        self.assertEqual(candidate.items[0].holder, "赵诚")
        self.assertEqual(candidate.relationships[0].normalized_key(), ("林默", "赵诚"))
        self.assertEqual(candidate.cultivation[0].current_stage, "二阶")
        self.assertEqual(candidate.foreshadows[0].foreshadow_id, "F0001")

    def test_missing_subsection_malformed_and_duplicate_fail_closed(self):
        missing = FULL_DELTA_ANALYSIS.replace("### 角色修炼状态", "### 错误标题")
        with self.assertRaisesRegex(ValueError, "subsection missing"):
            StateDelta.from_analysis(missing)
        malformed = FULL_DELTA_ANALYSIS.replace(
            "- 林默: 存活=存活, 位置=旧城区, 身体状态=左臂受伤, 身份=调查员",
            "- malformed")
        with self.assertRaisesRegex(ValueError, "Malformed"):
            StateDelta.from_analysis(malformed)
        duplicate = FULL_DELTA_ANALYSIS.replace(
            "- 黑色芯片: 持有者=赵诚, 来源=医院, 状态=可用",
            "- 黑色芯片: 持有者=赵诚, 来源=医院, 状态=可用\n"
            "- 黑色芯片: 持有者=林默, 来源=医院, 状态=可用")
        with self.assertRaisesRegex(ValueError, "Duplicate item"):
            StateDelta.from_analysis(duplicate)

    def test_explicit_no_change_delta_is_valid(self):
        delta = StateDelta.from_analysis(NO_CHANGE_ANALYSIS)
        self.assertEqual(delta, StateDelta())

    def test_item_old_holder_mismatch_and_unknown_foreshadow_fail(self):
        state = CurrentState(
            through_chapter=1,
            items=[CurrentItemState(
                name="芯片", holder="林默", status="可用", updated_chapter=1)],
            foreshadows=[CurrentForeshadowState(
                foreshadow_id="F0001", description="来源", status="OPEN",
                planted_chapter=1, last_progress_chapter=1)],
        )
        state.chapter.chapter_index = 1
        mismatch = FULL_DELTA_ANALYSIS.replace(
            "#### 消耗\n- 无", "#### 消耗\n- 芯片: 旧持有者=赵诚, 原因=损坏").replace(
            "- 黑色芯片: 持有者=赵诚, 来源=医院, 状态=可用", "- 无")
        with self.assertRaisesRegex(ValueError, "holder mismatch"):
            self.store.apply_delta(
                state, StateDelta.from_analysis(mismatch), 2, "", 1, "c2.md")
        unknown = FULL_DELTA_ANALYSIS.replace(
            "- NEW: 描述=芯片来源, 状态=OPEN, 预计回收=第2卷",
            "- F9999: 状态=RESOLVED, 回收章节=第2章")
        with self.assertRaisesRegex(ValueError, "Unknown foreshadow ID"):
            self.store.apply_delta(
                state, StateDelta.from_analysis(unknown), 2, "", 1, "c2.md")


class TestSQLiteProjection(E078Case):
    def _commit_full_state(self):
        base, _text, digest = self.store.initialize_empty()
        candidate = self.store.apply_delta(
            base, StateDelta.from_analysis(FULL_DELTA_ANALYSIS),
            1, "旧城追踪", 1234, "chapters/c1.md")
        result = self.store.commit(digest, candidate)
        self.assertTrue(result.success, result.error_message)
        return candidate

    def test_exact_queries_and_novel_isolation(self):
        self._commit_full_state()
        self.assertEqual(
            self.sqlite.get_character_current_state(
                self.novel_id, "林默")["location"], "旧城区")
        self.assertEqual(
            self.sqlite.get_item_current_holder(self.novel_id, "黑色芯片"), "赵诚")
        self.assertEqual(
            self.sqlite.get_relationship_current_state(
                self.novel_id, "赵诚", "林默")["relation_type"], "对手")
        self.assertEqual(
            self.sqlite.get_cultivation_current_state(
                self.novel_id, "林默")["current_stage"], "二阶")
        self.assertEqual(len(
            self.sqlite.get_current_pending_foreshadows(self.novel_id)), 1)
        self.assertIsNone(
            self.sqlite.get_character_current_state("other", "林默"))

    def test_hash_mismatch_rebuilds_from_markdown(self):
        candidate = self._commit_full_state()
        self.sqlite.conn.execute(
            "UPDATE current_character_state SET location='错误' WHERE novel_id=?",
            (self.novel_id,))
        self.sqlite.conn.execute(
            "UPDATE current_state_meta SET source_sha256='wrong' WHERE novel_id=?",
            (self.novel_id,))
        self.sqlite.conn.commit()
        self.store.ensure_sqlite_projection()
        self.assertEqual(
            self.sqlite.get_character_current_state(
                self.novel_id, "林默")["location"], candidate.characters[0].location)

    def test_stale_foreshadow_query_uses_last_progress(self):
        self._commit_full_state()
        self.assertEqual(len(
            self.sqlite.get_stale_foreshadows(self.novel_id, 10, 9)), 1)
        self.assertEqual(len(
            self.sqlite.get_stale_foreshadows(self.novel_id, 9, 9)), 0)


class TestMigration(E078Case):
    def test_legacy_markdown_and_sqlite_migrate_once(self):
        (self.fs.root / "tracking" / "character_states.md").write_text(
            "# 角色当前状态\n## 角色当前状态\n"
            "| 角色 | 存活 | 位置 | 身体状态 | 身份 | 更新章 |\n"
            "|---|---|---|---|---|---|\n"
            "| 林默 | 存活 | 医院 | 健康 | 调查员 | 第1章 |\n",
            encoding="utf-8")
        (self.fs.root / "tracking" / "items_equipment.md").write_text(
            "# 物品\n## 主角持有\n"
            "| 物品 | 来源 | 获得章 | 属性 | 状态 | 备注 |\n"
            "|---|---|---|---|---|---|\n"
            "| 芯片 | 医院 | 第1章 | 未知 | 可用 | 拥有者=林默 |\n",
            encoding="utf-8")
        self.sqlite.add_foreshadowing(
            self.novel_id, "芯片来源", "第1章", "第2卷")
        marker = self.fs.root / "states" / "chapter_0001_completed"
        marker.write_text("PASS", encoding="utf-8")

        state, text, digest = self.store.ensure_initialized()
        self.assertEqual(state.through_chapter, 1)
        self.assertEqual(state.characters[0].location, "医院")
        self.assertEqual(state.items[0].holder, "林默")
        self.assertEqual(state.foreshadows[0].foreshadow_id, "F0001")
        self.assertTrue(self.sqlite.current_state_projection_matches(
            self.novel_id, digest))
        self.assertEqual(self.store.ensure_initialized()[1], text)


class TestCommitBoundary(E078Case):
    def test_commit_writes_matching_markdown_sqlite_and_derived_marker(self):
        base, _text, digest = self.store.initialize_empty()
        candidate = self.store.apply_delta(
            base, StateDelta.from_analysis(FULL_DELTA_ANALYSIS),
            1, "追踪", 100, "chapters/c1.md")
        result = self.store.commit(digest, candidate)
        self.assertTrue(result.success)
        canonical = self.fs.load_generated_tracking_doc("current_state")
        expected_hash = self.store.content_hash(canonical)
        self.assertTrue(self.sqlite.current_state_projection_matches(
            self.novel_id, expected_hash))
        marker = (self.fs.root / "states" / "chapter_0001_derived").read_text(
            encoding="utf-8")
        self.assertIn(expected_hash, marker)

    def test_base_hash_mismatch_blocks_every_write(self):
        base, text, _digest = self.store.initialize_empty()
        candidate = self.store.apply_delta(
            base, StateDelta.from_analysis(FULL_DELTA_ANALYSIS),
            1, "追踪", 100, "chapters/c1.md")
        result = self.store.commit("stale-hash", candidate)
        self.assertFalse(result.success)
        self.assertEqual(self.store.load_text(), text)
        self.assertFalse(
            (self.fs.root / "states" / "chapter_0001_derived").exists())

    def test_sqlite_failure_restores_markdown_and_marker(self):
        base, text, digest = self.store.initialize_empty()
        candidate = self.store.apply_delta(
            base, StateDelta.from_analysis(FULL_DELTA_ANALYSIS),
            1, "追踪", 100, "chapters/c1.md")
        with patch.object(
            self.sqlite, "replace_current_state_projection",
            side_effect=RuntimeError("database down"),
        ):
            result = self.store.commit(digest, candidate)
        self.assertFalse(result.success)
        self.assertEqual(self.store.load_text(), text)
        self.assertFalse(
            (self.fs.root / "states" / "chapter_0001_derived").exists())

    def test_state_manager_requires_complete_delta(self):
        self.store.initialize_empty()
        result = StateManager(
            self.novel_id, self.sqlite
        ).update_tracking_docs(1, "正文", "## 审阅决策\n- **决策**: PASS")
        self.assertFalse(result["_commit_result"].success)
        self.assertFalse(
            (self.fs.root / "states" / "chapter_0001_derived").exists())


class TestWorkflowContext(E078Case):
    def test_load_node_checkpoints_one_snapshot_and_repairs_sqlite(self):
        result = load_current_state({
            "novel_id": self.novel_id, "chapter_index": 1})
        self.assertIn("# Current State", result["current_state_text"])
        self.assertEqual(
            CurrentStateStore.content_hash(result["current_state_text"]),
            result["current_state_sha256"])

    def test_plan_review_receives_checkpointed_current_state(self):
        self.fs.save_canonical("settings", "world_setting", "WORLD")
        self.fs.save_tracking_doc("book_plan", "BOOK")
        self.fs.save_tracking_doc("volume_plan", "VOLUME")
        marker = "CURRENT_STATE_MARKER_7812"
        with patch(
            "src.agents.author.plan_reviewer.PlanReviewer",
        ) as reviewer_class:
            review = reviewer_class.return_value.review_plan
            review.return_value = "## 审阅决策\n- **决策**: PASS"
            review_plan({
                "novel_id": self.novel_id, "chapter_index": 1,
                "chapter_plan_text": "PLAN", "current_state_text": marker,
            })
        self.assertEqual(review.call_args.kwargs["current_state"], marker)


if __name__ == "__main__":
    unittest.main()
