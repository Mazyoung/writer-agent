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
from src.config.runtime_policy import NovelRuntimePolicy, load_novel_runtime_policy
from src.storage.file_store import FileStore
from src.storage.story_savepoint import (
    NovelOperationLock,
    StorySavepointManager,
)
from src.workflows.chapter_workflow import (
    build_chapter_workflow,
    record_generation_event,
    render_chapter_sources,
)


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
    policy = getattr(runner, "runtime_policy", None)
    interval = (
        policy.auto_savepoint_every if policy is not None
        else get_settings().auto_savepoint_every
    )
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
        enriched.setdefault("chapter_index", runner.chapter_index)
        event = record_generation_event(
            enriched,
            "AUTO_SAVEPOINT_CREATED",
            discriminator=manifest["savepoint_id"],
            details={"savepoint_id": manifest["savepoint_id"]},
        )
        if hasattr(runner, "_open_graph"):
            connection, _checkpointer, graph = runner._open_graph()
            try:
                graph.update_state(runner.config, {"generation_events": event})
                snapshot = graph.get_state(runner.config)
                enriched.update(dict(snapshot.values))
                source_path = (
                    runner.file_store.root / "sources"
                    / f"chapter_{runner.chapter_index:04d}" / "chapter_sources.md"
                )
                if source_path.exists():
                    temp = source_path.with_suffix(".md.tmp")
                    temp.write_text(render_chapter_sources(enriched), encoding="utf-8")
                    temp.replace(source_path)
            finally:
                connection.close()
        print(f"  [AUTO SAVEPOINT] 已创建 {savepoint_id}（READY）。")
    else:
        print(f"  [AUTO SAVEPOINT] {savepoint_id} 已是有效 READY，视为完成。")
    return enriched


class ChapterWorkflowRunner:
    """Start or resume one chapter on its deterministic LangGraph thread."""

    def __init__(
        self,
        novel_id: str,
        chapter_index: int,
        *,
        ensure_dirs: bool = True,
        runtime_policy: NovelRuntimePolicy | None = None,
    ):
        self.novel_id = novel_id
        self.chapter_index = chapter_index
        settings = get_settings()
        self.runtime_policy = runtime_policy or load_novel_runtime_policy(
            novel_id, settings
        )
        self.file_store = FileStore(novel_id, settings.data_dir, ensure_dirs=ensure_dirs)
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

    def _open_graph(self, *, read_only: bool = False):
        if read_only:
            connection = sqlite3.connect(
                f"file:{self.checkpoint_path.as_posix()}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
        else:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.checkpoint_path, check_same_thread=False)
        checkpointer = SqliteSaver(connection)
        return connection, checkpointer, build_chapter_workflow(
            checkpointer=checkpointer)

    def _result_or_interrupt(self, graph, result: Any) -> dict[str, Any]:
        snapshot = graph.get_state(self.config)
        if isinstance(result, dict) and result.get("failed_runtime_stage"):
            return dict(result)
        if snapshot.interrupts:
            return self._waiting_result(snapshot)
        return dict(result)

    def _invoke_preserving_checkpoint(self, graph, value: Any) -> Any:
        """Report node exceptions without committing a terminal graph state."""
        try:
            return graph.invoke(value, config=self.config)
        except Exception as exc:
            snapshot = graph.get_state(self.config)
            stage = str(snapshot.next[0]) if snapshot.next else "UNKNOWN"
            return {
                "novel_id": self.novel_id,
                "chapter_index": self.chapter_index,
                "workflow_status": "error",
                "failed_runtime_stage": stage,
                "error": (
                    "Chapter workflow stopped due to a runtime error. "
                    f"{type(exc).__name__}: {exc}; checkpoint remains at {stage}. "
                    "Fix the problem and run continue to retry the failed node."
                ),
            }

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
        connection, _checkpointer, graph = self._open_graph(read_only=True)
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
    def refresh_derived_ready_sources(self) -> dict[str, Any]:
        """Refresh only the deterministic report for an already-ready chapter."""
        from src.storage.chapter_completion import is_derived_ready

        if not is_derived_ready(self.file_store, self.chapter_index):
            return {"refreshed": False}
        if not self.checkpoint_path.is_file():
            return {"refreshed": False}
        inspection = self.inspect()
        values = dict(inspection.get("values", {}))
        if not values or values.get("commit_success") is not True:
            return {"refreshed": False}
        final_state = {
            **values,
            "workflow_status": "DERIVED_READY",
            "failed_derivation_stage": "",
            "derivation_error": "",
            "active_derivation_errors": {},
            "derived_state_errors": [],
        }
        path = (
            self.file_store.root / "sources"
            / f"chapter_{self.chapter_index:04d}" / "chapter_sources.md"
        )
        content = render_chapter_sources(final_state)
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            return {"refreshed": False}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        return {
            "refreshed": True,
            "chapter_sources_path": str(
                path.relative_to(self.file_store.root)
            ).replace("\\", "/"),
        }


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
                    graph, self._invoke_preserving_checkpoint(graph, None)
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
                "chapter_mode": self.runtime_policy.chapter_mode,
                "agent_execution": self.runtime_policy.agent_execution,
                "auto_savepoint_every": self.runtime_policy.auto_savepoint_every,
                "rag_top_k": self.runtime_policy.rag_top_k,
                "workflow_status": "running",
                "warnings": [],
            }
            print("  [LangGraph] 正在启动 checkpointed 章节工作流。")
            return self._result_or_interrupt(
                graph, self._invoke_preserving_checkpoint(graph, initial_state)
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
        if interrupt_type not in {
            "plan_review", "chapter_review", "final_author_approval",
            "human_final_approval",
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
            feedback = str(resume_value.get("feedback", "")).strip()
            if (
                action == "agent_edit"
                and str(pending.get("verdict", "")).strip().upper() == "PASS"
                and not feedback
            ):
                raise ValueError(
                    "Review 已通过，Agent 自动修改需要提供修改意见"
                )
            if action == "restart":
                return {
                    "workflow_status": "RESTARTED",
                    "removed_candidates": self._discard_candidate(checkpointer),
                    "chapter_intent_preserved": True,
                }
            command_value = {
                "action": action,
                "feedback": feedback,
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
            result = self._invoke_preserving_checkpoint(
                graph, Command(resume=command_value)
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
                "ATOMIC_FACTS_DERIVED",
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

            if not values.get("updated_current_state_text"):
                as_node, status = "commit_canonical_prose", "CANONICAL_COMMITTED"
            elif values.get("current_state_persisted") is not True:
                as_node, status = "derive_semantics", "SEMANTICS_DERIVED"
            elif values.get("atomic_facts_derived") is not True:
                as_node, status = "persist_current_state", "CURRENT_STATE_PERSISTED"
            elif values.get("fact_verification_complete") is not True:
                as_node, status = "persist_fact_digest", "ATOMIC_FACTS_DERIVED"
            elif values.get("volume_progress_updated") is not True:
                as_node, status = "verify_atomic_facts", "FACT_DIGEST_PERSISTED"
            elif not values.get("chapter_sources_path"):
                as_node, status = "persist_volume_progress", "VOLUME_PROGRESS_PERSISTED"
            else:
                as_node, status = "persist_chapter_sources", "CHAPTER_SOURCES_PERSISTED"

            graph.update_state(
                self.config,
                {"workflow_status": status, "error": None},
                as_node=as_node,
            )
            print(
                f"  [LangGraph] 已恢复到 checkpoint：{as_node}，"
                "正在继续后续 Derivation。"
            )
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
    runtime_policy: NovelRuntimePolicy | None = None,
) -> dict[str, Any]:
    return ChapterWorkflowRunner(
        novel_id, chapter_index, runtime_policy=runtime_policy
    ).run(
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


def restart_chapter_workflow(
    novel_id: str,
    chapter_index: int,
    runtime_policy: NovelRuntimePolicy | None = None,
) -> dict[str, Any]:
    return ChapterWorkflowRunner(
        novel_id, chapter_index, runtime_policy=runtime_policy
    ).restart()
