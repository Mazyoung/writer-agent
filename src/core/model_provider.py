"""Thin provider adapter for model slots; Agents never select providers."""

from __future__ import annotations

from typing import Any

from openai import OpenAI

from src.config.settings import ModelSlot

try:
    from anthropic import Anthropic
except ImportError:  # Optional until an anthropic slot is actually selected.
    Anthropic = None


class ModelProviderClient:
    def __init__(self, slot: ModelSlot):
        self.slot = slot
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

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        thinking: bool = False,
        max_tokens: int = 8192,
    ) -> str:
        if self.slot.provider == "anthropic":
            return self._complete_anthropic(
                messages, temperature=temperature, max_tokens=max_tokens
            )
        kwargs: dict[str, Any] = {
            "model": self.slot.model,
            "messages": messages,
            "temperature": temperature,
        }
        if self.slot.provider == "deepseek" and thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        response = self.client.chat.completions.create(**kwargs)
        return str(response.choices[0].message.content or "")

    def _complete_anthropic(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
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
            max_tokens=max_tokens,
            temperature=temperature,
            system="\n\n".join(part for part in system_parts if part),
            messages=conversation,
        )
        for block in response.content:
            text = getattr(block, "text", None)
            if text is not None:
                return str(text)
        raise RuntimeError("Anthropic 响应中没有文本内容")
