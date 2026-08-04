"""E07.4 production runner for one resumable chapter execution."""

from __future__ import annotations

import sqlite3
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, StateSnapshot

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

    @staticmethod
    def _waiting_result(snapshot: StateSnapshot) -> dict[str, Any]:
        """Expose pending interrupts without creating a second resume store."""
        return {
            **dict(snapshot.values),
            "workflow_status": "WAITING_HUMAN",
            "interrupts": [
                {"id": pending.id, "value": pending.value}
                for pending in snapshot.interrupts
            ],
        }

    def _open_graph(self):
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.checkpoint_path,
            check_same_thread=False,
        )
        checkpointer = SqliteSaver(connection)
        return connection, build_chapter_workflow(checkpointer=checkpointer)

    def _result_or_interrupt(self, graph, result: Any) -> dict[str, Any]:
        snapshot = graph.get_state(self.config)
        if snapshot.interrupts:
            return self._waiting_result(snapshot)
        return dict(result)

    def run(
        self,
        chapter_outline: str = "",
        extra_instructions: str = "",
    ) -> dict[str, Any]:
        """Start, continue, or report a paused chapter execution."""
        connection, graph = self._open_graph()
        try:
            snapshot = graph.get_state(self.config)

            if snapshot.interrupts:
                print("  [LangGraph] Chapter workflow is waiting for human input.")
                return self._waiting_result(snapshot)
            if snapshot.values and snapshot.next:
                print(
                    "  [LangGraph] Resuming chapter workflow from checkpoint: "
                    + ", ".join(snapshot.next)
                )
                result = graph.invoke(None, config=self.config)
                return self._result_or_interrupt(graph, result)
            if snapshot.values:
                # A terminal checkpoint must never be replayed implicitly.
                print("  [LangGraph] Chapter workflow is already terminal; no nodes replayed.")
                return dict(snapshot.values)

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
            return self._result_or_interrupt(graph, result)
        finally:
            connection.close()

    def resume(self, resume_value: dict[str, Any]) -> dict[str, Any]:
        """Resume the pending human interrupt on this chapter thread."""
        if not isinstance(resume_value, dict):
            raise ValueError("Human resume value must be a decision object")
        action = str(resume_value.get("action", "")).strip().lower()
        if action not in ("acknowledge", "stop"):
            raise ValueError(
                "E07.5 resume action must be 'acknowledge' or 'stop'"
            )

        connection, graph = self._open_graph()
        try:
            snapshot = graph.get_state(self.config)
            if not snapshot.interrupts:
                raise ValueError(
                    "Chapter workflow has no pending human interrupt to resume"
                )
            print("  [LangGraph] Resuming chapter workflow with human input.")
            result = graph.invoke(
                Command(resume=resume_value),
                config=self.config,
            )
            return self._result_or_interrupt(graph, result)
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


def resume_chapter_workflow(
    novel_id: str,
    chapter_index: int,
    resume_value: dict[str, Any],
) -> dict[str, Any]:
    """Resume the existing chapter thread with one human decision."""
    return ChapterWorkflowRunner(novel_id, chapter_index).resume(resume_value)
