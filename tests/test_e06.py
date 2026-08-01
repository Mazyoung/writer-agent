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
        """无显式审阅决策 section 但有 T1 硬错误时 → NEEDS_REVISION"""
        rd = ReviewDecision.from_analysis(MOCK_RAW_ANALYSIS_NO_DECISION)
        self.assertEqual(rd.verdict, "NEEDS_REVISION",
                         "有 T1 硬错误时即使无显式决策也应为 NEEDS_REVISION")

    def test_parse_empty_returns_unknown(self):
        """完全空的 analysis → UNKNOWN（fail-closed）"""
        rd = ReviewDecision.from_analysis("")
        self.assertEqual(rd.verdict, "UNKNOWN")

    def test_parse_no_t1_no_decision_section_returns_pass(self):
        """无 T1、无显式决策 section → 推断为 PASS"""
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
        self.assertEqual(rd.verdict, "PASS")

    def test_severity_from_quality_review(self):
        """质量审阅 MAJOR → severity=MAJOR"""
        analysis = """# 复盘
## 一致性检查
### T1（硬错误）
无
## 质量审阅
- **情节逻辑**: MAJOR
- **节奏评估**: PASS
"""
        rd = ReviewDecision.from_analysis(analysis)
        self.assertEqual(rd.verdict, "NEEDS_REVISION")  # MAJOR quality → NEEDS_REVISION
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


if __name__ == "__main__":
    unittest.main()
