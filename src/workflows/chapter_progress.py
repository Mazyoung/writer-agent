"""Shared fail-closed chapter entry guard."""

from __future__ import annotations

from src.config.settings import get_settings
from src.storage.file_store import FileStore


def _status(novel_id: str, chapter_index: int) -> str:
    from src.workflows.chapter_runner import ChapterWorkflowRunner

    return ChapterWorkflowRunner(novel_id, chapter_index).get_workflow_status()


def ensure_chapter_can_start(novel_id: str, chapter_index: int) -> None:
    fs = FileStore(novel_id, get_settings().data_dir)
    current = fs.canonical_chapter_path(chapter_index)
    if current.exists():
        status = _status(novel_id, chapter_index) or "UNKNOWN"
        if status == "DERIVED_READY":
            raise ValueError(
                f"第 {chapter_index} 章已经完整完成（DERIVED_READY），"
                f"不能通过普通命令覆盖。\n下一章：第 {chapter_index + 1} 章"
            )
        raise ValueError(
            f"第 {chapter_index} 章正文已经 Canonical Commit，"
            f"但派生过程尚未完成。\n当前状态：{status}\n\n"
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
    status = _status(novel_id, previous) or "UNKNOWN"
    if status != "DERIVED_READY":
        raise ValueError(
            f"第 {previous} 章正文已经 Canonical Commit，"
            f"但派生过程尚未完成。\n当前状态：{status}\n\n"
            f"禁止开始第 {chapter_index} 章。请先执行：\n"
            f"python main.py repair-derivation {novel_id} --chapter {previous}"
        )
