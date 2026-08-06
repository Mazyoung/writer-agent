"""Simple fail-closed guard for complete formal planning documents."""

from __future__ import annotations

from collections.abc import Mapping

from src.config.settings import ModelSlot


PLANNING_CONTEXT_SAFETY_TOKENS = 100_000


def estimate_tokens(text: str) -> int:
    """Conservative dependency-free estimate; never used to truncate content."""
    return (len(text) + 3) // 4 if text else 0


def guard_planning_context(
    slot: ModelSlot,
    documents: Mapping[str, str],
) -> dict[str, int]:
    estimates = {
        name: estimate_tokens(content)
        for name, content in documents.items()
    }
    input_tokens = sum(estimates.values())
    if input_tokens + slot.max_tokens <= PLANNING_CONTEXT_SAFETY_TOKENS:
        return estimates
    details = "\n".join(
        f"  {name:<20} 约 {tokens} tokens"
        for name, tokens in estimates.items()
    )
    raise ValueError(
        "输入上下文已接近安全上限。\n\n"
        f"{details}\n"
        f"  输出预留             {slot.max_tokens} tokens\n\n"
        "系统不会自动截断正式规划内容。\n"
        "请优先精简较大的规划文件后重新执行。"
    )
