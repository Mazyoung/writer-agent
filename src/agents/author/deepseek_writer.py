"""
DeepSeekWriter — 接收 ChapterPlan (Part A + Part B) 的创意写手。

与旧 SceneWriter 的关键区别:
1. 先吸收 Part B 上下文包（角色关系/物品/修炼/伏笔/情感），再写 Part A 场景计划
2. 支持逐场景写作 (write_scene) 和整章写作 (write_chapter)
3. 上下文是权威的，不是可选的——角色关系和物品状态不可被写手篡改
"""

from src.core.agent_base import BaseAgent
from src.storage.document_formats import ChapterPlan, SceneSpec


class DeepSeekWriter(BaseAgent):
    """接收丰富上下文的创意写手"""

    def __init__(self, novel_id: str):
        super().__init__("deepseek_writer", novel_id, "deepseek_writer.txt")

    def write_chapter(self, chapter_plan: ChapterPlan,
                      world_setting: str = "",
                      prev_chapter_end: str = "") -> str:
        """整章一次性写作。

        Args:
            chapter_plan: 包含 Part A (场景计划) + Part B (上下文包)
            world_setting: 世界观设定（截断后注入 prompt 的【世界观与硬规则】区域，高优先级约束）
            prev_chapter_end: 上一章结尾（用于衔接）
        """
        prompt = chapter_plan.build_writer_prompt(world_setting, prev_chapter_end)

        result = self.run(
            user_message=prompt,
            save_category="chapters",
            save_prefix=f"chapter_{chapter_plan.chapter_index:04d}_draft",
        )
        return result.content

    def write_scene(self, scene: SceneSpec, chapter_plan: ChapterPlan,
                    prev_scene_end: str = "",
                    completed_scenes: list[str] | None = None) -> str:
        """逐场景写作。

        Args:
            scene: 当前场景的写作规格
            chapter_plan: 整章规划（提供 Part B 上下文 + 全部场景列表）
            prev_scene_end: 上一场景结尾文本
            completed_scenes: 已经完成的场景全文列表
        """
        parts = []

        # Part B 上下文（完整注入每个场景）
        if chapter_plan.context.character_relations:
            parts.append("## [必读] 角色关系图\n" + chapter_plan.context.character_relations)
        if chapter_plan.context.items_tracking:
            parts.append("## [必读] 物品/装备追踪\n" + chapter_plan.context.items_tracking)
        if chapter_plan.context.emotion_palette:
            parts.append("## [必读] 情感调色板\n" + chapter_plan.context.emotion_palette)
        if chapter_plan.context.forbidden_list:
            parts.append("## [禁止清单]\n" + chapter_plan.context.forbidden_list)

        # 已完成场景（必读）
        if completed_scenes:
            for i, cs in enumerate(completed_scenes, 1):
                parts.append(f"## [必读] 已完成场景 {i} 正文\n{cs}")

        # 上一场景结尾（强制衔接）
        if prev_scene_end:
            parts.append("## [必读] 上一场景结尾（第一句话必须衔接此内容）\n"
                         + prev_scene_end[-500:])

        # 当前场景指令
        parts.append(f"## 当前场景：场景 {scene.scene_number} — {scene.name}")
        parts.append(f"**发生什么**：{scene.what_happens}")
        parts.append(f"**戏剧功能**：{scene.dramatic_function}")
        parts.append(f"**信息增量**：{scene.dialogue_info_gain}")
        parts.append(f"**角色微时刻**：{scene.character_micro_moment}")
        parts.append(f"**涉及角色**：{scene.characters_involved}")
        parts.append(f"**情绪曲线**：{scene.emotion_curve}")
        parts.append(f"**字数预估**：{scene.word_estimate}")
        parts.append("")
        parts.append("只写这个场景。不要写下一个场景的内容。最后一段为下一场景留下自然过渡。")

        prompt = "\n\n".join(parts)

        result = self.run(
            user_message=prompt,
            save_category="chapters",
            save_prefix=f"scene_ch{chapter_plan.chapter_index:04d}_s{scene.scene_number:02d}",
        )
        return result.content
