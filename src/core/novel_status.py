"""Read-only novel status reporting."""

from src.config.settings import get_settings
from src.storage.current_state_store import CurrentStateStore
from src.storage.file_store import FileStore
from src.storage.volume_metadata import read_volume_metadata
from src.storage.sqlite_store import SQLiteStore


class NovelStatusService:
    """Build and print current progress without owning a workflow."""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.file_store = FileStore(novel_id, settings.data_dir)
        migrated = self.file_store.migrate_legacy_canonical_if_needed()
        if migrated:
            print(f"  [migration] canonical copies created: {list(migrated.keys())}")
        self.sqlite_path = settings.data_dir / "novels" / novel_id / "state.db"

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

        sqlite = SQLiteStore(self.sqlite_path)
        try:
            CurrentStateStore(
                self.novel_id, self.file_store, sqlite
            ).ensure_raw_initialized()
            current_meta = sqlite.get_current_chapter_meta(self.novel_id) or {}
            status["sqlite_chapter_count"] = int(
                current_meta.get("chapter_index", 0))
            status["pending_foreshadows"] = len(
                sqlite.get_current_pending_foreshadows(self.novel_id)
            )
        finally:
            sqlite.close()

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
