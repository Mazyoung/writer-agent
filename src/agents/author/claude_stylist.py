"""
Stylist — 风格编辑器（Claude 优先，DeepSeek 替代）。

模式:
- ANTHROPIC_API_KEY 已设置 → Claude API（更高质量）
- 无 Anthropic key → DeepSeek API（零配置，质量足够）
"""

from pathlib import Path
from openai import OpenAI

from src.config.settings import get_settings
from src.storage.file_store import FileStore


class ClaudeStylist:
    """风格编辑器 — Claude 优先，DeepSeek 备用"""

    PROMPT_FILE = "tone_editor_claude.txt"

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.file_store = FileStore(novel_id, settings.data_dir)
        self.settings = settings
        self.anthropic_key = settings.anthropic_api_key
        self._prompt = self._load_prompt(settings.prompts_dir)

        # DeepSeek client（复用项目配置）
        self.ds_client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
        )
        self.ds_model = settings.resolve_model_name("deepseek_writer")

    def _load_prompt(self, prompts_dir: Path) -> str:
        path = prompts_dir / self.PROMPT_FILE
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    @property
    def use_claude(self) -> bool:
        key = self.anthropic_key or ""
        return key.startswith("sk-ant-")

    # ── 主入口 ───────────────────────────────────────

    def edit_chapter(self, draft_text: str, chapter_index: int,
                     emotion_palette: str = "",
                     scene_plan_text: str = "",
                     style_feedback: str = "") -> str:
        """风格编辑 — 只负责 LLM 转换，返回 styled text。

        E05: 不再保存文件、不执行 StyleChecker。
        Orchestrator 负责所有 workflow side effects。
        """
        user_msg = self._build_message(draft_text, chapter_index,
                                        emotion_palette, scene_plan_text,
                                        style_feedback)

        if self.use_claude:
            result = self._call_claude(user_msg)
        else:
            result = self._call_deepseek(user_msg)

        return result

    def edit_scene(self, scene_text: str, scene_index: int,
                   chapter_index: int, emotion_palette: str = "",
                   style_feedback: str = "") -> str:
        user_msg = f"""## 第 {chapter_index} 章 场景 {scene_index}

### 情感调色板
{emotion_palette or "无特殊要求"}

### 人工反馈
{style_feedback or "无"}

### 原始正文
{scene_text}

---
请调整叙事语气。"""
        save_prefix = f"scene_ch{chapter_index:04d}_s{scene_index:02d}_styled"

        if self.use_claude:
            result = self._call_claude(user_msg)
        else:
            result = self._call_deepseek(user_msg)

        self.file_store.save("chapters", save_prefix, result)
        return result

    # ── 内部 ─────────────────────────────────────────

    def _build_message(self, draft: str, ch_idx: int,
                       emotion: str, plan: str, feedback: str) -> str:
        parts = [f"## 第 {ch_idx} 章"]
        if feedback:
            parts.append(f"### [最高优先级] 人工反馈\n{feedback}")
        if emotion:
            parts.append(f"### 情感调色板\n{emotion}")
        if plan:
            parts.append(f"### 场景规划（用于对齐检查）\n{plan[:3000]}")
        parts.append(f"### 原始正文\n{draft}")
        parts.append("---\n请调整叙事语气，检查场景规划对齐。")
        return "\n\n".join(parts)

    def _call_claude(self, user_msg: str) -> str:
        try:
            import anthropic
        except ImportError:
            print("  [Stylist] anthropic 未安装，使用 DeepSeek")
            return self._call_deepseek(user_msg)

        if not self.anthropic_key:
            return self._call_deepseek(user_msg)

        client = anthropic.Anthropic(api_key=self.anthropic_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            temperature=0.7,
            system=self._prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        # 提取文本块（跳过 thinking 块）
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        return response.content[0].text

    def _call_deepseek(self, user_msg: str) -> str:
        """DeepSeek V4 做风格编辑 — 与 Claude 使用完全相同的 prompt。"""
        response = self.ds_client.chat.completions.create(
            model=self.ds_model,
            temperature=0.7,
            messages=[
                {"role": "system", "content": self._prompt},
                {"role": "user", "content": user_msg},
            ],
        )
        return response.choices[0].message.content
