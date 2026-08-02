"""
ChromaStore — Lazy vector store for chapter chunks (E04 RAG MVP).

Only indexes finalized/styled historical chapter text.
Uses deterministic character chunking with stable IDs (no random UUID).
Supports filtered Top-K retrieval with future leakage prevention.
"""

from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings


# ── Configurable defaults ───────────────────────────────────

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100
DEFAULT_TOP_K = 5

COLLECTION_NAME = "chapter_chunks"
SOURCE_TYPE_CHAPTER = "chapter"
DEFAULT_BRANCH_ID = "main"


# ── Retrieval data structures ───────────────────────────────

@dataclass
class RetrievalResult:
    """Single chunk retrieval result."""
    doc_id: str = ""
    chapter_index: int = 0
    chunk_index: int = 0
    source_path: str = ""
    distance: float = 0.0
    text: str = ""


@dataclass
class RetrievalTrace:
    """Complete trace of one retrieval operation (E04 P0 #10)."""
    chapter_index: int = 0
    branch_id: str = DEFAULT_BRANCH_ID
    query: str = ""
    top_k: int = DEFAULT_TOP_K
    filters: dict = field(default_factory=dict)
    results: list[RetrievalResult] = field(default_factory=list)
    timestamp: str = ""
    success: bool = True
    error_message: str = ""

    def to_dict(self) -> dict:
        return {
            "chapter_index": self.chapter_index,
            "branch_id": self.branch_id,
            "query": self.query,
            "top_k": self.top_k,
            "filters": self.filters,
            "results": [
                {
                    "doc_id": r.doc_id,
                    "chapter_index": r.chapter_index,
                    "chunk_index": r.chunk_index,
                    "source_path": r.source_path,
                    "distance": r.distance,
                    "text": r.text,
                }
                for r in self.results
            ],
            "timestamp": self.timestamp,
            "success": self.success,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RetrievalTrace":
        trace = cls(
            chapter_index=d.get("chapter_index", 0),
            branch_id=d.get("branch_id", DEFAULT_BRANCH_ID),
            query=d.get("query", ""),
            top_k=d.get("top_k", DEFAULT_TOP_K),
            filters=d.get("filters", {}),
            timestamp=d.get("timestamp", ""),
            success=d.get("success", True),
            error_message=d.get("error_message", ""),
        )
        for r in d.get("results", []):
            trace.results.append(RetrievalResult(
                doc_id=r.get("doc_id", ""),
                chapter_index=r.get("chapter_index", 0),
                chunk_index=r.get("chunk_index", 0),
                source_path=r.get("source_path", ""),
                distance=r.get("distance", 0.0),
                text=r.get("text", ""),
            ))
        return trace


# ── Deterministic chunking ──────────────────────────────────

def chunk_text(text: str,
               chunk_size: int = DEFAULT_CHUNK_SIZE,
               chunk_overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[tuple[int, str]]:
    """Deterministic character chunking.

    Returns [(chunk_index, chunk_text), ...].
    Never returns empty chunks (whitespace-only is skipped).

    Args:
        text: The full chapter text to chunk.
        chunk_size: Characters per chunk.
        chunk_overlap: Overlapping characters between consecutive chunks.
    """
    if chunk_overlap >= chunk_size:
        chunk_overlap = max(0, chunk_size // 4)  # safety clamp
    chunks: list[tuple[int, str]] = []
    start = 0
    idx = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        if chunk.strip():  # no empty or whitespace-only chunks
            chunks.append((idx, chunk))
            idx += 1
        # advance — handle last segment (no overlap needed)
        if end >= len(text):
            break
        start = end - chunk_overlap
    return chunks


def make_chunk_id(novel_id: str, branch_id: str, chapter_index: int,
                  chunk_index: int) -> str:
    """Stable deterministic chunk ID — no random UUID (E04 P0 #3).

    Format: {novel_id}_{branch_id}_ch{NNNN}_chunk{NNN}
    """
    return f"{novel_id}_{branch_id}_ch{chapter_index:04d}_chunk{chunk_index:03d}"


# ── ChromaStore ─────────────────────────────────────────────

class ChromaStore:
    """Lazy vector store for chapter chunks.

    Client + collection are created on first access (E04 P0 #1: Lazy Chroma).
    Constructor is cheap — no disk I/O until index_chapter() or search().
    """

    def __init__(self, persist_dir: Path):
        self._persist_dir = persist_dir
        self._client: Optional[chromadb.PersistentClient] = None
        self._collection: Optional[chromadb.Collection] = None

    # ── Lazy init ──────────────────────────────────────

    @property
    def client(self) -> chromadb.PersistentClient:
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=str(self._persist_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def _ensure_collection(self) -> chromadb.Collection:
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=COLLECTION_NAME)
        return self._collection

    @property
    def is_initialized(self) -> bool:
        """Whether the ChromaDB client has been created (for lazy-init tests)."""
        return self._client is not None

    # ── Where-clause builders ──────────────────────────

    @staticmethod
    def _chapter_where(novel_id: str, branch_id: str,
                       chapter_index: int) -> dict:
        """Where clause to select all chunks of a specific chapter."""
        return {
            "$and": [
                {"novel_id": {"$eq": novel_id}},
                {"branch_id": {"$eq": branch_id}},
                {"chapter_index": {"$eq": chapter_index}},
            ]
        }

    @staticmethod
    def _branch_where(novel_id: str, branch_id: str) -> dict:
        """Where clause to select all chunks of a (novel, branch) pair."""
        return {
            "$and": [
                {"novel_id": {"$eq": novel_id}},
                {"branch_id": {"$eq": branch_id}},
            ]
        }

    @staticmethod
    def _retrieval_where(novel_id: str, branch_id: str,
                         chapter_index: int,
                         source_type: str = SOURCE_TYPE_CHAPTER) -> dict:
        """Where clause for retrieval: isolation + future leakage prevention."""
        return {
            "$and": [
                {"novel_id": {"$eq": novel_id}},
                {"branch_id": {"$eq": branch_id}},
                {"chapter_index": {"$lt": chapter_index}},
                {"source_type": {"$eq": source_type}},
            ]
        }

    # ── Indexing ───────────────────────────────────────

    def index_chapter(self, novel_id: str, branch_id: str,
                      chapter_index: int, content: str,
                      source_path: str = "",
                      chunk_size: int = DEFAULT_CHUNK_SIZE,
                      chunk_overlap: int = DEFAULT_CHUNK_OVERLAP) -> int:
        """Index a finalized chapter: remove stale chunks, chunk, embed, insert.

        Idempotent: repeated calls with same content produce same chunk count.
        Stale removal ensures chapter shrinking from 5→3 chunks leaves only 3.

        Returns:
            Number of chunks indexed.

        Raises:
            Exception on ChromaDB failures (caller handles graceful degradation).
        """
        if not content or not content.strip():
            return 0

        coll = self._ensure_collection()

        # 1. Delete existing chunks for this chapter (stale chunk removal — P0 #4)
        try:
            existing = coll.get(
                where=self._chapter_where(novel_id, branch_id, chapter_index))
            if existing and existing.get("ids"):
                coll.delete(ids=existing["ids"])
        except Exception as e:
            print(f"  [CHROMA WARNING] 清理第{chapter_index}章旧chunks失败: "
                  f"{type(e).__name__}: {e}")

        # 2. Chunk deterministically
        chunks = chunk_text(content, chunk_size, chunk_overlap)
        if not chunks:
            return 0

        # 3. Build batch with stable IDs + metadata (P0 #3, #5)
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []
        for cidx, ctext in chunks:
            doc_id = make_chunk_id(novel_id, branch_id, chapter_index, cidx)
            ids.append(doc_id)
            documents.append(ctext)
            metadatas.append({
                "novel_id": novel_id,
                "branch_id": branch_id,
                "chapter_index": chapter_index,       # int — enables $lt filter
                "chunk_index": cidx,
                "source_type": SOURCE_TYPE_CHAPTER,
                "source_path": source_path,
            })

        # 4. Insert (ChromaDB handles embedding automatically)
        coll.add(ids=ids, documents=documents, metadatas=metadatas)
        return len(chunks)

    # ── Retrieval ──────────────────────────────────────

    def search(self, novel_id: str, branch_id: str,
               query: str, chapter_index: int,
               top_k: int = DEFAULT_TOP_K,
               source_type: str = SOURCE_TYPE_CHAPTER) -> list[RetrievalResult]:
        """Retrieve top-k chunks with full isolation + future leakage prevention.

        Constraints (P0 #6, #7):
        - current novel only (novel A ≠ novel B)
        - active branch only
        - chapter_index < current chapter (future leakage prevention)
        - source_type == "chapter" (no settings/plans in corpus)

        Args:
            novel_id: Current novel.
            branch_id: Active branch.
            query: Deterministic retrieval query (no LLM rewrite).
            chapter_index: Current chapter being planned.
            top_k: Number of chunks to retrieve.
            source_type: Filter by source type (default "chapter").

        Returns:
            List of RetrievalResult sorted by distance (closest first).

        Raises:
            Exception on ChromaDB failures (caller handles graceful degradation).
        """
        coll = self._ensure_collection()
        where = self._retrieval_where(novel_id, branch_id, chapter_index,
                                      source_type)

        results = coll.query(
            query_texts=[query],
            where=where,
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        parsed: list[RetrievalResult] = []
        if results and results.get("ids") and results["ids"][0]:
            ids_list = results["ids"][0]
            docs_list = results.get("documents", [[]])[0] if results.get("documents") else []
            metas_list = results.get("metadatas", [[]])[0] if results.get("metadatas") else []
            dists_list = results.get("distances", [[]])[0] if results.get("distances") else []
            for i in range(len(ids_list)):
                meta = metas_list[i] if i < len(metas_list) else {}
                dist = dists_list[i] if i < len(dists_list) else 1.0
                doc_text = docs_list[i] if i < len(docs_list) else ""
                parsed.append(RetrievalResult(
                    doc_id=ids_list[i],
                    chapter_index=int(meta.get("chapter_index", 0)),
                    chunk_index=int(meta.get("chunk_index", 0)),
                    source_path=str(meta.get("source_path", "")),
                    distance=float(dist),
                    text=doc_text,
                ))
        return parsed

    # ── Rebuild ────────────────────────────────────────

    def rebuild_branch(self, novel_id: str, branch_id: str):
        """Delete all chunks for a (novel, branch) pair (used by --rebuild)."""
        coll = self._ensure_collection()
        try:
            existing = coll.get(where=self._branch_where(novel_id, branch_id))
            if existing and existing.get("ids"):
                coll.delete(ids=existing["ids"])
        except Exception as e:
            print(f"  [CHROMA WARNING] rebuild_branch 清理失败: "
                  f"{type(e).__name__}: {e}")
