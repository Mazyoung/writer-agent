"""Immutable novel Embedding schema plus explicit API vector generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from openai import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
)
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from src.config.settings import Settings, get_settings


EMBEDDING_SCHEMA = 1
PROBE_TEXT = "writer-agent embedding initialization probe"


@dataclass(frozen=True)
class NovelEmbeddingConfig:
    novel_id: str
    embedding_mode: str
    embedding_model: str
    embedding_dimensions: int
    embedding_request_dimensions: bool = False
    embedding_schema: int = EMBEDDING_SCHEMA


class NovelEmbeddingConfigStore:
    """Keep immutable vector-space identity outside Savepoint-restored state."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path_for(self, novel_id: str) -> Path:
        return (
            self.data_dir / "novels" / ".internal" / novel_id
            / "embedding.json"
        )

    def exists(self, novel_id: str) -> bool:
        return self.path_for(novel_id).is_file()

    def create(self, config: NovelEmbeddingConfig) -> NovelEmbeddingConfig:
        if config.embedding_mode not in {"local", "api"}:
            raise ValueError(f"不支持的 embedding_mode：{config.embedding_mode}")
        if not config.embedding_model.strip() or config.embedding_dimensions <= 0:
            raise ValueError("Embedding model 和 dimensions 必须是已 probe 的有效值")
        path = self.path_for(config.novel_id)
        if path.exists():
            current = self.load(config.novel_id)
            if current != config:
                raise ValueError(
                    f"小说 {config.novel_id} 的 Embedding vector space 已永久固定"
                )
            return current
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "novel_id": config.novel_id,
            "embedding_mode": config.embedding_mode,
            "embedding_model": config.embedding_model,
            "embedding_dimensions": config.embedding_dimensions,
            "embedding_request_dimensions": config.embedding_request_dimensions,
            "embedding_schema": config.embedding_schema,
        }
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
        except FileExistsError:
            current = self.load(config.novel_id)
            if current != config:
                raise ValueError(
                    f"小说 {config.novel_id} 的 Embedding vector space 已永久固定"
                )
            return current
        return self.load(config.novel_id)

    def load(self, novel_id: str) -> NovelEmbeddingConfig:
        path = self.path_for(novel_id)
        if not path.is_file():
            raise ValueError(
                f"小说 {novel_id} 缺少内部 Embedding 配置：{path}；"
                "当前系统不支持 legacy fallback"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"小说内部 Embedding 配置无法读取：{path}") from exc
        if payload.get("embedding_schema") != EMBEDDING_SCHEMA:
            raise ValueError("小说内部 embedding_schema 不受支持")
        if payload.get("novel_id") != novel_id:
            raise ValueError("小说内部 Embedding 配置的 novel_id 不匹配")
        mode = str(payload.get("embedding_mode", ""))
        model = str(payload.get("embedding_model", "")).strip()
        dimensions = payload.get("embedding_dimensions")
        request_dimensions = payload.get("embedding_request_dimensions")
        if mode not in {"local", "api"}:
            raise ValueError(f"小说内部 embedding_mode 无效：{mode!r}")
        if not model or not isinstance(dimensions, int) or dimensions <= 0:
            raise ValueError("小说内部 Embedding model/dimensions 无效")
        if not isinstance(request_dimensions, bool):
            raise ValueError(
                "小说内部 embedding_request_dimensions 无效；"
                "当前系统不支持 legacy fallback"
            )
        return NovelEmbeddingConfig(
            novel_id=novel_id,
            embedding_mode=mode,
            embedding_model=model,
            embedding_dimensions=dimensions,
            embedding_request_dimensions=request_dimensions,
        )


def _local_identity(function: DefaultEmbeddingFunction) -> str:
    return f"{type(function).__module__}.{type(function).__name__}:{function.name()}"


def _validate_vector(vector: list[float], expected: int, *, initialization: bool) -> None:
    actual = len(vector)
    if actual == expected:
        return
    if initialization:
        raise ValueError(
            "Embedding 配置错误。\n\n"
            f"配置的向量维度：{expected}\n"
            f"API 实际返回维度：{actual}\n\n"
            "请检查 EMBEDDING_MODEL 或 EMBEDDING_DIMENSIONS。\n\n"
            "小说尚未初始化，没有产生正式数据。"
        )
    raise ValueError(
        "Embedding 向量维度不一致。\n\n"
        f"当前小说固定维度：{expected}\n"
        f"API 当前返回维度：{actual}\n\n"
        "为避免破坏现有 Chroma 向量空间，本次操作已中止。\n\n"
        "请检查 EMBEDDING_BASE_URL 和模型服务配置。"
    )


def _api_vectors(
    *, api_key: str, base_url: str, model: str,
    texts: list[str], dimensions: int | None,
    client_factory: Callable = OpenAI,
) -> list[list[float]]:
    client = client_factory(api_key=api_key, base_url=base_url)
    kwargs = {"model": model, "input": list(texts)}
    if dimensions is not None:
        kwargs["dimensions"] = dimensions
    response = client.embeddings.create(**kwargs)
    ordered = sorted(response.data, key=lambda item: item.index)
    return [list(item.embedding) for item in ordered]


def probe_new_embedding(
    novel_id: str,
    settings: Settings,
    *,
    api_client_factory: Callable = OpenAI,
    local_function_factory: Callable = DefaultEmbeddingFunction,
) -> NovelEmbeddingConfig:
    """Probe before init; this writes no novel data or internal metadata."""
    if settings.embedding_mode == "local":
        function = local_function_factory()
        vector = list(function([PROBE_TEXT])[0])
        if not vector:
            raise ValueError("Chroma 内置 Embedding probe 未返回向量")
        return NovelEmbeddingConfig(
            novel_id=novel_id,
            embedding_mode="local",
            embedding_model=_local_identity(function),
            embedding_dimensions=len(vector),
        )

    missing = []
    if not settings.embedding_api_key:
        missing.append("EMBEDDING_API_KEY")
    if not settings.embedding_base_url:
        missing.append("EMBEDDING_BASE_URL")
    if not settings.embedding_model:
        missing.append("EMBEDDING_MODEL")
    if missing:
        raise ValueError(
            "错误：API Embedding 初始化缺少环境变量：" + "、".join(missing)
        )
    try:
        vectors = _api_vectors(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            model=settings.embedding_model,
            texts=[PROBE_TEXT],
            dimensions=settings.embedding_dimensions,
            client_factory=api_client_factory,
        )
    except APIConnectionError as exc:
        raise ValueError("Embedding 配置错误：无法连接 EMBEDDING_BASE_URL") from exc
    except AuthenticationError as exc:
        raise ValueError("Embedding 配置错误：EMBEDDING_API_KEY 无效") from exc
    except (BadRequestError, NotFoundError) as exc:
        raise ValueError("Embedding 配置错误：EMBEDDING_MODEL 不可用") from exc
    except Exception as exc:
        raise ValueError(
            f"Embedding probe 失败：{type(exc).__name__}；请检查 API 地址与模型"
        ) from exc
    if len(vectors) != 1 or not vectors[0]:
        raise ValueError("Embedding probe 未返回有效向量")
    actual_dimensions = len(vectors[0])
    if settings.embedding_dimensions is not None:
        _validate_vector(
            vectors[0], settings.embedding_dimensions, initialization=True
        )
    return NovelEmbeddingConfig(
        novel_id=novel_id,
        embedding_mode="api",
        embedding_model=settings.embedding_model,
        embedding_dimensions=actual_dimensions,
        embedding_request_dimensions=settings.embedding_dimensions is not None,
    )


class NovelEmbeddingRuntime:
    def __init__(
        self,
        config: NovelEmbeddingConfig,
        settings: Settings | None = None,
        *,
        api_client_factory: Callable = OpenAI,
    ):
        self.config = config
        self.settings = settings or get_settings()
        self._api_client_factory = api_client_factory
        if config.embedding_mode == "api":
            missing = []
            if not self.settings.embedding_api_key:
                missing.append("EMBEDDING_API_KEY")
            if not self.settings.embedding_base_url:
                missing.append("EMBEDDING_BASE_URL")
            if missing:
                raise ValueError(
                    "错误：当前小说使用 API Embedding，但环境中未配置 "
                    + "、".join(missing)
                )

    @property
    def is_api(self) -> bool:
        return self.config.embedding_mode == "api"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.is_api:
            raise ValueError("local 模式由 Chroma 内置 Embedding 处理")
        vectors = _api_vectors(
            api_key=self.settings.embedding_api_key,
            base_url=self.settings.embedding_base_url,
            model=self.config.embedding_model,
            texts=texts,
            dimensions=(
                self.config.embedding_dimensions
                if self.config.embedding_request_dimensions else None
            ),
            client_factory=self._api_client_factory,
        )
        if len(vectors) != len(texts):
            raise ValueError("Embedding API 返回的向量数量与输入数量不一致")
        for vector in vectors:
            _validate_vector(
                vector, self.config.embedding_dimensions, initialization=False
            )
        return vectors


def load_embedding_runtime(
    data_dir: Path, novel_id: str, settings: Settings | None = None
) -> NovelEmbeddingRuntime:
    config = NovelEmbeddingConfigStore(data_dir).load(novel_id)
    return NovelEmbeddingRuntime(config, settings)
