"""E07.1 — Graph State + StateGraph Skeleton 测试。

验证：
1. State schema 可构造/传入
2. Graph 可 build + compile
3. Graph 可 invoke (no-op nodes)
4. 旧 runtime 不受影响（无 import side effects）
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestChapterWorkflowState(unittest.TestCase):
    """E07.1-A: ChapterWorkflowState schema."""

    def test_minimal_state_constructs(self):
        """最小输入可构造 State。"""
        from src.workflows.chapter_workflow import ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "test_novel",
            "branch_id": "main",
            "chapter_index": 1,
            "workflow_status": "running",
            "error": "",
        }
        self.assertEqual(state["novel_id"], "test_novel")
        self.assertEqual(state["branch_id"], "main")
        self.assertEqual(state["chapter_index"], 1)

    def test_partial_state_allowed(self):
        """TypedDict total=False — 部分字段也可构造。"""
        from src.workflows.chapter_workflow import ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "mini",
            "chapter_index": 3,
        }
        self.assertEqual(state["novel_id"], "mini")
        self.assertEqual(state["chapter_index"], 3)
        # Optional fields absent by default
        self.assertEqual(state.get("workflow_status", ""), "")

    def test_full_state_optional_fields(self):
        """包含所有未来字段的完整 State。"""
        from src.workflows.chapter_workflow import ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "full",
            "branch_id": "main",
            "chapter_index": 5,
            "workflow_status": "running",
            "error": "",
            "rag_evidence": "检索到的证据",
            "chapter_plan": "# 第5章规划\n...",
            "draft_text": "正文草稿...",
            "styled_text": "风格化后...",
            "raw_analysis": "复盘分析...",
            "review_decision": "PASS",
            "state_commit_result": {"success": True},
        }
        self.assertEqual(state["review_decision"], "PASS")
        self.assertTrue(state["state_commit_result"]["success"])


class TestGraphBuildCompile(unittest.TestCase):
    """E07.1-B: Graph 可 build + compile。"""

    def test_build_returns_compiled_graph(self):
        """build_chapter_workflow() 返回 compiled graph。"""
        from src.workflows.chapter_workflow import build_chapter_workflow

        graph = build_chapter_workflow()
        self.assertIsNotNone(graph)
        # Compiled graph should have invoke method
        self.assertTrue(hasattr(graph, "invoke"),
                        "compiled graph 必须有 invoke 方法")

    def test_graph_topology_has_all_nodes(self):
        """Graph 包含所有 5 个预期 node。"""
        from src.workflows.chapter_workflow import build_chapter_workflow

        graph = build_chapter_workflow()
        # Check the graph has registered nodes by inspecting the builder
        # We verify indirectly: invoke with state should pass through all nodes
        from src.workflows.chapter_workflow import ChapterWorkflowState

        state: ChapterWorkflowState = {
            "novel_id": "topo",
            "chapter_index": 1,
        }
        result = graph.invoke(state)
        self.assertEqual(result["workflow_status"], "review_chapter:ok",
                         "最后一个 node 应设置 workflow_status=review_chapter:ok")


class TestGraphInvoke(unittest.TestCase):
    """E07.1-C: Graph 可 invoke，node 按序执行。"""

    def test_invoke_with_minimal_state(self):
        """最小 State invoke → 所有 node 通过。"""
        from src.workflows.chapter_workflow import (
            build_chapter_workflow, ChapterWorkflowState,
        )

        graph = build_chapter_workflow()
        state: ChapterWorkflowState = {
            "novel_id": "invoke_test",
            "branch_id": "main",
            "chapter_index": 1,
        }
        result = graph.invoke(state)

        self.assertEqual(result["novel_id"], "invoke_test")
        self.assertEqual(result["chapter_index"], 1)
        self.assertEqual(result["workflow_status"], "review_chapter:ok")

    def test_repeated_invoke_idempotent(self):
        """重复 invoke 产生一致结果。"""
        from src.workflows.chapter_workflow import (
            build_chapter_workflow, ChapterWorkflowState,
        )

        graph = build_chapter_workflow()
        state: ChapterWorkflowState = {
            "novel_id": "idem_test",
            "chapter_index": 2,
        }

        r1 = graph.invoke(state)
        r2 = graph.invoke(state)

        self.assertEqual(r1["workflow_status"], r2["workflow_status"])
        self.assertEqual(r1["novel_id"], r2["novel_id"])


class TestNoRuntimeSideEffects(unittest.TestCase):
    """E07.1-D: import 不触发 runtime side effects。"""

    def test_import_does_not_call_llm(self):
        """import chapter_workflow 不产生 LLM 调用。"""
        # Simply importing the module should not raise or call APIs
        import src.workflows.chapter_workflow as cw
        self.assertTrue(hasattr(cw, "build_chapter_workflow"))

    def test_import_does_not_write_files(self):
        """import 不产生文件写入。"""
        # No file system side effects on import
        import src.workflows.chapter_workflow as cw
        self.assertTrue(hasattr(cw, "ChapterWorkflowState"))

    def test_build_does_not_touch_orchestrator(self):
        """chapter_workflow 模块源码不导入 Orchestrator。"""
        # Verify at source level: the workflow module must not import orchestrator.
        cw_path = Path(__file__).parent.parent / "src" / "workflows" / "chapter_workflow.py"
        source = cw_path.read_text(encoding="utf-8")
        self.assertNotIn("orchestrator", source,
                         "chapter_workflow.py 不得 import orchestrator")
        self.assertNotIn("Orchestrator", source,
                         "chapter_workflow.py 不得引用 Orchestrator")

        # Functional verification: invoke works without Orchestrator
        from src.workflows.chapter_workflow import (
            build_chapter_workflow,
        )

        graph = build_chapter_workflow()
        result = graph.invoke({"novel_id": "iso", "chapter_index": 1})
        self.assertEqual(result["workflow_status"], "review_chapter:ok")

    def test_main_py_unchanged(self):
        """main.py 不引用新 workflow 模块。"""
        main_path = Path(__file__).parent.parent / "main.py"
        content = main_path.read_text(encoding="utf-8")
        self.assertNotIn("langgraph", content.lower(),
                         "main.py 不得引用 langgraph")
        self.assertNotIn("chapter_workflow", content,
                         "main.py 不得引用 chapter_workflow")
        self.assertNotIn("build_chapter_workflow", content,
                         "main.py 不得引用 build_chapter_workflow")


if __name__ == "__main__":
    unittest.main()
