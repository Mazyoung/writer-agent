from src.core.agent_base import BaseAgent, AgentOutput


class WorldBuilder(BaseAgent):
    """1A — 世界观构建师"""

    def __init__(self, novel_id: str):
        super().__init__("world_builder", novel_id, "world_builder.txt")

    def build(self, premise: str, extra_requirements: str = "") -> str:
        user_msg = f"## 故事前提\n{premise}"
        if extra_requirements:
            user_msg += f"\n\n## 额外要求\n{extra_requirements}"

        result = self.run(
            user_message=user_msg,
            save_category="settings",
            save_prefix="world_setting",
            use_canonical=True,
        )
        return result.content
