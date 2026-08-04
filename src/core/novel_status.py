"""Read-only novel status reporting."""

from src.config.settings import get_settings
from src.storage.document_formats import VolumePlan
from src.storage.file_store import FileStore
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
            active_volume = VolumePlan.from_markdown(volume_plan)
            status["active_volume"] = active_volume.volume_number
            status["active_volume_status"] = active_volume.status

        chapters_dir = self.file_store.root / "chapters"
        status["completed_chapters"] = len(
            list(chapters_dir.glob("chapter_*_styled*.md"))
        )

        for doc in [
            "character_relationships",
            "items_equipment",
            "cultivation_system",
        ]:
            status[f"has_{doc}"] = self.file_store.has_tracking_doc(doc)

        sqlite = SQLiteStore(self.sqlite_path)
        try:
            status["sqlite_chapter_count"] = sqlite.get_chapter_count(self.novel_id)
            status["pending_foreshadows"] = len(
                sqlite.get_pending_foreshadows(self.novel_id)
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
        relationships = "Y" if status.get("has_character_relationships") else "N"
        items = "Y" if status.get("has_items_equipment") else "N"
        cultivation = "Y" if status.get("has_cultivation_system") else "N"
        print(f"追踪文档: 角色关系{relationships} 物品装备{items} 修炼体系{cultivation}")
        print(f"未回收伏笔: {status['pending_foreshadows']}")
