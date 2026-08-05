"""Explicit Atomic Fact index maintenance operations (E07.7)."""

import re
from pathlib import Path

from src.config.settings import get_settings
from src.storage.atomic_fact_store import AtomicFactStore, DEFAULT_BRANCH_ID
from src.storage.document_formats import FactDigest
from src.storage.file_store import FileStore


class RAGMaintenanceService:
    """Backfill/rebuild Chroma exclusively from Markdown Fact Digests."""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.settings = settings
        self.file_store = FileStore(novel_id, settings.data_dir)
        self._chroma = None

    @property
    def chroma(self) -> AtomicFactStore:
        if self._chroma is None:
            self._chroma = AtomicFactStore(self.settings.data_dir / "chroma_db")
        return self._chroma

    def _latest_digests(self) -> dict[int, Path]:
        latest: dict[int, tuple[str, Path]] = {}
        for path in sorted(
            (self.file_store.root / "states").glob("fact_digest_ch*_*.md")
        ):
            match = re.match(r"fact_digest_ch(\d{4})_(\d{8}_\d{6})", path.name)
            if not match:
                continue
            chapter_index = int(match.group(1))
            timestamp = match.group(2)
            if chapter_index not in latest or timestamp > latest[chapter_index][0]:
                latest[chapter_index] = (timestamp, path)
        return {chapter: value[1] for chapter, value in latest.items()}

    def _latest_styled_path(self, chapter_index: int) -> str:
        files = sorted(
            (self.file_store.root / "chapters").glob(
                f"chapter_{chapter_index:04d}_styled_*.md"),
            reverse=True,
        )
        return f"chapters/{files[0].name}" if files else ""

    def run(self, rebuild: bool = False) -> dict:
        branch_id = DEFAULT_BRANCH_ID
        print(f"\n{'=' * 60}")
        print(f"Atomic Fact RAG {'重建' if rebuild else '补齐'}: {self.novel_id}")
        print(f"{'=' * 60}\n")

        legacy_removed = 0
        if rebuild:
            try:
                self.chroma.rebuild_branch(self.novel_id, branch_id)
                legacy_removed = self.chroma.purge_legacy_branch(
                    self.novel_id, branch_id)
            except Exception as exc:
                print(f"  [RAG ERROR] 索引清理失败，重建中止: {exc}")
                return {
                    "indexed_chapters": 0, "total_facts": 0,
                    "total_chunks": 0, "errors": 1,
                    "rebuild_aborted": True,
                }

        stats = {
            "indexed_chapters": 0,
            "total_facts": 0,
            "total_chunks": 0,
            "legacy_chunks_removed": legacy_removed,
            "legacy_index_isolated": True,
            "errors": 0,
        }
        for chapter_index, digest_path in sorted(self._latest_digests().items()):
            marker = (
                self.file_store.root / "states" /
                f"chapter_{chapter_index:04d}_completed"
            )
            if not marker.exists():
                continue
            try:
                digest = FactDigest.from_markdown(
                    digest_path.read_text(encoding="utf-8"))
                count = self.chroma.index_facts(
                    novel_id=self.novel_id,
                    branch_id=branch_id,
                    chapter_index=chapter_index,
                    facts=digest.atomic_facts,
                    source_path=self._latest_styled_path(chapter_index),
                    digest_path=f"states/{digest_path.name}",
                )
                stats["indexed_chapters"] += 1
                stats["total_facts"] += count
                print(f"  [RAG] 第{chapter_index}章: {count} Atomic Facts")
            except Exception as exc:
                stats["errors"] += 1
                print(f"  [RAG WARNING] 第{chapter_index}章索引失败: {exc}")

        print(
            f"\n  完成: {stats['indexed_chapters']} 章, "
            f"{stats['total_facts']} facts"
            + (f", {stats['errors']} 错误" if stats["errors"] else "")
        )
        return stats
