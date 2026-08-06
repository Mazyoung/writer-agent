"""Thin provider adapter for model slots; Agents never select providers."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from src.config.settings import ModelSlot

try:
    from anthropic import Anthropic
except ImportError:  # Optional until an anthropic slot is actually selected.
    Anthropic = None


def validate_model_slot(slot: ModelSlot) -> None:
    """Validate only the slot that is about to construct an SDK client."""
    prefix = slot.name.upper()
    problems: list[str] = []
    if slot.provider not in {"deepseek", "openai_compatible", "anthropic"}:
        problems.append(f"provider 无效（{prefix}_PROVIDER）")
    if not slot.api_key:
        if slot.name == "system":
            problems.append(f"缺少 API key（{prefix}_API_KEY）")
        else:
            problems.append(
                f"缺少 API key（{prefix}_API_KEY；"
                "connection 继承时请配置 SYSTEM_API_KEY）"
            )
    if slot.provider in {"deepseek", "openai_compatible"} and not slot.base_url:
        problems.append(f"缺少 Base URL（{prefix}_BASE_URL）")
    if not slot.model:
        problems.append(f"缺少 model（{prefix}_MODEL）")
    if isinstance(slot.max_tokens, bool) or not isinstance(slot.max_tokens, int):
        problems.append(f"max_tokens 无效（{prefix}_MAX_TOKENS）")
    elif slot.max_tokens <= 0:
        problems.append(f"max_tokens 必须为正整数（{prefix}_MAX_TOKENS）")
    if problems:
        details = "\n".join(f"  - {problem}" for problem in problems)
        raise ValueError(
            f"配置错误：{prefix} slot 配置无效：\n{details}"
        )


class ModelProviderClient:
    def __init__(self, slot: ModelSlot):
        self.slot = slot
        self.client = None

    def _ensure_client(self) -> None:
        if self.client is not None:
            return
        validate_model_slot(self.slot)
        slot = self.slot
        if slot.provider == "anthropic":
            if Anthropic is None:
                raise RuntimeError(
                    "使用 anthropic provider 需要安装 anthropic 软件包"
                )
            kwargs: dict[str, Any] = {"api_key": slot.api_key}
            if slot.base_url:
                kwargs["base_url"] = slot.base_url
            self.client = Anthropic(**kwargs)
        else:
            self.client = OpenAI(
                api_key=slot.api_key,
                base_url=slot.base_url or None,
            )

    def complete(self, messages: list[dict[str, str]]) -> str:
        self._ensure_client()
        if self.slot.provider == "anthropic":
            return self._complete_anthropic(messages)
        kwargs: dict[str, Any] = {
            "model": self.slot.model,
            "messages": messages,
            "max_tokens": self.slot.max_tokens,
        }
        response = self.client.chat.completions.create(**kwargs)
        return str(response.choices[0].message.content or "")

    def _complete_anthropic(
        self,
        messages: list[dict[str, str]],
    ) -> str:
        system_parts = [
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "system"
        ]
        conversation = [
            {
                "role": message["role"],
                "content": str(message.get("content", "")),
            }
            for message in messages
            if message.get("role") in {"user", "assistant"}
        ]
        response = self.client.messages.create(
            model=self.slot.model,
            max_tokens=self.slot.max_tokens,
            system="\n\n".join(part for part in system_parts if part),
            messages=conversation,
        )
        for block in response.content:
            text = getattr(block, "text", None)
            if text is not None:
                return str(text)
        raise RuntimeError("Anthropic 响应中没有文本内容")
