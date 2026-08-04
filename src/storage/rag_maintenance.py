"""Explicit RAG index maintenance operations."""

import re
from pathlib import Path

from src.config.settings import get_settings
from src.storage.chroma_store import ChromaStore, DEFAULT_BRANCH_ID
from src.storage.file_store import FileStore


class RAGMaintenanceService:
    """Backfill or rebuild derived chapter chunks from styled chapters."""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.settings = settings
        self.file_store = FileStore(novel_id, settings.data_dir)
        migrated = self.file_store.migrate_legacy_canonical_if_needed()
        if migrated:
            print(f"  [migration] canonical copies created: {list(migrated.keys())}")
        self._chroma = None

    @property
    def chroma(self) -> ChromaStore:
        if self._chroma is None:
            self._chroma = ChromaStore(self.settings.data_dir / "chroma_db")
        return self._chroma

    def run(self, rebuild: bool = False) -> dict:
        """Backfill or rebuild the main-branch index from finalized chapters."""
        branch_id = DEFAULT_BRANCH_ID
        print(f"\n{'=' * 60}")
        print(f"RAG 索引{'重建' if rebuild else '补齐'}: {self.novel_id}")
        print(f"{'=' * 60}\n")

        if rebuild:
            print("  [RAG] 清理当前分支索引...")
            if not self.chroma.rebuild_branch(self.novel_id, branch_id):
                print("\n  [RAG ERROR] 分支清理失败，重建中止。")
                print("  旧 chunks 可能仍然存在，继续重建会造成重复。")
                print("  请检查 ChromaDB 状态后重试。")
                return {
                    "indexed_chapters": 0,
                    "total_chunks": 0,
                    "errors": 1,
                    "rebuild_aborted": True,
                }

        styled_files = sorted(
            (self.file_store.root / "chapters").glob("chapter_*_styled_*.md")
        )
        latest: dict[int, tuple[str, Path]] = {}
        for path in styled_files:
            match = re.match(r"chapter_(\d{4})_styled_(\d{8}_\d{6})", path.name)
            if not match:
                continue
            chapter_index = int(match.group(1))
            timestamp = match.group(2)
            if chapter_index not in latest or timestamp > latest[chapter_index][0]:
                latest[chapter_index] = (timestamp, path)

        stats = {"indexed_chapters": 0, "total_chunks": 0, "errors": 0}
        for chapter_index in sorted(latest):
            path = latest[chapter_index][1]
            content = path.read_text(encoding="utf-8")
            try:
                count = self.chroma.index_chapter(
                    novel_id=self.novel_id,
                    branch_id=branch_id,
                    chapter_index=chapter_index,
                    content=content,
                    source_path=f"chapters/{path.name}",
                    chunk_size=self.settings.rag_chunk_size,
                    chunk_overlap=self.settings.rag_chunk_overlap,
                )
                stats["indexed_chapters"] += 1
                stats["total_chunks"] += count
                print(f"  [RAG] 第{chapter_index}章: {count} chunks")
            except Exception as exc:
                stats["errors"] += 1
                print(f"  [RAG WARNING] 第{chapter_index}章索引失败: {exc}")

        print(
            f"\n  完成: {stats['indexed_chapters']} 章, {stats['total_chunks']} chunks"
            + (f", {stats['errors']} 错误" if stats["errors"] else "")
        )
        return stats
