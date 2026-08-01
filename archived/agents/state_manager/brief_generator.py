import json

from src.core.agent_base import BaseAgent
from src.storage.sqlite_store import SQLiteStore


class BriefGenerator(BaseAgent):
    """4B — 简报生成师"""

    def __init__(self, novel_id: str, sqlite_store: SQLiteStore):
        super().__init__("brief_generator", novel_id, "brief_generator.txt")
        self.sqlite = sqlite_store

    def generate(self, chapter_index: int, chapter_outline: str,
                 world_setting: str = "", plot_structure: str = "") -> str:
        """为下一章生成写作简报"""
        all_states = self.sqlite.export_all_states(self.novel_id)

        parts = [f"## 第 {chapter_index} 章大纲\n{chapter_outline}"]

        if world_setting:
            parts.insert(0, f"## 世界观设定\n{world_setting[:3000]}")
        if plot_structure:
            parts.insert(1, f"## 情节大纲\n{plot_structure[:3000]}")

        parts.append(f"## 当前状态总览\n```json\n{json.dumps(all_states, ensure_ascii=False, indent=2)}\n```")

        user_msg = "\n\n".join(parts) + "\n\n请为本章生成写作简报。"

        result = self.run(
            user_message=user_msg,
            save_category="briefs",
            save_prefix=f"brief_ch{chapter_index:04d}",
        )
        return result.content
