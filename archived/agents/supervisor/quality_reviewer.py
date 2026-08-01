from src.core.agent_base import BaseAgent


class QualityReviewer(BaseAgent):
    """3B — 质量审阅者"""

    def __init__(self, novel_id: str):
        super().__init__("quality_reviewer", novel_id, "quality_reviewer.txt")

    def review(self, chapter_text: str, chapter_outline: str,
               chapter_index: int) -> str:
        user_msg = f"""## 第 {chapter_index} 章大纲
{chapter_outline}

## 章节正文
{chapter_text}

请对以上章节进行深度审阅。"""

        result = self.run(
            user_message=user_msg,
            save_category="briefs",
            save_prefix=f"review_ch{chapter_index:04d}",
        )
        return result.content

    def extract_scene_facts(self, scene_text: str, chapter_index: int,
                            scene_index: int) -> str:
        """从单个场景正文中提取结构化事实快照，用于章内场景间一致性防护。"""
        fact_prompt = self.load_prompt("scene_fact_extractor.txt")

        user_msg = f"""## 第 {chapter_index} 章 场景 {scene_index} 正文
{scene_text}

请提取以上场景的事实快照。"""

        messages = [
            {"role": "system", "content": fact_prompt},
            {"role": "user", "content": user_msg},
        ]

        response = self._call_llm(messages)
        return response
