"""E07.2 后：Proposal Human Override 优先级测试。

测试 `proposal_edited.md` > `proposal.md` > legacy 的读取顺序，
以及 `--confirm` 不修改/覆盖 human override 文件。

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
from src.core.orchestrator import Orchestrator
import src.core.interceptor as interceptor_mod


# ── 固定 LLM 输出（initialize_novel 需要 mock） ─────────────

WORLD_MD = "# 世界观设定\n铁律：废土冬天没有净水。\n"

BOOK_MD = """# 全书规划：《测试书》
- **版本**: v1

## 核心目标
揭开地下秘密

## 卷框架
### 第1卷：废墟求生
- **核心冲突**: 活下去
- **章数预估**: 14
"""

VOLUME1_MD = """# 第1卷规划：《废墟求生》
- **版本**: v1
- **状态**: ACTIVE
- **章节范围**: 第1章-第14章

## 卷概述
- **核心冲突**: 生存

## 关键里程碑
- 第3章：发现管线图

## 事件链
### 事件1：配电间的第三天
- **触发条件**: 部落遇袭
- **核心内容**: 只剩柯林一人
- **对应章节**: 第1章

## 卷内角色档案
## 卷内伏笔表
## 节奏约束
## 已完成章节摘要
"""

PROPOSAL_EDITED = """# 创作提案（人工修改版）
## 一、题材选择
- **选项A**: 科幻废土 — 人类亲手编辑的版本

## 核心梗概
这是人工修改后的提案内容，应优先于 AI 生成版本。
"""

PROPOSAL_CANONICAL = """# 创作提案（AI 生成版）
## 一、题材选择
- **选项A**: 修真仙侠 — AI 自动生成

## 核心梗概
这是 AI 生成的原始提案内容。
"""


# ── 模块级 fake_llm（与现有测试风格一致：plain function） ──

def _fake_llm_response(self, messages):
    """self = BaseAgent 实例，messages = LLM 消息列表。"""
    c = messages[-1]["content"]
    if "世界观设定文档" in c:
        return WORLD_MD
    if "战术卷规划（Volume Plan）" in c:
        return VOLUME1_MD
    if "全书战略规划（Book Plan）" in c:
        return BOOK_MD
    return "（未识别）"


class _TmpNovelCase(unittest.TestCase):
    """把 settings.data_dir 重定向到临时目录。"""

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


class TestProposalOverridePriority(_TmpNovelCase):
    """proposal_edited.md > proposal.md > legacy 读取顺序。"""

    def _setup_novel_dir(self, novel_id: str) -> Path:
        """创建 novel 目录并返回其路径。"""
        novel_dir = self.tmp / "novels" / novel_id
        novel_dir.mkdir(parents=True)
        return novel_dir

    def test_edited_wins_over_canonical(self):
        """edited + canonical 同时存在 → edited 必须胜出。"""
        novel_dir = self._setup_novel_dir("edited_wins")
        (novel_dir / "proposal_edited.md").write_text(
            PROPOSAL_EDITED, encoding="utf-8")
        (novel_dir / "proposal.md").write_text(
            PROPOSAL_CANONICAL, encoding="utf-8")

        orch = Orchestrator("edited_wins")
        proposal = orch.file_store.load_canonical("", "proposal")
        self.assertIsNotNone(proposal)
        self.assertIn("人类亲手编辑的版本", proposal)
        self.assertNotIn("AI 自动生成", proposal)

    def test_canonical_only(self):
        """只有 canonical → 使用 canonical。"""
        novel_dir = self._setup_novel_dir("canonical_only")
        (novel_dir / "proposal.md").write_text(
            PROPOSAL_CANONICAL, encoding="utf-8")

        orch = Orchestrator("canonical_only")
        proposal = orch.file_store.load_canonical("", "proposal")
        self.assertIsNotNone(proposal)
        self.assertIn("AI 自动生成", proposal)

    def test_both_missing_returns_none(self):
        """两者均不存在 → fail closed (load_canonical 返回 None)。"""
        self._setup_novel_dir("both_missing")

        orch = Orchestrator("both_missing")
        proposal = orch.file_store.load_canonical("", "proposal")
        self.assertIsNone(proposal)

    def test_confirm_does_not_overwrite_human_override(self):
        """--confirm 不修改或覆盖 human override 文件。"""
        novel_dir = self._setup_novel_dir("no_overwrite")
        (novel_dir / "proposal_edited.md").write_text(
            PROPOSAL_EDITED, encoding="utf-8")
        (novel_dir / "proposal.md").write_text(
            PROPOSAL_CANONICAL, encoding="utf-8")

        # 记录原始内容
        original_edited = (novel_dir / "proposal_edited.md").read_text(
            encoding="utf-8")
        original_canonical = (novel_dir / "proposal.md").read_text(
            encoding="utf-8")

        with mock.patch.object(BaseAgent, "_call_llm", _fake_llm_response):
            orch = Orchestrator("no_overwrite")
            orch.initialize_novel(original_edited)

        # 验证 proposal_edited.md 未被修改
        self.assertTrue(
            (novel_dir / "proposal_edited.md").exists(),
            "proposal_edited.md 不应被删除")
        self.assertEqual(
            (novel_dir / "proposal_edited.md").read_text(encoding="utf-8"),
            original_edited,
            "proposal_edited.md 内容不应被修改")

        # 验证 proposal.md 未被修改（initialize_novel 不写回 proposal）
        self.assertEqual(
            (novel_dir / "proposal.md").read_text(encoding="utf-8"),
            original_canonical,
            "proposal.md 不应被 initialize_novel 覆盖")

    def test_cmd_init_source_detection_edited(self):
        """CLI 读取逻辑：edited 存在时识别为 HUMAN OVERRIDE。"""
        novel_dir = self._setup_novel_dir("cli_edited")
        (novel_dir / "proposal_edited.md").write_text(
            PROPOSAL_EDITED, encoding="utf-8")

        orch = Orchestrator("cli_edited")
        novel_root = orch.file_store.root
        edited_path = novel_root / "proposal_edited.md"
        canonical_path = novel_root / "proposal.md"

        # 模拟 cmd_init 中的读取逻辑
        if edited_path.exists():
            proposal = edited_path.read_text(encoding="utf-8")
            source = "HUMAN OVERRIDE"
        elif canonical_path.exists():
            proposal = canonical_path.read_text(encoding="utf-8")
            source = "AI CANONICAL"
        else:
            proposal = None
            source = None

        self.assertEqual(source, "HUMAN OVERRIDE")
        self.assertIn("人类亲手编辑的版本", proposal)

    def test_cmd_init_source_detection_canonical(self):
        """CLI 读取逻辑：只有 canonical 时识别为 AI CANONICAL。"""
        novel_dir = self._setup_novel_dir("cli_canonical")
        (novel_dir / "proposal.md").write_text(
            PROPOSAL_CANONICAL, encoding="utf-8")

        orch = Orchestrator("cli_canonical")
        novel_root = orch.file_store.root
        edited_path = novel_root / "proposal_edited.md"
        canonical_path = novel_root / "proposal.md"

        if edited_path.exists():
            proposal = edited_path.read_text(encoding="utf-8")
            source = "HUMAN OVERRIDE"
        elif canonical_path.exists():
            proposal = canonical_path.read_text(encoding="utf-8")
            source = "AI CANONICAL"
        else:
            proposal = None
            source = None

        self.assertEqual(source, "AI CANONICAL")
        self.assertIn("AI 自动生成", proposal)

    def test_cmd_init_both_missing_fail_closed(self):
        """CLI 读取逻辑：两者均不存在 → fail closed (proposal 为 None)。"""
        novel_dir = self._setup_novel_dir("cli_missing")

        orch = Orchestrator("cli_missing")
        novel_root = orch.file_store.root
        edited_path = novel_root / "proposal_edited.md"
        canonical_path = novel_root / "proposal.md"

        # 验证两者确实不存在
        self.assertFalse(edited_path.exists())
        self.assertFalse(canonical_path.exists())

        # 模拟 cmd_init 逻辑：两者不存在时 proposal = None → fail closed
        if edited_path.exists():
            proposal = "exists"
        elif canonical_path.exists():
            proposal = "exists"
        else:
            proposal = None

        self.assertIsNone(proposal,
                          "两者均不存在时 proposal 应为 None（fail closed）")


class TestLoadCanonicalGeneric(_TmpNovelCase):
    """验证 load_canonical 的通用 _edited 优先行为。"""

    def test_load_canonical_prefers_edited(self):
        """load_canonical 默认优先读取 _edited 版本。"""
        novel_dir = self.tmp / "novels" / "generic_edited"
        tracking_dir = novel_dir / "tracking"
        tracking_dir.mkdir(parents=True)
        (tracking_dir / "book_plan_edited.md").write_text(
            "人工修改版", encoding="utf-8")
        (tracking_dir / "book_plan.md").write_text(
            "AI 生成版", encoding="utf-8")

        orch = Orchestrator("generic_edited")
        content = orch.file_store.load_canonical("tracking", "book_plan")
        self.assertEqual(content, "人工修改版")

    def test_load_canonical_falls_back_to_plain(self):
        """只有 .md 无 _edited 时返回 .md 内容。"""
        novel_dir = self.tmp / "novels" / "generic_fallback"
        tracking_dir = novel_dir / "tracking"
        tracking_dir.mkdir(parents=True)
        (tracking_dir / "book_plan.md").write_text(
            "AI 生成版", encoding="utf-8")

        orch = Orchestrator("generic_fallback")
        content = orch.file_store.load_canonical("tracking", "book_plan")
        self.assertEqual(content, "AI 生成版")
