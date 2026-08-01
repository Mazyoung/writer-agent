import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Optional


class SQLiteStore:
    """管理四张核心状态表 + 章节元数据表"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS character_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS foreshadowing (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id TEXT NOT NULL,
                description TEXT NOT NULL,
                planted_chapter TEXT NOT NULL,
                expected_resolve_chapter TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                resolved_chapter TEXT
            );

            CREATE TABLE IF NOT EXISTS chapter_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id TEXT NOT NULL,
                chapter_index INTEGER NOT NULL,
                title TEXT,
                summary TEXT,
                word_count INTEGER,
                created_at TEXT NOT NULL
            );
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ─── character_state ───

    def upsert_character_state(self, novel_id: str, chapter_id: str,
                                name: str, status: dict):
        now = datetime.now().isoformat()
        existing = self.conn.execute(
            "SELECT id FROM character_state WHERE novel_id=? AND name=?",
            (novel_id, name)
        ).fetchone()

        status_json = json.dumps(status, ensure_ascii=False)
        if existing:
            self.conn.execute(
                "UPDATE character_state SET chapter_id=?, status_json=?, updated_at=? WHERE id=?",
                (chapter_id, status_json, now, existing["id"])
            )
        else:
            self.conn.execute(
                "INSERT INTO character_state (novel_id, chapter_id, name, status_json, updated_at) VALUES (?,?,?,?,?)",
                (novel_id, chapter_id, name, status_json, now)
            )
        self.conn.commit()

    def get_all_characters(self, novel_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM character_state WHERE novel_id=? ORDER BY name",
            (novel_id,)
        ).fetchall()
        return [{"name": r["name"], "status": json.loads(r["status_json"]),
                 "chapter_id": r["chapter_id"], "updated_at": r["updated_at"]} for r in rows]

    # ─── foreshadowing ───

    def add_foreshadowing(self, novel_id: str, description: str,
                          planted_chapter: str, expected_resolve: Optional[str] = None):
        self.conn.execute(
            "INSERT INTO foreshadowing (novel_id, description, planted_chapter, expected_resolve_chapter, status) VALUES (?,?,?,?,?)",
            (novel_id, description, planted_chapter, expected_resolve, "pending")
        )
        self.conn.commit()

    def resolve_foreshadowing(self, foreshadow_id: int, resolved_chapter: str):
        self.conn.execute(
            "UPDATE foreshadowing SET status='resolved', resolved_chapter=? WHERE id=?",
            (resolved_chapter, foreshadow_id)
        )
        self.conn.commit()

    def resolve_foreshadowing_by_desc(self, description_substr: str, chapter_id: str):
        """通过描述子字符串匹配，将匹配的 pending 伏笔标记为已回收"""
        self.conn.execute(
            "UPDATE foreshadowing SET status='resolved', resolved_chapter=? "
            "WHERE description LIKE ? AND status='pending'",
            (chapter_id, f"%{description_substr}%")
        )
        self.conn.commit()

    def upsert_foreshadow(self, novel_id: str, description: str,
                          status: str, resolved_chapter: str = ""):
        """E06: 插入或更新伏笔状态。

        如果已存在相同描述的 pending 伏笔，更新状态；
        如果 status=RESOLVED，标记为已回收；
        如果不存在，插入新记录。
        """
        cursor = self.conn.execute(
            "SELECT id FROM foreshadowing WHERE novel_id=? AND description LIKE ?",
            (novel_id, f"%{description}%"))
        rows = cursor.fetchall()
        if rows:
            if status.upper() in ("RESOLVED", "resolved"):
                self.conn.execute(
                    "UPDATE foreshadowing SET status='resolved', resolved_chapter=? WHERE id=?",
                    (resolved_chapter, rows[0][0]))
            elif status.upper() in ("ABANDONED", "abandoned"):
                self.conn.execute(
                    "UPDATE foreshadowing SET status='abandoned' WHERE id=?",
                    (rows[0][0],))
            else:
                self.conn.execute(
                    "UPDATE foreshadowing SET status='pending' WHERE id=?",
                    (rows[0][0],))
        else:
            self.conn.execute(
                "INSERT INTO foreshadowing (novel_id, description, planted_chapter, "
                "expected_resolve_chapter, status) VALUES (?,?,?,?,?)",
                (novel_id, description, "",
                 resolved_chapter if status.upper() == "RESOLVED" else "",
                 "pending" if status.upper() not in ("RESOLVED", "ABANDONED")
                 else status.lower()))
        self.conn.commit()

    def get_pending_foreshadows(self, novel_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM foreshadowing WHERE novel_id=? AND status='pending' ORDER BY id",
            (novel_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ─── chapter_meta ───

    def add_chapter_meta(self, novel_id: str, chapter_index: int,
                         title: str, summary: str, word_count: int):
        now = datetime.now().isoformat()
        self.conn.execute(
            "INSERT INTO chapter_meta (novel_id, chapter_index, title, summary, word_count, created_at) VALUES (?,?,?,?,?,?)",
            (novel_id, chapter_index, title, summary, word_count, now)
        )
        self.conn.commit()

    def get_chapter_count(self, novel_id: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(chapter_index) as cnt FROM chapter_meta WHERE novel_id=?",
            (novel_id,)
        ).fetchone()
        return row["cnt"] or 0

    def export_all_states(self, novel_id: str) -> dict:
        """导出所有状态表为字典 — world_state 和 active_conflicts 已迁移到 Markdown 文档。"""
        return {
            "characters": self.get_all_characters(novel_id),
            "pending_foreshadows": self.get_pending_foreshadows(novel_id),
            "chapter_count": self.get_chapter_count(novel_id),
        }

    def export_states_summary(self, novel_id: str) -> str:
        """导出状态摘要文本 — 伏笔和角色状态来自 SQLite 缓存。"""
        chars = self.get_all_characters(novel_id)
        foreshadows = self.get_pending_foreshadows(novel_id)

        parts = []
        if chars:
            parts.append("## 角色当前状态 (SQLite缓存)")
            for c in chars:
                parts.append(f"- {c['name']}: {json.dumps(c['status'], ensure_ascii=False)}")
        if foreshadows:
            parts.append("\n## 未回收伏笔")
            for f in foreshadows:
                parts.append(f"- [{f['id']}] {f['description']} (埋于第{f['planted_chapter']}章)")

        return "\n".join(parts) if parts else ""
