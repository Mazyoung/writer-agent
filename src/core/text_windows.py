"""Shared complete-paragraph windows for recent prose context."""

from __future__ import annotations

import re

from src.storage.file_store import FileStore


def trailing_complete_paragraphs(text: str, target_chars: int = 1500) -> str:
    """Accumulate whole trailing paragraphs until the target is reached."""
    if target_chars <= 0:
        raise ValueError("target_chars 必须是正整数")
    paragraphs = [
        part.strip()
        for part in re.split(r"\n\s*\n", text or "")
        if part.strip()
    ]
    selected: list[str] = []
    length = 0
    for paragraph in reversed(paragraphs):
        selected.append(paragraph)
        length += len(paragraph)
        if length >= target_chars:
            break
    return "\n\n".join(reversed(selected))


def previous_chapter_end(
    file_store: FileStore,
    chapter_index: int,
    target_chars: int = 1500,
) -> str:
    if chapter_index <= 1:
        return ""
    prose = file_store.load_canonical_chapter(chapter_index - 1) or ""
    return trailing_complete_paragraphs(prose, target_chars)
