"""
StateManager — 合并审阅/状态更新/一致性检查/事实摘要的章节后分析Agent。

替换了旧的 BriefGenerator + StateUpdater + ConsistencyGuard + QualityReviewer。
"""

from pathlib import Path
from src.core.agent_base import BaseAgent
from src.storage.file_store import FileStore
from src.storage.document_formats import (
    FactDigest, CharacterRelationships, ItemsEquipment, CultivationSystem,
    RelationshipChange, ItemLog, ItemEntry, CharacterCultivation,
    _extract_section,
)
from src.storage.sqlite_store import SQLiteStore


class StateManager(BaseAgent):
    """章节后状态分析 + 追踪文档更新"""

    def __init__(self, novel_id: str, sqlite: SQLiteStore):
        super().__init__("state_manager", novel_id, "state_manager.txt")
        self.sqlite = sqlite
        # 使用独立 FileStore 来读写 tracking 文档
        from src.config.settings import get_settings
        settings = get_settings()
        self.fs = FileStore(novel_id, settings.data_dir)

    def review_chapter(self, chapter_text: str, chapter_index: int,
                       chapter_plan_text: str = "",
                       current_relationships: str = "",
                       current_items: str = "",
                       current_cultivation: str = "") -> dict:
        """分析章节并返回结构化结果。

        Returns:
            dict with keys: fact_digest, relationship_changes, item_changes,
                           cultivation_changes, consistency_issues, quality_review
        """
        user_msg = f"""## 第 {chapter_index} 章正文

{chapter_text}

---

## 当前角色关系文档
{current_relationships or "暂无"}

## 当前物品装备文档
{current_items or "暂无"}

## 当前修炼体系文档
{current_cultivation or "暂无"}

## 章规划（用于对比）
{chapter_plan_text or "暂无"}

---
请按输出格式分析本章。"""

        result = self.run(
            user_message=user_msg,
            save_category="states",
            save_prefix=f"review_ch{chapter_index:04d}",
        )
        return {"raw_analysis": result.content, "filepath": result.filepath}

    def extract_fact_digest(self, chapter_text: str, chapter_index: int) -> FactDigest:
        """从章节正文提取事实摘要（LLM 调用 — 保留用于独立 fact-digest 场景）。

        E05: review_chapter 主流程不再调用此方法。
        改为从 raw_analysis 中确定性提取，消除第二次 LLM 调用。
        """
        user_msg = f"""## 第 {chapter_index} 章正文

{chapter_text}

---
请只输出「事实摘要」部分（六个子节）。"""

        result = self.run(
            user_message=user_msg,
            save_category="states",
            save_prefix=f"fact_digest_ch{chapter_index:04d}",
        )
        return FactDigest.from_markdown(result.content)

    def extract_fact_digest_from_analysis(self, analysis_text: str,
                                          chapter_index: int) -> FactDigest:
        """E05: 从 review raw_analysis 中确定性提取事实摘要（无 LLM）。

        提取「## 事实摘要」→ 解析六个子节 → 保存
        → 返回 FactDigest 对象。

        正常路径不产生第二次 LLM 请求。
        解析失败时输出 [STATE WARNING]，不崩溃也不回滚 canonical state。
        """
        # 1. Extract ## 事实摘要 section from raw analysis
        section = _extract_section(analysis_text, "## 事实摘要")
        if not section.strip():
            print(f"  [STATE WARNING] raw_analysis 中未找到「## 事实摘要」区域，"
                  f"第{chapter_index}章 Fact Digest 未生成")
            return FactDigest(chapter_index=chapter_index)

        # 2. Parse with FactDigest.from_markdown
        fd = FactDigest.from_markdown(section)
        fd.chapter_index = chapter_index

        # 3. Verify at least some sub-sections contain content
        has_content = any([
            fd.confirmed_items.strip(),
            fd.confirmed_character_states.strip(),
            fd.confirmed_events.strip(),
            fd.confirmed_numbers.strip(),
            fd.explicitly_absent.strip(),
            fd.pending_suspense.strip(),
        ])
        if not has_content:
            print(f"  [STATE WARNING] 第{chapter_index}章 Fact Digest 六个子节全为空，"
                  f"可能解析失败（raw_analysis 格式与预期不一致）")
            return fd  # still return, don't crash

        # 4. Save via FileStore
        self.fs.save("states", f"fact_digest_ch{chapter_index:04d}",
                     fd.to_markdown())
        return fd

    def update_tracking_docs(self, chapter_index: int, chapter_text: str,
                             analysis_text: str) -> dict:
        """基于分析结果更新所有追踪文档。

        Returns:
            dict with keys: updated_rels, updated_items, updated_cult, change_log
        """
        changes = {"updated_rels": False, "updated_items": False,
                    "updated_cult": False, "change_log": ""}
        log_lines = [f"## 第{chapter_index}章 状态更新", ""]

        # 1. 角色关系
        rels_text = self.fs.load_tracking_doc("character_relationships")
        rels = CharacterRelationships.from_markdown(rels_text) if rels_text else CharacterRelationships()
        rel_changes = self._extract_relationship_changes(analysis_text, chapter_index)
        if rel_changes:
            rels.change_log.extend(rel_changes)
            self.fs.save_tracking_doc("character_relationships", rels.to_markdown())
            changes["updated_rels"] = True
            log_lines.append(f"### 角色关系 — {len(rel_changes)} 条变更")
            for rc in rel_changes:
                log_lines.append(f"- {rc.characters}: {rc.change}")

        # 2. 物品装备
        items_text = self.fs.load_tracking_doc("items_equipment")
        items = ItemsEquipment.from_markdown(items_text) if items_text else ItemsEquipment()
        item_changes = self._extract_item_changes(analysis_text, chapter_index)
        if item_changes:
            items.item_logs.append(ItemLog(
                chapter=f"第{chapter_index}章",
                gained=item_changes.get("gained", []),
                consumed=item_changes.get("consumed", []),
                lost=item_changes.get("lost", []),
            ))
            self.fs.save_tracking_doc("items_equipment", items.to_markdown())
            changes["updated_items"] = True
            log_lines.append(f"### 物品装备 — {sum(len(v) for v in item_changes.values())} 条变更")

        # 3. 修炼体系
        cult_text = self.fs.load_tracking_doc("cultivation_system")
        cult = CultivationSystem.from_markdown(cult_text) if cult_text else CultivationSystem()
        cult_changes = self._extract_cultivation_changes(analysis_text, chapter_index)
        if cult_changes:
            if cult.rule_changes:
                cult.rule_changes += f"\n### 第{chapter_index}章\n{cult_changes}"
            else:
                cult.rule_changes = f"### 第{chapter_index}章\n{cult_changes}"
            self.fs.save_tracking_doc("cultivation_system", cult.to_markdown())
            changes["updated_cult"] = True
            log_lines.append(f"### 修炼体系 — 已更新")

        # 4. SQLite 缓存同步
        try:
            self._sync_sqlite(chapter_index, chapter_text, analysis_text)
        except Exception:
            pass  # SQLite 是缓存，失败了不影响主流程

        changes["change_log"] = "\n".join(log_lines)
        self.fs.save("states", f"post_chapter_update_ch{chapter_index:04d}",
                      changes["change_log"])
        return changes

    # ── 内部方法 ─────────────────────────────────────────

    def _extract_relationship_changes(self, analysis_text: str,
                                      chapter_index: int) -> list:
        """从分析文本中提取角色关系变更。"""
        changes = []
        import re
        section = ""
        for header in ["### 角色关系", "## 追踪文档变更建议", "### 角色关系图"]:
            pattern = rf'{re.escape(header)}\s*\n(.*?)(?=##|\Z)'
            m = re.search(pattern, analysis_text, re.DOTALL)
            if m:
                section = m.group(1)
                break
        for line in section.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                content = stripped[2:]
                if ":" in content:
                    chars, change = content.split(":", 1)
                    changes.append(RelationshipChange(
                        chapter=f"第{chapter_index}章",
                        characters=chars.strip(),
                        change=change.strip(),
                    ))
        return changes

    def _extract_item_changes(self, analysis_text: str,
                              chapter_index: int) -> dict:
        """从分析文本中提取物品变更。"""
        import re
        result = {"gained": [], "consumed": [], "lost": []}
        section = ""
        for header in ["### 物品装备", "### 物品/装备追踪"]:
            pattern = rf'{re.escape(header)}\s*\n(.*?)(?=###|\Z)'
            m = re.search(pattern, analysis_text, re.DOTALL)
            if m:
                section = m.group(1)
                break
        current_type = None
        for line in section.split("\n"):
            stripped = line.strip()
            if "获得" in stripped:
                current_type = "gained"
            elif "消耗" in stripped:
                current_type = "consumed"
            elif "失去" in stripped:
                current_type = "lost"
            elif stripped.startswith("- ") and current_type:
                result[current_type].append(stripped[2:])
        return result

    def _extract_cultivation_changes(self, analysis_text: str,
                                     chapter_index: int) -> str:
        """从分析文本中提取修炼体系变更。"""
        import re
        for header in ["### 修炼体系", "### 修炼/力量体系现状"]:
            pattern = rf'{re.escape(header)}\s*\n(.*?)(?=###|\Z)'
            m = re.search(pattern, analysis_text, re.DOTALL)
            if m:
                return m.group(1).strip()
        return ""

    def _sync_sqlite(self, chapter_index: int, chapter_text: str,
                     analysis_text: str):
        """将提取的角色状态和伏笔同步到 SQLite 缓存。"""
        novel_id = self.novel_id

        # 提取角色名并确保 SQLite 中有记录
        import re
        char_names = set()
        for m in re.finditer(r'\*\*(.+?)\*\*', analysis_text):
            name = m.group(1).strip()
            if 2 <= len(name) <= 6 and not any(
                kw in name for kw in ["状态", "关系", "物品", "体系", "检查", "审阅"]
            ):
                char_names.add(name)

        for name in char_names:
            try:
                self.sqlite.upsert_character_state(
                    novel_id, str(chapter_index), name, {"last_seen": f"第{chapter_index}章"}
                )
            except Exception:
                pass
