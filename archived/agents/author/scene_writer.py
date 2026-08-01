from src.core.agent_base import BaseAgent


class SceneWriter(BaseAgent):
    """2B — 场景写手"""

    def __init__(self, novel_id: str):
        super().__init__("scene_writer", novel_id, "scene_writer.txt")

    def write(self, scene_spec: str, previous_end: str,
              chapter_index: int, scene_index: int,
              world_setting: str = "",
              character_profiles: str = "",
              plot_event_context: str = "",
              previous_scenes: list[str] = None,
              next_scene_title: str = "",
              previous_review: str = "",
              previous_state: str = "",
              volume_context: str = "",
              chroma_context: str = "",
              fact_context: str = "",
              chroma_verify: str = "",
              scene_fact_context: str = "") -> str:
        """写一个场景，注入完整上下文确保不偏离设定"""

        context = {}
        # ── 活跃写作上下文（场景创作的核心输入）──
        if world_setting:
            context["世界观设定（必须遵守的地名、势力名、境界名）"] = world_setting[:2000]
        if volume_context:
            context["卷大纲背景"] = volume_context[:1000]
        if character_profiles:
            context["出场角色档案"] = character_profiles[:1500]
        if previous_scenes:
            context["已完成场景正文——必须通读以确保连续性"] = "\n\n---\n\n".join(
                f"[场景{i+1}]\n{s}" for i, s in enumerate(previous_scenes)
            )
        if previous_end:
            context["上一场景结尾——第一句话必须从此处直接接着写"] = previous_end
        elif not previous_scenes:
            context["上一场景结尾"] = "（本章第一个场景，无需衔接上文）"
        if previous_state:
            context["当前角色与世界状态"] = previous_state[:1000]
        if previous_review:
            context["上一章质量审阅报告"] = previous_review[:800]
        if chroma_context:
            context["向量库检索的相关设定片段"] = chroma_context[:1000]

        # ── 仅供查阅（以下内容用于验证一致性，禁止在正文中复述）──
        reference = []
        if fact_context:
            reference.append("## 前文章节事实摘要\n\n以下信息你已经知道。场景中的前情引用必须与此一致，但**不要在正文中让角色重新发现或解释这些事实**——读者已经通过前文知道了。\n\n" + fact_context[:2000])
        if scene_fact_context:
            reference.append("## 本章已完成场景的事实快照\n\n同上，你已经知道，不要再在正文中复述。\n\n" + scene_fact_context[:1500])
        if chroma_verify:
            reference.append("## 前文相关原文验证\n\n" + chroma_verify[:800])
        if reference:
            context["【仅供查阅·禁止在正文中复述】以下信息用于确保一致性，不是写作素材。角色不需要重新发现他们已经知道的事情，叙事不需要停下来向读者解释设定。"] = "\n\n---\n\n".join(reference)

        # 构建硬边界指令
        boundary = ""
        if next_scene_title:
            boundary = (
                f"\n\n### 硬性截止线\n"
                f"本场景必须在抵达以下情节之前停止：\n"
                f"**下一个场景是「{next_scene_title}」**——本场景的内容不得进入下一场景的领域。\n"
                f"如果你发现自己在写下个场景才该出现的内容，立即收尾。"
            )

        # 清洗 scene_spec：去掉"与前后衔接"中引用未来场景的内容
        import re
        clean_spec = re.sub(
            r'-\s*\*\*与前后衔接\*\*[：:].*?为场景\d+.*?\n',
            r'- **与前后衔接**：承接上一场景结尾，从上一场景最后一句直接开始写。不要跳到后续场景的内容。\n',
            scene_spec
        )

        user_msg = f"""## 第 {chapter_index} 章 场景 {scene_index}/{self._total_scenes}

### 场景写作指令（严格按此执行，不得超出）
{clean_spec}
{boundary}

### 场景限定规则（违反即为不合格）
1. **只写本场景指令中"发生什么"字段描述的内容**。不要写其他场景的内容
2. 如果场景指令中提到了不属于本场景的角色、事件、线索——忽略它们
3. 不要在结尾处为后续场景"铺垫"或"预热"
4. 本场景结束时停在"发生什么"描述的自然终点即可

---
请写出本场景的正文。要求：
1. 第一句话必须和上一场景的结尾在时间/空间/动作上连续
2. 使用世界观设定中的地名、势力名、境界名，不要自行编造
3. 角色的行为、对话、能力必须符合角色档案
4. **篇幅自决**：简单场景短写，复杂场景多写。你是网文写手——短段落、快节奏、不注水。不要让读者翻三页才看到一个动作结束。"""

        result = self.run(
            user_message=user_msg,
            context=context,
            save_category="chapters",
            save_prefix=f"scene_ch{chapter_index:04d}_s{scene_index:02d}",
        )
        return result.content

    def rewrite(self, original_text: str, guard_feedback: str,
                scene_spec: str) -> str:
        user_msg = f"""## 场景规划
{scene_spec}

## 原始文本
{original_text}

## 需要修正的问题
{guard_feedback}

请根据以上反馈重写本场景。严格遵循原场景规划的设定。"""

        result = self.run(
            user_message=user_msg,
            save_category="chapters",
            save_prefix="scene_rewrite",
        )
        return result.content

    def set_total_scenes(self, total: int):
        self._total_scenes = total
