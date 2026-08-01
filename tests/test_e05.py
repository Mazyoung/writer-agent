"""E05 Cost & Duplicate Work Closure 测试。

覆盖:
- E05-1: Styled chapter single ownership (write/style-edit)
- E05-2: Fact Digest single LLM pass
- E05-3: LLM cost invariant (exactly 1 call in review)
- E05-4: ClaudeStylist.edit_chapter no side effects
- E05-5: FactDigest round-trip fix

运行: venv/Scripts/python.exe -m unittest discover -s tests -v
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.agent_base import BaseAgent
from src.storage.document_formats import FactDigest, ChapterPlan
from src.config.settings import get_settings
import src.core.interceptor as interceptor_mod


# ── Shared test data ──────────────────────────────────────

SAMPLE_PLAN_MD = """# 第1章规划：《测试》
## 一、章节信息
- **章大纲**: 测试大纲
- **章节类型**: 延续型
- **总场景数**: 1
## 二、写作上下文包
### 角色关系图
待生成
### 物品/装备追踪
待生成
### 修炼/力量体系现状
暂无
### 关键伏笔节点
暂无
### 情感调色板
紧张
### 禁止清单
暂无
## 三、场景级写作计划
### 场景 1：开场 [状态：待规划]
- **发生什么**：测试场景
- **本场景的戏剧功能**：推进
- **对话必须达成的信息增量**：无
- **角色微时刻**：无
- **涉及角色**：柯林
- **情绪曲线**：平淡
- **字数预估**：500
- **与前后衔接**：无
"""

MOCK_RAW_ANALYSIS = """# 第1章复盘分析
## 事实摘要
### 确定的物品
扳手、发光徽章
### 确定的角色状态
柯林：健康、警惕
### 确定的事件
柯林在配电间醒来，检查背包
### 确定的数字/数据
背包中2件物品
### 明确未出现的内容
FACT_DIGEST_SINGLE_PASS_5821
### 待解悬念
徽章来源不明
## 追踪文档变更建议
### 角色关系
### 物品装备
### 修炼体系
## 一致性检查
无硬错误
## 质量审阅
PASS
"""

VOLUME_PLAN = """# 第1卷规划：《测试卷》
- **版本**: v1
- **状态**: ACTIVE
- **章节范围**: 第1章-第5章
## 卷概述
- **核心冲突**: 生存
## 事件链
### 事件1：配电间
- **对应章节**: 第1章
## 节奏约束
"""

BOOK_PLAN = """# 全书规划：《测试》
- **版本**: v1
## 核心目标
探索
## 核心矛盾
生存与真相
## 主角长期成长方向
成长
## 战略约束
无
## 核心梗概
测试
## 全书主题
- 生存
## 结局方向
开放
## 卷框架
### 第1卷：测试卷
- **核心冲突**: 生存
## 全局伏笔追踪
"""


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


def _setup_write_dirs(root: Path):
    """Create minimal novel dirs for write_chapter test."""
    for d in ["settings", "tracking", "chapters", "outlines", "states"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "settings" / "world_setting.md").write_text(
        "# 世界观\n废土无净水\n", encoding="utf-8")
    (root / "tracking" / "volume_plan.md").write_text(
        VOLUME_PLAN, encoding="utf-8")
    (root / "tracking" / "book_plan.md").write_text(
        BOOK_PLAN, encoding="utf-8")
    (root / "outlines" / "chapter_plan_ch0001.md").write_text(
        SAMPLE_PLAN_MD, encoding="utf-8")


def _setup_review_dirs(root: Path):
    """Create minimal novel dirs for review_chapter test."""
    for d in ["settings", "tracking", "chapters", "outlines", "states"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "tracking" / "character_relationships.md").write_text(
        "# 角色关系\n", encoding="utf-8")
    (root / "tracking" / "items_equipment.md").write_text(
        "# 物品装备\n", encoding="utf-8")
    (root / "tracking" / "cultivation_system.md").write_text(
        "# 修炼体系\n", encoding="utf-8")
    (root / "outlines" / "chapter_plan_ch0001.md").write_text(
        SAMPLE_PLAN_MD, encoding="utf-8")
    (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
        "测试章节内容。" * 200, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# E05-1: Styled Chapter Single Ownership
# ═══════════════════════════════════════════════════════════════

class TestWriteChapterSingleSaveAndCheck(_TmpNovelCase):
    """write_chapter: 1 save, 1 StyleChecker, 1 LLM edit."""

    def test_write_chapter_single_save(self):
        from src.core.orchestrator import Orchestrator
        from src.storage.file_store import FileStore
        from src.agents.author.style_checker import StyleChecker

        root = self.tmp / "novels" / "wc_novel"
        _setup_write_dirs(root)

        # Provide a styled chapter so DeepSeekWriter.fake_llm return and
        # _get_prev_chapter_end doesn't crash on ch1
        orch = Orchestrator("wc_novel")

        mock_report = mock.MagicMock()
        mock_report.errors = 0
        mock_report.warnings = 0
        mock_report.summary.return_value = ""

        def fake_llm(self, messages):
            return "第一章草稿内容。" * 50

        def fake_styled(chapter_text, *args, **kwargs):
            return "编辑后的文本。" * 50

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(orch.stylist, "edit_chapter",
                               side_effect=fake_styled) as mock_edit, \
             mock.patch.object(StyleChecker, "check_all",
                               return_value=mock_report) as mock_check:
            orch.write_chapter(1)

        # ClaudeStylist.edit_chapter called exactly once
        self.assertEqual(mock_edit.call_count, 1,
                         "ClaudeStylist.edit_chapter 必须恰好调用 1 次")

        # StyleChecker.check_all called exactly once
        self.assertEqual(mock_check.call_count, 1,
                         f"StyleChecker 必须恰好执行 1 次，"
                         f"实际 {mock_check.call_count}")

        # One styled file created (timestamped)
        styled_files = list(
            (self.tmp / "novels" / "wc_novel" / "chapters").glob(
                "chapter_0001_styled_*.md"))
        self.assertEqual(len(styled_files), 1,
                         f"必须恰好 1 个 styled 文件，"
                         f"实际 {len(styled_files)}")

    def test_write_chapter_one_styled_file(self):
        """write_chapter 只生成一个 styled timestamp 文件。"""
        from src.core.orchestrator import Orchestrator
        from src.agents.author.style_checker import StyleChecker

        root = self.tmp / "novels" / "wc2_novel"
        _setup_write_dirs(root)

        orch = Orchestrator("wc2_novel")

        def fake_llm(self, messages):
            return "草稿内容。" * 50

        def fake_styled(chapter_text, *args, **kwargs):
            return "编辑后文本。" * 50

        mock_report = mock.MagicMock()
        mock_report.errors = 0
        mock_report.warnings = 0
        mock_report.summary.return_value = ""

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(orch.stylist, "edit_chapter",
                               side_effect=fake_styled), \
             mock.patch.object(StyleChecker, "check_all",
                               return_value=mock_report):
            orch.write_chapter(1)

        styled_files = list(
            (root / "chapters").glob("chapter_0001_styled_*.md"))
        self.assertEqual(len(styled_files), 1,
                         f"必须恰好 1 个 styled 文件，实际 {len(styled_files)}: "
                         f"{[f.name for f in styled_files]}")


class TestStyleEditSingleSaveAndCheck(_TmpNovelCase):
    """style_edit: 1 save, 1 StyleChecker, 1 LLM edit."""

    def test_style_edit_single_save_and_check(self):
        from src.core.orchestrator import Orchestrator
        from src.storage.file_store import FileStore
        from src.agents.author.style_checker import StyleChecker

        root = self.tmp / "novels" / "se_novel"
        _setup_write_dirs(root)
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            "已有 styled 章节。" * 50, encoding="utf-8")

        orch = Orchestrator("se_novel")

        mock_report = mock.MagicMock()
        mock_report.errors = 0
        mock_report.warnings = 0
        mock_report.summary.return_value = ""

        def fake_styled(chapter_text, *args, **kwargs):
            return "修改后文本。" * 50

        with mock.patch.object(orch.stylist, "edit_chapter",
                               side_effect=fake_styled) as mock_edit, \
             mock.patch.object(StyleChecker, "check_all",
                               return_value=mock_report) as mock_check:
            orch.style_edit(1, feedback="改短一点")

        self.assertEqual(mock_edit.call_count, 1,
                         "ClaudeStylist.edit_chapter 必须恰好调用 1 次")

        self.assertEqual(mock_check.call_count, 1,
                         f"StyleChecker 必须恰好执行 1 次，"
                         f"实际 {mock_check.call_count}")

        styled_saves = list(
            (self.tmp / "novels" / "se_novel" / "chapters").glob(
                "chapter_0001_styled_*.md"))
        self.assertEqual(len(styled_saves), 2,  # original + new
                         f"styled chapter 必须保存（原文件 + 新文件），"
                         f"实际 {len(styled_saves)}")


# ═══════════════════════════════════════════════════════════════
# E05-2/3: Fact Digest Single LLM Pass
# ═══════════════════════════════════════════════════════════════

class TestReviewChapterSingleLLMCall(_TmpNovelCase):
    """review_chapter: StateManager analysis LLM = exactly 1."""

    def test_review_chapter_exactly_one_llm_call(self):
        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        root = self.tmp / "novels" / "rv_novel"
        _setup_review_dirs(root)

        llm_call_count = []

        def counting_llm(self, messages):
            llm_call_count.append(1)
            return MOCK_RAW_ANALYSIS

        orch = Orchestrator("rv_novel")
        # Silently skip RAG indexing to avoid LLM noise
        with mock.patch.object(ChromaStore, "index_chapter",
                               return_value=2), \
             mock.patch.object(BaseAgent, "_call_llm", counting_llm):
            result = orch.review_chapter(1)

        # StateManager.review_chapter itself is 1 LLM call
        # E05: extract_fact_digest_from_analysis is deterministic (0 LLM)
        self.assertEqual(len(llm_call_count), 1,
                         f"review_chapter 内 LLM 调用必须恰好 1 次，"
                         f"实际 {len(llm_call_count)}")

        self.assertIn("change_log", result)

    def test_fact_digest_content_from_raw_analysis(self):
        """Fact Digest 内容来自第一次 raw_analysis，不是独立 LLM 调用。"""
        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        root = self.tmp / "novels" / "fd_novel"
        _setup_review_dirs(root)

        orch = Orchestrator("fd_novel")

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS

        with mock.patch.object(ChromaStore, "index_chapter",
                               return_value=2), \
             mock.patch.object(BaseAgent, "_call_llm", fake_llm):
            orch.review_chapter(1)

        # Load the saved fact_digest file
        fact_files = sorted(
            (root / "states").glob("fact_digest_ch0001_*.md"))
        self.assertGreater(len(fact_files), 0,
                           "Fact Digest 文件必须生成")
        content = fact_files[-1].read_text(encoding="utf-8")
        self.assertIn("FACT_DIGEST_SINGLE_PASS_5821", content,
                      "保存的 Fact Digest 必须包含唯一测试字符串，"
                      "证明它来自 raw_analysis 而非独立 LLM 调用")


# ═══════════════════════════════════════════════════════════════
# E05-2: extract_fact_digest_from_analysis unit tests
# ═══════════════════════════════════════════════════════════════

class TestExtractFactDigestFromAnalysis(unittest.TestCase):
    """StateManager.extract_fact_digest_from_analysis (no LLM)."""

    def setUp(self):
        from src.config.settings import get_settings
        from src.storage.sqlite_store import SQLiteStore

        self.tmp = Path(tempfile.mkdtemp())
        self.settings = get_settings()
        self._orig_data_dir = self.settings.data_dir
        self._orig_api_key = self.settings.api_key
        self.settings.data_dir = self.tmp
        self.settings.api_key = "test-key"

        # Create a proper StateManager
        sqlite = SQLiteStore(self.tmp / "novels" / "fdtest" / "state.db")
        from src.agents.state_manager.state_manager import StateManager
        self.sm = StateManager("fdtest", sqlite)

    def tearDown(self):
        self.settings.data_dir = self._orig_data_dir
        self.settings.api_key = self._orig_api_key
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_successful_extraction(self):
        fd = self.sm.extract_fact_digest_from_analysis(
            MOCK_RAW_ANALYSIS, 1)
        self.assertEqual(fd.chapter_index, 1)
        self.assertIn("扳手", fd.confirmed_items)
        self.assertIn("柯林", fd.confirmed_character_states)
        self.assertIn("配电间", fd.confirmed_events)
        self.assertIn("FACT_DIGEST_SINGLE_PASS_5821",
                      fd.explicitly_absent)
        self.assertIn("徽章来源", fd.pending_suspense)

        # Verify file was saved
        saved = sorted(
            (self.sm.fs.root / "states").glob("fact_digest_ch0001_*.md"))
        self.assertGreater(len(saved), 0, "Fact Digest 文件必须保存")

    def test_missing_section_returns_default(self):
        analysis_without_fact = "# 分析\n没有事实摘要区域\n"
        fd = self.sm.extract_fact_digest_from_analysis(
            analysis_without_fact, 3)
        self.assertEqual(fd.chapter_index, 3)
        # All fields should be empty (but not crash)
        self.assertEqual(fd.confirmed_items, "")
        self.assertEqual(fd.confirmed_events, "")

    def test_empty_subsections_returns_fd(self):
        """六个子节全为空时仍返回对象，不崩溃。"""
        analysis = "## 事实摘要\n### 确定的物品\n暂无\n### 确定的角色状态\n暂无\n"
        fd = self.sm.extract_fact_digest_from_analysis(analysis, 2)
        self.assertEqual(fd.chapter_index, 2)
        self.assertEqual(fd.confirmed_items.strip(), "暂无")


# ═══════════════════════════════════════════════════════════════
# E05-1: ClaudeStylist.edit_chapter — no side effects
# ═══════════════════════════════════════════════════════════════

class TestStylistEditChapterNoSideEffects(unittest.TestCase):
    """ClaudeStylist.edit_chapter: 只返回 styled text，不保存/不检查。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = get_settings()
        self._orig_data_dir = self.settings.data_dir
        self._orig_api_key = self.settings.api_key
        self.settings.data_dir = self.tmp
        self.settings.api_key = "test-key"
        # ClaudeStylist needs ANTHROPIC_API_KEY to be non-Claude (use DeepSeek)
        self._orig_anthropic = self.settings.anthropic_api_key
        self.settings.anthropic_api_key = ""

    def tearDown(self):
        self.settings.data_dir = self._orig_data_dir
        self.settings.api_key = self._orig_api_key
        self.settings.anthropic_api_key = self._orig_anthropic
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_edit_chapter_does_not_save(self):
        from src.agents.author.claude_stylist import ClaudeStylist

        stylist = ClaudeStylist("test_novel")

        with mock.patch.object(stylist, "_call_deepseek",
                               return_value="编辑后文本" * 30):
            with mock.patch.object(stylist.file_store, "save") as mock_save:
                result = stylist.edit_chapter(
                    "原始草稿。" * 30, chapter_index=1)

        # Must return styled text
        self.assertIn("编辑后文本", result)
        # Must NOT save
        mock_save.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# E05-5: FactDigest explicitly_absent round-trip
# ═══════════════════════════════════════════════════════════════

class TestFactDigestRoundTrip(unittest.TestCase):
    """FactDigest.from_markdown / to_markdown round-trip for explicitly_absent."""

    def test_explicit_absent_roundtrip(self):
        fd = FactDigest(chapter_index=5)
        fd.confirmed_items = "扳手"
        fd.confirmed_character_states = "柯林：健康"
        fd.confirmed_events = "出发"
        fd.confirmed_numbers = "2件"
        fd.explicitly_absent = "不曾提到水源"
        fd.pending_suspense = "目的地是什么"

        md = fd.to_markdown()
        restored = FactDigest.from_markdown(md)

        self.assertEqual(restored.chapter_index, 5)  # preserved in title
        self.assertEqual(restored.confirmed_items.strip(), "扳手")
        self.assertEqual(restored.explicitly_absent.strip(),
                         "不曾提到水源",
                         "明确未出现的内容 必须在 round-trip 中保留")

    def test_explicit_absent_from_old_format(self):
        """旧格式（无后缀）也能正确解析。"""
        old_format = """# 第1章 事实摘要

### 确定的物品
扳手

### 确定的角色状态
柯林

### 确定的事件
醒来

### 确定的数字/数据
无

### 明确未出现的内容
水源不存在

### 待解悬念
无
"""
        fd = FactDigest.from_markdown(old_format)
        self.assertIn("水源不存在", fd.explicitly_absent)


if __name__ == "__main__":
    unittest.main()
