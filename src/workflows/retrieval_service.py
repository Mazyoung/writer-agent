"""Planning retrieval service for the LangGraph chapter workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.agents.author.chapter_planner import ChapterPlanner
from src.config.settings import get_settings
from src.storage.atomic_fact_store import (
    AtomicFactStore,
    DEFAULT_BRANCH_ID,
    FactSearchResult,
)
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


class ChapterRetrievalService:
    """Own deterministic query, Chroma search, evidence, and trace lifecycle."""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.settings = settings
        self.fs = FileStore(novel_id, settings.data_dir)
        self.chroma = AtomicFactStore(settings.data_dir / "chroma_db")
        self.planner = ChapterPlanner(novel_id)

    def retrieve(
        self,
        chapter_index: int,
        chapter_outline: str = "",
        extra_instructions: str = "",
        chapter_intent: str = "",
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
                chapter_intent)
            trace.results = self.chroma.search(
                novel_id=self.novel_id,
                branch_id=branch_id,
                query=trace.query,
                chapter_index=chapter_index,
                top_k=trace.top_k,
            )
            excerpts = self._expand_sources(trace.results)
            outcome.fact_candidates = [result.to_dict() for result in trace.results]
            outcome.source_excerpts = [excerpt.to_dict() for excerpt in excerpts]
            outcome.evidence = self._format_evidence(trace.results, excerpts)
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
    ) -> str:
        parts: list[str] = []

        volume_plan = self.fs.load_tracking_doc("volume_plan") or ""
        if volume_plan:
            volume_context = self.planner._extract_chapter_from_volume(
                volume_plan, chapter_index)
            if volume_context:
                parts.append(volume_context[:1000])

        if chapter_outline:
            parts.append(chapter_outline[:500])

        relationships = self.fs.load_tracking_doc("character_relationships") or ""
        character_names: set[str] = set()
        for match in re.finditer(r"\*\*(.+?)\*\*", relationships):
            name = match.group(1).strip()
            if 2 <= len(name) <= 6 and not any(
                keyword in name
                for keyword in [
                    "状态", "关系", "类型", "态度", "互动", "变更", "物品", "体系", "检查",
                ]
            ):
                character_names.add(name)
        if character_names:
            parts.append("角色: " + ", ".join(sorted(character_names)[:10]))

        items = self.fs.load_tracking_doc("items_equipment") or ""
        item_names: set[str] = set()
        for match in re.finditer(r"\|\s*(.+?)\s*\|", items):
            name = match.group(1).strip()
            if name and 2 <= len(name) <= 10 and not any(
                keyword in name
                for keyword in [
                    "物品", "来源", "获得", "属性", "状态", "备注",
                    "拥有者", "首次出现", "已知属性", "---",
                ]
            ):
                item_names.add(name)
        if item_names:
            parts.append("物品: " + ", ".join(sorted(item_names)[:10]))

        if chapter_intent:
            parts.append(chapter_intent[:500])
        if extra_instructions:
            parts.append(extra_instructions[:500])

        return " ".join(parts) if parts else f"第{chapter_index}章 剧情"

    @staticmethod
    def _format_evidence(
        results: list[FactSearchResult], excerpts: list[SourceExcerpt]
    ) -> str:
        if not results:
            return ""
        lines = ["## Candidate Atomic Facts"]
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
        return "\n".join(lines)

    @staticmethod
    def _split_paragraphs(text: str) -> list[str]:
        return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]

    def _resolve_source_path(self, result: FactSearchResult) -> Path | None:
        if result.source_path:
            candidate = (self.fs.root / result.source_path).resolve()
            if candidate.is_relative_to(self.fs.root.resolve()) and candidate.is_file():
                return candidate
        files = sorted(
            (self.fs.root / "chapters").glob(
                f"chapter_{result.chapter_index:04d}_styled_*.md"
            ),
            reverse=True,
        )
        return files[0] if files else None

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
