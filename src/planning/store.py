"""PlanningStore — Replanning Foundation 的 JSON 持久化接口（E03）。

存储布局（位于 novels/<novel_id>/tracking/ 下，与现有规划文档同根）：

    tracking/
    ├── book_plan.md / volume_plan.md      # 活跃规划（canonical，不归本 store 管）
    ├── volumes/volume_NN.md               # 已完成卷的归档
    ├── revisions/<revision_id>.json       # PlanRevision
    ├── replan_requests/<report_id>.json   # PlanningModificationReport
    ├── strategic_repairs/<case_id>.json   # StrategicRepairCase
    ├── checkpoints/<checkpoint_id>.json   # ChapterCheckpoint
    └── branches/<branch_id>.json          # StoryBranch

每个记录一个 JSON 文件，可读、可 diff、可手工编辑。
本 store 只做持久化，不包含任何自动 L2/L3 行为。
"""

import json
from pathlib import Path

from src.planning.models import (
    PlanRevision, PlanningModificationReport, StrategicRepairCase,
    StoryBranch, ChapterCheckpoint,
)


class PlanningStore:
    """Replanning Foundation 各数据模型的统一持久化入口。"""

    # kind -> (子目录, 数据类, id 字段)
    _KINDS = {
        "revision": ("revisions", PlanRevision, "revision_id"),
        "report": ("replan_requests", PlanningModificationReport, "report_id"),
        "repair_case": ("strategic_repairs", StrategicRepairCase, "case_id"),
        "checkpoint": ("checkpoints", ChapterCheckpoint, "checkpoint_id"),
        "branch": ("branches", StoryBranch, "branch_id"),
    }

    def __init__(self, novel_root: Path):
        self.base = Path(novel_root) / "tracking"

    # ── 通用读写 ────────────────────────────────────────────

    def _dir(self, kind: str) -> Path:
        d = self.base / self._KINDS[kind][0]
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save(self, kind: str, obj) -> Path:
        _, cls, id_field = self._KINDS[kind]
        path = self._dir(kind) / f"{getattr(obj, id_field)}.json"
        path.write_text(json.dumps(obj.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path

    def _load(self, kind: str, record_id: str):
        _, cls, _ = self._KINDS[kind]
        path = self._dir(kind) / f"{record_id}.json"
        if not path.exists():
            return None
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _list(self, kind: str) -> list:
        _, cls, _ = self._KINDS[kind]
        d = self._dir(kind)
        return [cls.from_dict(json.loads(f.read_text(encoding="utf-8")))
                for f in sorted(d.glob("*.json"))]

    # ── 类型化接口 ──────────────────────────────────────────

    def save_revision(self, rev: PlanRevision) -> Path:
        return self._save("revision", rev)

    def load_revision(self, revision_id: str):
        return self._load("revision", revision_id)

    def list_revisions(self) -> list[PlanRevision]:
        return self._list("revision")

    def save_report(self, rep: PlanningModificationReport) -> Path:
        return self._save("report", rep)

    def load_report(self, report_id: str):
        return self._load("report", report_id)

    def list_reports(self) -> list[PlanningModificationReport]:
        return self._list("report")

    def save_repair_case(self, case: StrategicRepairCase) -> Path:
        return self._save("repair_case", case)

    def load_repair_case(self, case_id: str):
        return self._load("repair_case", case_id)

    def list_repair_cases(self) -> list[StrategicRepairCase]:
        return self._list("repair_case")

    def save_checkpoint(self, ckpt: ChapterCheckpoint) -> Path:
        return self._save("checkpoint", ckpt)

    def load_checkpoint(self, checkpoint_id: str):
        return self._load("checkpoint", checkpoint_id)

    def list_checkpoints(self) -> list[ChapterCheckpoint]:
        return self._list("checkpoint")

    def save_branch(self, branch: StoryBranch) -> Path:
        return self._save("branch", branch)

    def load_branch(self, branch_id: str):
        return self._load("branch", branch_id)

    def list_branches(self) -> list[StoryBranch]:
        return self._list("branch")
