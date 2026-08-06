"""Checkpointed E07.6 single-chapter production workflow.

LangGraph owns orchestration. Review, canonical prose commit, and derivation
are separate boundaries.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import wraps
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.storage.sqlite_store import SQLiteStore


class ChapterWorkflowState(TypedDict, total=False):
    """Data that must cross nodes in one checkpointed chapter execution."""

    novel_id: str
    branch_id: str
    chapter_index: int

    chapter_outline: str
    extra_instructions: str
    chapter_intent: str
    chapter_mode: str

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
    current_state_persisted: bool
    volume_progress: str
    volume_progress_updated: bool
    volume_progress_path: str

    plan_raw_analysis: str
    plan_verdict: str
    plan_review_reasons: list[str]
    plan_t1_issues: list[str]
    plan_planning_level: str
    plan_review_attempt: int

    raw_analysis: str
    verdict: str
    consistency_raw_analysis: str
    consistency_verdict: str
    consistency_warnings: list[str]
    review_reasons: list[str]
    t1_issues: list[str]
    planning_level: str
    review_round: int
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
    derived_state_errors: list[str]

    warnings: list[str]
    fact_digest_generated: bool
    rag_chunks: int

    workflow_status: str
    error: str | None


def _error_result(message: str) -> dict[str, Any]:
    return {"workflow_status": "error", "error": message}


def _guard_node(
    node: Callable[[ChapterWorkflowState], dict[str, Any]],
) -> Callable[[ChapterWorkflowState], dict[str, Any]]:
    """Keep runtime/API/database/disk failures on the error path."""
    @wraps(node)
    def guarded(state: ChapterWorkflowState) -> dict[str, Any]:
        try:
            result = node(state)
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
        return _error_result("Invalid novel_id: a non-empty string is required")

    chapter_index = state.get("chapter_index")
    if (isinstance(chapter_index, bool)
            or not isinstance(chapter_index, int)
            or chapter_index <= 0):
        return _error_result("Invalid chapter_index: a positive integer is required")

    branch_id = state.get("branch_id", "main")
    if branch_id != "main":
        return _error_result(
            f"Unsupported branch_id '{branch_id}': E07 currently supports only 'main'"
        )

    chapter_mode = state.get("chapter_mode", "agent")
    if chapter_mode not in {"agent", "human"}:
        return _error_result(
            f"Invalid chapter_mode {chapter_mode!r}: expected 'agent' or 'human'"
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
        _current, text, digest = CurrentStateStore(
            state["novel_id"], fs, sqlite
        ).ensure_initialized()
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
    return {"chapter_intent": intent, "workflow_status": "INTENT_LOADED"}


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


@_guard_node
def prepare_human_context(state: ChapterWorkflowState) -> dict[str, Any]:
    """Retrieve bounded history and persist an author-readable generated report."""
    from src.workflows.retrieval_service import ChapterRetrievalService

    intent = state.get("chapter_intent", "").strip()
    if not intent:
        return _error_result(
            "Human Mode requires a non-empty Chapter Intent for historical retrieval."
        )
    retrieval = ChapterRetrievalService(state["novel_id"]).retrieve(
        state["chapter_index"],
        chapter_intent=intent,
        current_state_text=state.get("current_state_text", ""),
        query_mode="human",
    )
    if not retrieval.trace.success:
        return _error_result(
            "Historical retrieval failed: " + retrieval.trace.error_message
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
    return {
        "historical_evidence": retrieval.evidence,
        "retrieval_success": True,
        "retrieval_result_count": len(retrieval.trace.results),
        "retrieval_trace_path": retrieval.trace_path,
        "retrieved_facts": retrieval.fact_candidates,
        "expanded_sources": retrieval.source_excerpts,
        "writing_context_path": str(path.relative_to(fs.root)).replace("\\", "/"),
        "warnings": retrieval.warnings,
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
        "allowed_actions": ["submit", "pause", "discard"],
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
    """确定性解析 CLEAN/WARN；缺失或非法结论保持 fail-closed。"""
    raw = state.get("consistency_raw_analysis", "")
    match = re.search(
        r"\*\*结论\*\*\s*[:：]\s*(CLEAN|WARN)\b", raw, re.IGNORECASE
    )
    if not match:
        return {
            **_error_result("一致性检查缺少有效的 CLEAN/WARN 结论"),
            "consistency_verdict": "UNKNOWN",
        }
    verdict = match.group(1).upper()
    section = re.search(
        r"^## 连续性问题\s*\n(.*?)(?=^## |\Z)",
        raw,
        re.MULTILINE | re.DOTALL,
    )
    warnings = []
    if section:
        warnings = [
            line.strip()[2:].strip()
            for line in section.group(1).splitlines()
            if line.strip().startswith("- ")
            and line.strip()[2:].strip() not in {"", "无"}
        ]
    if verdict == "WARN" and not warnings:
        reason = re.search(r"\*\*主要问题\*\*\s*[:：]\s*(.+)", raw)
        if reason and reason.group(1).strip() not in {"", "无"}:
            warnings = [
                item.strip() for item in reason.group(1).split(";") if item.strip()
            ]
    return {
        "consistency_verdict": verdict,
        "consistency_warnings": warnings,
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

    retrieval = ChapterRetrievalService(novel_id).retrieve(
        chapter_index,
        outline,
        instructions,
        chapter_intent=intent,
        current_state_text=state.get("current_state_text", ""),
    )
    if not retrieval.trace.success:
        return _error_result(
            "Historical retrieval failed: " + retrieval.trace.error_message
        )
    if retrieval.warnings:
        return _error_result("; ".join(retrieval.warnings))

    planner = ChapterPlanner(novel_id)
    plan = planner.plan_chapter(
        chapter_index,
        outline,
        instructions,
        rag_evidence=retrieval.evidence,
        chapter_intent=intent,
        current_state_text=state.get("current_state_text", ""),
    )
    fs = FileStore(novel_id, get_settings().data_dir)
    plan_text = fs.load_canonical(
        "outlines", f"chapter_plan_ch{chapter_index:04d}"
    ) or ""
    if not plan_text.strip():
        return _error_result("ChapterPlanner 未生成可审阅的 canonical Chapter Plan")

    print(f"  [plan_chapter] {len(plan.scenes)} scenes planned")
    return {
        "chapter_plan_text": plan_text,
        "historical_evidence": retrieval.evidence,
        "retrieval_success": retrieval.trace.success,
        "retrieval_result_count": len(retrieval.trace.results),
        "retrieval_trace_path": retrieval.trace_path,
        "warnings": retrieval.warnings,
        "retrieved_facts": retrieval.fact_candidates,
        "expanded_sources": retrieval.source_excerpts,
        "plan_review_attempt": 0,
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
        "plan_planning_level": decision.planning_level,
        "workflow_status": f"PLAN_DECISION_{decision.verdict}",
    }


def _route_after_plan_decision(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    if state.get("plan_verdict") == "PASS":
        return "write_draft"
    if state.get("plan_verdict") in ("NEEDS_REVISION", "HALT"):
        return "await_human_plan"
    return END



@_guard_node
def save_chapter_sources(state: ChapterWorkflowState) -> dict[str, Any]:
    """Write a truthful provenance report for Agent or Human creation."""
    if state.get("chapter_mode", "agent") == "human":
        chapter_index = state["chapter_index"]
        facts = list(state.get("retrieved_facts", []))
        excerpts = list(state.get("expanded_sources", []))
        lines = [
            f"# Chapter {chapter_index} Sources",
            "",
            "> 自动生成的来源报告；它记录系统提供给作者的历史上下文，不表示作者必然采用了这些事实。",
            "",
            "## Chapter Intent",
            state.get("chapter_intent", "") or "暂无",
            "",
            "## Human Writing Context Sources",
            f"- Writing Context: `{state.get('writing_context_path', '')}`",
            f"- Retrieval Trace: `{state.get('retrieval_trace_path', '')}`",
            "",
            "## Consistency and Author Approval Audit",
            f"- Consistency Verdict: `{state.get('consistency_verdict', 'UNKNOWN')}`",
            f"- Review Override Confirmed: `{str(state.get('review_override_confirmed') is True).lower()}`",
            "- Consistency Warnings:",
            *(
                [f"  - {warning}" for warning in state.get("consistency_warnings", [])]
                or ["  - 无"]
            ),
            "",
            "## Provided Historical Facts",
        ]
        if facts:
            for fact in facts:
                lines.append(
                    f"- **{fact.get('fact_id', '')}** (Chapter "
                    f"{fact.get('chapter_index', 0)}, "
                    f"{fact.get('fact_type', 'event')}): {fact.get('text', '')}"
                )
        else:
            lines.append("- 无")
        lines.extend(["", "## Provided Canonical Prose Excerpts"])
        if excerpts:
            for source in excerpts:
                lines.append(
                    f"- **{source.get('fact_id', '')}**: "
                    f"`{source.get('source_path', '')}` paragraphs "
                    f"{source.get('paragraph_start', 0)}-"
                    f"{source.get('paragraph_end', 0)} (provided-context)"
                )
        else:
            lines.append("- 无")
        fs = FileStore(state["novel_id"], get_settings().data_dir)
        path = (
            fs.root / "sources" / f"chapter_{chapter_index:04d}"
            / "chapter_sources.md"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {
            "chapter_sources_path": str(path.relative_to(fs.root)).replace("\\", "/"),
            "workflow_status": "SOURCES_SAVED",
        }

    from src.storage.document_formats import ChapterPlan

    plan_text = state.get("chapter_plan_text", "")
    plan = ChapterPlan.from_markdown(plan_text)
    adopted_ids = set(re.findall(r"FACT-\d{4}-\d{3}", plan_text))
    candidates = [
        fact for fact in state.get("retrieved_facts", [])
        if fact.get("fact_id") in adopted_ids
    ]
    excerpts = list(state.get("expanded_sources", []))
    chapter_index = state["chapter_index"]
    lines = [
        f"# Chapter {chapter_index} Sources",
        "",
        "> 自动生成的来源报告；请修改生产源后重新生成，不要直接编辑本文件。",
        "",
        "## Chapter Intent",
        state.get("chapter_intent", "") or "暂无（本章未提供人工 Intent）",
        "",
        "## Planning Sources",
        "- Book Plan: `tracking/book_plan.md`",
        "- Volume Plan: `tracking/volume_plan.md`",
        "",
        "## Adopted Historical Facts",
    ]
    if candidates:
        for fact in candidates:
            lines.append(
                f"- **{fact['fact_id']}** (Chapter {fact['chapter_index']}, "
                f"{fact.get('fact_type', 'event')}): {fact.get('text', '')}"
            )
    else:
        lines.append("- 无")
    lines.extend(["", "## Future Planning Constraints"])
    lines.append(plan.context.future_constraints or "暂无")
    lines.extend(["", "## Expanded Historical Prose"])
    if excerpts:
        for source in excerpts:
            usage = (
                "adopted" if source.get("fact_id") in adopted_ids
                else "candidate-only"
            )
            lines.append(
                f"- **{source['fact_id']}**: `{source['source_path']}` "
                f"paragraphs {source['paragraph_start']}-"
                f"{source['paragraph_end']} ({usage})"
            )
    else:
        lines.append("- 无")
    lines.extend([
        "",
        "## Review and Author Approval Audit",
        f"- Review Verdict: `{state.get('verdict', 'UNKNOWN')}`",
        f"- Review Override Confirmed: `{str(state.get('review_override_confirmed') is True).lower()}`",
    ])
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    path = fs.root / "sources" / f"chapter_{chapter_index:04d}" / "chapter_sources.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "chapter_sources_path": str(path.relative_to(fs.root)).replace("\\", "/"),
        "workflow_status": "SOURCES_SAVED",
    }
@_guard_node
def write_draft(state: ChapterWorkflowState) -> dict[str, Any]:
    """Write from the approved Chapter Plan, not from future plans."""
    from src.agents.author.deepseek_writer import DeepSeekWriter
    from src.storage.document_formats import ChapterPlan

    if state.get("plan_verdict") != "PASS":
        return _error_result("Writer blocked: latest Plan Review verdict is not PASS")
    plan_text = state.get("chapter_plan_text", "")
    if not plan_text:
        return _error_result("Chapter Plan 为空，无法写作")

    plan = ChapterPlan.from_markdown(plan_text)
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    # Writer sees the approved plan's curated Part B, limited world rules, and
    # previous prose continuity. It does not load Book Plan or Volume Plan.
    draft = DeepSeekWriter(state["novel_id"]).write_chapter(
        plan,
        fs.load_canonical("settings", "world_setting") or "",
        _load_prev_chapter_end(fs, state["chapter_index"]),
    )
    return {
        "draft_text": draft,
        "review_round": 1,
        "revision_used": False,
        "workflow_status": "DRAFTED",
    }


def _load_prev_chapter_end(fs: FileStore, chapter_index: int) -> str:
    if chapter_index <= 1:
        return ""
    prev = fs.load_canonical_chapter(chapter_index - 1)
    if not prev:
        return ""
    return prev[-500:] if len(prev) > 500 else prev


@_guard_node
def style_edit(state: ChapterWorkflowState) -> dict[str, Any]:
    """Style-edit the initial draft without adding planning context."""
    from src.agents.author.claude_stylist import ClaudeStylist
    from src.storage.document_formats import ChapterPlan

    draft = state.get("draft_text", "")
    if not draft:
        return _error_result("draft_text 为空，无法执行 style_edit")
    plan_text = state.get("chapter_plan_text", "")
    plan = ChapterPlan.from_markdown(plan_text) if plan_text else None
    styled = ClaudeStylist(state["novel_id"]).edit_chapter(
        draft,
        state["chapter_index"],
        emotion_palette=plan.context.emotion_palette if plan else "",
        scene_plan_text=plan_text[:3000],
    )
    return {"styled_text": styled, "workflow_status": "STYLED"}


@_guard_node
def agent_edit_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """Locally revise current prose only after an explicit human decision."""
    from src.agents.author.deepseek_writer import DeepSeekWriter
    from src.storage.document_formats import ChapterPlan

    if state.get("human_decision") != "agent_edit":
        return _error_result("agent_edit requires an explicit human decision")

    plan = ChapterPlan.from_markdown(state.get("chapter_plan_text", ""))
    revised = DeepSeekWriter(state["novel_id"]).revise_chapter(
        plan,
        state.get("styled_text", ""),
        [*state.get("review_reasons", []), state.get("human_feedback", "")],
        state.get("t1_issues", []),
    )
    if not revised.strip():
        return _error_result("Auto Revision 未产生正文")
    return {
        "styled_text": revised,
        "review_round": state.get("review_round", 1) + 1,
        "revision_used": True,
        "verdict": "",
        "workflow_status": "AGENT_EDITED",
    }


# Compatibility import only; the Graph never invokes revision automatically.
auto_revise_chapter = agent_edit_chapter


@_guard_node
def save_styled(state: ChapterWorkflowState) -> dict[str, Any]:
    """Save/check the current styled prose before every review."""
    from src.agents.author.style_checker import StyleChecker

    styled = state.get("styled_text", "")
    if not styled:
        return _error_result("styled_text 为空，无法保存")
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    saved_path = fs.save("chapters", f"chapter_{state['chapter_index']:04d}_styled", styled)
    report = StyleChecker(styled).check_all(
        file_path=f"第{state['chapter_index']}章"
    )
    print(report.summary())
    if report.errors > 0:
        print(f"\n  [!] {report.errors} 个错误 + {report.warnings} 个警告，请人工复核。")
    return {
        "workflow_status": "STYLED_SAVED",
    }


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
            "planning_level": decision.planning_level,
        }
    print(f"  [parse_chapter_decision] Review #{state.get('review_round', 1)}: "
          f"{decision.verdict}")
    return {
        "verdict": decision.verdict,
        "review_reasons": decision.reasons,
        "t1_issues": decision.t1_issues,
        "planning_level": decision.planning_level,
        "workflow_status": f"DECISION_{decision.verdict}",
    }


# Compatibility name for existing callers that import the E07.5 node directly.
parse_decision = parse_chapter_decision


def _route_after_chapter_decision(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    if state.get("verdict") in ("PASS", "NEEDS_REVISION", "HALT"):
        return "await_human_chapter"
    return END


def _stop_after_human(state: ChapterWorkflowState) -> dict[str, Any]:
    verdict = state.get("verdict", state.get("plan_verdict", "UNKNOWN"))
    return {
        "human_decision": "stop",
        "commit_success": False,
        "commit_error": f"Human stopped non-PASS execution: {verdict}",
        "workflow_status": "STOPPED_NON_PASS",
    }


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
        "allowed_actions": ["edit", "stop"],
    })
    if not isinstance(resume_value, dict):
        return _error_result("Human resume value must be a decision object")
    action = str(resume_value.get("action", "")).strip().lower()
    if action == "stop":
        return _stop_after_human(state)
    edited = str(resume_value.get("edited_text", "")).strip()
    if action != "edit" or not edited:
        return _error_result("Plan resume requires a non-empty human edit")
    return {
        "chapter_plan_text": edited,
        "human_decision": "edit",
        "human_feedback": str(resume_value.get("feedback", "")).strip(),
        "plan_verdict": "",
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
        *state.get("t1_issues", []),
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
        allowed_actions = ["manual_edit", "pause", "discard", "approve"]
    else:
        interrupt_type = "final_author_approval" if passed else "chapter_review"
        allowed_actions = [
            "agent_edit", "manual_edit", "regenerate", "pause", "discard", "approve"
        ]
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
        return {
            "human_decision": "approve",
            "final_author_approved": True,
            "review_override_confirmed": False,
            "human_feedback": feedback,
            "workflow_status": (
                "FINAL_AUTHOR_APPROVED" if passed else "REVIEW_OVERRIDE_REQUESTED"
            ),
        }
    if action == "agent_edit" and not human_mode:
        return {
            "human_decision": action,
            "human_feedback": feedback,
            "final_author_approved": False,
            "review_override_confirmed": False,
            "workflow_status": "AGENT_EDIT_REQUESTED",
        }
    if action == "manual_edit":
        edited = str(resume_value.get("edited_text", "")).strip()
        if not edited:
            return _error_result("manual_edit 需要非空正文")
        common = {
            "human_decision": action,
            "human_feedback": feedback,
            "final_author_approved": False,
            "review_override_confirmed": False,
            "review_round": state.get("review_round", 1) + 1,
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
    if action == "regenerate" and not human_mode:
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
        "allowed_actions": ["confirm_override", "back", "pause", "discard"],
    })
    if not isinstance(resume_value, dict):
        return _error_result("审阅 override 确认必须是一个决策对象")
    action = str(resume_value.get("action", "")).strip().lower()
    if action == "confirm_override":
        return {
            "human_decision": "confirm_override",
            "final_author_approved": True,
            "review_override_confirmed": True,
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
    return "review_plan" if state.get("human_decision") == "edit" else END


def _route_after_human_chapter(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    decision = state.get("human_decision", "")
    if decision == "approve":
        return (
            "commit_canonical_prose"
            if _normal_review_passed(state) else "await_review_override"
        )
    if decision == "manual_edit":
        return (
            "review_consistency"
            if state.get("chapter_mode", "agent") == "human" else "save_styled"
        )
    return {
        "agent_edit": "agent_edit_chapter",
        "regenerate": "write_draft",
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
        "workflow_status": "CANONICAL_COMMITTED",
    }


# Compatibility name for direct callers; semantics are prose-only now.
commit_state = commit_canonical_prose


def _route_after_commit(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error" or state.get("commit_success") is not True:
        return END
    return "derive_semantics"


def _derived_failure(state: ChapterWorkflowState, message: str) -> dict[str, Any]:
    return {
        "workflow_status": "DERIVATION_ERROR",
        "warnings": [*state.get("warnings", []), message],
        "derived_state_errors": [*state.get("derived_state_errors", []), message],
    }


def derive_semantics(state: ChapterWorkflowState) -> dict[str, Any]:
    """Call semantic derivation once and checkpoint its complete raw result."""
    from src.agents.state_manager.state_manager import StateManager

    if state.get("commit_success") is not True:
        return _derived_failure(state, "Derivation requires canonical prose")
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    canonical = fs.load_canonical_chapter(state["chapter_index"]) or ""
    if not canonical:
        return _derived_failure(state, "Canonical prose missing after commit")
    volume_plan = fs.load_tracking_doc("volume_plan") or ""
    if volume_plan:
        from src.storage.document_formats import VolumePlan
        try:
            if VolumePlan.from_markdown(volume_plan).status.upper() != "ACTIVE":
                volume_plan = ""
        except ValueError:
            volume_plan = ""
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        raw = StateManager(state["novel_id"], sqlite).derive_chapter(
            canonical,
            state["chapter_index"],
            state.get("current_state_text", ""),
            current_volume_plan=volume_plan,
        ).get("raw_analysis", "")
        if not raw.strip():
            raise ValueError("Deriver returned empty analysis")
    except Exception as exc:
        return _derived_failure(
            state, f"Semantic derivation failed: {type(exc).__name__}: {exc}"
        )
    finally:
        sqlite.close()
    return {
        "derivation_raw_analysis": raw,
        "workflow_status": "SEMANTICS_DERIVED",
    }


def persist_current_state(state: ChapterWorkflowState) -> dict[str, Any]:
    """Deterministically apply the checkpointed State Delta exactly once."""
    from src.agents.state_manager.state_manager import StateManager
    from src.storage.document_formats import ChapterPlan

    fs = FileStore(state["novel_id"], get_settings().data_dir)
    canonical = fs.load_canonical_chapter(state["chapter_index"]) or ""
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        chapter_title = ""
        if state.get("chapter_mode", "agent") != "human":
            chapter_title = ChapterPlan.from_markdown(
                state.get("chapter_plan_text", "")
            ).title
        changes = StateManager(state["novel_id"], sqlite).update_tracking_docs(
            state["chapter_index"], canonical,
            state.get("derivation_raw_analysis", ""),
            expected_state_sha256=state.get("current_state_sha256", ""),
            chapter_title=chapter_title,
            canonical_source_path=state.get("canonical_source_path", ""),
        )
        result = changes.get("_commit_result")
        if not result or not result.success:
            raise RuntimeError(
                "_commit_result missing" if result is None else result.error_message
            )
    except Exception as exc:
        return _derived_failure(
            state, f"Current State persistence failed: {type(exc).__name__}: {exc}"
        )
    finally:
        sqlite.close()
    marker = fs.root / "states" / f"chapter_{state['chapter_index']:04d}_derived"
    return {
        "current_state_persisted": True,
        "completion_marker_path": str(marker),
        "workflow_status": "CURRENT_STATE_PERSISTED",
    }


def persist_fact_digest(state: ChapterWorkflowState) -> dict[str, Any]:
    """Persist one deterministic Fact Digest from checkpointed semantics."""
    from src.agents.state_manager.state_manager import StateManager
    from src.storage.document_formats import FactDigest

    fs = FileStore(state["novel_id"], get_settings().data_dir)
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        raw = state.get("derivation_raw_analysis", "")
        if not raw:
            raise ValueError("derivation_raw_analysis is empty")
        digest = StateManager(state["novel_id"], sqlite).extract_fact_digest_from_analysis(
            raw, state["chapter_index"]
        )
        generated = bool(digest.atomic_facts) or any([
            digest.confirmed_items.strip(), digest.confirmed_character_states.strip(),
            digest.confirmed_events.strip(), digest.confirmed_numbers.strip(),
            digest.explicitly_absent.strip(), digest.pending_suspense.strip(),
        ])
        if not generated:
            raise ValueError("Fact Digest contains no derived facts")
        paths = sorted((fs.root / "states").glob(
            f"fact_digest_ch{state['chapter_index']:04d}_*.md"), reverse=True)
        if not paths:
            paths = [fs.save("states", f"fact_digest_ch{state['chapter_index']:04d}",
                             digest.to_markdown())]
        digest_path = paths[0]
        canonical_digest = FactDigest.from_markdown(
            digest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "fact_digest_generated": False,
            **_derived_failure(
                state, f"Fact Digest persistence failed: {type(exc).__name__}: {exc}"
            ),
        }
    finally:
        sqlite.close()
    return {
        "fact_digest_generated": True,
        "fact_digest_path": str(digest_path.relative_to(fs.root)).replace("\\", "/"),
        "atomic_fact_count": len(canonical_digest.atomic_facts),
        "workflow_status": "FACT_DIGEST_PERSISTED",
    }


def _parse_volume_progress(raw: str) -> str:
    section = re.search(
        r"(?:##\s*)?Volume Progress[^\n]*\n(.*?)(?=\n##\s|\Z)",
        raw, re.IGNORECASE | re.DOTALL,
    )
    candidate = section.group(1) if section else raw
    matches = re.findall(r"\b(CONTINUE|READY_TO_CLOSE|UNKNOWN)\b", candidate.upper())
    return matches[-1] if matches else "UNKNOWN"


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
            state, f"Volume Progress persistence failed: {type(exc).__name__}: {exc}"
        )
    return {
        "volume_progress": progress,
        "volume_progress_updated": True,
        "volume_progress_path": "tracking/volume_progress.md",
        "workflow_status": "VOLUME_PROGRESS_PERSISTED",
    }


def persist_chapter_sources(state: ChapterWorkflowState) -> dict[str, Any]:
    """Overwrite the deterministic provenance report for this chapter."""
    try:
        result = save_chapter_sources.__wrapped__(state)
        result["workflow_status"] = "CHAPTER_SOURCES_PERSISTED"
        return result
    except Exception as exc:
        return _derived_failure(
            state, f"Chapter sources persistence failed: {type(exc).__name__}: {exc}"
        )


def sync_chroma(state: ChapterWorkflowState) -> dict[str, Any]:
    """Replace this chapter's Atomic Facts so retries cannot append duplicates."""
    from src.storage.atomic_fact_store import AtomicFactStore, DEFAULT_BRANCH_ID
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
        )
        if count <= 0:
            raise ValueError("Atomic Fact list is empty")
    except Exception as exc:
        return {
            "rag_facts": 0, "rag_chunks": 0,
            **_derived_failure(
                state, f"Atomic Fact RAG failed: {type(exc).__name__}: {exc}"
            ),
        }
    return {
        "rag_facts": count,
        "rag_chunks": 0,
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
        {"write_draft": "write_draft", "await_human_plan": "await_human_plan", END: END},
    )
    graph.add_conditional_edges(
        "await_human_plan", _route_after_human_plan,
        {"review_plan": "review_plan", END: END},
    )
    graph.add_conditional_edges(
        "parse_chapter_decision", _route_after_chapter_decision,
        {
            "await_human_chapter": "await_human_chapter",
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
        ("persist_fact_digest", "FACT_DIGEST_PERSISTED", "persist_volume_progress"),
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
