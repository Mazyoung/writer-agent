"""Manual style-edit operation outside the chapter execution workflow."""

from src.agents.author.claude_stylist import ClaudeStylist
from src.agents.author.style_checker import StyleChecker
from src.config.settings import get_settings
from src.storage.file_store import FileStore


class ChapterEditingService:
    """Apply human-directed style feedback without review or state commit."""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.file_store = FileStore(novel_id, settings.data_dir)
        migrated = self.file_store.migrate_legacy_canonical_if_needed()
        if migrated:
            print(f"  [migration] canonical copies created: {list(migrated.keys())}")
        self.stylist = ClaudeStylist(novel_id)

    def style_edit(self, chapter_index: int, feedback: str = "") -> str:
        """Restyle the latest chapter, save it once, and run deterministic checks."""
        print("\n  [ClaudeStylist] 定向风格修改...")
        chapter_text = self.file_store.load_latest(
            "chapters", f"chapter_{chapter_index:04d}_styled"
        )
        if not chapter_text:
            chapter_text = self.file_store.load_latest(
                "chapters", f"chapter_{chapter_index:04d}"
            )
        if not chapter_text:
            raise ValueError(f"第 {chapter_index} 章正文不存在")

        plan_text = self.file_store.load_canonical(
            "outlines", f"chapter_plan_ch{chapter_index:04d}"
        ) or ""
        if feedback:
            self.file_store.save_feedback(
                f"style_feedback_ch{chapter_index:04d}", feedback
            )

        styled = self.stylist.edit_chapter(
            chapter_text,
            chapter_index,
            chapter_plan_text=plan_text,
            style_feedback=feedback,
        )
        self.file_store.save(
            "chapters", f"chapter_{chapter_index:04d}_styled", styled
        )

        report = StyleChecker(styled).check_all(file_path=f"第{chapter_index}章")
        print(report.summary())
        if report.errors > 0:
            print(
                f"\n  [!] {report.errors} 个错误 + {report.warnings} 个警告，请人工复核。"
            )
        print(f"  风格修改完成（{len(styled)} 字符）")
        return styled
