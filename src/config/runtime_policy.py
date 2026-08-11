"""Per-novel runtime policy without process-global environment mutation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from src.config.settings import Settings, get_settings


NOVEL_RUNTIME_KEYS = frozenset({
    "CHAPTER_MODE",
    "AGENT_EXECUTION",
    "AUTO_SAVEPOINT_EVERY",
    "RAG_TOP_K",
})


def _choice(name: str, value: Any, choices: set[str]) -> str:
    normalized = str(value).strip().lower()
    if normalized not in choices:
        raise ValueError(
            f"{name} 必须是 {', '.join(sorted(choices))}，当前值：{value!r}"
        )
    return normalized


def _integer(name: str, value: Any, *, minimum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数，当前值：{value!r}") from exc
    if parsed < minimum:
        raise ValueError(f"{name} 必须 >= {minimum}，当前值：{parsed}")
    return parsed


@dataclass(frozen=True)
class NovelRuntimePolicy:
    """One command-level snapshot of the allowlisted novel policy."""

    chapter_mode: str
    agent_execution: str
    auto_savepoint_every: int
    rag_top_k: int

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "NovelRuntimePolicy":
        return cls(
            chapter_mode=_choice(
                "CHAPTER_MODE", values["CHAPTER_MODE"], {"agent", "human"}
            ),
            agent_execution=_choice(
                "AGENT_EXECUTION",
                values["AGENT_EXECUTION"],
                {"supervised", "autonomous"},
            ),
            auto_savepoint_every=_integer(
                "AUTO_SAVEPOINT_EVERY", values["AUTO_SAVEPOINT_EVERY"], minimum=0
            ),
            rag_top_k=_integer("RAG_TOP_K", values["RAG_TOP_K"], minimum=1),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "NovelRuntimePolicy":
        return cls.from_mapping({
            "CHAPTER_MODE": getattr(settings, "chapter_mode", "agent"),
            "AGENT_EXECUTION": getattr(settings, "agent_execution", "supervised"),
            "AUTO_SAVEPOINT_EVERY": getattr(settings, "auto_savepoint_every", 0),
            "RAG_TOP_K": getattr(settings, "rag_top_k", 5),
        })

    def to_env_text(self) -> str:
        return (
            "# Writer-Agent 小说级运行配置\n\n"
            f"CHAPTER_MODE={self.chapter_mode}\n"
            f"AGENT_EXECUTION={self.agent_execution}\n"
            f"AUTO_SAVEPOINT_EVERY={self.auto_savepoint_every}\n"
            f"RAG_TOP_K={self.rag_top_k}\n"
        )


def novel_env_path(novel_id: str, settings: Settings | None = None) -> Path:
    resolved = settings or get_settings()
    return resolved.data_dir / "novels" / novel_id / ".env"


def load_novel_runtime_policy(
    novel_id: str, settings: Settings | None = None
) -> NovelRuntimePolicy:
    """Resolve Novel .env > effective Root .env > code defaults."""
    resolved = settings or get_settings()
    base = NovelRuntimePolicy.from_settings(resolved)
    path = novel_env_path(novel_id, resolved)
    if not path.is_file():
        return base

    parsed = dict(dotenv_values(path))
    unsupported = sorted(key for key in parsed if key not in NOVEL_RUNTIME_KEYS)
    if unsupported:
        raise ValueError(
            f"小说级 .env 包含不允许的配置：{', '.join(unsupported)}"
        )
    values: dict[str, Any] = {
        "CHAPTER_MODE": base.chapter_mode,
        "AGENT_EXECUTION": base.agent_execution,
        "AUTO_SAVEPOINT_EVERY": base.auto_savepoint_every,
        "RAG_TOP_K": base.rag_top_k,
    }
    values.update({key: value for key, value in parsed.items() if value is not None})
    return NovelRuntimePolicy.from_mapping(values)


def create_novel_runtime_env(
    novel_id: str, settings: Settings | None = None
) -> Path:
    """Materialize current root defaults for a newly created novel only."""
    resolved = settings or get_settings()
    path = novel_env_path(novel_id, resolved)
    if path.exists():
        raise FileExistsError(f"小说级运行配置已存在：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".env.tmp")
    temporary.write_text(
        NovelRuntimePolicy.from_settings(resolved).to_env_text(), encoding="utf-8"
    )
    temporary.replace(path)
    return path
