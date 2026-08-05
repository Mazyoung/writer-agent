"""Planning retrieval service for the LangGraph chapter workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.agents.author.chapter_planner import ChapterPlanner
from src.config.settings import get_settings
from src.storage.chroma_store import (
    ChromaStore,
    DEFAULT_BRANCH_ID,
    RetrievalResult,
    RetrievalTrace,
)
from src.storage.file_store import FileStore


@dataclass
class RetrievalOutcome:
    """Observable result of one planning retrieval attempt."""

    evidence: str = ""
    trace: RetrievalTrace = field(default_factory=RetrievalTrace)
    trace_path: str = ""
    warnings: list[str] = field(default_factory=list)


class ChapterRetrievalService:
    """Own deterministic query, Chroma search, evidence, and trace lifecycle."""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.settings = settings
        self.fs = FileStore(novel_id, settings.data_dir)
        self.chroma = ChromaStore(settings.data_dir / "chroma_db")
        self.planner = ChapterPlanner(novel_id)

    def retrieve(
        self,
        chapter_index: int,
        chapter_outline: str = "",
        extra_instructions: str = "",
        chapter_intent: str = "",
    ) -> RetrievalOutcome:
        branch_id = DEFAULT_BRANCH_ID
        trace = RetrievalTrace(
            chapter_index=chapter_index,
            branch_id=branch_id,
            query="",
            top_k=self.settings.rag_top_k,
            filters={
                "novel_id": self.novel_id,
                "branch_id": branch_id,
                "chapter_index <": chapter_index,
                "source_type": "chapter",
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
            outcome.evidence = self._format_evidence(trace.results)
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
    def _format_evidence(results: list[RetrievalResult]) -> str:
        if not results:
            return ""

        lines = [
            f"（从 {len(results)} 个历史章节片段中检索到以下相关内容，距离越近越相关）\n"
        ]
        for index, result in enumerate(results, 1):
            lines.append(
                f"**[证据{index}]** 第{result.chapter_index}章 "
                f"chunk-{result.chunk_index} "
                f"(distance={result.distance:.4f}):"
            )
            lines.append(f"> {result.text[:600]}")
            lines.append("")
        return "\n".join(lines)

    def _save_trace(self, trace: RetrievalTrace) -> Path:
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
