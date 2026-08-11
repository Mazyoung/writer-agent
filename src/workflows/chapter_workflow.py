"""Checkpointed E07.6 single-chapter production workflow.

LangGraph owns orchestration. Review, canonical prose commit, and derivation
are separate boundaries.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from src.config.settings import get_settings
from src.core.model_provider import GenerationLimitExceeded
from src.core.text_windows import previous_chapter_end
from src.storage.file_store import FileStore
from src.storage.sqlite_store import SQLiteStore


GENERATION_EVENT_TYPES = frozenset({
    "INTENT_FINALIZED",
    "QUERY_INTENT_FINALIZED",
    "QUERY_INTENT_RETRIED",
    "RETRIEVAL_COMPLETED",
    "PLAN_CREATED",
    "PLAN_REVIEWED",
    "PLAN_AGENT_EDITED",
    "PLAN_HUMAN_EDITED",
    "PROSE_CREATED",
    "PROSE_REVIEWED",
    "PROSE_REGENERATED",
    "PROSE_AGENT_EDITED",
    "PROSE_HUMAN_EDITED",
    "CONSISTENCY_REVIEWED",
    "REVIEW_OVERRIDE_REQUESTED",
    "REVIEW_OVERRIDE_CONFIRMED",
    "CANONICAL_COMMITTED",
    "DERIVATION_FAILED",
    "DERIVATION_RECOVERED",
    "DERIVED_READY",
    "AUTO_SAVEPOINT_CREATED",
})


class GenerationEvent(TypedDict, total=False):
    event_id: str
    event_type: str
    chapter_index: int
    attempt: int
    discriminator: str
    details: dict[str, Any]


def merge_generation_events(
    current: list[GenerationEvent] | None,
    updates: list[GenerationEvent] | None,
) -> list[GenerationEvent]:
    """Merge checkpointed audit facts by their stable workflow identity."""
    merged = list(current or [])
    by_id = {event["event_id"]: event for event in merged}
    for event in updates or []:
        event_type = event.get("event_type", "")
        event_id = event.get("event_id", "")
        if event_type not in GENERATION_EVENT_TYPES:
            raise ValueError(f"不支持的 generation event: {event_type}")
        if not event_id:
            raise ValueError("generation event 缺少 event_id")
        existing = by_id.get(event_id)
        if existing is None:
            merged.append(event)
            by_id[event_id] = event
        elif existing != event:
            raise ValueError(f"generation event ID 冲突: {event_id}")
    return merged


def record_generation_event(
    state: "ChapterWorkflowState",
    event_type: str,
    *,
    counter: int | None = None,
    discriminator: str = "",
    details: dict[str, Any] | None = None,
) -> list[GenerationEvent]:
    """Create one event whose ID comes only from durable workflow identity."""
    if event_type not in GENERATION_EVENT_TYPES:
        raise ValueError(f"不支持的 generation event: {event_type}")
    chapter_index = int(state.get("chapter_index", 0))
    parts = [str(chapter_index), event_type]
    if discriminator:
        parts.append(discriminator)
    if counter is not None:
        if counter < 1:
            raise ValueError("generation event counter 必须从 1 开始")
        parts.append(str(counter))
    event: GenerationEvent = {
        "event_id": ":".join(parts),
        "event_type": event_type,
        "chapter_index": chapter_index,
        "details": details or {},
    }
    if counter is not None:
        event["attempt"] = counter
    if discriminator:
        event["discriminator"] = discriminator
    for existing in state.get("generation_events", []):
        if existing.get("event_id") == event["event_id"]:
            return [existing]
    return [event]


class ChapterWorkflowState(TypedDict, total=False):
    """Data that must cross nodes in one checkpointed chapter execution."""

    novel_id: str
    branch_id: str
    chapter_index: int

    chapter_outline: str
    extra_instructions: str
    chapter_intent: str
    chapter_mode: str
    agent_execution: str

    chapter_plan_text: str
    historical_evidence: str
    current_state_text: str
    current_state_sha256: str
    draft_text: str
    styled_text: str
    candidate_text: str
    candidate_path: str
    canonical_source_path: str
    derivation_raw_analysis: str
    updated_current_state_text: str
    current_state_persisted: bool
    atomic_facts_derived: bool
    atomic_fact_candidates: list[dict]
    verified_atomic_facts: list[dict]
    fact_verification_complete: bool
    failed_derivation_stage: str
    derivation_error: str
    active_derivation_errors: dict[str, str]
    volume_progress: str
    volume_progress_updated: bool
    volume_progress_path: str

    plan_raw_analysis: str
    plan_verdict: str
    plan_review_reasons: list[str]
    plan_t1_issues: list[str]
    plan_planning_level: str
    plan_review_attempt: int
    plan_revision_count: int

    raw_analysis: str
    verdict: str
    consistency_raw_analysis: str
    consistency_verdict: str
    consistency_warnings: list[str]
    review_reasons: list[str]
    review_issues: list[str]
    t1_issues: list[str]
    planning_level: str
    review_round: int
    prose_regeneration_count: int
    revision_used: bool

    human_decision: str
    human_feedback: str
    final_author_approved: bool
    review_override_confirmed: bool

    commit_success: bool
    commit_error: str
    completion_marker_path: str

    retrieval_success: bool
    retrieval_result_count: int
    retrieval_trace_path: str
    retrieved_facts: list[dict]
    expanded_sources: list[dict]
    writing_context_path: str
    chapter_sources_path: str
    fact_digest_path: str
    atomic_fact_count: int
    rag_facts: int
    query_intent: str
    derived_state_errors: list[str]

    warnings: list[str]
    fact_digest_generated: bool
    rag_chunks: int

    workflow_status: str
    error: str | None

    generation_events: Annotated[
        list[GenerationEvent], merge_generation_events
    ]


def _error_result(message: str) -> dict[str, Any]:
    return {"workflow_status": "error", "error": message}


def _generation_limit_report(
    state: ChapterWorkflowState,
    workflow_node: str,
    exc: GenerationLimitExceeded,
) -> str:
    is_chapter_plan = (
        workflow_node == "plan_chapter" and exc.slot_name == "plan"
    )
    lines = [
        (
            "Chapter Plan 生成失败：模型输出达到生成上限"
            if is_chapter_plan else "模型生成失败：模型输出达到生成上限"
        ),
        "",
        f"Chapter: {state.get('chapter_index', 'UNKNOWN')}",
        f"Node: {'ChapterPlanner' if is_chapter_plan else workflow_node}",
        f"Model Slot: {exc.slot_name.upper()}",
        f"Provider: {exc.provider}",
        f"Model: {exc.model}",
        f"Configured max_tokens: {exc.configured_max_tokens}",
        f"finish_reason / stop_reason: {exc.reason}",
        "Partial output saved: NO",
    ]
    if is_chapter_plan:
        lines.extend([
            "Partial canonical saved: NO",
            "Plan Review executed: NO",
            "",
            "这是模型生成上限导致的技术性失败，不是 Plan Review 未通过。",
        ])
    return "\n".join(lines)


def _guard_node(
    node: Callable[[ChapterWorkflowState], dict[str, Any]],
) -> Callable[[ChapterWorkflowState], dict[str, Any]]:
    """Keep runtime/API/database/disk failures on the error path."""
    @wraps(node)
    def guarded(state: ChapterWorkflowState) -> dict[str, Any]:
        try:
            result = node(state)
        except GenerationLimitExceeded as exc:
            return _error_result(
                _generation_limit_report(state, node.__name__, exc)
            )
        except Exception as exc:
            return _error_result(
                f"{node.__name__} failed: {type(exc).__name__}: {exc}"
            )
        if result.get("workflow_status") == "error":
            result.setdefault("error", f"{node.__name__} failed")
        return result

    return guarded


def _route_after_node(state: ChapterWorkflowState, success_target: str) -> str:
    return END if state.get("workflow_status") == "error" else success_target


@_guard_node
def preflight(state: ChapterWorkflowState) -> dict[str, Any]:
    """Validate generation prerequisites before any production side effect."""
    novel_id = state.get("novel_id")
    if not isinstance(novel_id, str) or not novel_id.strip():
        return _error_result("novel_id 无效：必须是非空字符串")

    chapter_index = state.get("chapter_index")
    if (isinstance(chapter_index, bool)
            or not isinstance(chapter_index, int)
            or chapter_index <= 0):
        return _error_result("chapter_index 无效：必须是正整数")

    branch_id = state.get("branch_id", "main")
    if branch_id != "main":
        return _error_result(
            f"不支持 branch_id '{branch_id}'：E07 当前仅支持 main"
        )

    chapter_mode = state.get("chapter_mode", "agent")
    if chapter_mode not in {"agent", "human"}:
        return _error_result(
            f"chapter_mode {chapter_mode!r} 无效：应为 agent 或 human"
        )

    fs = FileStore(novel_id, get_settings().data_dir)
    if fs.canonical_chapter_path(chapter_index).exists():
        return _error_result(
            f"ERROR_ALREADY_EXISTS: 第{chapter_index}章已完成，普通 Generate 禁止覆盖"
        )
    return {"workflow_status": "PREFLIGHT_OK"}


@_guard_node
def load_current_state(state: ChapterWorkflowState) -> dict[str, Any]:
    """Checkpoint one validated present-state snapshot for this execution."""
    from src.storage.current_state_store import CurrentStateStore

    fs = FileStore(state["novel_id"], get_settings().data_dir)
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        text, digest = CurrentStateStore(
            state["novel_id"], fs, sqlite
        ).ensure_raw_initialized()
    finally:
        sqlite.close()
    return {
        "current_state_text": text,
        "current_state_sha256": digest,
        "workflow_status": "CURRENT_STATE_LOADED",
    }


@_guard_node
def load_chapter_intent(state: ChapterWorkflowState) -> dict[str, Any]:
    """Persist a supplied intent or load the existing human/canonical intent."""
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    prefix = f"chapter_intent_ch{state['chapter_index']:04d}"
    supplied = state.get("chapter_intent", "").strip()
    if supplied:
        fs.save_canonical("briefs", prefix, supplied)
        intent = supplied
    else:
        intent = fs.load_canonical("briefs", prefix) or ""
    return {
        "chapter_intent": intent,
        "generation_events": record_generation_event(
            state,
            "INTENT_FINALIZED",
            details={"provided": bool(supplied), "has_content": bool(intent.strip())},
        ),
        "workflow_status": "INTENT_LOADED",
    }


def _route_after_intent(state: ChapterWorkflowState) -> str:
    """Old checkpoints have Agent semantics; new modes were validated at start."""
    if state.get("workflow_status") == "error":
        return END
    if state.get("chapter_mode", "agent") == "human":
        return "prepare_human_context"
    return "plan_chapter"


def _evidence_section(evidence: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        evidence,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else "- None retrieved"


def _as_event_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    return dict(vars(value))


def _retrieval_event_details(retrieval: Any) -> dict[str, Any]:
    return {
        "query_intent": retrieval.trace.query,
        "result_count": len(retrieval.trace.results),
        "retrieval_trace_path": retrieval.trace_path,
        "facts": [_as_event_dict(fact) for fact in retrieval.fact_candidates],
        "sources": [_as_event_dict(source) for source in retrieval.source_excerpts],
    }


def _build_query_intent(
    state: ChapterWorkflowState,
) -> tuple[str, int, list[GenerationEvent]]:
    """Build the sole embedding query and expose its durable retry attempt."""
    from src.agents.author.query_intent_builder import QueryIntentBuilder

    events: list[GenerationEvent] = []
    final_attempt = 1

    def on_attempt(kind: str, attempt: int) -> None:
        nonlocal final_attempt
        if kind == "retried":
            final_attempt = attempt
            events.extend(record_generation_event(
                state,
                "QUERY_INTENT_RETRIED",
                counter=attempt,
            ))

    fs = FileStore(state["novel_id"], get_settings().data_dir)
    query_intent = QueryIntentBuilder(state["novel_id"]).build(
        volume_plan=fs.load_tracking_doc("volume_plan") or "",
        recent_chapter_end=previous_chapter_end(fs, state["chapter_index"]),
        current_state=state.get("current_state_text", ""),
        human_intent=state.get("chapter_intent", ""),
        on_attempt=on_attempt,
    )
    return query_intent, final_attempt, events


@_guard_node
def prepare_human_context(state: ChapterWorkflowState) -> dict[str, Any]:
    """Retrieve bounded history and persist an author-readable generated report."""
    from src.workflows.retrieval_service import ChapterRetrievalService

    intent = state.get("chapter_intent", "").strip()
    if not intent:
        return _error_result(
            "Human Mode 执行历史检索前必须提供非空 Chapter Intent。"
        )
    query_intent, query_attempt, query_events = _build_query_intent(state)
    retrieval = ChapterRetrievalService(state["novel_id"]).retrieve(
        state["chapter_index"], query_intent
    )
    if not retrieval.trace.success:
        return _error_result(
            "历史检索失败：" + retrieval.trace.error_message
        )
    if retrieval.warnings:
        return _error_result("; ".join(retrieval.warnings))

    evidence = retrieval.evidence.replace(
        "## Historical Atomic Facts", "## Relevant Historical Facts"
    ).replace(
        "## On-demand Historical Source Excerpts", "## Relevant Historical Prose"
    )
    chapter_index = state["chapter_index"]
    context = "\n".join([
        f"# Chapter {chapter_index} Writing Context",
        "",
        "> Automatically generated retrieval report; not a canonical production source.",
        "",
        "## Chapter Intent",
        intent,
        "",
        "## Retrieval Query Intent",
        retrieval.trace.query,
        "",
        "## Current State",
        state.get("current_state_text", "").strip() or "No current state content.",
        "",
        "## Relevant Historical Facts",
        _evidence_section(evidence, "Relevant Historical Facts"),
        "",
        "## Relevant Historical Prose",
        _evidence_section(evidence, "Relevant Historical Prose"),
        "",
        "## Author Knowledge",
        _evidence_section(evidence, "Author Knowledge"),
        "",
    ])
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    path = fs.save_generated_tracking_doc(
        f"writing_context_ch{chapter_index:04d}", context
    )
    query_events.extend(record_generation_event(
        state,
        "QUERY_INTENT_FINALIZED",
        counter=query_attempt,
        details={"query_intent": query_intent},
    ))
    query_events.extend(record_generation_event(
        state,
        "RETRIEVAL_COMPLETED",
        details=_retrieval_event_details(retrieval),
    ))
    return {
        "historical_evidence": retrieval.evidence,
        "query_intent": query_intent,
        "retrieval_success": True,
        "retrieval_result_count": len(retrieval.trace.results),
        "retrieval_trace_path": retrieval.trace_path,
        "retrieved_facts": retrieval.fact_candidates,
        "expanded_sources": retrieval.source_excerpts,
        "writing_context_path": str(path.relative_to(fs.root)).replace("\\", "/"),
        "warnings": retrieval.warnings,
        "generation_events": query_events,
        "workflow_status": "HUMAN_CONTEXT_READY",
    }


def await_human_writing(state: ChapterWorkflowState) -> dict[str, Any]:
    """接收人工正文 Candidate；正文仍须经过一致性检查与最终批准。"""
    chapter_index = state.get("chapter_index", 0)
    resume_value = interrupt({
        "type": "human_writing",
        "novel_id": state.get("novel_id", ""),
        "chapter_index": chapter_index,
        "writing_context_path": state.get("writing_context_path", ""),
        "message": "相关历史写作上下文已准备完成，正在等待作者提交正文 Candidate。",
        "allowed_actions": ["submit", "restart"],
    })
    if not isinstance(resume_value, dict):
        return _error_result("人工正文提交必须是一个决策对象")
    candidate = str(resume_value.get("candidate_text", "")).strip()
    if str(resume_value.get("action", "")).strip().lower() != "submit" or not candidate:
        return _error_result("submit 需要非空的人工正文 Candidate")
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    path = fs.save(
        "chapters", f"chapter_{chapter_index:04d}_human_candidate", candidate
    )
    return {
        "candidate_text": candidate,
        "candidate_path": str(path.relative_to(fs.root)).replace("\\", "/"),
        "final_author_approved": False,
        "review_override_confirmed": False,
        "generation_events": record_generation_event(
            state,
            "PROSE_CREATED",
            details={"source": "human"},
        ),
        "workflow_status": "HUMAN_CANDIDATE_SUBMITTED",
    }


@_guard_node
def review_consistency(state: ChapterWorkflowState) -> dict[str, Any]:
    """复用已 checkpoint 的 Writing Context 做一次硬连续性检查。"""
    from src.agents.state_manager.state_manager import StateManager

    candidate = state.get("candidate_text", "")
    if not candidate.strip():
        return _error_result("本次执行没有人工正文 Candidate，无法检查一致性")
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    context_path = state.get("writing_context_path", "")
    writing_context = ""
    if context_path:
        path = fs.root / context_path
        if not path.is_file():
            return _error_result(f"Writing Context 不存在: {context_path}")
        writing_context = path.read_text(encoding="utf-8")
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        analysis = StateManager(state["novel_id"], sqlite).review_consistency(
            candidate,
            state["chapter_index"],
            world_setting=fs.load_canonical("settings", "world_setting") or "",
            current_state_text=state.get("current_state_text", ""),
            writing_context_text=writing_context,
        )
    finally:
        sqlite.close()
    return {
        "consistency_raw_analysis": analysis["raw_analysis"],
        "workflow_status": "CONSISTENCY_REVIEWED",
    }


@_guard_node
def parse_consistency_decision(state: ChapterWorkflowState) -> dict[str, Any]:
    """只消费 Reviewer 明确给出的 CLEAN/WARN 结构化结果。"""
    from src.storage.document_formats import _extract_section, _parse_key_value

    raw = state.get("consistency_raw_analysis", "")
    decision_section = _extract_section(raw, "## 一致性结论")
    decision = _parse_key_value(decision_section) if decision_section else {}
    verdict = decision.get("结论", "").strip().upper()
    if verdict not in {"CLEAN", "WARN"}:
        return {
            **_error_result("一致性检查缺少有效的 CLEAN/WARN 结构化结论"),
            "consistency_verdict": "UNKNOWN",
        }

    issue_section = _extract_section(raw, "## 连续性问题")
    warnings = [
        line.strip()[2:].strip()
        for line in issue_section.splitlines()
        if line.strip().startswith("- ")
        and line.strip()[2:].strip() not in {"", "无"}
    ]
    main_issue = decision.get("主要问题", "").strip()
    if not warnings and main_issue not in {"", "无"}:
        warnings = [main_issue]
    return {
        "consistency_verdict": verdict,
        "consistency_warnings": warnings,
        "generation_events": record_generation_event(
            state,
            "CONSISTENCY_REVIEWED",
            counter=state.get("review_round", 1),
            details={"verdict": verdict, "issues": warnings},
        ),
        "workflow_status": f"CONSISTENCY_{verdict}",
    }


def _route_after_consistency(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    if state.get("consistency_verdict") in {"CLEAN", "WARN"}:
        return "await_human_chapter"
    return END


@_guard_node
def plan_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """Retrieve relevant history and generate the canonical Chapter Plan."""
    from src.agents.author.chapter_planner import ChapterPlanner
    from src.workflows.retrieval_service import ChapterRetrievalService

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    outline = state.get("chapter_outline", "")
    instructions = state.get("extra_instructions", "")
    intent = state.get("chapter_intent", "")

    query_intent, query_attempt, query_events = _build_query_intent(state)
    retrieval = ChapterRetrievalService(novel_id).retrieve(
        chapter_index, query_intent
    )
    if not retrieval.trace.success:
        return _error_result(
            "历史检索失败：" + retrieval.trace.error_message
        )
    if retrieval.warnings:
        return _error_result("; ".join(retrieval.warnings))

    planner = ChapterPlanner(novel_id)
    planner.plan_chapter(
        chapter_index,
        outline,
        instructions,
        rag_evidence=retrieval.evidence,
        query_intent=retrieval.trace.query,
        chapter_intent=intent,
        current_state_text=state.get("current_state_text", ""),
    )
    fs = FileStore(novel_id, get_settings().data_dir)
    world_setting = fs.load_canonical("settings", "world_setting") or ""
    book_plan = fs.load_tracking_doc("book_plan") or ""
    volume_plan = fs.load_tracking_doc("volume_plan") or ""
    previous_end = previous_chapter_end(fs, chapter_index)
    plan_text = fs.load_canonical(
        "outlines", f"chapter_plan_ch{chapter_index:04d}"
    ) or ""
    if not plan_text.strip():
        return _error_result("ChapterPlanner 未生成可审阅的 canonical Chapter Plan")

    print("  [plan_chapter] canonical Chapter Plan 已生成")
    query_events.extend(record_generation_event(
        state,
        "QUERY_INTENT_FINALIZED",
        counter=query_attempt,
        details={"query_intent": query_intent},
    ))
    query_events.extend(record_generation_event(
        state,
        "RETRIEVAL_COMPLETED",
        details=_retrieval_event_details(retrieval),
    ))
    query_events.extend(record_generation_event(
        state,
        "PLAN_CREATED",
        details={
            "artifact_path": f"outlines/chapter_plan_ch{chapter_index:04d}.md",
            "context_sources": {
                "world_setting": bool(world_setting.strip()),
                "book_plan": bool(book_plan.strip()),
                "volume_plan": bool(volume_plan.strip()),
                "current_state": bool(state.get("current_state_text", "").strip()),
                "previous_chapter_end": bool(previous_end.strip()),
                "human_intent": bool(intent.strip()),
                "rag_context": bool(retrieval.evidence.strip()),
            },
        },
    ))
    return {
        "chapter_plan_text": plan_text,
        "historical_evidence": retrieval.evidence,
        "query_intent": query_intent,
        "retrieval_success": retrieval.trace.success,
        "retrieval_result_count": len(retrieval.trace.results),
        "retrieval_trace_path": retrieval.trace_path,
        "warnings": retrieval.warnings,
        "retrieved_facts": retrieval.fact_candidates,
        "expanded_sources": retrieval.source_excerpts,
        "plan_review_attempt": 0,
        "generation_events": query_events,
        "workflow_status": "PLANNED",
    }


@_guard_node
def review_plan(state: ChapterWorkflowState) -> dict[str, Any]:
    """Review every generated or human-edited plan before Writer can run."""
    from src.agents.author.plan_reviewer import PlanReviewer

    plan_text = state.get("chapter_plan_text", "")
    if not plan_text.strip():
        return _error_result("chapter_plan_text 为空，无法执行 Plan Review")

    fs = FileStore(state["novel_id"], get_settings().data_dir)
    attempt = state.get("plan_review_attempt", 0) + 1
    analysis = PlanReviewer(state["novel_id"]).review_plan(
        chapter_index=state["chapter_index"],
        plan_text=plan_text,
        chapter_intent=state.get("chapter_intent", ""),
        world_setting=fs.load_canonical("settings", "world_setting") or "",
        book_plan=fs.load_tracking_doc("book_plan") or "",
        volume_plan=fs.load_tracking_doc("volume_plan") or "",
        current_state=state.get("current_state_text", ""),
        historical_evidence=state.get("historical_evidence", ""),
        review_attempt=attempt,
    )
    return {
        "plan_raw_analysis": analysis,
        "plan_review_attempt": attempt,
        "workflow_status": "PLAN_REVIEWED",
    }


def _parse_review(raw_analysis: str) -> Any:
    from src.storage.document_formats import ReviewDecision

    return ReviewDecision.from_analysis(raw_analysis)


@_guard_node
def parse_plan_decision(state: ChapterWorkflowState) -> dict[str, Any]:
    """Parse Plan Review deterministically; UNKNOWN remains an error."""
    raw = state.get("plan_raw_analysis", "")
    if not raw:
        return {
            **_error_result("Plan Review raw_analysis 为空，无法解析审阅决策"),
            "plan_verdict": "UNKNOWN",
        }
    decision = _parse_review(raw)
    if decision.verdict == "UNKNOWN":
        return {
            **_error_result("Plan Review verdict UNKNOWN; Writer blocked fail-closed"),
            "plan_verdict": "UNKNOWN",
            "plan_review_reasons": decision.reasons,
            "plan_t1_issues": decision.t1_issues,
            "plan_planning_level": decision.planning_level,
        }
    print(f"  [parse_plan_decision] {decision.verdict}")
    return {
        "plan_verdict": decision.verdict,
        "plan_review_reasons": decision.reasons,
        "plan_t1_issues": decision.t1_issues,
        "plan_review_issues": [
            *decision.t1_issues,
            *decision.t2_issues,
            *decision.t3_issues,
            *decision.quality_issues,
        ],
        "plan_planning_level": decision.planning_level,
        "generation_events": record_generation_event(
            state,
            "PLAN_REVIEWED",
            counter=state.get("plan_review_attempt", 1),
            details={
                "verdict": decision.verdict,
                "issues": [
                    *decision.t1_issues,
                    *decision.t2_issues,
                    *decision.t3_issues,
                    *decision.quality_issues,
                    *decision.reasons,
                ],
                "planning_level": decision.planning_level,
            },
        ),
        "workflow_status": f"PLAN_DECISION_{decision.verdict}",
    }


def _route_after_plan_decision(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    supervised = state.get("agent_execution", "supervised") == "supervised"
    if state.get("plan_verdict") == "PASS":
        return "await_human_plan" if supervised else "write_draft"
    if state.get("plan_verdict") == "NEEDS_REVISION":
        if (
            not supervised
            and state.get("plan_revision_count", 0) < 2
        ):
            return "agent_edit_plan"
        return "await_human_plan"
    return END


@_guard_node
def agent_edit_plan(state: ChapterWorkflowState) -> dict[str, Any]:
    """Revise only the issues identified by the latest Plan Review."""
    from src.agents.author.chapter_planner import ChapterPlanner

    fs = FileStore(state["novel_id"], get_settings().data_dir)
    issues = [
        *state.get("plan_t1_issues", []),
        *state.get("plan_review_reasons", []),
    ]
    planning_context = "\n\n".join([
        "## World Setting\n"
        + (fs.load_canonical("settings", "world_setting") or "无"),
        "## Book Plan\n"
        + (fs.load_tracking_doc("book_plan") or "无"),
        "## Volume Plan\n"
        + (fs.load_tracking_doc("volume_plan") or "无"),
        "## Current State\n"
        + (state.get("current_state_text", "") or "无"),
        "## Historical Evidence\n"
        + (state.get("historical_evidence", "") or "无"),
    ])
    revised = ChapterPlanner(state["novel_id"]).revise_plan(
        chapter_index=state["chapter_index"],
        current_plan=state.get("chapter_plan_text", ""),
        review_issues=issues,
        planning_context=planning_context,
        chapter_intent=state.get("chapter_intent", ""),
        human_feedback=state.get("human_feedback", ""),
    )
    revision_count = state.get("plan_revision_count", 0) + 1
    return {
        "chapter_plan_text": revised,
        "plan_verdict": "",
        "plan_raw_analysis": "",
        "plan_revision_count": revision_count,
        "generation_events": record_generation_event(
            state,
            "PLAN_AGENT_EDITED",
            counter=revision_count,
        ),
        "workflow_status": "PLAN_AGENT_EDITED",
    }



def _event_lines(state: ChapterWorkflowState) -> list[str]:
    lines = []
    for event in state.get("generation_events", []):
        event_type = event.get("event_type", "")
        details = event.get("details", {})
        verdict = details.get("verdict")
        suffix = f"：{verdict}" if verdict else ""
        issues = details.get("issues", [])
        lines.append(f"- `{event_type}`{suffix}")
        if issues and event_type in {"PLAN_REVIEWED", "PROSE_REVIEWED", "CONSISTENCY_REVIEWED"}:
            lines.extend(f"  - Review issue: {issue}" for issue in issues)
    return lines or ["- 暂无关键生成事件"]


def render_chapter_sources(state: ChapterWorkflowState) -> str:
    """Render only checkpointed, structured provenance facts."""
    chapter_index = state["chapter_index"]
    facts = list(state.get("retrieved_facts", []))
    excerpts = list(state.get("expanded_sources", []))
    events = state.get("generation_events", [])
    lines = [
        f"# Chapter {chapter_index} 内容来源与生成记录", "",
        "## 1. 本章创作意图",
        f"- Human Intent: {state.get('chapter_intent', '') or '未提供'}",
        f"- Query Intent: {state.get('query_intent', '') or '未生成'}", "",
        "## 2. 历史内容来源",
        f"- Retrieval Trace: `{state.get('retrieval_trace_path', '') or '未执行'}`",
    ]
    for fact in facts:
        lines.append(
            f"- **{fact.get('fact_id', '')}**（第{fact.get('chapter_index', 0)}章，"
            f"{fact.get('fact_type', 'event')}）: {fact.get('text', '')}"
        )
    for source in excerpts:
        lines.append(
            f"- Source `{source.get('source_path', '')}` paragraphs "
            f"{source.get('paragraph_start', 0)}-{source.get('paragraph_end', 0)}"
        )
    if not facts and not excerpts:
        lines.append("- 未提供历史来源")
    context_sources = None
    for event in events:
        if event.get("event_type") != "PLAN_CREATED":
            continue
        details = event.get("details", {})
        if isinstance(details.get("context_sources"), dict):
            context_sources = details["context_sources"]
            break
    lines.extend(["", "## 3. 规划与状态来源"])
    if context_sources is None:
        lines.append("- context_sources: 未记录（旧 checkpoint）")
    else:
        source_labels = {
            "world_setting": "World Setting",
            "book_plan": "Book Plan",
            "volume_plan": "Volume Plan",
            "current_state": "Current State",
            "previous_chapter_end": "Previous Chapter End",
            "human_intent": "Human Intent",
            "rag_context": "RAG Context",
        }
        for source_name, label in source_labels.items():
            status = "已使用" if context_sources.get(source_name) is True else "未使用"
            lines.append(f"- {label}: {status}")
    lines.extend([
        "", "## 4. 关键生成过程",
        *_event_lines(state),
        "", "## 5. 最终状态",
        f"- Canonical Commit: {'是' if state.get('commit_success') else '否'}",
        f"- DERIVED_READY: {'是' if state.get('workflow_status') == 'DERIVED_READY' or any(e.get('event_type') == 'DERIVED_READY' for e in events) else '否'}",
        f"- Review Override: {'是' if state.get('review_override_confirmed') is True else '否'}",
    ])
    consistency_seen = any(e.get("event_type") == "CONSISTENCY_REVIEWED" for e in events)
    lines.append(
        f"- Consistency Review: {state.get('consistency_verdict', '未执行') if consistency_seen else '未执行'}"
    )
    lines.extend([
        "", "建议在继续下一章前优先检查本记录中的 Review 与 Warning。"
    ])
    return "\n".join(lines) + "\n"


@_guard_node
def save_chapter_sources(state: ChapterWorkflowState) -> dict[str, Any]:
    """Write the checkpointed provenance projection for this chapter."""
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    chapter_index = state["chapter_index"]
    path = fs.root / "sources" / f"chapter_{chapter_index:04d}" / "chapter_sources.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".md.tmp")
    temp.write_text(render_chapter_sources(state), encoding="utf-8")
    temp.replace(path)
    return {
        "chapter_sources_path": str(path.relative_to(fs.root)).replace("\\", "/"),
        "workflow_status": "SOURCES_SAVED",
    }
@_guard_node
def write_draft(state: ChapterWorkflowState) -> dict[str, Any]:
    """Write from the approved Chapter Plan, not from future plans."""
    from src.agents.author.deepseek_writer import DeepSeekWriter

    if state.get("plan_verdict") != "PASS":
        return _error_result("Writer blocked: latest Plan Review verdict is not PASS")
    plan_text = state.get("chapter_plan_text", "")
    if not plan_text:
        return _error_result("Chapter Plan 为空，无法写作")

    fs = FileStore(state["novel_id"], get_settings().data_dir)
    draft = DeepSeekWriter(state["novel_id"]).write_chapter(
        plan_text,
        state["chapter_index"],
        fs.load_canonical("settings", "world_setting") or "",
        _load_prev_chapter_end(fs, state["chapter_index"]),
    )
    regenerated = state.get("human_decision") == "regenerate_prose"
    regeneration_count = state.get("prose_regeneration_count", 0) + 1
    event_type = "PROSE_REGENERATED" if regenerated else "PROSE_CREATED"
    return {
        "draft_text": draft,
        "review_round": 1,
        "prose_regeneration_count": regeneration_count if regenerated else 0,
        "revision_used": False,
        "generation_events": record_generation_event(
            state,
            event_type,
            counter=regeneration_count if regenerated else None,
            details={"source": "agent"},
        ),
        "workflow_status": "DRAFTED",
    }


def _load_prev_chapter_end(fs: FileStore, chapter_index: int) -> str:
    """Compatibility wrapper around the shared complete-paragraph window."""
    return previous_chapter_end(fs, chapter_index)


@_guard_node
def style_edit(state: ChapterWorkflowState) -> dict[str, Any]:
    """Style-edit the initial draft without adding planning context."""
    from src.agents.author.claude_stylist import ClaudeStylist

    draft = state.get("draft_text", "")
    if not draft:
        return _error_result("draft_text 为空，无法执行 style_edit")
    plan_text = state.get("chapter_plan_text", "")
    styled = ClaudeStylist(state["novel_id"]).edit_chapter(
        draft,
        state["chapter_index"],
        chapter_plan_text=plan_text,
    )
    return {"styled_text": styled, "workflow_status": "STYLED"}


@_guard_node
def agent_edit_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """Locally revise current prose only after an explicit human decision."""
    from src.agents.author.deepseek_writer import DeepSeekWriter

    autonomous = state.get("agent_execution") == "autonomous"
    if state.get("human_decision") != "agent_edit" and not autonomous:
        return _error_result("agent_edit 需要明确的人工决定")

    revised = DeepSeekWriter(state["novel_id"]).revise_chapter(
        state.get("chapter_plan_text", ""),
        state["chapter_index"],
        state.get("styled_text", ""),
        [*state.get("review_reasons", []), state.get("human_feedback", "")],
        state.get("t1_issues", []),
    )
    if not revised.strip():
        return _error_result("Auto Revision 未产生正文")
    next_round = state.get("review_round", 1) + 1
    return {
        "styled_text": revised,
        "review_round": next_round,
        "revision_used": True,
        "verdict": "",
        "generation_events": record_generation_event(
            state,
            "PROSE_AGENT_EDITED",
            counter=next_round,
        ),
        "workflow_status": "AGENT_EDITED",
    }


@_guard_node
def save_styled(state: ChapterWorkflowState) -> dict[str, Any]:
    """Save the current styled prose before every LLM review."""
    styled = state.get("styled_text", "")
    if not styled:
        return _error_result("styled_text 为空，无法保存")
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    fs.save("chapters", f"chapter_{state['chapter_index']:04d}_styled", styled)
    return {"workflow_status": "STYLED_SAVED"}


@_guard_node
def review_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """Review only prose produced or supplied by the current execution."""
    from src.agents.state_manager.state_manager import StateManager

    styled = state.get("styled_text", "")
    if not styled:
        return _error_result("本次执行没有 styled_text，禁止采用历史文件审阅")

    novel_id = state["novel_id"]
    fs = FileStore(novel_id, get_settings().data_dir)
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        analysis = StateManager(novel_id, sqlite).review_chapter(
            styled,
            state["chapter_index"],
            state.get("chapter_plan_text", ""),
            world_setting=fs.load_canonical("settings", "world_setting") or "",
            book_plan_text=fs.load_tracking_doc("book_plan") or "",
            volume_plan_text=fs.load_tracking_doc("volume_plan") or "",
            current_state_text=state.get("current_state_text", ""),
        )
    finally:
        sqlite.close()
    print(f"  [review_chapter] Review #{state.get('review_round', 1)}")
    return {"raw_analysis": analysis["raw_analysis"], "workflow_status": "REVIEWED"}


@_guard_node
def parse_chapter_decision(state: ChapterWorkflowState) -> dict[str, Any]:
    """Parse prose ReviewDecision deterministically and fail closed."""
    raw = state.get("raw_analysis", "")
    if not raw:
        return {
            **_error_result("raw_analysis 为空，无法解析审阅决策"),
            "verdict": "UNKNOWN",
        }
    decision = _parse_review(raw)
    if decision.verdict == "UNKNOWN":
        return {
            **_error_result("Review verdict UNKNOWN; commit blocked fail-closed"),
            "verdict": "UNKNOWN",
            "review_reasons": decision.reasons,
            "t1_issues": decision.t1_issues,
            "review_issues": [
                *decision.t1_issues,
                *decision.t2_issues,
                *decision.t3_issues,
                *decision.quality_issues,
            ],
            "planning_level": decision.planning_level,
        }
    print(f"  [parse_chapter_decision] Review #{state.get('review_round', 1)}: "
          f"{decision.verdict}")
    result = {
        "verdict": decision.verdict,
        "review_reasons": decision.reasons,
        "review_issues": [
            *decision.t1_issues,
            *decision.t2_issues,
            *decision.t3_issues,
            *decision.quality_issues,
        ],
        "t1_issues": decision.t1_issues,
        "planning_level": decision.planning_level,
        "generation_events": record_generation_event(
            state,
            "PROSE_REVIEWED",
            counter=state.get("review_round", 1),
            details={
                "verdict": decision.verdict,
                "issues": [
                    *decision.t1_issues,
                    *decision.t2_issues,
                    *decision.t3_issues,
                    *decision.quality_issues,
                    *decision.reasons,
                ],
            },
        ),
        "workflow_status": f"DECISION_{decision.verdict}",
    }
    if (
        state.get("chapter_mode", "agent") == "agent"
        and state.get("agent_execution") == "autonomous"
        and decision.verdict == "PASS"
    ):
        result["final_author_approved"] = True
    return result


# Compatibility name for existing callers that import the E07.5 node directly.
parse_decision = parse_chapter_decision


def _route_after_chapter_decision(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    if state.get("chapter_mode", "agent") == "human":
        return "await_human_chapter"
    autonomous = state.get("agent_execution") == "autonomous"
    if not autonomous:
        return "await_human_chapter"
    if state.get("verdict") == "PASS":
        return "commit_canonical_prose"
    if (
        state.get("verdict") == "NEEDS_REVISION"
        and state.get("review_round", 1) <= 2
    ):
        return "agent_edit_chapter"
    if state.get("verdict") == "NEEDS_REVISION":
        return "await_human_chapter"
    return END


def await_human_plan(state: ChapterWorkflowState) -> dict[str, Any]:
    """Pause for a human plan edit, then force another Plan Review."""
    chapter_index = state.get("chapter_index", 0)
    edit_path = f"outlines/chapter_plan_ch{chapter_index:04d}_edited.md"
    resume_value = interrupt({
        "type": "plan_review",
        "novel_id": state.get("novel_id", ""),
        "chapter_index": chapter_index,
        "verdict": state.get("plan_verdict", "UNKNOWN"),
        "planning_level": state.get("plan_planning_level", "L1"),
        "reasons": state.get("plan_review_reasons", []),
        "t1_issues": state.get("plan_t1_issues", []),
        "edit_path": edit_path,
        "allowed_actions": (
            ["approve", "agent_edit", "human_edit", "restart"]
            if state.get("plan_verdict") == "PASS"
            else ["agent_edit", "human_edit", "restart"]
        ),
    })
    if not isinstance(resume_value, dict):
        return _error_result("Human resume value 必须是 decision object")
    action = str(resume_value.get("action", "")).strip().lower()
    if action == "approve" and state.get("plan_verdict") == "PASS":
        return {
            "human_decision": "approve",
            "workflow_status": "PLAN_APPROVED",
        }
    if action == "agent_edit":
        feedback = str(resume_value.get("feedback", "")).strip()
        if state.get("plan_verdict") == "PASS" and not feedback:
            return _error_result(
                "Plan Review PASS 后使用 agent_edit 必须提供非空 human_feedback"
            )
        return {
            "human_decision": "agent_edit",
            "human_feedback": feedback,
            "workflow_status": "PLAN_AGENT_EDIT_REQUESTED",
        }
    edited = str(resume_value.get("edited_text", "")).strip()
    if action != "human_edit" or not edited:
        return _error_result("human_edit 需要非空的 Chapter Plan")
    return {
        "chapter_plan_text": edited,
        "human_decision": "human_edit",
        "human_feedback": str(resume_value.get("feedback", "")).strip(),
        "plan_verdict": "",
        "generation_events": record_generation_event(
            state,
            "PLAN_HUMAN_EDITED",
            counter=state.get("plan_review_attempt", 1),
        ),
        "workflow_status": "HUMAN_PLAN_EDITED",
    }


def _approval_verdict(state: ChapterWorkflowState) -> str:
    if state.get("chapter_mode", "agent") == "human":
        return state.get("consistency_verdict", "UNKNOWN")
    return state.get("verdict", "UNKNOWN")


def _approval_warnings(state: ChapterWorkflowState) -> list[str]:
    if state.get("chapter_mode", "agent") == "human":
        return list(state.get("consistency_warnings", []))
    return [
        *state.get("review_issues", []),
        *state.get("review_reasons", []),
    ]


def _normal_review_passed(state: ChapterWorkflowState) -> bool:
    verdict = _approval_verdict(state)
    expected = "CLEAN" if state.get("chapter_mode", "agent") == "human" else "PASS"
    return verdict == expected


def await_human_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """展示最新 Review/Consistency 结果并要求作者作最终决定。"""
    chapter_index = state.get("chapter_index", 0)
    human_mode = state.get("chapter_mode", "agent") == "human"
    verdict = _approval_verdict(state)
    passed = _normal_review_passed(state)
    edit_path = (
        f"chapters/chapter_{chapter_index:04d}_human_candidate_edited.md"
        if human_mode else
        f"chapters/chapter_{chapter_index:04d}_styled_edited.md"
    )
    if human_mode:
        interrupt_type = "human_final_approval"
        allowed_actions = ["human_edit", "restart", "approve"]
    else:
        interrupt_type = "final_author_approval" if passed else "chapter_review"
        allowed_actions = (
            [
                "approve", "agent_edit", "human_edit",
                "regenerate_prose", "restart",
            ]
            if passed else
            ["agent_edit", "human_edit", "regenerate_prose", "restart"]
        )
    resume_value = interrupt({
        "type": interrupt_type,
        "novel_id": state.get("novel_id", ""),
        "chapter_index": chapter_index,
        "verdict": verdict,
        "planning_level": state.get("planning_level", "L1"),
        "reasons": _approval_warnings(state),
        "t1_issues": state.get("t1_issues", []) if not human_mode else [],
        "review_round": state.get("review_round", 1),
        "edit_path": edit_path,
        "allowed_actions": allowed_actions,
    })
    if not isinstance(resume_value, dict):
        return _error_result("人工恢复值必须是一个决策对象")
    action = str(resume_value.get("action", "")).strip().lower()
    feedback = str(resume_value.get("feedback", "")).strip()
    if action == "approve":
        result = {
            "human_decision": "approve",
            "final_author_approved": True,
            "review_override_confirmed": False,
            "human_feedback": feedback,
            "workflow_status": (
                "FINAL_AUTHOR_APPROVED" if passed else "REVIEW_OVERRIDE_REQUESTED"
            ),
        }
        if not passed:
            result["generation_events"] = record_generation_event(
                state,
                "REVIEW_OVERRIDE_REQUESTED",
                details={"original_verdict": verdict},
            )
        return result
    if action == "agent_edit" and not human_mode:
        if passed and not feedback:
            return _error_result(
                "Prose Review PASS 后使用 agent_edit 必须提供非空 human_feedback"
            )
        return {
            "human_decision": action,
            "human_feedback": feedback,
            "final_author_approved": False,
            "review_override_confirmed": False,
            "workflow_status": "AGENT_EDIT_REQUESTED",
        }
    if action == "human_edit":
        edited = str(resume_value.get("edited_text", "")).strip()
        if not edited:
            return _error_result("human_edit 需要非空正文")
        next_round = state.get("review_round", 1) + 1
        common = {
            "human_decision": action,
            "human_feedback": feedback,
            "final_author_approved": False,
            "review_override_confirmed": False,
            "review_round": next_round,
            "generation_events": record_generation_event(
                state,
                "PROSE_HUMAN_EDITED",
                counter=next_round,
            ),
            "workflow_status": "MANUAL_EDITED",
        }
        if human_mode:
            return {
                **common,
                "candidate_text": edited,
                "candidate_path": edit_path,
                "consistency_raw_analysis": "",
                "consistency_verdict": "",
                "consistency_warnings": [],
            }
        return {**common, "styled_text": edited, "verdict": ""}
    if action == "regenerate_prose" and not human_mode:
        return {
            "human_decision": action,
            "human_feedback": feedback,
            "final_author_approved": False,
            "review_override_confirmed": False,
            "verdict": "",
            "workflow_status": "REGENERATE_REQUESTED",
        }
    return _error_result(f"当前审批阶段不支持操作: {action}")


def await_review_override(state: ChapterWorkflowState) -> dict[str, Any]:
    """非 PASS/CLEAN 的 approve 必须在独立 checkpoint interrupt 二次确认。"""
    verdict = _approval_verdict(state)
    resume_value = interrupt({
        "type": "review_override_confirmation",
        "novel_id": state.get("novel_id", ""),
        "chapter_index": state.get("chapter_index", 0),
        "verdict": verdict,
        "reasons": _approval_warnings(state),
        "message": (
            "当前审阅尚未通过。原审阅结论不会被修改；继续将记录为作者在已知警告后的主动提交。"
        ),
        "allowed_actions": ["confirm_override", "back"],
    })
    if not isinstance(resume_value, dict):
        return _error_result("审阅 override 确认必须是一个决策对象")
    action = str(resume_value.get("action", "")).strip().lower()
    if action == "confirm_override":
        return {
            "human_decision": "confirm_override",
            "final_author_approved": True,
            "review_override_confirmed": True,
            "generation_events": record_generation_event(
                state,
                "REVIEW_OVERRIDE_CONFIRMED",
                details={"original_verdict": verdict},
            ),
            "workflow_status": "REVIEW_OVERRIDE_CONFIRMED",
        }
    if action == "back":
        return {
            "human_decision": "back",
            "final_author_approved": False,
            "review_override_confirmed": False,
            "workflow_status": "REVIEW_OVERRIDE_BACK",
        }
    return _error_result(f"Override 确认阶段不支持操作: {action}")

# Compatibility alias; E07.6 routes to the typed interrupt nodes above.
await_human_review = await_human_chapter


def _route_after_human_plan(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    return {
        "approve": "write_draft",
        "agent_edit": "agent_edit_plan",
        "human_edit": "review_plan",
    }.get(state.get("human_decision", ""), END)


def _route_after_human_chapter(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    decision = state.get("human_decision", "")
    if decision == "approve":
        return (
            "commit_canonical_prose"
            if _normal_review_passed(state) else "await_review_override"
        )
    if decision == "human_edit":
        return (
            "review_consistency"
            if state.get("chapter_mode", "agent") == "human" else "save_styled"
        )
    return {
        "agent_edit": "agent_edit_chapter",
        "regenerate_prose": "write_draft",
    }.get(decision, END)


def _route_after_override(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    if state.get("human_decision") == "confirm_override":
        return "commit_canonical_prose"
    if state.get("human_decision") == "back":
        return "await_human_chapter"
    return END


def _is_canonical_authorized(state: ChapterWorkflowState) -> bool:
    return bool(state.get("final_author_approved")) and (
        _normal_review_passed(state)
        or state.get("review_override_confirmed") is True
    )


def _candidate_prose(state: ChapterWorkflowState) -> str:
    if state.get("chapter_mode", "agent") == "human":
        return state.get("candidate_text", "")
    return state.get("styled_text", "")


@_guard_node
def commit_canonical_prose(state: ChapterWorkflowState) -> dict[str, Any]:
    """Create the one formal chapter from the shared Candidate seam."""
    if not _is_canonical_authorized(state):
        return {
            **_error_result("Canonical 提交需要作者批准，以及 PASS/CLEAN 或显式 override 确认"),
            "commit_success": False,
            "commit_error": "canonical authorization missing",
        }
    candidate = _candidate_prose(state)
    if not candidate.strip():
        return {
            **_error_result("Candidate 正文为空，无法提交 Canonical"),
            "commit_success": False,
            "commit_error": "candidate prose missing",
        }
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    path = fs.commit_canonical_chapter(state["chapter_index"], candidate)
    relative = str(path.relative_to(fs.root)).replace("\\", "/")
    return {
        "commit_success": True,
        "canonical_source_path": relative,
        "generation_events": record_generation_event(
            state,
            "CANONICAL_COMMITTED",
            details={"canonical_source_path": relative},
        ),
        "workflow_status": "CANONICAL_COMMITTED",
    }


# Compatibility name for direct callers; semantics are prose-only now.
commit_state = commit_canonical_prose


def _route_after_commit(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error" or state.get("commit_success") is not True:
        return END
    return "derive_semantics"


def _recovery_event(
    state: ChapterWorkflowState,
    stage: str,
) -> list[GenerationEvent]:
    """Record recovery only after a previously checkpointed stage failure."""
    failed_id = f"{state['chapter_index']}:DERIVATION_FAILED:{stage}"
    if not any(
        event.get("event_id") == failed_id
        for event in state.get("generation_events", [])
    ):
        return []
    return record_generation_event(
        state,
        "DERIVATION_RECOVERED",
        discriminator=stage,
        details={"stage": stage},
    )


def _derived_failure(
    state: ChapterWorkflowState,
    message: str,
    *,
    stage: str,
) -> dict[str, Any]:
    active = dict(state.get("active_derivation_errors", {}))
    previous = active.get(stage)
    active[stage] = message
    warnings = [item for item in state.get("warnings", []) if item != previous]
    if message not in warnings:
        warnings.append(message)
    return {
        "workflow_status": "DERIVATION_ERROR",
        "warnings": warnings,
        "derived_state_errors": list(active.values()),
        "active_derivation_errors": active,
        "failed_derivation_stage": stage,
        "derivation_error": message,
        "generation_events": record_generation_event(
            state,
            "DERIVATION_FAILED",
            discriminator=stage,
            details={"stage": stage, "message": message},
        ),
    }


def _clear_derived_failure(state: ChapterWorkflowState, stage: str) -> dict[str, Any]:
    active = dict(state.get("active_derivation_errors", {}))
    previous = active.pop(stage, None)
    return {
        "warnings": [
            item for item in state.get("warnings", []) if item != previous
        ],
        "active_derivation_errors": active,
        "derived_state_errors": list(active.values()),
        "failed_derivation_stage": "",
        "derivation_error": "",
    }


def derive_semantics(state: ChapterWorkflowState) -> dict[str, Any]:
    """Generate one complete raw Markdown Current State with SYSTEM."""
    from src.agents.state_manager.state_manager import StateManager

    if state.get("commit_success") is not True:
        return _derived_failure(
            state, "Derivation 需要 Canonical 正文", stage="update_current_state"
        )
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    canonical = fs.load_canonical_chapter(state["chapter_index"]) or ""
    if not canonical:
        return _derived_failure(
            state, "Canonical prose missing after commit", stage="update_current_state"
        )
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        raw = StateManager(state["novel_id"], sqlite).update_current_state(
            canonical, state["chapter_index"], state.get("current_state_text", "")
        ).get("updated_current_state", "")
        if not raw.strip():
            raise ValueError("Current State Updater returned empty Markdown")
    except Exception as exc:
        return _derived_failure(
            state, f"Current State 更新生成失败：{type(exc).__name__}: {exc}",
            stage="update_current_state",
        )
    finally:
        sqlite.close()
    return {
        "updated_current_state_text": raw,
        **_clear_derived_failure(state, "update_current_state"),
        "generation_events": _recovery_event(state, "update_current_state"),
        "workflow_status": "SEMANTICS_DERIVED",
    }


def persist_current_state(state: ChapterWorkflowState) -> dict[str, Any]:
    """Atomically save checkpointed raw Markdown without semantic parsing."""
    from src.storage.current_state_store import CurrentStateStore

    fs = FileStore(state["novel_id"], get_settings().data_dir)
    canonical = fs.load_canonical_chapter(state["chapter_index"]) or ""
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        result = CurrentStateStore(state["novel_id"], fs, sqlite).commit_raw(
            state.get("current_state_sha256", ""),
            state.get("updated_current_state_text", ""),
            state["chapter_index"],
            state.get("canonical_source_path", ""),
        )
        if not result or not result.success:
            raise RuntimeError(
                "_commit_result missing" if result is None else result.error_message
            )
    except Exception as exc:
        return _derived_failure(
            state, f"Current State 持久化失败：{type(exc).__name__}: {exc}",
            stage="current-state",
        )
    finally:
        sqlite.close()
    marker = fs.root / "states" / f"chapter_{state['chapter_index']:04d}_derived"
    return {
        "current_state_persisted": True,
        "completion_marker_path": str(marker),
        **_clear_derived_failure(state, "current-state"),
        "generation_events": _recovery_event(state, "current-state"),
        "workflow_status": "CURRENT_STATE_PERSISTED",
    }


def _fact_dict(fact: Any, *, repair_used: bool = False) -> dict[str, Any]:
    return {
        "fact_id": fact.fact_id,
        "chapter_index": fact.chapter_index,
        "source_ranges": fact.source_ranges,
        "fact_text": fact.fact_text,
        "repair_used": repair_used,
    }


def _fact_object(data: dict[str, Any]) -> Any:
    from src.storage.document_formats import AtomicFact
    return AtomicFact(
        fact_id=str(data.get("fact_id", "")),
        chapter_index=int(data.get("chapter_index", 0)),
        source_ranges=list(data.get("source_ranges", [])),
        fact_text=str(data.get("fact_text", "")),
    )


def persist_fact_digest(state: ChapterWorkflowState) -> dict[str, Any]:
    """Derive and checkpoint address-validated Atomic Fact candidates."""
    from src.agents.state_manager.state_manager import StateManager
    from src.storage.atomic_fact_protocol import (
        chapter_paragraphs, format_source_ranges, parse_atomic_facts,
        validate_source_ranges,
    )

    fs = FileStore(state["novel_id"], get_settings().data_dir)
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        canonical = fs.load_canonical_chapter(state["chapter_index"]) or ""
        manager = StateManager(state["novel_id"], sqlite)
        raw = manager.derive_atomic_facts(
            canonical, state["chapter_index"]
        ).get("raw_analysis", "")
        facts = parse_atomic_facts(raw, state["chapter_index"])
        paragraphs = chapter_paragraphs(canonical)
        numbered = "\n\n".join(
            f"[P{index:04d}] {paragraph}"
            for index, paragraph in enumerate(paragraphs, 1)
        )
        candidates = []
        for fact in facts:
            repair_used = False
            try:
                validate_source_ranges(fact, len(paragraphs))
            except ValueError as address_error:
                repaired_raw = manager.repair_atomic_fact(
                    fact.fact_text, format_source_ranges(fact.source_ranges), numbered,
                    f"Source address invalid: {address_error}", state["chapter_index"],
                    len(candidates) + 1,
                ).get("raw_analysis", "").strip()
                repair_used = True
                if repaired_raw.upper() == "DROP":
                    continue
                repaired = parse_atomic_facts(
                    "## Atomic Facts\n\n" + repaired_raw, state["chapter_index"]
                )
                if len(repaired) != 1:
                    raise ValueError("Address repair must return one fact or DROP")
                fact = repaired[0]
                validate_source_ranges(fact, len(paragraphs))
            candidates.append(_fact_dict(fact, repair_used=repair_used))
    except Exception as exc:
        return {
            "fact_digest_generated": False,
            **_derived_failure(
                state, f"Atomic Fact derivation failed: {type(exc).__name__}: {exc}",
                stage="derive_atomic_facts",
            ),
        }
    finally:
        sqlite.close()
    return {
        "atomic_fact_candidates": candidates,
        "atomic_facts_derived": True,
        **_clear_derived_failure(state, "derive_atomic_facts"),
        "generation_events": _recovery_event(state, "derive_atomic_facts"),
        "workflow_status": "ATOMIC_FACTS_DERIVED",
    }


def _verification_payload(facts: list[Any], paragraphs: list[str]) -> str:
    from src.storage.atomic_fact_protocol import format_source_ranges, source_excerpt
    blocks = []
    for index, fact in enumerate(facts, 1):
        blocks.append(
            f"FACT {index}\nFact Text: {fact.fact_text}\n"
            f"Source Range: {format_source_ranges(fact.source_ranges)}\n"
            f"Canonical Source Excerpt:\n{source_excerpt(fact, paragraphs)}"
        )
    return "\n\n---\n\n".join(blocks)


def _run_verification_batch(manager: Any, facts: list[Any], paragraphs: list[str],
                            chapter_index: int, attempt: int) -> list[Any]:
    from src.storage.atomic_fact_protocol import parse_verification_decisions
    payload = _verification_payload(facts, paragraphs)
    raw = manager.verify_atomic_facts(
        payload, chapter_index, attempt=attempt
    ).get("raw_analysis", "")
    try:
        return parse_verification_decisions(raw, len(facts))
    except ValueError as exc:
        corrected = manager.verify_atomic_facts(
            payload, chapter_index,
            protocol_correction=f"{type(exc).__name__}: {exc}\n\n上次输出：\n{raw}",
            attempt=attempt,
        ).get("raw_analysis", "")
        return parse_verification_decisions(corrected, len(facts))


def _repair_failed_fact(manager: Any, fact: Any, paragraphs: list[str],
                        reason: str, chapter_index: int,
                        fact_number: int) -> Any | None:
    from src.storage.atomic_fact_protocol import (
        format_source_ranges, parse_atomic_facts, source_excerpt,
        validate_source_ranges,
    )
    raw = manager.repair_atomic_fact(
        fact.fact_text, format_source_ranges(fact.source_ranges),
        source_excerpt(fact, paragraphs), reason, chapter_index, fact_number,
    ).get("raw_analysis", "").strip()
    if raw.upper() == "DROP":
        return None
    repaired = parse_atomic_facts("## Atomic Facts\n\n" + raw, chapter_index)
    if len(repaired) != 1:
        raise ValueError("Targeted Fact Repair must return one fact or DROP")
    validate_source_ranges(repaired[0], len(paragraphs))
    repaired[0].fact_id = fact.fact_id
    return repaired[0]


def verify_atomic_facts(state: ChapterWorkflowState) -> dict[str, Any]:
    """Verify, correct, and persist facts with finite per-fact passes."""
    from src.agents.state_manager.state_manager import StateManager
    from src.storage.atomic_fact_protocol import chapter_paragraphs, expand_source_ranges
    from src.storage.document_formats import FactDigest

    fs = FileStore(state["novel_id"], get_settings().data_dir)
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        canonical = fs.load_canonical_chapter(state["chapter_index"]) or ""
        paragraphs = chapter_paragraphs(canonical)
        manager = StateManager(state["novel_id"], sqlite)
        initial_data = list(state.get("atomic_fact_candidates", []))
        facts = [_fact_object(item) for item in initial_data]
        repair_used = {
            fact.fact_id: bool(data.get("repair_used", False))
            for fact, data in zip(facts, initial_data)
        }
        accepted: list[Any] = []
        round_two: list[Any] = []
        if facts:
            decisions = _run_verification_batch(
                manager, facts, paragraphs, state["chapter_index"], 1
            )
            for number, (fact, decision) in enumerate(zip(facts, decisions), 1):
                if decision.decision == "VERIFIED":
                    accepted.append(fact)
                elif decision.decision == "INSUFFICIENT":
                    round_two.append(expand_source_ranges(fact, len(paragraphs)))
                else:
                    repaired = _repair_failed_fact(
                        manager, fact, paragraphs, decision.reason,
                        state["chapter_index"], number,
                    )
                    repair_used[fact.fact_id] = True
                    if repaired is not None:
                        round_two.append(repaired)

        round_three: list[Any] = []
        if round_two:
            decisions = _run_verification_batch(
                manager, round_two, paragraphs, state["chapter_index"], 2
            )
            for number, (fact, decision) in enumerate(zip(round_two, decisions), 1):
                if decision.decision == "VERIFIED":
                    accepted.append(fact)
                elif not repair_used.get(fact.fact_id, False):
                    repaired = _repair_failed_fact(
                        manager, fact, paragraphs, decision.reason,
                        state["chapter_index"], number,
                    )
                    repair_used[fact.fact_id] = True
                    if repaired is not None:
                        round_three.append(repaired)
        if round_three:
            decisions = _run_verification_batch(
                manager, round_three, paragraphs, state["chapter_index"], 3
            )
            accepted.extend(
                fact for fact, decision in zip(round_three, decisions)
                if decision.decision == "VERIFIED"
            )

        digest = FactDigest(chapter_index=state["chapter_index"], atomic_facts=accepted)
        content = digest.to_markdown() if accepted else (
            f"# 第{state['chapter_index']}章 Fact Digest\n\n"
            "## Atomic Facts\n\n- 无\n"
        )
        digest_path = fs.save(
            "states", f"fact_digest_ch{state['chapter_index']:04d}", content
        )
    except Exception as exc:
        return {
            "fact_digest_generated": False,
            **_derived_failure(
                state, f"Atomic Fact verification failed: {type(exc).__name__}: {exc}",
                stage="verify_atomic_facts",
            ),
        }
    finally:
        sqlite.close()
    return {
        "verified_atomic_facts": [_fact_dict(fact) for fact in accepted],
        "fact_verification_complete": True,
        "fact_digest_generated": True,
        "fact_digest_path": str(digest_path.relative_to(fs.root)).replace("\\", "/"),
        "atomic_fact_count": len(accepted),
        **_clear_derived_failure(state, "verify_atomic_facts"),
        "generation_events": _recovery_event(state, "verify_atomic_facts"),
        "workflow_status": "FACT_DIGEST_PERSISTED",
    }


def _parse_volume_progress(raw: str) -> str:
    section = re.search(
        r"^##\s+Volume Progress\s*$\n(.*?)(?=^##\s|\Z)",
        raw, re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )
    if section is None:
        return "UNKNOWN"
    candidate = section.group(1)
    recommendation = re.search(
        r"^\s*-?\s*\*\*Recommendation\*\*\s*:\s*"
        r"(CONTINUE|READY_TO_CLOSE|UNKNOWN)\s*$",
        candidate,
        re.IGNORECASE | re.MULTILINE,
    )
    return recommendation.group(1).upper() if recommendation else "UNKNOWN"


def persist_volume_progress(state: ChapterWorkflowState) -> dict[str, Any]:
    """Persist a suggestion only; never close or create a volume."""
    progress = _parse_volume_progress(state.get("derivation_raw_analysis", ""))
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    path = fs.root / "tracking" / "volume_progress.md"
    content = (
        "# Volume Progress\n\n"
        f"- **Through Chapter**: {state['chapter_index']}\n"
        f"- **Recommendation**: {progress}\n\n"
        "> Advisory only. Volume closure always requires an explicit human command.\n"
    )
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        return _derived_failure(
            state, f"Volume Progress persistence failed: {type(exc).__name__}: {exc}",
            stage="volume-progress",
        )
    return {
        "volume_progress": progress,
        "volume_progress_updated": True,
        "volume_progress_path": "tracking/volume_progress.md",
        "generation_events": _recovery_event(state, "volume-progress"),
        "workflow_status": "VOLUME_PROGRESS_PERSISTED",
    }


def persist_chapter_sources(state: ChapterWorkflowState) -> dict[str, Any]:
    """Overwrite the deterministic provenance report for this chapter."""
    try:
        result = save_chapter_sources.__wrapped__(state)
        result["generation_events"] = _recovery_event(state, "chapter-sources")
        result["workflow_status"] = "CHAPTER_SOURCES_PERSISTED"
        return result
    except Exception as exc:
        return _derived_failure(
            state, f"Chapter sources persistence failed: {type(exc).__name__}: {exc}",
            stage="chapter-sources",
        )


def sync_chroma(state: ChapterWorkflowState) -> dict[str, Any]:
    """Replace this chapter's Atomic Facts so retries cannot append duplicates."""
    from src.storage.atomic_fact_store import AtomicFactStore, DEFAULT_BRANCH_ID
    from src.storage.chapter_completion import mark_derived_ready
    from src.storage.document_formats import FactDigest

    try:
        fs = FileStore(state["novel_id"], get_settings().data_dir)
        digest_rel = state.get("fact_digest_path", "")
        digest = FactDigest.from_markdown(
            (fs.root / digest_rel).read_text(encoding="utf-8"))
        count = AtomicFactStore(get_settings().data_dir / "chroma_db").index_facts(
            novel_id=state["novel_id"], branch_id=DEFAULT_BRANCH_ID,
            chapter_index=state["chapter_index"], facts=digest.atomic_facts,
            source_path=state.get("canonical_source_path", ""),
            digest_path=digest_rel,
            canonical_hash=hashlib.sha256(
                (fs.load_canonical_chapter(state["chapter_index"]) or "").encode("utf-8")
            ).hexdigest(),
        )
        completion = mark_derived_ready(
            fs, state["chapter_index"]
        )
    except Exception as exc:
        return {
            "rag_facts": 0, "rag_chunks": 0,
            **_derived_failure(
                state, f"Atomic Fact RAG failed: {type(exc).__name__}: {exc}",
                stage="rag",
            ),
        }
    ready_event = record_generation_event(
        state,
        "DERIVED_READY",
        details={"completion_marker_path": str(completion.relative_to(fs.root)).replace("\\", "/")},
    )
    return {
        "rag_facts": count,
        "rag_chunks": 0,
        "completion_marker_path": str(
            completion.relative_to(fs.root)
        ).replace("\\", "/"),
        "generation_events": [*_recovery_event(state, "rag"), *ready_event],
        "workflow_status": "DERIVED_READY",
    }


def _route_derivation(state: ChapterWorkflowState, status: str, target: str) -> str:
    return target if state.get("workflow_status") == status else END


# Direct-call compatibility for focused persistence tests. The graph routes only
# through the explicit E07.9 stage names above.
rag_index = sync_chroma

def build_chapter_workflow(checkpointer: Any = None) -> Any:
    """Build the stable E07.6 chapter creation backbone."""
    graph = StateGraph(ChapterWorkflowState)
    for name, node in (
        ("preflight", preflight),
        ("load_current_state", load_current_state),
        ("load_chapter_intent", load_chapter_intent),
        ("prepare_human_context", prepare_human_context),
        ("await_human_writing", await_human_writing),
        ("review_consistency", review_consistency),
        ("parse_consistency_decision", parse_consistency_decision),
        ("plan_chapter", plan_chapter),
        ("review_plan", review_plan),
        ("parse_plan_decision", parse_plan_decision),
        ("agent_edit_plan", agent_edit_plan),
        ("await_human_plan", await_human_plan),
        ("write_draft", write_draft),
        ("style_edit", style_edit),
        ("agent_edit_chapter", agent_edit_chapter),
        ("save_styled", save_styled),
        ("review_chapter", review_chapter),
        ("parse_chapter_decision", parse_chapter_decision),
        ("await_human_chapter", await_human_chapter),
        ("await_review_override", await_review_override),
        ("commit_canonical_prose", commit_canonical_prose),
        ("derive_semantics", derive_semantics),
        ("persist_current_state", persist_current_state),
        ("persist_fact_digest", persist_fact_digest),
        ("verify_atomic_facts", verify_atomic_facts),
        ("persist_volume_progress", persist_volume_progress),
        ("persist_chapter_sources", persist_chapter_sources),
        ("sync_chroma", sync_chroma),
    ):
        graph.add_node(name, node)

    graph.add_edge(START, "preflight")
    for node, target in (
        ("preflight", "load_current_state"),
        ("load_current_state", "load_chapter_intent"),
        ("plan_chapter", "review_plan"),
        ("review_plan", "parse_plan_decision"),
        ("write_draft", "style_edit"),
        ("style_edit", "save_styled"),
        ("agent_edit_chapter", "save_styled"),
        ("save_styled", "review_chapter"),
        ("review_chapter", "parse_chapter_decision"),
        ("review_consistency", "parse_consistency_decision"),
    ):
        graph.add_conditional_edges(
            node,
            lambda state, next_node=target: _route_after_node(state, next_node),
            {target: target, END: END},
        )

    graph.add_conditional_edges(
        "load_chapter_intent", _route_after_intent,
        {
            "plan_chapter": "plan_chapter",
            "prepare_human_context": "prepare_human_context",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "prepare_human_context",
        lambda state: _route_after_node(state, "await_human_writing"),
        {"await_human_writing": "await_human_writing", END: END},
    )
    graph.add_conditional_edges(
        "await_human_writing",
        lambda state: _route_after_node(state, "review_consistency"),
        {"review_consistency": "review_consistency", END: END},
    )
    graph.add_conditional_edges(
        "parse_consistency_decision", _route_after_consistency,
        {"await_human_chapter": "await_human_chapter", END: END},
    )

    graph.add_conditional_edges(
        "parse_plan_decision", _route_after_plan_decision,
        {
            "write_draft": "write_draft",
            "agent_edit_plan": "agent_edit_plan",
            "await_human_plan": "await_human_plan",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "agent_edit_plan",
        lambda state: _route_after_node(state, "review_plan"),
        {"review_plan": "review_plan", END: END},
    )
    graph.add_conditional_edges(
        "await_human_plan", _route_after_human_plan,
        {
            "write_draft": "write_draft",
            "agent_edit_plan": "agent_edit_plan",
            "review_plan": "review_plan",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "parse_chapter_decision", _route_after_chapter_decision,
        {
            "await_human_chapter": "await_human_chapter",
            "agent_edit_chapter": "agent_edit_chapter",
            "commit_canonical_prose": "commit_canonical_prose",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "await_human_chapter", _route_after_human_chapter,
        {
            "commit_canonical_prose": "commit_canonical_prose",
            "await_review_override": "await_review_override",
            "review_consistency": "review_consistency",
            "agent_edit_chapter": "agent_edit_chapter",
            "save_styled": "save_styled",
            "write_draft": "write_draft",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "await_review_override", _route_after_override,
        {
            "commit_canonical_prose": "commit_canonical_prose",
            "await_human_chapter": "await_human_chapter",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "commit_canonical_prose", _route_after_commit,
        {"derive_semantics": "derive_semantics", END: END},
    )
    for node, status, target in (
        ("derive_semantics", "SEMANTICS_DERIVED", "persist_current_state"),
        ("persist_current_state", "CURRENT_STATE_PERSISTED", "persist_fact_digest"),
        ("persist_fact_digest", "ATOMIC_FACTS_DERIVED", "verify_atomic_facts"),
        ("verify_atomic_facts", "FACT_DIGEST_PERSISTED", "persist_volume_progress"),
        ("persist_volume_progress", "VOLUME_PROGRESS_PERSISTED", "persist_chapter_sources"),
        ("persist_chapter_sources", "CHAPTER_SOURCES_PERSISTED", "sync_chroma"),
    ):
        graph.add_conditional_edges(
            node,
            lambda state, expected=status, next_node=target: _route_derivation(
                state, expected, next_node),
            {target: target, END: END},
        )
    graph.add_edge("sync_chroma", END)
    return graph.compile(checkpointer=checkpointer)
