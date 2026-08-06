import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


SUPPORTED_PROVIDERS = {"deepseek", "openai_compatible", "anthropic"}


@dataclass(frozen=True)
class ModelSlot:
    name: str
    provider: str
    api_key: str
    base_url: str
    model: str
    max_tokens: int


@dataclass(frozen=True)
class AgentModelPolicy:
    slot: str


AGENT_MODEL_POLICIES = {
    "world_builder": AgentModelPolicy("architect"),
    "plot_designer": AgentModelPolicy("architect"),
    "chapter_planner": AgentModelPolicy("plan"),
    "plan_reviewer": AgentModelPolicy("plan"),
    "deepseek_writer": AgentModelPolicy("write"),
    "writer": AgentModelPolicy("write"),
    "stylist": AgentModelPolicy("write"),
    "state_manager": AgentModelPolicy("system"),
}


class Settings:
    """Global runtime configuration loaded from four model slots."""

    def __init__(self, env_file: Optional[str] = None):
        self.project_root = Path(__file__).parent.parent.parent
        load_dotenv(env_file or (self.project_root / ".env"))
        self.data_dir = self.project_root / "data"
        self.prompts_dir = Path(__file__).parent / "prompts"

        system_provider = (
            os.getenv("SYSTEM_PROVIDER", "").strip().lower() or "deepseek"
        )
        self._validate_provider("SYSTEM_PROVIDER", system_provider)
        system_api_key = os.getenv("SYSTEM_API_KEY", "").strip()
        system_base_url = os.getenv("SYSTEM_BASE_URL", "").strip()
        if not system_base_url and system_provider == "deepseek":
            system_base_url = "https://api.deepseek.com"
        system = ModelSlot(
            name="system",
            provider=system_provider,
            api_key=system_api_key,
            base_url=system_base_url,
            model=os.getenv("SYSTEM_MODEL", "").strip(),
            max_tokens=self._positive_integer("SYSTEM_MAX_TOKENS", "16384"),
        )

        self._model_slots = {"system": system}
        max_token_defaults = {
            "architect": "32768",
            "plan": "16384",
            "write": "32768",
        }
        for slot_name, max_tokens_default in max_token_defaults.items():
            prefix = slot_name.upper()
            configured_provider = os.getenv(
                f"{prefix}_PROVIDER", ""
            ).strip().lower()
            provider = configured_provider or system.provider
            self._validate_provider(f"{prefix}_PROVIDER", provider)
            configured_api_key = os.getenv(f"{prefix}_API_KEY", "").strip()
            configured_base_url = os.getenv(f"{prefix}_BASE_URL", "").strip()
            if configured_provider:
                api_key = configured_api_key
                base_url = configured_base_url
            else:
                api_key = configured_api_key or system.api_key
                base_url = configured_base_url or system.base_url
            self._model_slots[slot_name] = ModelSlot(
                name=slot_name,
                provider=provider,
                api_key=api_key,
                base_url=base_url,
                model=os.getenv(f"{prefix}_MODEL", "").strip(),
                max_tokens=self._positive_integer(
                    f"{prefix}_MAX_TOKENS", max_tokens_default
                ),
            )

        self.chapter_mode = os.getenv("CHAPTER_MODE", "agent").strip().lower()
        if self.chapter_mode not in {"agent", "human"}:
            raise ValueError(
                "CHAPTER_MODE 只支持 agent 或 human，"
                f"当前值为 {self.chapter_mode!r}"
            )

        self.rag_chunk_size = 800
        self.rag_chunk_overlap = 100
        self.rag_top_k = self._positive_integer("RAG_TOP_K", "5")
        self.auto_savepoint_every = self._nonnegative_integer(
            "AUTO_SAVEPOINT_EVERY", "0"
        )
        self.embedding_mode = os.getenv(
            "EMBEDDING_MODE", "local"
        ).strip().lower()
        if self.embedding_mode not in {"local", "api"}:
            raise ValueError(
                "EMBEDDING_MODE 只支持 local 或 api，"
                f"当前值为 {self.embedding_mode!r}"
            )
        self.embedding_api_key = os.getenv(
            "EMBEDDING_API_KEY", ""
        ).strip()
        self.embedding_base_url = os.getenv(
            "EMBEDDING_BASE_URL", ""
        ).strip()
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "").strip()
        raw_dimensions = os.getenv("EMBEDDING_DIMENSIONS", "").strip()
        if raw_dimensions:
            try:
                self.embedding_dimensions = int(raw_dimensions)
            except ValueError as exc:
                raise ValueError(
                    "EMBEDDING_DIMENSIONS 必须留空或填写正整数，"
                    f"当前值为 {raw_dimensions!r}"
                ) from exc
            if self.embedding_dimensions <= 0:
                raise ValueError(
                    "EMBEDDING_DIMENSIONS 必须留空或填写正整数，"
                    f"当前值为 {raw_dimensions!r}"
                )
        else:
            self.embedding_dimensions = None

    @staticmethod
    def _validate_provider(variable: str, provider: str) -> None:
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"{variable} 只支持 "
                f"{', '.join(sorted(SUPPORTED_PROVIDERS))}，当前值为 {provider!r}"
            )

    @staticmethod
    def _positive_integer(variable: str, default: str) -> int:
        raw = os.getenv(variable, default).strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{variable} 必须是正整数，当前值为 {raw!r}"
            ) from exc
        if value <= 0:
            raise ValueError(
                f"{variable} 必须是正整数，当前值为 {raw!r}"
            )
        return value

    @staticmethod
    def _nonnegative_integer(variable: str, default: str) -> int:
        raw = os.getenv(variable, default).strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{variable} 必须是 0 或正整数，当前值为 {raw!r}"
            ) from exc
        if value < 0:
            raise ValueError(
                f"{variable} 必须是 0 或正整数，当前值为 {raw!r}"
            )
        return value

    def get_model_slot(self, slot_name: str) -> ModelSlot:
        normalized = str(slot_name).strip().lower()
        if normalized not in self._model_slots:
            raise ValueError(
                f"未知 model slot：{slot_name!r}；应为 "
                "architect、plan、write、system 之一"
            )
        return self._model_slots[normalized]

    def get_agent_policy(self, agent_name: str) -> AgentModelPolicy:
        if agent_name not in AGENT_MODEL_POLICIES:
            raise ValueError(
                f"未知 agent：{agent_name}。可用值："
                f"{list(AGENT_MODEL_POLICIES)}"
            )
        return AGENT_MODEL_POLICIES[agent_name]

    # Compatibility-only aliases for older callers/tests. Core Agent code uses slots.
    @property
    def api_key(self) -> str:
        return self.get_model_slot("system").api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        self._model_slots["system"] = replace(
            self._model_slots["system"], api_key=str(value or "")
        )

    @property
    def base_url(self) -> str:
        return self.get_model_slot("system").base_url

    @base_url.setter
    def base_url(self, value: str) -> None:
        self._model_slots["system"] = replace(
            self._model_slots["system"], base_url=str(value or "")
        )


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
