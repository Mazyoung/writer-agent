"""Machine-readable metadata from a raw Volume Plan Markdown artifact."""

from __future__ import annotations

import re
from dataclasses import dataclass


VOLUME_STATUSES = frozenset({"DRAFT", "ACTIVE", "COMPLETED"})


@dataclass(frozen=True)
class VolumeMetadata:
    volume_number: int
    status: str


def read_volume_metadata(text: str) -> VolumeMetadata:
    """Read only the volume number and lifecycle status metadata."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Volume Plan 为空")

    title = re.search(r"^\s*#\s+[^\n]*第\s*(\d+)\s*卷[^\n]*$", text, re.MULTILINE)
    if title is None:
        raise ValueError("Volume Plan 标题缺少卷号元数据")

    status_match = re.search(
        r"^\s*(?:-\s*)?\*\*状态\*\*\s*[:：]\s*([^\s]+)\s*$",
        text,
        re.MULTILINE,
    )
    if status_match is None:
        raise ValueError("volume_plan.md 缺少状态元数据")
    status = status_match.group(1).upper()
    if status not in VOLUME_STATUSES:
        raise ValueError(f"非法 status：{status}")

    return VolumeMetadata(volume_number=int(title.group(1)), status=status)
