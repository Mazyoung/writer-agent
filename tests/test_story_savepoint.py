"""Destructive integration coverage for Story Savepoint + Load Savepoint."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import chromadb
from chromadb.config import Settings as ChromaSettings
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver

import main as cli
from src.config.settings import get_settings
from src.config.runtime_policy import NovelRuntimePolicy
from src.storage.document_formats import (
    CurrentChapterMeta, CurrentItemState, CurrentState,
)
from src.storage.chapter_completion import mark_derived_ready
from src.storage.story_savepoint import (
    NovelOperationLock,
    SavepointError,
    SavepointVerificationError,
    StorySavepointManager,
    _json_bytes,
    _json_hash,
    _sha256_file,
)
from src.workflows.chapter_runner import ChapterWorkflowRunner
from src.workflows.continuation import NovelContinuationService


class IsolatedManager(StorySavepointManager):
    workflow_states: dict[int, str]

    def __init__(self, novel_id: str, data_dir: Path):
        super().__init__(novel_id, data_dir)
        self.workflow_states = {}

    def _workflow_states(self) -> dict[int, str]:
        return dict(self.workflow_states)


class StorySavepointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = Path(tempfile.mkdtemp(prefix="writer-savepoint-test-"))
        cls.case_number = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def setUp(self):
        type(self).case_number += 1
        self.temp_dir = self.data_dir
        novel_id = f"isolated-story-{self.case_number}"
        root = self.temp_dir / "novels" / novel_id
        for name in (
            "settings", "outlines", "chapters", "states", "briefs",
            "tracking", "feedback",
        ):
            (root / name).mkdir(parents=True, exist_ok=True)
        self.manager = IsolatedManager(
            novel_id, self.temp_dir
        )
        self._write_world(1, "world-one")

    def _write_world(self, chapter: int, label: str) -> None:
        root = self.manager.root
        for path in (root / "chapters").glob("chapter_*.md"):
            path.unlink()
        for path in (root / "states").glob("chapter_*_derived"):
            path.unlink()
        for path in (root / "states").glob("chapter_*_derived_ready.json"):
            path.unlink()
        for index in range(1, chapter + 1):
            (root / "chapters" / f"chapter_{index:04d}.md").write_text(
                f"{label}-chapter-{index}", encoding="utf-8"
            )
            mark_derived_ready(self.manager.file_store, index)
        (root / "states" / f"chapter_{chapter:04d}_derived").write_text(
            f"derived-{label}", encoding="utf-8"
        )
        state = CurrentState(
            through_chapter=chapter,
            chapter=CurrentChapterMeta(
                chapter_index=chapter,
                title=label,
                word_count=chapter * 100,
                canonical_source_path=f"chapters/chapter_{chapter:04d}.md",
            ),
        )
        (root / "tracking" / "current_state.md").write_text(
            state.to_markdown(), encoding="utf-8"
        )
        (root / "tracking" / "book_plan.md").write_text(
            f"book-plan-{label}", encoding="utf-8"
        )
        (root / "tracking" / "volume_plan.md").write_text(
            "# 第1卷规划：《测试》\n- **版本**: v1\n- **状态**: ACTIVE\n"
            "\n## 起始状态\n起点\n## 本卷目标\n目标\n"
            "## 主要冲突\n冲突\n## 故事阶段/路径\n- 路径\n"
            "## 关键转折\n- 转折\n## 限制条件\n限制\n"
            "## 目标结束状态\n终点\n",
            encoding="utf-8",
        )
        (root / "outlines" / f"chapter_plan_ch{chapter:04d}.md").write_text(
            f"plan-{label}", encoding="utf-8"
        )
        sources = root / "sources"
        sources.mkdir(exist_ok=True)
        for path in sources.glob("*"):
            path.unlink()
        (sources / f"chapter_{chapter:04d}.md").write_text(
            f"source-{label}", encoding="utf-8"
        )
        database = sqlite3.connect(root / "state.db")
        database.execute("CREATE TABLE IF NOT EXISTS world(value TEXT)")
        database.execute("DELETE FROM world")
        database.execute("INSERT INTO world VALUES (?)", (label,))
        database.commit()
        database.close()

        client = chromadb.PersistentClient(
            path=str(self.manager.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        for name, schema in (
            ("atomic_facts_v2", "atomic-fact-v2"),
            ("author_knowledge_v1", "author-knowledge-v1"),
        ):
            collection = client.get_or_create_collection(
                name=name, metadata={"schema": schema}
            )
            current = collection.get(where={"$and": [
                {"novel_id": {"$eq": self.manager.novel_id}},
                {"branch_id": {"$eq": "main"}},
            ]})
            if current.get("ids"):
                collection.delete(ids=current["ids"])
            collection.add(
                ids=[f"{self.manager.novel_id}_main_{name}_{chapter}"],
                documents=[f"{name}-{label}"],
                metadatas=[{
                    "novel_id": self.manager.novel_id,
                    "branch_id": "main",
                    "chapter_index": chapter,
                    "label": label,
                }],
                embeddings=[[float(chapter), 1.0, 2.0]],
            )
        self.manager.workflow_states = {chapter: "DERIVED_READY"}

    def _chroma_documents(self, collection_name: str) -> list[str]:
        client = chromadb.PersistentClient(
            path=str(self.manager.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        collection = client.get_collection(collection_name)
        raw = collection.get(where={"$and": [
            {"novel_id": {"$eq": self.manager.novel_id}},
            {"branch_id": {"$eq": "main"}},
        ]}, include=["documents"])
        return list(raw.get("documents") or [])

    def test_create_requires_current_derived_ready_world(self):
        self.manager.workflow_states = {1: "WAITING_HUMAN"}
        with self.assertRaisesRegex(SavepointError, "未结束"):
            self.manager.create()
        self.manager.workflow_states = {1: "DERIVATION_ERROR"}
        with self.assertRaisesRegex(SavepointError, "未结束"):
            self.manager.create()
        self.manager.workflow_states = {1: "DERIVED_READY"}
        current = self.manager.root / "tracking" / "current_state.md"
        mismatched = CurrentState(
            through_chapter=0, chapter=CurrentChapterMeta(chapter_index=0)
        )
        current.write_text(mismatched.to_markdown(), encoding="utf-8")
        with self.assertRaisesRegex(SavepointError, "Current State"):
            self.manager.create()

    def test_branch_fails_closed_and_operation_lock_releases_in_finally(self):
        with self.assertRaisesRegex(SavepointError, "main"):
            StorySavepointManager("isolated-story", self.temp_dir, branch_id="dev")
        lock_path = self.manager.root / ".novel_operation.lock"
        with self.assertRaisesRegex(ValueError, "injected"):
            with NovelOperationLock(self.manager.root):
                self.assertTrue(lock_path.exists())
                with self.assertRaisesRegex(SavepointError, "operation lock"):
                    with NovelOperationLock(self.manager.root):
                        pass
                raise ValueError("injected")
        self.assertFalse(lock_path.exists())
        with NovelOperationLock(self.manager.root):
            self.assertTrue(lock_path.exists())

    def test_create_accepts_legacy_prestory_via_current_state_parser(self):
        current = self.manager.root / "tracking" / "current_state.md"
        state = CurrentState(
            through_chapter=1,
            items=[CurrentItemState(
                name="旧钥匙", acquired_chapter=0, updated_chapter=1,
            )],
            chapter=CurrentChapterMeta(
                chapter_index=1,
                title="world-one",
                word_count=100,
                canonical_source_path="chapters/chapter_0001.md",
            ),
        )
        legacy = state.to_markdown().replace(
            "| 旧钥匙 |  |  |  | 0 |",
            "| 旧钥匙 |  |  |  | 前史 |",
        )
        current.write_text(legacy, encoding="utf-8")

        manifest = self.manager.create()

        self.assertEqual(manifest["status"], "READY")
        self.assertEqual(
            CurrentState.from_markdown(legacy).items[0].acquired_chapter, 0
        )

    def test_create_manifest_and_verify_include_sqlite_and_embeddings(self):
        manifest = self.manager.create()
        self.assertEqual("S0001", manifest["savepoint_id"])
        self.assertEqual("main", manifest["branch_id"])
        self.assertEqual("READY", manifest["status"])
        self.assertTrue(manifest["files"])
        self.assertEqual("ok", manifest["sqlite"]["state_db"]["integrity_result"])
        self.assertEqual(
            {"atomic_facts_v2", "author_knowledge_v1"},
            {entry["collection_name"] for entry in manifest["chroma"]},
        )
        for entry in manifest["chroma"]:
            payload = json.loads(
                (self.manager.savepoints_root / "S0001" / entry["snapshot_path"])
                .read_text(encoding="utf-8")
            )
            self.assertEqual(1, len(payload["embeddings"]))
        self.assertEqual("READY", self.manager.verify("S0001")["status"])

    def test_verify_does_not_create_missing_working_directories(self):
        self.manager.create()
        missing = self.manager.root / "feedback"
        missing.rmdir()
        reader = StorySavepointManager(
            self.manager.novel_id, self.manager.data_dir
        )
        self.assertEqual("READY", reader.verify("S0001")["status"])
        self.assertFalse(missing.exists())

    def test_verify_rejects_tampered_file_corrupt_sqlite_and_incomplete_chroma(self):
        self.manager.create()
        target = self.manager.savepoints_root / "S0001"
        manifest_path = target / "manifest.json"
        original_manifest = manifest_path.read_bytes()

        first_file = target / "files" / self.manager.verify("S0001")["files"][0]["relative_path"]
        original_file = first_file.read_bytes()
        first_file.write_bytes(original_file + b"tampered")
        with self.assertRaises(SavepointVerificationError):
            self.manager.verify("S0001")
        first_file.write_bytes(original_file)

        manifest = json.loads(original_manifest)
        sqlite_path = target / "sqlite" / "state.db"
        sqlite_path.write_bytes(b"not-a-sqlite-database")
        entry = manifest["sqlite"]["state_db"]
        entry["size"] = sqlite_path.stat().st_size
        entry["sha256"] = _sha256_file(sqlite_path)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(SavepointVerificationError):
            self.manager.verify("S0001")

        # Recreate a clean snapshot, then make one Chroma field incomplete while
        # keeping its transport hashes consistent; structural verification must fail.
        shutil.rmtree(target)
        self.manager.create()
        manifest = self.manager.verify("S0001")
        chroma_entry = manifest["chroma"][0]
        chroma_path = target / chroma_entry["snapshot_path"]
        payload = json.loads(chroma_path.read_text(encoding="utf-8"))
        payload["embeddings"] = []
        chroma_path.write_bytes(_json_bytes(payload))
        chroma_entry["snapshot_sha256"] = _sha256_file(chroma_path)
        chroma_entry["logical_hash"] = _json_hash(payload)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(SavepointVerificationError, "record count"):
            self.manager.verify("S0001")

    def test_load_round_trip_preserves_other_ready_savepoints(self):
        self.manager.create()
        self._write_world(2, "world-two")
        self.manager.create()
        manifest_bytes = {
            name: (self.manager.savepoints_root / name / "manifest.json").read_bytes()
            for name in ("S0001", "S0002")
        }

        self.manager.load("S0001")
        self.assertFalse((self.manager.root / "chapters" / "chapter_0002.md").exists())
        self.assertFalse((self.manager.root / "sources" / "chapter_0002.md").exists())
        self.assertEqual(
            "book-plan-world-one",
            (self.manager.root / "tracking" / "book_plan.md").read_text(),
        )
        database = sqlite3.connect(self.manager.state_db)
        self.assertEqual("world-one", database.execute("SELECT value FROM world").fetchone()[0])
        database.close()
        self.assertEqual(
            ["atomic_facts_v2-world-one"],
            self._chroma_documents("atomic_facts_v2"),
        )
        self.assertEqual("READY", self.manager.verify("S0002")["status"])

        (self.manager.root / "tracking" / "book_plan.md").write_text(
            "new-world-from-one", encoding="utf-8"
        )
        self.manager.load("S0002")
        self.assertTrue((self.manager.root / "chapters" / "chapter_0002.md").exists())
        self.assertEqual(
            "book-plan-world-two",
            (self.manager.root / "tracking" / "book_plan.md").read_text(),
        )
        self.assertEqual(
            ["author_knowledge_v1-world-two"],
            self._chroma_documents("author_knowledge_v1"),
        )
        for name, before in manifest_bytes.items():
            self.assertEqual(
                before,
                (self.manager.savepoints_root / name / "manifest.json").read_bytes(),
            )

    def test_load_s40_then_s80_continues_at_chapter_81_without_checkpoints(self):
        self._write_world(40, "world-forty")
        self.manager.create()
        self._write_world(80, "world-eighty")
        self.manager.create()
        self.manager.load("S0040")
        self.manager.load("S0080")

        settings = get_settings()
        old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir
        try:
            with patch.object(
                ChapterWorkflowRunner,
                "inspect",
                return_value={"values": {}, "next": [], "interrupts": []},
            ):
                decision = NovelContinuationService(
                    self.manager.novel_id,
                    NovelRuntimePolicy("agent", "supervised", 0, 5),
                ).route()
        finally:
            settings.data_dir = old_data_dir
        self.assertEqual(
            {"action": "start_chapter", "chapter_index": 81},
            decision,
        )

    def test_corrupt_target_never_modifies_working_state(self):
        self.manager.create()
        self._write_world(2, "world-two")
        before = (self.manager.root / "tracking" / "book_plan.md").read_bytes()
        target_file = next((self.manager.savepoints_root / "S0001" / "files").rglob("*.md"))
        target_file.write_text("corrupt", encoding="utf-8")
        with self.assertRaises(SavepointVerificationError):
            self.manager.load("S0001")
        self.assertEqual(
            before, (self.manager.root / "tracking" / "book_plan.md").read_bytes()
        )

    def test_mid_restore_failure_uses_safety_snapshot(self):
        self.manager.create()
        self._write_world(2, "world-two")
        original_restore = self.manager._restore
        target = self.manager.savepoints_root / "S0001"

        def fail_after_target(directory, manifest, restore_workflow):
            original_restore(directory, manifest, restore_workflow)
            if directory == target:
                raise OSError("injected restore failure")

        with patch.object(self.manager, "_restore", side_effect=fail_after_target):
            with self.assertRaisesRegex(SavepointError, "已恢复加载前状态"):
                self.manager.load("S0001")
        self.assertEqual(
            "book-plan-world-two",
            (self.manager.root / "tracking" / "book_plan.md").read_text(),
        )
        self.assertEqual(2, self.manager._latest_canonical_chapter())
        self.assertFalse((self.manager.root / "LOAD_ERROR.json").exists())

    def test_double_restore_failure_blocks_future_writes_and_preserves_safety(self):
        self.manager.create()
        self._write_world(2, "world-two")
        with patch.object(self.manager, "_restore", side_effect=OSError("broken disk")):
            with self.assertRaisesRegex(SavepointError, "LOAD_ERROR"):
                self.manager.load("S0001")
        marker = json.loads(
            (self.manager.root / "LOAD_ERROR.json").read_text(encoding="utf-8")
        )
        self.assertEqual("LOAD_ERROR", marker["status"])
        self.assertTrue(Path(marker["safety_snapshot"]).is_dir())
        with self.assertRaisesRegex(SavepointError, "阻断状态"):
            self.manager.create()
        with self.assertRaisesRegex(RuntimeError, "LOAD_ERROR"):
            self.manager.file_store.save_canonical("tracking", "book_plan", "blocked")

    def test_future_workflow_checkpoints_are_deleted_only_for_this_novel(self):
        connection = sqlite3.connect(self.manager.workflow_db)
        saver = SqliteSaver(connection)
        for novel, chapter in (
            (self.manager.novel_id, 1),
            (self.manager.novel_id, 2),
            ("other-story", 9),
        ):
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"]["workflow_status"] = "DERIVED_READY"
            saver.put(
                {"configurable": {
                    "thread_id": f"chapter:{novel}:{chapter:04d}",
                    "checkpoint_ns": "",
                }},
                checkpoint,
                {},
                {},
            )
        connection.close()
        self.manager._clear_future_workflow_checkpoints(1)
        connection = sqlite3.connect(self.manager.workflow_db)
        saver = SqliteSaver(connection)
        threads = {
            item.config["configurable"]["thread_id"] for item in saver.list(None)
        }
        connection.close()
        self.assertIn(f"chapter:{self.manager.novel_id}:0001", threads)
        self.assertNotIn(f"chapter:{self.manager.novel_id}:0002", threads)
        self.assertIn("chapter:other-story:0009", threads)


class StorySavepointCliTests(unittest.TestCase):
    def test_load_requires_both_exact_confirmations(self):
        temp_dir = Path(tempfile.mkdtemp(prefix="writer-savepoint-cli-"))
        try:
            (temp_dir / "novels" / "novel-a").mkdir(parents=True)
            manager = MagicMock()
            manager.load.return_value = {
                "savepoint_id": "S0040", "chapter_index": 40,
            }
            args = SimpleNamespace(
                name="novel-a", savepoint_action="load", savepoint_id="S0040"
            )
            with patch.object(
                cli, "get_settings", return_value=SimpleNamespace(data_dir=temp_dir)
            ), patch.object(cli, "StorySavepointManager", return_value=manager), patch(
                "builtins.input", side_effect=["novel-a", "wrong"]
            ):
                cli.cmd_savepoint(args)
            manager.load.assert_not_called()
            with patch.object(
                cli, "get_settings", return_value=SimpleNamespace(data_dir=temp_dir)
            ), patch.object(cli, "StorySavepointManager", return_value=manager), patch(
                "builtins.input", side_effect=["novel-a", "LOAD S0040"]
            ):
                cli.cmd_savepoint(args)
            manager.load.assert_called_once_with("S0040")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
