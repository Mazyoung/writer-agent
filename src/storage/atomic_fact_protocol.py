"""Mechanical Atomic Fact parsing, addressing, and verification protocols."""

from __future__ import annotations

from dataclasses import dataclass
import re

from src.storage.document_formats import AtomicFact


_RANGE = re.compile(
    r"^\s*P?\s*0*(\d+)\s*(?:-|~|—|–)\s*P?\s*0*(\d+)\s*$",
    re.IGNORECASE,
)
_SINGLE = re.compile(r"^\s*P?\s*0*(\d+)\s*$", re.IGNORECASE)


def chapter_paragraphs(text: str) -> list[str]:
    """Return the same one-based canonical paragraphs shown to SYSTEM LLMs."""
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def parse_source_ranges(value: str) -> list[dict[str, int]]:
    """Normalize harmless range syntax without interpreting prose descriptions."""
    raw = value.strip().strip("[]()（）")
    if not raw:
        raise ValueError("Atomic Fact source range is empty")
    ranges: list[dict[str, int]] = []
    segments = re.split(r"[,，;；/|]", raw)
    for segment in segments:
        segment = segment.strip()
        if not segment:
            raise ValueError(f"Unrecognized source range address: {value}")
        match = _RANGE.fullmatch(segment)
        if match:
            ranges.append({
                "start": int(match.group(1)),
                "end": int(match.group(2)),
            })
            continue
        single = _SINGLE.fullmatch(segment)
        if single:
            number = int(single.group(1))
            ranges.append({"start": number, "end": number})
            continue
        raise ValueError(f"Unrecognized source range address: {value}")
    return ranges


def format_source_ranges(ranges: list[dict[str, int]]) -> str:
    return "; ".join(
        f"P{item['start']:04d}-P{item['end']:04d}" for item in ranges
    )


def parse_atomic_facts(text: str, chapter_index: int) -> list[AtomicFact]:
    """Parse the new one-bullet/one-fact protocol and deterministic IDs."""
    section = re.search(
        r"^##\s+Atomic Facts\s*$\n(.*?)(?=^##\s|\Z)",
        text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    if section is None:
        raise ValueError("Atomic Fact response is missing '## Atomic Facts'")
    facts: list[AtomicFact] = []
    for raw in section.group(1).splitlines():
        line = raw.strip()
        if not line or line in {"- 无", "无", "- DROP", "DROP"}:
            continue
        if not line.startswith("-"):
            continue
        match = re.match(r"^-\s*\[([^\]]+)\]\s*(.+?)\s*$", line)
        if match is None:
            match = re.match(
                r"^-\s*((?:P?\s*\d+\s*(?:-|~|—|–)\s*P?\s*\d+)(?:\s*[,，;；/|]\s*P?\s*\d+\s*(?:-|~|—|–)\s*P?\s*\d+)*)\s+(.+?)\s*$",
                line,
                re.IGNORECASE,
            )
        if match is None:
            raise ValueError(f"Malformed Atomic Fact bullet: {line}")
        fact_text = match.group(2).strip()
        if not fact_text:
            raise ValueError("Atomic Fact text is empty")
        ranges = parse_source_ranges(match.group(1))
        facts.append(AtomicFact(
            fact_id=f"FACT-{chapter_index:04d}-{len(facts) + 1:03d}",
            chapter_index=chapter_index,
            source_ranges=ranges,
            fact_text=fact_text,
        ))
    if not facts:
        raise ValueError("Atomic Fact list is empty")
    return facts


def validate_source_ranges(fact: AtomicFact, paragraph_count: int) -> None:
    if not fact.source_ranges:
        raise ValueError(f"{fact.fact_id}: source_ranges is empty")
    for item in fact.source_ranges:
        start, end = int(item.get("start", 0)), int(item.get("end", 0))
        if start < 1:
            raise ValueError(f"{fact.fact_id}: source range start must be >= 1")
        if end < start:
            raise ValueError(f"{fact.fact_id}: source range end precedes start")
        if end > paragraph_count:
            raise ValueError(
                f"{fact.fact_id}: paragraph P{end:04d} does not exist "
                f"(chapter has {paragraph_count} paragraphs)"
            )


def source_excerpt(fact: AtomicFact, paragraphs: list[str]) -> str:
    validate_source_ranges(fact, len(paragraphs))
    blocks = []
    for item in fact.source_ranges:
        blocks.extend(
            f"[P{index:04d}] {paragraphs[index - 1]}"
            for index in range(item["start"], item["end"] + 1)
        )
    return "\n\n".join(blocks)


def expand_source_ranges(
    fact: AtomicFact, paragraph_count: int, radius: int = 1
) -> AtomicFact:
    """Expand addresses only; no story-language interpretation occurs here."""
    validate_source_ranges(fact, paragraph_count)
    expanded = []
    for item in fact.source_ranges:
        expanded.append({
            "start": max(1, item["start"] - radius),
            "end": min(paragraph_count, item["end"] + radius),
        })
    fact.source_ranges = expanded
    return fact


@dataclass(frozen=True)
class FactVerification:
    index: int
    decision: str
    reason: str = ""


def parse_verification_decisions(text: str, expected_count: int) -> list[FactVerification]:
    """Parse only explicit per-fact Decision fields; never scan free analysis."""
    matches = list(re.finditer(
        r"^FACT\s+(\d+)\s*$\n(.*?)(?=^FACT\s+\d+\s*$|\Z)",
        text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    ))
    parsed: list[FactVerification] = []
    for match in matches:
        body = match.group(2)
        decision = re.search(
            r"^\s*Decision\s*:\s*(VERIFIED|INCORRECT|INSUFFICIENT)\s*$",
            body,
            re.IGNORECASE | re.MULTILINE,
        )
        if decision is None:
            raise ValueError(f"FACT {match.group(1)} is missing an explicit Decision")
        reason = re.search(r"^\s*Reason\s*:\s*(.*?)\s*$", body, re.MULTILINE)
        parsed.append(FactVerification(
            index=int(match.group(1)),
            decision=decision.group(1).upper(),
            reason=reason.group(1).strip() if reason else "",
        ))
    if [item.index for item in parsed] != list(range(1, expected_count + 1)):
        raise ValueError("Fact Verification response does not cover each fact exactly once")
    return parsed
