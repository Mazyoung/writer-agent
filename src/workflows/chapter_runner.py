"""E07.4 production runner for one resumable chapter execution."""

from __future__ import annotations

import sqlite3
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from src.config.settings import get_settings
from src.workflows.chapter_workflow import build_chapter_workflow


class ChapterWorkflowRunner:
    """Run or resume one chapter using a persistent LangGraph checkpoint.

    A deterministic thread id maps one novel/chapter pair to one execution.
    This is workflow recovery only; it is not story rollback or savepoints.
    """

    def __init__(self, novel_id: str, chapter_index: int):
        self.novel_id = novel_id
        self.chapter_index = chapter_index
        settings = get_settings()
        self.checkpoint_path = (
            settings.data_dir / "novels" / novel_id / "workflow_checkpoints.sqlite"
        )

    @property
    def thread_id(self) -> str:
        return f"chapter:{self.novel_id}:{self.chapter_index:04d}"

    @property
    def config(self) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": self.thread_id,
            }
        }

    def run(
        self,
        chapter_outline: str = "",
        extra_instructions: str = "",
    ) -> dict[str, Any]:
        """Start a new execution or resume the last incomplete checkpoint."""
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.checkpoint_path,
            check_same_thread=False,
        )
        try:
            checkpointer = SqliteSaver(connection)
            graph = build_chapter_workflow(checkpointer=checkpointer)
            snapshot = graph.get_state(self.config)

            if snapshot.values and snapshot.next:
                print(
                    "  [LangGraph] Resuming chapter workflow from checkpoint: "
                    + ", ".join(snapshot.next)
                )
                result = graph.invoke(None, config=self.config)
            elif snapshot.values:
                # A terminal checkpoint must never be replayed implicitly.
                result = dict(snapshot.values)
                print("  [LangGraph] Chapter workflow is already terminal; no nodes replayed.")
            else:
                initial_state = {
                    "novel_id": self.novel_id,
                    "branch_id": "main",
                    "chapter_index": self.chapter_index,
                    "chapter_outline": chapter_outline,
                    "extra_instructions": extra_instructions,
                    "workflow_status": "running",
                    "warnings": [],
                }
                print("  [LangGraph] Starting checkpointed chapter workflow.")
                result = graph.invoke(initial_state, config=self.config)

            return dict(result)
        finally:
            connection.close()


def run_chapter_workflow(
    novel_id: str,
    chapter_index: int,
    chapter_outline: str = "",
    extra_instructions: str = "",
) -> dict[str, Any]:
    """Convenience production entry point used by the CLI."""
    return ChapterWorkflowRunner(novel_id, chapter_index).run(
        chapter_outline=chapter_outline,
        extra_instructions=extra_instructions,
    )
