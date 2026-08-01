from src.core.agent_base import BaseAgent


class ChapterPolisher(BaseAgent):
    """2C — 章节润色师"""

    def __init__(self, novel_id: str):
        super().__init__("chapter_polisher", novel_id, "chapter_polisher.txt")

    def polish(self, scene_texts: list[str], chapter_outline: str,
               chapter_index: int) -> str:
        scenes_combined = "\n\n---\n\n".join(
            f"### 场景 {i+1}\n{t}" for i, t in enumerate(scene_texts)
        )

        user_msg = f"""## 第 {chapter_index} 章大纲
{chapter_outline}

## 场景片段
{scenes_combined}

请将以上场景拼接润色为完整章节。"""

        result = self.run(
            user_message=user_msg,
            save_category="chapters",
            save_prefix=f"chapter_{chapter_index:04d}",
        )
        return result.content

    def revise(self, chapter_text: str, review_feedback: str) -> str:
        user_msg = f"""## 原始章节
{chapter_text}

## 审阅反馈
{review_feedback}

请根据审阅反馈修订本章。"""

        result = self.run(
            user_message=user_msg,
            save_category="chapters",
            save_prefix="chapter_revised",
        )
        return result.content
