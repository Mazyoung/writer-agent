from pathlib import Path
from datetime import datetime
from typing import Optional


class FileStore:
    """管理 data/novels/<novel_id>/ 下的文件读写"""

    def __init__(self, novel_id: str, data_dir: Path):
        self.novel_id = novel_id
        self.root = data_dir / "novels" / novel_id
        self._ensure_dirs()

    def _ensure_dirs(self):
        for sub in ["settings", "outlines", "chapters", "states", "briefs",
                     "tracking", "feedback"]:
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def save(self, category: str, filename: str, content: str) -> Path:
        """保存文件，自动加时间戳"""
        ts = self._timestamp()
        filepath = self.root / category / f"{filename}_{ts}.md"
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def load_latest(self, category: str, prefix: str) -> Optional[str]:
        """读取最新版本的文本文件"""
        edited = self._find_edited(category, prefix)
        if edited:
            return edited.read_text(encoding="utf-8")

        files = sorted(self.root.glob(f"{category}/{prefix}_*.md"), reverse=True)
        if not files:
            return None
        return files[0].read_text(encoding="utf-8")

    def has_human_edit(self, category: str, prefix: str) -> bool:
        """检查是否存在人工编辑版本"""
        return self._find_edited(category, prefix) is not None

    def _find_edited(self, category: str, prefix: str) -> Optional[Path]:
        """查找 _edited 版本的文件"""
        edited = self.root / category / f"{prefix}_edited.md"
        if edited.exists():
            return edited

        edited_json = self.root / category / f"{prefix}_edited.json"
        if edited_json.exists():
            return edited_json

        return None

    def save_canonical(self, category: str, filename: str, content: str) -> Path:
        """保存为固定文件名（无时间戳），覆盖旧版本。旧版本备份为 .bak.md"""
        filepath = self.root / category / f"{filename}.md"
        self._backup_if_exists(filepath)
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def load_canonical(self, category: str, filename: str) -> Optional[str]:
        """读取 canonical 文件。优先 _edited.md，其次 .md"""
        edited = self.root / category / f"{filename}_edited.md"
        if edited.exists():
            return edited.read_text(encoding="utf-8")
        plain = self.root / category / f"{filename}.md"
        if plain.exists():
            return plain.read_text(encoding="utf-8")
        return None

    def has_canonical(self, category: str, filename: str) -> bool:
        """检查 canonical 文件是否存在"""
        return (self.root / category / f"{filename}.md").exists()

    def _backup_if_exists(self, filepath: Path):
        """如果文件存在，重命名为 .bak.md"""
        if filepath.exists():
            bak = filepath.with_suffix(".bak.md")
            if bak.exists():
                bak.unlink()
            filepath.rename(bak)

    def migrate_legacy_canonical_if_needed(self) -> dict:
        """Create canonical copies for legacy novels when migration is needed."""
        if self.has_canonical("settings", "world_setting"):
            return {}
        if not self.load_latest("settings", "world_setting"):
            return {}
        return self.migrate_to_canonical()

    def migrate_to_canonical(self) -> dict:
        """一次性迁移：从最新时间戳文件创建 canonical 副本。不删除旧文件。"""
        migrated = {}

        # world_setting
        ws = self.load_latest("settings", "world_setting")
        if ws:
            self.save_canonical("settings", "world_setting", ws)
            migrated["settings/world_setting.md"] = "ok"

        # plot_structure
        ps = self.load_latest("outlines", "plot_structure")
        if ps:
            self.save_canonical("outlines", "plot_structure", ps)
            migrated["outlines/plot_structure.md"] = "ok"

        # scene plans (per chapter)
        import re
        plan_files = sorted(self.root.glob("outlines/scene_plan_ch*_*.md"))
        seen_chapters = set()
        # 逆序处理，最新的在前；replan 优先
        for f in reversed(plan_files):
            m = re.match(r'scene_plan_ch(\d{4})', f.stem)
            if m:
                ch = m.group(1)
                if ch in seen_chapters:
                    continue
                seen_chapters.add(ch)
                content = f.read_text(encoding="utf-8")
                self.save_canonical("outlines", f"scene_plan_ch{ch}", content)
                migrated[f"outlines/scene_plan_ch{ch}.md"] = "ok"

        return migrated

    def list_chapters(self) -> list[Path]:
        """列出所有已完成章节"""
        return sorted(self.root.glob("chapters/chapter_*.md"))

    def rollback_canonical(self, category: str, filename: str) -> bool:
        """回退 canonical 文件：用 .bak.md 替换 .md。成功返回 True。"""
        main = self.root / category / f"{filename}.md"
        bak = self.root / category / f"{filename}.bak.md"
        if bak.exists():
            if main.exists():
                main.unlink()
            bak.rename(main)
            return True
        return False

    def has_bak(self, category: str, filename: str) -> bool:
        """检查是否存在 .bak 备份"""
        return (self.root / category / f"{filename}.bak.md").exists()

    def get_novel_dir(self) -> Path:
        return self.root

    # ── 新系统追踪文档 ──────────────────────────────────

    def load_tracking_doc(self, name: str) -> Optional[str]:
        """加载 tracking 目录下的文档（优先 _edited，其次 .md）。"""
        return self.load_canonical("tracking", name)

    def save_tracking_doc(self, name: str, content: str) -> Path:
        """保存 tracking 目录下的 canonical 文档。"""
        return self.save_canonical("tracking", name, content)

    def has_tracking_doc(self, name: str) -> bool:
        """检查 tracking 文档是否存在。"""
        return self.has_canonical("tracking", name)

    def load_feedback(self, prefix: str) -> Optional[str]:
        """加载人工反馈文件。"""
        return self.load_canonical("feedback", prefix)

    def save_feedback(self, prefix: str, content: str) -> Path:
        """保存人工反馈文件。"""
        return self.save_canonical("feedback", prefix, content)
