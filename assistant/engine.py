"""
ConsistencyEngine — 轻量一致性辅助引擎。

网页版 Claude 完成主体创作，本引擎负责:
- 初始化模板（book_plan/volume_plan/world_setting/tracking docs）
- 一致性扫描（章正文 vs 追踪文档）
- 事实提取 & 追踪文档更新
- 风格检测
- 状态查看
"""

import sys
from pathlib import Path

# 复用现有项目的存储层
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.storage.file_store import FileStore
from src.storage.document_formats import (
    BookPlan, VolumePlan, ChapterPlan, ContextPackage, SceneSpec,
    CharacterRelationships, ItemsEquipment, CultivationSystem, FactDigest,
    RelationshipChange, ItemLog, ItemEntry, CharacterCultivation,
)
from src.agents.author.style_checker import StyleChecker
from src.config.settings import get_settings
from openai import OpenAI


class ConsistencyEngine:
    """网页 Claude 创作的辅助引擎"""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.settings = settings
        self.fs = FileStore(novel_id, settings.data_dir)
        self.prompts_dir = Path(__file__).parent / "prompts"

        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
        )
        self.model = settings.model_names.get("pro", "deepseek-v4-pro")

    # ═══ Init: 生成初始模板 ═══════════════════════════════

    def init_all(self, premise: str = "") -> dict:
        """一次性生成全部初始模板。"""
        result = {}
        print("生成初始模板...")

        result["world_setting"] = self._init_world_setting(premise)
        result["book_plan"] = self._init_book_plan(premise, result["world_setting"])
        result["volume_plan"] = self._init_volume_plan(premise, result["book_plan"])
        result["rels"] = self._init_tracking_doc("character_relationships", result["world_setting"])
        result["items"] = self._init_tracking_doc("items_equipment", "")
        result["cult"] = self._init_tracking_doc("cultivation_system", result["world_setting"])

        print("全部模板已生成。")
        print("  编辑 settings/world_setting_edited.md（如需调整设定）")
        print("  编辑 tracking/book_plan.md（调整全书框架）")
        print("  编辑 tracking/volume_plan.md（细化当前卷的事件链）")
        print("  然后将第1章正文粘贴到 chapters/chapter_0001_draft.md")
        print("  运行: python assistant/main.py check <小说名> --chapter 1")
        print("        python assistant/main.py sync <小说名> --chapter 1")
        return result

    def _init_world_setting(self, premise: str) -> str:
        prompt = self._load_prompt("init_world.txt")
        user = f"故事前提:\n{premise}\n\n请生成完整世界观设定。"
        return self._call_llm(prompt, user, "settings", "world_setting")

    def _init_book_plan(self, premise: str, world_setting: str) -> str:
        prompt = self._load_prompt("init_book.txt")
        user = f"故事前提:\n{premise}\n\n世界观设定:\n{world_setting[:3000]}"
        return self._call_llm(prompt, user, "tracking", "book_plan")

    def _init_volume_plan(self, premise: str, book_plan: str) -> str:
        prompt = self._load_prompt("init_volume.txt")
        user = f"故事前提:\n{premise}\n\n全书规划:\n{book_plan[:3000]}"
        return self._call_llm(prompt, user, "tracking", "volume_plan")

    def _init_tracking_doc(self, name: str, source: str) -> str:
        doc_map = {
            "character_relationships": CharacterRelationships().to_markdown(),
            "items_equipment": ItemsEquipment().to_markdown(),
            "cultivation_system": CultivationSystem().to_markdown(),
        }
        content = doc_map.get(name, "")
        self.fs.save_tracking_doc(name, content)
        return content

    # ═══ Check: 一致性扫描 ═══════════════════════════════

    def check_chapter(self, chapter_index: int, chapter_text: str = "") -> dict:
        """扫描章正文与追踪文档的一致性。"""
        if not chapter_text:
            chapter_text = self._load_chapter_text(chapter_index)
        if not chapter_text:
            return {"error": f"第{chapter_index}章正文不存在"}

        # 加载追踪文档
        rels = self.fs.load_tracking_doc("character_relationships") or ""
        items = self.fs.load_tracking_doc("items_equipment") or ""
        cult = self.fs.load_tracking_doc("cultivation_system") or ""
        world = self.fs.load_canonical("settings", "world_setting") or ""
        vol = self.fs.load_tracking_doc("volume_plan") or ""

        prompt = self._load_prompt("check.txt")
        user = f"""## 第 {chapter_index} 章正文

{chapter_text}

---

## 追踪文档（权威数据）

### 世界观设定
{world[:2000]}

### 角色关系
{rels[:2000]}

### 物品装备
{items[:1500]}

### 修炼体系
{cult[:1500]}

### 卷规划
{vol[:1500]}

---
请扫描以下维度：
1. 角色名/地名/设定名是否与追踪文档一致
2. 时间线是否与卷规划衔接
3. 物品状态是否与物品追踪文档一致
4. 角色关系描写是否与关系文档一致
5. 是否存在事实矛盾（死去的角色再出场、已消耗的物品再出现等）"""

        result = self._call_llm(prompt, user, "states",
                                f"consistency_check_ch{chapter_index:04d}")
        return {"raw": result}

    # ═══ Sync: 事实提取 & 追踪更新 ════════════════════════

    def sync_chapter(self, chapter_index: int, chapter_text: str = "") -> dict:
        """从章正文提取事实，更新全部追踪文档。"""
        if not chapter_text:
            chapter_text = self._load_chapter_text(chapter_index)
        if not chapter_text:
            return {"error": f"第{chapter_index}章正文不存在"}

        changes = {}
        prompt = self._load_prompt("sync.txt")
        user = f"""## 第 {chapter_index} 章正文

{chapter_text}

---

## 当前追踪文档

### 角色关系
{self.fs.load_tracking_doc('character_relationships') or '暂无'}

### 物品装备
{self.fs.load_tracking_doc('items_equipment') or '暂无'}

### 修炼体系
{self.fs.load_tracking_doc('cultivation_system') or '暂无'}

### 卷规划
{self.fs.load_tracking_doc('volume_plan') or '暂无'}

---
请提取本章事实并按格式输出。"""

        result = self._call_llm(prompt, user, "states",
                                f"sync_ch{chapter_index:04d}")
        changes["raw_analysis"] = result

        # 更新追踪文档（基于 LLM 输出）
        self._update_from_sync(chapter_index, chapter_text, result, changes)

        # 生成事实摘要
        self._generate_fact_digest(chapter_index, chapter_text)
        changes["fact_digest"] = f"states/fact_digest_ch{chapter_index:04d}.md"

        # 更新卷规划完成标记
        self._mark_volume_complete(chapter_index)

        return changes

    def _update_from_sync(self, chapter_index: int, chapter_text: str,
                          analysis: str, changes: dict):
        """从 LLM 分析结果更新追踪文档。"""
        import re

        # 角色关系
        rels_text = self.fs.load_tracking_doc("character_relationships")
        rels = CharacterRelationships.from_markdown(rels_text) if rels_text else CharacterRelationships()

        rel_section = ""
        for hdr in ["### 角色关系变更", "## 角色关系变更"]:
            m = re.search(rf'{hdr}\s*\n(.*?)(?=##|\Z)', analysis, re.DOTALL)
            if m:
                rel_section = m.group(1)
                break

        for line in rel_section.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- ") and ":" in stripped:
                content = stripped[2:]
                chars, change = content.split(":", 1)
                rels.change_log.append(RelationshipChange(
                    chapter=f"第{chapter_index}章",
                    characters=chars.strip(),
                    change=change.strip(),
                ))

        if rel_section:
            self.fs.save_tracking_doc("character_relationships", rels.to_markdown())
            changes["character_relationships"] = "updated"

        # 物品装备
        items_text = self.fs.load_tracking_doc("items_equipment")
        items = ItemsEquipment.from_markdown(items_text) if items_text else ItemsEquipment()

        gained, consumed, lost = [], [], []
        for hdr in ["### 物品变更", "## 物品变更"]:
            item_section = ""
            m = re.search(rf'{hdr}\s*\n(.*?)(?=##|\Z)', analysis, re.DOTALL)
            if m:
                item_section = m.group(1)
                break

        current_type = None
        for line in item_section.split("\n"):
            stripped = line.strip()
            if "获得" in stripped:
                current_type = "gained"
            elif "消耗" in stripped:
                current_type = "consumed"
            elif "失去" in stripped:
                current_type = "lost"
            elif stripped.startswith("- ") and current_type:
                ({"gained": gained, "consumed": consumed, "lost": lost}
                 [current_type].append(stripped[2:]))

        if gained or consumed or lost:
            items.item_logs.append(ItemLog(
                chapter=f"第{chapter_index}章",
                gained=gained, consumed=consumed, lost=lost))
            self.fs.save_tracking_doc("items_equipment", items.to_markdown())
            changes["items_equipment"] = "updated"

    def _generate_fact_digest(self, chapter_index: int, chapter_text: str):
        prompt = self._load_prompt("sync.txt")
        user = f"""## 第 {chapter_index} 章正文

{chapter_text}

---
请只输出「事实摘要」部分（确定的物品/角色状态/事件/数字/未出现内容/待解悬念）。"""

        result = self._call_llm(prompt, user, "states",
                                f"fact_digest_ch{chapter_index:04d}")

    def _mark_volume_complete(self, chapter_index: int):
        vp_text = self.fs.load_tracking_doc("volume_plan")
        if vp_text:
            vp_text += f"\n\n### 第{chapter_index}章 [已完成]\n- **实际写了**: 待人工补充\n"
            self.fs.save_tracking_doc("volume_plan", vp_text)

    # ═══ Style: 风格检测 ══════════════════════════════

    def check_style(self, chapter_index: int, chapter_text: str = "") -> dict:
        """运行 StyleChecker 检测 AI 高频句式。"""
        if not chapter_text:
            chapter_text = self._load_chapter_text(chapter_index)
        if not chapter_text:
            return {"error": f"第{chapter_index}章正文不存在"}

        report = StyleChecker(chapter_text).check_all(file_path=f"第{chapter_index}章")
        return {
            "errors": report.errors,
            "warnings": report.warnings,
            "infos": report.infos,
            "summary": report.summary(),
            "annotated": report.annotate_text(chapter_text),
        }

    # ═══ Status ═══════════════════════════════════════

    def get_status(self) -> dict:
        s = {"novel": self.novel_id}
        s["has_world_setting"] = self.fs.has_canonical("settings", "world_setting")
        s["has_book_plan"] = self.fs.has_tracking_doc("book_plan")
        s["has_volume_plan"] = self.fs.has_tracking_doc("volume_plan")
        s["has_rels"] = self.fs.has_tracking_doc("character_relationships")
        s["has_items"] = self.fs.has_tracking_doc("items_equipment")
        s["has_cult"] = self.fs.has_tracking_doc("cultivation_system")

        chapters_dir = self.fs.root / "chapters"
        chapter_files = sorted(chapters_dir.glob("chapter_*.md"))
        # 去重：同一章只算一次
        seen = set()
        unique = []
        for f in chapter_files:
            num = f.stem.split("_")[1] if "_" in f.stem else "0"
            if num not in seen:
                seen.add(num)
                unique.append(f)
        s["draft_chapters"] = len(unique)
        s["chapter_list"] = [f.stem for f in chapter_files]

        return s

    def print_status(self):
        s = self.get_status()
        print(f"\n小说: {s['novel']}")
        print(f"世界观: {'Y' if s['has_world_setting'] else 'N'}  "
              f"全书规划: {'Y' if s['has_book_plan'] else 'N'}  "
              f"卷规划: {'Y' if s['has_volume_plan'] else 'N'}")
        print(f"追踪文档: 角色关系{'Y' if s['has_rels'] else 'N'}  "
              f"物品{'Y' if s['has_items'] else 'N'}  "
              f"修炼{'Y' if s['has_cult'] else 'N'}")
        print(f"草稿章节: {s['draft_chapters']}")
        for ch in s['chapter_list']:
            print(f"  {ch}")

    # ═══ Snapshot: 存档快照 ════════════════════════════

    def snapshot(self):
        """为所有关键文档创建 .bak 快照，供 detect 对比。

        _edited.md 优先——因为 load_canonical 也优先读它。
        快照对象与检测对象保持一致。
        """
        files = [
            ("settings", "world_setting"),
            ("tracking", "book_plan"),
            ("tracking", "volume_plan"),
            ("tracking", "character_relationships"),
            ("tracking", "items_equipment"),
            ("tracking", "cultivation_system"),
        ]
        created = []
        for cat, name in files:
            # _edited.md 优先（与 load_canonical 逻辑一致）
            src = self.fs.root / cat / f"{name}_edited.md"
            if not src.exists():
                src = self.fs.root / cat / f"{name}.md"
            if src.exists():
                bak = self.fs.root / cat / f"{name}.bak.md"
                bak.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                created.append(f"{src.name} -> {cat}/{name}.bak.md")
        return created

    # ═══ Detect: 设定变更检测 ══════════════════════════

    def detect_changes(self) -> dict:
        """检测 world_setting / book_plan / volume_plan 是否有未同步的修改。

        对比 canonical .md 和 .bak 备份，找出变更的实体，
        扫描所有章节和追踪文档中的引用位置。
        """
        result = {"world_setting": None, "book_plan": None, "volume_plan": None}

        for name, category in [("world_setting", "settings"),
                                ("book_plan", "tracking"),
                                ("volume_plan", "tracking")]:
            current = self.fs.load_canonical(category, name)
            backup = None
            bak_path = self.fs.root / category / f"{name}.bak.md"
            if bak_path.exists():
                backup = bak_path.read_text(encoding="utf-8")

            if not current:
                continue
            if not backup or current == backup:
                continue

            changes = self._analyze_changes(name, backup, current)
            if changes:
                affected = self._find_affected_files(changes, name)
                result[name] = {
                    "entity_count": len(changes),
                    "changes": changes[:20],
                    "affected_files": affected,
                    "summary": self._format_change_report(name, changes, affected),
                }

        return result

    def _analyze_changes(self, doc_name: str, old_text: str,
                         new_text: str) -> list[dict]:
        """对比新旧文本，提取命名实体的增/删/改。"""
        prompt = f"""你是文本变更检测器。对比以下两份文档的差异，列出所有被修改的命名实体（角色名/地名/势力名/境界名/物品名/规则）。

## 旧版本
{old_text[:4000]}

## 新版本
{new_text[:4000]}

---
请列出所有变更。每行一条:
- **[新增] 实体名**: 描述
- **[修改] 实体名**: 旧描述 → 新描述
- **[删除] 实体名**: 旧描述"""

        response = self.client.chat.completions.create(
            model=self.model, temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        changes = []
        for line in response.choices[0].message.content.split("\n"):
            line = line.strip()
            if line.startswith("- **[新增]") or line.startswith("- **[修改]") or line.startswith("- **[删除]"):
                tag = "added" if "新增" in line[:10] else ("modified" if "修改" in line[:10] else "removed")
                content = line.split("]", 1)[1].strip() if "]" in line else line
                changes.append({"type": tag, "description": content})
        return changes

    def _find_affected_files(self, changes: list[dict],
                             source_doc: str) -> list[str]:
        """扫描章节和追踪文档，找出引用过变更实体的文件。"""
        affected = []
        entity_names = set()
        for c in changes:
            desc = c.get("description", "")
            name = desc.split(":")[0].split("：")[0].strip()
            if 2 <= len(name) <= 30:
                entity_names.add(name)

        # 扫描章节
        chapters_dir = self.fs.root / "chapters"
        for f in sorted(chapters_dir.glob("chapter_*.md")):
            text = f.read_text(encoding="utf-8")
            for name in entity_names:
                if name in text:
                    affected.append(str(f.relative_to(self.fs.root)))
                    break

        # 扫描追踪文档（排除自己）
        for doc in ["character_relationships", "items_equipment", "cultivation_system"]:
            doc_text = self.fs.load_tracking_doc(doc) or ""
            for name in entity_names:
                if name in doc_text:
                    affected.append(f"tracking/{doc}.md")
                    break

        return affected

    def _format_change_report(self, name: str, changes: list[dict],
                              affected: list[str]) -> str:
        doc_names = {"world_setting": "世界观设定", "book_plan": "全书规划",
                      "volume_plan": "卷规划"}
        lines = [f"\n  === {doc_names.get(name, name)} 变更检测 ==="]
        lines.append(f"  实体变更: {len(changes)} 处")
        for c in changes[:15]:
            tag = {"added": "+", "modified": "~", "removed": "-"}[c["type"]]
            lines.append(f"    [{tag}] {c['description'][:100]}")
        if affected:
            lines.append(f"  受影响文件 ({len(affected)}):")
            for a in affected[:20]:
                lines.append(f"    -> {a}")
        else:
            lines.append("  无受影响的文件。")
        return "\n".join(lines)

    # ═══ Helpers ══════════════════════════════════════

    def _load_prompt(self, filename: str) -> str:
        path = self.prompts_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _call_llm(self, system_prompt: str, user_msg: str,
                  save_cat: str, save_prefix: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
        )
        result = response.choices[0].message.content
        if save_cat:
            self.fs.save(save_cat, save_prefix, result)
        return result

    def _load_chapter_text(self, chapter_index: int) -> str:
        return (self.fs.load_latest("chapters", f"chapter_{chapter_index:04d}_draft")
                or self.fs.load_latest("chapters", f"chapter_{chapter_index:04d}")
                or "")
