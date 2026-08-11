"""E07.7 Chroma index for Atomic Facts only."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from src.storage.document_formats import AtomicFact
from src.storage.embedding_config import load_embedding_runtime


COLLECTION_NAME = "atomic_facts_v2"
LEGACY_COLLECTION_NAME = "chapter_chunks"
SOURCE_TYPE = "atomic_fact"
DEFAULT_BRANCH_ID = "main"


@dataclass
class FactSearchResult:
    fact_id: str = ""
    chapter_index: int = 0
    fact_type: str = ""
    entities: str = ""
    paragraph_start: int = 0
    paragraph_end: int = 0
    source_ranges: list[dict[str, int]] = field(default_factory=list)
    canonical_hash: str = ""
    source_path: str = ""
    digest_path: str = ""
    distance: float = 0.0
    text: str = ""

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "chapter_index": self.chapter_index,
            "fact_type": self.fact_type,
            "entities": self.entities,
            "paragraph_start": self.paragraph_start,
            "paragraph_end": self.paragraph_end,
            "source_ranges": self.source_ranges,
            "canonical_hash": self.canonical_hash,
            "source_path": self.source_path,
            "digest_path": self.digest_path,
            "distance": self.distance,
            "text": self.text,
        }


class AtomicFactStore:
    """Lazy, version-isolated Chroma collection containing only Fact Text."""

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

    @property
    def is_initialized(self) -> bool:
        return self._client is not None

    def _ensure_collection(self, novel_id: str) -> chromadb.Collection:
        if self._collection is not None:  # explicit test/compatibility injection
            return self._collection
        self._runtime(novel_id)
        if novel_id not in self._collections:
            self._collections[novel_id] = self.client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"schema": "atomic-fact-v2"},
            )
        return self._collections[novel_id]

    @staticmethod
    def _chapter_where(novel_id: str, branch_id: str, chapter_index: int) -> dict:
        return {"$and": [
            {"novel_id": {"$eq": novel_id}},
            {"branch_id": {"$eq": branch_id}},
            {"chapter_index": {"$eq": chapter_index}},
        ]}

    @staticmethod
    def _branch_where(novel_id: str, branch_id: str) -> dict:
        return {"$and": [
            {"novel_id": {"$eq": novel_id}},
            {"branch_id": {"$eq": branch_id}},
        ]}

    def index_facts(
        self,
        novel_id: str,
        branch_id: str,
        chapter_index: int,
        facts: list[AtomicFact],
        source_path: str,
        digest_path: str,
        canonical_hash: str = "",
    ) -> int:
        """Replace one chapter's facts; documents are Fact Text, never prose."""
        coll = self._ensure_collection(novel_id)
        usable = [fact for fact in facts if fact.fact_text.strip()]
        if not usable:
            existing = coll.get(where=self._chapter_where(
                novel_id, branch_id, chapter_index))
            if existing and existing.get("ids"):
                coll.delete(ids=existing["ids"])
            return 0
        ids = []
        documents = []
        metadatas = []
        for sequence, fact in enumerate(usable, 1):
            fact_id = f"FACT-{chapter_index:04d}-{sequence:03d}"
            ids.append(f"{novel_id}_{branch_id}_{fact_id}")
            documents.append(fact.fact_text.strip())
            metadata = {
                "novel_id": novel_id,
                "branch_id": branch_id,
                "source_type": SOURCE_TYPE,
                "fact_id": fact_id,
                "chapter_index": chapter_index,
                "source_ranges": json.dumps(
                    fact.source_ranges, ensure_ascii=False, separators=(",", ":")
                ),
                "canonical_hash": canonical_hash,
                "source_path": source_path,
                "digest_path": digest_path,
            }
            if not fact.source_ranges:
                # Optional read compatibility for pre-protocol Markdown digests.
                metadata.update({
                    "fact_type": fact.fact_type or "event",
                    "entities": ", ".join(fact.entities),
                    "paragraph_start": int(fact.paragraph_start or 0),
                    "paragraph_end": int(fact.paragraph_end or 0),
                })
            metadatas.append(metadata)
        add_kwargs = {
            "ids": ids, "documents": documents, "metadatas": metadatas,
        }
        runtime = self._runtime(novel_id)
        if runtime.is_api:
            add_kwargs["embeddings"] = runtime.embed(documents)
        existing = coll.get(where=self._chapter_where(
            novel_id, branch_id, chapter_index))
        if existing and existing.get("ids"):
            coll.delete(ids=existing["ids"])
        coll.add(**add_kwargs)
        return len(usable)

    def search(
        self,
        novel_id: str,
        branch_id: str,
        query: str,
        chapter_index: int,
        top_k: int,
    ) -> list[FactSearchResult]:
        coll = self._ensure_collection(novel_id)
        where = {"$and": [
            {"novel_id": {"$eq": novel_id}},
            {"branch_id": {"$eq": branch_id}},
            {"chapter_index": {"$lt": chapter_index}},
            {"source_type": {"$eq": SOURCE_TYPE}},
        ]}
        query_kwargs = {
            "where": where, "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        runtime = self._runtime(novel_id)
        if runtime.is_api:
            query_kwargs["query_embeddings"] = runtime.embed([query])
        else:
            query_kwargs["query_texts"] = [query]
        raw = coll.query(**query_kwargs)
        parsed = []
        ids = raw.get("ids", [[]])[0] if raw else []
        docs = raw.get("documents", [[]])[0] if raw and raw.get("documents") else []
        metas = raw.get("metadatas", [[]])[0] if raw and raw.get("metadatas") else []
        distances = raw.get("distances", [[]])[0] if raw and raw.get("distances") else []
        for index, _ in enumerate(ids):
            meta = metas[index] if index < len(metas) else {}
            raw_ranges = meta.get("source_ranges", "")
            try:
                source_ranges = (
                    json.loads(raw_ranges) if isinstance(raw_ranges, str)
                    else list(raw_ranges or [])
                )
                if not isinstance(source_ranges, list):
                    source_ranges = []
            except (TypeError, ValueError, json.JSONDecodeError):
                source_ranges = []
            parsed.append(FactSearchResult(
                fact_id=str(meta.get("fact_id", "")),
                chapter_index=int(meta.get("chapter_index", 0)),
                fact_type=str(meta.get("fact_type", "")),
                entities=str(meta.get("entities", "")),
                paragraph_start=int(meta.get("paragraph_start", 0)),
                paragraph_end=int(meta.get("paragraph_end", 0)),
                source_ranges=source_ranges,
                canonical_hash=str(meta.get("canonical_hash", "")),
                source_path=str(meta.get("source_path", "")),
                digest_path=str(meta.get("digest_path", "")),
                distance=float(distances[index]) if index < len(distances) else 1.0,
                text=str(docs[index]) if index < len(docs) else "",
            ))
        return parsed

    def quarantine_fact(self, novel_id: str, branch_id: str, fact_id: str) -> bool:
        """Immediately remove a confirmed-bad fact from the active vector index."""
        coll = self._ensure_collection(novel_id)
        existing = coll.get(where={"$and": [
            {"novel_id": {"$eq": novel_id}},
            {"branch_id": {"$eq": branch_id}},
            {"fact_id": {"$eq": fact_id}},
        ]})
        ids = existing.get("ids", []) if existing else []
        if ids:
            coll.delete(ids=ids)
            return True
        return False

    def rebuild_branch(self, novel_id: str, branch_id: str) -> bool:
        self._runtime(novel_id)
        coll = self._ensure_collection(novel_id)
        existing = coll.get(where=self._branch_where(novel_id, branch_id))
        if existing and existing.get("ids"):
            coll.delete(ids=existing["ids"])
        return True

    def purge_legacy_branch(self, novel_id: str, branch_id: str) -> int:
        """Best-effort cleanup; collection isolation already prevents mixing."""
        try:
            legacy = self.client.get_collection(LEGACY_COLLECTION_NAME)
        except Exception:
            return 0
        existing = legacy.get(where=self._branch_where(novel_id, branch_id))
        ids = existing.get("ids", []) if existing else []
        if ids:
            legacy.delete(ids=ids)
        return len(ids)
