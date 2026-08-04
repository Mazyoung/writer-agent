"""Chapter Workflow conditional routing state machine.

Each Node is an adapter that calls an existing Agent/Service.
Conditional edges stop failed and non-PASS paths before downstream work.
The production runner supplies the E07.4 checkpointer; interrupt and revision
loops remain future E07.5/E07.6 work.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TypedDict, Any

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.storage.sqlite_store import SQLiteStore


# ── State ──────────────────────────────────────────────────

class ChapterWorkflowState(TypedDict, total=False):
    """State carried across one chapter workflow execution.

    E07.3: Data flow plus explicit decision and terminal status fields.
    Only contains data that must flow between Nodes.
    """

    # ── Execution identity ──
    novel_id: str
    branch_id: str
    chapter_index: int

    # ── Plan inputs ──
    chapter_outline: str
    extra_instructions: str

    # ── Flow data (text passed between nodes) ──
    chapter_plan_text: str       # plan_chapter → write_draft
    draft_text: str               # write_draft → style_edit
    styled_text: str              # style_edit → save_styled → review_chapter
    raw_analysis: str             # review_chapter → parse_decision → commit_state

    # ── Decision routing ──
    verdict: str                  # parse_decision → conditional edge
    review_reasons: list[str]
    t1_issues: list[str]
    planning_level: str

    # ── Human review ──
    human_decision: str
    human_feedback: str

    # ── Commit guard ──
    commit_success: bool          # commit_state → mark_completed → fact_digest → rag_index
    commit_error: str
    completion_marker_path: str

    # ── Results / diagnostics ──
    retrieval_success: bool
    retrieval_result_count: int
    retrieval_trace_path: str
    warnings: list[str]
    fact_digest_generated: bool
    rag_chunks: int

    # ── Workflow status ──
    workflow_status: str          # "running" | "completed" | "error" | "stopped_non_pass"
    error: str | None


# ═══════════════════════════════════════════════════════════
# E07.2 Adapter Nodes
# ═══════════════════════════════════════════════════════════


def _error_result(message: str) -> dict[str, Any]:
    """Return the shared fail-closed runtime error state."""
    return {
        "workflow_status": "error",
        "error": message,
    }


def _guard_node(
    node: Callable[[ChapterWorkflowState], dict[str, Any]],
) -> Callable[[ChapterWorkflowState], dict[str, Any]]:
    """Normalize a node's own exceptions into workflow error state."""
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


@_guard_node
def preflight(state: ChapterWorkflowState) -> dict[str, Any]:
    """Validate mechanical generation prerequisites before side effects."""
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
    completion_marker = (
        fs.root / "states" / f"chapter_{chapter_index:04d}_completed"
    )
    if completion_marker.exists():
        return _error_result(
            f"ERROR_ALREADY_EXISTS: 第{chapter_index}章已完成，普通 Generate 禁止覆盖"
        )

    return {"workflow_status": "PREFLIGHT_OK"}


def _route_after_node(
    state: ChapterWorkflowState,
    success_target: str,
) -> str:
    return END if state.get("workflow_status") == "error" else success_target


def _route_after_decision(state: ChapterWorkflowState) -> str:
    if state.get("workflow_status") == "error":
        return END
    verdict = state.get("verdict", "UNKNOWN")
    if verdict == "PASS":
        return "commit_state"
    if verdict in ("NEEDS_REVISION", "HALT"):
        return "await_human_review"
    return END


def _route_after_commit(state: ChapterWorkflowState) -> str:
    if (state.get("workflow_status") == "error"
            or state.get("commit_success") is not True):
        return END
    return "save_fact_digest"


def _route_after_fact_digest(state: ChapterWorkflowState) -> str:
    return END if state.get("workflow_status") == "error" else "rag_index"


# ── Node: plan_chapter ────────────────────────────────────

@_guard_node
def plan_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """Plan chapter with RAG retrieval and ChapterPlanner.

    Side effects: 1 LLM call, 1 chapter plan .md, 1 retrieval trace JSON.
    """
    from src.agents.author.chapter_planner import ChapterPlanner
    from src.workflows.retrieval_service import ChapterRetrievalService

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    chapter_outline = state.get("chapter_outline", "")
    extra_instructions = state.get("extra_instructions", "")

    fs = FileStore(novel_id, get_settings().data_dir)
    retrieval = ChapterRetrievalService(novel_id).retrieve(
        chapter_index, chapter_outline, extra_instructions)

    # ChapterPlanner (1 LLM call, saves canonical chapter_plan .md)
    planner = ChapterPlanner(novel_id)
    plan = planner.plan_chapter(
        chapter_index, chapter_outline, extra_instructions,
        rag_evidence=retrieval.evidence)

    # Load saved plan text for downstream nodes
    plan_text = fs.load_canonical("outlines", f"chapter_plan_ch{chapter_index:04d}") or ""

    print(f"  [plan_chapter] {len(plan.scenes)} scenes planned")
    return {
        "chapter_plan_text": plan_text,
        "retrieval_success": retrieval.trace.success,
        "retrieval_result_count": len(retrieval.trace.results),
        "retrieval_trace_path": retrieval.trace_path,
        "warnings": retrieval.warnings,
        "workflow_status": "PLANNED",
    }


# ── Node: write_draft ─────────────────────────────────────

@_guard_node
def write_draft(state: ChapterWorkflowState) -> dict[str, Any]:
    """Write a draft with DeepSeekWriter.

    Side effects: 1 LLM call, 1 draft .md.
    """
    from src.agents.author.deepseek_writer import DeepSeekWriter
    from src.storage.document_formats import ChapterPlan

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    chapter_plan_text = state.get("chapter_plan_text", "")

    if not chapter_plan_text:
        return {
            "workflow_status": "error",
            "error": f"第{chapter_index}章规划不存在，请先运行 plan_chapter",
        }

    plan = ChapterPlan.from_markdown(chapter_plan_text)

    fs = FileStore(novel_id, get_settings().data_dir)
    world_setting = fs.load_canonical("settings", "world_setting") or ""
    prev_end = _load_prev_chapter_end(fs, chapter_index)

    writer = DeepSeekWriter(novel_id)
    print(f"  [write_draft] DeepSeekWriter ({len(plan.scenes)} scenes)...")
    draft = writer.write_chapter(plan, world_setting, prev_end)

    return {
        "draft_text": draft,
        "workflow_status": "DRAFTED",
    }


def _load_prev_chapter_end(fs: FileStore, chapter_index: int) -> str:
    if chapter_index <= 1:
        return ""
    prev = fs.load_latest("chapters", f"chapter_{chapter_index - 1:04d}_styled")
    if not prev:
        prev = fs.load_latest("chapters", f"chapter_{chapter_index - 1:04d}")
    if prev:
        return prev[-500:] if len(prev) > 500 else prev
    return ""


# ── Node: style_edit ──────────────────────────────────────

@_guard_node
def style_edit(state: ChapterWorkflowState) -> dict[str, Any]:
    """Style-edit the draft with ClaudeStylist.

    Side effects: 1 LLM call. No file write (returns str only).
    """
    from src.agents.author.claude_stylist import ClaudeStylist
    from src.storage.document_formats import ChapterPlan

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    draft_text = state.get("draft_text", "")
    chapter_plan_text = state.get("chapter_plan_text", "")

    if not draft_text:
        return {
            "workflow_status": "error",
            "error": "draft_text 为空，无法执行 style_edit",
        }

    plan = ChapterPlan.from_markdown(chapter_plan_text) if chapter_plan_text else None
    emotion = plan.context.emotion_palette if plan else ""
    scene_plan_text = chapter_plan_text[:3000] if chapter_plan_text else ""

    stylist = ClaudeStylist(novel_id)
    print(f"  [style_edit] ClaudeStylist...")
    styled = stylist.edit_chapter(
        draft_text, chapter_index,
        emotion_palette=emotion,
        scene_plan_text=scene_plan_text)

    return {
        "styled_text": styled,
        "workflow_status": "STYLED",
    }


# ── Node: save_styled ─────────────────────────────────────

@_guard_node
def save_styled(state: ChapterWorkflowState) -> dict[str, Any]:
    """Save the styled chapter and run StyleChecker.

    Side effects: 1 styled .md write. 0 LLM calls.
    """
    from src.agents.author.style_checker import StyleChecker

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    styled_text = state.get("styled_text", "")

    if not styled_text:
        return {
            "workflow_status": "error",
            "error": "styled_text 为空，无法保存",
        }

    fs = FileStore(novel_id, get_settings().data_dir)
    fs.save("chapters", f"chapter_{chapter_index:04d}_styled", styled_text)

    report = StyleChecker(styled_text).check_all(file_path=f"第{chapter_index}章")
    print(report.summary())
    if report.errors > 0:
        print(f"\n  [!] {report.errors} 个错误 + {report.warnings} 个警告，请人工复核。")

    return {
        "workflow_status": "STYLED_SAVED",
    }


# ── Node: review_chapter ──────────────────────────────────

@_guard_node
def review_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """Review the styled chapter with StateManager.

    Requires styled chapter — no fallback to draft.
    Side effects: 1 LLM call, 1 review analysis .md.
    """
    from src.agents.state_manager.state_manager import StateManager

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    styled_text = state.get("styled_text", "")

    # The review may only consume the styled text produced by this invocation.
    if not styled_text:
        return _error_result(
            f"第{chapter_index}章本次运行未产生 styled_text，禁止使用历史 styled 文件"
        )

    fs = FileStore(novel_id, get_settings().data_dir)
    settings = get_settings()
    sqlite = SQLiteStore(settings.data_dir / "novels" / novel_id / "state.db")

    # Load the canonical planning and story context required by review.
    plan_text = fs.load_canonical("outlines", f"chapter_plan_ch{chapter_index:04d}") or ""
    world_setting = fs.load_canonical("settings", "world_setting") or ""
    book_plan = fs.load_tracking_doc("book_plan") or ""
    volume_plan = fs.load_tracking_doc("volume_plan") or ""
    rels = fs.load_tracking_doc("character_relationships") or ""
    items = fs.load_tracking_doc("items_equipment") or ""
    cult = fs.load_tracking_doc("cultivation_system") or ""
    char_states = fs.load_tracking_doc("character_states") or ""

    sm = StateManager(novel_id, sqlite)
    print(f"  [review_chapter] StateManager...")
    analysis = sm.review_chapter(
        styled_text, chapter_index, plan_text, rels, items, cult,
        world_setting=world_setting,
        current_character_states=char_states,
        book_plan_text=book_plan,
        volume_plan_text=volume_plan)

    return {
        "raw_analysis": analysis["raw_analysis"],
        "workflow_status": "REVIEWED",
    }


# ── Node: parse_decision ──────────────────────────────────

@_guard_node
def parse_decision(state: ChapterWorkflowState) -> dict[str, Any]:
    """Parse ReviewDecision from raw_analysis (deterministic, 0 LLM).

    Adapter for StateManager.parse_review_decision().
    Fail-closed: parse failure → UNKNOWN.
    """
    from src.agents.state_manager.state_manager import StateManager

    novel_id = state["novel_id"]
    raw_analysis = state.get("raw_analysis", "")

    if not raw_analysis:
        return {
            "verdict": "UNKNOWN",
            "review_reasons": ["raw_analysis 为空"],
            "t1_issues": [],
            "planning_level": "L1",
            "workflow_status": "error",
            "error": "raw_analysis 为空，无法解析审阅决策",
        }

    settings = get_settings()
    sqlite = SQLiteStore(settings.data_dir / "novels" / novel_id / "state.db")
    sm = StateManager(novel_id, sqlite)
    decision = sm.parse_review_decision(raw_analysis)
    if decision.verdict == "UNKNOWN":
        return {
            "verdict": "UNKNOWN",
            "review_reasons": decision.reasons,
            "t1_issues": decision.t1_issues,
            "planning_level": decision.planning_level,
            "workflow_status": "error",
            "error": "Review verdict UNKNOWN; commit blocked fail-closed",
        }

    print(f"  [parse_decision] 审阅决策: {decision.verdict}"
          + (f" — {'; '.join(decision.reasons[:3])}" if decision.reasons else ""))

    return {
        "verdict": decision.verdict,
        "review_reasons": decision.reasons,
        "t1_issues": decision.t1_issues,
        "planning_level": decision.planning_level,
        "workflow_status": f"DECISION_{decision.verdict}",
    }


# ── Node: await_human_review ─────────────────────────────

def await_human_review(state: ChapterWorkflowState) -> dict[str, Any]:
    """Pause NEEDS_REVISION/HALT until a human acknowledges the decision.

    The interrupt is deliberately the first operation: LangGraph restarts this
    node from the beginning on resume, so no side effect may precede it.
    E07.5 records feedback and terminates; it never promotes non-PASS to commit.
    """
    resume_value = interrupt({
        "type": "chapter_review",
        "novel_id": state.get("novel_id", ""),
        "chapter_index": state.get("chapter_index", 0),
        "verdict": state.get("verdict", "UNKNOWN"),
        "planning_level": state.get("planning_level", "L1"),
        "reasons": state.get("review_reasons", []),
        "t1_issues": state.get("t1_issues", []),
        "allowed_actions": ["acknowledge", "stop"],
    })

    if not isinstance(resume_value, dict):
        return _error_result("Human resume value must be a decision object")

    action = str(resume_value.get("action", "")).strip().lower()
    if action not in ("acknowledge", "stop"):
        return _error_result(
            "Unsupported human action; E07.5 accepts only 'acknowledge' or 'stop'"
        )

    feedback = str(resume_value.get("feedback", "")).strip()
    verdict = state.get("verdict", "UNKNOWN")
    detail = f"Review verdict: {verdict}; human action: {action}"
    if feedback:
        detail += f" — {feedback}"

    return {
        "human_decision": action,
        "human_feedback": feedback,
        "commit_success": False,
        "commit_error": detail,
        "workflow_status": "STOPPED_NON_PASS",
    }


# ── Node: commit_state ────────────────────────────────────

@_guard_node
def commit_state(state: ChapterWorkflowState) -> dict[str, Any]:
    """Commit canonical state: StateManager.update_tracking_docs().

    Reuses existing ALL-OLD / ALL-NEW atomic commit.
    Does NOT rewrite _parse_state_deltas() or _commit_all_tracking_docs().

    Defense-in-depth: only PASS may commit, and failures return an error state.
    """
    from src.agents.state_manager.state_manager import StateManager

    # Defense-in-depth — conditional routing should only send PASS here.
    verdict = state.get("verdict", "")
    if verdict != "PASS":
        print(f"  [commit_state] SKIPPED — verdict is '{verdict}', not PASS")
        return {
            "commit_success": False,
            "commit_error": f"verdict is '{verdict}', not PASS — commit blocked",
            "workflow_status": "error",
            "error": f"Commit blocked: verdict '{verdict}' != PASS",
        }

    # Guard 3: raw_analysis must exist
    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    raw_analysis = state.get("raw_analysis", "")
    styled_text = state.get("styled_text", "")

    if not raw_analysis:
        return {
            "commit_success": False,
            "commit_error": "raw_analysis 为空，无法提交 canonical state",
            "workflow_status": "error",
            "error": "raw_analysis 为空，无法提交 canonical state",
        }

    # Commit may only consume the styled text produced by this invocation.
    if not styled_text:
        return {
            **_error_result(
                "styled_text 为空，禁止使用历史 styled 文件提交 canonical state"
            ),
            "commit_error": "styled_text 为空，无法提交 canonical state",
        }

    settings = get_settings()
    sqlite = SQLiteStore(settings.data_dir / "novels" / novel_id / "state.db")
    sm = StateManager(novel_id, sqlite)

    print(f"  [commit_state] StateManager.update_tracking_docs()...")
    changes = sm.update_tracking_docs(chapter_index, styled_text, raw_analysis)

    commit_result = changes.get("_commit_result")
    if not commit_result or not commit_result.success:
        error_msg = (
            "_commit_result missing from changes dict"
            if commit_result is None
            else commit_result.error_message
        )
        print(f"  [commit_state] FAILED: {error_msg}")
        return {
            "commit_success": False,
            "commit_error": error_msg,
            "workflow_status": "error",
            "error": f"Canonical state commit failed: {error_msg}",
        }

    marker = FileStore(novel_id, settings.data_dir).root / "states" / (
        f"chapter_{chapter_index:04d}_completed"
    )
    if not marker.exists():
        return {
            "commit_success": False,
            "commit_error": "completion marker missing after canonical transaction",
            "workflow_status": "error",
            "error": "Canonical transaction did not produce completion marker",
        }

    print(f"  [commit_state] 提交成功: {commit_result.changed_files}")
    return {
        "commit_success": True,
        "completion_marker_path": str(marker),
        "workflow_status": "COMMITTED",
    }


# ── Node: save_fact_digest ────────────────────────────────

@_guard_node
def save_fact_digest(state: ChapterWorkflowState) -> dict[str, Any]:
    """Save Fact Digest: StateManager.extract_fact_digest_from_analysis().

    Deterministic extraction from raw_analysis (0 LLM).
    Guard: skipped if commit_success is not True.
    """
    from src.agents.state_manager.state_manager import StateManager

    # Guard: only run after successful commit
    if state.get("workflow_status") == "STOPPED_NON_PASS":
        print(f"  [save_fact_digest] SKIPPED — workflow stopped (non-PASS verdict)")
        return {}
    if state.get("commit_success") is not True:
        print(f"  [save_fact_digest] SKIPPED — commit was not successful")
        return {}

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    raw_analysis = state.get("raw_analysis", "")

    if not raw_analysis:
        return {
            "fact_digest_generated": False,
            "error": "raw_analysis 为空，无法提取 Fact Digest",
        }

    settings = get_settings()
    sqlite = SQLiteStore(settings.data_dir / "novels" / novel_id / "state.db")
    sm = StateManager(novel_id, sqlite)

    print(f"  [save_fact_digest] extract_fact_digest_from_analysis()...")
    digest = sm.extract_fact_digest_from_analysis(raw_analysis, chapter_index)
    generated = any([
        digest.confirmed_items.strip(),
        digest.confirmed_character_states.strip(),
        digest.confirmed_events.strip(),
        digest.confirmed_numbers.strip(),
        digest.explicitly_absent.strip(),
        digest.pending_suspense.strip(),
    ])

    return {
        "fact_digest_generated": generated,
        "workflow_status": (
            "FACT_DIGEST_SAVED" if generated else "FACT_DIGEST_MISSING"
        ),
    }


# ── Node: rag_index ───────────────────────────────────────

@_guard_node
def rag_index(state: ChapterWorkflowState) -> dict[str, Any]:
    """Index chapter to RAG: ChromaStore.index_chapter().

    Derived state — failure does NOT rollback canonical state.
    Guard: skipped if commit_success is not True.
    """
    from src.storage.chroma_store import ChromaStore, DEFAULT_BRANCH_ID

    # Guard: only run after successful commit
    if state.get("workflow_status") == "STOPPED_NON_PASS":
        print(f"  [rag_index] SKIPPED — workflow stopped (non-PASS verdict)")
        return {}
    if state.get("commit_success") is not True:
        print(f"  [rag_index] SKIPPED — commit was not successful")
        return {}

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    chapter_text = state.get("styled_text", "")
    settings = get_settings()

    if not chapter_text:
        warning = (
            f"第{chapter_index}章本次运行缺少 styled_text，跳过 RAG 索引"
        )
        print(f"  [RAG WARNING] {warning}")
        return {
            "rag_chunks": 0,
            "warnings": [*state.get("warnings", []), warning],
            "workflow_status": "completed",
        }

    source_path = f"chapters/chapter_{chapter_index:04d}_styled"
    chroma = ChromaStore(settings.data_dir / "chroma_db")
    branch_id = DEFAULT_BRANCH_ID  # E07.2: always main branch (no branch semantics)

    try:
        count = chroma.index_chapter(
            novel_id=novel_id, branch_id=branch_id,
            chapter_index=chapter_index, content=chapter_text,
            source_path=source_path,
            chunk_size=settings.rag_chunk_size,
            chunk_overlap=settings.rag_chunk_overlap)
        print(f"  [rag_index] 第{chapter_index}章已索引: {count} chunks")
        return {
            "rag_chunks": count,
            "workflow_status": "completed",
        }
    except Exception as e:
        # RAG failure MUST NOT rollback canonical state
        print(f"  [RAG WARNING] 第{chapter_index}章索引失败（章节状态不受影响）: {e}")
        return {
            "rag_chunks": 0,
            "workflow_status": "completed",  # canonical state is committed — workflow complete
            "error": f"RAG index failed (non-blocking): {type(e).__name__}: {e}",
        }


# ═══════════════════════════════════════════════════════════
# Builder
# ═══════════════════════════════════════════════════════════

def build_chapter_workflow(checkpointer: Any = None) -> Any:
    """Build and compile the workflow with an optional checkpointer.

    The graph owns orchestration state only. Canonical story state remains
    managed by the existing StateManager transaction.
    """
    graph = StateGraph(ChapterWorkflowState)

    graph.add_node("preflight", preflight)
    graph.add_node("plan_chapter", plan_chapter)
    graph.add_node("write_draft", write_draft)
    graph.add_node("style_edit", style_edit)
    graph.add_node("save_styled", save_styled)
    graph.add_node("review_chapter", review_chapter)
    graph.add_node("parse_decision", parse_decision)
    graph.add_node("await_human_review", await_human_review)
    graph.add_node("commit_state", commit_state)
    graph.add_node("save_fact_digest", save_fact_digest)
    graph.add_node("rag_index", rag_index)

    graph.add_edge(START, "preflight")
    for node, target in [
        ("preflight", "plan_chapter"),
        ("plan_chapter", "write_draft"),
        ("write_draft", "style_edit"),
        ("style_edit", "save_styled"),
        ("save_styled", "review_chapter"),
        ("review_chapter", "parse_decision"),
    ]:
        graph.add_conditional_edges(
            node,
            lambda state, next_node=target: _route_after_node(
                state, next_node),
            {target: target, END: END},
        )

    graph.add_conditional_edges(
        "parse_decision",
        _route_after_decision,
        {
            "commit_state": "commit_state",
            "await_human_review": "await_human_review",
            END: END,
        },
    )
    graph.add_edge("await_human_review", END)
    graph.add_conditional_edges(
        "commit_state",
        _route_after_commit,
        {"save_fact_digest": "save_fact_digest", END: END},
    )
    graph.add_conditional_edges(
        "save_fact_digest",
        _route_after_fact_digest,
        {"rag_index": "rag_index", END: END},
    )
    graph.add_edge("rag_index", END)

    return graph.compile(checkpointer=checkpointer)
