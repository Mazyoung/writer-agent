"""Focused pre-retrieval agent that produces the sole embedding query."""

from __future__ import annotations

from collections.abc import Callable

from src.config.settings import get_settings
from src.core.model_provider import ModelProviderClient
from src.core.token_guard import guard_planning_context


SEVERE_QUERY_INTENT_CHARS = 10_000


class QueryIntentBuilder:
    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.slot = settings.get_query_intent_slot()
        self.client = ModelProviderClient(self.slot)
        prompt_path = settings.prompts_dir / "query_intent_builder.txt"
        self.system_prompt = prompt_path.read_text(encoding="utf-8")

    @staticmethod
    def _user_prompt(
        *,
        volume_plan: str,
        recent_chapter_end: str,
        current_state: str,
        human_intent: str,
        correction: str = "",
    ) -> str:
        priority = (
            "Human Chapter Intent 是最高优先级指令；必须完整保留其核心要求。"
            if human_intent.strip()
            else "本章没有 Human Chapter Intent，请根据其他正式状态推导检索目的。"
        )
        parts = [
            "## Volume Plan（完整）\n" + (volume_plan or "无"),
            "## Recent Chapter End（完整段落窗口）\n"
            + (recent_chapter_end or "无"),
            "## Current State（完整）\n" + (current_state or "无"),
            "## Human Chapter Intent（作者原文）\n"
            + (human_intent or "无"),
            "## 优先级\n" + priority,
        ]
        if correction:
            parts.append("## 严重超长纠正要求\n" + correction)
        parts.append(
            "---\n只生成高度精炼的历史检索 Query Intent。"
            "不要生成 Chapter Plan，不要复述完整输入。"
        )
        return "\n\n".join(parts)

    def _call(self, user_prompt: str) -> str:
        return self.client.complete([
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ]).strip()

    def build(
        self,
        *,
        volume_plan: str,
        recent_chapter_end: str,
        current_state: str,
        human_intent: str = "",
        on_attempt: Callable[[str, int], None] | None = None,
    ) -> str:
        documents = {
            "volume_plan.md": volume_plan,
            "recent_chapter_end": recent_chapter_end,
            "current_state.md": current_state,
            "human_intent": human_intent,
        }
        guard_planning_context(self.slot, documents)
        prompt = self._user_prompt(
            volume_plan=volume_plan,
            recent_chapter_end=recent_chapter_end,
            current_state=current_state,
            human_intent=human_intent,
        )
        first = self._call(prompt)
        if not first:
            raise ValueError("Query Intent Builder 返回空内容")
        if len(first) < SEVERE_QUERY_INTENT_CHARS:
            if on_attempt:
                on_attempt("finalized", 1)
            return first

        if on_attempt:
            on_attempt("retried", 2)
        correction = f"""你上一次生成的 Query Intent 严重超长，已经达到 {len(first)} 字。

Query Intent 只是用于 Embedding 历史检索，不是 Chapter Plan，也不是剧情总结。

必须显著压缩：
- 删除重复说明和无关背景；
- 不复述完整 Volume Plan 或 Current State；
- 只保留真正需要检索的历史人物、事件、物品、关系、伏笔、地点和约束；
- Human Intent 中的核心要求必须保留。

目标最好在 1000 字以内，通常不要超过 3000 字。

## 上一次 Query Intent
{first}"""
        retry_documents = {**documents, "previous_query_intent": first}
        guard_planning_context(self.slot, retry_documents)
        retry_prompt = self._user_prompt(
            volume_plan=volume_plan,
            recent_chapter_end=recent_chapter_end,
            current_state=current_state,
            human_intent=human_intent,
            correction=correction,
        )
        second = self._call(retry_prompt)
        if not second:
            raise ValueError("Query Intent Builder 纠正后返回空内容")
        if len(second) >= SEVERE_QUERY_INTENT_CHARS:
            raise ValueError(
                "Query Intent 生成失败：模型连续生成了超过 10000 字的"
                "检索意图，系统不会静默截断后继续检索。"
                "请检查 QUERY_INTENT_MODEL 或相关输入后重新执行。"
            )
        if on_attempt:
            on_attempt("finalized", 2)
        return second
