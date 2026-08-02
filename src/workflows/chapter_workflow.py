"""E07.1 — Chapter Workflow StateGraph Skeleton.

No business side effects. No LLM calls. No file writes.
Side-by-side with existing production runtime.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import StateGraph, START, END


# ── State ──────────────────────────────────────────────────

class ChapterWorkflowState(TypedDict, total=False):
    """State carried across one chapter workflow execution.

    Only contains data that must flow between Nodes.
    This is NOT an application database.
    """

    # ── Execution identity ──
    novel_id: str
    branch_id: str
    chapter_index: int

    # ── Workflow status ──
    workflow_status: str       # "running" | "completed" | "error"

    # ── Error ──
    error: str

    # ── Planning output ──
    rag_evidence: str
    chapter_plan: str          # serialised Markdown

    # ── Writing output ──
    draft_text: str
    styled_text: str

    # ── Review output ──
    raw_analysis: str
    review_decision: str       # "PASS" | "NEEDS_REVISION" | "HALT" | "UNKNOWN"
    state_commit_result: dict  # StateCommitResult as dict (success, error_message, ...)


# ── No-op Nodes (E07.1: skeleton only) ─────────────────────

def _noop_node(state: ChapterWorkflowState, *, node_name: str = "") -> ChapterWorkflowState:
    """No-op pass-through. Each node just updates workflow_status."""
    state["workflow_status"] = f"{node_name}:ok"
    return state


def _retrieve_history_node(state: ChapterWorkflowState) -> ChapterWorkflowState:
    return _noop_node(state, node_name="retrieve_history")


def _plan_chapter_node(state: ChapterWorkflowState) -> ChapterWorkflowState:
    return _noop_node(state, node_name="plan_chapter")


def _write_draft_node(state: ChapterWorkflowState) -> ChapterWorkflowState:
    return _noop_node(state, node_name="write_draft")


def _style_edit_node(state: ChapterWorkflowState) -> ChapterWorkflowState:
    return _noop_node(state, node_name="style_edit")


def _review_chapter_node(state: ChapterWorkflowState) -> ChapterWorkflowState:
    return _noop_node(state, node_name="review_chapter")


# ── Builder ────────────────────────────────────────────────

def build_chapter_workflow() -> StateGraph:
    """Build and compile the Chapter Workflow StateGraph skeleton.

    E07.1: Linear happy-path topology only.
    No conditional routing. No checkpoint. No side effects.

    Returns:
        Compiled StateGraph ready for invoke().
    """
    graph = StateGraph(ChapterWorkflowState)

    # ── Register nodes ──
    graph.add_node("retrieve_history", _retrieve_history_node)
    graph.add_node("plan_chapter", _plan_chapter_node)
    graph.add_node("write_draft", _write_draft_node)
    graph.add_node("style_edit", _style_edit_node)
    graph.add_node("review_chapter", _review_chapter_node)

    # ── Linear topology ──
    graph.add_edge(START, "retrieve_history")
    graph.add_edge("retrieve_history", "plan_chapter")
    graph.add_edge("plan_chapter", "write_draft")
    graph.add_edge("write_draft", "style_edit")
    graph.add_edge("style_edit", "review_chapter")
    graph.add_edge("review_chapter", END)

    return graph.compile()
