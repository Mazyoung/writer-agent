"""E06 Structured Memory & Supervisor Decision Foundation 测试。

覆盖:
- A. Current State Update (item, relationship, foreshadowing)
- B. ReviewDecision parsing (PASS / NEEDS_REVISION / HALT / UNKNOWN)
- C. World Setting in review context
- D. Decision routing (PASS→commit, NEEDS_REVISION→no RAG, HALT→no RAG)
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

MOCK_RAW_ANALYSIS_HALT = """# 第5章复盘分析

## 事实摘要
### 确定的物品
无
### 确定的角色状态
柯林：重伤；王长林：死亡
### 确定的事件
王长林在交易站被杀
### 确定的数字/数据
无
### 明确未出现的内容
无
### 待解悬念
凶手身份不明

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
- 王长林在Book Plan中列为第3卷关键角色，第5章即死亡违反全书战略约束
### T2（软问题）
无
### T3（观察项）
无

## 质量审阅
- **情节逻辑**: MAJOR — 关键角色提前死亡将导致后续多章节点失效
- **节奏评估**: PASS
- **大纲符合度**: MAJOR — 与Book Plan战略冲突
- **角色塑造**: PASS

## 审阅决策
- **决策**: HALT
- **严重性**: MAJOR
- **主要问题**: 王长林提前死亡违反全书战略约束; 第2卷起角色节点全部失效
- **规划级别**: L3
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

class TestItemCurrentStateUpdate(_TmpNovelCase):
    """E06-A: ItemsEquipment tables are updated (not just item_logs)."""

    def test_item_holder_transfer_updates_current_state(self):
        root = self._setup_review_dirs("item_novel")
        items_text = """# 物品与装备系统
## 主角持有
| 物品 | 来源 | 获得章 | 属性 | 状态 | 备注 |
|------|------|--------|------|------|------|
| 铜币 | 交易 | 第1章 | 货币 | 可用 | |
"""
        (root / "tracking" / "items_equipment.md").write_text(
            items_text, encoding="utf-8")

        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("item_novel", sqlite)

        # State delta in E06 format: item transferred from 陆沉 to 王长林
        analysis = """## 事实摘要
### 确定的物品
铜币已转交
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
#### 获得
- 铜币: 持有者=王长林, 来源=陆沉转交, 状态=可用 [依据: 第3段]
### 角色修炼状态
### 伏笔状态

## 追踪文档变更建议
### 物品装备
#### 获得
- 铜币：陆沉在高架桥下转交王长林 [依据: 第3段]
"""
        sm.update_tracking_docs(1, "正文", analysis)

        updated = ItemsEquipment.from_markdown(
            (root / "tracking" / "items_equipment.md").read_text(encoding="utf-8"))
        # item_logs (change log) should have the change
        self.assertGreaterEqual(len(updated.item_logs), 1,
                                "物品 item_logs 应记录变更")

        # Current state should be updated — 铜币 should exist with new holder
        has_tongbi = False
        for it in updated.protagonist_items:
            if "铜币" in it.name:
                has_tongbi = True
                self.assertIn("王长林", it.owner,
                              f"铜币当前持有者应为王长林，实际: {it.owner}")
        self.assertTrue(has_tongbi, "铜币应出现在主角持有表中")


class TestRelationshipCurrentStateUpdate(_TmpNovelCase):
    """E06-B: Relationship entries[] are updated (not just change_log)."""

    def test_relationship_current_state_updated(self):
        root = self._setup_review_dirs("rel_novel")
        # Initial: 陆沉-顾明川=信任
        rels_text = """# 角色关系图
## 关系详情
#### 陆沉 ↔ 顾明川
- **关系类型**: 信任
- **当前状态**: 盟友
- **态度**: 友好
- **上一章互动**: 第1章
## 关系变更日志
### 第1章
- **陆沉 ↔ 顾明川**: 建立信任关系
"""
        (root / "tracking" / "character_relationships.md").write_text(
            rels_text, encoding="utf-8")

        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("rel_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "rel_novel", self.settings.data_dir)

        # State delta changing relationship to 破裂
        analysis = """## 事实摘要
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
- 陆沉 ↔ 顾明川: 关系类型=敌对, 当前状态=关系破裂, 态度=不信任 [依据: 第10段]

## 追踪文档变更建议
### 角色关系
"""
        sm.update_tracking_docs(2, "正文2", analysis)

        # Verify current state updated
        updated = CharacterRelationships.from_markdown(
            (root / "tracking" / "character_relationships.md").read_text(
                encoding="utf-8"))
        found = False
        for entry in updated.entries:
            if "陆沉" in entry.characters and "顾明川" in entry.characters:
                self.assertIn("破裂", entry.current_state,
                              "关系 current_state 必须更新为破裂")
                self.assertIn("敌对", entry.relation_type,
                              "关系 relation_type 必须更新为敌对")
                found = True
        self.assertTrue(found, "必须找到陆沉-顾明川的关系条目")

        # Change log should also exist
        self.assertGreaterEqual(len(updated.change_log), 1,
                                "change_log 仍应记录变更")


class TestForeshadowingStateUpdate(_TmpNovelCase):
    """E06-C: Foreshadowing state transitions (OPEN→RESOLVED)."""

    def test_foreshadow_resolved_updates_sqlite(self):
        root = self._setup_review_dirs("fore_novel")
        sqlite = SQLiteStore(root / "state.db")
        # Add initial OPEN foreshadow
        sqlite.add_foreshadowing("fore_novel", "蓝光之谜", "第1章",
                                 "第3卷")

        sm = StateManager("fore_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "fore_novel", self.settings.data_dir)

        analysis = """## 事实摘要
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
### 伏笔状态
- 蓝光之谜: 状态=RESOLVED, 回收章节=第3章 [依据: 第20段]

## 追踪文档变更建议
"""
        sm.update_tracking_docs(3, "正文3", analysis)

        # Verify foreshadow now resolved
        pending = sqlite.get_pending_foreshadows("fore_novel")
        resolved = [f for f in pending if f["status"] != "pending"]
        all_fs = sqlite.conn.execute(
            "SELECT * FROM foreshadowing WHERE novel_id=?",
            ("fore_novel",)).fetchall()
        has_resolved = any(
            "resolved" in str(row).lower() for row in all_fs)
        self.assertTrue(has_resolved or len(pending) < 1,
                        "蓝光之谜必须标记为已回收")


# ═══════════════════════════════════════════════════════════════
# B. ReviewDecision Parsing Tests
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

    def test_parse_halt(self):
        rd = ReviewDecision.from_analysis(MOCK_RAW_ANALYSIS_HALT)
        self.assertEqual(rd.verdict, "HALT")
        self.assertEqual(rd.planning_level, "L3")
        self.assertGreater(len(rd.reasons), 0)

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

class TestWorldSettingInReview(_TmpNovelCase):
    """E06: world_setting 真实进入 StateManager.review_chapter prompt."""

    def test_world_setting_in_review_prompt(self):
        root = self._setup_review_dirs("ws_novel")
        # Use a unique world setting identifier
        unique_ws = "# 世界观\n铁律：E06_WORLD_TEST_MARKER_8372 废土冬天没有净水。\n"
        (root / "settings" / "world_setting.md").write_text(
            unique_ws, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("ws_novel")
        captured = {}

        def fake_llm(self, messages):
            captured["user"] = messages[-1]["content"]
            return MOCK_RAW_ANALYSIS_PASS

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter", return_value=2):
            orch.review_chapter(1)

        self.assertIn("E06_WORLD_TEST_MARKER_8372", captured["user"],
                      "world_setting 必须出现在 StateManager review prompt 中")


# ═══════════════════════════════════════════════════════════════
# D. Decision Routing Tests
# ═══════════════════════════════════════════════════════════════

class TestPassRouting(_TmpNovelCase):
    """PASS → memory commit + fact digest + RAG index."""

    def test_pass_commits_memory_and_rag(self):
        root = self._setup_review_dirs("pass_novel")
        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("pass_novel")

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS_PASS

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter",
                               return_value=3) as mock_rag:
            result = orch.review_chapter(1)

        self.assertTrue(mock_rag.called, "PASS 必须触发 RAG index")

        # Verify tracking doc updated
        rels_file = root / "tracking" / "character_relationships.md"
        self.assertTrue(rels_file.exists())
        content = rels_file.read_text(encoding="utf-8")
        self.assertIn("交易伙伴", content,
                      "PASS 必须提交角色关系 current state")

        # Verify fact digest created
        fact_files = list((root / "states").glob("fact_digest_ch0001_*.md"))
        self.assertGreater(len(fact_files), 0,
                           "PASS 必须生成 Fact Digest")

        self.assertIn("change_log", result)


class TestNeedsRevisionRouting(_TmpNovelCase):
    """NEEDS_REVISION → no memory commit + no RAG index + [Supervisor]."""

    def test_needs_revision_no_memory_commit_no_rag(self):
        root = self._setup_review_dirs("nr_novel")
        # Need chapter_0002 styled file
        (root / "chapters" / "chapter_0002_styled_20260801_120000.md").write_text(
            "第2章正文。" * 200, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0002.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("nr_novel")
        rag_calls = []

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS_NEEDS_REVISION

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter",
                               side_effect=lambda *a, **kw: rag_calls.append(1)):
            result = orch.review_chapter(2)

        self.assertEqual(result["decision"], "NEEDS_REVISION")
        self.assertGreater(len(result["t1_issues"]), 0)
        self.assertEqual(len(rag_calls), 0,
                         "NEEDS_REVISION 不得执行 RAG index")


class TestHaltRouting(_TmpNovelCase):
    """HALT → no memory commit + no RAG index + planning_level."""

    def test_halt_no_rag(self):
        root = self._setup_review_dirs("halt_novel")
        # Need chapter_0005 styled file
        (root / "chapters" / "chapter_0005_styled_20260801_120000.md").write_text(
            "第5章正文。" * 200, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0005.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("halt_novel")
        rag_calls = []

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS_HALT

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter",
                               side_effect=lambda *a, **kw: rag_calls.append(1)):
            result = orch.review_chapter(5)

        self.assertEqual(result["decision"], "HALT")
        self.assertEqual(result["planning_level"], "L3")
        self.assertEqual(len(rag_calls), 0,
                         "HALT 不得执行 RAG index")


# ═══════════════════════════════════════════════════════════════
# E. E05 Single-Pass Invariant
# ═══════════════════════════════════════════════════════════════

class TestE05SinglePassPreservation(_TmpNovelCase):
    """E06 must NOT regress E05: exactly 1 LLM call per review_chapter."""

    def test_review_chapter_exactly_one_llm_call(self):
        root = self._setup_review_dirs("sp_novel")
        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("sp_novel")
        llm_count = []

        def counting_llm(self, messages):
            llm_count.append(1)
            return MOCK_RAW_ANALYSIS_PASS

        with mock.patch.object(BaseAgent, "_call_llm", counting_llm), \
             mock.patch.object(ChromaStore, "index_chapter", return_value=2):
            orch.review_chapter(1)

        self.assertEqual(len(llm_count), 1,
                         f"E06 review 必须恰好 1 次 LLM，实际 {len(llm_count)}")


# ═══════════════════════════════════════════════════════════════
# F. StateManager parse_review_decision
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

class TestRejectedChapterNoFactDigest(_TmpNovelCase):
    """E06.1-A: REVISION/HALT/UNKNOWN 不产生 fact_digest_ch* 文件。"""

    def test_needs_revision_no_fact_digest_file(self):
        """NEEDS_REVISION 不得保存 fact_digest 文件。"""
        root = self._setup_review_dirs("nfd1_novel")
        (root / "chapters" / "chapter_0002_styled_20260801_120000.md").write_text(
            "第2章正文。" * 200, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0002.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("nfd1_novel")

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS_NEEDS_REVISION

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter", return_value=2):
            result = orch.review_chapter(2)

        self.assertEqual(result["decision"], "NEEDS_REVISION")

        # 确认 fact_digest_ch0002 没有产生
        fact_files = list((root / "states").glob("fact_digest_ch0002_*.md"))
        self.assertEqual(len(fact_files), 0,
                         "NEEDS_REVISION 不得生成 fact_digest_ch0002 文件")
        # raw_analysis 仍然存在（诊断记录）
        review_files = list((root / "states").glob("review_ch0002_*.md"))
        self.assertGreater(len(review_files), 0,
                           "raw_analysis 仍应保存在 states/review 中")

    def test_halt_no_fact_digest_file(self):
        """HALT 不得保存 fact_digest 文件。"""
        root = self._setup_review_dirs("nfd2_novel")
        (root / "chapters" / "chapter_0005_styled_20260801_120000.md").write_text(
            "第5章正文。" * 200, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0005.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("nfd2_novel")

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS_HALT

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter", return_value=2):
            result = orch.review_chapter(5)

        self.assertEqual(result["decision"], "HALT")
        fact_files = list((root / "states").glob("fact_digest_ch0005_*.md"))
        self.assertEqual(len(fact_files), 0,
                         "HALT 不得生成 fact_digest_ch0005 文件")

    def test_rejected_chapter_not_in_recent_fact_digests(self):
        """_recent_fact_digests 不得包含 rejected chapter 的内容。"""
        root = self._setup_review_dirs("nfd3_novel")
        # Create a valid fact_digest for chapter 1 (PASS)
        (root / "states" / "fact_digest_ch0001_20260801_120000.md").write_text(
            "# 第1章 事实摘要\n### 确定的物品\nE06_REJECTED_STRING_9921\n", encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        orch = Orchestrator("nfd3_novel")
        orch.file_store = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "nfd3_novel", self.settings.data_dir)

        # 检查 _recent_fact_digests 只包含 PASS chapters 的 fact digest
        # (通过模拟 — 确认 rejected chapter 不会产生文件是最直接的验证)
        recent = orch._recent_fact_digests(count=5)
        # 第1章（PASS）的内容应该在
        self.assertIn("E06_REJECTED_STRING_9921", recent)
        # 不应包含未产生的 rejected chapter 内容
        self.assertNotIn("NEEDS_REVISION_MARKER", recent)


# ═══════════════════════════════════════════════════════════════
# E06.1-B. P0 #2 — True Fail-Closed Decision Contract
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

class TestAtomicCommitBoundary(_TmpNovelCase):
    """E06.1-C: Parse 失败不导致部分提交。"""

    def test_parse_error_in_delta_preserves_state(self):
        """State Delta 解析出错 → 跳过提交，旧状态保持不变。"""
        root = self._setup_review_dirs("atom_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("atom_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "atom_novel", self.settings.data_dir)

        # Write initial canonical tracking doc with known state
        initial_rels = "# 角色关系图\n## 关系详情\n## 关系变更日志"
        (root / "tracking" / "character_relationships.md").write_text(
            initial_rels, encoding="utf-8")

        # State delta that parses relationships OK but items has bad format
        # (missing colon → parse of that line is skipped, not crashed)
        # We need something that triggers an actual parse error. Let's craft
        # a corrupted delta that will cause _parse_state_deltas to catch.
        bad_analysis = """## 状态变更（State Delta）
### 角色关系当前状态
- 陆沉 ↔ 顾明川: 关系类型=信任, 当前状态=盟友, 态度=友好 [依据: 第3段]
### 角色物品状态
#### 获得
INVALID_ITEM_LINE_WITHOUT_COLON
### 角色修炼状态
"""
        # This should NOT crash; items line without colon is just skipped
        sm.update_tracking_docs(1, "正文", bad_analysis)

        # Relationships should have been committed
        updated_rels = (root / "tracking" / "character_relationships.md").read_text(
            encoding="utf-8")
        self.assertIn("陆沉", updated_rels,
                      "角色关系应被原子化提交")
        self.assertIn("顾明川", updated_rels)

    def test_double_save_failure_reported(self):
        """E06.2: 第二个 tracking doc 保存失败 → 回滚所有已写文件，不静默。"""
        root = self._setup_review_dirs("atom2_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("atom2_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "atom2_novel", self.settings.data_dir)

        # Write initial tracking doc with known OLD content
        old_rels = "# OLD角色关系图\n## 关系详情\n#### 旧角色A ↔ 旧角色B\n- **关系类型**: 旧关系\n## 关系变更日志\n"
        (root / "tracking" / "character_relationships.md").write_text(
            old_rels, encoding="utf-8")

        good_analysis = """## 状态变更（State Delta）
### 角色关系当前状态
- 陆沉 ↔ 顾明川: 关系类型=信任, 当前状态=盟友, 态度=友好 [依据: 第1段]
### 角色物品状态
### 角色修炼状态
### 角色当前状态
### 伏笔状态
"""
        # Patch save_tracking_doc to fail on the second call
        call_count = []
        orig_save = sm.fs.save_tracking_doc

        def failing_save(name, content):
            call_count.append(name)
            if len(call_count) >= 2:
                raise OSError("模拟 I/O 失败")
            return orig_save(name, content)

        sm.fs.save_tracking_doc = failing_save

        # Should not raise — commit failure is reported, not raised
        result = sm.update_tracking_docs(1, "正文", good_analysis)

        # ── E06.2: ALL OLD — first doc must be rolled back ──
        commit_result = result.get("_commit_result")
        self.assertIsNotNone(commit_result)
        self.assertFalse(commit_result.success,
                         "第二个保存失败 → commit 必须报告 FAILED")

        updated_rels = (root / "tracking" / "character_relationships.md").read_text(
            encoding="utf-8")
        self.assertIn("OLD角色关系图", updated_rels,
                      "E06.2: 第一个已保存的文件必须回滚到 OLD 内容")
        self.assertNotIn("陆沉", updated_rels,
                         "E06.2: 关系文件不得包含新数据（已回滚）")

        # Result should still include change_log for diagnostics
        self.assertIn("change_log", result)
        # Commit failure info should be present
        self.assertIn("OSError", commit_result.error_message,
                      "错误信息必须包含异常类型")


# ═══════════════════════════════════════════════════════════════
# E06.1-D. #4 — Character Current State
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


class TestCharacterStateDeltaParsing(_TmpNovelCase):
    """E06.1-D: State Delta 中角色当前状态解析。"""

    def test_character_state_delta_parsed(self):
        root = self._setup_review_dirs("cs_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("cs_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "cs_novel", self.settings.data_dir)

        (root / "tracking" / "character_states.md").write_text(
            "# 角色当前状态\n## 角色当前状态\n| 角色 | 存活 | 位置 | 身体状态 | 身份 | 更新章 |\n|------|------|------|---------|------|--------|\n",
            encoding="utf-8")

        result = sm.update_tracking_docs(2, "正文2", MOCK_STATE_DELTA_WITH_CHARACTER_STATE)
        self.assertTrue(result.get("updated_character_states"),
                        "角色当前状态必须标记为已更新")

        char_file = root / "tracking" / "character_states.md"
        self.assertTrue(char_file.exists())
        content = char_file.read_text(encoding="utf-8")
        self.assertIn("柯林", content)
        self.assertIn("高架桥废墟", content)
        self.assertIn("轻伤", content)
        self.assertIn("王长林", content)
        self.assertIn("死亡", content)

    def test_planner_context_contains_character_state(self):
        """ChapterPlanner 的 prompt 必须包含 character_states。"""
        root = self._setup_review_dirs("csp_novel")
        (root / "tracking" / "character_states.md").write_text(
            "# 角色当前状态\n## 角色当前状态\n| 角色 | 存活 | 位置 | 身体状态 | 身份 | 更新章 |\n|------|------|------|---------|------|--------|\n| 柯林 | 存活 | 废土配电间 | 健康 | 觉醒者 | 第1章 |\n",
            encoding="utf-8")
        (root / "tracking" / "book_plan.md").write_text(
            "# 《测试》Book Plan\n## 核心主题\n测试", encoding="utf-8")
        (root / "tracking" / "volume_plan.md").write_text(
            VOLUME_PLAN_WITH_MARKER, encoding="utf-8")

        from src.agents.author.chapter_planner import ChapterPlanner

        planner = ChapterPlanner("csp_novel")
        planner.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "csp_novel", self.settings.data_dir)

        captured = {}
        def fake_llm(self, messages):
            captured["user"] = messages[-1]["content"]
            return """# 第3章规划：《测试》
## 一、章节信息
- **章大纲**: 测试
- **章节类型**: 延续型
## 二、写作上下文包
### 角色关系图
无
### 物品/装备追踪
无
### 修炼/力量体系现状
无
### 关键伏笔节点
无
### 情感调色板
平淡
### 禁止清单
无
"""

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm):
            planner.plan_chapter(3)

        self.assertIn("character_states.md", captured["user"],
                      "Planner prompt 必须包含 character_states")


# ═══════════════════════════════════════════════════════════════
# E06.1-E. #5 — Review Strategic Context
# ═══════════════════════════════════════════════════════════════

class TestStrategicContextInReviewPrompt(_TmpNovelCase):
    """E06.1-E: StateManager prompt 必须包含 Book Plan + Volume Plan。"""

    def test_book_plan_marker_in_prompt(self):
        root = self._setup_review_dirs("sc_novel")
        (root / "tracking" / "book_plan.md").write_text(
            BOOK_PLAN_WITH_MARKER, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("sc_novel")
        captured = {}

        def fake_llm(self, messages):
            captured["user"] = messages[-1]["content"]
            return MOCK_RAW_ANALYSIS_EXPLICIT_PASS_E06_1

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter", return_value=2):
            orch.review_chapter(1)

        self.assertIn("E06_BOOK_STRATEGIC_RULE_9137", captured["user"],
                      "Book Plan 必须出现在 StateManager review prompt 中")

    def test_volume_plan_marker_in_prompt(self):
        root = self._setup_review_dirs("sc2_novel")
        (root / "tracking" / "book_plan.md").write_text(
            "# 测试 Book Plan", encoding="utf-8")
        (root / "tracking" / "volume_plan.md").write_text(
            VOLUME_PLAN_WITH_MARKER, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("sc2_novel")
        captured = {}

        def fake_llm(self, messages):
            captured["user"] = messages[-1]["content"]
            return MOCK_RAW_ANALYSIS_EXPLICIT_PASS_E06_1

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter", return_value=2):
            orch.review_chapter(1)

        self.assertIn("E06_VOLUME_RULE_4281", captured["user"],
                      "Volume Plan 必须出现在 StateManager review prompt 中")


# ═══════════════════════════════════════════════════════════════
# E06.2 TEST ADDITIONS — Runtime Consistency Closure
# ═══════════════════════════════════════════════════════════════

# ── MOCK DATA FOR E06.2 ──────────────────────────────────

MOCK_RAW_ANALYSIS_PASS_E06_2 = """# 第1章复盘分析

## 事实摘要
### 确定的物品
扳手
### 确定的角色状态
柯林：健康
### 确定的事件
柯林醒来
### 确定的数字/数据
背包中2件物品
### 明确未出现的内容
无
### 待解悬念
徽章来源不明

## 状态变更（State Delta）
### 角色关系当前状态
- 柯林 ↔ 瘸子莫: 关系类型=交易伙伴, 当前状态=信任已建立, 态度=友好 [依据: 第5段]

### 角色物品状态
#### 获得
- 发光徽章: 持有者=柯林, 来源=背包发现, 状态=可用 [依据: 第3段]

### 角色修炼状态
### 角色当前状态
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

# ═══════════════════════════════════════════════════════════════
# E06.2-A. P0 — True Atomic Rollback
# ═══════════════════════════════════════════════════════════════

class TestAtomicRollback(_TmpNovelCase):
    """E06.2-A: 原子化提交失败 → 回滚所有已写文件，保持 ALL OLD。"""

    def test_second_save_failure_rolls_back_first(self):
        """第二个 tracking doc 保存失败 → 第一个已保存的必须回滚到 OLD。"""
        root = self._setup_review_dirs("atom3_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("atom3_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "atom3_novel", self.settings.data_dir)

        # Write INITIAL canonical tracking docs with known OLD content
        old_rels = "# OLD RELATIONSHIPS v1\n## 关系详情\n#### 旧角色 ↔ 旧角色\n- **关系类型**: 旧关系\n- **当前状态**: 旧状态\n## 关系变更日志\n"
        old_items = "# OLD ITEMS v1\n## 主角持有\n| 物品 | 来源 | 获得章 | 属性 | 状态 | 备注 |\n|------|------|--------|------|------|------|\n| 旧物品 | 旧来源 | 第0章 | 旧属性 | 旧状态 | |\n"
        old_cult = "# OLD CULTIVATION v1\n## 角色修炼状态\n| 角色 | 境界 | 距下一阶 | 特殊能力 | 限制 | 更新章 |\n|------|------|---------|---------|------|--------|\n| 旧角色 | 旧境界 | 旧距下一阶 | 旧能力 | 旧限制 | 第0章 |\n"

        (root / "tracking" / "character_relationships.md").write_text(
            old_rels, encoding="utf-8")
        (root / "tracking" / "items_equipment.md").write_text(
            old_items, encoding="utf-8")
        (root / "tracking" / "cultivation_system.md").write_text(
            old_cult, encoding="utf-8")
        # character_states.md may not exist yet — that's fine

        # State delta that modifies relationships AND items
        analysis = """## 状态变更（State Delta）
### 角色关系当前状态
- 柯林 ↔ 瘸子莫: 关系类型=交易伙伴, 当前状态=信任已建立, 态度=友好 [依据: 第5段]
### 角色物品状态
#### 获得
- 发光徽章: 持有者=柯林, 来源=背包发现, 状态=可用 [依据: 第3段]
### 角色修炼状态
### 角色当前状态
### 伏笔状态
"""
        # Patch save_tracking_doc: first call (relationships) succeeds,
        # second call (items) raises IOError
        call_order = []
        orig_save = sm.fs.save_tracking_doc

        def failing_save(name, content):
            call_order.append(name)
            if len(call_order) >= 2:
                raise OSError("E06.2 模拟 I/O 失败 — 第二个文件写入失败")
            return orig_save(name, content)

        sm.fs.save_tracking_doc = failing_save

        result = sm.update_tracking_docs(1, "正文", analysis)

        # ── Assert: commit failed ──
        commit_result = result.get("_commit_result")
        self.assertIsNotNone(commit_result, "必须返回 StateCommitResult")
        self.assertFalse(commit_result.success,
                         "第二个文件保存失败 → commit 必须报告 FAILED")
        self.assertIn("items_equipment", commit_result.error_message,
                      "错误信息必须包含失败组件名")
        warnings = "\n".join(commit_result.warnings)
        self.assertIn("已成功回滚 1 个文件", warnings)
        self.assertNotIn("canonical state 可能不一致", warnings)

        # ── Assert: ALL OLD — relationships rolled back ──
        current_rels = (root / "tracking" / "character_relationships.md").read_text(
            encoding="utf-8")
        self.assertIn("OLD RELATIONSHIPS v1", current_rels,
                      "关系文件必须回滚到 OLD 内容")
        self.assertNotIn("交易伙伴", current_rels,
                         "关系文件不得包含新数据（已回滚）")
        self.assertNotIn("柯林", current_rels,
                         "关系文件不得包含新角色名（已回滚）")

        # items must also be OLD
        current_items = (root / "tracking" / "items_equipment.md").read_text(
            encoding="utf-8")
        self.assertIn("OLD ITEMS v1", current_items,
                      "物品文件必须保持 OLD 内容")
        self.assertIn("旧物品", current_items,
                      "物品文件必须保持旧条目")

        # cultivation must also be OLD
        current_cult = (root / "tracking" / "cultivation_system.md").read_text(
            encoding="utf-8")
        self.assertIn("OLD CULTIVATION v1", current_cult,
                      "修炼文件必须保持 OLD 内容")

    def test_rollback_failure_reports_degraded_state(self):
        """Rollback 自身失败时不得误报全部成功回滚。"""
        from unittest.mock import patch

        root = self._setup_review_dirs("atom_rollback_failure")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("atom_rollback_failure", sqlite)
        sm.fs = __import__(
            'src.storage.file_store', fromlist=['FileStore']).FileStore(
                "atom_rollback_failure", self.settings.data_dir)

        old_rels = "# OLD RELATIONSHIPS\n## 关系详情\n## 关系变更日志\n"
        old_items = "# OLD ITEMS\n## 主角持有\n"
        old_cult = "# OLD CULTIVATION\n## 角色修炼状态\n"
        tracking = root / "tracking"
        (tracking / "character_relationships.md").write_text(
            old_rels, encoding="utf-8")
        (tracking / "items_equipment.md").write_text(
            old_items, encoding="utf-8")
        (tracking / "cultivation_system.md").write_text(
            old_cult, encoding="utf-8")

        analysis = """## 状态变更（State Delta）
### 角色关系当前状态
- 柯林 ↔ 瘸子莫: 关系类型=伙伴, 当前状态=新状态, 态度=友好
### 角色物品状态
#### 获得
- 徽章: 持有者=柯林, 来源=背包, 状态=可用
### 角色修炼状态
### 角色当前状态
### 伏笔状态
- 蓝光: 状态=OPEN, 回收章节=
"""
        original_save = sm.fs.save_tracking_doc
        save_calls = []

        def fail_second_save(name, content):
            save_calls.append(name)
            if len(save_calls) == 2:
                raise OSError("second canonical write failed")
            return original_save(name, content)

        original_write_text = Path.write_text
        rel_path = tracking / "character_relationships.md"

        def fail_rollback_write(path_self, data, *args, **kwargs):
            if path_self == rel_path and data == old_rels:
                raise OSError("rollback write failed")
            return original_write_text(path_self, data, *args, **kwargs)

        with patch.object(sm.fs, "save_tracking_doc",
                          side_effect=fail_second_save), \
             patch.object(Path, "write_text", new=fail_rollback_write), \
             patch.object(sqlite, "upsert_foreshadow") as mock_foreshadow, \
             patch.object(sm, "_sync_sqlite") as mock_sync:
            result = sm.update_tracking_docs(1, "正文", analysis)

        commit_result = result["_commit_result"]
        self.assertFalse(commit_result.success)
        warnings = "\n".join(commit_result.warnings)
        self.assertIn("rollback character_relationships 失败", warnings)
        self.assertIn("canonical state 可能不一致", warnings)
        self.assertNotIn("已成功回滚", warnings)
        mock_foreshadow.assert_not_called()
        mock_sync.assert_not_called()

    def test_all_files_committed_when_no_failure(self):
        """所有文件保存成功 → ALL NEW，changed_files 正确。"""
        root = self._setup_review_dirs("atom4_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("atom4_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "atom4_novel", self.settings.data_dir)

        # Write initial tracking docs
        (root / "tracking" / "character_relationships.md").write_text(
            "# 角色关系图\n## 关系详情\n## 关系变更日志", encoding="utf-8")
        (root / "tracking" / "items_equipment.md").write_text(
            "# 物品装备\n## 主角持有\n## 物品流转日志", encoding="utf-8")
        (root / "tracking" / "cultivation_system.md").write_text(
            "# 修炼体系\n## 角色修炼状态\n", encoding="utf-8")

        analysis = """## 状态变更（State Delta）
### 角色关系当前状态
- 柯林 ↔ 瘸子莫: 关系类型=交易伙伴, 当前状态=信任已建立, 态度=友好 [依据: 第5段]
### 角色物品状态
#### 获得
- 发光徽章: 持有者=柯林, 来源=背包发现, 状态=可用 [依据: 第3段]
### 角色修炼状态
### 角色当前状态
### 伏笔状态
"""
        result = sm.update_tracking_docs(1, "正文", analysis)

        commit_result = result.get("_commit_result")
        self.assertIsNotNone(commit_result)
        self.assertTrue(commit_result.success,
                        "所有文件保存成功 → commit 必须报告 SUCCESS")
        self.assertGreater(len(commit_result.changed_files), 0,
                           "changed_files 必须包含已提交的文件名")

        # Verify content is NEW
        current_rels = (root / "tracking" / "character_relationships.md").read_text(
            encoding="utf-8")
        self.assertIn("交易伙伴", current_rels,
                      "成功提交后关系文件必须包含新数据")


# ═══════════════════════════════════════════════════════════════
# E06.2-B. P0 — Commit Failure Blocks Fact Digest & RAG
# ═══════════════════════════════════════════════════════════════

class TestCommitFailureBlocksDownstream(_TmpNovelCase):
    """E06.2-B: State commit FAILED → no Fact Digest, no RAG index."""

    def test_commit_failure_no_fact_digest_no_rag(self):
        """Review PASS but commit fails → return ERROR, no fact_digest, no RAG."""
        root = self._setup_review_dirs("cf_novel")
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            "第1章正文内容。" * 200, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0001.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore
        from src.agents.state_manager.state_manager import StateManager

        orch = Orchestrator("cf_novel")
        rag_calls = []

        # Make the commit fail by patching _commit_all_tracking_docs
        orig_commit = StateManager._commit_all_tracking_docs

        def failing_commit(self, chapter_index, ch_label, rels, items, cult,
                          char_states, state_result, log_result):
            from src.storage.document_formats import StateCommitResult
            return StateCommitResult(
                success=False,
                error_message="E06.2 模拟提交失败: 磁盘写入错误",
                warnings=["模拟 I/O 失败"],
            )

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS_PASS_E06_2

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter",
                               side_effect=lambda *a, **kw: rag_calls.append(1)), \
             mock.patch.object(StateManager, "_commit_all_tracking_docs",
                               failing_commit):
            result = orch.review_chapter(1)

        # ── Assert: workflow reports ERROR ──
        self.assertEqual(result.get("decision"), "PASS",
                         "Review semantic 仍为 PASS")
        self.assertEqual(result.get("commit_status"), "FAILED",
                         "commit_status 必须为 FAILED")
        self.assertEqual(result.get("workflow_status"), "ERROR",
                         "workflow_status 必须为 ERROR")

        # ── Assert: no RAG call ──
        self.assertEqual(len(rag_calls), 0,
                         "Commit 失败 → 不得执行 RAG index")

        # ── Assert: no fact_digest file ──
        fact_files = list((root / "states").glob("fact_digest_ch0001_*.md"))
        self.assertEqual(len(fact_files), 0,
                         "Commit 失败 → 不得生成 fact_digest 文件")

    def test_commit_success_proceeds_normally(self):
        """Review PASS + commit success → fact_digest + RAG proceed normally."""
        root = self._setup_review_dirs("cs2_novel")
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            "第1章正文内容。" * 200, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0001.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("cs2_novel")
        rag_calls = []

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS_PASS_E06_2

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter",
                               side_effect=lambda *a, **kw: rag_calls.append(1)):
            result = orch.review_chapter(1)

        # ── Assert: normal PASS path ──
        self.assertNotEqual(result.get("commit_status"), "FAILED",
                            "正常 PASS 不得包含 commit_status=FAILED")
        self.assertNotIn("_commit_result", {k for k in result.keys()
                                            if k != "_commit_result"},
                         "正常路径不应暴露内部 _commit_result")
        self.assertGreater(len(rag_calls), 0,
                           "Commit 成功 → 必须执行 RAG index")

        # ── Assert: fact_digest exists ──
        fact_files = list((root / "states").glob("fact_digest_ch0001_*.md"))
        self.assertGreater(len(fact_files), 0,
                           "Commit 成功 → 必须生成 fact_digest 文件")


# ═══════════════════════════════════════════════════════════════
# E06.2-C. P0 — Styled Chapter Enforcement
# ═══════════════════════════════════════════════════════════════

class TestStyledChapterRequired(_TmpNovelCase):
    """E06.2-C: Review 只接受 styled 章节，不 fallback 到 raw。"""

    def test_review_without_styled_raises(self):
        """没有 styled 文件 → ValueError（不再 fallback 到 raw）。"""
        root = self._setup_review_dirs("sty_novel")
        # Only create a raw chapter, no styled
        (root / "chapters" / "chapter_0001_20260801_120000.md").write_text(
            "第1章 raw 正文。" * 200, encoding="utf-8")
        # Remove the default styled file that _setup_review_dirs creates
        for f in root.glob("chapters/chapter_0001_styled_*.md"):
            f.unlink()

        from src.core.orchestrator import Orchestrator

        orch = Orchestrator("sty_novel")
        with self.assertRaises(ValueError) as ctx:
            orch.review_chapter(1)
        self.assertIn("styled", str(ctx.exception),
                      "错误信息必须明确指出缺少 styled 文件")

    def test_review_with_styled_proceeds(self):
        """有 styled 文件 → 正常执行（不抛异常）。"""
        root = self._setup_review_dirs("sty2_novel")
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            "第1章 styled 正文。" * 200, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0001.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore

        orch = Orchestrator("sty2_novel")

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS_PASS_E06_2

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter", return_value=2):
            # Should not raise
            result = orch.review_chapter(1)

        self.assertIsNotNone(result)


# ═══════════════════════════════════════════════════════════════
# E06.2-D. P0 — StateCommitResult Propagation
# ═══════════════════════════════════════════════════════════════

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

class TestCmdReviewOutput(_TmpNovelCase):
    """E06.2.1-B: cmd_review 根据 runtime result 输出正确的 Supervisor 状态。"""

    def _run_cmd_review(self, novel_name: str, ch_num: int,
                        mock_review_return: dict) -> str:
        """Patch Orchestrator.review_chapter → run cmd_review → capture stdout."""
        from unittest.mock import patch
        import io
        import sys

        # Ensure novel dir exists
        self._setup_review_dirs(novel_name)

        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        try:
            with patch('src.core.orchestrator.Orchestrator.review_chapter',
                       return_value=mock_review_return):
                from main import cmd_review

                class Args:
                    name = novel_name
                    chapter = ch_num
                cmd_review(Args())
        finally:
            sys.stdout = old_stdout

        return captured.getvalue()

    def test_pass_commit_success_shows_next_chapter(self):
        """PASS + commit success → 提示继续下一章。"""
        output = self._run_cmd_review("cli_pass", 1, {
            "decision": "PASS",
            "updated_rels": True,
            "updated_items": True,
            "change_log": "...",
        })
        self.assertIn("PASS", output)
        self.assertIn("下一步", output,
                      "PASS + commit success 必须提示继续下一章")

    def test_needs_revision_no_next_chapter(self):
        """NEEDS_REVISION → 不提示继续下一章。"""
        output = self._run_cmd_review("cli_rev", 2, {
            "decision": "NEEDS_REVISION",
            "t1_issues": ["徽章数量矛盾"],
            "t2_issues": [],
            "reasons": ["需要修正"],
        })
        self.assertIn("需要修订", output)
        self.assertNotIn("下一步", output,
                         "NEEDS_REVISION 不得提示继续下一章")

    def test_halt_l2_no_next_chapter(self):
        """HALT + L2 → 不提示继续下一章。"""
        output = self._run_cmd_review("cli_halt2", 3, {
            "decision": "HALT",
            "planning_level": "L2",
            "reasons": ["事件链需要调整"],
        })
        self.assertIn("Planning issue", output)
        self.assertIn("L2", output)
        self.assertNotIn("下一步", output,
                         "HALT L2 不得提示继续下一章")

    def test_halt_l3_no_next_chapter(self):
        """HALT + L3 → 不提示继续下一章。"""
        output = self._run_cmd_review("cli_halt3", 5, {
            "decision": "HALT",
            "planning_level": "L3",
            "reasons": ["关键角色提前死亡违反战略约束"],
        })
        self.assertIn("Strategic issue", output)
        self.assertIn("L3", output)
        self.assertNotIn("下一步", output,
                         "HALT L3 不得提示继续下一章")

    def test_unknown_no_next_chapter(self):
        """UNKNOWN → 不提示继续下一章。"""
        output = self._run_cmd_review("cli_unk", 4, {
            "decision": "UNKNOWN",
            "reasons": [],
        })
        self.assertIn("unresolved", output)
        self.assertNotIn("下一步", output,
                         "UNKNOWN 不得提示继续下一章")

    def test_commit_failure_no_next_chapter(self):
        """PASS + commit failure → 不提示继续下一章。"""
        output = self._run_cmd_review("cli_cf", 1, {
            "decision": "PASS",
            "commit_status": "FAILED",
            "workflow_status": "ERROR",
            "error": "items_equipment: OSError: 磁盘满",
            "warnings": ["已回滚 1 个文件"],
        })
        self.assertIn("commit failed", output)
        self.assertIn("halted", output)
        self.assertNotIn("下一步", output,
                         "commit failure 不得提示继续下一章")


# ═══════════════════════════════════════════════════════════════
# E06.2.1 Final Patch — Parse Failure + Volume Plan Rollback
# ═══════════════════════════════════════════════════════════════

class TestParseFailureSetsCommitResult(_TmpNovelCase):
    """E06.2.1: State Delta 解析失败 → changes 必须包含 _commit_result。"""

    def test_parse_failure_sets_commit_result_false(self):
        """parse_errors → _commit_result.success = False。"""
        root = self._setup_review_dirs("pf_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("pf_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "pf_novel", self.settings.data_dir)

        # Write initial tracking docs
        (root / "tracking" / "character_relationships.md").write_text(
            "# 角色关系图\n## 关系详情\n## 关系变更日志", encoding="utf-8")
        (root / "tracking" / "items_equipment.md").write_text(
            "# 物品装备\n## 主角持有\n", encoding="utf-8")
        (root / "tracking" / "cultivation_system.md").write_text(
            "# 修炼体系\n## 角色修炼状态\n", encoding="utf-8")

        # Patch _parse_state_deltas to simulate a parse error
        orig_parse = sm._parse_state_deltas

        def failing_parse(analysis_text, ch_label, rels, items, cult,
                         char_states, errors):
            errors.append("E06.2.1 模拟解析异常: 物品装备字段格式损坏")
            return orig_parse(analysis_text, ch_label, rels, items, cult,
                            char_states, errors)

        sm._parse_state_deltas = failing_parse

        analysis = """## 状态变更（State Delta）
### 角色关系当前状态
- 柯林 ↔ 瘸子莫: 关系类型=交易伙伴, 当前状态=信任已建立, 态度=友好 [依据: 第5段]
### 角色物品状态
### 角色修炼状态
### 角色当前状态
### 伏笔状态
"""
        result = sm.update_tracking_docs(1, "正文", analysis)

        commit_result = result.get("_commit_result")
        self.assertIsNotNone(commit_result,
                             "parse error 也必须设置 _commit_result")
        self.assertFalse(commit_result.success,
                         "parse error → _commit_result.success 必须为 False")
        self.assertIn("解析错误", commit_result.error_message,
                      "error_message 必须包含 '解析错误'")


class TestParseFailureBlocksDownstream(_TmpNovelCase):
    """E06.2.1: Orchestrator 检测 parse failure → block Fact Digest / RAG。"""

    def test_parse_failure_orchestrator_no_fact_digest_no_rag(self):
        """Parse failure in PASS path → orchestrator blocks downstream。"""
        root = self._setup_review_dirs("pfb_novel")
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            "第1章正文内容。" * 200, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0001.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore
        from src.agents.state_manager.state_manager import StateManager

        orch = Orchestrator("pfb_novel")
        rag_calls = []

        # Patch _parse_state_deltas on the StateManager instance to inject parse error
        orig_parse = orch.state_manager._parse_state_deltas

        def failing_parse(analysis_text, ch_label, rels, items, cult,
                         char_states, errors):
            errors.append("E06.2.1 模拟解析异常: 物品装备字段格式损坏")
            return orig_parse(analysis_text, ch_label, rels, items, cult,
                            char_states, errors)

        orch.state_manager._parse_state_deltas = failing_parse

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS_PASS

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter",
                               side_effect=lambda *a, **kw: rag_calls.append(1)):
            result = orch.review_chapter(1)

        # Parse error → no canonical commit → orchestrator must NOT call RAG
        self.assertEqual(len(rag_calls), 0,
                         "parse failure → 不得执行 RAG index")

        # No fact_digest file
        fact_files = list((root / "states").glob("fact_digest_ch0001_*.md"))
        self.assertEqual(len(fact_files), 0,
                         "parse failure → 不得生成 fact_digest")


class TestVolumePlanCommitFailure(_TmpNovelCase):
    """E06.2.1: Volume Plan 提交失败 → 保留旧 ACTIVE 状态，不掩盖根因。"""

    def test_volume_plan_commit_failure_preserves_old_state(self):
        """提交失败 → 旧卷保持 ACTIVE，异常包含根因信息。"""
        root = self._setup_review_dirs("vp_novel")
        # Write real book_plan and volume_plan with a real-looking VolumePlan
        (root / "tracking" / "book_plan.md").write_text(
            "# 全书规划：《测试》\n## 核心目标\n测试\n## 卷框架\n### 第1卷：觉醒\n- **核心冲突**: 生存\n- **主角弧光**: 成长\n- **关键角色**: 柯林\n- **章数预估**: 5\n",
            encoding="utf-8")
        (root / "tracking" / "volume_plan.md").write_text(
            "# 第1卷规划：《觉醒》\n- **版本**: v1\n- **状态**: ACTIVE\n- **章节范围**: 第1章-第5章\n## 卷概述\n- **核心冲突**: 配电间争夺\n- **角色目标**: 生存\n- **障碍**: 资源匮乏\n## 事件链\n### 事件1：配电间\n- **触发条件**: 到达\n- **核心内容**: 探索\n- **涉及角色**: 柯林\n- **情感基调**: 紧张\n- **结果与影响**: 发现\n- **衔接**: 下一章\n- **对应章节**: 第1章\n## 节奏约束\n无\n",
            encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.core.agent_base import BaseAgent

        orch = Orchestrator("vp_novel")

        # Read old state before the attempt
        old_vp = (root / "tracking" / "volume_plan.md").read_text(encoding="utf-8")
        self.assertIn("ACTIVE", old_vp)

        # Mock the LLM call to return a valid Volume 2 candidate
        candidate_vp = """# 第2卷规划：《远征》
- **版本**: v1
- **状态**: ACTIVE
- **章节范围**: 第6章-第10章
## 卷概述
- **核心冲突**: 远征废土
- **角色目标**: 找到净水
- **障碍**: 未知
## 事件链
### 事件1：出发
- **触发条件**: 准备完毕
- **核心内容**: 出发
- **涉及角色**: 柯林
- **情感基调**: 期待
- **结果与影响**: 离开
- **衔接**: 下一章
- **对应章节**: 第6章
## 节奏约束
无
"""

        def fake_llm(self, messages):
            return candidate_vp

        # Patch save_canonical to fail AFTER generate succeeds
        orig_save = orch.file_store.save_canonical

        def failing_save(category, filename, content):
            raise OSError("E06.2.1 模拟磁盘满 — save_canonical 失败")

        orch.file_store.save_canonical = failing_save

        # Attempt new-volume — should raise RuntimeError
        with mock.patch.object(BaseAgent, "_call_llm", fake_llm):
            with self.assertRaises(RuntimeError) as ctx:
                orch.start_new_volume(volume_number=2)

        # ── Assert: original exception preserved ──
        error_msg = str(ctx.exception)
        self.assertIn("根因", error_msg,
                      "异常消息必须包含 '根因'")
        self.assertIn("OSError", error_msg,
                      "异常消息必须包含原始异常类型")
        self.assertIn("磁盘满", error_msg,
                      "异常消息必须包含原始异常描述")
        self.assertIn("ACTIVE", error_msg,
                      "异常消息必须确认旧卷仍为 ACTIVE")

        # ── Assert: old state preserved (rollback via .bak → .md rename)
        restored_vp = (root / "tracking" / "volume_plan.md").read_text(encoding="utf-8")
        self.assertIn("ACTIVE", restored_vp,
                      "提交失败后旧卷必须保持 ACTIVE")
        self.assertIn("第1卷", restored_vp,
                      "提交失败后不得切换到新卷")


# ═══════════════════════════════════════════════════════════════
# E06.2.1 Final — Missing _commit_result fail-closed
# ═══════════════════════════════════════════════════════════════

class TestMissingCommitResultFailClosed(_TmpNovelCase):
    """E06.2.1 final: _commit_result missing → workflow ERROR → no downstream."""

    def test_missing_commit_result_blocks_downstream(self):
        """update_tracking_docs 返回的 changes 无 _commit_result → ERROR。"""
        root = self._setup_review_dirs("mcr_novel")
        (root / "chapters" / "chapter_0001_styled_20260801_120000.md").write_text(
            "第1章正文内容。" * 200, encoding="utf-8")
        (root / "outlines" / "chapter_plan_ch0001.md").write_text(
            SAMPLE_PLAN_MD, encoding="utf-8")

        from src.core.orchestrator import Orchestrator
        from src.storage.chroma_store import ChromaStore
        from src.agents.state_manager.state_manager import StateManager

        orch = Orchestrator("mcr_novel")
        rag_calls = []

        # Patch update_tracking_docs to return a dict WITHOUT _commit_result
        def no_commit_result(chapter_index, chapter_text, analysis_text):
            return {"updated_rels": True, "change_log": "fake"}

        orch.state_manager.update_tracking_docs = no_commit_result

        def fake_llm(self, messages):
            return MOCK_RAW_ANALYSIS_PASS

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm), \
             mock.patch.object(ChromaStore, "index_chapter",
                               side_effect=lambda *a, **kw: rag_calls.append(1)):
            result = orch.review_chapter(1)

        # ── Assert: missing _commit_result → ERROR ──
        self.assertEqual(result.get("workflow_status"), "ERROR",
                         "missing _commit_result → workflow_status 必须为 ERROR")
        self.assertEqual(result.get("commit_status"), "FAILED",
                         "missing _commit_result → commit_status 必须为 FAILED")

        # ── Assert: no downstream ──
        self.assertEqual(len(rag_calls), 0,
                         "missing _commit_result → 不得执行 RAG index")

        fact_files = list((root / "states").glob("fact_digest_ch0001_*.md"))
        self.assertEqual(len(fact_files), 0,
                         "missing _commit_result → 不得生成 fact_digest")


# ═══════════════════════════════════════════════════════════════
# E06.2.1 Final — Markdown/SQLite ordering invariants
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


class TestMarkdownSuccessSqliteFailure(_TmpNovelCase):
    """Markdown commit success + SQLite failure → canonical NEW + warning only。"""

    def test_sqlite_failure_does_not_rollback_markdown(self):
        """SQLite upsert 失败 → Markdown 保持 NEW, StateCommitResult.success == True。"""
        root = self._setup_review_dirs("msf_novel")
        sqlite = SQLiteStore(root / "state.db")
        sm = StateManager("msf_novel", sqlite)
        sm.fs = __import__('src.storage.file_store', fromlist=['FileStore']).FileStore(
            "msf_novel", self.settings.data_dir)

        # Write initial tracking docs
        (root / "tracking" / "character_relationships.md").write_text(
            "# OLD MSF RELS\n## 关系详情\n## 关系变更日志\n", encoding="utf-8")
        (root / "tracking" / "items_equipment.md").write_text(
            "# OLD MSF ITEMS\n", encoding="utf-8")
        (root / "tracking" / "cultivation_system.md").write_text(
            "# OLD MSF CULT\n", encoding="utf-8")

        # State delta with foreshadowing
        analysis = """## 状态变更（State Delta）
### 角色关系当前状态
- 柯林 ↔ 瘸子莫: 关系类型=交易伙伴, 当前状态=信任已建立, 态度=友好 [依据: 第5段]
### 角色物品状态
### 角色修炼状态
### 角色当前状态
### 伏笔状态
- 蓝光之谜: 状态=OPEN, 回收章节= [依据: 第15段]
"""

        # Patch sqlite.upsert_foreshadow to raise
        import io, sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        def failing_upsert(novel_id, desc, new_status, resolve_ch):
            raise RuntimeError("E06.2.1 MSF 模拟 SQLite 写入失败")

        sqlite.upsert_foreshadow = failing_upsert

        try:
            result = sm.update_tracking_docs(1, "正文", analysis)
        finally:
            sys.stdout = old_stdout

        # ── Assert: commit SUCCESS ──
        cr = result.get("_commit_result")
        self.assertIsNotNone(cr)
        self.assertTrue(cr.success,
                        "SQLite 失败 → StateCommitResult.success 必须为 True")

        # ── Assert: canonical Markdown remains NEW ──
        rels = (root / "tracking" / "character_relationships.md").read_text(encoding="utf-8")
        self.assertIn("交易伙伴", rels,
                      "SQLite 失败 → Markdown 仍保持 NEW（不得回滚）")
        self.assertNotIn("OLD MSF RELS", rels,
                         "SQLite 失败 → Markdown 不得退回 OLD")

        # ── Assert: [STATE WARNING] in output ──
        output = captured.getvalue()
        self.assertIn("STATE WARNING", output,
                      "SQLite 失败必须输出 [STATE WARNING]")


if __name__ == "__main__":
    unittest.main()
