"""Shared fail-closed chapter entry guard."""

from __future__ import annotations

from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.storage.chapter_completion import is_derived_ready


def ensure_chapter_can_start(novel_id: str, chapter_index: int) -> None:
    fs = FileStore(novel_id, get_settings().data_dir)
    current = fs.canonical_chapter_path(chapter_index)
    if current.exists():
        if is_derived_ready(fs, chapter_index):
            raise ValueError(
                f"第 {chapter_index} 章已经完整完成（DERIVED_READY），"
                f"不能通过普通命令覆盖。\n下一章：第 {chapter_index + 1} 章"
            )
        raise ValueError(
            f"第 {chapter_index} 章正文已经 Canonical Commit，"
            "但正式 DERIVED_READY marker 尚未成立。\n\n"
            "在派生完成前不能继续后续章节。\n请执行：\n"
            f"python main.py repair-derivation {novel_id} "
            f"--chapter {chapter_index}"
        )
    if chapter_index <= 1:
        return
    previous = chapter_index - 1
    if not fs.canonical_chapter_path(previous).exists():
        raise ValueError(
            f"第 {previous} 章尚未 Canonical，不能开始第 {chapter_index} 章"
        )
    if not is_derived_ready(fs, previous):
        raise ValueError(
            f"第 {previous} 章正文已经 Canonical Commit，"
            "但正式 DERIVED_READY marker 尚未成立。\n\n"
            f"禁止开始第 {chapter_index} 章。请先执行：\n"
            f"python main.py repair-derivation {novel_id} --chapter {previous}"
        )
