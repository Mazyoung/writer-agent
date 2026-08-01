"""E04 RAG MVP 测试：chunking / index / retrieval / isolation / degradation。

覆盖 E04 P0 不变量：
- chunk size / overlap / deterministic / no empty chunk
- index idempotency / stale chunk removal / metadata
- future leakage / novel isolation / branch isolation
- planner prompt injection / empty retrieval
- retrieval exception degradation / index failure without rollback
- lazy initialization / backfill idempotency / rebuild / corpus

运行: venv/Scripts/python.exe -m unittest discover -s tests -v
"""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.chroma_store import (
    ChromaStore, chunk_text, make_chunk_id,
    RetrievalTrace, RetrievalResult,
    DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP, DEFAULT_TOP_K,
    DEFAULT_BRANCH_ID, COLLECTION_NAME,
)
from src.core.agent_base import BaseAgent
from src.config.settings import get_settings
import src.core.interceptor as interceptor_mod


# ═══════════════════════════════════════════════════════════════
# Unit: Chunking
# ═══════════════════════════════════════════════════════════════

class TestChunkSize(unittest.TestCase):
    def test_all_chunks_within_size(self):
        text = "A" * 2500
        chunks = chunk_text(text, chunk_size=800, chunk_overlap=100)
        self.assertGreater(len(chunks), 1)
        for _, c in chunks:
            self.assertLessEqual(len(c), 800, "每个 chunk 不得超过 chunk_size")


class TestChunkOverlap(unittest.TestCase):
    def test_consecutive_chunks_overlap(self):
        """chunk N 的尾部与 chunk N+1 的头部重叠。"""
        text = "abcdefghij" * 100  # 1000 chars
        chunks = chunk_text(text, chunk_size=200, chunk_overlap=50)
        self.assertGreaterEqual(len(chunks), 2)
        c0_end = chunks[0][1][-50:]
        c1_start = chunks[1][1][:50]
        self.assertEqual(c0_end, c1_start, "连续 chunks 必须按 overlap 重叠")


class TestDeterministicChunking(unittest.TestCase):
    def test_same_input_same_output(self):
        text = "test content " * 100
        c1 = chunk_text(text, chunk_size=500, chunk_overlap=50)
        c2 = chunk_text(text, chunk_size=500, chunk_overlap=50)
        self.assertEqual(len(c1), len(c2))
        for (i1, t1), (i2, t2) in zip(c1, c2):
            self.assertEqual(i1, i2)
            self.assertEqual(t1, t2)

    def test_different_seed_same_result(self):
        """多次调用产生完全相同的 chunk index 和内容。"""
        text = "确定性测试。" * 150
        first = chunk_text(text)
        for _ in range(5):
            self.assertEqual(first, chunk_text(text))


class TestNoEmptyChunk(unittest.TestCase):
    def test_whitespace_only_returns_empty(self):
        self.assertEqual(len(chunk_text("   \n  \t  ")), 0)

    def test_empty_string_returns_empty(self):
        self.assertEqual(len(chunk_text("")), 0)

    def test_single_char_produces_one_chunk(self):
        chunks = chunk_text("A")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][1], "A")


class TestChunkIdFormat(unittest.TestCase):
    def test_contains_all_components(self):
        cid = make_chunk_id("novel_x", "main", 3, 0)
        self.assertIn("novel_x", cid)
        self.assertIn("main", cid)
        self.assertIn("ch0003", cid)
        self.assertIn("chunk000", cid)

    def test_no_uuid_pattern(self):
        """Chunk ID 中不应出现随机 UUID 格式（E04 P0 #3）。"""
        cid = make_chunk_id("novel_x", "main", 3, 0)
        # UUID pattern: 8-4-4-4-12 hex
        uuid_pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
        self.assertIsNone(re.search(uuid_pattern, cid))

    def test_different_chapters_different_ids(self):
        id1 = make_chunk_id("n", "main", 1, 0)
        id2 = make_chunk_id("n", "main", 2, 0)
        self.assertNotEqual(id1, id2)


# ═══════════════════════════════════════════════════════════════
# Unit: RetrievalTrace
# ═══════════════════════════════════════════════════════════════

class TestRetrievalTraceRoundTrip(unittest.TestCase):
    def test_to_dict_from_dict(self):
        trace = RetrievalTrace(
            chapter_index=5, branch_id="main",
            query="test query", top_k=3,
            filters={"novel_id": "test"},
            timestamp="2026-08-01T00:00:00", success=True,
        )
        trace.results.append(RetrievalResult(
            doc_id="id_1", chapter_index=2, chunk_index=0,
            source_path="chapters/ch_0002", distance=0.35, text="sample",
        ))
        d = trace.to_dict()
        restored = RetrievalTrace.from_dict(d)
        self.assertEqual(restored.chapter_index, 5)
        self.assertEqual(len(restored.results), 1)
        self.assertEqual(restored.results[0].doc_id, "id_1")
        self.assertEqual(restored.results[0].distance, 0.35)

    def test_failed_trace_serialization(self):
        trace = RetrievalTrace(success=False, error_message="embedding API error")
        d = trace.to_dict()
        self.assertFalse(d["success"])
        self.assertEqual(d["error_message"], "embedding API error")
        restored = RetrievalTrace.from_dict(d)
        self.assertFalse(restored.success)


# ═══════════════════════════════════════════════════════════════
# Integration: ChromaStore with temp ChromaDB
# ═══════════════════════════════════════════════════════════════

class _TempChromaCase(unittest.TestCase):
    """每个测试独立 temp ChromaStore。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = ChromaStore(self.tmp / "chroma_db")

    def tearDown(self):
        try:
            del self.store
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _index(self, novel_id="test_novel", branch_id="main",
               chapter_index=1, content=None):
        if content is None:
            content = "第{}章测试内容。".format(chapter_index) * 200
        return self.store.index_chapter(
            novel_id=novel_id, branch_id=branch_id,
            chapter_index=chapter_index, content=content,
            source_path="chapters/chapter_{:04d}_styled".format(chapter_index),
        )


class TestIndexIdempotency(_TempChromaCase):
    def test_repeat_index_same_count(self):
        content = "测试内容。 " * 300
        c1 = self._index(content=content)
        c2 = self._index(content=content)
        self.assertEqual(c1, c2, "重复索引相同内容 chunk 数必须不变")
        self.assertGreater(c1, 0)


class TestStaleChunkRemoval(_TempChromaCase):
    def test_shorter_content_fewer_chunks(self):
        long_c = "长内容。" * 500
        short_c = "短。" * 50
        c1 = self._index(content=long_c)
        c2 = self._index(content=short_c)
        self.assertLess(c2, c1, "缩短内容后 chunk 数应减少")

        # Verify only new (shorter) chunks exist
        results = self.store.search(
            "test_novel", "main", "短", chapter_index=99, top_k=10)
        self.assertEqual(len(results), c2,
                         "数据库中只能存在最新索引的 chunks")


class TestMetadata(_TempChromaCase):
    def test_all_metadata_fields_present(self):
        self._index(novel_id="meta_novel", chapter_index=7)
        results = self.store.search(
            "meta_novel", "main", "测试", chapter_index=99, top_k=5)
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertEqual(r.chapter_index, 7)
            self.assertIn("meta_novel", r.doc_id)
            self.assertIn("main", r.doc_id)
            self.assertIn("chunk", r.doc_id)
            self.assertTrue(r.source_path.startswith("chapters/"),
                            f"source_path 应以 chapters/ 开头: {r.source_path}")


class TestFutureLeakage(_TempChromaCase):
    def test_planning_ch5_cannot_see_ch5_or_later(self):
        """规划第5章时，chapter_index ≥ 5 的 chunks 不可见（E04 P0 #6）。"""
        for ci in [3, 5, 7]:
            self._index(chapter_index=ci,
                        content="第{}章独特关键词XYZ。".format(ci) * 200)
        results = self.store.search(
            "test_novel", "main", "独特关键词XYZ", chapter_index=5, top_k=10)
        for r in results:
            self.assertLess(r.chapter_index, 5,
                            "chapter_index={} 不应 ≥ 5".format(r.chapter_index))

    def test_future_chapters_not_leaked(self):
        """规划 ch4 时，即使 DB 中有 ch5/ch6，也不能检索出来。"""
        for ci in range(1, 7):
            self._index(chapter_index=ci,
                        content="章节{}内容。".format(ci) * 200)
        results = self.store.search(
            "test_novel", "main", "章节6", chapter_index=4, top_k=10)
        for r in results:
            self.assertLess(r.chapter_index, 4,
                            "chapter_index={} 不应 ≥ 4".format(r.chapter_index))


class TestNovelIsolation(_TempChromaCase):
    def test_novel_a_hidden_from_novel_b(self):
        """小说 A 不能检索小说 B 的内容（E04 P0 #7）。"""
        self._index(novel_id="novel_a", chapter_index=1,
                    content="小说A的绝密情报。 " * 200)
        results = self.store.search(
            "novel_b", "main", "绝密情报", chapter_index=99, top_k=10)
        self.assertEqual(len(results), 0, "小说B 不应检索到小说A 的内容")


class TestBranchIsolation(_TempChromaCase):
    def test_main_branch_isolated_from_experiment(self):
        """main branch 不能检索其他 branch 的内容（E04 P0 #7）。"""
        self._index(branch_id="main", chapter_index=1,
                    content="主分支剧情。 " * 200)
        self._index(branch_id="experiment", chapter_index=1,
                    content="实验分支完全不同的走向。 " * 200)

        # 从 main 检索"实验" —— 不应返回 experiment 分支内容
        results = self.store.search(
            "test_novel", "main", "实验", chapter_index=99, top_k=10)
        for r in results:
            self.assertNotIn("experiment", r.doc_id,
                             "main 分支不应检索到 experiment 分支")


class TestEmptyRetrieval(_TempChromaCase):
    def test_empty_collection_returns_empty_list(self):
        results = self.store.search(
            "test_novel", "main", "任何查询", chapter_index=5, top_k=5)
        self.assertEqual(len(results), 0)

    def test_no_matching_query_returns_empty(self):
        """索引了内容但不匹配的查询应返回空（或语义仍匹配则返回结果）。
        至少不崩溃。"""
        self._index(content="完全无关的内容。" * 200)
        results = self.store.search(
            "test_novel", "main", "XYZXYZ_不存在的关键词", chapter_index=99,
            top_k=5)
        # 语义搜索可能仍然返回结果，但至少不崩溃
        self.assertIsInstance(results, list)


class TestLazyInitialization(unittest.TestCase):
    def test_constructor_does_not_create_client(self):
        store = ChromaStore(Path(tempfile.mkdtemp()))
        self.assertFalse(store.is_initialized,
                         "构造 ChromaStore 不得立即创建 ChromaDB client（E04 P0 #1）")

    def test_index_creates_client(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            store = ChromaStore(tmp / "chroma")
            self.assertFalse(store.is_initialized)
            store.index_chapter("test", "main", 1,
                               "测试内容。" * 100,
                               source_path="chapters/test")
            self.assertTrue(store.is_initialized)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_search_creates_client(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            store = ChromaStore(tmp / "chroma")
            self.assertFalse(store.is_initialized)
            store.search("test", "main", "query", chapter_index=5, top_k=3)
            self.assertTrue(store.is_initialized)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# System: Orchestrator-level RAG tests (mock LLM)
# ═══════════════════════════════════════════════════════════════

class _TmpNovelCase(unittest.TestCase):
    """Redirect settings.data_dir to temp for isolation."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = get_settings()
        self._orig_data_dir = self.settings.data_dir
        self._orig_api_key = self.settings.api_key
        self.settings.data_dir = self.tmp
        self.settings.api_key = "test-key"
        interceptor_mod._interceptor = None

    def tearDown(self):
        self.settings.data_dir = self._orig_data_dir
        self.settings.api_key = self._orig_api_key
        interceptor_mod._interceptor = None
        shutil.rmtree(self.tmp, ignore_errors=True)


# ── Shared test data ──────────────────────────────────────

CHAPTER_1_CONTENT = (
    "柯林在废墟配电间醒来。他检查背包，发现一把扳手和一枚发光徽章。"
    "外面传来变异兽的嚎叫，他决定先守住这片避难所。" * 80
)
CHAPTER_2_CONTENT = (
    "柯林向东走了两天，在旧高架桥下遇到瘸子莫。瘸子莫经营着一处废土交易站。"
    "柯林用徽章换取了净水器和一张标注了地下入口的旧地图。" * 80
)

SAMPLE_PLAN_MD = """# 第3章规划：《出发》
## 一、章节信息
- **章大纲**: 柯林按地图出发前往地下入口
- **章节类型**: 延续型
- **总场景数**: 1
## 二、写作上下文包
### 角色关系图
柯林 ↔ 瘸子莫： 交易伙伴
### 物品/装备追踪
扳手、净水器、旧地图
### 修炼/力量体系现状
暂无
### 关键伏笔节点
发光徽章的用途
### 情感调色板
希望
### 禁止清单
暂无
## 三、场景级写作计划
### 场景 1：出发 [状态：待规划]
- **发生什么**：柯林离开交易站沿地图出发
- **本场景的戏剧功能**：开启地下探索线
- **对话必须达成的信息增量**：地图上标记的危险区域
- **角色微时刻**：握紧徽章
- **涉及角色**：柯林、瘸子莫
- **情绪曲线**：期待中带着不安
- **字数预估**：1200
- **与前后衔接**：承接交易站，开启旅程
"""

VOLUME_PLAN = """# 第1卷规划：《废墟求生》
- **版本**: v1
- **状态**: ACTIVE
- **章节范围**: 第1章-第5章
## 卷概述
- **核心冲突**: 生存
## 事件链
### 事件1：配电间求生
- **对应章节**: 第1章
### 事件2：交易站
- **对应章节**: 第2章
### 事件3：按地图出发前往地下入口
- **对应章节**: 第3章
## 节奏约束
"""

BOOK_PLAN = """# 全书规划：《测试》
- **版本**: v1
## 核心目标
探索地下文明
## 核心矛盾
生存与真相
## 主角长期成长方向
拾荒者到领袖
## 战略约束
- 废土无净水
## 核心梗概
测试
## 全书主题
- 生存
## 结局方向
开放
## 卷框架
### 第1卷：废墟求生
- **核心冲突**: 生存
## 全局伏笔追踪
"""

WORLD_SETTING = "# 世界观\n废土设定：地表无净水，变异兽横行。\n"


def _setup_novel_dirs(root: Path):
    """Create minimal novel directory structure for tests."""
    for d in ["settings", "tracking", "chapters", "outlines", "states"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "settings" / "world_setting.md").write_text(
        WORLD_SETTING, encoding="utf-8")
    (root / "tracking" / "book_plan.md").write_text(
        BOOK_PLAN, encoding="utf-8")
    (root / "tracking" / "volume_plan.md").write_text(
        VOLUME_PLAN, encoding="utf-8")


class TestPlannerPromptInjection(_TmpNovelCase):
    """RAG evidence appears in ChapterPlanner LLM prompt (E04 P0 #8, #9)."""

    def test_rag_evidence_section_in_prompt(self):
        from src.core.orchestrator import Orchestrator

        root = self.tmp / "novels" / "rag_novel"
        _setup_novel_dirs(root)

        # Write finalized/styled chapters
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            CHAPTER_1_CONTENT, encoding="utf-8")
        (root / "chapters" / "chapter_0002_styled_20260801_120000.md").write_text(
            CHAPTER_2_CONTENT, encoding="utf-8")

        captured_user_msg = {}

        def fake_llm(self, messages):
            captured_user_msg["content"] = messages[-1]["content"]
            return SAMPLE_PLAN_MD

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm):
            orch = Orchestrator("rag_novel")
            # Index chapters first
            orch._index_chapter_to_rag(1)
            orch._index_chapter_to_rag(2)
            # Plan chapter 3 — should retrieve evidence from ch1/ch2
            orch.plan_chapter(3)

        prompt = captured_user_msg["content"]
        self.assertIn("【历史检索证据（RAG）】", prompt,
                      "RAG evidence section MUST appear in planner prompt")


class TestRetrievalExceptionDegradation(_TmpNovelCase):
    """RAG search failure must NOT crash ChapterPlanner (E04 P0 #11)."""

    def test_search_exception_does_not_crash_planning(self):
        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        root = self.tmp / "novels" / "degrade_novel"
        _setup_novel_dirs(root)

        captured = {}

        def fake_llm(self, messages):
            captured["user"] = messages[-1]["content"]
            return SAMPLE_PLAN_MD

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm):
            orch = Orchestrator("degrade_novel")
            # Make search fail
            with mock.patch.object(ChromaStore, "search",
                                   side_effect=RuntimeError("ChromaDB down")):
                plan = orch.plan_chapter(3)

        # Planning must complete successfully despite RAG failure
        self.assertEqual(plan.chapter_index, 3)
        self.assertEqual(len(plan.scenes), 1)
        # Prompt still assembled with standard sections
        self.assertIn("世界观设定", captured["user"])


class TestIndexFailureWithoutRollback(_TmpNovelCase):
    """Index failure must NOT rollback chapter state (E04 P0 #12)."""

    def test_index_failure_preserves_chapter_review(self):
        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        root = self.tmp / "novels" / "indexfail_novel"
        _setup_novel_dirs(root)

        # Write tracking docs needed for review
        (root / "tracking" / "character_relationships.md").write_text(
            "# 角色关系\n", encoding="utf-8")
        (root / "tracking" / "items_equipment.md").write_text(
            "# 物品装备\n", encoding="utf-8")
        (root / "tracking" / "cultivation_system.md").write_text(
            "# 修炼体系\n", encoding="utf-8")

        # Write styled chapter
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            "测试章节内容。" * 200, encoding="utf-8")
        # Write chapter plan
        (root / "outlines" / "chapter_plan_ch0001.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")

        # E05: mock must include ## 事实摘要 for deterministic extraction
        MOCK_ANALYSIS = """# 分析
## 事实摘要
### 确定的物品
扳手
### 确定的角色状态
柯林：健康
### 确定的事件
柯林醒来
### 确定的数字/数据
无
### 明确未出现的内容
无
### 待解悬念
无
## 追踪文档变更建议
无变更
## 一致性检查
通过
## 质量审阅
通过
"""
        with mock.patch.object(BaseAgent, "_call_llm",
                               lambda self, messages: MOCK_ANALYSIS):
            orch = Orchestrator("indexfail_novel")
            # Make index_chapter fail
            with mock.patch.object(ChromaStore, "index_chapter",
                                   side_effect=RuntimeError("embedding error")):
                result = orch.review_chapter(1)

        # Chapter review must still complete
        self.assertIn("change_log", result)
        # Fact digest must still exist
        digests = list((root / "states").glob("fact_digest_ch0001_*.md"))
        self.assertGreater(len(digests), 0,
                           "事实摘要必须生成，即使索引失败")


class TestBackfillIdempotency(_TmpNovelCase):
    """Repeated backfill produces same chunk count (E04 spec)."""

    def test_backfill_idempotent(self):
        from src.core.orchestrator import Orchestrator

        root = self.tmp / "novels" / "backfill_novel"
        (root / "chapters").mkdir(parents=True)

        content = "测试正文。" * 200
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            content, encoding="utf-8")
        (root / "chapters" / "chapter_0002_styled_20260801_120000.md").write_text(
            content, encoding="utf-8")

        orch = Orchestrator("backfill_novel")
        r1 = orch.rag_index_backfill(rebuild=False)
        r2 = orch.rag_index_backfill(rebuild=False)
        self.assertEqual(r1["total_chunks"], r2["total_chunks"],
                         "Backfill must be idempotent")
        self.assertEqual(r1["indexed_chapters"], 2)


class TestRebuild(_TmpNovelCase):
    """--rebuild clears and re-indexes from scratch."""

    def test_rebuild_produces_same_chunk_count(self):
        from src.core.orchestrator import Orchestrator

        root = self.tmp / "novels" / "rebuild_novel"
        (root / "chapters").mkdir(parents=True)

        c1 = "第一章内容。" * 200
        c2 = "第二章不同内容。" * 200
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            c1, encoding="utf-8")
        (root / "chapters" / "chapter_0002_styled_20260801_120000.md").write_text(
            c2, encoding="utf-8")

        orch = Orchestrator("rebuild_novel")
        r1 = orch.rag_index_backfill(rebuild=False)
        self.assertEqual(r1["indexed_chapters"], 2)

        r2 = orch.rag_index_backfill(rebuild=True)
        self.assertEqual(r2["indexed_chapters"], 2,
                         "Rebuild must re-index all chapters")
        self.assertEqual(r1["total_chunks"], r2["total_chunks"],
                         "Rebuild must produce same chunk count (deterministic)")


class TestCorpusOnlyStyledChapters(_TmpNovelCase):
    """Only styled/finalized chapters are indexed (E04 P0 #2)."""

    def test_draft_not_indexed(self):
        from src.core.orchestrator import Orchestrator

        root = self.tmp / "novels" / "corpus_novel"
        (root / "chapters").mkdir(parents=True)

        # Draft only (no _styled suffix)
        (root / "chapters" / "chapter_0001_draft_20260801_120000.md").write_text(
            "草稿内容。" * 200, encoding="utf-8")

        orch = Orchestrator("corpus_novel")
        result = orch.rag_index_backfill(rebuild=False)
        self.assertEqual(result["indexed_chapters"], 0,
                         "草稿文件（非 styled）不应被索引")


class TestLazyOrchestratorChroma(_TmpNovelCase):
    """Orchestrator() constructor must NOT initialize Chroma (E04 P0 #1)."""

    def test_orchestrator_constructor_no_chroma_init(self):
        from src.core.orchestrator import Orchestrator

        root = self.tmp / "novels" / "lazy_novel"
        root.mkdir(parents=True)

        orch = Orchestrator("lazy_novel")
        self.assertTrue(orch._chroma is None,
                        "Orchestrator() 构造后 _chroma 必须为 None")
        # Accessing .chroma property should create it
        _ = orch.chroma
        self.assertIsNotNone(orch._chroma,
                             "访问 .chroma property 时必须创建 ChromaStore")


# ═══════════════════════════════════════════════════════════════
# E04.1 Closure / Audit Fix Tests
# ═══════════════════════════════════════════════════════════════

# Volume Plan data with **bold markers** (canonical format from VolumePlan.to_markdown)
VOLUME_PLAN_CANONICAL = """# 第1卷规划：《废墟求生》
- **版本**: v1
- **状态**: ACTIVE
- **章节范围**: 第1章-第5章
## 卷概述
- **核心冲突**: 生存
## 事件链
### 事件1：配电间求生
- **触发条件**: 部落遇袭
- **核心内容**: 柯林独自生存
- **涉及角色**: 柯林
- **情感基调**: 压抑
- **结果与影响**: 获得初始装备
- **衔接**: 往东
- **对应章节**: 第1章
### 事件2：交易站
- **触发条件**: 需要补给
- **核心内容**: 柯林遇到瘸子莫
- **涉及角色**: 柯林、瘸子莫
- **情感基调**: 试探
- **结果与影响**: 获得情报
- **衔接**: 获得地图
- **对应章节**: 第2章
### 事件3：RAG_VOLUME_EVENT_TEST_7319 出发前往地下入口
- **触发条件**: 获得地图
- **核心内容**: 柯林按地图标记出发
- **涉及角色**: 柯林
- **情感基调**: 期待中带着不安
- **结果与影响**: 抵达地下入口
- **衔接**: 进入地下
- **对应章节**: 第3章
## 节奏约束
"""


class TestIndexChapterToRagRawFallbackRejected(_TmpNovelCase):
    """E04.1 Fix 1: _index_chapter_to_rag MUST NOT index raw/draft chapters."""

    def test_only_raw_chapter_no_styled_returns_zero(self):
        from src.core.orchestrator import Orchestrator

        root = self.tmp / "novels" / "raw_reject_novel"
        (root / "chapters").mkdir(parents=True)

        # Only a raw/draft chapter — no _styled file
        (root / "chapters" / "chapter_0001_draft_20260801_120000.md").write_text(
            "草稿内容。" * 200, encoding="utf-8")

        orch = Orchestrator("raw_reject_novel")
        count = orch._index_chapter_to_rag(1)
        self.assertEqual(count, 0,
                         "仅有 raw/draft 章节时 chunk count 必须为 0，不得 fallback")

    def test_styled_chapter_present_is_indexed(self):
        from src.core.orchestrator import Orchestrator

        root = self.tmp / "novels" / "styled_ok_novel"
        (root / "chapters").mkdir(parents=True)

        (root / "chapters" / "chapter_0002_styled_20260801_120000.md").write_text(
            "正式章节内容。" * 200, encoding="utf-8")

        orch = Orchestrator("styled_ok_novel")
        count = orch._index_chapter_to_rag(2)
        self.assertGreater(count, 0,
                           "有 styled 文件时必须成功索引")

    def test_source_path_matches_actual_styled_file(self):
        """source_path must reflect the actual _styled file name, not a synthetic prefix."""
        from src.core.orchestrator import Orchestrator

        root = self.tmp / "novels" / "srcpath_novel"
        (root / "chapters").mkdir(parents=True)

        (root / "chapters" / "chapter_0003_styled_20260801_120000.md").write_text(
            "正式章节。" * 200, encoding="utf-8")

        # Patch chroma.index_chapter to capture source_path
        captured_source_path = {}

        from src.storage.chroma_store import ChromaStore
        orig_index = ChromaStore.index_chapter

        def capture_index(self, novel_id, branch_id, chapter_index,
                          content, source_path="", **kwargs):
            captured_source_path["path"] = source_path
            return orig_index(self, novel_id, branch_id, chapter_index,
                            content, source_path=source_path, **kwargs)

        with mock.patch.object(ChromaStore, "index_chapter", capture_index):
            orch = Orchestrator("srcpath_novel")
            orch._index_chapter_to_rag(3)

        self.assertIn("_styled_", captured_source_path.get("path", ""),
                      "source_path 必须包含 _styled_ 文件名")
        self.assertIn("chapter_0003", captured_source_path.get("path", ""),
                      "source_path 必须包含正确的章节号")


class TestVolumeEventInRetrievalQuery(_TmpNovelCase):
    """E04.1 Fix 2: Canonical Volume Event text enters the retrieval query."""

    def test_volume_event_unique_string_in_query(self):
        """写入 canonical volume_plan.md 含唯一标识字符串，
        验证 _build_retrieval_query 产出必须包含该字符串。"""
        from src.core.orchestrator import Orchestrator

        root = self.tmp / "novels" / "volevt_novel"
        (root / "tracking").mkdir(parents=True)
        (root / "tracking" / "volume_plan.md").write_text(
            VOLUME_PLAN_CANONICAL, encoding="utf-8")

        orch = Orchestrator("volevt_novel")
        query = orch._build_retrieval_query(chapter_index=3)
        self.assertIn("RAG_VOLUME_EVENT_TEST_7319", query,
                      "Canonical Volume Event 文本必须进入 retrieval query")


class TestQueryBuilderExceptionGeneratesFailedTrace(_TmpNovelCase):
    """E04.1 Fix 3: _build_retrieval_query exception must produce a failed
    RetrievalTrace (not silent degradation without trace)."""

    def test_query_builder_exception_produces_failed_trace(self):
        from src.core.orchestrator import Orchestrator

        root = self.tmp / "novels" / "qbfail_novel"
        root.mkdir(parents=True)

        orch = Orchestrator("qbfail_novel")
        # Make _build_retrieval_query throw
        with mock.patch.object(
            orch, "_build_retrieval_query",
            side_effect=RuntimeError("volume plan parsing error")
        ):
            evidence, trace = orch._retrieve_evidence(chapter_index=5)

        self.assertEqual(evidence, "",
                         "异常时 evidence 必须为空字符串")
        self.assertIsNotNone(trace, "即使 query build 异常也必须生成 trace")
        self.assertFalse(trace.success,
                         "trace.success 必须为 False")
        self.assertIn("RuntimeError", trace.error_message,
                      "trace 必须包含异常类型")
        self.assertEqual(trace.chapter_index, 5)
        self.assertEqual(trace.branch_id, "main")
        self.assertEqual(trace.top_k, self.settings.rag_top_k)
        # Filters must still be populated even on failure
        self.assertIn("novel_id", trace.filters)
        self.assertIn("chapter_index <", trace.filters)


class TestRagSettingsControl(_TmpNovelCase):
    """E04.1 Fix 4: Settings.rag_chunk_size/rag_chunk_overlap/rag_top_k
    actually control index/search behavior (not just module-level defaults)."""

    def test_settings_override_chunk_size(self):
        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore
        from src.config.settings import Settings

        root = self.tmp / "novels" / "settings_novel"
        (root / "chapters").mkdir(parents=True)
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            "章节内容。" * 500, encoding="utf-8")

        # Create a fresh Settings with non-default values
        orch = Orchestrator("settings_novel")
        orch.settings.rag_chunk_size = 400
        orch.settings.rag_chunk_overlap = 60

        captured_kwargs = {}

        orig_index = ChromaStore.index_chapter
        def capture_kwargs(self, *args, **kwargs):
            captured_kwargs.update(kwargs)
            return orig_index(self, *args, **kwargs)

        with mock.patch.object(ChromaStore, "index_chapter", capture_kwargs):
            orch._index_chapter_to_rag(1)

        # Index call should use the overridden settings values
        self.assertEqual(captured_kwargs.get("chunk_size"), 400,
                         "index_chapter 必须使用 settings.rag_chunk_size")
        self.assertEqual(captured_kwargs.get("chunk_overlap"), 60,
                         "index_chapter 必须使用 settings.rag_chunk_overlap")

    def test_settings_override_top_k(self):
        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        root = self.tmp / "novels" / "topk_novel"
        (root / "chapters").mkdir(parents=True)
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            "章节内容。" * 200, encoding="utf-8")
        (root / "tracking").mkdir(parents=True)
        (root / "tracking" / "volume_plan.md").write_text(
            VOLUME_PLAN_CANONICAL, encoding="utf-8")

        orch = Orchestrator("topk_novel")
        orch.settings.rag_top_k = 3

        captured_top_k = {}

        orig_search = ChromaStore.search
        def capture_top_k(self, *args, **kwargs):
            captured_top_k["top_k"] = kwargs.get("top_k")
            return orig_search(self, *args, **kwargs)

        with mock.patch.object(ChromaStore, "search", capture_top_k):
            orch._index_chapter_to_rag(1)
            orch._retrieve_evidence(chapter_index=3)

        self.assertEqual(captured_top_k.get("top_k"), 3,
                         "search 必须使用 settings.rag_top_k")


if __name__ == "__main__":
    unittest.main()
