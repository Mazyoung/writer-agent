"""Estimate complete model inputs and warn when they exceed slot guidance."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

from src.config.settings import ModelSlot


def estimate_tokens(text: str) -> int:
    """Conservative tokenizer-free estimate for mixed Chinese/ASCII content."""
    if not text:
        return 0
    cjk = len(re.findall(
        r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]",
        text,
    ))
    non_cjk = len(text) - cjk
    base = cjk + math.ceil(non_cjk / 4)
    return math.ceil(base * 1.15)


def guard_planning_context(
    slot: ModelSlot,
    documents: Mapping[str, str],
) -> dict[str, int]:
    estimates = {
        name: estimate_tokens(content)
        for name, content in documents.items()
    }
    input_tokens = sum(estimates.values())
    if input_tokens > slot.max_tokens:
        print(
            f"[Token Warning] {slot.name.upper()} 输入上下文较大\n"
            f"Estimated Input Tokens: {input_tokens}\n"
            f"Configured {slot.name.upper()}_MAX_TOKENS: {slot.max_tokens}\n"
            "当前仅作提示。\n"
            "不会截断、压缩或阻断正式上下文，将继续完整发送。\n"
            "实际请求是否可执行由远端模型 API 决定。"
        )
    return estimates
