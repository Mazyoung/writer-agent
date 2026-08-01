"""
StateManager — 章节后状态分析 + 追踪文档更新 + 审阅决策（E06）。

E06 核心变更：
- review_chapter 接收 world_setting，确保 T1 一致性检查有真实依据
- update_tracking_docs 维护 Current Structured State（不只是 change log）
- parse_review_decision 从 raw_analysis 确定性提取 ReviewDecision（无额外 LLM）
- 状态变更采用 State Delta → deterministic apply 模式
"""

from pathlib import Path
import re
from typing import Optional

from src.core.agent_base import BaseAgent
from src.storage.file_store import FileStore
from src.storage.document_formats import (
    FactDigest, CharacterRelationships, ItemsEquipment, CultivationSystem,
    RelationshipChange, RelationshipEntry, ItemLog, ItemEntry, CharacterCultivation,
    ReviewDecision, _extract_section,
)
from src.storage.sqlite_store import SQLiteStore


class StateManager(BaseAgent):
    """章节后状态分析 + 追踪文档更新 + 审阅决策（E06）"""

    def __init__(self, novel_id: str, sqlite: SQLiteStore):
        super().__init__("state_manager", novel_id, "state_manager.txt")
        self.sqlite = sqlite
        from src.config.settings import get_settings
        settings = get_settings()
        self.fs = FileStore(novel_id, settings.data_dir)

    def review_chapter(self, chapter_text: str, chapter_index: int,
                       chapter_plan_text: str = "",
                       current_relationships: str = "",
                       current_items: str = "",
                       current_cultivation: str = "",
                       world_setting: str = "") -> dict:
        """E06: 分析章节 + 世界观上下文，返回结构化结果。

        Args:
            chapter_text: 章节正文
            chapter_index: 章序号
            chapter_plan_text: 章规划
            current_relationships: tracking/character_relationships.md
            current_items: tracking/items_equipment.md
            current_cultivation: tracking/cultivation_system.md
            world_setting: settings/world_setting.md（E06 新增）

        Returns:
            dict with keys: raw_analysis, filepath
        """
        parts = [f"## 第 {chapter_index} 章正文\n\n{chapter_text}\n\n---"]

        # E06: World Setting 进入 review（T1 一致性检查必需）
        if world_setting:
            parts.append(f"## 世界观设定（用于一致性检查，截断至 2000 字符）"
                         f"\n{world_setting[:2000]}\n\n---")

        parts.append(f"## 章规划（用于对比）\n{chapter_plan_text or '暂无'}\n\n---")

        parts.append("## 当前追踪文档\n")

        parts.append(f"### character_relationships.md\n"
                     f"{current_relationships or '暂无'}")

        parts.append(f"### items_equipment.md\n"
                     f"{current_items or '暂无'}")

        parts.append(f"### cultivation_system.md\n"
                     f"{current_cultivation or '暂无'}")

        parts.append("---\n请按输出格式分析本章。")

        user_msg = "\n\n".join(parts)

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
        """E05/E06: 从 review raw_analysis 中确定性提取事实摘要（无 LLM）。

        提取「## 事实摘要」→ 解析六个子节 → 保存
        → 返回 FactDigest 对象。

        正常路径不产生第二次 LLM 请求。
        解析失败时输出 [STATE WARNING]，不崩溃也不回滚 canonical state。
        """
        section = _extract_section(analysis_text, "## 事实摘要")
        if not section.strip():
            print(f"  [STATE WARNING] raw_analysis 中未找到「## 事实摘要」区域，"
                  f"第{chapter_index}章 Fact Digest 未生成")
            return FactDigest(chapter_index=chapter_index)

        fd = FactDigest.from_markdown(section)
        fd.chapter_index = chapter_index

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
            return fd

        self.fs.save("states", f"fact_digest_ch{chapter_index:04d}",
                     fd.to_markdown())
        return fd

    def parse_review_decision(self, analysis_text: str) -> ReviewDecision:
        """E06: 从 raw_analysis 确定性提取 ReviewDecision（无额外 LLM）。

        Fail-closed: 解析失败 → UNKNOWN + [SUPERVISOR WARNING]。
        """
        rd = ReviewDecision.from_analysis(analysis_text)
        if rd.verdict == "UNKNOWN":
            print(f"  [SUPERVISOR WARNING] 无法从 raw_analysis 解析审阅决策，"
                  f"默认 UNKNOWN（fail-closed），不会自动 PASS")
        return rd

    def update_tracking_docs(self, chapter_index: int, chapter_text: str,
                             analysis_text: str) -> dict:
        """E06: 基于分析结果更新追踪文档。

        E06 关键变更：
        - 解析「## 状态变更（State Delta）」→ 确定性应用到 Current State
        - 「## 追踪文档变更建议」→ 继续追加 Change Log
        - 双双维护（Current State + Change Log）

        Returns:
            dict with keys: updated_rels, updated_items, updated_cult, change_log
        """
        changes = {"updated_rels": False, "updated_items": False,
                    "updated_cult": False, "change_log": ""}
        log_lines = [f"## 第{chapter_index}章 状态更新", ""]

        # ── Step 1: Deterministic State Delta apply ──
        state_changes = self._apply_state_deltas(chapter_index, analysis_text)
        if state_changes.get("relationships"):
            changes["updated_rels"] = True
            log_lines.append(f"### 角色关系 — {len(state_changes['relationships'])} 条状态变更")
            for entry in state_changes["relationships"]:
                log_lines.append(f"- {entry.characters}: {entry.relation_type}, {entry.current_state}")
        if state_changes.get("items"):
            changes["updated_items"] = True
            log_lines.append(f"### 物品装备 — {len(state_changes['items'])} 条状态变更")
        if state_changes.get("cultivation"):
            changes["updated_cult"] = True
            log_lines.append(f"### 修炼体系 — {len(state_changes['cultivation'])} 条状态变更")

        # ── Step 2: Change log append (historical audit trail) ──
        log_changes = self._append_change_logs(chapter_index, analysis_text)
        if log_changes.get("rels"):
            changes["updated_rels"] = True
        if log_changes.get("items"):
            changes["updated_items"] = True
        if log_changes.get("cult"):
            changes["updated_cult"] = True

        # ── Step 3: SQLite cache sync ──
        try:
            self._sync_sqlite(chapter_index, chapter_text, analysis_text)
        except Exception:
            pass

        changes["change_log"] = "\n".join(log_lines)
        self.fs.save("states", f"post_chapter_update_ch{chapter_index:04d}",
                      changes["change_log"])
        return changes

    # ── State Delta Parsing & Apply ────────────────────────

    @staticmethod
    def _parse_state_kv(text: str) -> dict[str, str]:
        """Parse `key=value, key=value` format (E06 state delta format).

        Unlike _parse_key_value which handles `**key**: value`,
        this handles the comma-separated key=value format used in
        state delta sections.
        """
        result: dict[str, str] = {}
        # Remove [依据: ...] suffix if present
        bracket = text.find("[依据:")
        if bracket >= 0:
            text = text[:bracket].strip().rstrip(",")
        for part in text.split(","):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                result[k.strip()] = v.strip()
        return result

    def _apply_state_deltas(self, chapter_index: int,
                            analysis_text: str) -> dict:
        """Parse 「## 状态变更（State Delta）」and deterministically apply.

        Returns:
            dict with keys: relationships, items, cultivation
        """
        result: dict = {"relationships": [], "items": [], "cultivation": []}
        delta_section = _extract_section(analysis_text, "## 状态变更（State Delta）")
        if not delta_section:
            delta_section = _extract_section(analysis_text, "## 状态变更")
        if not delta_section.strip():
            return result

        ch_label = f"第{chapter_index}章"

        # ── 1. Relationships Current State ──
        rel_delta = _extract_section(delta_section, "### 角色关系当前状态")
        if rel_delta.strip():
            rels_text = self.fs.load_tracking_doc("character_relationships")
            rels = (CharacterRelationships.from_markdown(rels_text)
                    if rels_text else CharacterRelationships())
            for line in rel_delta.strip().split("\n"):
                stripped = line.strip()
                if not stripped.startswith("- "):
                    continue
                content = stripped[2:].strip()
                # Format: 角色A ↔ 角色B: 关系类型=XX, 当前状态=XX, 态度=XX [依据: ...]
                if ":" not in content:
                    continue
                chars, rest = content.split(":", 1)
                chars = chars.strip()
                kv = self._parse_state_kv(rest)
                rel_type = kv.get("关系类型", "")
                cur_state = kv.get("当前状态", "")
                attitude = kv.get("态度", "")

                # Deterministic apply: update or create entry
                found = False
                for entry in rels.entries:
                    if entry.characters == chars:
                        if rel_type:
                            entry.relation_type = rel_type
                        if cur_state:
                            entry.current_state = cur_state
                        if attitude:
                            entry.attitude = attitude
                        entry.last_interaction = ch_label
                        found = True
                        break
                if not found:
                    rels.entries.append(RelationshipEntry(
                        characters=chars, relation_type=rel_type,
                        current_state=cur_state, attitude=attitude,
                        last_interaction=ch_label,
                    ))
                result["relationships"].append(
                    RelationshipEntry(characters=chars, relation_type=rel_type,
                                      current_state=cur_state, attitude=attitude))
            self.fs.save_tracking_doc("character_relationships", rels.to_markdown())

        # ── 2. Items State ──
        item_delta = _extract_section(delta_section, "### 角色物品状态")
        if item_delta.strip():
            items_text = self.fs.load_tracking_doc("items_equipment")
            items = (ItemsEquipment.from_markdown(items_text)
                     if items_text else ItemsEquipment())
            # Parse: #### 获得 / #### 消耗 / #### 失去 with key=value format
            for cat, cat_label in [("#### 获得", "gained"),
                                    ("#### 消耗", "consumed"),
                                    ("#### 失去", "lost")]:
                cat_text = _extract_section(item_delta, cat)
                if not cat_text.strip():
                    continue
                for line in cat_text.strip().split("\n"):
                    stripped = line.strip()
                    if not stripped.startswith("- "):
                        continue
                    content = stripped[2:].strip()
                    # Format: 物品名: 持有者=XX, 来源=XX, 状态=XX [依据: ...]
                    if ":" not in content:
                        continue
                    item_name, rest = content.split(":", 1)
                    item_name = item_name.strip()
                    kv = self._parse_state_kv(rest)
                    owner = kv.get("持有者", kv.get("旧持有者", ""))
                    source = kv.get("来源", ch_label)
                    status = kv.get("状态", "")
                    reason = kv.get("原因", "")

                    if cat_label == "gained":
                        # Add or update protagonist item
                        found = False
                        for it in items.protagonist_items:
                            if it.name == item_name:
                                if owner:
                                    it.owner = owner
                                if status:
                                    it.status = status
                                it.source = source
                                found = True
                                break
                        if not found:
                            items.protagonist_items.append(ItemEntry(
                                name=item_name, owner=owner or "主角",
                                source=source, acquired_chapter=ch_label,
                                status=status or "可用"))
                    elif cat_label == "lost":
                        for it in items.protagonist_items:
                            if it.name == item_name:
                                it.status = "已失去"
                                it.notes = reason
                                break
                    # consumed: update status
                    elif cat_label == "consumed":
                        for it in items.protagonist_items:
                            if it.name == item_name:
                                it.status = "已消耗"
                                it.notes = reason
                                break
                result["items"].append(item_name)
            self.fs.save_tracking_doc("items_equipment", items.to_markdown())

        # ── 3. Cultivation State ──
        cult_delta = _extract_section(delta_section, "### 角色修炼状态")
        if cult_delta.strip():
            cult_text = self.fs.load_tracking_doc("cultivation_system")
            cult = (CultivationSystem.from_markdown(cult_text)
                    if cult_text else CultivationSystem())
            for line in cult_delta.strip().split("\n"):
                stripped = line.strip()
                if not stripped.startswith("- "):
                    continue
                content = stripped[2:].strip()
                if ":" not in content:
                    continue
                name, rest = content.split(":", 1)
                name = name.strip()
                kv = self._parse_state_kv(rest)
                stage = kv.get("当前境界", "")
                ability = kv.get("特殊能力", "")
                limit = kv.get("限制", "")

                found = False
                for cs in cult.character_states:
                    if cs.name == name:
                        if stage:
                            cs.current_stage = stage
                        if ability:
                            cs.special_ability = ability
                        if limit:
                            cs.limitation = limit
                        cs.updated_chapter = ch_label
                        found = True
                        break
                if not found:
                    cult.character_states.append(CharacterCultivation(
                        name=name, current_stage=stage,
                        special_ability=ability, limitation=limit,
                        updated_chapter=ch_label))
                result["cultivation"].append(name)
            self.fs.save_tracking_doc("cultivation_system", cult.to_markdown())

        # ── 4. Foreshadowing State ──
        foreshadow_delta = _extract_section(delta_section, "### 伏笔状态")
        if foreshadow_delta.strip():
            for line in foreshadow_delta.strip().split("\n"):
                stripped = line.strip()
                if not stripped.startswith("- "):
                    continue
                content = stripped[2:].strip()
                if ":" not in content:
                    continue
                desc, rest = content.split(":", 1)
                desc = desc.strip()
                kv = self._parse_state_kv(rest)
                new_status = kv.get("状态", "")
                resolve_ch = kv.get("回收章节", "")
                if new_status in ("OPEN", "RESOLVED", "ABANDONED", "pending"):
                    try:
                        self.sqlite.upsert_foreshadow(
                            self.novel_id, desc, new_status, resolve_ch)
                    except Exception:
                        pass

        return result

    # ── Change Log Append (Existing behavior preserved) ────

    def _append_change_logs(self, chapter_index: int,
                            analysis_text: str) -> dict:
        """E06: Append to change logs from 「## 追踪文档变更建议」(historical audit).

        This is the legacy behavior — preserved for historical tracking.
        Current State is handled by _apply_state_deltas above.
        """
        ch_label = f"第{chapter_index}章"
        result = {"rels": False, "items": False, "cult": False}

        # 1. Character Relationships
        rel_changes = self._extract_relationship_changes(analysis_text, chapter_index)
        if rel_changes:
            rels_text = self.fs.load_tracking_doc("character_relationships")
            rels = (CharacterRelationships.from_markdown(rels_text)
                    if rels_text else CharacterRelationships())
            rels.change_log.extend(rel_changes)
            self.fs.save_tracking_doc("character_relationships", rels.to_markdown())
            result["rels"] = True

        # 2. Items Equipment
        item_changes = self._extract_item_changes(analysis_text, chapter_index)
        if item_changes:
            items_text = self.fs.load_tracking_doc("items_equipment")
            items = (ItemsEquipment.from_markdown(items_text)
                     if items_text else ItemsEquipment())
            items.item_logs.append(ItemLog(
                chapter=ch_label,
                gained=item_changes.get("gained", []),
                consumed=item_changes.get("consumed", []),
                lost=item_changes.get("lost", []),
            ))
            self.fs.save_tracking_doc("items_equipment", items.to_markdown())
            result["items"] = True

        # 3. Cultivation System
        cult_changes = self._extract_cultivation_changes(analysis_text, chapter_index)
        if cult_changes:
            cult_text = self.fs.load_tracking_doc("cultivation_system")
            cult = (CultivationSystem.from_markdown(cult_text)
                    if cult_text else CultivationSystem())
            if cult.rule_changes:
                cult.rule_changes += f"\n### {ch_label}\n{cult_changes}"
            else:
                cult.rule_changes = f"### {ch_label}\n{cult_changes}"
            self.fs.save_tracking_doc("cultivation_system", cult.to_markdown())
            result["cult"] = True

        return result

    # ── Legacy Extractors (unchanged behavior) ─────────────

    def _extract_relationship_changes(self, analysis_text: str,
                                      chapter_index: int) -> list:
        """从分析文本中提取角色关系变更（审计日志用）。"""
        changes = []
        section = ""
        for header in ["### 角色关系", "## 追踪文档变更建议", "### 角色关系图"]:
            h_level = header.count("#")
            pattern = rf'{re.escape(header)}\s*\n(.*?)(?=^#{{1,{h_level}}}\s|\Z)'
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
        """从分析文本中提取物品变更（审计日志用）。"""
        result = {"gained": [], "consumed": [], "lost": []}
        section = ""
        for header in ["### 物品装备", "### 物品/装备追踪"]:
            h_level = header.count("#")
            pattern = rf'{re.escape(header)}\s*\n(.*?)(?=^#{{1,{h_level}}}\s|\Z)'
            m = re.search(pattern, analysis_text, re.DOTALL)
            if m:
                section = m.group(1)
                break
        current_type: Optional[str] = None
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
        """从分析文本中提取修炼体系变更（审计日志用）。"""
        for header in ["### 修炼体系", "### 修炼/力量体系现状"]:
            h_level = header.count("#")
            pattern = rf'{re.escape(header)}\s*\n(.*?)(?=^#{{1,{h_level}}}\s|\Z)'
            m = re.search(pattern, analysis_text, re.DOTALL)
            if m:
                return m.group(1).strip()
        return ""

    def _sync_sqlite(self, chapter_index: int, chapter_text: str,
                     analysis_text: str):
        """将提取的角色状态和伏笔同步到 SQLite 缓存。"""
        novel_id = self.novel_id
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
                    novel_id, str(chapter_index), name,
                    {"last_seen": f"第{chapter_index}章"}
                )
            except Exception:
                pass
