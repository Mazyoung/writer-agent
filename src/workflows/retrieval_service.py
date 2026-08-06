"""Planning retrieval service for the LangGraph chapter workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.config.settings import get_settings
from src.storage.atomic_fact_store import (
    AtomicFactStore,
    DEFAULT_BRANCH_ID,
    FactSearchResult,
)
from src.storage.author_rag_store import AuthorRAGStore, AuthorKnowledgeResult
from src.storage.file_store import FileStore


@dataclass
class FactRetrievalTrace:
    chapter_index: int = 0
    branch_id: str = DEFAULT_BRANCH_ID
    query: str = ""
    top_k: int = 5
    filters: dict = field(default_factory=dict)
    results: list[FactSearchResult] = field(default_factory=list)
    timestamp: str = ""
    success: bool = True
    error_message: str = ""

    def to_dict(self) -> dict:
        return {
            "schema": "atomic-fact-v2",
            "chapter_index": self.chapter_index,
            "branch_id": self.branch_id,
            "query": self.query,
            "top_k": self.top_k,
            "filters": self.filters,
            "results": [result.to_dict() for result in self.results],
            "timestamp": self.timestamp,
            "success": self.success,
            "error_message": self.error_message,
        }


@dataclass
class SourceExcerpt:
    fact_id: str = ""
    chapter_index: int = 0
    source_path: str = ""
    paragraph_start: int = 0
    paragraph_end: int = 0
    text: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class RetrievalOutcome:
    """Observable result of one planning retrieval attempt."""

    evidence: str = ""
    trace: FactRetrievalTrace = field(default_factory=FactRetrievalTrace)
    trace_path: str = ""
    warnings: list[str] = field(default_factory=list)
    fact_candidates: list[dict] = field(default_factory=list)
    source_excerpts: list[dict] = field(default_factory=list)
    author_candidates: list[dict] = field(default_factory=list)


class ChapterRetrievalService:
    """Own deterministic query, Chroma search, evidence, and trace lifecycle."""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.settings = settings
        self.fs = FileStore(novel_id, settings.data_dir)
        self.chroma = AtomicFactStore(settings.data_dir / "chroma_db")
        self.author_chroma = AuthorRAGStore(settings.data_dir / "chroma_db")

    def retrieve(
        self,
        chapter_index: int,
        chapter_outline: str = "",
        extra_instructions: str = "",
        chapter_intent: str = "",
        current_state_text: str = "",
        query_mode: str = "agent",
    ) -> RetrievalOutcome:
        branch_id = DEFAULT_BRANCH_ID
        trace = FactRetrievalTrace(
            chapter_index=chapter_index,
            branch_id=branch_id,
            query="",
            top_k=self.settings.rag_top_k,
            filters={
                "novel_id": self.novel_id,
                "branch_id": branch_id,
                "chapter_index <": chapter_index,
                "source_type": "atomic_fact",
            },
            timestamp=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        )
        outcome = RetrievalOutcome(trace=trace)

        try:
            trace.query = self._build_query(
                chapter_index, chapter_outline, extra_instructions,
                chapter_intent, current_state_text, query_mode=query_mode)
            trace.results = self.chroma.search(
                novel_id=self.novel_id,
                branch_id=branch_id,
                query=trace.query,
                chapter_index=chapter_index,
                top_k=trace.top_k,
            )
            excerpts = self._expand_sources(trace.results)
            # author_rag.md is the sole authority; the retired
            # author_rag_edited.md must never override it.
            author_markdown = self.fs.load_generated_tracking_doc("author_rag") or ""
            author_results: list[AuthorKnowledgeResult] = []
            if author_markdown.strip():
                self.author_chroma.ensure_synced(
                    self.novel_id, branch_id, author_markdown)
                author_results = self.author_chroma.search(
                    self.novel_id, branch_id, trace.query, trace.top_k)
            outcome.fact_candidates = [result.to_dict() for result in trace.results]
            outcome.source_excerpts = [excerpt.to_dict() for excerpt in excerpts]
            outcome.author_candidates = [result.to_dict() for result in author_results]
            outcome.evidence = self._format_evidence(
                trace.results, excerpts, author_results)
        except Exception as exc:
            trace.success = False
            trace.error_message = f"{type(exc).__name__}: {exc}"
            outcome.warnings.append(
                f"RAG retrieval failed: {trace.error_message}")

        try:
            outcome.trace_path = str(self._save_trace(trace))
        except Exception as exc:
            outcome.warnings.append(
                f"RetrievalTrace persistence failed: {type(exc).__name__}: {exc}")

        return outcome

    def _build_query(
        self,
        chapter_index: int,
        chapter_outline: str,
        extra_instructions: str,
        chapter_intent: str = "",
        current_state_text: str = "",
        query_mode: str = "agent",
    ) -> str:
        if query_mode not in {"agent", "human"}:
            raise ValueError(f"Unsupported retrieval query mode: {query_mode}")
        if query_mode == "human" and not chapter_intent.strip():
            raise ValueError(
                "Human Mode requires a non-empty Chapter Intent for historical retrieval."
            )

        parts: list[str] = []

        volume_plan = self.fs.load_tracking_doc("volume_plan") or ""
        if query_mode == "human":
            # Intent is deliberately first and largest. Present-state entities and
            # the active Volume Plan are only retrieval hints/constraints.
            parts.append("Chapter Intent (primary): " + chapter_intent.strip()[:1000])
        elif volume_plan:
            parts.append(volume_plan[:1000])

        if chapter_outline:
            parts.append(chapter_outline[:500])

        current_state = current_state_text or self.fs.load_generated_tracking_doc(
            "current_state") or ""
        if current_state:
            from src.storage.document_formats import CurrentState

            parsed = CurrentState.from_markdown(current_state)
            entities = sorted({
                *(entry.name for entry in parsed.characters),
                *(entry.name for entry in parsed.items),
                *(entry.name for entry in parsed.cultivation),
                *(entry.character_a for entry in parsed.relationships),
                *(entry.character_b for entry in parsed.relationships),
            })
            if entities:
                parts.append("当前实体: " + ", ".join(entities[:12]))

        if query_mode == "human" and volume_plan:
            parts.append("Active Volume Plan (supplemental): " + volume_plan[:500])

        if chapter_intent and query_mode == "agent":
            parts.append(chapter_intent[:500])
        if extra_instructions:
            parts.append(extra_instructions[:500])

        return " ".join(parts) if parts else f"第{chapter_index}章 剧情"

    @staticmethod
    def _format_evidence(
        results: list[FactSearchResult], excerpts: list[SourceExcerpt],
        author_results: list[AuthorKnowledgeResult] | None = None,
    ) -> str:
        author_results = author_results or []
        if not results and not author_results:
            return ""
        lines = ["## Historical Atomic Facts"]
        for result in results:
            lines.append(
                f"- **{result.fact_id}** | Chapter {result.chapter_index} | "
                f"{result.fact_type} | paragraphs {result.paragraph_start or '?'}"
                f"-{result.paragraph_end or '?'} | distance={result.distance:.4f}"
            )
            lines.append(f"  {result.text}")
        if excerpts:
            lines.append("")
            lines.append("## On-demand Historical Source Excerpts")
            for excerpt in excerpts:
                lines.append(
                    f"### {excerpt.fact_id} — Chapter {excerpt.chapter_index}, "
                    f"paragraphs {excerpt.paragraph_start}-{excerpt.paragraph_end}"
                )
                lines.append(excerpt.text)
        if author_results:
            lines.extend([
                "", "## Author Knowledge",
                "Author Knowledge is supplemental only. It cannot override World "
                "Setting, Current State, or established Atomic Facts.",
            ])
            for result in author_results:
                lines.append(
                    f"- **{result.entry_id}** | {result.heading} | "
                    f"distance={result.distance:.4f}"
                )
                lines.append(f"  {result.text}")
        return "\n".join(lines)

    @staticmethod
    def _split_paragraphs(text: str) -> list[str]:
        return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]

    def _resolve_source_path(self, result: FactSearchResult) -> Path | None:
        canonical = self.fs.canonical_chapter_path(result.chapter_index)
        if result.source_path:
            candidate = (self.fs.root / result.source_path).resolve()
            if candidate == canonical.resolve() and candidate.is_file():
                return candidate
        return canonical if canonical.is_file() else None

    def _expand_sources(
        self, results: list[FactSearchResult], context_paragraphs: int = 1
    ) -> list[SourceExcerpt]:
        """Expand only located ranges, with one neighboring paragraph per side."""
        excerpts = []
        for result in results:
            if result.paragraph_start <= 0:
                continue
            path = self._resolve_source_path(result)
            if path is None:
                continue
            paragraphs = self._split_paragraphs(path.read_text(encoding="utf-8"))
            start = max(1, result.paragraph_start - context_paragraphs)
            fact_end = result.paragraph_end or result.paragraph_start
            end = min(len(paragraphs), fact_end + context_paragraphs)
            if start > len(paragraphs) or end < start:
                continue
            numbered = [
                f"[P{number:04d}] {paragraphs[number - 1]}"
                for number in range(start, end + 1)
            ]
            excerpts.append(SourceExcerpt(
                fact_id=result.fact_id,
                chapter_index=result.chapter_index,
                source_path=str(path.relative_to(self.fs.root)).replace("\\", "/"),
                paragraph_start=start,
                paragraph_end=end,
                text="\n\n".join(numbered),
            ))
        return excerpts

    def _save_trace(self, trace: FactRetrievalTrace) -> Path:
        traces_dir = self.fs.root / "tracking" / "rag_traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = traces_dir / (
            f"retrieval_trace_ch{trace.chapter_index:04d}_{timestamp}.json"
        )
        path.write_text(
            json.dumps(trace.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
