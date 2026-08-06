"""Rebuildable Chroma index for author-owned supplemental knowledge."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from src.workflows.context_governance import content_hash
from src.storage.embedding_config import load_embedding_runtime

COLLECTION_NAME = "author_knowledge_v1"
SOURCE_TYPE = "author_knowledge"
DEFAULT_BRANCH_ID = "main"


@dataclass
class AuthorKnowledgeResult:
    entry_id: str = ""
    heading: str = ""
    text: str = ""
    source_path: str = "tracking/author_rag.md"
    source_hash: str = ""
    distance: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class AuthorRAGStore:
    """Index only author_rag.md; Markdown remains the authority."""

    def __init__(self, persist_dir: Path):
        self._persist_dir = persist_dir
        self._client: Optional[chromadb.PersistentClient] = None
        self._collection: Optional[chromadb.Collection] = None
        self._collections: dict[str, chromadb.Collection] = {}
        self._runtimes = {}

    def _runtime(self, novel_id: str):
        if novel_id not in self._runtimes:
            self._runtimes[novel_id] = load_embedding_runtime(
                self._persist_dir.parent, novel_id
            )
        return self._runtimes[novel_id]

    @property
    def client(self) -> chromadb.PersistentClient:
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=str(self._persist_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        return self._client

    def _ensure_collection(self, novel_id: str) -> chromadb.Collection:
        if self._collection is not None:
            return self._collection
        self._runtime(novel_id)
        if novel_id not in self._collections:
            self._collections[novel_id] = self.client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"schema": "author-knowledge-v1"},
            )
        return self._collections[novel_id]

    @staticmethod
    def _where(novel_id: str, branch_id: str) -> dict:
        return {"$and": [
            {"novel_id": {"$eq": novel_id}},
            {"branch_id": {"$eq": branch_id}},
        ]}

    @staticmethod
    def parse_entries(text: str) -> list[tuple[str, str, str]]:
        """Return stable entry id, heading, and complete entry text."""
        entries = []
        blocks = re.split(r"(?=^##+\s+)", text, flags=re.MULTILINE)
        sequence = 0
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            sequence += 1
            heading_match = re.match(r"^##+\s+(.+)$", block, re.MULTILINE)
            heading = heading_match.group(1).strip() if heading_match else f"Entry {sequence}"
            entries.append((f"AUTHOR-{sequence:04d}", heading, block))
        return entries

    def indexed_hash(self, novel_id: str, branch_id: str) -> str:
        raw = self._ensure_collection(novel_id).get(
            where=self._where(novel_id, branch_id)
        )
        metas = raw.get("metadatas", []) if raw else []
        hashes = {str(meta.get("source_hash", "")) for meta in metas if meta}
        return hashes.pop() if len(hashes) == 1 else ""

    def rebuild(
        self, novel_id: str, branch_id: str, markdown: str,
        source_path: str = "tracking/author_rag.md",
    ) -> int:
        coll = self._ensure_collection(novel_id)
        existing = coll.get(where=self._where(novel_id, branch_id))
        ids = existing.get("ids", []) if existing else []
        if ids:
            coll.delete(ids=ids)
        entries = self.parse_entries(markdown)
        if not entries:
            return 0
        digest = content_hash(markdown)
        documents = [body for _, _, body in entries]
        add_kwargs = dict(
            ids=[f"{novel_id}_{branch_id}_{entry_id}" for entry_id, _, _ in entries],
            documents=documents,
            metadatas=[{
                "novel_id": novel_id,
                "branch_id": branch_id,
                "source_type": SOURCE_TYPE,
                "entry_id": entry_id,
                "heading": heading,
                "source_path": source_path,
                "source_hash": digest,
            } for entry_id, heading, _ in entries],
        )
        runtime = self._runtime(novel_id)
        if runtime.is_api:
            add_kwargs["embeddings"] = runtime.embed(documents)
        coll.add(**add_kwargs)
        return len(entries)

    def ensure_synced(
        self, novel_id: str, branch_id: str, markdown: str,
    ) -> int:
        expected = content_hash(markdown)
        if self.indexed_hash(novel_id, branch_id) == expected:
            return 0
        count = self.rebuild(novel_id, branch_id, markdown)
        if markdown.strip() and count <= 0:
            raise RuntimeError("Author RAG rebuild produced no entries")
        if markdown.strip() and self.indexed_hash(novel_id, branch_id) != expected:
            raise RuntimeError("Author RAG hash mismatch after rebuild")
        return count

    def search(
        self, novel_id: str, branch_id: str, query: str, top_k: int,
    ) -> list[AuthorKnowledgeResult]:
        coll = self._ensure_collection(novel_id)
        query_kwargs = {
            "where": self._where(novel_id, branch_id),
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        runtime = self._runtime(novel_id)
        if runtime.is_api:
            query_kwargs["query_embeddings"] = runtime.embed([query])
        else:
            query_kwargs["query_texts"] = [query]
        raw = coll.query(**query_kwargs)
        parsed = []
        docs = raw.get("documents", [[]])[0] if raw and raw.get("documents") else []
        metas = raw.get("metadatas", [[]])[0] if raw and raw.get("metadatas") else []
        distances = raw.get("distances", [[]])[0] if raw and raw.get("distances") else []
        for index, doc in enumerate(docs):
            meta = metas[index] if index < len(metas) else {}
            parsed.append(AuthorKnowledgeResult(
                entry_id=str(meta.get("entry_id", "")),
                heading=str(meta.get("heading", "")),
                text=str(doc),
                source_path=str(meta.get("source_path", "tracking/author_rag.md")),
                source_hash=str(meta.get("source_hash", "")),
                distance=float(distances[index]) if index < len(distances) else 1.0,
            ))
        return parsed
