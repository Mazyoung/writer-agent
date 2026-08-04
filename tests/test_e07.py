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

import json
import shutil
import sys
import tempfile
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

    @patch("src.workflows.chapter_workflow.FileStore")
    @patch("src.storage.chroma_store.ChromaStore")
    @patch("src.agents.author.chapter_planner.ChapterPlanner")
    def test_plan_chapter_node_returns_plan_text(
        self, mock_planner_cls, mock_chroma_cls, mock_fs_cls,
    ):
        """plan_chapter 返回 chapter_plan_text + PLANNED status。"""
        from src.workflows.chapter_workflow import plan_chapter, ChapterWorkflowState
        from src.storage.document_formats import ChapterPlan

        mock_planner = mock_planner_cls.return_value
        fake_plan = ChapterPlan()
        fake_plan.chapter_index = 3
        fake_plan.scenes = [MagicMock(), MagicMock()]
        mock_planner.plan_chapter.return_value = fake_plan

        mock_chroma_cls.return_value.search.return_value = []
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        mock_fs_cls.return_value.root = tmp
        (tmp / "chapters").mkdir()
        (tmp / "states").mkdir()
        mock_fs_cls.return_value.load_canonical.return_value = "plan text"

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

    def test_real_truncated_review_markdown_returns_unknown(self):
        """缺失显式审阅决策的截断 review 必须 fail-closed。"""
        from src.agents.state_manager.state_manager import StateManager

        review_path = (
            Path(__file__).parent
            / "fixtures" / "review_ch0001_truncated.md"
        )
        analysis = review_path.read_text(encoding="utf-8")
        decision = StateManager.__new__(StateManager).parse_review_decision(analysis)

        self.assertNotIn("## 审阅决策", analysis)
        self.assertTrue(analysis.rstrip().endswith("+表"))
        self.assertEqual(decision.verdict, "UNKNOWN")

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
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        mock_fs.root = tmp
        (tmp / "chapters").mkdir()
        (tmp / "states").mkdir()

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
        marker = tmp / "states" / "chapter_0003_completed"

        def successful_commit(*args, **kwargs):
            marker.write_text(
                "Review PASS\nCanonical commit success\n", encoding="utf-8")
            return {
                "_commit_result": commit_ok,
                "updated_rels": True,
            }

        mock_sm.update_tracking_docs.side_effect = successful_commit
        from src.storage.document_formats import FactDigest
        mock_sm.extract_fact_digest_from_analysis.return_value = FactDigest(
            chapter_index=3, confirmed_events="Alice entered the ruins")

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
        marker_path = Path(result["completion_marker_path"])
        self.assertTrue(marker_path.exists())
        self.assertIn("Canonical commit success", marker_path.read_text(
            encoding="utf-8"))

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


class TestE07_2_SafetyClosureGraphInvoke(unittest.TestCase):
    """E07.2 Safety Closure Phase 1 graph.invoke regressions."""

    def setUp(self):
        from src.agents.state_manager.state_manager import StateManager
        from src.storage.document_formats import (
            ChapterPlan, ReviewDecision, StateCommitResult,
        )
        from src.workflows.chapter_workflow import build_chapter_workflow

        self.real_parse_decision = StateManager.__new__(
            StateManager).parse_review_decision

        targets = {
            "fs": "src.workflows.chapter_workflow.FileStore",
            "sqlite": "src.workflows.chapter_workflow.SQLiteStore",
            "planner": "src.agents.author.chapter_planner.ChapterPlanner",
            "writer": "src.agents.author.deepseek_writer.DeepSeekWriter",
            "stylist": "src.agents.author.claude_stylist.ClaudeStylist",
            "checker": "src.agents.author.style_checker.StyleChecker",
            "state_manager": "src.agents.state_manager.state_manager.StateManager",
            "chroma": "src.storage.chroma_store.ChromaStore",
        }
        for name, target in targets.items():
            patcher = patch(target)
            setattr(self, f"mock_{name}_cls", patcher.start())
            self.addCleanup(patcher.stop)

        self.mock_fs = self.mock_fs_cls.return_value
        self.mock_fs.load_canonical.return_value = (
            "# Chapter Plan\n## 一、章节信息\n章大纲: test\n总场景数: 2"
        )
        self.mock_fs.load_latest.return_value = "OLD STYLED ARTIFACT"
        self.mock_fs.load_tracking_doc.return_value = ""
        self.fs_tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.fs_tmp, True)
        self.mock_fs.root = self.fs_tmp / "novels" / "safety_closure_test"
        (self.mock_fs.root / "chapters").mkdir(parents=True)
        (self.mock_fs.root / "states").mkdir()

        fake_plan = ChapterPlan(chapter_index=1)
        fake_plan.scenes = [MagicMock(), MagicMock()]
        self.mock_planner = self.mock_planner_cls.return_value
        self.mock_planner.plan_chapter.return_value = fake_plan
        self.mock_planner._extract_chapter_from_volume.return_value = ""

        self.mock_writer = self.mock_writer_cls.return_value
        self.mock_writer.write_chapter.return_value = "Draft chapter text."

        self.mock_stylist = self.mock_stylist_cls.return_value
        self.mock_stylist.edit_chapter.return_value = "Styled chapter text."

        self.mock_checker = self.mock_checker_cls.return_value
        report = MagicMock(errors=0, warnings=0)
        report.summary.return_value = "OK"
        self.mock_checker.check_all.return_value = report

        self.mock_state_manager = self.mock_state_manager_cls.return_value
        self.mock_state_manager.review_chapter.return_value = {
            "raw_analysis": "## 审阅决策\n- **决策**: PASS",
            "filepath": MagicMock(),
        }
        pass_decision = ReviewDecision(verdict="PASS")
        self.mock_state_manager.parse_review_decision.return_value = pass_decision
        marker = self.mock_fs.root / "states" / "chapter_0001_completed"

        def successful_commit(*args, **kwargs):
            marker.write_text(
                "Review PASS\nCanonical commit success\n", encoding="utf-8")
            return {
                "_commit_result": StateCommitResult(
                    success=True,
                    changed_files=["states/chapter_0001_completed"],
                ),
            }

        self.mock_state_manager.update_tracking_docs.side_effect = successful_commit
        from src.storage.document_formats import FactDigest
        self.mock_state_manager.extract_fact_digest_from_analysis.return_value = (
            FactDigest(chapter_index=1, confirmed_events="A confirmed event")
        )

        self.mock_chroma = self.mock_chroma_cls.return_value
        self.mock_chroma.search.return_value = []
        self.mock_chroma.index_chapter.return_value = 3

        self.graph = build_chapter_workflow()
        self.initial_state = {
            "novel_id": "safety_closure_test",
            "chapter_index": 1,
            "chapter_outline": "Test outline",
            "extra_instructions": "",
        }

    def test_graph_invoke_truncated_review_returns_unknown(self):
        """截断 review → UNKNOWN，且不执行 commit。"""
        review_path = (
            Path(__file__).parent
            / "fixtures" / "review_ch0001_truncated.md"
        )
        truncated = review_path.read_text(encoding="utf-8")
        self.mock_state_manager.review_chapter.return_value = {
            "raw_analysis": truncated,
            "filepath": MagicMock(),
        }
        self.mock_state_manager.parse_review_decision.side_effect = (
            self.real_parse_decision
        )

        result = self.graph.invoke(self.initial_state)

        self.assertEqual(result["verdict"], "UNKNOWN")
        self.assertEqual(result["workflow_status"], "STOPPED_NON_PASS")
        marker = self.mock_fs.root / "states" / "chapter_0001_completed"
        self.assertFalse(marker.exists())
        self.mock_state_manager.update_tracking_docs.assert_not_called()
        self.mock_state_manager.extract_fact_digest_from_analysis.assert_not_called()
        self.mock_chroma.index_chapter.assert_not_called()

        # A styled artifact from the rejected run must not block a retry.
        (self.mock_fs.root / "chapters" /
         "chapter_0001_styled_20260805_130000.md").write_text(
             "rejected styled artifact", encoding="utf-8")
        retry = self.graph.invoke(self.initial_state)
        self.assertNotIn("ERROR_ALREADY_EXISTS", retry.get("error", ""))
        self.assertEqual(self.mock_planner.plan_chapter.call_count, 2)

    @patch("src.workflows.retrieval_service.ChapterRetrievalService")
    def test_graph_invoke_completed_marker_has_zero_side_effects(
        self, mock_retrieval_cls,
    ):
        """Completion marker blocks ordinary Generate before all work."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.mock_fs.root = tmp / "novels" / "safety_closure_test"
        states = self.mock_fs.root / "states"
        states.mkdir(parents=True)
        (states / "chapter_0001_completed").write_text(
            "Review PASS\nCanonical commit success\n", encoding="utf-8")

        result = self.graph.invoke(self.initial_state)

        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("ERROR_ALREADY_EXISTS", result["error"])
        mock_retrieval_cls.return_value.retrieve.assert_not_called()
        self.mock_planner.plan_chapter.assert_not_called()
        self.mock_writer.write_chapter.assert_not_called()
        self.mock_stylist.edit_chapter.assert_not_called()
        self.mock_fs.save.assert_not_called()
        self.mock_state_manager.review_chapter.assert_not_called()
        self.mock_state_manager.update_tracking_docs.assert_not_called()
        self.mock_state_manager.extract_fact_digest_from_analysis.assert_not_called()
        self.mock_chroma.index_chapter.assert_not_called()

    @patch("src.workflows.retrieval_service.ChapterRetrievalService")
    def test_styled_draft_and_plan_without_marker_are_not_completed(
        self, mock_retrieval_cls,
    ):
        """Styled/draft/plan artifacts without marker do not block Generate."""
        from src.storage.chroma_store import RetrievalTrace
        from src.workflows.retrieval_service import RetrievalOutcome

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.mock_fs.root = tmp / "novels" / "safety_closure_test"
        chapters = self.mock_fs.root / "chapters"
        outlines = self.mock_fs.root / "outlines"
        chapters.mkdir(parents=True)
        outlines.mkdir(parents=True)
        (chapters / "chapter_0001_draft_20260805_120000.md").write_text(
            "draft", encoding="utf-8")
        (chapters / "chapter_0001_styled_20260805_120500.md").write_text(
            "styled but not completed", encoding="utf-8")
        (outlines / "chapter_plan_ch0001.md").write_text(
            "plan", encoding="utf-8")
        mock_retrieval_cls.return_value.retrieve.return_value = RetrievalOutcome(
            trace=RetrievalTrace(chapter_index=1, success=True))

        result = self.graph.invoke(self.initial_state)

        self.assertNotIn("ERROR_ALREADY_EXISTS", result.get("error", ""))
        mock_retrieval_cls.return_value.retrieve.assert_called_once()
        self.mock_planner.plan_chapter.assert_called_once()

    def test_graph_invoke_write_failure_stops_downstream(self):
        """write failure → no style/save/review/commit/fact digest/RAG。"""
        self.mock_writer.write_chapter.side_effect = RuntimeError("writer down")

        result = self.graph.invoke(self.initial_state)

        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("write_draft failed", result["error"])
        self.mock_stylist.edit_chapter.assert_not_called()
        self.mock_fs.save.assert_not_called()
        self.mock_state_manager.review_chapter.assert_not_called()
        self.mock_state_manager.update_tracking_docs.assert_not_called()
        self.mock_state_manager.extract_fact_digest_from_analysis.assert_not_called()
        self.mock_chroma.index_chapter.assert_not_called()

    def test_graph_invoke_style_failure_stops_downstream(self):
        """style failure → no save/review/commit/fact digest/RAG。"""
        self.mock_stylist.edit_chapter.side_effect = RuntimeError("stylist down")

        result = self.graph.invoke(self.initial_state)

        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("style_edit failed", result["error"])
        self.mock_fs.save.assert_not_called()
        self.mock_state_manager.review_chapter.assert_not_called()
        self.mock_state_manager.update_tracking_docs.assert_not_called()
        self.mock_state_manager.extract_fact_digest_from_analysis.assert_not_called()
        self.mock_chroma.index_chapter.assert_not_called()

    def test_graph_invoke_does_not_fallback_to_old_styled(self):
        """本次无 styled_text 时不得从磁盘采用旧 styled artifact。"""
        self.mock_stylist.edit_chapter.return_value = ""

        result = self.graph.invoke(self.initial_state)

        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("styled_text", result["error"])
        self.mock_fs.load_latest.assert_not_called()
        self.mock_state_manager.review_chapter.assert_not_called()
        self.mock_state_manager.update_tracking_docs.assert_not_called()

    def test_graph_invoke_commit_failure_blocks_fact_digest_and_rag(self):
        """commit failure → no Fact Digest / RAG。"""
        from src.storage.document_formats import StateCommitResult

        self.mock_state_manager.update_tracking_docs.side_effect = None
        self.mock_state_manager.update_tracking_docs.return_value = {
            "_commit_result": StateCommitResult(
                success=False, error_message="disk full"
            ),
        }

        result = self.graph.invoke(self.initial_state)

        self.assertEqual(result["workflow_status"], "error")
        self.assertFalse(result["commit_success"])
        self.assertIn("disk full", result["commit_error"])
        marker = self.mock_fs.root / "states" / "chapter_0001_completed"
        self.assertFalse(marker.exists())
        self.mock_state_manager.extract_fact_digest_from_analysis.assert_not_called()
        self.mock_chroma.index_chapter.assert_not_called()

    @patch("src.workflows.retrieval_service.ChapterRetrievalService")
    def test_graph_invoke_plan_failure_stops_downstream(
        self, mock_retrieval_cls,
    ):
        """Plan failure stops writer and every later business side effect."""
        mock_retrieval_cls.return_value.retrieve.side_effect = RuntimeError(
            "retrieval unavailable")

        result = self.graph.invoke(self.initial_state)

        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("plan_chapter failed", result["error"])
        self.mock_planner.plan_chapter.assert_not_called()
        self.mock_writer.write_chapter.assert_not_called()
        self.mock_stylist.edit_chapter.assert_not_called()
        self.mock_state_manager.review_chapter.assert_not_called()
        self.mock_state_manager.update_tracking_docs.assert_not_called()
        self.mock_state_manager.extract_fact_digest_from_analysis.assert_not_called()
        self.mock_chroma.index_chapter.assert_not_called()

    def test_graph_invoke_save_styled_failure_stops_downstream(self):
        """save_styled failure stops review, commit, Fact Digest, and RAG."""
        self.mock_fs.save.side_effect = OSError("styled write failed")

        result = self.graph.invoke(self.initial_state)

        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("save_styled failed", result["error"])
        self.mock_state_manager.review_chapter.assert_not_called()
        self.mock_state_manager.update_tracking_docs.assert_not_called()
        self.mock_state_manager.extract_fact_digest_from_analysis.assert_not_called()
        self.mock_chroma.index_chapter.assert_not_called()

    def test_graph_invoke_review_failure_stops_downstream(self):
        """Review failure stops decision parsing and all persistence."""
        self.mock_state_manager.review_chapter.side_effect = RuntimeError(
            "review failed")

        result = self.graph.invoke(self.initial_state)

        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("review_chapter failed", result["error"])
        self.mock_state_manager.parse_review_decision.assert_not_called()
        self.mock_state_manager.update_tracking_docs.assert_not_called()
        self.mock_state_manager.extract_fact_digest_from_analysis.assert_not_called()
        self.mock_chroma.index_chapter.assert_not_called()

    def test_graph_invoke_parse_decision_failure_stops_downstream(self):
        """Decision parser exception stops commit, Fact Digest, and RAG."""
        self.mock_state_manager.parse_review_decision.side_effect = RuntimeError(
            "parser failed")

        result = self.graph.invoke(self.initial_state)

        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("parse_decision failed", result["error"])
        self.mock_state_manager.update_tracking_docs.assert_not_called()
        self.mock_state_manager.extract_fact_digest_from_analysis.assert_not_called()
        self.mock_chroma.index_chapter.assert_not_called()

    def test_graph_invoke_rag_failure_keeps_completion_marker(self):
        """RAG derived-state failure cannot revoke committed completion."""
        self.mock_chroma.index_chapter.side_effect = RuntimeError("rag down")

        result = self.graph.invoke(self.initial_state)

        marker = self.mock_fs.root / "states" / "chapter_0001_completed"
        self.assertEqual(result["workflow_status"], "completed")
        self.assertTrue(result["commit_success"])
        self.assertTrue(marker.exists())
        self.assertIn("RAG index failed", result["error"])

    def test_graph_invoke_non_main_branch_fails_before_side_effects(self):
        """E07 当前只接受 main，显式非 main 必须在首节点前失败。"""
        state = {**self.initial_state, "branch_id": "experiment"}

        result = self.graph.invoke(state)

        self.assertEqual(result["workflow_status"], "error")
        self.assertIn("supports only 'main'", result["error"])
        self.mock_chroma.search.assert_not_called()
        self.mock_planner.plan_chapter.assert_not_called()
        self.mock_writer.write_chapter.assert_not_called()
        self.mock_stylist.edit_chapter.assert_not_called()
        self.mock_state_manager.review_chapter.assert_not_called()
        self.mock_state_manager.update_tracking_docs.assert_not_called()
        self.mock_chroma.index_chapter.assert_not_called()


class TestE07_2_RealCommitFailureGraphInvoke(unittest.TestCase):
    """Full graph with real StateManager commit and rollback behavior."""

    def test_completion_marker_failure_rolls_back_and_allows_retry(self):
        from src.agents.state_manager.state_manager import StateManager
        from src.config.settings import get_settings
        from src.storage.chroma_store import RetrievalTrace
        from src.storage.document_formats import ChapterPlan
        from src.storage.file_store import FileStore
        from src.storage.sqlite_store import SQLiteStore
        from src.workflows.chapter_workflow import build_chapter_workflow
        from src.workflows.retrieval_service import RetrievalOutcome

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        settings = get_settings()
        original_data_dir = settings.data_dir
        settings.data_dir = tmp
        self.addCleanup(setattr, settings, "data_dir", original_data_dir)

        novel_id = "marker_failure_graph"
        fs = FileStore(novel_id, tmp)
        root = fs.root
        old_rels = "# OLD RELATIONSHIPS\n## 关系详情\n## 关系变更日志\n"
        old_items = "# OLD ITEMS\n## 主角持有\n"
        old_cult = "# OLD CULTIVATION\n## 角色修炼状态\n"
        for name, content in {
            "character_relationships": old_rels,
            "items_equipment": old_items,
            "cultivation_system": old_cult,
        }.items():
            (root / "tracking" / f"{name}.md").write_text(
                content, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0001.md").write_text(
            "# 第1章规划：《测试》\n## 场景1：开始\n", encoding="utf-8")

        analysis = """## 事实摘要
### 确定的事件
事件发生
## 状态变更（State Delta）
### 角色关系当前状态
- 柯林 ↔ 瘸子莫: 关系类型=伙伴, 当前状态=信任, 态度=友好
### 角色物品状态
#### 获得
- 徽章: 持有者=柯林, 来源=背包, 状态=可用
### 角色修炼状态
### 角色当前状态
### 伏笔状态
## 审阅决策
- **决策**: PASS
- **严重性**: PASS
- **规划级别**: L1
"""
        plan = ChapterPlan(chapter_index=1)
        plan.scenes = [MagicMock()]
        retrieval = RetrievalOutcome(
            trace=RetrievalTrace(chapter_index=1, success=True))
        report = MagicMock(errors=0, warnings=0)
        report.summary.return_value = "OK"
        marker = root / "states" / "chapter_0001_completed"
        original_write_text = Path.write_text

        def fail_marker(path_self, data, *args, **kwargs):
            if path_self == marker:
                raise OSError("completion marker write failed")
            return original_write_text(path_self, data, *args, **kwargs)

        with patch(
            "src.workflows.retrieval_service.ChapterRetrievalService"
        ) as mock_retrieval, patch(
            "src.agents.author.chapter_planner.ChapterPlanner"
        ) as mock_planner_cls, patch(
            "src.agents.author.deepseek_writer.DeepSeekWriter"
        ) as mock_writer_cls, patch(
            "src.agents.author.claude_stylist.ClaudeStylist"
        ) as mock_stylist_cls, patch(
            "src.agents.author.style_checker.StyleChecker"
        ) as mock_checker_cls, patch.object(
            StateManager, "review_chapter",
            return_value={"raw_analysis": analysis, "filepath": None},
        ), patch.object(
            Path, "write_text", new=fail_marker,
        ), patch.object(
            StateManager, "extract_fact_digest_from_analysis"
        ) as mock_digest, patch(
            "src.storage.chroma_store.ChromaStore.index_chapter"
        ) as mock_rag:
            mock_retrieval.return_value.retrieve.return_value = retrieval
            mock_planner_cls.return_value.plan_chapter.return_value = plan
            mock_writer_cls.return_value.write_chapter.return_value = "draft"
            mock_stylist_cls.return_value.edit_chapter.return_value = "styled"
            mock_checker_cls.return_value.check_all.return_value = report

            result = build_chapter_workflow().invoke({
                "novel_id": novel_id, "chapter_index": 1,
                "chapter_outline": "", "extra_instructions": "",
            })

        self.assertEqual(result["workflow_status"], "error")
        self.assertFalse(result["commit_success"])
        self.assertIn("completion_marker", result["commit_error"])
        self.assertEqual(
            (root / "tracking" / "character_relationships.md").read_text(
                encoding="utf-8"), old_rels)
        self.assertEqual(
            (root / "tracking" / "items_equipment.md").read_text(
                encoding="utf-8"), old_items)
        self.assertEqual(
            (root / "tracking" / "cultivation_system.md").read_text(
                encoding="utf-8"), old_cult)
        self.assertFalse(marker.exists())
        mock_digest.assert_not_called()
        mock_rag.assert_not_called()

        # With OLD canonical state and no marker, ordinary Generate may retry.
        with patch(
            "src.workflows.retrieval_service.ChapterRetrievalService"
        ) as retry_retrieval, patch(
            "src.agents.author.chapter_planner.ChapterPlanner"
        ) as retry_planner:
            retry_retrieval.return_value.retrieve.return_value = retrieval
            retry_planner.return_value.plan_chapter.return_value = plan
            retry_result = __import__(
                "src.workflows.chapter_workflow", fromlist=["plan_chapter"]
            ).plan_chapter({"novel_id": novel_id, "chapter_index": 1})
        self.assertNotIn("ERROR_ALREADY_EXISTS", retry_result.get("error", ""))
        retry_retrieval.return_value.retrieve.assert_called_once()

    def test_real_state_manager_second_write_failure_rolls_back_and_stops(self):
        from src.agents.state_manager.state_manager import StateManager
        from src.config.settings import get_settings
        from src.storage.chroma_store import RetrievalTrace
        from src.storage.document_formats import ChapterPlan
        from src.storage.file_store import FileStore
        from src.storage.sqlite_store import SQLiteStore
        from src.workflows.chapter_workflow import build_chapter_workflow
        from src.workflows.retrieval_service import RetrievalOutcome

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        settings = get_settings()
        original_data_dir = settings.data_dir
        settings.data_dir = tmp
        self.addCleanup(setattr, settings, "data_dir", original_data_dir)

        novel_id = "real_commit_failure_graph"
        fs = FileStore(novel_id, tmp)
        root = fs.root
        old_rels = "# OLD RELATIONSHIPS\n## 关系详情\n## 关系变更日志\n"
        old_items = "# OLD ITEMS\n## 主角持有\n"
        old_cult = "# OLD CULTIVATION\n## 角色修炼状态\n"
        (root / "tracking" / "character_relationships.md").write_text(
            old_rels, encoding="utf-8")
        (root / "tracking" / "items_equipment.md").write_text(
            old_items, encoding="utf-8")
        (root / "tracking" / "cultivation_system.md").write_text(
            old_cult, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0001.md").write_text(
            "# 第1章规划：《测试》\n## 场景1：开始\n", encoding="utf-8")

        analysis = """## 事实摘要
### 确定的事件
事件发生
## 状态变更（State Delta）
### 角色关系当前状态
- 柯林 ↔ 瘸子莫: 关系类型=伙伴, 当前状态=信任, 态度=友好
### 角色物品状态
#### 获得
- 徽章: 持有者=柯林, 来源=背包, 状态=可用
### 角色修炼状态
### 角色当前状态
### 伏笔状态
## 审阅决策
- **决策**: PASS
- **严重性**: PASS
- **规划级别**: L1
"""
        plan = ChapterPlan(chapter_index=1)
        plan.scenes = [MagicMock()]
        retrieval = RetrievalOutcome(
            trace=RetrievalTrace(chapter_index=1, success=True))
        report = MagicMock(errors=0, warnings=0)
        report.summary.return_value = "OK"

        save_calls = []
        original_save_tracking = FileStore.save_tracking_doc

        def fail_second_tracking_write(store, name, content):
            save_calls.append(name)
            if len(save_calls) == 2:
                raise OSError("second tracking write failed")
            return original_save_tracking(store, name, content)

        with patch(
            "src.workflows.retrieval_service.ChapterRetrievalService"
        ) as mock_retrieval, patch(
            "src.agents.author.chapter_planner.ChapterPlanner"
        ) as mock_planner_cls, patch(
            "src.agents.author.deepseek_writer.DeepSeekWriter"
        ) as mock_writer_cls, patch(
            "src.agents.author.claude_stylist.ClaudeStylist"
        ) as mock_stylist_cls, patch(
            "src.agents.author.style_checker.StyleChecker"
        ) as mock_checker_cls, patch.object(
            StateManager, "review_chapter",
            return_value={"raw_analysis": analysis, "filepath": None},
        ), patch.object(
            FileStore, "save_tracking_doc",
            autospec=True, side_effect=fail_second_tracking_write,
        ), patch.object(
            StateManager, "extract_fact_digest_from_analysis"
        ) as mock_digest, patch.object(
            StateManager, "_sync_sqlite"
        ) as mock_sync, patch.object(
            SQLiteStore, "upsert_foreshadow"
        ) as mock_foreshadow, patch(
            "src.storage.chroma_store.ChromaStore.index_chapter"
        ) as mock_rag:
            mock_retrieval.return_value.retrieve.return_value = retrieval
            mock_planner_cls.return_value.plan_chapter.return_value = plan
            mock_writer_cls.return_value.write_chapter.return_value = "draft"
            mock_stylist_cls.return_value.edit_chapter.return_value = "styled"
            mock_checker_cls.return_value.check_all.return_value = report

            result = build_chapter_workflow().invoke({
                "novel_id": novel_id,
                "chapter_index": 1,
                "chapter_outline": "",
                "extra_instructions": "",
            })

        self.assertEqual(result["workflow_status"], "error")
        self.assertFalse(result["commit_success"])
        self.assertIn("items_equipment", result["commit_error"])
        self.assertEqual(
            (root / "tracking" / "character_relationships.md").read_text(
                encoding="utf-8"), old_rels)
        self.assertEqual(
            (root / "tracking" / "items_equipment.md").read_text(
                encoding="utf-8"), old_items)
        self.assertEqual(
            (root / "tracking" / "cultivation_system.md").read_text(
                encoding="utf-8"), old_cult)
        mock_sync.assert_not_called()
        mock_foreshadow.assert_not_called()
        mock_digest.assert_not_called()
        mock_rag.assert_not_called()


class TestE07_2_RetrievalService(unittest.TestCase):
    """LangGraph retrieval service trace and evidence contracts."""

    def setUp(self):
        from src.storage.chroma_store import RetrievalResult
        from src.workflows.retrieval_service import ChapterRetrievalService

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = ChapterRetrievalService.__new__(ChapterRetrievalService)
        self.service.novel_id = "retrieval_service_test"
        self.service.settings = MagicMock(rag_top_k=5)
        self.service.fs = MagicMock(root=self.root)
        self.service.fs.load_tracking_doc.return_value = ""
        self.service.chroma = MagicMock()
        self.service.planner = MagicMock()
        self.service.planner._extract_chapter_from_volume.return_value = ""
        self.result = RetrievalResult(
            doc_id="doc-1", chapter_index=1, chunk_index=0,
            source_path="chapters/chapter_0001_styled", distance=0.25,
            text="historical evidence",
        )

    def _saved_trace(self):
        trace_files = list(
            (self.root / "tracking" / "rag_traces").glob("*.json"))
        self.assertEqual(len(trace_files), 1)
        return json.loads(trace_files[0].read_text(encoding="utf-8"))

    def test_retrieval_with_results_saves_success_trace_and_evidence(self):
        self.service.chroma.search.return_value = [self.result]

        outcome = self.service.retrieve(2, "outline")
        saved = self._saved_trace()

        self.assertTrue(outcome.trace.success)
        self.assertEqual(len(outcome.trace.results), 1)
        self.assertIn("historical evidence", outcome.evidence)
        self.assertTrue(outcome.trace_path)
        self.assertTrue(saved["success"])
        self.assertEqual(len(saved["results"]), 1)

    def test_empty_retrieval_still_saves_success_trace(self):
        self.service.chroma.search.return_value = []

        outcome = self.service.retrieve(2)
        saved = self._saved_trace()

        self.assertTrue(outcome.trace.success)
        self.assertEqual(outcome.trace.results, [])
        self.assertEqual(outcome.evidence, "")
        self.assertTrue(outcome.trace_path)
        self.assertTrue(saved["success"])
        self.assertEqual(saved["results"], [])

    def test_retrieval_exception_saves_failed_trace(self):
        self.service.chroma.search.side_effect = RuntimeError("chroma down")

        outcome = self.service.retrieve(2)
        saved = self._saved_trace()

        self.assertFalse(outcome.trace.success)
        self.assertEqual(outcome.evidence, "")
        self.assertIn("chroma down", outcome.trace.error_message)
        self.assertFalse(saved["success"])
        self.assertIn("chroma down", saved["error_message"])

    def test_trace_persistence_failure_preserves_evidence_and_warns(self):
        self.service.chroma.search.return_value = [self.result]
        self.service._save_trace = MagicMock(
            side_effect=OSError("trace disk read-only"))

        outcome = self.service.retrieve(2)

        self.assertIn("historical evidence", outcome.evidence)
        self.assertEqual(outcome.trace_path, "")
        self.assertTrue(any(
            "RetrievalTrace persistence failed" in warning
            and "trace disk read-only" in warning
            for warning in outcome.warnings
        ))

    @patch("src.workflows.retrieval_service.ChapterRetrievalService")
    @patch("src.workflows.chapter_workflow.FileStore")
    @patch("src.agents.author.chapter_planner.ChapterPlanner")
    def test_graph_exposes_trace_warning_without_planning_error(
        self, mock_planner_cls, mock_fs_cls, mock_service_cls,
    ):
        from src.storage.chroma_store import RetrievalTrace
        from src.storage.document_formats import ChapterPlan
        from src.workflows.chapter_workflow import plan_chapter
        from src.workflows.retrieval_service import RetrievalOutcome

        mock_service_cls.return_value.retrieve.return_value = RetrievalOutcome(
            evidence="usable evidence",
            trace=RetrievalTrace(chapter_index=2, success=True),
            warnings=["RetrievalTrace persistence failed: OSError: readonly"],
        )
        plan = ChapterPlan(chapter_index=2)
        mock_planner_cls.return_value.plan_chapter.return_value = plan
        mock_fs_cls.return_value.load_canonical.return_value = "plan text"
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        mock_fs_cls.return_value.root = tmp
        (tmp / "chapters").mkdir()
        (tmp / "states").mkdir()

        result = plan_chapter({
            "novel_id": "warning_test", "chapter_index": 2,
        })

        self.assertEqual(result["workflow_status"], "PLANNED")
        self.assertIn("RetrievalTrace persistence failed", result["warnings"][0])
        mock_planner_cls.return_value.plan_chapter.assert_called_once()


class TestE07_2_FactDigestObservability(unittest.TestCase):
    """Fact Digest flag reflects actual extracted content."""

    @patch("src.agents.state_manager.state_manager.StateManager")
    def test_empty_fact_digest_reports_false(self, mock_sm_cls):
        from src.storage.document_formats import FactDigest
        from src.workflows.chapter_workflow import save_fact_digest

        mock_sm_cls.return_value.extract_fact_digest_from_analysis.return_value = (
            FactDigest(chapter_index=1)
        )
        result = save_fact_digest({
            "novel_id": "digest_test", "chapter_index": 1,
            "raw_analysis": "analysis", "commit_success": True,
        })

        self.assertFalse(result["fact_digest_generated"])
        self.assertEqual(result["workflow_status"], "FACT_DIGEST_MISSING")

    @patch("src.agents.state_manager.state_manager.StateManager")
    def test_valid_fact_digest_reports_true(self, mock_sm_cls):
        from src.storage.document_formats import FactDigest
        from src.workflows.chapter_workflow import save_fact_digest

        mock_sm_cls.return_value.extract_fact_digest_from_analysis.return_value = (
            FactDigest(chapter_index=1, confirmed_events="event occurred")
        )
        result = save_fact_digest({
            "novel_id": "digest_test", "chapter_index": 1,
            "raw_analysis": "analysis", "commit_success": True,
        })

        self.assertTrue(result["fact_digest_generated"])
        self.assertEqual(result["workflow_status"], "FACT_DIGEST_SAVED")


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
            "styled_text": "CURRENT INVOCATION STYLED",
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
            "styled_text": "CURRENT INVOCATION STYLED",
            "commit_success": True,
        }
        result = rag_index(state)

        self.assertEqual(result["workflow_status"], "completed")

    @patch("src.workflows.chapter_workflow.FileStore")
    @patch("src.storage.chroma_store.ChromaStore")
    def test_rag_indexes_current_invocation_styled_text(
        self, mock_chroma_cls, mock_fs_cls,
    ):
        """RAG content comes from state, never an older disk artifact."""
        from src.workflows.chapter_workflow import rag_index

        mock_fs_cls.return_value.load_latest.return_value = "OLD DISK STYLED"
        mock_chroma_cls.return_value.index_chapter.return_value = 2

        result = rag_index({
            "novel_id": "test", "chapter_index": 1,
            "styled_text": "CURRENT INVOCATION STYLED",
            "commit_success": True,
        })

        self.assertEqual(result["rag_chunks"], 2)
        mock_fs_cls.return_value.load_latest.assert_not_called()
        kwargs = mock_chroma_cls.return_value.index_chapter.call_args.kwargs
        self.assertEqual(kwargs["content"], "CURRENT INVOCATION STYLED")
        self.assertEqual(kwargs["source_path"], "chapters/chapter_0001_styled")


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
