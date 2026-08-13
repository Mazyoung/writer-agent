import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config.settings import get_settings
from src.storage.atomic_fact_protocol import (
    expand_source_ranges,
    parse_atomic_facts,
    parse_source_ranges,
    parse_verification_decisions,
    validate_source_ranges,
)
from src.storage.atomic_fact_store import FactSearchResult
from src.storage.current_state_store import CurrentStateStore
from src.storage.document_formats import (
    AtomicFact, CurrentForeshadowState, CurrentItemState, CurrentState,
)
from src.storage.file_store import FileStore
from src.storage.sqlite_store import SQLiteStore
from src.workflows.chapter_workflow import (
    _derived_failure, persist_current_state, verify_atomic_facts,
)
from src.workflows.retrieval_service import ChapterRetrievalService


class DerivationProtocolCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = get_settings()
        old = self.settings.data_dir
        self.settings.data_dir = Path(self.tmp.name)
        self.addCleanup(setattr, self.settings, "data_dir", old)
        self.fs = FileStore("protocol", self.settings.data_dir)
        self.fs.commit_canonical_chapter(1, "第一段。\n\n第二段。\n\n第三段。")

    def state(self, facts):
        return {
            "novel_id": "protocol",
            "chapter_index": 1,
            "atomic_fact_candidates": facts,
            "generation_events": [],
            "warnings": [],
        }


class TestCurrentStateRawMarkdown(DerivationProtocolCase):
    def test_non_schema_markdown_is_rejected_before_persistence(self):
        sqlite = SQLiteStore(self.fs.root / "state.db")
        try:
            store = CurrentStateStore("protocol", self.fs, sqlite)
            old, digest = store.ensure_raw_initialized()
            updated = "# 故事当前状态\n\n## 自由标题\n老陈警告纪远别查太深。"
            result = store.commit_raw(
                digest, updated, 1, "chapters/chapter_0001.md"
            )
        finally:
            sqlite.close()
        self.assertFalse(result.success)
        self.assertIn("Invalid Updated Current State", result.error_message)
        self.assertEqual(old, self.fs.load_generated_tracking_doc("current_state"))


    def test_invalid_candidate_does_not_replace_valid_current_state(self):
        sqlite = SQLiteStore(self.fs.root / "state.db")
        try:
            store = CurrentStateStore("protocol", self.fs, sqlite)
            old_state = CurrentState(
                through_chapter=1,
                items=[CurrentItemState(
                    name="旧钥匙", acquired_chapter=0, updated_chapter=1,
                )],
            )
            old_state.chapter.chapter_index = 1
            old = old_state.to_markdown()
            self.fs.save_generated_tracking_doc("current_state", old)
            invalid = old.replace(
                "| 旧钥匙 |  |  |  | 0 |",
                "| 旧钥匙 |  |  |  | 正文前 |",
            )
            result = store.commit_raw(
                store.content_hash(old), invalid, 1, "chapters/chapter_0001.md"
            )
        finally:
            sqlite.close()
        self.assertFalse(result.success)
        self.assertIn("Invalid Acquired Chapter", result.error_message)
        self.assertEqual(old, self.fs.load_generated_tracking_doc("current_state"))
        self.assertFalse((self.fs.root / "states" / "chapter_0001_derived").exists())

    def test_valid_zero_candidate_persists_and_workflow_can_continue(self):
        sqlite = SQLiteStore(self.fs.root / "state.db")
        try:
            store = CurrentStateStore("protocol", self.fs, sqlite)
            old, digest = store.ensure_raw_initialized()
            valid = CurrentState(
                through_chapter=1,
                items=[CurrentItemState(
                    name="旧钥匙", acquired_chapter=0, updated_chapter=1,
                )],
            )
            valid.chapter.chapter_index = 1
            result = store.commit_raw(
                digest, valid.to_markdown(), 1, "chapters/chapter_0001.md"
            )
        finally:
            sqlite.close()
        self.assertTrue(result.success, result.error_message)
        parsed = CurrentState.from_markdown(
            self.fs.load_generated_tracking_doc("current_state")
        )
        self.assertEqual(parsed.items[0].acquired_chapter, 0)

    def test_invalid_foreshadow_rejects_then_valid_candidate_commits(self):
        sqlite = SQLiteStore(self.fs.root / "state.db")
        try:
            store = CurrentStateStore("protocol", self.fs, sqlite)
            old_state = CurrentState(through_chapter=1)
            old_state.chapter.chapter_index = 1
            old = old_state.to_markdown()
            self.fs.save_generated_tracking_doc("current_state", old)
            candidate = CurrentState(
                through_chapter=1,
                foreshadows=[CurrentForeshadowState(
                    foreshadow_id="F0001", description="门后低语",
                    status="OPEN", planted_chapter=1,
                    last_progress_chapter=1, resolved_chapter=0,
                )],
            )
            candidate.chapter.chapter_index = 1
            valid = candidate.to_markdown()
            invalid = valid.replace("F0001", "FS-001")

            failed = store.commit_raw(
                store.content_hash(old), invalid, 1, "chapters/chapter_0001.md"
            )
            self.assertFalse(failed.success)
            self.assertIn("Invalid foreshadow ID: FS-001", failed.error_message)
            self.assertEqual(old, self.fs.load_generated_tracking_doc("current_state"))
            self.assertFalse(
                (self.fs.root / "states" / "chapter_0001_derived").exists()
            )
            self.assertEqual(
                self.fs.load_canonical_chapter(1),
                "第一段。\n\n第二段。\n\n第三段。",
            )

            recovered = store.commit_raw(
                store.content_hash(old), valid, 1, "chapters/chapter_0001.md"
            )
        finally:
            sqlite.close()
        self.assertTrue(recovered.success, recovered.error_message)
        persisted = CurrentState.from_markdown(
            self.fs.load_generated_tracking_doc("current_state")
        )
        self.assertEqual(persisted.foreshadows[0].foreshadow_id, "F0001")
        self.assertTrue(
            (self.fs.root / "states" / "chapter_0001_derived").exists()
        )
    def test_invalid_candidate_becomes_derivation_failure_without_canonical_change(self):
        valid = CurrentState(through_chapter=1)
        valid.chapter.chapter_index = 1
        old = valid.to_markdown()
        self.fs.save_generated_tracking_doc("current_state", old)
        invalid = valid.to_markdown().replace(
            "- **Through Chapter**: 1", "- **Through Chapter**: 正文前"
        )
        result = persist_current_state({
            "novel_id": "protocol",
            "chapter_index": 1,
            "current_state_sha256": CurrentStateStore.content_hash(old),
            "updated_current_state_text": invalid,
            "canonical_source_path": "chapters/chapter_0001.md",
            "generation_events": [],
            "warnings": [],
        })
        self.assertEqual(result["workflow_status"], "DERIVATION_ERROR")
        self.assertEqual(result["failed_derivation_stage"], "current-state")
        self.assertEqual(old, self.fs.load_generated_tracking_doc("current_state"))
        self.assertEqual(
            self.fs.load_canonical_chapter(1),
            "第一段。\n\n第二段。\n\n第三段。",
        )


class TestAtomicFactProtocol(unittest.TestCase):
    def test_source_range_normalization_accepts_harmless_variants(self):
        for value in (
            "[P0170-P0192]", "[P170-P192]", "P0170-P0192",
            "P170 - P192", "P170~P192", "P170—P192",
        ):
            self.assertEqual(parse_source_ranges(value), [{"start": 170, "end": 192}])
        self.assertEqual(
            parse_source_ranges("[P3-P4; P17-P19]"),
            [{"start": 3, "end": 4}, {"start": 17, "end": 19}],
        )

    def test_new_bullet_protocol_and_invalid_addresses(self):
        facts = parse_atomic_facts(
            "## Atomic Facts\n\n- [P1-P2] 纪远进入医院。", 1
        )
        self.assertEqual(facts[0].fact_id, "FACT-0001-001")
        self.assertEqual(facts[0].fact_text, "纪远进入医院。")
        validate_source_ranges(facts[0], 2)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_source_ranges(facts[0], 1)
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            parse_source_ranges("医院那一段")

    def test_batch_decisions_require_explicit_complete_protocol(self):
        parsed = parse_verification_decisions(
            "FACT 1\nDecision: VERIFIED\nReason: 原文明确。\n\n"
            "FACT 2\nDecision: INSUFFICIENT\nReason: 地址过窄。",
            2,
        )
        self.assertEqual([item.decision for item in parsed], ["VERIFIED", "INSUFFICIENT"])
        with self.assertRaises(ValueError):
            parse_verification_decisions("分析认为大概正确", 1)


class TestFactCorrectivePass(DerivationProtocolCase):
    def candidate(self, text="旧事实", start=2, end=2):
        return {
            "fact_id": "FACT-0001-001", "chapter_index": 1,
            "source_ranges": [{"start": start, "end": end}],
            "fact_text": text, "repair_used": False,
        }

    @patch("src.agents.state_manager.state_manager.StateManager.repair_atomic_fact")
    @patch("src.agents.state_manager.state_manager.StateManager.verify_atomic_facts")
    def test_incorrect_gets_one_targeted_repair_then_verified(self, verify, repair):
        verify.side_effect = [
            {"raw_analysis": "FACT 1\nDecision: INCORRECT\nReason: 夸大。"},
            {"raw_analysis": "FACT 1\nDecision: VERIFIED\nReason: 明确。"},
        ]
        repair.return_value = {"raw_analysis": "- [P0002-P0002] 第二段发生。"}
        result = verify_atomic_facts(self.state([self.candidate()]))
        self.assertEqual(result["workflow_status"], "FACT_DIGEST_PERSISTED")
        self.assertEqual(result["verified_atomic_facts"][0]["fact_text"], "第二段发生。")
        repair.assert_called_once()

    @patch("src.agents.state_manager.state_manager.StateManager.verify_atomic_facts")
    def test_insufficient_expands_address_before_second_verification(self, verify):
        verify.side_effect = [
            {"raw_analysis": "FACT 1\nDecision: INSUFFICIENT\nReason: 过窄。"},
            {"raw_analysis": "FACT 1\nDecision: VERIFIED\nReason: 邻域支持。"},
        ]
        result = verify_atomic_facts(self.state([self.candidate()]))
        self.assertEqual(
            result["verified_atomic_facts"][0]["source_ranges"],
            [{"start": 1, "end": 3}],
        )

    @patch("src.agents.state_manager.state_manager.StateManager.repair_atomic_fact")
    @patch("src.agents.state_manager.state_manager.StateManager.verify_atomic_facts")
    def test_explicit_drop_is_not_index_candidate(self, verify, repair):
        verify.return_value = {
            "raw_analysis": "FACT 1\nDecision: INCORRECT\nReason: 原文没有。"
        }
        repair.return_value = {"raw_analysis": "DROP"}
        result = verify_atomic_facts(self.state([self.candidate()]))
        self.assertEqual(result["atomic_fact_count"], 0)
        self.assertEqual(result["verified_atomic_facts"], [])

    def test_active_error_is_deduplicated_by_stage(self):
        first = _derived_failure(
            {"chapter_index": 1, "warnings": [], "generation_events": []},
            "same", stage="verify_atomic_facts",
        )
        second = _derived_failure(
            {"chapter_index": 1, **first}, "same", stage="verify_atomic_facts",
        )
        self.assertEqual(second["derived_state_errors"], ["same"])
        self.assertEqual(second["warnings"], ["same"])


class TestRAGSourceRanges(DerivationProtocolCase):
    def test_multiple_ranges_expand_each_canonical_address(self):
        service = object.__new__(ChapterRetrievalService)
        service.fs = self.fs
        result = FactSearchResult(
            fact_id="FACT-0001-001",
            chapter_index=1,
            source_path="chapters/chapter_0001.md",
            source_ranges=[
                {"start": 1, "end": 1},
                {"start": 3, "end": 3},
            ],
            text="第一段与第三段共同支持该事实。",
        )
        excerpts = service._expand_sources([result], context_paragraphs=1)
        self.assertEqual(
            [(item.paragraph_start, item.paragraph_end) for item in excerpts],
            [(1, 2), (2, 3)],
        )
        self.assertIn("[P0001]", excerpts[0].text)
        self.assertIn("[P0003]", excerpts[1].text)

if __name__ == "__main__":
    unittest.main()
