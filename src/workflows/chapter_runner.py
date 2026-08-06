"""Persistent runner for one checkpointed E07.6 chapter execution."""

from __future__ import annotations

import re
import sqlite3
from functools import wraps
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command, StateSnapshot

from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.storage.document_formats import ChapterPlan
from src.storage.story_savepoint import (
    NovelOperationLock,
    StorySavepointManager,
)
from src.workflows.chapter_workflow import build_chapter_workflow


def _novel_mutation_locked(method):
    """Prevent chapter production from racing savepoint create/load."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with NovelOperationLock(self.file_store.root):
            result = method(self, *args, **kwargs)
        return _maybe_create_auto_savepoint(self, result)
    return wrapped


def _maybe_create_auto_savepoint(
    runner: "ChapterWorkflowRunner", result: dict[str, Any]
) -> dict[str, Any]:
    """Create an interval Savepoint after the chapter operation lock is released."""
    if not isinstance(result, dict) or str(
        result.get("workflow_status", "")
    ).upper() != "DERIVED_READY":
        return result
    interval = get_settings().auto_savepoint_every
    if interval == 0 or runner.chapter_index % interval != 0:
        return result

    savepoint_id = f"S{runner.chapter_index:04d}"
    enriched = dict(result)
    try:
        manager = StorySavepointManager(runner.novel_id, get_settings().data_dir)
        target = manager.savepoints_root / savepoint_id
        if target.exists():
            manifest = manager.verify(savepoint_id)
            outcome = "EXISTING_READY"
        else:
            manifest = manager.create()
            outcome = "CREATED"
    except Exception as exc:
        # A concurrent creator may have won between exists() and create(). Only
        # a fully verified READY snapshot is accepted as idempotent success.
        try:
            manifest = manager.verify(savepoint_id) if "manager" in locals() else None
            if manifest is None:
                raise exc
            outcome = "EXISTING_READY"
        except Exception:
            message = f"{type(exc).__name__}: {exc}"
            enriched["auto_savepoint"] = {
                "status": "ERROR",
                "savepoint_id": savepoint_id,
                "error": message,
            }
            print(f"  [AUTO SAVEPOINT ERROR] {savepoint_id} 创建失败：{message}")
            return enriched

    enriched["auto_savepoint"] = {
        "status": outcome,
        "savepoint_id": manifest["savepoint_id"],
    }
    if outcome == "CREATED":
        print(f"  [AUTO SAVEPOINT] 已创建 {savepoint_id}（READY）。")
    else:
        print(f"  [AUTO SAVEPOINT] {savepoint_id} 已是有效 READY，视为完成。")
    return enriched


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

    def get_workflow_status(self) -> str:
        """Return durable completion first, otherwise the execution status."""
        from src.storage.chapter_completion import is_derived_ready

        if self.file_store.canonical_chapter_path(self.chapter_index).is_file():
            if is_derived_ready(self.file_store, self.chapter_index):
                return "DERIVED_READY"
        connection, _checkpointer, graph = self._open_graph()
        try:
            snapshot = graph.get_state(self.config)
            return str(snapshot.values.get("workflow_status", "")).upper()
        finally:
            connection.close()

    def inspect(self) -> dict[str, Any]:
        """Return durable checkpoint state without advancing it."""
        connection, _checkpointer, graph = self._open_graph()
        try:
            snapshot = graph.get_state(self.config)
            return {
                "values": dict(snapshot.values),
                "next": list(snapshot.next),
                "interrupts": [
                    {"id": item.id, "value": item.value}
                    for item in snapshot.interrupts
                ],
            }
        finally:
            connection.close()

    @_novel_mutation_locked
    def run(
        self,
        chapter_outline: str = "",
        extra_instructions: str = "",
        chapter_intent: str = "",
    ) -> dict[str, Any]:
        """Start, continue, or report a paused chapter execution."""
        from src.workflows.chapter_progress import ensure_chapter_can_start

        ensure_chapter_can_start(self.novel_id, self.chapter_index)
        connection, checkpointer, graph = self._open_graph()
        try:
            snapshot = graph.get_state(self.config)
            if snapshot.interrupts:
                print("  [LangGraph] 章节工作流正在等待人工输入。")
                return self._waiting_result(snapshot)
            if snapshot.values and snapshot.next:
                print(
                    "  [LangGraph] 正在从 checkpoint 恢复章节工作流："
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
                        "  [LangGraph] 已清除可重试的终态 checkpoint；"
                        "正在开始新的章节执行。"
                    )
                else:
                    print("  [LangGraph] 章节工作流已结束，不会重复执行节点。")
                    return dict(snapshot.values)

            initial_state = {
                "novel_id": self.novel_id,
                "branch_id": "main",
                "chapter_index": self.chapter_index,
                "chapter_outline": chapter_outline,
                "extra_instructions": extra_instructions,
                "chapter_intent": chapter_intent,
                # Freeze the resolved mode into the first checkpoint. Resume never
                # consults later environment/config changes for this execution.
                "chapter_mode": get_settings().chapter_mode,
                "agent_execution": get_settings().agent_execution,
                "workflow_status": "running",
                "warnings": [],
            }
            print("  [LangGraph] 正在启动 checkpointed 章节工作流。")
            return self._result_or_interrupt(
                graph, graph.invoke(initial_state, config=self.config)
            )
        finally:
            connection.close()

    def _load_human_edit(self, interrupt_value: dict[str, Any]) -> str:
        edit_path = str(interrupt_value.get("edit_path", "")).strip()
        if not edit_path:
            raise ValueError("待处理 interrupt 未提供编辑文件路径")
        path = self.file_store.root / edit_path
        if not path.exists():
            raise ValueError(f"人工编辑文件不存在：{edit_path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"人工编辑文件为空：{edit_path}")

        interrupt_type = interrupt_value.get("type")
        if interrupt_type == "plan_review":
            plan = ChapterPlan.from_markdown(text)
            if plan.chapter_index != self.chapter_index or not plan.scenes:
                raise ValueError(
                    "人工编辑的 Chapter Plan 无效或指向其他章节"
                )
        elif interrupt_type not in {
            "chapter_review", "final_author_approval", "human_final_approval"
        }:
            raise ValueError(f"不支持的待处理 interrupt 类型：{interrupt_type}")
        return text

    def _load_candidate_file(
        self, candidate_file: str, interrupt_value: dict[str, Any]
    ) -> str:
        """在消费 human_writing interrupt 前验证人工正文文件。"""
        if interrupt_value.get("type") != "human_writing":
            raise ValueError("submit 仅可用于 human_writing 阶段")
        if (
            interrupt_value.get("novel_id") != self.novel_id
            or interrupt_value.get("chapter_index") != self.chapter_index
        ):
            raise ValueError("待处理的人工写作 checkpoint 与 novel/chapter 不匹配")
        raw_path = str(candidate_file or "").strip()
        if not raw_path:
            raise ValueError("submit 必须通过 --file 指定人工正文 Candidate")
        path = Path(raw_path).expanduser().resolve()
        novels_root = (get_settings().data_dir / "novels").resolve()
        try:
            relative_novel = path.relative_to(novels_root)
        except ValueError:
            relative_novel = None
        if relative_novel is not None and (
            not relative_novel.parts or relative_novel.parts[0] != self.novel_id
        ):
            raise ValueError("禁止从其他 novel 目录提交正文 Candidate")
        chapter_match = re.search(r"chapter[_-]?0*(\d+)", path.stem, re.IGNORECASE)
        if chapter_match and int(chapter_match.group(1)) != self.chapter_index:
            raise ValueError(
                f"Candidate 文件名指向第 {int(chapter_match.group(1))} 章，"
                f"当前 checkpoint 是第 {self.chapter_index} 章"
            )
        if not path.is_file():
            raise ValueError(f"人工正文 Candidate 文件不存在: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"人工正文 Candidate 文件为空: {path}")
        return text

    def _discard_candidate(self, checkpointer: SqliteSaver) -> list[str]:
        """Delete only this pre-canonical execution; preserve Chapter Intent."""
        if self.file_store.canonical_chapter_path(self.chapter_index).exists():
            raise ValueError("Canonical commit 后禁止 restart")
        removed = []
        patterns = [
            f"chapters/chapter_{self.chapter_index:04d}_draft*.md",
            f"chapters/chapter_{self.chapter_index:04d}_revision*.md",
            f"chapters/chapter_{self.chapter_index:04d}_styled*.md",
            f"chapters/chapter_{self.chapter_index:04d}_human_candidate*.md",
            f"chapters/scene_ch{self.chapter_index:04d}_*.md",
            f"outlines/chapter_plan_ch{self.chapter_index:04d}*.md",
            f"outlines/scene_plan_ch{self.chapter_index:04d}*.md",
            f"tracking/writing_context_ch{self.chapter_index:04d}.md",
            f"tracking/rag_traces/retrieval_trace_ch{self.chapter_index:04d}_*.json",
            f"states/review_ch{self.chapter_index:04d}_*.md",
            f"states/consistency_review_ch{self.chapter_index:04d}_*.md",
            f"states/derivation_ch{self.chapter_index:04d}_*.md",
            f"states/fact_digest_ch{self.chapter_index:04d}_*.md",
        ]
        for pattern in patterns:
            for path in self.file_store.root.glob(pattern):
                path.unlink()
                removed.append(str(path.relative_to(self.file_store.root)))
        checkpointer.delete_thread(self.thread_id)
        return removed

    @_novel_mutation_locked
    def resume(self, resume_value: dict[str, Any]) -> dict[str, Any]:
        """Resume the existing interrupt with a validated human edit or stop."""
        if not isinstance(resume_value, dict):
            raise ValueError("人工 resume 值必须是决策对象")
        action = str(resume_value.get("action", "")).strip().lower()

        connection, checkpointer, graph = self._open_graph()
        try:
            snapshot = graph.get_state(self.config)
            if not snapshot.interrupts:
                raise ValueError("Chapter workflow 没有可 resume 的待处理人工 interrupt")
            if len(snapshot.interrupts) != 1:
                raise ValueError("Chapter workflow 的 interrupt 数量异常")

            pending = snapshot.interrupts[0].value
            if (
                pending.get("novel_id", self.novel_id) != self.novel_id
                or pending.get("chapter_index", self.chapter_index) != self.chapter_index
            ):
                raise ValueError("待处理 checkpoint 与当前 novel/chapter 不匹配")
            allowed = set(pending.get("allowed_actions", []))
            if action not in allowed:
                raise ValueError(
                    f"操作 '{action}' 不适用于 {pending.get('type', 'interrupt')}"
                )
            if action == "restart":
                return {
                    "workflow_status": "RESTARTED",
                    "removed_candidates": self._discard_candidate(checkpointer),
                    "chapter_intent_preserved": True,
                }
            command_value = {
                "action": action,
                "feedback": str(resume_value.get("feedback", "")).strip(),
            }
            # Validate before Command(resume=...) so a bad/missing edit does not
            # consume the pending checkpoint interrupt.
            if action == "human_edit":
                command_value["edited_text"] = self._load_human_edit(pending)
            if action == "submit":
                command_value["candidate_text"] = self._load_candidate_file(
                    str(resume_value.get("candidate_file", "")), pending
                )

            if action == "submit":
                print(f"  已接收第 {self.chapter_index} 章人工正文 Candidate。")
                print("  正在执行一致性检查……")
            else:
                print("  [LangGraph] 正在使用人工输入恢复章节工作流。")
            result = graph.invoke(
                Command(resume=command_value),
                config=self.config,
            )
            return self._result_or_interrupt(graph, result)
        finally:
            connection.close()


    @_novel_mutation_locked
    def restart(self) -> dict[str, Any]:
        """Discard one pre-Canonical execution using the shared reset primitive."""
        connection, checkpointer, _graph = self._open_graph()
        try:
            return {
                "workflow_status": "RESTARTED",
                "removed_candidates": self._discard_candidate(checkpointer),
                "chapter_intent_preserved": True,
            }
        finally:
            connection.close()


    @_novel_mutation_locked
    def repair_derivation(self) -> dict[str, Any]:
        """Resume only the first incomplete post-canonical derivation stage."""
        from src.storage.chapter_completion import is_derived_ready

        connection, _checkpointer, graph = self._open_graph()
        try:
            if not self.file_store.canonical_chapter_path(self.chapter_index).exists():
                raise ValueError("repair-derivation 需要 Canonical 正文")
            if is_derived_ready(self.file_store, self.chapter_index):
                return {
                    "workflow_status": "DERIVED_READY",
                    "chapter_index": self.chapter_index,
                }

            snapshot = graph.get_state(self.config)
            values = dict(snapshot.values)
            if not values or values.get("commit_success") is not True:
                raise ValueError(
                    "Canonical 已存在，但缺少可判断 Derivation 恢复位置的 "
                    "LangGraph checkpoint；为避免重复或跳过派生阶段，系统已 fail-closed"
                )
            status = str(values.get("workflow_status", "")).upper()
            legal = {
                "CANONICAL_COMMITTED",
                "SEMANTICS_DERIVED",
                "CURRENT_STATE_PERSISTED",
                "FACT_DIGEST_PERSISTED",
                "VOLUME_PROGRESS_PERSISTED",
                "CHAPTER_SOURCES_PERSISTED",
                "DERIVATION_ERROR",
            }
            if status not in legal:
                raise ValueError(
                    f"Canonical 已存在，但 checkpoint 状态 {status or 'UNKNOWN'} "
                    "不是可安全恢复的 Derivation 状态"
                )

            if status != "DERIVATION_ERROR" and snapshot.next:
                print(
                    "  [LangGraph] 正在从合法 Derivation checkpoint 继续："
                    + ", ".join(snapshot.next)
                )
                return self._result_or_interrupt(
                    graph, graph.invoke(None, config=self.config)
                )

            if not values.get("derivation_raw_analysis"):
                as_node, status = "commit_canonical_prose", "CANONICAL_COMMITTED"
            elif values.get("current_state_persisted") is not True:
                as_node, status = "derive_semantics", "SEMANTICS_DERIVED"
            elif values.get("fact_digest_generated") is not True:
                as_node, status = "persist_current_state", "CURRENT_STATE_PERSISTED"
            elif values.get("volume_progress_updated") is not True:
                as_node, status = "persist_fact_digest", "FACT_DIGEST_PERSISTED"
            elif not values.get("chapter_sources_path"):
                as_node, status = "persist_volume_progress", "VOLUME_PROGRESS_PERSISTED"
            else:
                as_node, status = "persist_chapter_sources", "CHAPTER_SOURCES_PERSISTED"

            graph.update_state(
                self.config,
                {"workflow_status": status, "error": None},
                as_node=as_node,
            )
            print(f"  [LangGraph] 正在从首个未完成阶段继续：{as_node}")
            return self._result_or_interrupt(
                graph, graph.invoke(None, config=self.config)
            )
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


def repair_chapter_derivation(novel_id: str, chapter_index: int) -> dict[str, Any]:
    return ChapterWorkflowRunner(novel_id, chapter_index).repair_derivation()


def restart_chapter_workflow(novel_id: str, chapter_index: int) -> dict[str, Any]:
    return ChapterWorkflowRunner(novel_id, chapter_index).restart()
