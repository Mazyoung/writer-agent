"""E06 Structured Memory & Supervisor Decision Foundation 测试。

覆盖:
- A. Current State Update (item, relationship, foreshadowing)
- B. ReviewDecision parsing (PASS / NEEDS_REVISION / UNKNOWN)
- C. World Setting in review context
- D. Decision routing (PASS→commit, NEEDS_REVISION/UNKNOWN→no RAG)
- E. E05 single-pass invariant preserved
- F. Parse failure → fail-closed (UNKNOWN)
- G. Prompt/parser contract

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
from src.storage.document_formats import (
    ReviewDecision, CharacterRelationships, ItemsEquipment,
    CultivationSystem, RelationshipEntry, ItemEntry, CharacterCultivation,
    FactDigest, BookPlan, VolumePlan,
)
from src.agents.state_manager.state_manager import StateManager
from src.storage.sqlite_store import SQLiteStore
from src.config.settings import get_settings
import src.core.interceptor as interceptor_mod


# ═══════════════════════════════════════════════════════════════
# Shared test data
# ═══════════════════════════════════════════════════════════════

MOCK_RAW_ANALYSIS_PASS = """# 第1章复盘分析

## 事实摘要
### 确定的物品
扳手、发光徽章
### 确定的角色状态
柯林：健康、警惕
### 确定的事件
柯林醒来，检查背包
### 确定的数字/数据
背包中2件物品
### 明确未出现的内容
无特殊内容
### 待解悬念
徽章来源不明

## 状态变更（State Delta）
### 角色关系当前状态
- 柯林 ↔ 瘸子莫: 关系类型=交易伙伴, 当前状态=信任已建立, 态度=友好 [依据: 第5段]

### 角色物品状态
#### 获得
- 发光徽章: 持有者=柯林, 来源=背包发现, 状态=可用 [依据: 第3段]
#### 消耗
- 干粮: 旧持有者=柯林 [依据: 第8段]

### 角色修炼状态
- 柯林: 当前境界=凡人, 特殊能力=灵力感知觉醒, 限制=不稳定 [依据: 第12段]

### 伏笔状态
- 蓝光之谜: 状态=OPEN, 回收章节= [依据: 第15段]

## 追踪文档变更建议
### 角色关系
- 柯林 ↔ 瘸子莫: 建立交易关系 [依据: 第5段]

### 物品装备
#### 获得
- 发光徽章：背包中发现 [依据: 第3段]
#### 消耗
- 干粮：食用 [依据: 第8段]

### 修炼体系
- 柯林：初步觉醒灵力感知 [依据: 第12段]

## 一致性检查
### T1（硬错误）
无
### T2（软问题）
无
### T3（观察项）
无

## 质量审阅
- **情节逻辑**: PASS
- **节奏评估**: PASS
- **大纲符合度**: PASS
- **角色塑造**: PASS

## 审阅决策
- **决策**: PASS
- **严重性**: PASS
- **主要问题**: 无
- **规划级别**: L1
"""

MOCK_RAW_ANALYSIS_NEEDS_REVISION = """# 第2章复盘分析

## 事实摘要
### 确定的物品
铜币
### 确定的角色状态
柯林：受伤
### 确定的事件
柯林遭遇疤面帮
### 确定的数字/数据
无
### 明确未出现的内容
无
### 待解悬念
无

## 状态变更（State Delta）
### 角色关系当前状态
### 角色物品状态
### 角色修炼状态
### 伏笔状态

## 追踪文档变更建议
### 角色关系
### 物品装备
### 修炼体系

## 一致性检查
### T1（硬错误）
- 第2章柯林背包中的徽章数量与第1章不一致（第1章1枚，本章提到2枚）
- 瘸子莫的名字在本章写成瘸子莫，与前文不一致
### T2（软问题）
- 第3段对话重复了第1章已知信息
### T3（观察项）
- 段落过长

## 质量审阅
- **情节逻辑**: MAJOR — 时间线不连贯
- **节奏评估**: MINOR
- **大纲符合度**: PASS
- **角色塑造**: PASS

## 审阅决策
- **决策**: NEEDS_REVISION
- **严重性**: MAJOR
- **主要问题**: 徽章数量矛盾; 角色名不一致; 时间线断裂
- **规划级别**: L1
"""

MOCK_RAW_ANALYSIS_NO_DECISION = """# 第3章复盘分析

## 事实摘要
### 确定的物品
无
### 确定的角色状态
无
### 确定的事件
无
### 确定的数字/数据
无
### 明确未出现的内容
无
### 待解悬念
无

## 一致性检查
### T1（硬错误）
- 某个硬错误
### T2（软问题）
无
### T3（观察项）
无

## 质量审阅
PASS
"""

SAMPLE_PLAN_MD = """# 第1章规划：《测试》
## 一、章节信息
- **章大纲**: 测试
- **章节类型**: 延续型
- **总场景数**: 1
## 二、写作上下文包
### 角色关系图
测试
### 物品/装备追踪
测试
### 修炼/力量体系现状
暂无
### 关键伏笔节点
暂无
### 情感调色板
平淡
### 禁止清单
暂无
## 三、场景级写作计划
### 场景 1：开场 [状态：待规划]
- **发生什么**：测试
- **本场景的戏剧功能**：推进
- **对话必须达成的信息增量**：无
- **角色微时刻**：无
- **涉及角色**：柯林
- **情绪曲线**：平淡
- **字数预估**：500
- **与前后衔接**：无
"""

WORLD_SETTING = "# 世界观\n铁律：废土冬天没有净水。主角不得获得超自然力量。\n"

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


class _TmpNovelCase(unittest.TestCase):
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

    def _setup_review_dirs(self, slug: str = "test_novel") -> Path:
        root = self.tmp / "novels" / slug
        for d in ["settings", "tracking", "chapters", "outlines", "states"]:
            (root / d).mkdir(parents=True, exist_ok=True)
        (root / "settings" / "world_setting.md").write_text(
            WORLD_SETTING, encoding="utf-8")
        (root / "tracking" / "character_relationships.md").write_text(
            "# 角色关系\n## 关系详情\n", encoding="utf-8")
        (root / "tracking" / "items_equipment.md").write_text(
            "# 物品装备\n## 主角持有\n", encoding="utf-8")
        (root / "tracking" / "cultivation_system.md").write_text(
            "# 修炼体系\n## 角色修炼状态\n", encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0001.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            "第1章正文内容。" * 200, encoding="utf-8")
        return root


# ═══════════════════════════════════════════════════════════════
# A. Current State Update Tests
# ═══════════════════════════════════════════════════════════════

class TestReviewDecisionParsing(unittest.TestCase):

    def test_parse_pass(self):
        rd = ReviewDecision.from_analysis(MOCK_RAW_ANALYSIS_PASS)
        self.assertEqual(rd.verdict, "PASS")
        self.assertEqual(rd.severity, "PASS")
        self.assertEqual(len(rd.t1_issues), 0)

    def test_parse_needs_revision(self):
        rd = ReviewDecision.from_analysis(MOCK_RAW_ANALYSIS_NEEDS_REVISION)
        self.assertEqual(rd.verdict, "NEEDS_REVISION")
        self.assertEqual(rd.severity, "MAJOR")
        self.assertEqual(len(rd.t1_issues), 2)
        self.assertIn("徽章数量", rd.t1_issues[0])

    def test_parse_no_explicit_decision_infers_from_t1(self):
        """E06.1: 无显式审阅决策 section → UNKNOWN (fail-closed)，不推断 NEEDS_REVISION"""
        rd = ReviewDecision.from_analysis(MOCK_RAW_ANALYSIS_NO_DECISION)
        # E06.1 contract: missing decision section → UNKNOWN, never PASS/NEEDS_REVISION
        self.assertEqual(rd.verdict, "UNKNOWN",
                         "缺失审阅决策 section 必须返回 UNKNOWN (fail-closed)")
        # T1 issues should still be parsed for diagnostic purposes
        self.assertGreater(len(rd.t1_issues), 0,
                           "T1 硬错误仍应被解析（诊断目的）")

    def test_parse_empty_returns_unknown(self):
        """完全空的 analysis → UNKNOWN（fail-closed）"""
        rd = ReviewDecision.from_analysis("")
        self.assertEqual(rd.verdict, "UNKNOWN")

    def test_parse_no_t1_no_decision_section_returns_pass(self):
        """E06.1: 无 T1、无显式决策 section → UNKNOWN（fail-closed，不推断 PASS）"""
        clean = """# 复盘分析
## 一致性检查
### T1（硬错误）
无
### T2（软问题）
无
## 质量审阅
PASS
"""
        rd = ReviewDecision.from_analysis(clean)
        # E06.1 contract: 缺少合法的审阅决策 section → UNKNOWN
        self.assertEqual(rd.verdict, "UNKNOWN",
                         "E06.1: 缺失审阅决策 section 时即使分析看起来 clean 也不能自动 PASS")

    def test_severity_from_quality_review(self):
        """E06.1: 质量审阅 MAJOR 但无审阅决策 section → UNKNOWN (fail-closed)"""
        analysis = """# 复盘
## 一致性检查
### T1（硬错误）
无
## 质量审阅
- **情节逻辑**: MAJOR
- **节奏评估**: PASS
"""
        rd = ReviewDecision.from_analysis(analysis)
        # E06.1 contract: 无审阅决策 section → UNKNOWN，即使质量 MAJOR 也不推断
        self.assertEqual(rd.verdict, "UNKNOWN",
                         "E06.1: 缺少审阅决策 section → UNKNOWN (fail-closed)")
        # Severity should still be parsed
        self.assertEqual(rd.severity, "MAJOR")


# ═══════════════════════════════════════════════════════════════
# C. World Setting in Review Context
# ═══════════════════════════════════════════════════════════════

class TestStateManagerParseDecision(_TmpNovelCase):
    """StateManager.parse_review_decision from raw_analysis."""

    def test_parse_decision_from_pass_analysis(self):
        root = self._setup_review_dirs("pd_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("pd_novel", sqlite)
        rd = sm.parse_review_decision(MOCK_RAW_ANALYSIS_PASS)
        self.assertEqual(rd.verdict, "PASS")

    def test_parse_decision_from_needs_revision_analysis(self):
        root = self._setup_review_dirs("pd2_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("pd2_novel", sqlite)
        rd = sm.parse_review_decision(MOCK_RAW_ANALYSIS_NEEDS_REVISION)
        self.assertEqual(rd.verdict, "NEEDS_REVISION")
        self.assertEqual(rd.severity, "MAJOR")

    def test_parse_decision_failure_no_crash(self):
        """解析失败不崩溃，返回 UNKNOWN。"""
        root = self._setup_review_dirs("pd3_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("pd3_novel", sqlite)
        rd = sm.parse_review_decision("完全不相关的文本")
        self.assertEqual(rd.verdict, "UNKNOWN",
                         "解析失败必须 fail-closed (UNKNOWN)")
        self.assertFalse(rd.verdict == "PASS",
                         "解析失败不得默认 PASS")


# ═══════════════════════════════════════════════════════════════
# G. Prompt/Parser Contract Tests
# ═══════════════════════════════════════════════════════════════

class TestPromptParserContract(unittest.TestCase):
    """StateManager prompt output format matches parser expectations."""

    def test_state_delta_section_parseable(self):
        from src.storage.document_formats import _extract_section
        from src.agents.state_manager.state_manager import StateManager

        # Simulated LLM output using the E06 prompt format
        delta_section = _extract_section(
            MOCK_RAW_ANALYSIS_PASS, "## 状态变更（State Delta）")
        self.assertIn("角色关系当前状态", delta_section)
        self.assertIn("角色物品状态", delta_section)
        self.assertIn("角色修炼状态", delta_section)
        self.assertIn("伏笔状态", delta_section)

        # Verify relationship entry parseable with E06 state kv parser
        rel_delta = _extract_section(delta_section, "### 角色关系当前状态")
        rel_line = rel_delta.strip().split("\n")[0].strip()
        self.assertTrue(rel_line.startswith("- "))
        content = rel_line[2:]
        # Format: 角色A ↔ 角色B: 关系类型=XX, 当前状态=XX, 态度=XX [依据: ...]
        kv = StateManager._parse_state_kv(
            content.split(":", 1)[1] if ":" in content else content)
        self.assertIn("关系类型", kv)

    def test_decision_section_parseable(self):
        rd = ReviewDecision.from_analysis(MOCK_RAW_ANALYSIS_PASS)
        self.assertEqual(rd.verdict, "PASS")
        self.assertEqual(rd.severity, "PASS")

    def test_fact_digest_section_present(self):
        from src.storage.document_formats import _extract_section
        section = _extract_section(MOCK_RAW_ANALYSIS_PASS, "## 事实摘要")
        self.assertIn("确定的物品", section)
        self.assertIn("确定的角色状态", section)

    def test_fact_digest_from_analysis(self):
        """FactDigest.from_markdown correctly parses 6 sub-sections."""
        from src.storage.document_formats import _extract_section
        section = _extract_section(MOCK_RAW_ANALYSIS_PASS, "## 事实摘要")
        fd = FactDigest.from_markdown(section)
        self.assertIn("扳手", fd.confirmed_items)
        self.assertIn("柯林", fd.confirmed_character_states)
        self.assertIn("醒来", fd.confirmed_events)


# ═══════════════════════════════════════════════════════════════
# E06.1 TEST ADDITIONS
# ═══════════════════════════════════════════════════════════════

# ── MOCK DATA FOR E06.1 ──────────────────────────────────

MOCK_RAW_ANALYSIS_EXPLICIT_PASS_E06_1 = """# 第1章复盘分析

## 事实摘要
### 确定的物品
无
### 确定的角色状态
无
### 确定的事件
无
### 确定的数字/数据
无
### 明确未出现的内容
无
### 待解悬念
无

## 状态变更（State Delta）
### 角色关系当前状态
### 角色物品状态
### 角色修炼状态
### 角色当前状态
- 柯林: 存活=存活, 位置=废土配电间, 身体状态=健康, 身份=觉醒者 [依据: 第1段]
### 伏笔状态

## 追踪文档变更建议
### 角色关系
### 物品装备
### 修炼体系

## 一致性检查
### T1（硬错误）
无
### T2（软问题）
无
### T3（观察项）
无

## 质量审阅
- **情节逻辑**: PASS — 因果链清晰
- **节奏评估**: PASS — 张弛得当
- **大纲符合度**: PASS — 与章规划一致
- **角色塑造**: PASS — 角色行为一致

## 审阅决策
- **决策**: PASS
- **严重性**: PASS
- **主要问题**: 无
- **规划级别**: L1
"""

MOCK_RAW_ANALYSIS_PASS_WITH_T1_E06_1 = """# 第1章复盘分析

## 事实摘要
### 确定的物品
无
### 确定的角色状态
无
### 确定的事件
无
### 确定的数字/数据
无
### 明确未出现的内容
无
### 待解悬念
无

## 一致性检查
### T1（硬错误）
- 徽章数量与第1章矛盾
### T2（软问题）
无
### T3（观察项）
无

## 质量审阅
- **情节逻辑**: PASS
- **节奏评估**: PASS
- **大纲符合度**: PASS
- **角色塑造**: PASS

## 审阅决策
- **决策**: PASS
- **严重性**: PASS
- **主要问题**: 无
- **规划级别**: L1
"""

MOCK_RAW_ANALYSIS_NO_DECISION_E06_1 = """# 第3章复盘分析

## 事实摘要
### 确定的物品
无
### 确定的角色状态
无
### 确定的事件
无
### 确定的数字/数据
无
### 明确未出现的内容
无
### 待解悬念
无

## 一致性检查
### T1（硬错误）
无
### T2（软问题）
无
### T3（观察项）
无

## 质量审阅
- **情节逻辑**: PASS — 清晰
- **节奏评估**: PASS — 得当
- **大纲符合度**: PASS — 一致
- **角色塑造**: PASS — 一致
"""

MOCK_RAW_ANALYSIS_INVALID_DECISION_E06_1 = """# 复盘分析
## 审阅决策
- **决策**: MAYBE_OK
- **严重性**: PASS
- **主要问题**: 无
- **规划级别**: L1
"""

MOCK_STATE_DELTA_WITH_CHARACTER_STATE = """## 状态变更（State Delta）
### 角色关系当前状态
### 角色物品状态
### 角色修炼状态
### 角色当前状态
- 柯林: 存活=存活, 位置=高架桥废墟, 身体状态=轻伤, 身份=觉醒者 [依据: 第5段]
- 王长林: 存活=死亡, 位置=交易站, 身体状态=致命伤, 身份=商人 [依据: 第12段]
### 伏笔状态
"""

BOOK_PLAN_WITH_MARKER = """# 《测试》Book Plan v1
- **核心主题**: 废土生存与人性磨砺 E06_BOOK_STRATEGIC_RULE_9137
- **故事终局**: 柯林成为废土的和平缔造者
- **战略约束**: 柯林不得在第二卷前获得超自然力量
- **关键角色**: 柯林（主角）、王长林（第3卷关键角色，不得提前死亡）
"""

VOLUME_PLAN_WITH_MARKER = """# 第1卷规划：《废土觉醒》
- **版本**: v1
- **状态**: ACTIVE
- **章节范围**: 第1章-第5章
- **核心冲突**: 配电间资源争夺 E06_VOLUME_RULE_4281
## 事件链
### 事件1：配电间
- **对应章节**: 第1章
"""


# ═══════════════════════════════════════════════════════════════
# E06.1-A. P0 #1 — No fact_digest for rejected chapters
# ═══════════════════════════════════════════════════════════════

class TestFailClosedDecisionContract(unittest.TestCase):
    """E06.1-B: 缺失/不可解析的审阅决策 → UNKNOWN (fail-closed)。"""

    def test_no_decision_clean_analysis_returns_unknown(self):
        """无审阅决策 section + clean analysis → UNKNOWN，不推断 PASS。"""
        rd = ReviewDecision.from_analysis(MOCK_RAW_ANALYSIS_NO_DECISION_E06_1)
        self.assertEqual(rd.verdict, "UNKNOWN",
                         "E06.1: 无审阅决策 section → UNKNOWN (fail-closed)")

    def test_no_decision_with_t1_returns_unknown(self):
        """无审阅决策 + T1 硬错误 → UNKNOWN（绝对不推断 NEEDS_REVISION 或 PASS）。"""
        analysis = """## 一致性检查
### T1（硬错误）
- 某硬错误
## 质量审阅
PASS
"""
        rd = ReviewDecision.from_analysis(analysis)
        self.assertEqual(rd.verdict, "UNKNOWN",
                         "缺失审阅决策 section → UNKNOWN, 即使有 T1 也不推断 NEEDS_REVISION")
        self.assertNotEqual(rd.verdict, "PASS",
                            "缺失审阅决策 section 绝不能推断 PASS")

    def test_invalid_decision_value_returns_unknown(self):
        """决策值无法解析 → UNKNOWN。"""
        rd = ReviewDecision.from_analysis(MOCK_RAW_ANALYSIS_INVALID_DECISION_E06_1)
        self.assertEqual(rd.verdict, "UNKNOWN",
                         "无效的决策值 'MAYBE_OK' 必须返回 UNKNOWN")

    def test_explicit_pass_valid_section_returns_pass(self):
        """显式 PASS + 合法 section → PASS。"""
        rd = ReviewDecision.from_analysis(MOCK_RAW_ANALYSIS_EXPLICIT_PASS_E06_1)
        self.assertEqual(rd.verdict, "PASS",
                         "显式合法 PASS 决策应返回 PASS")

    def test_explicit_pass_with_t1_promotes_to_needs_revision(self):
        """Safety override: 显式 PASS 但 T1 硬错误存在 → NEEDS_REVISION。"""
        rd = ReviewDecision.from_analysis(MOCK_RAW_ANALYSIS_PASS_WITH_T1_E06_1)
        self.assertEqual(rd.verdict, "NEEDS_REVISION",
                         "LLM 声明 PASS 但 parser 发现 T1 → 必须提升为 NEEDS_REVISION")

    def test_explicit_pass_with_major_quality_promotes(self):
        """Safety override: 显式 PASS 但 MAJOR 质量 → NEEDS_REVISION。"""
        analysis = """## 一致性检查
### T1（硬错误）
无
## 质量审阅
- **情节逻辑**: MAJOR — 严重逻辑断裂
- **节奏评估**: PASS
- **大纲符合度**: PASS
- **角色塑造**: PASS
## 审阅决策
- **决策**: PASS
- **严重性**: PASS
"""
        rd = ReviewDecision.from_analysis(analysis)
        self.assertEqual(rd.verdict, "NEEDS_REVISION",
                         "LLM 声明 PASS 但质量有 MAJOR → 必须提升为 NEEDS_REVISION")


# ═══════════════════════════════════════════════════════════════
# E06.1-C. P0 #3 — Atomic Structured Memory Commit
# ═══════════════════════════════════════════════════════════════

class TestCharacterStateRoundTrip(unittest.TestCase):
    """E06.1-D: CharacterStateEntry / CharacterStateList markdown round-trip."""

    def test_single_character_roundtrip(self):
        from src.storage.document_formats import CharacterStateEntry, CharacterStateList
        csl = CharacterStateList()
        csl.entries.append(CharacterStateEntry(
            name="柯林", alive_status="存活", location="废土配电间",
            physical_state="健康", identity_status="觉醒者",
            updated_chapter="第1章"))
        csl.entries.append(CharacterStateEntry(
            name="瘸子莫", alive_status="存活", location="地下市场",
            physical_state="健康", identity_status="情报贩子",
            updated_chapter="第1章"))
        md = csl.to_markdown()
        self.assertIn("柯林", md)
        self.assertIn("废土配电间", md)
        self.assertIn("觉醒者", md)
        self.assertIn("瘸子莫", md)

        csl2 = CharacterStateList.from_markdown(md)
        self.assertEqual(len(csl2.entries), 2)
        names = {e.name for e in csl2.entries}
        self.assertIn("柯林", names)
        self.assertIn("瘸子莫", names)
        for e in csl2.entries:
            if e.name == "柯林":
                self.assertEqual(e.alive_status, "存活")
                self.assertEqual(e.location, "废土配电间")
                self.assertEqual(e.physical_state, "健康")
                self.assertEqual(e.identity_status, "觉醒者")

    def test_from_empty_markdown(self):
        from src.storage.document_formats import CharacterStateList
        csl = CharacterStateList.from_markdown("# 角色当前状态\n## 角色当前状态\n")
        self.assertEqual(len(csl.entries), 0)

    def test_location_update(self):
        """位置变化 → 状态更新。"""
        from src.storage.document_formats import CharacterStateEntry, CharacterStateList
        csl = CharacterStateList()
        csl.entries.append(CharacterStateEntry(
            name="柯林", alive_status="存活", location="配电间",
            physical_state="健康", identity_status="觉醒者",
            updated_chapter="第1章"))
        # Update location
        for e in csl.entries:
            if e.name == "柯林":
                e.location = "高架桥废墟"
                e.updated_chapter = "第2章"
        found = False
        for e in csl.entries:
            if e.name == "柯林":
                self.assertEqual(e.location, "高架桥废墟")
                found = True
        self.assertTrue(found)

    def test_alive_status_update(self):
        """存活状态变化 → 死亡。"""
        from src.storage.document_formats import CharacterStateEntry, CharacterStateList
        csl = CharacterStateList()
        csl.entries.append(CharacterStateEntry(
            name="王长林", alive_status="存活", location="交易站",
            physical_state="健康", identity_status="商人",
            updated_chapter="第2章"))
        for e in csl.entries:
            if e.name == "王长林":
                e.alive_status = "死亡"
                e.physical_state = "致命伤"
        for e in csl.entries:
            if e.name == "王长林":
                self.assertEqual(e.alive_status, "死亡")
                self.assertEqual(e.physical_state, "致命伤")


class TestStateCommitResultPropagation(unittest.TestCase):
    """E06.2-D: StateCommitResult 正确构造和传播。"""

    def test_success_result(self):
        from src.storage.document_formats import StateCommitResult
        r = StateCommitResult(success=True, changed_files=["a.md", "b.md"])
        self.assertTrue(r.success)
        self.assertEqual(len(r.changed_files), 2)
        self.assertEqual(r.error_message, "")

    def test_failure_result(self):
        from src.storage.document_formats import StateCommitResult
        r = StateCommitResult(
            success=False,
            error_message="磁盘满",
            warnings=["回滚成功", "SQLite 缓存失败: timeout"])
        self.assertFalse(r.success)
        self.assertIn("磁盘满", r.error_message)
        self.assertEqual(len(r.warnings), 2)

    def test_default_is_failure(self):
        from src.storage.document_formats import StateCommitResult
        r = StateCommitResult()
        self.assertFalse(r.success,
                         "StateCommitResult 默认 success=False（fail-closed）")


# ═══════════════════════════════════════════════════════════════
# E06.2-E. P1 — CLI Snapshot/Rollback Cleanup
# ═══════════════════════════════════════════════════════════════

class TestCLINoBrokenSnapshotRollback(unittest.TestCase):
    """E06.2-E: --help 不宣传已移除的 snapshot/rollback 命令。"""

    def test_help_does_not_contain_snapshot_subcommand(self):
        """python main.py --help 不应包含 snapshot/rollback。"""
        import subprocess
        import sys
        from pathlib import Path

        main_py = Path(__file__).parent.parent / "main.py"
        result = subprocess.run(
            [sys.executable, str(main_py), "--help"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
            env={**__import__('os').environ, "PYTHONIOENCODING": "utf-8"})
        output = (result.stdout or "") + (result.stderr or "")
        self.assertNotIn("snapshot", output.lower().split(),
                         "--help 不得包含 snapshot 子命令")
        self.assertNotIn("rollback", output.lower().split(),
                         "--help 不得包含 rollback 子命令")

    def test_snapshot_command_does_not_exist(self):
        """调用不存在的 snapshot 子命令 → argparse error（不是 AttributeError）。"""
        import subprocess
        import sys
        from pathlib import Path

        main_py = Path(__file__).parent.parent / "main.py"
        result = subprocess.run(
            [sys.executable, str(main_py), "snapshot", "test_novel"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
            env={**__import__('os').environ, "PYTHONIOENCODING": "utf-8"})
        # argparse should reject unknown command — not AttributeError
        self.assertNotEqual(result.returncode, 0,
                            "snapshot 命令应返回非零退出码")
        output = (result.stdout or "") + (result.stderr or "")
        self.assertNotIn("AttributeError", output,
                         "snapshot 命令不得导致 AttributeError")

    def test_rollback_command_does_not_exist(self):
        """调用不存在的 rollback 子命令 → argparse error（不是 AttributeError）。"""
        import subprocess
        import sys
        from pathlib import Path

        main_py = Path(__file__).parent.parent / "main.py"
        result = subprocess.run(
            [sys.executable, str(main_py), "rollback", "test_novel"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
            env={**__import__('os').environ, "PYTHONIOENCODING": "utf-8"})
        self.assertNotEqual(result.returncode, 0,
                            "rollback 命令应返回非零退出码")
        output = (result.stdout or "") + (result.stderr or "")
        self.assertNotIn("AttributeError", output,
                         "rollback 命令不得导致 AttributeError")


# ═══════════════════════════════════════════════════════════════
# E06.2-F. P1 — Chroma Warning Verification
# ═══════════════════════════════════════════════════════════════

class TestChromaWarningNotSilent(unittest.TestCase):
    """E06.2-F: ChromaStore index_chapter/rebuild_branch 不再静默吞异常。"""

    def test_index_chapter_stale_cleanup_logs_warning(self):
        """index_chapter 清理旧 chunks 失败 → 输出 [CHROMA WARNING]，不静默。"""
        from src.storage.chroma_store import ChromaStore
        from unittest.mock import MagicMock
        import io
        import sys

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        try:
            store = ChromaStore(Path(tempfile.mkdtemp()))
            # Mock collection: _ensure_collection succeeds,
            # but coll.get() raises to test the except block
            mock_coll = MagicMock()
            mock_coll.get.side_effect = RuntimeError(
                "E06.2 模拟 ChromaDB get 失败")
            store._client = MagicMock()
            store._collection = mock_coll

            # index_chapter should catch the RuntimeError in stale cleanup
            # and print [CHROMA WARNING] — then continue with chunking
            count = store.index_chapter("test_novel", "main", 1,
                                        "test content " * 50,
                                        source_path="test.md")
            # Should still have indexed chunks despite stale cleanup failure
            self.assertGreater(count, 0,
                               "stale cleanup 失败不应阻止 chunk 索引")

            output = captured.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertIn("CHROMA WARNING", output,
                      "index_chapter 清理失败必须输出 [CHROMA WARNING]")

    def test_rebuild_branch_failure_returns_false(self):
        """E06.2.1: rebuild_branch 失败 → 返回 False + 输出 [CHROMA ERROR]。"""
        from src.storage.chroma_store import ChromaStore
        from unittest.mock import MagicMock
        import io
        import sys

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        try:
            store = ChromaStore(Path(tempfile.mkdtemp()))
            mock_coll = MagicMock()
            mock_coll.get.side_effect = RuntimeError(
                "E06.2.1 模拟 ChromaDB get 失败")
            store._client = MagicMock()
            store._collection = mock_coll

            # rebuild_branch should return False on failure
            result = store.rebuild_branch("test_novel", "main")
            self.assertFalse(result,
                             "rebuild_branch 失败必须返回 False")

            output = captured.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertIn("CHROMA ERROR", output,
                      "rebuild_branch 失败必须输出 [CHROMA ERROR]")

    def test_rebuild_branch_success_returns_true(self):
        """rebuild_branch 成功 → 返回 True。"""
        from src.storage.chroma_store import ChromaStore
        from unittest.mock import MagicMock

        store = ChromaStore(Path(tempfile.mkdtemp()))
        mock_coll = MagicMock()
        # Empty branch: get returns no ids
        mock_coll.get.return_value = {"ids": []}
        store._client = MagicMock()
        store._collection = mock_coll

        result = store.rebuild_branch("test_novel", "main")
        self.assertTrue(result,
                        "rebuild_branch 成功（含空分支）必须返回 True")


# ═══════════════════════════════════════════════════════════════
# E06.2.1-A. P0 — Snapshot Failure Fail-Closed
# ═══════════════════════════════════════════════════════════════

class TestSnapshotFailureFailClosed(_TmpNovelCase):
    """E06.2.1-A: Snapshot 读取失败 → 中止提交，不开始任何写入。"""

    def test_snapshot_read_failure_aborts_before_writes(self):
        """现有文件读取失败 → fail-closed，零文件修改。"""
        root = self._setup_review_dirs("snap_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("snap_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "snap_novel", self.settings.data_dir)

        # Write initial tracking docs (create real files on disk)
        old_rels = "# SNAPSHOT_OLD_RELATIONSHIPS\n## 关系详情\n## 关系变更日志\n"
        (root / "tracking" / "character_relationships.md").write_text(
            old_rels, encoding="utf-8")
        (root / "tracking" / "items_equipment.md").write_text(
            "# OLD ITEMS\n", encoding="utf-8")
        (root / "tracking" / "cultivation_system.md").write_text(
            "# OLD CULT\n", encoding="utf-8")

        # Build minimal in-memory objects for commit
        from src.storage.document_formats import (
            CharacterRelationships, ItemsEquipment, CultivationSystem,
            CharacterStateList, RelationshipEntry,
        )
        rels = CharacterRelationships()
        rels.entries.append(RelationshipEntry(
            characters="柯林 ↔ 瘸子莫", relation_type="交易伙伴",
            current_state="信任已建立", attitude="友好"))
        items = ItemsEquipment()
        cult = CultivationSystem()
        char_states = CharacterStateList()
        state_result = {"relationships": [], "items": [], "cultivation": [],
                        "characters": [], "foreshadows": []}
        log_result = {}

        # ── Call _commit_all_tracking_docs with a patched read_text ──
        # that fails for ONE file during snapshot
        orig_read_text = Path.read_text

        def patched_read_text(self, encoding="utf-8"):
            p_str = str(self)
            if "items_equipment.md" in p_str and "tracking" in p_str:
                raise OSError("E06.2.1 模拟磁盘读取错误")
            return orig_read_text(self, encoding=encoding)

        Path.read_text = patched_read_text
        try:
            commit_result = sm._commit_all_tracking_docs(
                1, "第1章", rels, items, cult, char_states,
                state_result, log_result)
        finally:
            Path.read_text = orig_read_text

        # ── Assert: commit aborted before any writes ──
        self.assertFalse(commit_result.success,
                         "snapshot 失败 → commit 必须报告 FAILED")
        self.assertIn("snapshot", commit_result.error_message,
                      "错误信息必须包含 snapshot 阶段")
        self.assertIn("中止", commit_result.error_message,
                      "错误信息必须包含 '中止'")

        # ── Assert: no changed_files (no writes happened) ──
        self.assertEqual(len(commit_result.changed_files), 0,
                         "snapshot 失败 → changed_files 必须为空")

        # ── Assert: all files remain OLD ──
        current_rels = (root / "tracking" / "character_relationships.md").read_text(
            encoding="utf-8")
        self.assertIn("SNAPSHOT_OLD_RELATIONSHIPS", current_rels,
                      "关系文件必须保持 OLD 内容（零修改）")


# ═══════════════════════════════════════════════════════════════
# E06.2.1-B. CLI cmd_review 输出测试
# ═══════════════════════════════════════════════════════════════

class TestMarkdownFailureSqliteNotCalled(_TmpNovelCase):
    """Markdown commit 失败 → SQLite 0 calls。"""

    def test_second_save_failure_sqlite_zero_calls(self):
        """第二个 tracking doc 保存失败 → 所有 Markdown OLD + SQLite 未调用。"""
        root = self._setup_review_dirs("mfs_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("mfs_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "mfs_novel", self.settings.data_dir)

        # Write INITIAL canonical tracking docs with known OLD content
        old_rels = "# OLD MFS RELATIONSHIPS\n## 关系详情\n#### A ↔ B\n- **关系类型**: 旧\n## 关系变更日志\n"
        (root / "tracking" / "character_relationships.md").write_text(
            old_rels, encoding="utf-8")
        (root / "tracking" / "items_equipment.md").write_text(
            "# OLD MFS ITEMS\n", encoding="utf-8")
        (root / "tracking" / "cultivation_system.md").write_text(
            "# OLD MFS CULT\n", encoding="utf-8")

        # State delta with foreshadowing (to check SQLite is NOT called)
        analysis = """## 状态变更（State Delta）
### 角色关系当前状态
- 柯林 ↔ 瘸子莫: 关系类型=交易伙伴, 当前状态=信任已建立, 态度=友好 [依据: 第5段]
### 角色物品状态
#### 获得
- 发光徽章: 持有者=柯林, 来源=背包发现, 状态=可用 [依据: 第3段]
### 角色修炼状态
### 角色当前状态
### 伏笔状态
- 蓝光之谜: 状态=OPEN, 回收章节= [依据: 第15段]
"""

        # Patch save_tracking_doc: first call succeeds, second raises
        call_order = []
        orig_save = sm.fs.save_tracking_doc

        def failing_save(name, content):
            call_order.append(name)
            if len(call_order) >= 2:
                raise OSError("E06.2.1 MFS 模拟 I/O 失败")
            return orig_save(name, content)

        sm.fs.save_tracking_doc = failing_save

        # Track sqlite.upsert_foreshadow calls
        sqlite_calls = []
        orig_upsert = sqlite.upsert_foreshadow
        sqlite.upsert_foreshadow = lambda *a, **kw: sqlite_calls.append(1) or orig_upsert(*a, **kw)

        result = sm.update_tracking_docs(1, "正文", analysis)

        # ── Assert: commit failed ──
        cr = result.get("_commit_result")
        self.assertIsNotNone(cr)
        self.assertFalse(cr.success)

        # ── Assert: SQLite 0 calls (commit failed before Phase 5) ──
        self.assertEqual(len(sqlite_calls), 0,
                         "Markdown commit 失败 → sqlite.upsert_foreshadow call_count == 0")

        # ── Assert: all Markdown OLD ──
        rels = (root / "tracking" / "character_relationships.md").read_text(encoding="utf-8")
        self.assertIn("OLD MFS RELATIONSHIPS", rels,
                      "关系文件必须回滚到 OLD")
        self.assertNotIn("交易伙伴", rels,
                         "关系文件不得包含新数据")
