"""Standalone chapter planning service outside the execution workflow."""

from src.agents.author.chapter_planner import ChapterPlanner
from src.agents.author.query_intent_builder import QueryIntentBuilder
from src.config.settings import get_settings
from src.core.text_windows import previous_chapter_end
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
    ) -> str:
        """Generate a Chapter Plan while preserving retrieval observability."""
        from src.workflows.chapter_progress import ensure_chapter_can_start

        ensure_chapter_can_start(self.novel_id, chapter_index)
        print(f"\n{'=' * 60}")
        print(f"第 {chapter_index} 章规划")
        print(f"{'=' * 60}\n")

        print("  [RAG] 检索历史证据...")
        from src.storage.current_state_store import CurrentStateStore
        from src.storage.sqlite_store import SQLiteStore

        sqlite = SQLiteStore(self.file_store.root / "state.db")
        try:
            current_state, _digest = CurrentStateStore(
                self.novel_id, self.file_store, sqlite
            ).ensure_raw_initialized()
        finally:
            sqlite.close()
        query_intent = QueryIntentBuilder(self.novel_id).build(
            volume_plan=self.file_store.load_tracking_doc("volume_plan") or "",
            recent_chapter_end=previous_chapter_end(
                self.file_store, chapter_index
            ),
            current_state=current_state,
        )
        retrieval = self.retrieval.retrieve(chapter_index, query_intent)
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
            query_intent=query_intent,
            current_state_text=current_state,
        )

        print("  Chapter Plan 已生成。")
        print(f"  已保存: outlines/chapter_plan_ch{chapter_index:04d}.md")
        return plan
