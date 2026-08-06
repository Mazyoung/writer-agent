"""Story Savepoint: immutable whole-world snapshots for one novel."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import chromadb
from chromadb.config import Settings as ChromaSettings

from src.config.settings import get_settings
from src.storage.atomic_fact_store import COLLECTION_NAME as ATOMIC_FACT_COLLECTION
from src.storage.author_rag_store import COLLECTION_NAME as AUTHOR_RAG_COLLECTION
from src.storage.document_formats import CurrentState
from src.storage.file_store import FileStore
from src.storage.chapter_completion import is_derived_ready


SCHEMA_VERSION = 1
BRANCH_ID = "main"
READY = "READY"
COLLECTIONS = (ATOMIC_FACT_COLLECTION, AUTHOR_RAG_COLLECTION)
_TERMINAL_WORKFLOW_STATUSES = {"DERIVED_READY", "DISCARDED", "STOPPED_NON_PASS"}


class SavepointError(RuntimeError):
    """A savepoint operation was refused or failed safely."""


class SavepointVerificationError(SavepointError):
    """The immutable snapshot failed integrity verification."""


class NovelOperationLock(AbstractContextManager):
    """Small cross-process novel mutation lock based on exclusive creation."""

    def __init__(self, novel_root: Path):
        self.novel_root = novel_root
        self.path = novel_root / ".novel_operation.lock"
        self._held = False

    def __enter__(self) -> "NovelOperationLock":
        error_marker = self.novel_root / "LOAD_ERROR.json"
        if error_marker.exists():
            raise SavepointError(
                f"小说处于 LOAD_ERROR 阻断状态；请先按 {error_marker} 人工恢复"
            )
        self.novel_root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "pid": os.getpid(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise SavepointError(
                f"小说正在执行另一项写操作（operation lock: {self.path}）"
            ) from exc
        try:
            os.write(descriptor, payload.encode("utf-8"))
        finally:
            os.close(descriptor)
        self._held = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _json_hash(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _plain(value: Any) -> Any:
    """Convert numpy-like Chroma values to JSON-native values."""
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


class StorySavepointManager:
    """Create, list, verify, and transactionally load Story Savepoints."""

    def __init__(
        self,
        novel_id: str,
        data_dir: Path | None = None,
        branch_id: str = BRANCH_ID,
    ):
        if branch_id != BRANCH_ID:
            raise SavepointError("当前仅支持 branch_id='main'")
        self.novel_id = novel_id
        self.branch_id = branch_id
        self.data_dir = Path(data_dir or get_settings().data_dir)
        self.file_store = FileStore(novel_id, self.data_dir, ensure_dirs=False)
        self.root = self.file_store.root
        if not self.root.is_dir():
            raise SavepointError(f"小说不存在：{novel_id}")
        self.savepoints_root = self.root / "story_savepoints"
        self.chroma_dir = self.data_dir / "chroma_db"
        self.state_db = self.root / "state.db"
        self.workflow_db = self.root / "workflow_checkpoints.sqlite"

    # ── public API ──────────────────────────────────────────

    def create(self) -> dict[str, Any]:
        """Capture the current latest DERIVED_READY creative world."""
        with NovelOperationLock(self.root):
            chapter_index = self._assert_creation_boundary()
            savepoint_id = f"S{chapter_index:04d}"
            target = self.savepoints_root / savepoint_id
            if target.exists():
                raise SavepointError(f"READY Savepoint 已存在，不能覆盖：{savepoint_id}")
            self.savepoints_root.mkdir(parents=True, exist_ok=True)
            staging = self.savepoints_root / f".staging-{savepoint_id}-{uuid.uuid4().hex}"
            try:
                manifest = self._capture(staging, savepoint_id, chapter_index)
                self._verify_directory(staging, require_ready=False)
                manifest["status"] = READY
                self._write_manifest(staging, manifest)
                self._verify_directory(staging, require_ready=True)
                staging.replace(target)
                return manifest
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise

    def list(self) -> list[dict[str, Any]]:
        """Return READY manifests; internal staging/safety data is invisible."""
        if not self.savepoints_root.exists():
            return []
        result = []
        for path in sorted(self.savepoints_root.iterdir()):
            if not path.is_dir() or not re.fullmatch(r"S\d{4,}", path.name):
                continue
            try:
                manifest = self._read_manifest(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if manifest.get("status") == READY:
                result.append(manifest)
        return result

    def verify(self, savepoint_id: str) -> dict[str, Any]:
        """Verify without mutating the working creative state."""
        return self._verify_directory(self._savepoint_path(savepoint_id), True)

    def load(self, savepoint_id: str) -> dict[str, Any]:
        """Restore a complete state with an internal transactional safety copy."""
        target = self._savepoint_path(savepoint_id)
        manifest = self._verify_directory(target, True)
        self._assert_no_pending_workflow()
        with NovelOperationLock(self.root):
            # Re-check after acquiring the mutation lock to close the race window.
            self._assert_no_pending_workflow()
            safety = self.savepoints_root / ".internal" / f"safety-{uuid.uuid4().hex}"
            safety.parent.mkdir(parents=True, exist_ok=True)
            self._capture(
                safety, "INTERNAL_SAFETY", self._latest_canonical_chapter(),
                include_workflow=True,
            )
            try:
                self._restore(target, manifest, restore_workflow=False)
                self._clear_future_workflow_checkpoints(int(manifest["chapter_index"]))
                self._verify_loaded_state(manifest)
            except Exception as load_exc:
                try:
                    safety_manifest = self._read_manifest(safety)
                    self._restore(safety, safety_manifest, restore_workflow=True)
                except Exception as safety_exc:
                    marker = self.root / "LOAD_ERROR.json"
                    marker.write_text(json.dumps({
                        "status": "LOAD_ERROR",
                        "target_savepoint": savepoint_id,
                        "load_error": f"{type(load_exc).__name__}: {load_exc}",
                        "safety_restore_error": (
                            f"{type(safety_exc).__name__}: {safety_exc}"
                        ),
                        "safety_snapshot": str(safety),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "recovery": "停止创作写入；使用保留的 safety snapshot 人工恢复。",
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    raise SavepointError(
                        f"Load 与 safety restore 均失败，已进入 LOAD_ERROR 阻断状态：{marker}"
                    ) from load_exc
                else:
                    shutil.rmtree(safety, ignore_errors=True)
                    raise SavepointError(
                        f"Load 失败；已恢复加载前状态：{type(load_exc).__name__}: {load_exc}"
                    ) from load_exc
            shutil.rmtree(safety, ignore_errors=True)
            self._remove_empty_internal_dir()
            return manifest

    # ── creation boundary / workflow state ─────────────────

    def _latest_canonical_chapter(self) -> int:
        indices = []
        for path in self.file_store.list_chapters():
            match = re.fullmatch(r"chapter_(\d{4,})\.md", path.name)
            if match:
                indices.append(int(match.group(1)))
        return max(indices, default=0)

    def _workflow_states(self) -> dict[int, str]:
        if not self.workflow_db.exists():
            return {}
        from langgraph.checkpoint.sqlite import SqliteSaver

        connection = sqlite3.connect(self.workflow_db, check_same_thread=False)
        saver = SqliteSaver(connection)
        states: dict[int, str] = {}
        try:
            for item in saver.list(None):
                thread_id = str(
                    item.config.get("configurable", {}).get("thread_id", "")
                )
                match = re.fullmatch(
                    rf"chapter:{re.escape(self.novel_id)}:(\d{{4,}})", thread_id
                )
                if not match:
                    continue
                chapter = int(match.group(1))
                if chapter in states:
                    continue
                status = item.checkpoint.get("channel_values", {}).get(
                    "workflow_status", ""
                )
                states[chapter] = str(status).upper()
        finally:
            connection.close()
        return states

    def _assert_no_pending_workflow(self) -> None:
        pending = {
            chapter: status for chapter, status in self._workflow_states().items()
            if status not in _TERMINAL_WORKFLOW_STATUSES
        }
        if pending:
            details = ", ".join(
                f"Chapter {chapter}: {status or 'UNKNOWN'}"
                for chapter, status in sorted(pending.items())
            )
            raise SavepointError(f"存在未结束的 chapter workflow：{details}")

    def _assert_creation_boundary(self) -> int:
        chapter = self._latest_canonical_chapter()
        if chapter <= 0:
            raise SavepointError("没有已完成的正式章节，不能创建 Savepoint")
        states = self._workflow_states()
        pending = {
            index: status for index, status in states.items()
            if status not in _TERMINAL_WORKFLOW_STATUSES
        }
        if pending:
            self._assert_no_pending_workflow()
        try:
            completed = is_derived_ready(self.file_store, chapter)
        except ValueError as exc:
            raise SavepointError(str(exc)) from exc
        if not completed:
            raise SavepointError(
                f"最新正式章节 Chapter {chapter} 尚未达到 DERIVED_READY"
            )
        current_path = self.root / "tracking" / "current_state.md"
        if not current_path.is_file():
            raise SavepointError("缺少 tracking/current_state.md")
        current = CurrentState.from_markdown(current_path.read_text(encoding="utf-8"))
        if (
            current.through_chapter != chapter
            or current.chapter.chapter_index != chapter
        ):
            raise SavepointError(
                "Current State 与最新正式章节不一致，不能补建过去章节 Savepoint"
            )
        if not self.state_db.is_file():
            raise SavepointError("缺少 state.db")
        return chapter

    # ── capture / manifest / verify ─────────────────────────

    @staticmethod
    def _excluded(relative: Path) -> bool:
        parts = {part.lower() for part in relative.parts}
        if parts & {"story_savepoints", "staging", "temp", "tmp", "cache", "__pycache__"}:
            return True
        name = relative.name.lower()
        return name in {
            "state.db", "state.db-wal", "state.db-shm",
            "workflow_checkpoints.sqlite", "workflow_checkpoints.sqlite-wal",
            "workflow_checkpoints.sqlite-shm", ".novel_operation.lock",
            "load_error.json",
        } or name.endswith((".tmp", ".lock"))

    def _creative_files(self) -> Iterable[Path]:
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and not self._excluded(path.relative_to(self.root)):
                yield path

    @staticmethod
    def _sqlite_backup(source: Path, target: Path) -> dict[str, Any]:
        if not source.is_file():
            raise SavepointError(f"SQLite 文件不存在：{source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite3.connect(source)
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
            row = target_conn.execute("PRAGMA integrity_check").fetchone()
            integrity = str(row[0]) if row else "missing"
        finally:
            target_conn.close()
            source_conn.close()
        if integrity.lower() != "ok":
            raise SavepointVerificationError(
                f"SQLite snapshot integrity_check 失败：{integrity}"
            )
        return {
            "snapshot_path": target.as_posix(),
            "sha256": _sha256_file(target),
            "size": target.stat().st_size,
            "integrity_result": integrity,
        }

    def _capture(
        self,
        directory: Path,
        savepoint_id: str,
        chapter_index: int,
        include_workflow: bool = False,
    ) -> dict[str, Any]:
        directory.mkdir(parents=True, exist_ok=False)
        file_entries = []
        for source in self._creative_files():
            relative = source.relative_to(self.root)
            destination = directory / "files" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            file_entries.append({
                "relative_path": relative.as_posix(),
                "sha256": _sha256_file(destination),
                "size": destination.stat().st_size,
            })
        state_snapshot = directory / "sqlite" / "state.db"
        state_entry = self._sqlite_backup(self.state_db, state_snapshot)
        state_entry["snapshot_path"] = "sqlite/state.db"
        sqlite_entries: dict[str, Any] = {"state_db": state_entry}
        if include_workflow and self.workflow_db.is_file():
            workflow_snapshot = directory / "sqlite" / "workflow_checkpoints.sqlite"
            workflow_entry = self._sqlite_backup(self.workflow_db, workflow_snapshot)
            workflow_entry["snapshot_path"] = "sqlite/workflow_checkpoints.sqlite"
            sqlite_entries["workflow_checkpoints"] = workflow_entry
        chroma_entries = self._export_chroma(directory / "chroma")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "savepoint_id": savepoint_id,
            "novel_id": self.novel_id,
            "branch_id": self.branch_id,
            "chapter_index": chapter_index,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "STAGING",
            "files": file_entries,
            "sqlite": sqlite_entries,
            "chroma": chroma_entries,
        }
        self._write_manifest(directory, manifest)
        return manifest

    @staticmethod
    def _write_manifest(directory: Path, manifest: dict[str, Any]) -> None:
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _read_manifest(directory: Path) -> dict[str, Any]:
        return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))

    def _export_chroma(self, directory: Path) -> list[dict[str, Any]]:
        directory.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(
            path=str(self.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        available = {collection.name for collection in client.list_collections()}
        result = []
        where = {"$and": [
            {"novel_id": {"$eq": self.novel_id}},
            {"branch_id": {"$eq": self.branch_id}},
        ]}
        for name in COLLECTIONS:
            metadata: dict[str, Any] = {}
            payload = {
                "ids": [], "documents": [], "metadatas": [], "embeddings": [],
            }
            if name in available:
                collection = client.get_collection(name)
                metadata = _plain(collection.metadata or {})
                raw = collection.get(
                    where=where,
                    include=["documents", "metadatas", "embeddings"],
                )
                payload = {
                    "ids": _plain(raw.get("ids") or []),
                    "documents": _plain(raw.get("documents") or []),
                    "metadatas": _plain(raw.get("metadatas") or []),
                    "embeddings": _plain(
                        raw.get("embeddings")
                        if raw.get("embeddings") is not None else []
                    ),
                }
            snapshot = directory / f"{name}.json"
            snapshot.write_bytes(_json_bytes(payload))
            result.append({
                "collection_name": name,
                "collection_metadata": metadata,
                "record_count": len(payload["ids"]),
                "snapshot_path": f"chroma/{name}.json",
                "snapshot_sha256": _sha256_file(snapshot),
                "logical_hash": _json_hash(payload),
                "fields": ["ids", "documents", "metadatas", "embeddings"],
            })
        return result

    def _verify_directory(
        self, directory: Path, require_ready: bool
    ) -> dict[str, Any]:
        if not directory.is_dir():
            raise SavepointVerificationError(f"Savepoint 不存在：{directory.name}")
        try:
            manifest = self._read_manifest(directory)
        except Exception as exc:
            raise SavepointVerificationError(f"manifest 无法读取：{exc}") from exc
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise SavepointVerificationError("不支持的 Savepoint schema_version")
        if manifest.get("novel_id") != self.novel_id:
            raise SavepointVerificationError("manifest novel_id 不匹配")
        if manifest.get("branch_id") != BRANCH_ID:
            raise SavepointVerificationError("manifest branch_id 非 main")
        if require_ready and manifest.get("status") != READY:
            raise SavepointVerificationError("Savepoint 尚未达到 READY")
        for entry in manifest.get("files", []):
            relative = Path(str(entry.get("relative_path", "")))
            if relative.is_absolute() or ".." in relative.parts or self._excluded(relative):
                raise SavepointVerificationError(f"非法 snapshot 路径：{relative}")
            path = directory / "files" / relative
            self._verify_file_entry(path, entry)
        sqlite_entries = manifest.get("sqlite", {})
        if "state_db" not in sqlite_entries:
            raise SavepointVerificationError("manifest 缺少 state.db snapshot")
        for entry in sqlite_entries.values():
            snapshot_path = Path(str(entry.get("snapshot_path", "")))
            if snapshot_path.is_absolute() or ".." in snapshot_path.parts:
                raise SavepointVerificationError(
                    f"非法 SQLite snapshot 路径：{snapshot_path}"
                )
            path = directory / snapshot_path
            self._verify_file_entry(path, entry)
            try:
                connection = sqlite3.connect(path)
                row = connection.execute("PRAGMA integrity_check").fetchone()
            except sqlite3.DatabaseError as exc:
                raise SavepointVerificationError(f"SQLite 损坏：{path}") from exc
            finally:
                if "connection" in locals():
                    connection.close()
                    del connection
            if not row or str(row[0]).lower() != "ok":
                raise SavepointVerificationError(f"SQLite integrity_check 失败：{path}")
        chroma_entries = manifest.get("chroma", [])
        names = [str(entry.get("collection_name", "")) for entry in chroma_entries]
        if sorted(names) != sorted(COLLECTIONS):
            raise SavepointVerificationError("Chroma collection 清单不完整或重复")
        for entry in chroma_entries:
            snapshot_path = Path(str(entry.get("snapshot_path", "")))
            if snapshot_path.is_absolute() or ".." in snapshot_path.parts:
                raise SavepointVerificationError(
                    f"非法 Chroma snapshot 路径：{snapshot_path}"
                )
            path = directory / snapshot_path
            if not path.is_file() or _sha256_file(path) != entry.get("snapshot_sha256"):
                raise SavepointVerificationError(f"Chroma snapshot hash 不匹配：{path}")
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise SavepointVerificationError(f"Chroma snapshot 无法读取：{path}") from exc
            fields = [payload.get(field) for field in ("ids", "documents", "metadatas", "embeddings")]
            if any(not isinstance(field, list) for field in fields):
                raise SavepointVerificationError(f"Chroma snapshot 字段不完整：{path}")
            lengths = {len(field) for field in fields}
            if lengths != {int(entry.get("record_count", -1))}:
                raise SavepointVerificationError(f"Chroma snapshot record count 不一致：{path}")
            if fields[0] and any(embedding is None for embedding in fields[3]):
                raise SavepointVerificationError(f"Chroma snapshot 缺少 embeddings：{path}")
            if _json_hash(payload) != entry.get("logical_hash"):
                raise SavepointVerificationError(f"Chroma logical hash 不匹配：{path}")
        return manifest

    @staticmethod
    def _verify_file_entry(path: Path, entry: dict[str, Any]) -> None:
        if not path.is_file():
            raise SavepointVerificationError(f"snapshot 文件缺失：{path}")
        if path.stat().st_size != int(entry.get("size", -1)):
            raise SavepointVerificationError(f"snapshot 文件大小不匹配：{path}")
        if _sha256_file(path) != entry.get("sha256"):
            raise SavepointVerificationError(f"snapshot 文件 hash 不匹配：{path}")

    # ── restore ─────────────────────────────────────────────

    def _restore(
        self,
        directory: Path,
        manifest: dict[str, Any],
        restore_workflow: bool,
    ) -> None:
        expected = {
            Path(entry["relative_path"]) for entry in manifest.get("files", [])
        }
        current = {path.relative_to(self.root) for path in self._creative_files()}
        for relative in sorted(current - expected, key=lambda item: len(item.parts), reverse=True):
            (self.root / relative).unlink(missing_ok=True)
        for relative in sorted(expected):
            source = directory / "files" / relative
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".savepoint-tmp")
            shutil.copy2(source, temporary)
            temporary.replace(destination)
        self._restore_sqlite(
            directory / manifest["sqlite"]["state_db"]["snapshot_path"],
            self.state_db,
        )
        workflow_entry = manifest.get("sqlite", {}).get("workflow_checkpoints")
        if restore_workflow:
            if workflow_entry:
                self._restore_sqlite(
                    directory / workflow_entry["snapshot_path"], self.workflow_db
                )
            else:
                self.workflow_db.unlink(missing_ok=True)
        self._restore_chroma(directory, manifest.get("chroma", []))

    @staticmethod
    def _restore_sqlite(snapshot: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".savepoint-tmp")
        shutil.copy2(snapshot, temporary)
        temporary.replace(destination)
        for suffix in ("-wal", "-shm"):
            Path(str(destination) + suffix).unlink(missing_ok=True)

    def _restore_chroma(
        self, directory: Path, entries: list[dict[str, Any]]
    ) -> None:
        client = chromadb.PersistentClient(
            path=str(self.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        where = {"$and": [
            {"novel_id": {"$eq": self.novel_id}},
            {"branch_id": {"$eq": self.branch_id}},
        ]}
        for entry in entries:
            name = str(entry["collection_name"])
            collection = client.get_or_create_collection(
                name=name, metadata=entry.get("collection_metadata") or None
            )
            existing = collection.get(where=where)
            ids = existing.get("ids", []) if existing else []
            if ids:
                collection.delete(ids=ids)
            payload = json.loads(
                (directory / entry["snapshot_path"]).read_text(encoding="utf-8")
            )
            if payload["ids"]:
                collection.add(
                    ids=payload["ids"],
                    documents=payload["documents"],
                    metadatas=payload["metadatas"],
                    embeddings=payload["embeddings"],
                )

    def _clear_future_workflow_checkpoints(self, chapter_index: int) -> None:
        if not self.workflow_db.is_file():
            return
        from langgraph.checkpoint.sqlite import SqliteSaver

        connection = sqlite3.connect(self.workflow_db, check_same_thread=False)
        saver = SqliteSaver(connection)
        thread_ids = set()
        try:
            for item in saver.list(None):
                thread_id = str(
                    item.config.get("configurable", {}).get("thread_id", "")
                )
                match = re.fullmatch(
                    rf"chapter:{re.escape(self.novel_id)}:(\d{{4,}})", thread_id
                )
                if match and int(match.group(1)) > chapter_index:
                    thread_ids.add(thread_id)
            for thread_id in thread_ids:
                saver.delete_thread(thread_id)
        finally:
            connection.close()

    def _verify_loaded_state(self, manifest: dict[str, Any]) -> None:
        chapter = int(manifest["chapter_index"])
        if self._latest_canonical_chapter() != chapter:
            raise SavepointVerificationError("Load 后 latest canonical chapter 不匹配")
        current = CurrentState.from_markdown(
            (self.root / "tracking" / "current_state.md").read_text(encoding="utf-8")
        )
        if current.through_chapter != chapter or current.chapter.chapter_index != chapter:
            raise SavepointVerificationError("Load 后 Current State 章节不匹配")
        try:
            completed = is_derived_ready(self.file_store, chapter)
        except ValueError as exc:
            raise SavepointVerificationError(str(exc)) from exc
        if not completed:
            raise SavepointVerificationError(
                f"Load 后缺少 Chapter {chapter} DERIVED_READY marker"
            )
        connection = sqlite3.connect(self.state_db)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        if not row or str(row[0]).lower() != "ok":
            raise SavepointVerificationError("Load 后 state.db integrity_check 失败")
        expected_db_hash = manifest["sqlite"]["state_db"]["sha256"]
        if _sha256_file(self.state_db) != expected_db_hash:
            raise SavepointVerificationError("Load 后 state.db hash 不匹配")
        for entry in manifest.get("files", []):
            path = self.root / entry["relative_path"]
            self._verify_file_entry(path, entry)
        self._verify_live_chroma(manifest.get("chroma", []))

    def _verify_live_chroma(self, entries: list[dict[str, Any]]) -> None:
        client = chromadb.PersistentClient(
            path=str(self.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        where = {"$and": [
            {"novel_id": {"$eq": self.novel_id}},
            {"branch_id": {"$eq": self.branch_id}},
        ]}
        for entry in entries:
            collection = client.get_collection(entry["collection_name"])
            raw = collection.get(
                where=where,
                include=["documents", "metadatas", "embeddings"],
            )
            payload = {
                "ids": _plain(raw.get("ids") if raw.get("ids") is not None else []),
                "documents": _plain(
                    raw.get("documents")
                    if raw.get("documents") is not None else []
                ),
                "metadatas": _plain(
                    raw.get("metadatas")
                    if raw.get("metadatas") is not None else []
                ),
                "embeddings": _plain(
                    raw.get("embeddings")
                    if raw.get("embeddings") is not None else []
                ),
            }
            if _json_hash(payload) != entry["logical_hash"]:
                raise SavepointVerificationError(
                    f"Load 后 Chroma collection 不匹配：{entry['collection_name']}"
                )

    def _savepoint_path(self, savepoint_id: str) -> Path:
        normalized = str(savepoint_id).strip().upper()
        if not re.fullmatch(r"S\d{4,}", normalized):
            raise SavepointError(f"非法 Savepoint ID：{savepoint_id}")
        return self.savepoints_root / normalized

    def _remove_empty_internal_dir(self) -> None:
        internal = self.savepoints_root / ".internal"
        try:
            internal.rmdir()
        except OSError:
            pass
