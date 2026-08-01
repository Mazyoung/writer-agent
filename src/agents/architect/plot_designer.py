from src.core.agent_base import BaseAgent


class PlotDesigner(BaseAgent):
    """1B — 情节设计师"""

    def __init__(self, novel_id: str):
        super().__init__("plot_designer", novel_id, "plot_designer.txt")

    def design(self, premise: str, world_setting: str,
               extra_requirements: str = "") -> str:
        user_msg = f"""## 故事前提
{premise}

## 世界观设定
{world_setting}"""
        if extra_requirements:
            user_msg += f"\n\n## 额外要求\n{extra_requirements}"

        result = self.run(
            user_message=user_msg,
            save_category="outlines",
            save_prefix="plot_structure",
            use_canonical=True,
        )
        return result.content
