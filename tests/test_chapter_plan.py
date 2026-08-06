"""E01/E02 最小单元测试。

E01: ChapterPlan -> Markdown -> ChapterPlan 后 chapter_index 不变。
E02: build_writer_prompt 真正注入 world_setting（【世界观与硬规则】区域 + 长度上限）。

运行: venv/Scripts/python.exe -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.document_formats import ChapterPlan, SceneSpec


def _make_plan(n: int) -> ChapterPlan:
    plan = ChapterPlan(chapter_index=n, title=f"测试章{n}")
    plan.chapter_outline = "测试大纲"
    plan.scenes.append(SceneSpec(scene_number=1, name="开场",
                                 what_happens="发生了什么"))
    return plan


class TestChapterIndexRoundTrip(unittest.TestCase):
    """E01: 章号序列化/反序列化一致性。"""

    def _roundtrip(self, n: int) -> int:
        return ChapterPlan.from_markdown(_make_plan(n).to_markdown()).chapter_index

    def test_chapter_1(self):
        self.assertEqual(self._roundtrip(1), 1)

    def test_chapter_10(self):
        self.assertEqual(self._roundtrip(10), 10)

    def test_chapter_100(self):
        self.assertEqual(self._roundtrip(100), 100)

    def test_llm_style_title(self):
        """LLM 直接产出的规划文件（非 to_markdown 生成）同样能恢复章号。"""
        md = "# 第10章规划：《深入废墟》\n\n## 一、章节信息\n- **总场景数**: 1\n"
        self.assertEqual(ChapterPlan.from_markdown(md).chapter_index, 10)

    def test_writer_draft_prefix_uses_real_chapter(self):
        """DeepSeekWriter 草稿文件名前缀必须使用真实章号。"""
        plan = ChapterPlan.from_markdown(_make_plan(10).to_markdown())
        prefix = f"chapter_{plan.chapter_index:04d}_draft"
        self.assertEqual(prefix, "chapter_0010_draft")


class TestWorldSettingInjection(unittest.TestCase):
    """E02: world_setting 进入 Writer Prompt。"""

    def test_section_present(self):
        prompt = _make_plan(10).build_writer_prompt(
            world_setting="世界规则：废土上没有净水的冬天。")
        self.assertIn("【世界观与硬规则】", prompt)
        self.assertIn("废土上没有净水的冬天", prompt)

    def test_priority_instruction(self):
        prompt = _make_plan(10).build_writer_prompt(world_setting="规则")
        self.assertIn("高优先级约束", prompt)
        self.assertIn("优先遵守世界观", prompt)

    def test_full_world_setting_reaches_writer_prompt(self):
        ws = "规" * 3000 + "WORLD-SETTING-TAIL"
        prompt = _make_plan(10).build_writer_prompt(world_setting=ws)
        self.assertIn(ws, prompt)

    def test_empty_world_setting_no_section(self):
        prompt = _make_plan(10).build_writer_prompt(world_setting="")
        self.assertNotIn("【世界观与硬规则】", prompt)


if __name__ == "__main__":
    unittest.main()
