"""Durable creative-state fact for fully derived canonical chapters."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from src.storage.file_store import FileStore


DERIVED_READY = "DERIVED_READY"
SCHEMA_VERSION = 1


def marker_path(file_store: FileStore, chapter_index: int) -> Path:
    return (
        file_store.root / "states"
        / f"chapter_{chapter_index:04d}_derived_ready.json"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mark_derived_ready(file_store: FileStore, chapter_index: int) -> Path:
    """Write the final marker only after every Derivation stage succeeded."""
    canonical = file_store.canonical_chapter_path(chapter_index)
    if not canonical.is_file():
        raise ValueError(
            f"无法写入 DERIVED_READY：第 {chapter_index} 章 Canonical 不存在"
        )
    path = marker_path(file_store, chapter_index)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": DERIVED_READY,
        "chapter_index": chapter_index,
        "canonical_source_path": (
            f"chapters/chapter_{chapter_index:04d}.md"
        ),
        "canonical_sha256": _sha256(canonical),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def is_derived_ready(file_store: FileStore, chapter_index: int) -> bool:
    """Validate the durable marker against the canonical chapter."""
    path = marker_path(file_store, chapter_index)
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"第 {chapter_index} 章 DERIVED_READY marker 无法读取：{exc}"
        ) from exc
    canonical = file_store.canonical_chapter_path(chapter_index)
    expected_source = f"chapters/chapter_{chapter_index:04d}.md"
    problems = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version")
    if payload.get("status") != DERIVED_READY:
        problems.append("status")
    if payload.get("chapter_index") != chapter_index:
        problems.append("chapter_index")
    if payload.get("canonical_source_path") != expected_source:
        problems.append("canonical_source_path")
    if not canonical.is_file():
        problems.append("Canonical")
    elif payload.get("canonical_sha256") != _sha256(canonical):
        problems.append("canonical_sha256")
    if problems:
        raise ValueError(
            f"第 {chapter_index} 章 DERIVED_READY marker 无效："
            + ", ".join(problems)
        )
    return True
