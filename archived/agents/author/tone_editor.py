from src.core.agent_base import BaseAgent


class ToneEditor(BaseAgent):
    """调性编辑 — 调整叙事语气，不改核心内容"""

    def __init__(self, novel_id: str):
        super().__init__("tone_editor", novel_id, "tone_editor.txt")

    def edit(self, scene_text: str, scene_spec: str,
             chapter_index: int, scene_index: int) -> str:
        """调整场景的叙事调性并检查场景规划对齐"""
        user_msg = f"""## 第 {chapter_index} 章 场景 {scene_index}

### 场景写作指令（用于对齐检查）
{scene_spec}

### 原始正文
{scene_text}

---
请调整叙事语气，检查场景规划对齐。"""

        result = self.run(
            user_message=user_msg,
            save_category="chapters",
            save_prefix=f"scene_ch{chapter_index:04d}_s{scene_index:02d}_edited",
        )
        return result.content
