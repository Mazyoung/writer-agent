"""Standalone chapter planning service outside the execution workflow."""

from src.agents.author.chapter_planner import ChapterPlanner
from src.config.settings import get_settings
from src.storage.document_formats import ChapterPlan
from src.storage.file_store import FileStore
from src.workflows.retrieval_service import ChapterRetrievalService


class ChapterPlanningService:
    """Generate a canonical Chapter Plan with historical RAG evidence."""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.file_store = FileStore(novel_id, settings.data_dir)
        migrated = self.file_store.migrate_legacy_canonical_if_needed()
        if migrated:
            print(f"  [migration] canonical copies created: {list(migrated.keys())}")
        self.chapter_planner = ChapterPlanner(novel_id)
        self.retrieval = ChapterRetrievalService(novel_id)

    def plan_chapter(
        self,
        chapter_index: int,
        chapter_outline: str = "",
        extra_instructions: str = "",
    ) -> ChapterPlan:
        """Generate a Chapter Plan while preserving retrieval observability."""
        print(f"\n{'=' * 60}")
        print(f"第 {chapter_index} 章规划")
        print(f"{'=' * 60}\n")

        print("  [RAG] 检索历史证据...")
        retrieval = self.retrieval.retrieve(
            chapter_index,
            chapter_outline,
            extra_instructions,
        )
        if retrieval.evidence:
            print(f"  [RAG] 检索到 {len(retrieval.trace.results)} 个相关历史片段")
        else:
            print("  [RAG] 未检索到相关历史片段（或数据库为空）")
        for warning in retrieval.warnings:
            print(f"  [RAG WARNING] {warning}")
        if retrieval.trace_path:
            print(f"  [RAG] 检索追踪已保存: {retrieval.trace_path}")

        print("  [ChapterPlanner] 加载追踪文档 + 生成规划...")
        plan = self.chapter_planner.plan_chapter(
            chapter_index,
            chapter_outline,
            extra_instructions,
            rag_evidence=retrieval.evidence,
        )

        print(f"  Part A: {len(plan.scenes)} 个场景")
        context = plan.context
        has_relationships = bool(
            context.character_relations and context.character_relations != "暂无"
        )
        has_items = bool(context.items_tracking and context.items_tracking != "暂无")
        has_cultivation = bool(
            context.cultivation_status and context.cultivation_status != "暂无"
        )
        print(
            "  Part B: "
            f"角色关系{'Y' if has_relationships else 'N'} "
            f"物品{'Y' if has_items else 'N'} "
            f"修炼{'Y' if has_cultivation else 'N'}"
        )
        print(f"  已保存: outlines/chapter_plan_ch{chapter_index:04d}.md")
        return plan
