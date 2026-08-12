import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config.settings import get_settings
from src.storage.atomic_fact_protocol import (
    expand_source_ranges,
    parse_atomic_facts,
    source_excerpt,
)
from src.storage.file_store import FileStore
from src.workflows.chapter_workflow import persist_fact_digest, verify_atomic_facts


class AtomicFactRetryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = get_settings()
        old = settings.data_dir
        settings.data_dir = Path(self.tmp.name)
        self.addCleanup(setattr, settings, "data_dir", old)
        self.fs = FileStore("retry", settings.data_dir)
        self.canonical = "????\n\n????\n\n????\n\n????"
        self.fs.commit_canonical_chapter(1, self.canonical)

    def state(self):
        return {
            "novel_id": "retry",
            "chapter_index": 1,
            "generation_events": [],
            "warnings": [],
        }

    @patch("src.agents.state_manager.state_manager.StateManager.derive_atomic_facts")
    def test_deriver_missing_provenance_retries_once_then_succeeds(self, derive):
        derive.side_effect = [
            {"raw_analysis": "## Atomic Facts\n\n- ????"},
            {"raw_analysis": "## Atomic Facts\n\n- [P0002] ????"},
        ]
        result = persist_fact_digest(self.state())
        self.assertEqual(result["workflow_status"], "ATOMIC_FACTS_DERIVED")
        self.assertEqual(
            result["atomic_fact_candidates"][0]["source_ranges"],
            [{"start": 2, "end": 2}],
        )
        self.assertIn("Malformed Atomic Fact bullet", derive.call_args.kwargs[
            "protocol_correction"
        ])
        self.assertIn("Previous raw output", derive.call_args.kwargs[
            "protocol_correction"
        ])
        self.assertEqual(derive.call_count, 2)

    @patch("src.agents.state_manager.state_manager.StateManager.derive_atomic_facts")
    def test_deriver_two_invalid_outputs_fails_closed_at_derive(self, derive):
        derive.return_value = {"raw_analysis": "## Atomic Facts\n\n- ????"}
        canonical_before = self.fs.load_canonical_chapter(1)
        result = persist_fact_digest(self.state())
        self.assertEqual(result["workflow_status"], "DERIVATION_ERROR")
        self.assertEqual(result["failed_derivation_stage"], "derive_atomic_facts")
        self.assertEqual(derive.call_count, 2)
        self.assertEqual(self.fs.load_canonical_chapter(1), canonical_before)
        self.assertNotIn("atomic_fact_candidates", result)

    @patch("src.agents.state_manager.state_manager.StateManager.repair_atomic_fact")
    @patch("src.agents.state_manager.state_manager.StateManager.verify_atomic_facts")
    def test_targeted_repair_missing_provenance_retries_once(self, verify, repair):
        verify.side_effect = [
            {"raw_analysis": "FACT 1\nDecision: INCORRECT\nReason: wrong"},
            {"raw_analysis": "FACT 1\nDecision: VERIFIED\nReason: supported"},
        ]
        repair.side_effect = [
            {"raw_analysis": "- ????"},
            {"raw_analysis": "- [P0002] ????"},
        ]
        state = {
            **self.state(),
            "atomic_fact_candidates": [{
                "fact_id": "FACT-0001-001",
                "chapter_index": 1,
                "source_ranges": [{"start": 2, "end": 2}],
                "fact_text": "????",
                "repair_used": False,
            }],
        }
        result = verify_atomic_facts(state)
        self.assertEqual(result["workflow_status"], "FACT_DIGEST_PERSISTED")
        self.assertEqual(result["verified_atomic_facts"][0]["fact_text"], "????")
        self.assertEqual(repair.call_count, 2)
        self.assertIn("Previous raw output", repair.call_args.kwargs[
            "protocol_correction"
        ])

    @patch("src.agents.state_manager.state_manager.StateManager.repair_atomic_fact")
    @patch("src.agents.state_manager.state_manager.StateManager.verify_atomic_facts")
    def test_targeted_repair_two_invalid_outputs_fails_closed(self, verify, repair):
        verify.return_value = {
            "raw_analysis": "FACT 1\nDecision: INCORRECT\nReason: wrong"
        }
        repair.return_value = {"raw_analysis": "- ????"}
        state = {
            **self.state(),
            "atomic_fact_candidates": [{
                "fact_id": "FACT-0001-001", "chapter_index": 1,
                "source_ranges": [{"start": 2, "end": 2}],
                "fact_text": "????", "repair_used": False,
            }],
        }
        result = verify_atomic_facts(state)
        self.assertEqual(result["workflow_status"], "DERIVATION_ERROR")
        self.assertEqual(result["failed_derivation_stage"], "verify_atomic_facts")
        self.assertEqual(repair.call_count, 2)

    def test_compound_ranges_reach_excerpt_and_each_expansion(self):
        fact = parse_atomic_facts(
            "## Atomic Facts\n\n- [P0001; P0003-P0004] ?????", 1
        )[0]
        excerpt = source_excerpt(fact, self.canonical.split("\n\n"))
        self.assertIn("[P0001] ????", excerpt)
        self.assertIn("[P0003] ????", excerpt)
        self.assertIn("[P0004]", excerpt)
        expanded = expand_source_ranges(fact, 4)
        self.assertEqual(expanded.source_ranges, [
            {"start": 1, "end": 2}, {"start": 2, "end": 4}
        ])
