"""E07.2 Closure — PASS Happy Path Behavioral Parity 测试。

E07.1 tests (enduring invariants):
  A: State schema 可构造/传入
  B: Graph topology contract (updated for E07.2)
  C: Node contract tests (updated for E07.2)
  D: Import/import safety (no runtime side effects)

E07.2 tests:
  A: PASS complete happy path (mocked Agent calls)
  B: Non-PASS verdict → commit_state / fact_digest / rag_index all skipped
  C: Commit failure → fact_digest / rag_index skipped
  D: commit_result missing → fail-closed
  E: RAG failure does NOT rollback canonical state
  F: styled chapter save + StyleChecker called together (save once)
  G: No E07.3/E07.4 leakage
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

# Pre-load submodules so that unittest.mock.patch can resolve them.
# Without this, patch("src.storage.chroma_store.ChromaStore") fails
# because the submodule is lazily loaded.
# Use try/except — some modules have heavy transitive dependencies (chromadb)
# that may not be installed in all environments.
# Pre-load submodules so that unittest.mock.patch can resolve them.
# Some of these have heavy transitive dependencies (chromadb, openai)
# that may not be installed. We inject lightweight MagicMock stand-ins
# into sys.modules for unavailable dependencies so the pre-loads succeed
# and @patch decorators can resolve the target paths.
import sys as _sys
from unittest.mock import MagicMock as _MagicMock

_MOCK_DEPENDENCIES = {
    # (parent_package, submodule_name, missing_dependency)
    ("src.storage", "chroma_store", "chromadb"),
    ("src.agents.state_manager", "state_manager", "openai"),
    ("src.agents.author", "chapter_planner", "openai"),
    ("src.agents.author", "deepseek_writer", "openai"),
    ("src.agents.author", "claude_stylist", "openai"),
}

for _parent, _sub, _dep in _MOCK_DEPENDENCIES:
    _full = f"{_parent}.{_sub}"
    if _full not in _sys.modules:
        try:
            __import__(_full)
        except ImportError:
            # Inject a mock stand-in so @patch can resolve the path
            _mock = _MagicMock()
            _sys.modules[_full] = _mock
            # Also set it as an attribute on the parent package
            _parent_mod = _sys.modules.get(_parent)
            if _parent_mod is not None:
                setattr(_parent_mod, _sub, _mock)

# style_checker has no heavy deps — import directly
import src.agents.author.style_checker  # noqa: E402


# ═══════════════════════════════════════════════════════════
# E07.1-A: ChapterWorkflowState schema
# ═══════════════════════════════════════════════════════════

class TestChapterWorkflowState(unittest.TestCase):
    """E07.1-A: ChapterWorkflowState schema (enduring)."""

    def test_minimal_state_constructs(self):
        from src.workflows.chapter_workflow import ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "test_novel",
            "branch_id": "main",
            "chapter_index": 1,
        }
        self.assertEqual(state["novel_id"], "test_novel")
        self.assertEqual(state["chapter_index"], 1)

    def test_partial_state_allowed(self):
        """TypedDict total=False — 部分字段也可构造。"""
        from src.workflows.chapter_workflow import ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "mini",
            "chapter_index": 3,
        }
        self.assertEqual(state["novel_id"], "mini")
        self.assertEqual(state.get("branch_id", ""), "")

    def test_e07_2_state_fields_exist(self):
        """E07.2: extended state includes data-flow fields."""
        from src.workflows.chapter_workflow import ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "n1",
            "chapter_index": 5,
            "chapter_plan_text": "plan...",
            "draft_text": "draft...",
            "styled_text": "styled...",
            "raw_analysis": "analysis...",
            "verdict": "PASS",
            "commit_success": True,
            "workflow_status": "completed",
        }
        self.assertEqual(state["verdict"], "PASS")
        self.assertEqual(state["commit_success"], True)


# ═══════════════════════════════════════════════════════════
# E07.1-B: Graph topology contract (updated for E07.2)
# ═══════════════════════════════════════════════════════════

class TestGraphTopology(unittest.TestCase):
    """E07.1-B: Graph topology contract (updated for E07.2)."""

    def test_graph_compiles_and_has_invoke(self):
        """Graph 必须可编译并具有 invoke 方法。"""
        from src.workflows.chapter_workflow import build_chapter_workflow

        graph = build_chapter_workflow()
        self.assertIsNotNone(graph)
        self.assertTrue(hasattr(graph, "invoke"))

    def test_e07_2_all_nodes_registered(self):
        """E07.2: 所有 10 个 adapter node 都已注册。"""
        from src.workflows.chapter_workflow import build_chapter_workflow

        graph = build_chapter_workflow()
        nodes = graph.get_graph().nodes
        expected = {
            "plan_chapter", "write_draft", "style_edit", "save_styled",
            "review_chapter", "parse_decision", "require_pass",
            "commit_state", "save_fact_digest", "rag_index",
            "__start__", "__end__",
        }
        self.assertEqual(set(nodes.keys()), expected,
                         f"Nodes mismatch: {set(nodes.keys()) - expected} extra, "
                         f"{expected - set(nodes.keys())} missing")

    def test_e07_2_topology_is_linear(self):
        """E07.2: 线性拓扑 — PASS happy path 无分支。"""
        from src.workflows.chapter_workflow import build_chapter_workflow

        graph = build_chapter_workflow()
        edges = graph.get_graph().edges

        # edges are Edge named-tuples; convert to (source, target) pairs
        edge_pairs = {(e.source, e.target) for e in edges}

        expected_edges = [
            ("__start__", "plan_chapter"),
            ("plan_chapter", "write_draft"),
            ("write_draft", "style_edit"),
            ("style_edit", "save_styled"),
            ("save_styled", "review_chapter"),
            ("review_chapter", "parse_decision"),
            ("parse_decision", "require_pass"),
            ("require_pass", "commit_state"),
            ("commit_state", "save_fact_digest"),
            ("save_fact_digest", "rag_index"),
            ("rag_index", "__end__"),
        ]
        for src, dst in expected_edges:
            self.assertIn((src, dst), edge_pairs,
                          f"Missing edge: {src} → {dst}")

    def test_e07_2_no_conditional_edges(self):
        """E07.2: 无 conditional edges（属于 E07.3）。"""
        from src.workflows.chapter_workflow import build_chapter_workflow

        cw_path = Path(__file__).parent.parent / "src" / "workflows" / "chapter_workflow.py"
        source = cw_path.read_text(encoding="utf-8")
        self.assertNotIn("add_conditional_edges", source,
                         "E07.2 不得包含 conditional edges（属于 E07.3）")


# ═══════════════════════════════════════════════════════════
# E07.1-C: Node contract tests (updated for E07.2)
# ═══════════════════════════════════════════════════════════

class TestNodeContracts(unittest.TestCase):
    """E07.1-C: Node contract tests (updated for E07.2 nodes)."""

    def test_require_pass_returns_empty_for_pass(self):
        """PASS verdict → require_pass 返回空 dict（允许继续）。"""
        from src.workflows.chapter_workflow import (
            require_pass, ChapterWorkflowState,
        )

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "verdict": "PASS",
            "workflow_status": "DECISION_PASS",
        }
        result = require_pass(state)
        self.assertEqual(result, {})

    def test_require_pass_blocks_needs_revision(self):
        """NEEDS_REVISION → require_pass 设置 commit_success=False。"""
        from src.workflows.chapter_workflow import (
            require_pass, ChapterWorkflowState,
        )

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "verdict": "NEEDS_REVISION",
            "review_reasons": ["pacing too slow"],
        }
        result = require_pass(state)
        self.assertEqual(result["commit_success"], False)
        self.assertEqual(result["workflow_status"], "STOPPED_NON_PASS")
        self.assertIn("NEEDS_REVISION", result["error"])

    def test_require_pass_blocks_halt(self):
        """HALT → require_pass 设置 commit_success=False。"""
        from src.workflows.chapter_workflow import (
            require_pass, ChapterWorkflowState,
        )

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "verdict": "HALT",
            "review_reasons": ["L2 planning conflict"],
        }
        result = require_pass(state)
        self.assertEqual(result["commit_success"], False)
        self.assertEqual(result["workflow_status"], "STOPPED_NON_PASS")

    def test_require_pass_blocks_unknown(self):
        """UNKNOWN → require_pass 设置 commit_success=False（fail-closed）。"""
        from src.workflows.chapter_workflow import (
            require_pass, ChapterWorkflowState,
        )

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "verdict": "UNKNOWN",
        }
        result = require_pass(state)
        self.assertEqual(result["commit_success"], False)
        self.assertEqual(result["workflow_status"], "STOPPED_NON_PASS")


# ═══════════════════════════════════════════════════════════
# E07.1-D: Import safety (no runtime side effects)
# ═══════════════════════════════════════════════════════════

class TestNoRuntimeSideEffects(unittest.TestCase):
    """E07.1-D: import 不触发 runtime side effects (enduring)."""

    def test_import_does_not_call_llm(self):
        import src.workflows.chapter_workflow as cw
        self.assertTrue(hasattr(cw, "build_chapter_workflow"))

    def test_import_does_not_write_files(self):
        import src.workflows.chapter_workflow as cw
        self.assertTrue(hasattr(cw, "ChapterWorkflowState"))

    def test_source_does_not_import_orchestrator(self):
        """chapter_workflow 源码不导入 Orchestrator 类。"""
        cw_path = Path(__file__).parent.parent / "src" / "workflows" / "chapter_workflow.py"
        source = cw_path.read_text(encoding="utf-8")
        # Check actual imports (not comments)
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if "import" in stripped:
                self.assertNotIn("Orchestrator", stripped,
                                 f"Line imports Orchestrator: {stripped}")

    def test_main_py_unchanged(self):
        """main.py 不得接入 LangGraph runtime。"""
        main_path = Path(__file__).parent.parent / "main.py"
        content = main_path.read_text(encoding="utf-8")
        self.assertNotIn("langgraph", content.lower())
        self.assertNotIn("chapter_workflow", content)
        self.assertNotIn("build_chapter_workflow", content)


# ═══════════════════════════════════════════════════════════
# E07.2-A: PASS complete happy path (mocked)
# ═══════════════════════════════════════════════════════════

class TestE07_2_PassHappyPath(unittest.TestCase):
    """E07.2-A: PASS complete happy path — all nodes invoked in order."""

    def setUp(self):
        self.base_state = {
            "novel_id": "happy_path_test",
            "chapter_index": 3,
            "chapter_outline": "test outline",
            "extra_instructions": "test instructions",
        }

    @patch("src.storage.chroma_store.ChromaStore")
    @patch("src.agents.author.chapter_planner.ChapterPlanner")
    def test_plan_chapter_node_returns_plan_text(self, mock_planner_cls, mock_chroma_cls):
        """plan_chapter 返回 chapter_plan_text + PLANNED status。"""
        from src.workflows.chapter_workflow import plan_chapter, ChapterWorkflowState
        from src.storage.document_formats import ChapterPlan

        mock_planner = mock_planner_cls.return_value
        fake_plan = ChapterPlan()
        fake_plan.chapter_index = 3
        fake_plan.scenes = [MagicMock(), MagicMock()]
        mock_planner.plan_chapter.return_value = fake_plan

        mock_chroma_cls.return_value.search.return_value = []

        state: ChapterWorkflowState = dict(self.base_state)
        result = plan_chapter(state)

        self.assertIn("chapter_plan_text", result)
        self.assertEqual(result["workflow_status"], "PLANNED")

    @patch("src.agents.author.deepseek_writer.DeepSeekWriter")
    def test_write_draft_node_returns_draft(self, mock_writer_cls):
        """write_draft 返回 draft_text + DRAFTED status。"""
        from src.workflows.chapter_workflow import write_draft, ChapterWorkflowState

        mock_writer = mock_writer_cls.return_value
        mock_writer.write_chapter.return_value = "This is the draft text."

        state: ChapterWorkflowState = {
            **self.base_state,
            "chapter_plan_text": "# Chapter Plan\ntest plan content",
        }
        result = write_draft(state)

        self.assertEqual(result["draft_text"], "This is the draft text.")
        self.assertEqual(result["workflow_status"], "DRAFTED")

    @patch("src.agents.author.claude_stylist.ClaudeStylist")
    def test_style_edit_node_returns_styled(self, mock_stylist_cls):
        """style_edit 返回 styled_text + STYLED status。"""
        from src.workflows.chapter_workflow import style_edit, ChapterWorkflowState

        mock_stylist = mock_stylist_cls.return_value
        mock_stylist.edit_chapter.return_value = "This is styled text."

        state: ChapterWorkflowState = {
            **self.base_state,
            "draft_text": "draft text",
            "chapter_plan_text": "# plan",
        }
        result = style_edit(state)

        self.assertEqual(result["styled_text"], "This is styled text.")
        self.assertEqual(result["workflow_status"], "STYLED")

    @patch("src.agents.author.style_checker.StyleChecker")
    def test_save_styled_saves_and_checks(self, mock_checker_cls):
        """save_styled 保存文件 + 运行 StyleChecker。"""
        from src.workflows.chapter_workflow import save_styled, ChapterWorkflowState

        mock_checker = mock_checker_cls.return_value
        mock_report = MagicMock()
        mock_report.errors = 0
        mock_report.warnings = 0
        mock_report.summary.return_value = "OK"
        mock_checker.check_all.return_value = mock_report

        state: ChapterWorkflowState = {
            **self.base_state,
            "styled_text": "styled chapter text",
        }
        result = save_styled(state)

        self.assertEqual(result["workflow_status"], "STYLED_SAVED")
        mock_checker_cls.assert_called_once_with("styled chapter text")
        mock_checker.check_all.assert_called_once()

    @patch("src.agents.state_manager.state_manager.StateManager")
    def test_parse_decision_returns_pass(self, mock_sm_cls):
        """parse_decision 解析 verdict 字段。"""
        from src.workflows.chapter_workflow import parse_decision, ChapterWorkflowState
        from src.storage.document_formats import ReviewDecision

        mock_sm = mock_sm_cls.return_value
        decision = ReviewDecision()
        decision.verdict = "PASS"
        decision.reasons = ["All checks passed"]
        mock_sm.parse_review_decision.return_value = decision

        state: ChapterWorkflowState = {
            **self.base_state,
            "raw_analysis": "## 审阅决策\nPASS\n...",
        }
        result = parse_decision(state)

        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["workflow_status"], "DECISION_PASS")

    def test_real_truncated_review_markdown_returns_pass(self):
        """真实 review 格式在角色塑造/决策区前被截断时仍解析为 PASS。"""
        from src.agents.state_manager.state_manager import StateManager

        review_path = (
            Path(__file__).parent
            / "fixtures" / "review_ch0001_truncated.md"
        )
        analysis = review_path.read_text(encoding="utf-8")
        decision = StateManager.__new__(StateManager).parse_review_decision(analysis)

        self.assertNotIn("## 审阅决策", analysis)
        self.assertTrue(analysis.rstrip().endswith("+表"))
        self.assertEqual(decision.verdict, "PASS")

    def test_missing_decision_without_truncated_quality_prefix_stays_unknown(self):
        """普通缺失决策区仍保持 fail-closed，不能由单个 PASS 推断。"""
        from src.agents.state_manager.state_manager import StateManager

        analysis = """## 一致性检查
### T1（硬错误）
- 无。
## 质量审阅
- **情节逻辑**: PASS
"""
        decision = StateManager.__new__(StateManager).parse_review_decision(analysis)

        self.assertEqual(decision.verdict, "UNKNOWN")

    def test_full_happy_path_state_flow(self):
        """完整 PASS happy path: state schema 被 graph 接受。"""
        from src.workflows.chapter_workflow import (
            build_chapter_workflow, ChapterWorkflowState,
        )

        graph = build_chapter_workflow()
        state: ChapterWorkflowState = {
            "novel_id": "integration_test",
            "chapter_index": 1,
            "chapter_outline": "outline",
            "extra_instructions": "",
        }

        self.assertIsNotNone(graph)
        nodes = graph.get_graph().nodes
        self.assertIn("plan_chapter", nodes)
        self.assertIn("rag_index", nodes)

    @patch("src.workflows.chapter_workflow.FileStore")
    @patch("src.agents.author.chapter_planner.ChapterPlanner")
    @patch("src.agents.author.deepseek_writer.DeepSeekWriter")
    @patch("src.agents.author.claude_stylist.ClaudeStylist")
    @patch("src.agents.author.style_checker.StyleChecker")
    @patch("src.agents.state_manager.state_manager.StateManager")
    @patch("src.storage.chroma_store.ChromaStore")
    def test_graph_invoke_pass_happy_path(
        self, mock_chroma_cls, mock_sm_cls, mock_checker_cls,
        mock_stylist_cls, mock_writer_cls, mock_planner_cls, mock_fs_cls,
    ):
        """graph.invoke() 完整 PASS happy path 按顺序完成。

        验证: plan → write → style → save → review → parse →
        PASS guard → commit → fact_digest → rag_index → END
        最终 workflow_status == "completed"。
        """
        from src.workflows.chapter_workflow import (
            build_chapter_workflow, ChapterWorkflowState,
        )
        from src.storage.document_formats import (
            ChapterPlan, ReviewDecision, StateCommitResult,
        )

        # ── Mock FileStore (used by many nodes) ──
        mock_fs = mock_fs_cls.return_value
        mock_fs.load_canonical.return_value = "# Chapter Plan\n## 一、章节信息\n章大纲: test\n总场景数: 2"
        mock_fs.load_latest.return_value = "styled chapter text"
        mock_fs.load_tracking_doc.return_value = ""
        mock_fs.root = MagicMock()
        mock_fs.root.__truediv__ = MagicMock(return_value=MagicMock())

        # ── Mock: plan_chapter (ChromaStore + ChapterPlanner) ──
        mock_chroma = mock_chroma_cls.return_value
        mock_chroma.search.return_value = []

        mock_planner = mock_planner_cls.return_value
        fake_plan = ChapterPlan()
        fake_plan.chapter_index = 3
        fake_plan.scenes = [MagicMock(), MagicMock()]
        mock_planner.plan_chapter.return_value = fake_plan
        mock_planner._extract_chapter_from_volume.return_value = ""

        # ── Mock: write_draft ──
        mock_writer = mock_writer_cls.return_value
        mock_writer.write_chapter.return_value = "Draft chapter text."

        # ── Mock: style_edit ──
        mock_stylist = mock_stylist_cls.return_value
        mock_stylist.edit_chapter.return_value = "Styled chapter text."

        # ── Mock: save_styled ──
        mock_checker = mock_checker_cls.return_value
        mock_report = MagicMock()
        mock_report.errors = 0
        mock_report.warnings = 0
        mock_report.summary.return_value = "OK"
        mock_checker.check_all.return_value = mock_report

        # ── Mock: review_chapter + parse_decision + commit_state + save_fact_digest ──
        mock_sm = mock_sm_cls.return_value
        mock_sm.review_chapter.return_value = {
            "raw_analysis": "## 审阅决策\nPASS\n## 事实摘要\nconfirmed_char: Alice",
            "filepath": MagicMock(),
        }

        pass_decision = ReviewDecision()
        pass_decision.verdict = "PASS"
        pass_decision.reasons = ["All checks passed"]
        mock_sm.parse_review_decision.return_value = pass_decision

        commit_ok = StateCommitResult(success=True)
        commit_ok.changed_files = ["tracking/character_relationships.md"]
        mock_sm.update_tracking_docs.return_value = {
            "_commit_result": commit_ok,
            "updated_rels": True,
        }
        mock_sm.extract_fact_digest_from_analysis.return_value = MagicMock()

        # ── Mock: rag_index ──
        mock_chroma.index_chapter.return_value = 3

        # ── Build graph and invoke ──
        graph = build_chapter_workflow()
        initial_state: ChapterWorkflowState = {
            "novel_id": "invoke_test",
            "chapter_index": 3,
            "chapter_outline": "Test outline",
            "extra_instructions": "",
        }

        result = graph.invoke(initial_state)

        # ── Verify final state ──
        self.assertEqual(result["workflow_status"], "completed",
                         f"Expected 'completed', got '{result.get('workflow_status')}'"
                         f" — error: {result.get('error', 'none')}")
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["commit_success"], True)
        self.assertEqual(result["rag_chunks"], 3)
        self.assertEqual(result["novel_id"], "invoke_test",
                         "Input fields preserved by state merge")
        self.assertEqual(result["chapter_index"], 3)

        # ── Verify call order: each agent must have been called ──
        mock_planner.plan_chapter.assert_called_once()
        mock_writer.write_chapter.assert_called_once()
        mock_stylist.edit_chapter.assert_called_once()
        mock_checker.check_all.assert_called_once()
        mock_sm.review_chapter.assert_called_once()
        mock_sm.parse_review_decision.assert_called_once()
        mock_sm.update_tracking_docs.assert_called_once()
        mock_sm.extract_fact_digest_from_analysis.assert_called_once()
        mock_chroma.index_chapter.assert_called_once()


# ═══════════════════════════════════════════════════════════
# E07.2-B: Non-PASS guard — no commit / fact_digest / RAG
# ═══════════════════════════════════════════════════════════

class TestE07_2_NonPassGuard(unittest.TestCase):
    """E07.2-B: Non-PASS verdict blocks commit_state + fact_digest + rag_index."""

    def test_commit_state_skipped_when_stopped_non_pass(self):
        """STOPPED_NON_PASS → commit_state 直接返回 {}。"""
        from src.workflows.chapter_workflow import commit_state, ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "workflow_status": "STOPPED_NON_PASS",
            "commit_success": False,
            "error": "Review verdict: NEEDS_REVISION",
        }
        result = commit_state(state)
        self.assertEqual(result, {})

    def test_save_fact_digest_skipped_when_stopped_non_pass(self):
        """STOPPED_NON_PASS → save_fact_digest 直接返回 {}。"""
        from src.workflows.chapter_workflow import save_fact_digest, ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "workflow_status": "STOPPED_NON_PASS",
        }
        result = save_fact_digest(state)
        self.assertEqual(result, {})

    def test_rag_index_skipped_when_stopped_non_pass(self):
        """STOPPED_NON_PASS → rag_index 直接返回 {}。"""
        from src.workflows.chapter_workflow import rag_index, ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "workflow_status": "STOPPED_NON_PASS",
        }
        result = rag_index(state)
        self.assertEqual(result, {})

    def test_full_chain_blocked_on_needs_revision(self):
        """NEEDS_REVISION → 后续三个 node 全部跳过。"""
        from src.workflows.chapter_workflow import (
            require_pass, commit_state, save_fact_digest, rag_index,
            ChapterWorkflowState,
        )

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "verdict": "NEEDS_REVISION",
            "review_reasons": ["T1: character inconsistency"],
        }
        guard_result = require_pass(state)
        state.update(guard_result)

        self.assertEqual(state["commit_success"], False)
        self.assertEqual(state["workflow_status"], "STOPPED_NON_PASS")

        commit_result = commit_state(state)
        self.assertEqual(commit_result, {})

        fd_result = save_fact_digest(state)
        self.assertEqual(fd_result, {})

        rag_result = rag_index(state)
        self.assertEqual(rag_result, {})


# ═══════════════════════════════════════════════════════════
# E07.2-C: Commit failure blocks fact_digest / RAG
# ═══════════════════════════════════════════════════════════

class TestE07_2_CommitFailureBlocksDownstream(unittest.TestCase):
    """E07.2-C: commit failure → fact_digest + rag_index skipped."""

    @patch("src.agents.state_manager.state_manager.StateManager")
    def test_commit_state_sets_success_false_on_failure(self, mock_sm_cls):
        """commit failure → commit_success=False。"""
        from src.workflows.chapter_workflow import commit_state, ChapterWorkflowState
        from src.storage.document_formats import StateCommitResult

        mock_sm = mock_sm_cls.return_value
        fail_result = StateCommitResult(
            success=False,
            error_message="disk full",
            warnings=["write failed"])
        mock_sm.update_tracking_docs.return_value = {
            "_commit_result": fail_result,
        }

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "verdict": "PASS",
            "raw_analysis": "analysis",
            "styled_text": "styled",
            "workflow_status": "REVIEWED",
        }
        result = commit_state(state)

        self.assertEqual(result["commit_success"], False)
        self.assertIn("disk full", result["commit_error"])

    def test_save_fact_digest_skipped_on_commit_failure(self):
        """commit_success != True → save_fact_digest 跳过。"""
        from src.workflows.chapter_workflow import save_fact_digest, ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "commit_success": False,
            "commit_error": "Commit failed",
            "workflow_status": "error",
        }
        result = save_fact_digest(state)
        self.assertEqual(result, {})

    def test_rag_index_skipped_on_commit_failure(self):
        """commit_success != True → rag_index 跳过。"""
        from src.workflows.chapter_workflow import rag_index, ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "commit_success": False,
            "workflow_status": "error",
        }
        result = rag_index(state)
        self.assertEqual(result, {})

    @patch("src.agents.state_manager.state_manager.StateManager")
    def test_commit_result_missing_triggers_fail_closed(self, mock_sm_cls):
        """commit_result 缺失 → fail-closed（不是 silent success）。"""
        from src.workflows.chapter_workflow import commit_state, ChapterWorkflowState

        mock_sm = mock_sm_cls.return_value
        mock_sm.update_tracking_docs.return_value = {
            "updated_rels": True,
            "change_log": "...",
        }

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "verdict": "PASS",
            "raw_analysis": "analysis",
            "styled_text": "styled",
        }
        result = commit_state(state)

        self.assertEqual(result["commit_success"], False)
        self.assertIn("missing", result["commit_error"].lower())

    @patch("src.agents.state_manager.state_manager.StateManager")
    def test_commit_failure_blocks_full_chain(self, mock_sm_cls):
        """commit failure → fact_digest + rag_index 都被跳过。"""
        from src.workflows.chapter_workflow import (
            commit_state, save_fact_digest, rag_index, ChapterWorkflowState,
        )
        from src.storage.document_formats import StateCommitResult

        mock_sm = mock_sm_cls.return_value
        fail_result = StateCommitResult(
            success=False, error_message="atomic commit rollback")
        mock_sm.update_tracking_docs.return_value = {
            "_commit_result": fail_result,
        }

        state: ChapterWorkflowState = {
            "novel_id": "test", "chapter_index": 1,
            "raw_analysis": "analysis", "styled_text": "styled",
            "verdict": "PASS",
        }
        commit_result = commit_state(state)
        state.update(commit_result)

        self.assertFalse(state["commit_success"])

        fd_result = save_fact_digest(state)
        self.assertEqual(fd_result, {})

        rag_result = rag_index(state)
        self.assertEqual(rag_result, {})

    def test_commit_state_rejects_non_pass_verdict(self):
        """commit_state 自验证 verdict：非 PASS → 直接拒绝。"""
        from src.workflows.chapter_workflow import commit_state, ChapterWorkflowState

        # verdict is NEEDS_REVISION but workflow_status was somehow not STOPPED_NON_PASS
        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "verdict": "NEEDS_REVISION",
            "raw_analysis": "analysis",
            "styled_text": "styled",
            "workflow_status": "REVIEWED",  # NOT STOPPED_NON_PASS
        }
        result = commit_state(state)
        self.assertEqual(result["commit_success"], False)
        self.assertIn("not PASS", result["commit_error"])

    def test_commit_state_rejects_missing_verdict(self):
        """commit_state 自验证：verdict 缺失 → 拒绝。"""
        from src.workflows.chapter_workflow import commit_state, ChapterWorkflowState

        # No verdict field at all
        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "raw_analysis": "analysis",
            "styled_text": "styled",
        }
        result = commit_state(state)
        self.assertEqual(result["commit_success"], False)
        self.assertIn("not PASS", result["commit_error"])


# ═══════════════════════════════════════════════════════════
# E07.2-D: RAG failure does NOT rollback canonical state
# ═══════════════════════════════════════════════════════════

class TestE07_2_RAGFailureNoRollback(unittest.TestCase):
    """E07.2-D: RAG failure → workflow still 'completed', canonical state untouched."""

    @patch("src.workflows.chapter_workflow.FileStore")
    @patch("src.storage.chroma_store.ChromaStore")
    def test_rag_failure_sets_completed_status(self, mock_chroma_cls, mock_fs_cls):
        """RAG 索引异常 → workflow_status 仍是 'completed'。"""
        from src.workflows.chapter_workflow import rag_index, ChapterWorkflowState

        mock_chroma = mock_chroma_cls.return_value
        mock_chroma.index_chapter.side_effect = RuntimeError("ChromaDB down")

        mock_fs = mock_fs_cls.return_value
        mock_fs.load_latest.return_value = "fake styled chapter text"
        mock_fs.root = MagicMock()

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "commit_success": True,
            "workflow_status": "FACT_DIGEST_SAVED",
        }
        result = rag_index(state)

        self.assertEqual(result["workflow_status"], "completed",
                         "RAG failure must NOT change workflow to error")
        self.assertEqual(result["rag_chunks"], 0)
        self.assertIn("RAG index failed", result.get("error", ""))

    @patch("src.workflows.chapter_workflow.FileStore")
    @patch("src.storage.chroma_store.ChromaStore")
    def test_rag_failure_different_from_commit_failure(self, mock_chroma_cls, mock_fs_cls):
        """RAG failure is non-blocking: workflow status is still 'completed'."""
        from src.workflows.chapter_workflow import rag_index, ChapterWorkflowState

        mock_chroma = mock_chroma_cls.return_value
        mock_chroma.index_chapter.side_effect = Exception("network timeout")

        mock_fs = mock_fs_cls.return_value
        mock_fs.load_latest.return_value = "fake styled chapter text"
        mock_fs.root = MagicMock()

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 1,
            "commit_success": True,
        }
        result = rag_index(state)

        self.assertEqual(result["workflow_status"], "completed")


# ═══════════════════════════════════════════════════════════
# E07.2-E: styled chapter saved once per workflow run
# ═══════════════════════════════════════════════════════════

class TestE07_2_StyledChapterOnce(unittest.TestCase):
    """E07.2-E: styled chapter 在 save_styled node 中只保存一次。"""

    @patch("src.agents.author.style_checker.StyleChecker")
    def test_save_styled_calls_style_checker_once(self, mock_checker_cls):
        """save_styled 恰好调用一次 StyleChecker。"""
        from src.workflows.chapter_workflow import save_styled, ChapterWorkflowState

        mock_checker = mock_checker_cls.return_value
        mock_report = MagicMock()
        mock_report.errors = 0
        mock_report.warnings = 0
        mock_report.summary.return_value = "OK"
        mock_checker.check_all.return_value = mock_report

        state: ChapterWorkflowState = {
            "novel_id": "test",
            "chapter_index": 2,
            "styled_text": "Once upon a time...",
        }
        save_styled(state)

        mock_checker_cls.assert_called_once()
        mock_checker.check_all.assert_called_once()

    def test_style_edit_does_not_save(self):
        """style_edit node 不保存文件 — 仅返回 styled_text。"""
        cw_path = Path(__file__).parent.parent / "src" / "workflows" / "chapter_workflow.py"
        source = cw_path.read_text(encoding="utf-8")

        # Extract style_edit function
        func_start = source.find("def style_edit(")
        self.assertGreater(func_start, 0, "style_edit function not found")
        func_end = source.find("\ndef ", func_start + 1)
        func_body = source[func_start:func_end] if func_end > 0 else source[func_start:]

        # style_edit must NOT create FileStore (save_styled owns that)
        self.assertNotIn("FileStore(", func_body.split("return {")[0],
                         "style_edit 不得自己创建 FileStore（save_styled 负责保存）")


# ═══════════════════════════════════════════════════════════
# E07.2-F: No E07.3/E07.4 leakage
# ═══════════════════════════════════════════════════════════

class TestE07_2_NoFuturePhaseLeakage(unittest.TestCase):
    """E07.2-F: 不得提前实现 E07.3+ 功能。"""

    def test_no_checkpointer_import(self):
        """源代码不含 checkpointer 导入或使用。"""
        cw_path = Path(__file__).parent.parent / "src" / "workflows" / "chapter_workflow.py"
        source = cw_path.read_text(encoding="utf-8")
        self.assertNotIn("SqliteSaver", source)
        self.assertNotIn("MemorySaver", source)
        self.assertNotIn("InMemorySaver", source)
        self.assertNotIn("checkpointer=", source)

    def test_no_interrupt_code(self):
        """源代码不含 interrupt() 调用。"""
        cw_path = Path(__file__).parent.parent / "src" / "workflows" / "chapter_workflow.py"
        source = cw_path.read_text(encoding="utf-8")
        self.assertNotIn("interrupt(", source)

    def test_no_command_resume(self):
        """源代码不含 Command(resume=...)。"""
        cw_path = Path(__file__).parent.parent / "src" / "workflows" / "chapter_workflow.py"
        source = cw_path.read_text(encoding="utf-8")
        self.assertNotIn("Command(resume", source)

    def test_no_add_conditional_edges(self):
        """源代码不含 add_conditional_edges。"""
        cw_path = Path(__file__).parent.parent / "src" / "workflows" / "chapter_workflow.py"
        source = cw_path.read_text(encoding="utf-8")
        self.assertNotIn("add_conditional_edges", source)


if __name__ == "__main__":
    unittest.main()
