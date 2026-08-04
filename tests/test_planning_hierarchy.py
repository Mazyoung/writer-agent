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
- **章节范围**: 第1章-第14章

## 卷概述
- **核心冲突**: 生存
- **角色目标**: 活着
- **障碍**: 物资稀缺

## 关键里程碑
- 第3章：发现管线图
- 第8章：首次遭遇疤面帮

## 事件链
### 事件1：配电间的第三天
- **触发条件**: 部落遇袭
- **核心内容**: 只剩柯林一人
- **涉及角色**: 柯林
- **情感基调**: 压抑
- **结果与影响**: 获得初始装备
- **衔接**: 往东
- **对应章节**: 第1章

## 卷内角色档案
### 柯林
- **当前状态**: 健康

## 卷内伏笔表
| 伏笔描述 | 埋伏章节 | 预计回收位置 | 状态 |
|---------|---------|------------|------|
| 蓝光 | 第1章 | 第14章 | pending |

## 节奏约束
紧张与日常交替

## 已完成章节摘要
"""

VOLUME2_MD = """# 第2卷规划：《地下结构》
- **版本**: v1
- **状态**: ACTIVE
- **章节范围**: 第15章-第30章

## 卷概述
- **核心冲突**: 深入地下

## 关键里程碑
- 第17章：打开闸门

## 事件链
### 事件1：重返交易站
- **触发条件**: 需要情报
- **核心内容**: 柯林回到交易站找瘸子莫
- **对应章节**: 第15章

## 卷内角色档案
## 卷内伏笔表
## 节奏约束
## 已完成章节摘要
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
        self.assertEqual(vp2.chapter_range, "第1章-第14章")        # chapter range 保留
        self.assertEqual(len(vp2.milestones), 2)                 # milestones 保留
        self.assertIn("第8章", vp2.milestones[1])
        self.assertIn("紧张与日常交替", vp2.pacing_constraints)
        self.assertEqual(len(vp2.events), 1)

    def test_volume_number_from_title(self):
        vp = VolumePlan.from_markdown(VOLUME2_MD)
        self.assertEqual(vp.volume_number, 2)


class TestInitialization(_TmpNovelCase):
    """init → Book Plan exists → Volume 1 Plan exists（mock LLM）。"""

    def test_init_produces_book_and_volume_plans(self):
        from src.core.orchestrator import Orchestrator

        def fake_llm(self, messages):
            c = messages[-1]["content"]
            if "世界观设定文档" in c:
                return WORLD_MD
            # 卷规划 prompt 会内嵌 Book Plan 内容，须先判 Volume 再判 Book
            if "战术卷规划（Volume Plan）" in c:
                return VOLUME1_MD
            if "全书战略规划（Book Plan）" in c:
                return BOOK_MD
            return "（未识别）"

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm):
            orch = Orchestrator("init_novel")
            orch.initialize_novel("测试提案")

        root = self.tmp / "novels" / "init_novel"
        book_path = root / "tracking" / "book_plan.md"
        vol_path = root / "tracking" / "volume_plan.md"
        self.assertTrue(book_path.exists(), "Book Plan 未生成")
        self.assertTrue(vol_path.exists(), "Volume 1 Plan 未生成")
        # plot_structure.md 已退休，不再是运行时产物
        self.assertFalse((root / "outlines" / "plot_structure.md").exists())

        bp = BookPlan.from_markdown(book_path.read_text(encoding="utf-8"))
        self.assertEqual(bp.version, "v1")
        self.assertIn("金手指不得更换", bp.strategic_constraints)

        vp = VolumePlan.from_markdown(vol_path.read_text(encoding="utf-8"))
        self.assertEqual(vp.volume_number, 1)
        self.assertEqual(vp.status, "ACTIVE")
        self.assertEqual(vp.chapter_range, "第1章-第14章")
        self.assertEqual(len(vp.milestones), 2)


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
        self.assertIn("重返交易站", prompt)              # Current Volume Plan 事件
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


class TestNewVolumeRolling(_TmpNovelCase):
    """Rolling Horizon: start_new_volume 归档旧卷 + 激活新卷 + PlanRevision。"""

    def test_start_new_volume(self):
        from src.core.orchestrator import Orchestrator
        from src.planning.store import PlanningStore

        root = self.tmp / "novels" / "roll_novel"
        (root / "tracking").mkdir(parents=True)
        (root / "tracking" / "book_plan.md").write_text(BOOK_MD, encoding="utf-8")
        (root / "tracking" / "volume_plan.md").write_text(VOLUME1_MD, encoding="utf-8")

        def fake_llm(self, messages):
            return VOLUME2_MD

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm):
            orch = Orchestrator("roll_novel")
            orch.start_new_volume(notes="加快节奏")

        # 旧卷归档为 COMPLETED
        archive = root / "tracking" / "volumes" / "volume_01.md"
        self.assertTrue(archive.exists())
        old_vp = VolumePlan.from_markdown(archive.read_text(encoding="utf-8"))
        self.assertEqual(old_vp.status, "COMPLETED")

        # 新卷成为 ACTIVE（唯一活跃卷）
        new_vp = VolumePlan.from_markdown(
            (root / "tracking" / "volume_plan.md").read_text(encoding="utf-8"))
        self.assertEqual(new_vp.volume_number, 2)
        self.assertEqual(new_vp.status, "ACTIVE")

        # PlanRevision 已记录
        revs = PlanningStore(root).list_revisions()
        self.assertEqual(len(revs), 1)
        self.assertEqual(revs[0].plan_type, "volume_plan")
        self.assertEqual(revs[0].status, "APPLIED")


# ── E03.1: 事务式 new-volume + Volume1 依赖 BookPlan ────────────

VOLUME3_MD = VOLUME2_MD.replace("第2卷规划", "第3卷规划").replace(
    "第15章-第30章", "第31章-第45章")
VOLUME_PLANNED_MD = VOLUME2_MD.replace("**状态**: ACTIVE", "**状态**: PLANNED")
BOOK_WITH_MARKER = BOOK_MD.replace(
    "- 主角不得死亡", "- 主角不得死亡\n- STRATEGIC_TEST_CONSTRAINT_9271")


class TestNewVolumeTransaction(_TmpNovelCase):
    """E03.1: Generate → Validate → Commit。
    任何失败路径下：原 canonical 不变、不产生归档、不产生 PlanRevision。"""

    def _setup_novel(self) -> tuple[Path, str]:
        root = self.tmp / "novels" / "tx_novel"
        (root / "tracking").mkdir(parents=True)
        (root / "tracking" / "book_plan.md").write_text(BOOK_MD, encoding="utf-8")
        (root / "tracking" / "volume_plan.md").write_text(VOLUME1_MD, encoding="utf-8")
        return root, VOLUME1_MD

    def _assert_untouched(self, root: Path, original_text: str):
        self.assertEqual(
            (root / "tracking" / "volume_plan.md").read_text(encoding="utf-8"),
            original_text, "原 volume_plan.md 内容被修改")
        vol_dir = root / "tracking" / "volumes"
        archived = list(vol_dir.glob("*.md")) if vol_dir.exists() else []
        self.assertEqual(archived, [], "不应产生归档文件")
        from src.planning.store import PlanningStore
        self.assertEqual(PlanningStore(root).list_revisions(), [],
                         "不应产生 PlanRevision")
        self.assertEqual(
            VolumePlan.from_markdown(original_text).status, "ACTIVE",
            "原 status 仍为 ACTIVE")

    def _run_with_llm(self, root_name, fake):
        from src.core.orchestrator import Orchestrator
        with mock.patch.object(BaseAgent, "_call_llm", fake):
            return Orchestrator(root_name).start_new_volume()

    def test_api_failure_keeps_state(self):
        root, original = self._setup_novel()

        def fake(self, messages):
            raise RuntimeError("API down")

        with self.assertRaises(RuntimeError):
            self._run_with_llm("tx_novel", fake)
        self._assert_untouched(root, original)

    def test_empty_output_keeps_state(self):
        root, original = self._setup_novel()
        with self.assertRaises(ValueError):
            self._run_with_llm("tx_novel", lambda self, messages: "   ")
        self._assert_untouched(root, original)

    def test_unparseable_output_keeps_state(self):
        root, original = self._setup_novel()
        with self.assertRaises(ValueError):
            self._run_with_llm("tx_novel",
                               lambda self, messages: "随便一段没有结构的文字")
        self._assert_untouched(root, original)

    def test_wrong_volume_index_keeps_state(self):
        root, original = self._setup_novel()
        with self.assertRaises(ValueError) as ctx:
            self._run_with_llm("tx_novel", lambda self, messages: VOLUME3_MD)
        self.assertIn("卷号错误", str(ctx.exception))
        self._assert_untouched(root, original)

    def test_wrong_status_keeps_state(self):
        root, original = self._setup_novel()
        with self.assertRaises(ValueError) as ctx:
            self._run_with_llm("tx_novel", lambda self, messages: VOLUME_PLANNED_MD)
        self.assertIn("ACTIVE", str(ctx.exception))
        self._assert_untouched(root, original)

    def test_save_failure_rolls_back(self):
        """Commit 阶段保存失败：canonical 回滚、归档清除、无 Revision。"""
        from src.storage.file_store import FileStore
        root, original = self._setup_novel()

        with mock.patch.object(BaseAgent, "_call_llm",
                               lambda self, messages: VOLUME2_MD), \
             mock.patch.object(FileStore, "save_canonical",
                               side_effect=IOError("disk full")):
            from src.core.orchestrator import Orchestrator
            with self.assertRaises(RuntimeError) as ctx:
                Orchestrator("tx_novel").start_new_volume()
        self.assertIn("已回滚", str(ctx.exception))
        self._assert_untouched(root, original)


class TestVolume1DependsOnBookPlan(_TmpNovelCase):
    """E03.1: Volume 1 生成必须消费刚生成并成功解析的 Book Plan。"""

    def test_volume_prompt_contains_bookplan_marker(self):
        from src.core.orchestrator import Orchestrator

        prompts = []

        def fake_llm(self, messages):
            c = messages[-1]["content"]
            prompts.append(c)
            if "世界观设定文档" in c:
                return WORLD_MD
            if "战术卷规划（Volume Plan）" in c:
                return VOLUME1_MD
            if "全书战略规划（Book Plan）" in c:
                return BOOK_WITH_MARKER
            return "（未识别）"

        with mock.patch.object(BaseAgent, "_call_llm", fake_llm):
            Orchestrator("dep_novel").initialize_novel("测试提案")

        volume_prompts = [p for p in prompts if "战术卷规划（Volume Plan）" in p]
        self.assertEqual(len(volume_prompts), 1, "应恰好有一次 Volume 1 生成调用")
        vp_prompt = volume_prompts[0]
        # 唯一标识证明 prompt 包含刚解析的 Book Plan 内容
        self.assertIn("STRATEGIC_TEST_CONSTRAINT_9271", vp_prompt)
        # 服从性声明
        self.assertIn("服从 Book Plan 的战略方向", vp_prompt)
        self.assertIn("不得重新定义 Book Plan", vp_prompt)


if __name__ == "__main__":
    unittest.main()
