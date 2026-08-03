"""E07.2 — Chapter Workflow PASS Happy Path (Adapter Nodes).

Each Node is an adapter that calls an existing Agent/Service.
No conditional routing (→ E07.3). No checkpoint (→ E07.4).
Side-by-side with existing production runtime.
"""

from __future__ import annotations

from typing import TypedDict, Any

from langgraph.graph import StateGraph, START, END

from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.storage.sqlite_store import SQLiteStore


# ── State ──────────────────────────────────────────────────

class ChapterWorkflowState(TypedDict, total=False):
    """State carried across one chapter workflow execution.

    E07.2: Extended with data-flow fields for the full PASS happy path.
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

    # ── Decision routing (temporary fields — replaced by conditional edges in E07.3) ──
    verdict: str                  # parse_decision → require_pass
    review_reasons: list[str]
    t1_issues: list[str]
    planning_level: str

    # ── Commit guard ──
    commit_success: bool          # commit_state → save_fact_digest → rag_index
    commit_error: str

    # ── Results ──
    fact_digest_generated: bool
    rag_chunks: int

    # ── Workflow status ──
    workflow_status: str          # "running" | "completed" | "error" | "stopped_non_pass"
    error: str | None


# ═══════════════════════════════════════════════════════════
# E07.2 Adapter Nodes
# ═══════════════════════════════════════════════════════════

# ── Node: plan_chapter ────────────────────────────────────

def plan_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """Plan chapter: RAG retrieval + ChapterPlanner.plan_chapter().

    Adapter for Orchestrator.plan_chapter().
    Side effects: 1 LLM call, 1 chapter plan .md, 1 retrieval trace JSON.
    """
    from src.agents.author.chapter_planner import ChapterPlanner

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    chapter_outline = state.get("chapter_outline", "")
    extra_instructions = state.get("extra_instructions", "")

    fs = FileStore(novel_id, get_settings().data_dir)

    # RAG retrieval (same flow as Orchestrator._retrieve_evidence)
    rag_evidence = _run_rag_retrieval(
        fs, novel_id, chapter_index, chapter_outline, extra_instructions)

    # ChapterPlanner (1 LLM call, saves canonical chapter_plan .md)
    planner = ChapterPlanner(novel_id)
    plan = planner.plan_chapter(
        chapter_index, chapter_outline, extra_instructions,
        rag_evidence=rag_evidence)

    # Load saved plan text for downstream nodes
    plan_text = fs.load_canonical("outlines", f"chapter_plan_ch{chapter_index:04d}") or ""

    print(f"  [plan_chapter] {len(plan.scenes)} scenes planned")
    return {
        "chapter_plan_text": plan_text,
        "workflow_status": "PLANNED",
    }


def _run_rag_retrieval(
    fs: FileStore, novel_id: str, chapter_index: int,
    chapter_outline: str, extra_instructions: str,
) -> str:
    """Run RAG retrieval and return formatted evidence text.

    Graceful degradation: any error returns empty string.
    Mirrors Orchestrator._retrieve_evidence() behavior.
    """
    import json
    from datetime import datetime
    from src.storage.chroma_store import (
        ChromaStore, RetrievalTrace, DEFAULT_BRANCH_ID,
    )

    settings = get_settings()
    chroma = ChromaStore(settings.data_dir / "chroma_db")
    branch_id = DEFAULT_BRANCH_ID
    top_k = settings.rag_top_k

    # Build deterministic query (compact version of Orchestrator._build_retrieval_query)
    import re
    parts: list[str] = []
    vp_text = fs.load_tracking_doc("volume_plan") or ""
    if vp_text:
        events = re.findall(
            r'(### 事件\d+[：:].*?\n.*?对应章节\**\s*[：:]\s*第' + str(chapter_index) + r'章.*?)(?=### 事件|\Z)',
            vp_text, re.DOTALL)
        if events:
            parts.append(events[0][:1000])
    if chapter_outline:
        parts.append(chapter_outline[:500])
    if extra_instructions:
        parts.append(extra_instructions[:500])
    query = " ".join(parts) if parts else f"第{chapter_index}章 剧情"

    # Create trace
    trace = RetrievalTrace(
        chapter_index=chapter_index, branch_id=branch_id,
        query=query, top_k=top_k,
        filters={
            "novel_id": novel_id, "branch_id": branch_id,
            "chapter_index <": chapter_index, "source_type": "chapter",
        },
        timestamp=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    )

    # Search
    try:
        results = chroma.search(
            novel_id=novel_id, branch_id=branch_id,
            query=query, chapter_index=chapter_index, top_k=top_k)
    except Exception as e:
        trace.success = False
        trace.error_message = f"{type(e).__name__}: {e}"
        print(f"  [RAG WARNING] 检索失败: {e}")
        return ""

    if not results:
        return ""

    # Save trace
    try:
        traces_dir = fs.root / "tracking" / "rag_traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        trace.results = results
        (traces_dir / f"retrieval_trace_ch{trace.chapter_index:04d}_{ts}.json").write_text(
            json.dumps(trace.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        pass  # trace save failure is non-blocking

    # Format evidence
    lines = [
        f"（从 {len(results)} 个历史章节片段中检索到以下相关内容，"
        f"距离越近越相关）\n"
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            f"**[证据{i}]** 第{r.chapter_index}章 "
            f"chunk-{r.chunk_index} (distance={r.distance:.4f}):")
        lines.append(f"> {r.text[:600]}")
        lines.append("")

    print(f"  [RAG] 检索到 {len(results)} 个相关历史片段")
    return "\n".join(lines)


# ── Node: write_draft ─────────────────────────────────────

def write_draft(state: ChapterWorkflowState) -> dict[str, Any]:
    """Write draft: DeepSeekWriter.write_chapter().

    Adapter for Orchestrator.write_chapter() step 1.
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

def style_edit(state: ChapterWorkflowState) -> dict[str, Any]:
    """Style-edit draft: ClaudeStylist.edit_chapter().

    Adapter for Orchestrator.write_chapter() step 2.
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

def save_styled(state: ChapterWorkflowState) -> dict[str, Any]:
    """Save styled chapter + run StyleChecker.

    Adapter for Orchestrator._save_and_check_styled().
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

def review_chapter(state: ChapterWorkflowState) -> dict[str, Any]:
    """Review chapter: StateManager.review_chapter().

    Adapter for Orchestrator.review_chapter() Step 1.
    Requires styled chapter — no fallback to draft.
    Side effects: 1 LLM call, 1 review analysis .md.
    """
    from src.agents.state_manager.state_manager import StateManager

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    styled_text = state.get("styled_text", "")

    # Enforce: review requires styled chapter
    if not styled_text:
        fs = FileStore(novel_id, get_settings().data_dir)
        styled_text = fs.load_latest("chapters", f"chapter_{chapter_index:04d}_styled") or ""
    if not styled_text:
        return {
            "workflow_status": "error",
            "error": (
                f"第{chapter_index}章 styled 文件不存在。"
                f"Review 只接受 styled 章节（经过 ClaudeStylist 编辑）。"
            ),
        }

    fs = FileStore(novel_id, get_settings().data_dir)
    settings = get_settings()
    sqlite = SQLiteStore(settings.data_dir / "novels" / novel_id / "state.db")

    # Load context (same as Orchestrator.review_chapter)
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

    print(f"  [parse_decision] 审阅决策: {decision.verdict}"
          + (f" — {'; '.join(decision.reasons[:3])}" if decision.reasons else ""))

    return {
        "verdict": decision.verdict,
        "review_reasons": decision.reasons,
        "t1_issues": decision.t1_issues,
        "planning_level": decision.planning_level,
        "workflow_status": f"DECISION_{decision.verdict}",
    }


# ── Node: require_pass ───────────────────────────────────

def require_pass(state: ChapterWorkflowState) -> dict[str, Any]:
    """E07.2 temporary fail-closed guard (replaced by conditional edges in E07.3).

    Only PASS may proceed to commit_state.
    NEEDS_REVISION, HALT, UNKNOWN, and any other verdict → STOP.
    """
    verdict = state.get("verdict", "UNKNOWN")

    if verdict == "PASS":
        print(f"  [require_pass] PASS → 继续 commit")
        return {}  # allow downstream to proceed

    # Non-PASS: fail-closed — set guard flag for downstream nodes
    reasons = state.get("review_reasons", [])
    print(f"  [require_pass] {verdict} → 停止（禁止 commit）"
          + (f": {'; '.join(reasons[:3])}" if reasons else ""))

    return {
        "commit_success": False,
        "commit_error": f"Review verdict: {verdict}"
                        + (f" — {'; '.join(reasons[:3])}" if reasons else ""),
        "workflow_status": "STOPPED_NON_PASS",
        "error": f"Review verdict is {verdict}, not PASS. Commit blocked.",
    }


# ── Node: commit_state ────────────────────────────────────

def commit_state(state: ChapterWorkflowState) -> dict[str, Any]:
    """Commit canonical state: StateManager.update_tracking_docs().

    Reuses existing ALL-OLD / ALL-NEW atomic commit.
    Does NOT rewrite _parse_state_deltas() or _commit_all_tracking_docs().

    Guard: skipped if require_pass blocked (verdict != PASS).
    Guard: if commit fails → commit_success=False → downstream blocked.
    """
    from src.agents.state_manager.state_manager import StateManager

    # Guard: check if require_pass blocked
    if state.get("workflow_status") == "STOPPED_NON_PASS":
        print(f"  [commit_state] SKIPPED — workflow stopped (non-PASS verdict)")
        return {}

    novel_id = state["novel_id"]
    chapter_index = state["chapter_index"]
    raw_analysis = state.get("raw_analysis", "")
    styled_text = state.get("styled_text", "")

    # Load styled from FileStore if not in state
    if not styled_text:
        fs = FileStore(novel_id, get_settings().data_dir)
        styled_text = fs.load_latest("chapters", f"chapter_{chapter_index:04d}_styled") or ""
    if not styled_text:
        return {
            "commit_success": False,
            "commit_error": "styled_text 为空，无法提交 canonical state",
            "workflow_status": "error",
            "error": "styled_text 为空，无法提交 canonical state",
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

    print(f"  [commit_state] 提交成功: {commit_result.changed_files}")
    return {
        "commit_success": True,
        "workflow_status": "COMMITTED",
    }


# ── Node: save_fact_digest ────────────────────────────────

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
    sm.extract_fact_digest_from_analysis(raw_analysis, chapter_index)

    return {
        "fact_digest_generated": True,
        "workflow_status": "FACT_DIGEST_SAVED",
    }


# ── Node: rag_index ───────────────────────────────────────

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

    fs = FileStore(novel_id, get_settings().data_dir)
    settings = get_settings()

    styled_prefix = f"chapter_{chapter_index:04d}_styled"
    chapter_text = fs.load_latest("chapters", styled_prefix)
    if not chapter_text:
        print(f"  [RAG WARNING] 第{chapter_index}章 styled 文件不存在，跳过索引")
        return {"rag_chunks": 0, "workflow_status": "completed"}

    # Determine source_path
    styled_files = sorted(
        (fs.root / "chapters").glob(f"{styled_prefix}_*.md"), reverse=True)
    source_path = f"chapters/{styled_files[0].name}" if styled_files else f"chapters/{styled_prefix}"

    chroma = ChromaStore(settings.data_dir / "chroma_db")
    branch_id = state.get("branch_id", DEFAULT_BRANCH_ID)

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

def build_chapter_workflow() -> Any:
    """Build and compile the Chapter Workflow StateGraph.

    E07.2: Full PASS happy path as linear adapter-node chain.
    Topology: START → plan_chapter → write_draft → style_edit → save_styled
              → review_chapter → parse_decision → require_pass
              → commit_state → save_fact_digest → rag_index → END

    No conditional routing (→ E07.3). No checkpoint (→ E07.4).
    require_pass is a regular node with guard flags for downstream.

    Returns:
        Compiled graph (CompiledStateGraph) ready for invoke().
    """
    graph = StateGraph(ChapterWorkflowState)

    # ── Add nodes ──
    graph.add_node("plan_chapter", plan_chapter)
    graph.add_node("write_draft", write_draft)
    graph.add_node("style_edit", style_edit)
    graph.add_node("save_styled", save_styled)
    graph.add_node("review_chapter", review_chapter)
    graph.add_node("parse_decision", parse_decision)
    graph.add_node("require_pass", require_pass)
    graph.add_node("commit_state", commit_state)
    graph.add_node("save_fact_digest", save_fact_digest)
    graph.add_node("rag_index", rag_index)

    # ── Linear edges: PASS happy path ──
    graph.add_edge(START, "plan_chapter")
    graph.add_edge("plan_chapter", "write_draft")
    graph.add_edge("write_draft", "style_edit")
    graph.add_edge("style_edit", "save_styled")
    graph.add_edge("save_styled", "review_chapter")
    graph.add_edge("review_chapter", "parse_decision")
    graph.add_edge("parse_decision", "require_pass")
    graph.add_edge("require_pass", "commit_state")
    graph.add_edge("commit_state", "save_fact_digest")
    graph.add_edge("save_fact_digest", "rag_index")
    graph.add_edge("rag_index", END)

    return graph.compile()
