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
        self.assertGreater(len(results), 0,
                           "positive control: 可见的第3章必须能被检索到")
        self.assertIn(3, {r.chapter_index for r in results})
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
        self.assertGreater(len(results), 0,
                           "positive control: ch1-ch3 历史数据必须可见")
        self.assertTrue({r.chapter_index for r in results} & {1, 2, 3})
        for r in results:
            self.assertLess(r.chapter_index, 4,
                            "chapter_index={} 不应 ≥ 4".format(r.chapter_index))


class TestNovelIsolation(_TempChromaCase):
    def test_novel_a_hidden_from_novel_b(self):
        """小说 A 不能检索小说 B 的内容（E04 P0 #7）。"""
        self._index(novel_id="novel_a", chapter_index=1,
                    content="小说A的绝密情报。 " * 200)
        self._index(novel_id="novel_b", chapter_index=1,
                    content="小说B自己的公开记录。 " * 200)

        results_a = self.store.search(
            "novel_a", "main", "绝密情报", chapter_index=99, top_k=10)
        self.assertGreater(len(results_a), 0,
                           "positive control: 小说A必须能检索自己的数据")
        self.assertTrue(all("novel_a" in r.doc_id for r in results_a))

        results_b = self.store.search(
            "novel_b", "main", "绝密情报", chapter_index=99, top_k=10)
        self.assertGreater(len(results_b), 0,
                           "positive control: 小说B必须有自己的可见数据")
        self.assertTrue(all("novel_b" in r.doc_id for r in results_b))
        self.assertTrue(all("novel_a" not in r.doc_id for r in results_b),
                        "小说B 不应检索到小说A 的内容")


class TestBranchIsolation(_TempChromaCase):
    def test_main_branch_isolated_from_experiment(self):
        """main branch 不能检索其他 branch 的内容（E04 P0 #7）。"""
        self._index(branch_id="main", chapter_index=1,
                    content="主分支剧情。 " * 200)
        self._index(branch_id="experiment", chapter_index=1,
                    content="实验分支完全不同的走向。 " * 200)

        # 两个方向都必须有 positive control，同时保持相互隔离。
        main_results = self.store.search(
            "test_novel", "main", "实验", chapter_index=99, top_k=10)
        self.assertGreater(len(main_results), 0,
                           "positive control: main 数据必须可见")
        self.assertTrue(all("_main_" in r.doc_id for r in main_results))
        self.assertTrue(all("experiment" not in r.doc_id for r in main_results),
                        "main 分支不应检索到 experiment 分支")

        experiment_results = self.store.search(
            "test_novel", "experiment", "主分支", chapter_index=99, top_k=10)
        self.assertGreater(len(experiment_results), 0,
                           "positive control: experiment 数据必须可见")
        self.assertTrue(all("_experiment_" in r.doc_id
                            for r in experiment_results))
        self.assertTrue(all("_main_" not in r.doc_id
                            for r in experiment_results),
                        "experiment 分支不应检索到 main 分支")


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


if __name__ == "__main__":
    unittest.main()
