"""Focused E07.7 tests; no paid LLM or embedding calls."""

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src.agents.state_manager.state_manager import StateManager

from src.config.settings import get_settings
from src.storage.atomic_fact_store import AtomicFactStore, FactSearchResult
from src.storage.document_formats import AtomicFact, ChapterPlan, FactDigest
from src.storage.file_store import FileStore
from src.workflows.chapter_workflow import rag_index, save_chapter_sources
from src.storage.sqlite_store import SQLiteStore
from src.workflows.retrieval_service import ChapterRetrievalService


ATOMIC_MD = """# 第72章 Fact Digest

## Atomic Facts

### FACT-0072-001
- **Chapter**: 72
- **Fact Type**: character_state
- **Entities**: 林默
- **Paragraph Range**: 2-3
- **Fact Text**: 林默左臂受伤。

### FACT-0072-002
- **Chapter**: 72
- **Fact Type**: item
- **Entities**: 黑色芯片, 赵诚
- **Paragraph Range**: 5
- **Fact Text**: 黑色芯片被赵诚夺走。
"""


PLAN = """# 第73章规划：《追踪》

## 一、章节信息
- **章大纲**: 林默追踪芯片。
- **章节类型**: 延续型
- **总场景数**: 1

## 二、写作上下文包
### 角色关系图
暂无
### 物品/装备追踪
暂无
### 修炼/力量体系现状
暂无
### 关键伏笔节点
暂无
### 情感调色板
紧张
### 禁止清单
暂无
### 采用的历史事实
- FACT-0072-002 黑色芯片被赵诚夺走。
### 历史原文局部
- FACT-0072-002 第72章 P0004-P0006
### 未来规划约束
- 本章不能揭露幕后主使。

## 三、场景级写作计划
### 场景 1：追踪 [状态：待规划]
- **发生什么**：林默追踪赵诚。
- **本场景的戏剧功能**：推进主线
- **对话必须达成的信息增量**：确认方向
- **角色微时刻**：按住伤臂
- **涉及角色**：林默
- **情绪曲线**：紧张 → 坚定
- **字数预估**：800
- **与前后衔接**：承接芯片被夺
"""


class FakeCollection:
    def __init__(self):
        self.added = None

    def get(self, where):
        return {"ids": []}

    def add(self, **kwargs):
        self.added = kwargs


class E077Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = get_settings()
        self.original_data_dir = self.settings.data_dir
        self.settings.data_dir = Path(self.tmp.name)
        self.addCleanup(setattr, self.settings, "data_dir", self.original_data_dir)
        self.fs = FileStore("e077", self.settings.data_dir)


class TestAtomicFactFormat(E077Case):
    def test_markdown_round_trip_has_stable_ids_and_required_fields(self):
        digest = FactDigest.from_markdown(ATOMIC_MD)
        self.assertEqual([fact.fact_id for fact in digest.atomic_facts], [
            "FACT-0072-001", "FACT-0072-002"])
        rendered = digest.to_markdown()
        self.assertIn("- **Chapter**: 72", rendered)
        self.assertIn("- **Paragraph Range**: 2-3", rendered)
        self.assertIn("- **Fact Text**: 林默左臂受伤。", rendered)

    def test_legacy_digest_migrates_deterministically_without_json(self):
        legacy = """# 第3章 事实摘要
### 确定的事件
- 林默进入旧城。
- 赵诚关闭城门。
"""
        digest = FactDigest.from_markdown(legacy)
        self.assertEqual([fact.fact_id for fact in digest.atomic_facts], [
            "FACT-0003-001", "FACT-0003-002"])



    def test_review_fact_section_persists_atomic_markdown(self):
        atomic_section = ATOMIC_MD.split("## Atomic Facts", 1)[1]
        analysis = (
            "## 事实摘要\n" + atomic_section
            + "\n## 状态变更（State Delta）\n暂无\n"
            + "## 审阅决策\n- **决策**: PASS\n"
        )
        sqlite = SQLiteStore(self.fs.root / "state.db")
        try:
            digest = StateManager("e077", sqlite).extract_fact_digest_from_analysis(
                analysis, 72)
        finally:
            sqlite.close()
        self.assertEqual(len(digest.atomic_facts), 2)
        saved = sorted(
            (self.fs.root / "states").glob("fact_digest_ch0072_*.md"))
        self.assertEqual(len(saved), 1)
        persisted = saved[0].read_text(encoding="utf-8")
        self.assertIn("### FACT-0072-001", persisted)
        self.assertIn("- **Paragraph Range**: 2-3", persisted)
        self.assertNotIn("## 状态变更", persisted)
class TestFactOnlyChroma(E077Case):
    def test_embedding_documents_are_fact_text_not_chapter_chunks(self):
        store = AtomicFactStore(self.settings.data_dir / "chroma")
        fake = FakeCollection()
        store._collection = fake
        digest = FactDigest.from_markdown(ATOMIC_MD)
        count = store.index_facts(
            "e077", "main", 72, digest.atomic_facts,
            "chapters/chapter_0072_styled_x.md",
            "states/fact_digest_ch0072_x.md",
        )
        self.assertEqual(count, 2)
        self.assertEqual(fake.added["documents"], [
            "林默左臂受伤。", "黑色芯片被赵诚夺走。"])
        self.assertTrue(all(
            meta["source_type"] == "atomic_fact"
            for meta in fake.added["metadatas"]))


class TestFactSourceFunnel(E077Case):
    def test_fact_range_expands_only_local_paragraphs(self):
        path = self.fs.root / "chapters" / "chapter_0072.md"
        path.write_text("第一段。\n\n第二段。\n\n第三段命中。\n\n第四段。\n\n第五段。", encoding="utf-8")
        service = ChapterRetrievalService("e077")
        excerpts = service._expand_sources([FactSearchResult(
            fact_id="FACT-0072-001", chapter_index=72,
            paragraph_start=3, paragraph_end=3,
            source_path="chapters/chapter_0072.md",
            text="第三段命中。",
        )])
        self.assertEqual(len(excerpts), 1)
        self.assertEqual((excerpts[0].paragraph_start, excerpts[0].paragraph_end), (2, 4))
        self.assertIn("第三段命中。", excerpts[0].text)
        self.assertNotIn("第一段。", excerpts[0].text)
        self.assertNotIn("第五段。", excerpts[0].text)


class TestSourcesAndBoundaries(E077Case):
    def test_sources_report_records_only_adopted_fact_and_expansion(self):
        result = save_chapter_sources({
            "novel_id": "e077", "chapter_index": 73,
            "chapter_intent": "追踪芯片但不揭露幕后主使。",
            "chapter_plan_text": PLAN,
            "retrieved_facts": [
                {"fact_id": "FACT-0072-001", "chapter_index": 72,
                 "fact_type": "character_state", "text": "林默左臂受伤。"},
                {"fact_id": "FACT-0072-002", "chapter_index": 72,
                 "fact_type": "item", "text": "黑色芯片被赵诚夺走。"},
            ],
            "expanded_sources": [
                {"fact_id": "FACT-0072-002", "source_path": "chapters/c72.md",
                 "paragraph_start": 4, "paragraph_end": 6},
            ],
        })
        report = (self.fs.root / result["chapter_sources_path"]).read_text(encoding="utf-8")
        self.assertIn("FACT-0072-002", report)
        self.assertNotIn("FACT-0072-001", report)
        self.assertIn("paragraphs 4-6", report)
        self.assertIn("tracking/book_plan.md", report)
        self.assertIn("本章不能揭露幕后主使", report)

        writer_prompt = ChapterPlan.from_markdown(PLAN).build_writer_prompt()
        self.assertIn("FACT-0072-002", writer_prompt)
        self.assertIn("本章不能揭露幕后主使", writer_prompt)
        self.assertNotIn("Book Plan", writer_prompt)
        self.assertNotIn("Volume Plan", writer_prompt)

    def test_production_maintenance_does_not_call_fulltext_index(self):
        from src.storage.rag_maintenance_v2 import RAGMaintenanceService
        source = inspect.getsource(RAGMaintenanceService.run)
        self.assertIn("index_facts", source)
        self.assertNotIn("index_chapter", source)


class TestDerivedFailure(E077Case):
    def test_rag_failure_is_visible_without_revoking_commit(self):
        digest_path = self.fs.root / "states" / "fact_digest_ch0072_x.md"
        digest_path.write_text(ATOMIC_MD, encoding="utf-8")
        canonical = self.fs.canonical_chapter_path(72)
        canonical.write_text("canonical prose", encoding="utf-8")
        with patch.object(AtomicFactStore, "index_facts", side_effect=RuntimeError("down")):
            result = rag_index({
                "novel_id": "e077", "chapter_index": 72,
                "commit_success": True,
                "fact_digest_path": "states/fact_digest_ch0072_x.md",
                "canonical_source_path": "chapters/chapter_0072.md",
                "warnings": [],
            })
        self.assertEqual(result["workflow_status"], "DERIVATION_ERROR")
        self.assertIn("Atomic Fact RAG failed", result["derived_state_errors"][0])
        self.assertTrue(canonical.exists())


if __name__ == "__main__":
    unittest.main()
