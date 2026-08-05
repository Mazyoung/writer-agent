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

    chapter_plan_text: str
    historical_evidence: str
    current_state_text: str
    current_state_sha256: str
    draft_text: str
    styled_text: str
    styled_source_path: str
    canonical_source_path: str
    derivation_raw_analysis: str

    plan_raw_analysis: str
    plan_verdict: str
    plan_review_reasons: list[str]
    plan_t1_issues: list[str]
    plan_planning_level: str
    plan_review_attempt: int

    raw_analysis: str
    verdict: str
    review_reasons: list[str]
    t1_issues: list[str]
    planning_level: str
    review_round: int
    revision_used: bool

    human_decision: str
    human_feedback: str
    final_author_approved: bool

    commit_success: bool
    commit_error: str
    completion_marker_path: str

    retrieval_success: bool
    retrieval_result_count: int
    retrieval_trace_path: str
    retrieved_facts: list[dict]
    expanded_sources: list[dict]
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
    """Write a stable automatic provenance report from the approved plan."""
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
        "styled_source_path": str(saved_path.relative_to(fs.root)).replace("\\", "/"),
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


def await_human_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """Require a human decision after every prose Review, including PASS."""
    chapter_index = state.get("chapter_index", 0)
    edit_path = f"chapters/chapter_{chapter_index:04d}_styled_edited.md"
    passed = state.get("verdict") == "PASS"
    resume_value = interrupt({
        "type": "final_author_approval" if passed else "chapter_review",
        "novel_id": state.get("novel_id", ""),
        "chapter_index": chapter_index,
        "verdict": state.get("verdict", "UNKNOWN"),
        "planning_level": state.get("planning_level", "L1"),
        "reasons": state.get("review_reasons", []),
        "t1_issues": state.get("t1_issues", []),
        "review_round": state.get("review_round", 1),
        "edit_path": edit_path,
        "allowed_actions": [
            "agent_edit", "manual_edit", "regenerate", "pause", "discard",
            *(["approve"] if passed else []),
        ],
    })
    if not isinstance(resume_value, dict):
        return _error_result("Human resume value must be a decision object")
    action = str(resume_value.get("action", "")).strip().lower()
    feedback = str(resume_value.get("feedback", "")).strip()
    if action == "approve":
        if not passed:
            return _error_result("Only a latest Review PASS can be approved")
        return {
            "human_decision": "approve",
            "final_author_approved": True,
            "human_feedback": feedback,
            "workflow_status": "FINAL_AUTHOR_APPROVED",
        }
    if action == "agent_edit":
        return {
            "human_decision": action,
            "human_feedback": feedback,
            "final_author_approved": False,
            "workflow_status": "AGENT_EDIT_REQUESTED",
        }
    if action == "manual_edit":
        edited = str(resume_value.get("edited_text", "")).strip()
        if not edited:
            return _error_result("manual_edit requires non-empty edited prose")
        return {
            "styled_text": edited,
            "human_decision": action,
            "human_feedback": feedback,
            "final_author_approved": False,
            "review_round": state.get("review_round", 1) + 1,
            "verdict": "",
            "workflow_status": "MANUAL_EDITED",
        }
    if action == "regenerate":
        return {
            "human_decision": action,
            "human_feedback": feedback,
            "final_author_approved": False,
            "verdict": "",
            "workflow_status": "REGENERATE_REQUESTED",
        }
    return _error_result(f"Unsupported in-graph human action: {action}")


# Compatibility alias; E07.6 routes to the typed interrupt nodes above.
await_human_review = await_human_chapter


def _route_after_human_plan(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    return "review_plan" if state.get("human_decision") == "edit" else END


def _route_after_human_chapter(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    return {
        "approve": "commit_canonical_prose",
        "agent_edit": "agent_edit_chapter",
        "manual_edit": "save_styled",
        "regenerate": "write_draft",
    }.get(state.get("human_decision", ""), END)


@_guard_node
def commit_canonical_prose(state: ChapterWorkflowState) -> dict[str, Any]:
    """Create the one formal chapter only after explicit final approval."""
    if state.get("verdict") != "PASS" or not state.get("final_author_approved"):
        return {
            **_error_result("Canonical commit requires Review PASS and final approval"),
            "commit_success": False,
            "commit_error": "final author approval missing",
        }
    styled = state.get("styled_text", "")
    if not styled.strip():
        return {
            **_error_result("styled_text 为空，无法提交 canonical prose"),
            "commit_success": False,
            "commit_error": "styled_text missing",
        }
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    path = fs.commit_canonical_chapter(state["chapter_index"], styled)
    relative = str(path.relative_to(fs.root)).replace("\\", "/")
    return {
        "commit_success": True,
        "canonical_source_path": relative,
        "styled_source_path": relative,
        "workflow_status": "CANONICAL_COMMITTED",
    }


# Compatibility name for direct callers; semantics are prose-only now.
commit_state = commit_canonical_prose


def _route_after_commit(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error" or state.get("commit_success") is not True:
        return END
    return "derive_chapter"



def _derived_failure(state: ChapterWorkflowState, message: str) -> dict[str, Any]:
    return {
        "workflow_status": "DERIVATION_ERROR",
        "warnings": [*state.get("warnings", []), message],
        "derived_state_errors": [*state.get("derived_state_errors", []), message],
    }


def derive_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """Semantically derive facts/state from canonical prose, then persist state."""
    from src.agents.state_manager.state_manager import StateManager
    from src.storage.document_formats import ChapterPlan

    if state.get("commit_success") is not True:
        return _derived_failure(state, "Derivation requires canonical prose")
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    canonical = fs.load_canonical_chapter(state["chapter_index"]) or ""
    if not canonical:
        return _derived_failure(state, "Canonical prose missing after commit")
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        manager = StateManager(state["novel_id"], sqlite)
        derived = manager.derive_chapter(
            canonical, state["chapter_index"], state.get("current_state_text", "")
        )
        raw = derived.get("raw_analysis", "")
        if not raw.strip():
            raise ValueError("Deriver returned empty analysis")
        chapter_plan = ChapterPlan.from_markdown(state.get("chapter_plan_text", ""))
        changes = manager.update_tracking_docs(
            state["chapter_index"], canonical, raw,
            expected_state_sha256=state.get("current_state_sha256", ""),
            chapter_title=chapter_plan.title,
            styled_source_path=state.get("canonical_source_path", ""),
        )
        result = changes.get("_commit_result")
        if not result or not result.success:
            raise RuntimeError(
                "_commit_result missing" if result is None else result.error_message
            )
    except Exception as exc:
        return _derived_failure(
            state, f"State derivation failed: {type(exc).__name__}: {exc}"
        )
    finally:
        sqlite.close()
    marker = fs.root / "states" / f"chapter_{state['chapter_index']:04d}_derived"
    return {
        "derivation_raw_analysis": raw,
        "completion_marker_path": str(marker),
        "workflow_status": "CURRENT_STATE_DERIVED",
    }


def _route_after_derivation(state: ChapterWorkflowState) -> str:
    return (
        "save_fact_digest"
        if state.get("workflow_status") == "CURRENT_STATE_DERIVED" else END
    )



def save_chapter_sources_v2(state: ChapterWorkflowState) -> dict[str, Any]:
    """Create provenance after commit; report failure is derived-state only."""
    if state.get("commit_success") is not True:
        return {}
    try:
        return save_chapter_sources.__wrapped__(state)
    except Exception as exc:
        return _derived_failure(
            state,
            f"chapter_sources.md failed after commit: {type(exc).__name__}: {exc}",
        )


def _route_after_chapter_sources(state: ChapterWorkflowState) -> str:
    return (
        "rag_index"
        if state.get("workflow_status") == "SOURCES_SAVED" else END
    )

def save_fact_digest(state: ChapterWorkflowState) -> dict[str, Any]:
    """Persist Markdown facts after commit; failure never revokes the chapter."""
    from src.agents.state_manager.state_manager import StateManager
    from src.storage.document_formats import FactDigest

    if state.get("commit_success") is not True:
        return {}
    fs = FileStore(state["novel_id"], get_settings().data_dir)
    sqlite = SQLiteStore(fs.root / "state.db")
    try:
        raw = state.get("derivation_raw_analysis", "")
        if not raw:
            raise ValueError("derivation_raw_analysis 为空，无法提取 Fact Digest")
        digest = StateManager(
            state["novel_id"], sqlite
        ).extract_fact_digest_from_analysis(raw, state["chapter_index"])
        generated = bool(digest.atomic_facts) or any([
            digest.confirmed_items.strip(),
            digest.confirmed_character_states.strip(),
            digest.confirmed_events.strip(),
            digest.confirmed_numbers.strip(),
            digest.explicitly_absent.strip(),
            digest.pending_suspense.strip(),
        ])
        if not generated:
            raise ValueError("Fact Digest 未生成有效事实")
        paths = sorted(
            (fs.root / "states").glob(
                f"fact_digest_ch{state['chapter_index']:04d}_*.md"),
            reverse=True,
        )
        if not paths:
            paths = [fs.save(
                "states", f"fact_digest_ch{state['chapter_index']:04d}",
                digest.to_markdown())]
        digest_path = paths[0]
        canonical_digest = FactDigest.from_markdown(digest_path.read_text(encoding="utf-8"))
        return {
            "fact_digest_generated": True,
            "fact_digest_path": str(digest_path.relative_to(fs.root)).replace("\\", "/"),
            "atomic_fact_count": len(canonical_digest.atomic_facts),
            "workflow_status": "FACT_DIGEST_SAVED",
        }
    except Exception as exc:
        return {
            "fact_digest_generated": False,
            **_derived_failure(
                state,
                f"Fact Digest failed after commit: {type(exc).__name__}: {exc}",
            ),
        }
    finally:
        sqlite.close()


def _route_after_fact_digest(state: ChapterWorkflowState) -> str:
    return (
        "update_volume_progress"
        if state.get("fact_digest_generated") is True else END
    )


def update_volume_progress(state: ChapterWorkflowState) -> dict[str, Any]:
    """E07.9 boundary: expose the hook without closing/creating any volume."""
    if state.get("commit_success") is not True:
        return _derived_failure(state, "Volume Progress requires canonical prose")
    return {
        "volume_progress_updated": False,
        "workflow_status": "VOLUME_PROGRESS_READY",
    }


def _route_after_volume_progress(state: ChapterWorkflowState) -> str:
    return (
        "save_chapter_sources"
        if state.get("workflow_status") == "VOLUME_PROGRESS_READY" else END
    )


def rag_index(state: ChapterWorkflowState) -> dict[str, Any]:
    """Index Fact Text only; derived failure remains visible and non-blocking."""
    from src.storage.atomic_fact_store import AtomicFactStore, DEFAULT_BRANCH_ID
    from src.storage.document_formats import FactDigest

    if state.get("commit_success") is not True:
        return {}
    try:
        fs = FileStore(state["novel_id"], get_settings().data_dir)
        digest_rel = state.get("fact_digest_path", "")
        digest_path = fs.root / digest_rel
        digest = FactDigest.from_markdown(digest_path.read_text(encoding="utf-8"))
        count = AtomicFactStore(
            get_settings().data_dir / "chroma_db"
        ).index_facts(
            novel_id=state["novel_id"],
            branch_id=DEFAULT_BRANCH_ID,
            chapter_index=state["chapter_index"],
            facts=digest.atomic_facts,
            source_path=state.get("canonical_source_path", ""),
            digest_path=digest_rel,
        )
        if count <= 0:
            raise ValueError("Atomic Fact list is empty")
        return {
            "rag_facts": count,
            "rag_chunks": 0,
            "workflow_status": "DERIVED_READY",
        }
    except Exception as exc:
        return {
            "rag_facts": 0,
            "rag_chunks": 0,
            **_derived_failure(
                state,
                f"Atomic Fact RAG failed after commit: {type(exc).__name__}: {exc}",
            ),
        }

def build_chapter_workflow(checkpointer: Any = None) -> Any:
    """Build the stable E07.6 chapter creation backbone."""
    graph = StateGraph(ChapterWorkflowState)
    for name, node in (
        ("preflight", preflight),
        ("load_current_state", load_current_state),
        ("load_chapter_intent", load_chapter_intent),
        ("plan_chapter", plan_chapter),
        ("review_plan", review_plan),
        ("parse_plan_decision", parse_plan_decision),
        ("await_human_plan", await_human_plan),
        ("save_chapter_sources", save_chapter_sources_v2),
        ("write_draft", write_draft),
        ("style_edit", style_edit),
        ("agent_edit_chapter", agent_edit_chapter),
        ("save_styled", save_styled),
        ("review_chapter", review_chapter),
        ("parse_chapter_decision", parse_chapter_decision),
        ("await_human_chapter", await_human_chapter),
        ("commit_canonical_prose", commit_canonical_prose),
        ("derive_chapter", derive_chapter),
        ("save_fact_digest", save_fact_digest),
        ("update_volume_progress", update_volume_progress),
        ("rag_index", rag_index),
    ):
        graph.add_node(name, node)

    graph.add_edge(START, "preflight")
    for node, target in (
        ("preflight", "load_current_state"),
        ("load_current_state", "load_chapter_intent"),
        ("load_chapter_intent", "plan_chapter"),
        ("plan_chapter", "review_plan"),
        ("review_plan", "parse_plan_decision"),
        ("write_draft", "style_edit"),
        ("style_edit", "save_styled"),
        ("agent_edit_chapter", "save_styled"),
        ("save_styled", "review_chapter"),
        ("review_chapter", "parse_chapter_decision"),
    ):
        graph.add_conditional_edges(
            node,
            lambda state, next_node=target: _route_after_node(state, next_node),
            {target: target, END: END},
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
            "agent_edit_chapter": "agent_edit_chapter",
            "save_styled": "save_styled",
            "write_draft": "write_draft",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "commit_canonical_prose", _route_after_commit,
        {"derive_chapter": "derive_chapter", END: END},
    )
    graph.add_conditional_edges(
        "derive_chapter", _route_after_derivation,
        {"save_fact_digest": "save_fact_digest", END: END},
    )
    graph.add_conditional_edges(
        "save_fact_digest", _route_after_fact_digest,
        {"update_volume_progress": "update_volume_progress", END: END},
    )
    graph.add_conditional_edges(
        "update_volume_progress", _route_after_volume_progress,
        {"save_chapter_sources": "save_chapter_sources", END: END},
    )
    graph.add_conditional_edges(
        "save_chapter_sources", _route_after_chapter_sources,
        {"rag_index": "rag_index", END: END},
    )
    graph.add_edge("rag_index", END)
    return graph.compile(checkpointer=checkpointer)
