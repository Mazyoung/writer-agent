"""Persistent runner for one checkpointed E07.6 chapter execution."""

from __future__ import annotations

import sqlite3
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, StateSnapshot

from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.storage.document_formats import ChapterPlan
from src.workflows.chapter_workflow import build_chapter_workflow


class ChapterWorkflowRunner:
    """Start or resume one chapter on its deterministic LangGraph thread."""

    def __init__(self, novel_id: str, chapter_index: int):
        self.novel_id = novel_id
        self.chapter_index = chapter_index
        settings = get_settings()
        self.file_store = FileStore(novel_id, settings.data_dir)
        self.checkpoint_path = (
            settings.data_dir / "novels" / novel_id / "workflow_checkpoints.sqlite"
        )

    @property
    def thread_id(self) -> str:
        return f"chapter:{self.novel_id}:{self.chapter_index:04d}"

    @property
    def config(self) -> dict[str, Any]:
        return {"configurable": {"thread_id": self.thread_id}}

    @staticmethod
    def _waiting_result(snapshot: StateSnapshot) -> dict[str, Any]:
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
        connection = sqlite3.connect(self.checkpoint_path, check_same_thread=False)
        checkpointer = SqliteSaver(connection)
        return connection, checkpointer, build_chapter_workflow(
            checkpointer=checkpointer)

    def _result_or_interrupt(self, graph, result: Any) -> dict[str, Any]:
        snapshot = graph.get_state(self.config)
        if snapshot.interrupts:
            return self._waiting_result(snapshot)
        return dict(result)

    def run(
        self,
        chapter_outline: str = "",
        extra_instructions: str = "",
        chapter_intent: str = "",
    ) -> dict[str, Any]:
        """Start, continue, or report a paused chapter execution."""
        connection, checkpointer, graph = self._open_graph()
        try:
            if self.file_store.canonical_chapter_path(self.chapter_index).exists():
                return {
                    "workflow_status": "error",
                    "error": (
                        f"ERROR_ALREADY_EXISTS: 第{self.chapter_index}章已完成，"
                        "普通 Generate 禁止覆盖"),
                }

            snapshot = graph.get_state(self.config)
            if snapshot.interrupts:
                print("  [LangGraph] Chapter workflow is waiting for human input.")
                return self._waiting_result(snapshot)
            if snapshot.values and snapshot.next:
                print(
                    "  [LangGraph] Resuming chapter workflow from checkpoint: "
                    + ", ".join(snapshot.next)
                )
                return self._result_or_interrupt(
                    graph, graph.invoke(None, config=self.config)
                )
            if snapshot.values:
                terminal_status = str(
                    snapshot.values.get("workflow_status", "")
                ).upper()
                if terminal_status in {"ERROR", "STOPPED_NON_PASS", "DISCARDED"}:
                    checkpointer.delete_thread(self.thread_id)
                    print(
                        "  [LangGraph] Cleared retryable terminal checkpoint; "
                        "starting a new chapter execution."
                    )
                else:
                    print("  [LangGraph] Chapter workflow is already terminal; no nodes replayed.")
                    return dict(snapshot.values)

            initial_state = {
                "novel_id": self.novel_id,
                "branch_id": "main",
                "chapter_index": self.chapter_index,
                "chapter_outline": chapter_outline,
                "extra_instructions": extra_instructions,
                "chapter_intent": chapter_intent,
                "workflow_status": "running",
                "warnings": [],
            }
            print("  [LangGraph] Starting checkpointed chapter workflow.")
            return self._result_or_interrupt(
                graph, graph.invoke(initial_state, config=self.config)
            )
        finally:
            connection.close()

    def _load_human_edit(self, interrupt_value: dict[str, Any]) -> str:
        edit_path = str(interrupt_value.get("edit_path", "")).strip()
        if not edit_path:
            raise ValueError("Pending interrupt does not declare an edit path")
        path = self.file_store.root / edit_path
        if not path.exists():
            raise ValueError(f"Human edit file does not exist: {edit_path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"Human edit file is empty: {edit_path}")

        interrupt_type = interrupt_value.get("type")
        if interrupt_type == "plan_review":
            plan = ChapterPlan.from_markdown(text)
            if plan.chapter_index != self.chapter_index or not plan.scenes:
                raise ValueError(
                    "Human-edited Chapter Plan is invalid or targets another chapter"
                )
        elif interrupt_type not in {"chapter_review", "final_author_approval"}:
            raise ValueError(f"Unsupported pending interrupt type: {interrupt_type}")
        return text

    def _discard_candidate(self, checkpointer: SqliteSaver) -> list[str]:
        """Delete only this pre-canonical execution; preserve Chapter Intent."""
        if self.file_store.canonical_chapter_path(self.chapter_index).exists():
            raise ValueError("discard is forbidden after canonical commit")
        removed = []
        patterns = [
            f"chapters/chapter_{self.chapter_index:04d}_styled*.md",
            f"outlines/chapter_plan_ch{self.chapter_index:04d}*.md",
            f"states/review_ch{self.chapter_index:04d}_*.md",
            f"states/derivation_ch{self.chapter_index:04d}_*.md",
            f"states/fact_digest_ch{self.chapter_index:04d}_*.md",
        ]
        for pattern in patterns:
            for path in self.file_store.root.glob(pattern):
                path.unlink()
                removed.append(str(path.relative_to(self.file_store.root)))
        checkpointer.delete_thread(self.thread_id)
        return removed

    def resume(self, resume_value: dict[str, Any]) -> dict[str, Any]:
        """Resume the existing interrupt with a validated human edit or stop."""
        if not isinstance(resume_value, dict):
            raise ValueError("Human resume value must be a decision object")
        action = str(resume_value.get("action", "")).strip().lower()

        connection, checkpointer, graph = self._open_graph()
        try:
            snapshot = graph.get_state(self.config)
            if not snapshot.interrupts:
                raise ValueError("Chapter workflow has no pending human interrupt to resume")
            if len(snapshot.interrupts) != 1:
                raise ValueError("Chapter workflow has an unexpected number of interrupts")

            pending = snapshot.interrupts[0].value
            if action == "edit" and pending.get("type") in {
                "chapter_review", "final_author_approval"
            }:
                action = "manual_edit"
            allowed = set(pending.get("allowed_actions", []))
            if action not in allowed:
                raise ValueError(
                    f"Action '{action}' is not allowed for {pending.get('type', 'interrupt')}"
                )
            if action == "pause":
                return self._waiting_result(snapshot)
            if action == "discard":
                return {
                    "workflow_status": "DISCARDED",
                    "removed_candidates": self._discard_candidate(checkpointer),
                    "chapter_intent_preserved": True,
                }
            command_value = {
                "action": action,
                "feedback": str(resume_value.get("feedback", "")).strip(),
            }
            # Validate before Command(resume=...) so a bad/missing edit does not
            # consume the pending checkpoint interrupt.
            if action in {"edit", "manual_edit"}:
                command_value["edited_text"] = self._load_human_edit(pending)

            print("  [LangGraph] Resuming chapter workflow with human input.")
            result = graph.invoke(
                Command(resume=command_value),
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
    chapter_intent: str = "",
) -> dict[str, Any]:
    return ChapterWorkflowRunner(novel_id, chapter_index).run(
        chapter_outline=chapter_outline,
        extra_instructions=extra_instructions,
        chapter_intent=chapter_intent,
    )


def resume_chapter_workflow(
    novel_id: str,
    chapter_index: int,
    resume_value: dict[str, Any],
) -> dict[str, Any]:
    return ChapterWorkflowRunner(novel_id, chapter_index).resume(resume_value)
