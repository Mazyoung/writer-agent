from src.core.agent_base import BaseAgent


class ConsistencyGuard(BaseAgent):
    """3A — 一致性守护者"""

    def __init__(self, novel_id: str):
        super().__init__("consistency_guard", novel_id, "consistency_guard.txt")

    def check_plan(self, scene_plan: str, brief: str) -> str:
        """写前审核：检查场景大纲"""
        user_msg = f"""## 写作简报
{brief}

## 待审核的场景规划
{scene_plan}

请检查以上场景规划是否存在一致性问题。"""

        result = self.run(
            user_message=user_msg,
            save_category="briefs",
            save_prefix="plan_check",
        )
        return result.content.strip()

    def check_scene(self, scene_text: str, scene_spec: str,
                    world_setting: str = "",
                    fact_context: str = "") -> str:
        """写中抽查：检查单个场景。world_setting 和 fact_context 用于验证境界编号和时间线。"""
        parts = [f"## 场景规划\n{scene_spec}",
                 f"## 场景正文\n{scene_text}"]
        if world_setting:
            # 只传修炼体系部分，减少噪音
            import re
            cult_match = re.search(
                r'(?:力量|修炼).*?(?=##\s+\d\.\s+(?:世界物理|核心))',
                world_setting, re.DOTALL
            )
            cult_section = cult_match.group(0)[:1500] if cult_match else world_setting[:1500]
            parts.insert(0, f"## 【权威参考】世界设定·修炼体系\n{cult_section}")
        if fact_context:
            parts.insert(1, f"## 【权威参考】前文章节事实摘要（时间线以此为准）\n{fact_context[:1200]}")
        parts.append("\n请快速检查以上场景是否存在一致性问题。重点：境界编号是否正确、时间线是否与前文一致。")

        user_msg = "\n".join(parts)
        result = self.run(
            user_message=user_msg,
            save_category="briefs",
            save_prefix="scene_check",
        )
        return result.content.strip()
