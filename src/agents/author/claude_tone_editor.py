"""
Legacy tone-editor entry point using the explicit WRITE model slot.

用法:
  from src.agents.author.claude_tone_editor import ClaudeToneEditor
  editor = ClaudeToneEditor(novel_id)
  edited = editor.edit(scene_text, scene_spec, chapter_index, scene_index)
"""

import sys
from pathlib import Path
from typing import Optional

from src.config.settings import get_settings
from src.core.model_provider import ModelProviderClient
from src.storage.file_store import FileStore


class ClaudeToneEditor:
    """Claude 调性编辑器"""

    # Claude ToneEditor 系统提示词的文件名
    PROMPT_FILE = "tone_editor_claude.txt"

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.file_store = FileStore(novel_id, settings.data_dir)
        self.model_slot = settings.get_model_slot("write")
        self.provider_client = ModelProviderClient(self.model_slot)
        self._prompt = self._load_prompt(settings.prompts_dir)

    def _load_prompt(self, prompts_dir: Path) -> str:
        path = prompts_dir / self.PROMPT_FILE
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    @property
    def use_api(self) -> bool:
        """WRITE slot always selects one explicit API provider."""
        return True

    def edit(self, scene_text: str, scene_spec: str,
             chapter_index: int, scene_index: int = 0) -> str:
        """
        对场景/章节文本进行调性编辑。

        API 模式: 直接调用 Anthropic API 并返回结果。
        文件模式: 保存草稿，提示用户在 Claude Code 中编辑，然后读取结果。
        """
        user_msg = self._build_user_message(scene_text, scene_spec,
                                            chapter_index, scene_index)
        save_prefix = f"scene_ch{chapter_index:04d}_s{scene_index:02d}_claude_draft"

        return self._edit_via_api(user_msg, save_prefix)

    def edit_chapter_full(self, chapter_text: str, scene_plan_text: str,
                          chapter_index: int) -> str:
        """整章调性编辑"""
        user_msg = f"""## 第 {chapter_index} 章场景规划（用于对齐检查）
{scene_plan_text}

## 原始正文
{chapter_text}

---
请调整叙事语气，检查场景规划对齐。"""
        save_prefix = f"chapter_{chapter_index:04d}_claude_draft"

        return self._edit_via_api(user_msg, save_prefix)

    def _build_user_message(self, scene_text: str, scene_spec: str,
                            chapter_index: int, scene_index: int) -> str:
        return f"""## 第 {chapter_index} 章 场景 {scene_index}

### 场景写作指令（用于对齐检查）
{scene_spec}

### 原始正文
{scene_text}

---
请调整叙事语气，检查场景规划对齐。"""

    def _edit_via_api(self, user_msg: str, save_prefix: str) -> str:
        """Call the configured WRITE provider."""
        result = self.provider_client.complete(
            [
                {"role": "system", "content": self._prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
        )
        self.file_store.save("chapters", save_prefix, result)
        return result

    def _edit_via_file(self, user_msg: str, save_prefix: str,
                       chapter_index: int) -> str:
        """
        文件模式：保存完整上下文到临时文件，提示用户让 Claude 编辑。
        如果存在已编辑的 _claude_edited 文件则直接读取返回。
        """
        edited_prefix = save_prefix.replace("_draft", "_edited")

        # 检查是否已经有编辑后的版本
        existing = self.file_store.load_latest("chapters", edited_prefix)
        if existing:
            print(f"  [ClaudeToneEditor] 读取已有 Claude 编辑版本")
            return existing

        # 保存上下文供 Claude Code 编辑
        context = f"{self._prompt}\n\n---\n\n{user_msg}"
        draft_path = self.file_store.save("chapters", save_prefix, context)

        print(f"\n  ╔══════════════════════════════════════════════════════════╗")
        print(f"  ║  Claude 调性编辑 — 请在 Claude Code 中执行:             ║")
        print(f"  ║                                                        ║")
        print(f"  ║  读取草稿: {draft_path.name:43s} ║")
        print(f"  ║  应用调性编辑后保存到:                                 ║")
        print(f"  ║  chapters/{edited_prefix}.md                          ║")
        print(f"  ║                                                        ║")
        print(f"  ║  或直接对我说: /tone-edit 第{chapter_index}章           ║")
        print(f"  ╚══════════════════════════════════════════════════════════╝")
        print(f"  [ClaudeToneEditor] 当前使用原始草稿继续流水线...")

        # 返回原始文本（流水线继续，Claude 编辑异步进行）
        # 从 user_msg 中提取原始正文
        marker = "### 原始正文"
        if marker in user_msg:
            return user_msg.split(marker, 1)[1].split("---", 1)[0].strip()
        return ""
