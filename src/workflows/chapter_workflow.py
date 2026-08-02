"""E07.1 — Chapter Workflow StateGraph Skeleton.

No business side effects. No LLM calls. No file writes.
Side-by-side with existing production runtime.
"""

from __future__ import annotations

from typing import TypedDict, Any

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
    error: str | None


# ── E07.1 Node: initialize_workflow ───────────────────────

def initialize_workflow(state: ChapterWorkflowState) -> dict[str, Any]:
    """E07.1 skeleton entry point.

    Returns a partial state update — does NOT mutate the input state.
    Existing fields (novel_id, branch_id, chapter_index) are preserved
    by LangGraph's state merge.
    """
    return {
        "workflow_status": "SKELETON_READY",
        "error": None,
    }


# ── Builder ────────────────────────────────────────────────

def build_chapter_workflow() -> Any:
    """Build and compile the Chapter Workflow StateGraph skeleton.

    E07.1: Single-node skeleton. No business logic wired yet.
    No conditional routing. No checkpoint. No side effects.

    Returns:
        Compiled graph (CompiledStateGraph) ready for invoke().
    """
    graph = StateGraph(ChapterWorkflowState)

    graph.add_node("initialize_workflow", initialize_workflow)

    graph.add_edge(START, "initialize_workflow")
    graph.add_edge("initialize_workflow", END)

    return graph.compile()
