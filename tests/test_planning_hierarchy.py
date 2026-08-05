"""E03 分层规划测试：BookPlan / VolumePlan round-trip、init 生产链、
ChapterPlanner 消费链（mock LLM，不需要真实 API）。

运行: venv/Scripts/python.exe -m unittest discover -s tests -v
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.settings import get_settings
from src.core.agent_base import BaseAgent
from src.storage.document_formats import BookPlan, VolumePlan
import src.core.interceptor as interceptor_mod


# ── 固定 LLM 输出 ─────────────────────────────────────────

WORLD_MD = "# 世界观设定\n铁律：废土冬天没有净水。\n"

BOOK_MD = """# 全书规划：《测试书》
- **版本**: v1

## 核心目标
揭开地下秘密

## 核心矛盾
生存与真相的冲突

## 主角长期成长方向
从拾荒者到文明破解者

## 战略约束
- 金手指不得更换
- 主角不得死亡

## 核心梗概
测试梗概

## 全书主题
- 生存

## 结局方向
开放结局

## 卷框架
### 第1卷：废墟求生
- **核心冲突**: 活下去
- **主角弧光**: 被动到主动
- **关键角色**: 柯林
- **章数预估**: 14

## 全局伏笔追踪
| 伏笔描述 | 埋伏章节 | 预计回收卷 | 状态 | 回收章节 |
|---------|---------|-----------|------|---------|
| 蓝光之谜 | 第1章 | 第2卷 | pending | |
"""

VOLUME1_MD = """# 第1卷规划：《废墟求生》
- **版本**: v1
- **状态**: ACTIVE

## 起始状态
柯林独自在废墟醒来。

## 本卷目标
建立稳定的生存据点。

## 主要冲突
物资稀缺与疤面帮争夺水源。

## 故事阶段/路径
- 柯林确认周边威胁
- 寻找净水管线
- 联合幸存者建立据点

## 关键转折
- 管线图暴露地下结构

## 限制条件
- 金手指不得更换

## 目标结束状态
据点建立，地下秘密浮现。
"""
VOLUME2_MD = """# 第2卷规划：《地下结构》
- **版本**: v1
- **状态**: ACTIVE

## 起始状态
据点已建立，地下入口出现。

## 本卷目标
查明地下结构的用途。

## 主要冲突
探索风险与外部势力争夺。

## 故事阶段/路径
- 重返交易站搜集情报
- 打开地下闸门
- 深入未知结构

## 关键转折
- 地下设施仍在运转

## 限制条件
- 既有生存规则继续有效

## 目标结束状态
柯林掌握地下结构的核心线索。
"""
CHAPTER_PLAN_MD = """# 第15章规划：《重返交易站》

## 一、章节信息
- **章大纲**: 柯林回到交易站
- **章节类型**: 延续型
- **总场景数**: 1

## 二、写作上下文包

### 角色关系图
柯林 ↔ 瘸子莫： 交易关系

### 物品/装备追踪
扳手

### 修炼/力量体系现状
暂无

### 关键伏笔节点
蓝光

### 情感调色板
试探

### 禁止清单
暂无

## 三、场景级写作计划

### 场景 1：交易站 [状态：待规划]
- **发生什么**：柯林到达交易站
- **本场景的戏剧功能**：推进主线
- **对话必须达成的信息增量**：情报
- **角色微时刻**：摩挲扳手
- **涉及角色**：柯林
- **情绪曲线**：从警惕 → 放松
- **字数预估**：800
- **与前后衔接**：承接上章
"""


class _TmpNovelCase(unittest.TestCase):
    """把 settings.data_dir 重定向到临时目录，避免污染真实数据。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = get_settings()
        self._orig_data_dir = self.settings.data_dir
        self._orig_api_key = self.settings.api_key
        self.settings.data_dir = self.tmp
        self.settings.api_key = "test-key"  # OpenAI 客户端构造需要非空 key
        interceptor_mod._interceptor = None  # 拦截器单例也指向临时目录

    def tearDown(self):
        self.settings.data_dir = self._orig_data_dir
        self.settings.api_key = self._orig_api_key
        interceptor_mod._interceptor = None
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestBookPlanRoundTrip(unittest.TestCase):
    def test_roundtrip_preserves_all(self):
        bp = BookPlan.from_markdown(BOOK_MD)
        bp2 = BookPlan.from_markdown(bp.to_markdown())
        self.assertEqual(bp2.title, bp.title)
        self.assertEqual(bp2.version, "v1")                      # version 保留
        self.assertIn("金手指不得更换", bp2.strategic_constraints)  # 战略约束保留
        self.assertIn("揭开地下秘密", bp2.core_goal)
        self.assertIn("生存与真相", bp2.core_conflict)
        self.assertIn("文明破解者", bp2.protagonist_growth)
        self.assertEqual(len(bp2.volumes), 1)
        self.assertEqual(len(bp2.global_foreshadows), 1)

    def test_missing_fields_default(self):
        bp = BookPlan.from_markdown("# 全书规划：《旧书》\n\n## 核心梗概\n旧梗概\n")
        self.assertEqual(bp.version, "v1")
        self.assertEqual(bp.strategic_constraints, "")


class TestVolumePlanRoundTrip(unittest.TestCase):
    def test_roundtrip_preserves_all(self):
        vp = VolumePlan.from_markdown(VOLUME1_MD)
        vp2 = VolumePlan.from_markdown(vp.to_markdown())
        self.assertEqual(vp2.volume_number, 1)                   # volume_index 保留
        self.assertEqual(vp2.version, "v1")
        self.assertEqual(vp2.status, "ACTIVE")                   # status 保留
        self.assertIn("独自在废墟醒来", vp2.starting_state)
        self.assertEqual(len(vp2.story_path), 3)
        self.assertIn("寻找净水管线", vp2.story_path[1])
        self.assertIn("地下秘密浮现", vp2.target_end_state)

    def test_volume_number_from_title(self):
        vp = VolumePlan.from_markdown(VOLUME2_MD)
        self.assertEqual(vp.volume_number, 2)


class TestChapterPlannerConsumption(_TmpNovelCase):
    """ChapterPlanner 上下文真实包含 Book Plan / Current Volume Plan /
    World Setting，并识别正确的 Active Volume。"""

    def _setup_novel(self):
        root = self.tmp / "novels" / "planner_novel"
        (root / "settings").mkdir(parents=True)
        (root / "tracking").mkdir(parents=True)
        (root / "settings" / "world_setting.md").write_text(WORLD_MD, encoding="utf-8")
        (root / "tracking" / "book_plan.md").write_text(BOOK_MD, encoding="utf-8")
        (root / "tracking" / "volume_plan.md").write_text(VOLUME2_MD, encoding="utf-8")
        return root

    def test_prompt_contains_three_layers_and_active_volume(self):
        from src.agents.author.chapter_planner import ChapterPlanner

        self._setup_novel()
        captured = {}

        def fake_llm(self, messages):
            captured["user"] = messages[-1]["content"]
            return CHAPTER_PLAN_MD

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm):
            planner = ChapterPlanner("planner_novel")
            # 识别正确 Active Volume（第2卷，而非默认第1卷）
            self.assertEqual(planner.load_active_volume().volume_number, 2)
            plan = planner.plan_chapter(15)

        prompt = captured["user"]
        self.assertIn("废土冬天没有净水", prompt)      # World Setting
        self.assertIn("金手指不得更换", prompt)          # Book Plan 战略约束
        self.assertIn("重返交易站", prompt)              # Current Volume Plan 路径
        self.assertIn("以事实为准", prompt)              # 事实优先于计划声明
        self.assertEqual(plan.chapter_index, 15)         # E01 round-trip

    def test_missing_plans_raise_clear_error(self):
        from src.agents.author.chapter_planner import ChapterPlanner

        planner = ChapterPlanner("empty_novel")  # 只有空目录
        with self.assertRaises(FileNotFoundError) as ctx:
            planner.plan_chapter(1)
        msg = str(ctx.exception)
        self.assertIn("tracking/book_plan.md", msg)
        self.assertIn("scripts/migrate_legacy_data.py", msg)  # 必须给出迁移路径，不静默继续
