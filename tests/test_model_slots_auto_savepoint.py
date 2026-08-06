"""Focused coverage for model slots/providers and interval Savepoints."""

from __future__ import annotations

import os
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from contextlib import redirect_stdout

import main as cli
import httpx
from openai import APIConnectionError, NotFoundError

from src.config.settings import AGENT_MODEL_POLICIES, ModelSlot, Settings
from src.core.model_provider import ModelProviderClient
from src.storage.story_savepoint import SavepointVerificationError
from src.storage.atomic_fact_store import AtomicFactStore
from src.storage.document_formats import AtomicFact
from src.storage.embedding_config import (
    NovelEmbeddingConfig,
    NovelEmbeddingConfigStore,
    NovelEmbeddingRuntime,
    probe_new_embedding,
)
from src.workflows.chapter_runner import (
    _maybe_create_auto_savepoint,
    _novel_mutation_locked,
)


MODEL_ENV_KEYS = {
    "SYSTEM_PROVIDER", "SYSTEM_API_KEY", "SYSTEM_BASE_URL", "SYSTEM_MODEL",
    "ARCHITECT_PROVIDER", "ARCHITECT_API_KEY", "ARCHITECT_BASE_URL",
    "ARCHITECT_MODEL", "PLAN_PROVIDER", "PLAN_API_KEY", "PLAN_BASE_URL",
    "PLAN_MODEL", "WRITE_PROVIDER", "WRITE_API_KEY", "WRITE_BASE_URL",
    "WRITE_MODEL", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
    "CHAPTER_MODE", "RAG_TOP_K", "AUTO_SAVEPOINT_EVERY", "EMBEDDING_MODE",
    "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS",
}


class ModelSlotSettingsTests(unittest.TestCase):
    def _settings(self, text: str) -> Settings:
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", encoding="utf-8", delete=False
        )
        try:
            handle.write(text)
            handle.close()
            missing = object()
            original = {
                key: os.environ.get(key, missing) for key in MODEL_ENV_KEYS
            }
            for key in MODEL_ENV_KEYS:
                os.environ.pop(key, None)
            try:
                return Settings(handle.name)
            finally:
                for key, value in original.items():
                    os.environ.pop(key, None)
                    if value is not missing:
                        os.environ[key] = value
        finally:
            Path(handle.name).unlink(missing_ok=True)

    def test_slot_inheritance_defaults_and_agent_mapping(self):
        settings = self._settings(
            "SYSTEM_PROVIDER=deepseek\n"
            "SYSTEM_API_KEY=system-secret\n"
            "SYSTEM_BASE_URL=https://system.example\n"
            "SYSTEM_MODEL=system-model\n"
            "ARCHITECT_MODEL=architect-model\n"
            "PLAN_MODEL=plan-model\n"
            "WRITE_MODEL=write-model\n"
        )
        write = settings.get_model_slot("write")
        self.assertEqual("deepseek", write.provider)
        self.assertEqual("system-secret", write.api_key)
        self.assertEqual("https://system.example", write.base_url)
        self.assertEqual("write-model", write.model)
        expected = {
            "world_builder": "architect", "plot_designer": "architect",
            "chapter_planner": "plan", "plan_reviewer": "plan",
            "deepseek_writer": "write", "stylist": "write",
            "state_manager": "system",
        }
        self.assertEqual(
            expected,
            {name: AGENT_MODEL_POLICIES[name].slot for name in expected},
        )
        self.assertEqual("local", settings.embedding_mode)

    def test_slot_override_and_legacy_system_fallback(self):
        settings = self._settings(
            "DEEPSEEK_API_KEY=legacy-key\n"
            "DEEPSEEK_BASE_URL=https://legacy.example\n"
            "SYSTEM_MODEL=system-model\n"
            "WRITE_PROVIDER=anthropic\n"
            "WRITE_API_KEY=write-key\n"
            "WRITE_BASE_URL=https://anthropic.example\n"
            "WRITE_MODEL=claude-model\n"
        )
        self.assertEqual("legacy-key", settings.get_model_slot("system").api_key)
        write = settings.get_model_slot("write")
        self.assertEqual(
            ("anthropic", "write-key", "https://anthropic.example", "claude-model"),
            (write.provider, write.api_key, write.base_url, write.model),
        )

    def test_invalid_provider_empty_model_and_auto_interval_fail_fast(self):
        cases = (
            ("SYSTEM_PROVIDER=unknown\n", "SYSTEM_PROVIDER"),
            ("WRITE_MODEL=\n", "WRITE_MODEL"),
            ("AUTO_SAVEPOINT_EVERY=-1\n", "AUTO_SAVEPOINT_EVERY"),
            ("AUTO_SAVEPOINT_EVERY=abc\n", "AUTO_SAVEPOINT_EVERY"),
        )
        for content, variable in cases:
            with self.subTest(variable=variable):
                with self.assertRaisesRegex(ValueError, variable):
                    self._settings(content)


class ModelProviderTests(unittest.TestCase):
    def test_deepseek_sends_thinking_but_openai_compatible_does_not(self):
        for provider, expects_thinking in (
            ("deepseek", True), ("openai_compatible", False)
        ):
            with self.subTest(provider=provider):
                raw_client = MagicMock()
                raw_client.chat.completions.create.return_value = SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
                )
                with patch("src.core.model_provider.OpenAI", return_value=raw_client):
                    client = ModelProviderClient(ModelSlot(
                        provider=provider, api_key="secret",
                        base_url="https://provider.example", model="model-x",
                    ))
                    self.assertEqual(
                        "ok", client.complete(
                            [{"role": "user", "content": "hello"}],
                            temperature=0.3, thinking=True,
                        )
                    )
                kwargs = raw_client.chat.completions.create.call_args.kwargs
                self.assertEqual(expects_thinking, "extra_body" in kwargs)

    def test_anthropic_messages_adapter(self):
        raw_client = MagicMock()
        raw_client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="anthropic-ok")]
        )
        constructor = MagicMock(return_value=raw_client)
        with patch("src.core.model_provider.Anthropic", constructor):
            client = ModelProviderClient(ModelSlot(
                provider="anthropic", api_key="secret",
                base_url="https://anthropic.example", model="claude-x",
            ))
            result = client.complete([
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "hello"},
            ], temperature=0.7, thinking=True)
        self.assertEqual("anthropic-ok", result)
        kwargs = raw_client.messages.create.call_args.kwargs
        self.assertEqual("rules", kwargs["system"])
        self.assertNotIn("thinking", kwargs)


class AutoSavepointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="auto-savepoint-test-"))
        self.runner = SimpleNamespace(
            novel_id="novel-a", chapter_index=10,
            file_store=SimpleNamespace(root=self.temp_dir),
        )

    def _settings(self, interval: int):
        return SimpleNamespace(
            auto_savepoint_every=interval, data_dir=self.temp_dir
        )

    def test_disabled_and_interval_miss_do_not_construct_manager(self):
        for interval in (0, 6):
            with self.subTest(interval=interval), patch(
                "src.workflows.chapter_runner.get_settings",
                return_value=self._settings(interval),
            ), patch(
                "src.workflows.chapter_runner.StorySavepointManager"
            ) as manager_type:
                result = _maybe_create_auto_savepoint(
                    self.runner, {"workflow_status": "DERIVED_READY"}
                )
                self.assertNotIn("auto_savepoint", result)
                manager_type.assert_not_called()

    def test_interval_hit_creates_formal_savepoint(self):
        manager = MagicMock()
        manager.savepoints_root = self.temp_dir / "savepoints"
        manager.create.return_value = {"savepoint_id": "S0010"}
        with patch(
            "src.workflows.chapter_runner.get_settings",
            return_value=self._settings(5),
        ), patch(
            "src.workflows.chapter_runner.StorySavepointManager",
            return_value=manager,
        ):
            result = _maybe_create_auto_savepoint(
                self.runner, {"workflow_status": "DERIVED_READY"}
            )
        manager.create.assert_called_once_with()
        self.assertEqual("CREATED", result["auto_savepoint"]["status"])

    def test_existing_ready_is_idempotent_but_invalid_is_reported(self):
        manager = MagicMock()
        manager.savepoints_root = self.temp_dir / "savepoints"
        (manager.savepoints_root / "S0010").mkdir(parents=True)
        manager.verify.return_value = {"savepoint_id": "S0010"}
        with patch(
            "src.workflows.chapter_runner.get_settings",
            return_value=self._settings(5),
        ), patch(
            "src.workflows.chapter_runner.StorySavepointManager",
            return_value=manager,
        ):
            result = _maybe_create_auto_savepoint(
                self.runner, {"workflow_status": "DERIVED_READY"}
            )
        manager.create.assert_not_called()
        self.assertEqual("EXISTING_READY", result["auto_savepoint"]["status"])

        manager.verify.side_effect = SavepointVerificationError("corrupt")
        with patch(
            "src.workflows.chapter_runner.get_settings",
            return_value=self._settings(5),
        ), patch(
            "src.workflows.chapter_runner.StorySavepointManager",
            return_value=manager,
        ):
            failed = _maybe_create_auto_savepoint(
                self.runner, {"workflow_status": "DERIVED_READY"}
            )
        self.assertEqual("ERROR", failed["auto_savepoint"]["status"])

    def test_repair_derived_ready_triggers_after_operation_lock_release(self):
        manager = MagicMock()
        manager.savepoints_root = self.temp_dir / "savepoints"

        def create_without_nested_lock():
            self.assertFalse((self.temp_dir / ".novel_operation.lock").exists())
            return {"savepoint_id": "S0010"}

        manager.create.side_effect = create_without_nested_lock

        class RepairRunner:
            novel_id = "novel-a"
            chapter_index = 10
            file_store = SimpleNamespace(root=self.temp_dir)

            @_novel_mutation_locked
            def repair_derivation(inner_self):
                self.assertTrue(
                    (self.temp_dir / ".novel_operation.lock").exists()
                )
                return {"workflow_status": "DERIVED_READY"}

        with patch(
            "src.workflows.chapter_runner.get_settings",
            return_value=self._settings(5),
        ), patch(
            "src.workflows.chapter_runner.StorySavepointManager",
            return_value=manager,
        ):
            result = RepairRunner().repair_derivation()
        self.assertEqual("CREATED", result["auto_savepoint"]["status"])


class NovelEmbeddingConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="embedding-config-test-"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _settings(
        self, mode="local", key="", base_url="", model="", dimensions=None
    ):
        return SimpleNamespace(
            data_dir=self.temp_dir,
            embedding_mode=mode,
            embedding_api_key=key,
            embedding_base_url=base_url,
            embedding_model=model,
            embedding_dimensions=dimensions,
        )

    @staticmethod
    def _local_function():
        function = MagicMock()
        function.return_value = [[0.1, 0.2, 0.3]]
        function.name.return_value = "default"
        return function

    def _candidate(
        self, novel_id: str, mode: str, model: str, dimensions: int,
        *, request_dimensions: bool = False,
    ):
        return NovelEmbeddingConfig(
            novel_id=novel_id, embedding_mode=mode,
            embedding_model=model, embedding_dimensions=dimensions,
            embedding_request_dimensions=request_dimensions,
        )

    def test_local_init_rejection_has_no_partial_state(self):
        output = io.StringIO()
        candidate = self._candidate(
            "novel-local", "local", "chromadb.default:default", 3
        )
        with patch.object(cli, "get_settings", return_value=self._settings()), patch.object(
            cli, "probe_new_embedding", return_value=candidate
        ), patch("builtins.input", return_value=""), redirect_stdout(output):
            self.assertFalse(cli._confirm_and_bind_embedding("novel-local"))
        self.assertIn("Chroma 内置 Embedding（本地）", output.getvalue())
        self.assertFalse((self.temp_dir / "novels" / "novel-local").exists())
        self.assertFalse(
            NovelEmbeddingConfigStore(self.temp_dir).exists("novel-local")
        )

    def test_cmd_init_rejection_never_constructs_lifecycle(self):
        args = SimpleNamespace(
            name="novel-local", confirm=False, force=False, premise="hint"
        )
        candidate = self._candidate(
            "novel-local", "local", "chromadb.default:default", 384
        )
        with patch.object(cli, "get_settings", return_value=self._settings()), patch.object(
            cli, "probe_new_embedding", return_value=candidate
        ), patch("builtins.input", return_value=""), patch.object(
            cli, "NovelLifecycleService"
        ) as lifecycle:
            cli.cmd_init(args)
        lifecycle.assert_not_called()
        self.assertFalse((self.temp_dir / "novels" / "novel-local").exists())

    def test_local_and_api_confirmation_persist_vector_space_without_secret(self):
        cases = (
            ("local", "", "", "local-model", 384, "novel-local", "Chroma 内置 Embedding（本地）"),
            (
                "api", "api-secret", "https://embedding.example", "model-a", 1024,
                "novel-api", "OpenAI-compatible Embedding API",
            ),
        )
        store = NovelEmbeddingConfigStore(self.temp_dir)
        for mode, key, base_url, model, dimensions, novel_id, label in cases:
            with self.subTest(mode=mode):
                output = io.StringIO()
                candidate = self._candidate(novel_id, mode, model, dimensions)
                with patch.object(
                    cli, "get_settings",
                    return_value=self._settings(mode, key, base_url, model),
                ), patch.object(
                    cli, "probe_new_embedding", return_value=candidate
                ), patch("builtins.input", return_value="y"), redirect_stdout(output):
                    self.assertTrue(cli._confirm_and_bind_embedding(novel_id))
                self.assertIn(label, output.getvalue())
                persisted_config = store.load(novel_id)
                self.assertEqual((mode, model, dimensions), (
                    persisted_config.embedding_mode,
                    persisted_config.embedding_model,
                    persisted_config.embedding_dimensions,
                ))
                persisted = store.path_for(novel_id).read_text(encoding="utf-8")
                self.assertNotIn(key, persisted) if key else None

    def test_api_probe_missing_key_and_dimension_mismatch_fail_before_confirmation(self):
        with self.assertRaisesRegex(ValueError, "EMBEDDING_API_KEY"):
            probe_new_embedding("novel-api", self._settings("api"))
        raw = MagicMock()
        raw.embeddings.create.return_value = SimpleNamespace(data=[
            SimpleNamespace(index=0, embedding=[0.1, 0.2, 0.3])
        ])
        with self.assertRaisesRegex(ValueError, "配置的向量维度：2"):
            probe_new_embedding(
                "novel-api",
                self._settings(
                    "api", "secret", "https://embedding.example", "model-a", 2
                ),
                api_client_factory=MagicMock(return_value=raw),
            )

    def test_api_probe_reports_connection_and_model_errors_in_chinese(self):
        settings = self._settings(
            "api", "secret", "https://embedding.example", "model-a", None
        )
        connection_error = APIConnectionError(
            request=httpx.Request("POST", "https://embedding.example")
        )
        with self.assertRaisesRegex(ValueError, "无法连接 EMBEDDING_BASE_URL"):
            probe_new_embedding(
                "novel-api", settings,
                api_client_factory=MagicMock(side_effect=connection_error),
            )
        response = httpx.Response(
            404,
            request=httpx.Request("POST", "https://embedding.example"),
        )
        model_error = NotFoundError("missing model", response=response, body=None)
        raw = MagicMock()
        raw.embeddings.create.side_effect = model_error
        with self.assertRaisesRegex(ValueError, "EMBEDDING_MODEL 不可用"):
            probe_new_embedding(
                "novel-api", settings,
                api_client_factory=MagicMock(return_value=raw),
            )

    def test_api_probe_detects_dimensions_and_persists_after_confirmation(self):
        raw = MagicMock()
        raw.embeddings.create.return_value = SimpleNamespace(data=[
            SimpleNamespace(index=0, embedding=[0.1, 0.2, 0.3, 0.4])
        ])
        settings = self._settings(
            "api", "secret", "https://embedding.example", "model-a", None
        )
        candidate = probe_new_embedding(
            "novel-api", settings,
            api_client_factory=MagicMock(return_value=raw),
        )
        self.assertEqual(4, candidate.embedding_dimensions)
        self.assertFalse(candidate.embedding_request_dimensions)
        self.assertNotIn("dimensions", raw.embeddings.create.call_args.kwargs)

        runtime_raw = MagicMock()
        runtime_raw.embeddings.create.return_value = SimpleNamespace(data=[
            SimpleNamespace(index=0, embedding=[0.1, 0.2, 0.3, 0.4])
        ])
        runtime = NovelEmbeddingRuntime(
            candidate, settings,
            api_client_factory=MagicMock(return_value=runtime_raw),
        )
        runtime.embed(["text"])
        self.assertNotIn(
            "dimensions", runtime_raw.embeddings.create.call_args.kwargs
        )

    def test_env_mode_change_affects_only_new_novel(self):
        store = NovelEmbeddingConfigStore(self.temp_dir)
        store.create(self._candidate("novel-a", "local", "local-default", 384))
        api_settings = self._settings(
            "api", "api-secret", "https://embedding.example", "model-b", 1536
        )
        with patch.object(cli, "get_settings", return_value=api_settings):
            self.assertTrue(cli._validate_existing_embedding("novel-a"))
        self.assertEqual("local", store.load("novel-a").embedding_mode)
        candidate_b = self._candidate("novel-b", "api", "model-b", 1536)
        with patch.object(cli, "get_settings", return_value=api_settings), patch.object(
            cli, "probe_new_embedding", return_value=candidate_b
        ), patch("builtins.input", return_value="y"):
            self.assertTrue(cli._confirm_and_bind_embedding("novel-b"))
        self.assertEqual("api", store.load("novel-b").embedding_mode)

    def test_api_runtime_uses_internal_model_and_dimension_and_fails_closed(self):
        store = NovelEmbeddingConfigStore(self.temp_dir)
        config = self._candidate(
            "novel-api", "api", "model-a", 3, request_dimensions=True
        )
        store.create(config)
        with self.assertRaisesRegex(ValueError, "EMBEDDING_API_KEY"):
            NovelEmbeddingRuntime(config, self._settings("local"))
        raw = MagicMock()
        raw.embeddings.create.return_value = SimpleNamespace(data=[
            SimpleNamespace(index=0, embedding=[0.1, 0.2, 0.3])
        ])
        runtime = NovelEmbeddingRuntime(
            config,
            self._settings(
                "api", "rotated-key", "https://new-endpoint", "model-b", 99
            ),
            api_client_factory=MagicMock(return_value=raw),
        )
        self.assertEqual([[0.1, 0.2, 0.3]], runtime.embed(["text"]))
        kwargs = raw.embeddings.create.call_args.kwargs
        self.assertEqual("model-a", kwargs["model"])
        self.assertEqual(3, kwargs["dimensions"])
        with self.assertRaisesRegex(ValueError, "legacy fallback"):
            store.load("missing-novel")

    def test_api_runtime_rejects_changed_vector_dimensions(self):
        config = self._candidate("novel-api", "api", "model-a", 3)
        raw = MagicMock()
        raw.embeddings.create.return_value = SimpleNamespace(data=[
            SimpleNamespace(index=0, embedding=[0.1, 0.2])
        ])
        runtime = NovelEmbeddingRuntime(
            config,
            self._settings("api", "secret", "https://embedding.example"),
            api_client_factory=MagicMock(return_value=raw),
        )
        with self.assertRaisesRegex(ValueError, "当前小说固定维度：3"):
            runtime.embed(["text"])


class ChromaEmbeddingPathTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="embedding-chroma-test-"))
        self.store = AtomicFactStore(self.temp_dir / "chroma_db")
        self.collection = MagicMock()
        self.store._collection = self.collection
        self.fact = AtomicFact(fact_text="事实文本", fact_type="event")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_api_mode_explicitly_adds_and_queries_embeddings(self):
        runtime = MagicMock(is_api=True)
        runtime.embed.side_effect = [[[0.1, 0.2]], [[0.3, 0.4]]]
        self.store._runtimes["novel-api"] = runtime
        self.collection.get.return_value = {"ids": []}
        self.collection.query.return_value = {"ids": [[]]}
        self.store.index_facts(
            "novel-api", "main", 1, [self.fact], "chapter.md", "digest.md"
        )
        self.assertEqual(
            [[0.1, 0.2]], self.collection.add.call_args.kwargs["embeddings"]
        )
        self.store.search("novel-api", "main", "query", 2, 5)
        query_kwargs = self.collection.query.call_args.kwargs
        self.assertEqual([[0.3, 0.4]], query_kwargs["query_embeddings"])
        self.assertNotIn("query_texts", query_kwargs)

    def test_local_mode_keeps_chroma_default_embedding_path(self):
        runtime = MagicMock(is_api=False)
        self.store._runtimes["novel-local"] = runtime
        self.collection.get.return_value = {"ids": []}
        self.collection.query.return_value = {"ids": [[]]}
        self.store.index_facts(
            "novel-local", "main", 1, [self.fact], "chapter.md", "digest.md"
        )
        self.assertNotIn("embeddings", self.collection.add.call_args.kwargs)
        self.store.search("novel-local", "main", "query", 2, 5)
        self.assertEqual(
            ["query"], self.collection.query.call_args.kwargs["query_texts"]
        )
        runtime.embed.assert_not_called()

    def test_dimension_error_happens_before_chroma_write(self):
        runtime = MagicMock(is_api=True)
        runtime.embed.side_effect = ValueError("Embedding 向量维度不一致")
        self.store._runtimes["novel-api"] = runtime
        self.collection.get.return_value = {"ids": []}
        with self.assertRaisesRegex(ValueError, "维度不一致"):
            self.store.index_facts(
                "novel-api", "main", 1, [self.fact], "chapter.md", "digest.md"
            )
        self.collection.add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
