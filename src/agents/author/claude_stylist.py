"""Stylist using the explicit WRITE model slot."""

from pathlib import Path

from src.config.settings import get_settings
from src.core.model_provider import ModelProviderClient
from src.core.token_guard import guard_planning_context
from src.storage.file_store import FileStore


class ClaudeStylist:
    """Compatibility class name; provider/model are owned by WRITE."""

    PROMPT_FILE = "tone_editor_claude.txt"

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.file_store = FileStore(novel_id, settings.data_dir)
        self._prompt = self._load_prompt(settings.prompts_dir)
        self.model_slot = settings.get_model_slot("write")
        self.provider_client = ModelProviderClient(self.model_slot)

    def _load_prompt(self, prompts_dir: Path) -> str:
        path = prompts_dir / self.PROMPT_FILE
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def edit_chapter(
        self,
        draft_text: str,
        chapter_index: int,
        chapter_plan_text: str = "",
        **legacy_context: str,
    ) -> str:
        if not chapter_plan_text:
            chapter_plan_text = legacy_context.get("scene_plan_text", "")
        guard_planning_context(self.model_slot, {
            "chapter_plan.md": chapter_plan_text,
            "candidate_prose.md": draft_text,
        })
        user_msg = self._build_message(
            draft_text,
            chapter_index,
            chapter_plan_text,
        )
        return self._call_write_slot(user_msg)

    def edit_scene(
        self,
        scene_text: str,
        scene_index: int,
        chapter_index: int,
        emotion_palette: str = "",
    ) -> str:
        user_msg = f"""## 第 {chapter_index} 章 场景 {scene_index}

### 情感调色板
{emotion_palette or "无特殊要求"}

### 原始正文
{scene_text}

---
请调整叙事语气。"""
        result = self._call_write_slot(user_msg)
        self.file_store.save(
            "chapters", f"scene_ch{chapter_index:04d}_s{scene_index:02d}_styled", result
        )
        return result

    def _build_message(
        self, draft: str, ch_idx: int, plan: str
    ) -> str:
        parts = [f"## 第 {ch_idx} 章"]
        if plan:
            parts.append(f"### 已通过审阅的 Chapter Plan（完整原文）\n{plan}")
        parts.append(f"### 原始正文\n{draft}")
        parts.append("---\n请调整叙事语气，检查场景规划对齐。")
        return "\n\n".join(parts)

    def _call_write_slot(self, user_msg: str) -> str:
        return self.provider_client.complete([
            {"role": "system", "content": self._prompt},
            {"role": "user", "content": user_msg},
        ])
