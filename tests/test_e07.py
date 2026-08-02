"""E07.1 Closure — Graph State + StateGraph Skeleton 测试。

验证：
1. State schema 可构造/传入
2. Graph 唯一业务 node = initialize_workflow
3. Topology: START → initialize_workflow → END
4. invoke 后 workflow_status == SKELETON_READY
5. initialize_workflow 返回 partial update（不原地 mutate）
6. 旧 runtime 不受影响
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestChapterWorkflowState(unittest.TestCase):
    """E07.1-A: ChapterWorkflowState schema."""

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


class TestGraphTopology(unittest.TestCase):
    """E07.1-B: Graph topology contract。"""

    def test_single_business_node(self):
        """唯一业务 node = initialize_workflow。"""
        from src.workflows.chapter_workflow import build_chapter_workflow

        graph = build_chapter_workflow()
        self.assertIsNotNone(graph)
        self.assertTrue(hasattr(graph, "invoke"))

    def test_topology_start_to_init_to_end(self):
        """Topology: START → initialize_workflow → END。"""
        from src.workflows.chapter_workflow import (
            build_chapter_workflow, ChapterWorkflowState,
        )

        graph = build_chapter_workflow()
        state: ChapterWorkflowState = {
            "novel_id": "topo",
            "chapter_index": 1,
        }
        result = graph.invoke(state)

        self.assertEqual(result["workflow_status"], "SKELETON_READY",
                         "invoke 后 workflow_status 必须为 SKELETON_READY")
        # Input fields preserved by state merge
        self.assertEqual(result["novel_id"], "topo")
        self.assertEqual(result["chapter_index"], 1)


class TestInitializeWorkflowNode(unittest.TestCase):
    """E07.1-C: initialize_workflow node contract。"""

    def test_returns_partial_update_not_full_state(self):
        """返回 partial update，不原地 mutate 输入 state。"""
        from src.workflows.chapter_workflow import (
            initialize_workflow, ChapterWorkflowState,
        )

        original: ChapterWorkflowState = {
            "novel_id": "partial_test",
            "branch_id": "main",
            "chapter_index": 42,
            "workflow_status": "running",
        }

        result = initialize_workflow(original)

        # ── Returns partial update (dict, not full state) ──
        self.assertIsInstance(result, dict)
        self.assertEqual(result["workflow_status"], "SKELETON_READY")
        self.assertIsNone(result["error"])

        # ── Does NOT mutate input state ──
        self.assertEqual(original["workflow_status"], "running",
                         "输入 state 不得被原地修改")
        self.assertEqual(original["chapter_index"], 42)

    def test_graph_invoke_merges_partial_update(self):
        """Graph invoke 后 state merge 保留输入字段。"""
        from src.workflows.chapter_workflow import (
            build_chapter_workflow, ChapterWorkflowState,
        )

        graph = build_chapter_workflow()
        state: ChapterWorkflowState = {
            "novel_id": "merge_test",
            "branch_id": "main",
            "chapter_index": 7,
        }

        result = graph.invoke(state)

        self.assertEqual(result["novel_id"], "merge_test")
        self.assertEqual(result["branch_id"], "main")
        self.assertEqual(result["chapter_index"], 7)
        self.assertEqual(result["workflow_status"], "SKELETON_READY")
        self.assertIsNone(result.get("error"))


class TestNoRuntimeSideEffects(unittest.TestCase):
    """E07.1-D: import 不触发 runtime side effects。"""

    def test_import_does_not_call_llm(self):
        import src.workflows.chapter_workflow as cw
        self.assertTrue(hasattr(cw, "build_chapter_workflow"))

    def test_import_does_not_write_files(self):
        import src.workflows.chapter_workflow as cw
        self.assertTrue(hasattr(cw, "ChapterWorkflowState"))

    def test_source_does_not_import_orchestrator(self):
        cw_path = Path(__file__).parent.parent / "src" / "workflows" / "chapter_workflow.py"
        source = cw_path.read_text(encoding="utf-8")
        self.assertNotIn("orchestrator", source)
        self.assertNotIn("Orchestrator", source)

    def test_main_py_unchanged(self):
        main_path = Path(__file__).parent.parent / "main.py"
        content = main_path.read_text(encoding="utf-8")
        self.assertNotIn("langgraph", content.lower())
        self.assertNotIn("chapter_workflow", content)
        self.assertNotIn("build_chapter_workflow", content)


if __name__ == "__main__":
    unittest.main()
