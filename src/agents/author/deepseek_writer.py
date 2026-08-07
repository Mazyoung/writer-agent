"""DeepSeekWriter — 根据已批准的 Chapter Plan 原文生成或修订正文。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.agent_base import BaseAgent
from src.core.token_guard import guard_planning_context

if TYPE_CHECKING:
    from src.storage.document_formats import ChapterPlan, SceneSpec


class DeepSeekWriter(BaseAgent):
    """接收丰富上下文的创意写手"""

    def __init__(self, novel_id: str):
        super().__init__("deepseek_writer", novel_id, "deepseek_writer.txt")

    def write_chapter(
        self,
        chapter_plan_text: str,
        chapter_index: int,
        world_setting: str = "",
        prev_chapter_end: str = "",
    ) -> str:
        """根据完整、已通过审阅的 Chapter Plan 原文生成整章正文。"""
        guard_planning_context(self.model_slot, {
            "world_setting.md": world_setting,
            "chapter_plan.md": chapter_plan_text,
            "recent_chapter_end": prev_chapter_end,
        })
        prompt = f"""## 已通过审阅的 Chapter Plan
{chapter_plan_text}

## World Setting
{world_setting or "无"}

## Previous Chapter End
{prev_chapter_end or "无"}

---
严格执行完整 Chapter Plan，在 World Setting 与既有正文连续性边界内写出第 {chapter_index} 章完整正文。
直接输出正文，不写规划、分析或说明。"""
        result = self.run(
            user_message=prompt,
            save_category="chapters",
            save_prefix=f"chapter_{chapter_index:04d}_draft",
        )
        return result.content

    def revise_chapter(
        self,
        chapter_plan_text: str,
        chapter_index: int,
        chapter_text: str,
        review_reasons: list[str],
        t1_issues: list[str],
    ) -> str:
        """Apply one L1 prose revision without loading future plans."""
        issues = [*t1_issues, *review_reasons]
        issue_text = "\n".join(f"- {issue}" for issue in issues if issue.strip())
        prompt = f"""## 已通过审阅的 Chapter Plan
{chapter_plan_text}

## 待修订正文
{chapter_text}

## 必须修复的问题
{issue_text or '- 修复审阅指出的正文问题'}

---
只修订当前正文以解决以上 L1 问题。不得修改 Chapter Plan，不得新增规划外事件，
不得推测或引入 Book Plan / Volume Plan 中未出现在 Chapter Plan 的未来剧情。
直接输出完整修订正文，不写说明。"""
        result = self.run(
            user_message=prompt,
            save_category="chapters",
            save_prefix=f"chapter_{chapter_index:04d}_revision",
        )
        return result.content

    def write_scene(self, scene: "SceneSpec", chapter_plan: "ChapterPlan",
                    prev_scene_end: str = "",
                    completed_scenes: list[str] | None = None) -> str:
        """Legacy 逐场景写作工具；正式整章路径使用 raw Chapter Plan。

        Args:
            scene: 当前场景的写作规格
            chapter_plan: legacy 结构化整章规划
            prev_scene_end: 上一场景结尾文本
            completed_scenes: 已经完成的场景全文列表
        """
        parts = []

        # Legacy structured-plan context (not used by formal whole-chapter flow).
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
