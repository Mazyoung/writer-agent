from __future__ import annotations

import unittest

from src.storage.atomic_fact_protocol import (
    expand_source_ranges,
    format_source_ranges,
    parse_atomic_facts,
    parse_source_ranges,
    source_excerpt,
)
from src.storage.document_formats import AtomicFact


class CompoundSourceRangeTests(unittest.TestCase):
    def test_formatter_uses_canonical_paragraph_addresses(self):
        cases = (
            ([{"start": 23, "end": 25}], "P0023-P0025"),
            ([{"start": 13, "end": 13}], "P0013"),
            ([
                {"start": 4, "end": 6},
                {"start": 13, "end": 13},
                {"start": 20, "end": 22},
            ], "P0004-P0006; P0013; P0020-P0022"),
            ([], ""),
        )
        for ranges, expected in cases:
            with self.subTest(ranges=ranges):
                self.assertEqual(expected, format_source_ranges(ranges))

    def test_single_and_compound_addresses_canonicalize_to_range_list(self):
        cases = {
            "P0004": [{"start": 4, "end": 4}],
            "P0004-P0006": [{"start": 4, "end": 6}],
            "P0004-P0006; P0013": [
                {"start": 4, "end": 6}, {"start": 13, "end": 13},
            ],
            "P0004; P0013-P0015": [
                {"start": 4, "end": 4}, {"start": 13, "end": 15},
            ],
            " P0004-P0006 ； P0013 ": [
                {"start": 4, "end": 6}, {"start": 13, "end": 13},
            ],
        }
        for address, expected in cases.items():
            with self.subTest(address=address):
                self.assertEqual(expected, parse_source_ranges(address))

    def test_atomic_fact_list_accepts_plain_and_compound_items(self):
        facts = parse_atomic_facts(
            "## Atomic Facts\n\n"
            "- [P0001] 第一条。\n"
            "- [P0004-P0006; P0013] 第二条。",
            4,
        )
        self.assertEqual(
            facts[1].source_ranges,
            [{"start": 4, "end": 6}, {"start": 13, "end": 13}],
        )

    def test_all_ranges_enter_excerpt_and_radius_expansion(self):
        paragraphs = [f"第{index}段" for index in range(1, 17)]
        fact = AtomicFact(
            fact_id="FACT-0004-001",
            chapter_index=4,
            source_ranges=parse_source_ranges("P0004-P0006; P0013"),
            fact_text="多处原文共同支持。",
        )
        excerpt = source_excerpt(fact, paragraphs)
        self.assertIn("[P0004]", excerpt)
        self.assertIn("[P0006]", excerpt)
        self.assertIn("[P0013]", excerpt)

        expanded = expand_source_ranges(fact, len(paragraphs))
        self.assertEqual(
            expanded.source_ranges,
            [{"start": 3, "end": 7}, {"start": 12, "end": 14}],
        )

    def test_malformed_segment_fails_closed_without_dropping_later_ranges(self):
        for value in (
            "P0004-P0006; 医院那一段",
            "P0004-P0006;; P0013",
            "P0004-P0006; P0013-extra",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "Unrecognized source range address"
            ):
                parse_source_ranges(value)


if __name__ == "__main__":
    unittest.main()
