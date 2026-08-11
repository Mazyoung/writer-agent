"""Read-only novel status reporting."""

import re
import sqlite3

from src.config.settings import get_settings
from src.storage.chapter_completion import is_derived_ready
from src.storage.file_store import FileStore
from src.storage.volume_metadata import read_volume_metadata
from src.workflows.chapter_runner import ChapterWorkflowRunner


class NovelStatusService:
    """Build and print current progress without owning a workflow."""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.file_store = FileStore(novel_id, settings.data_dir, ensure_dirs=False)
        self.sqlite_path = settings.data_dir / "novels" / novel_id / "state.db"
        self.checkpoint_path = (
            settings.data_dir / "novels" / novel_id
            / "workflow_checkpoints.sqlite"
        )

    def _canonical_indexes(self) -> list[int]:
        indexes = []
        for path in self.file_store.list_chapters():
            match = re.fullmatch(r"chapter_(\d{4})\.md", path.name)
            if match:
                indexes.append(int(match.group(1)))
        return sorted(indexes)

    def _inspect_chapter(self, chapter_index: int) -> dict:
        if not self.checkpoint_path.is_file():
            return {"values": {}, "next": [], "interrupts": []}
        return ChapterWorkflowRunner(
            self.novel_id, chapter_index, ensure_dirs=False
        ).inspect()

    @staticmethod
    def _checkpoint_label(inspection: dict) -> str:
        interrupts = inspection.get("interrupts", [])
        payload = interrupts[0].get("value", {}) if interrupts else {}
        labels = {
            "plan_review": "Plan Review",
            "chapter_review": "Prose Review",
            "final_author_approval": "Prose Review",
            "human_final_approval": "Consistency Review",
            "review_override_confirmation": "Review Override",
            "human_writing": "Human Writing",
        }
        kind = str(payload.get("type", "")).strip()
        return labels.get(kind, kind or "Unknown")

    def _chapter_status(self) -> dict:
        indexes = self._canonical_indexes()
        for chapter in indexes:
            try:
                ready = is_derived_ready(self.file_store, chapter)
            except ValueError as exc:
                return {
                    "latest_chapter": chapter,
                    "canonical_committed": True,
                    "workflow_status": "DERIVATION_ERROR",
                    "failed_stage": "completion-marker",
                    "status_error": str(exc),
                    "next_action": "continue（恢复未完成派生）",
                }
            if not ready:
                inspection = self._inspect_chapter(chapter)
                values = inspection.get("values", {})
                workflow_status = str(
                    values.get("workflow_status", "CANONICAL_COMMITTED")
                ).upper()
                return {
                    "latest_chapter": chapter,
                    "canonical_committed": True,
                    "workflow_status": workflow_status,
                    "failed_stage": str(
                        values.get("failed_derivation_stage", "")
                    ),
                    "status_error": str(values.get("derivation_error", "")),
                    "next_action": "continue（恢复未完成派生）",
                }

        latest = indexes[-1] if indexes else 0
        next_chapter = latest + 1
        inspection = self._inspect_chapter(next_chapter)
        if inspection.get("interrupts"):
            return {
                "latest_chapter": next_chapter,
                "canonical_committed": False,
                "workflow_status": "WAITING_HUMAN",
                "checkpoint": self._checkpoint_label(inspection),
                "next_action": "continue（进入人工交互）",
            }
        values = inspection.get("values", {})
        if values and (
            inspection.get("next")
            or str(values.get("workflow_status", "")).upper()
            not in {"", "DERIVED_READY"}
        ):
            return {
                "latest_chapter": next_chapter,
                "canonical_committed": False,
                "workflow_status": str(
                    values.get("workflow_status", "IN_PROGRESS")
                ).upper(),
                "next_action": "continue（继续现有章节工作流）",
            }
        if latest:
            return {
                "latest_chapter": latest,
                "canonical_committed": True,
                "workflow_status": "DERIVED_READY",
                "next_action": f"continue（开始第 {latest + 1} 章）",
            }
        return {
            "latest_chapter": 0,
            "canonical_committed": False,
            "workflow_status": "NOT_STARTED",
            "next_action": "continue（开始第 1 章）",
        }

    def get_status(self) -> dict:
        status = {"novel": self.novel_id}

        volume_plan = self.file_store.load_tracking_doc("volume_plan")
        status["has_volume_plan"] = bool(volume_plan)
        status["has_book_plan"] = bool(
            self.file_store.load_tracking_doc("book_plan")
        )
        if volume_plan:
            active_volume = read_volume_metadata(volume_plan)
            status["active_volume"] = active_volume.volume_number
            status["active_volume_status"] = active_volume.status

        status["completed_chapters"] = len(self.file_store.list_chapters())

        status["has_current_state"] = self.file_store.has_tracking_doc(
            "current_state")
        status.update(self._chapter_status())

        status["sqlite_chapter_count"] = 0
        status["pending_foreshadows"] = 0
        if self.sqlite_path.is_file():
            connection = sqlite3.connect(
                f"file:{self.sqlite_path.as_posix()}?mode=ro", uri=True
            )
            try:
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "current_chapter_meta" in tables:
                    row = connection.execute(
                        "SELECT chapter_index FROM current_chapter_meta "
                        "WHERE novel_id=?", (self.novel_id,)
                    ).fetchone()
                    status["sqlite_chapter_count"] = int(row[0]) if row else 0
                if "current_foreshadow_state" in tables:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM current_foreshadow_state "
                        "WHERE novel_id=? AND status='OPEN'", (self.novel_id,)
                    ).fetchone()
                    status["pending_foreshadows"] = int(row[0]) if row else 0
            finally:
                connection.close()

        return status

    def print_status(self) -> None:
        status = self.get_status()
        print(f"\n小说: {status['novel']}")
        if status.get("active_volume"):
            print(
                f"当前卷: 第{status['active_volume']}卷 "
                f"({status['active_volume_status']})  "
                f"全书规划: {'有' if status['has_book_plan'] else '无'}"
            )
        else:
            print(
                f"卷规划: {'有' if status['has_volume_plan'] else '无'}  "
                f"全书规划: {'有' if status.get('has_book_plan') else '无'}"
            )
        print(f"已完成章节: {status['completed_chapters']}")
        current_state = "Y" if status.get("has_current_state") else "N"
        print(f"当前状态报告: {current_state}")
        print(f"未回收伏笔: {status['pending_foreshadows']}")
        chapter = status.get("latest_chapter", 0)
        if chapter:
            print(f"最新章节: 第 {chapter} 章")
            print(
                "正式正文: "
                + ("YES" if status.get("canonical_committed") else "NO")
            )
        workflow_status = status.get("workflow_status", "UNKNOWN")
        if workflow_status in {"DERIVED_READY", "DERIVATION_ERROR"}:
            print(f"派生状态: {workflow_status}")
        else:
            print(f"状态: {workflow_status}")
        if status.get("failed_stage"):
            print(f"失败阶段: {status['failed_stage']}")
        if status.get("checkpoint"):
            print(f"检查点: {status['checkpoint']}")
        print(f"下一动作: {status['next_action']}")
