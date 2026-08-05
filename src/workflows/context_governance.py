"""Planning context limits, provenance hashes, and lightweight metrics."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

PLANNING_RECOMMENDED_CHARS = 8000
PLANNING_HARD_LIMIT_CHARS = 10000
HIGH_CONTEXT_CHARS = 30000


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_version(text: str) -> str:
    match = re.search(r"\*\*版本\*\*\s*[:：]\s*([^\n]+)", text)
    return match.group(1).strip() if match else "unversioned"


def validate_planning_document(name: str, text: str) -> list[str]:
    length = len(text)
    if length > PLANNING_HARD_LIMIT_CHARS:
        raise ValueError(
            f"{name} exceeds hard limit: {length} > {PLANNING_HARD_LIMIT_CHARS} chars"
        )
    if length >= PLANNING_RECOMMENDED_CHARS:
        return [
            f"{name} is {length} chars; recommended maximum is "
            f"{PLANNING_RECOMMENDED_CHARS}"
        ]
    return []


def validate_planning_context(
    world_setting: str, book_plan: str, volume_plan: str,
) -> list[str]:
    warnings = []
    for name, text in (
        ("world_setting.md", world_setting),
        ("book_plan.md", book_plan),
        ("volume_plan.md", volume_plan),
    ):
        warnings.extend(validate_planning_document(name, text))
    return warnings


def estimate_tokens(chars: int) -> int:
    # Diagnostic estimate only; no provider tokenizer/network call.
    return (chars + 2) // 3


def write_context_metrics(
    root: Path,
    chapter_index: int,
    stage: str,
    parts: dict[str, str],
) -> tuple[str, list[str]]:
    metrics_dir = root / "tracking" / "context_metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    total = sum(len(text) for text in parts.values())
    warnings = []
    if total >= HIGH_CONTEXT_CHARS:
        warnings.append(
            f"{stage} context is high: {total} chars / ~{estimate_tokens(total)} tokens"
        )
    lines = [
        f"# Chapter {chapter_index} {stage} Context Metrics", "",
        "| Part | Chars | Estimated Tokens |", "|---|---:|---:|",
    ]
    for name, text in parts.items():
        lines.append(f"| {name} | {len(text)} | {estimate_tokens(len(text))} |")
    lines.extend([
        f"| **Total** | **{total}** | **{estimate_tokens(total)}** |", "",
        "## Warnings",
        *(f"- {warning}" for warning in warnings),
    ])
    if not warnings:
        lines.append("- None")
    path = metrics_dir / f"chapter_{chapter_index:04d}_{stage.lower()}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path.relative_to(root)).replace("\\", "/"), warnings
