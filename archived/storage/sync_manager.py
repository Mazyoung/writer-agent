"""SyncManager — 确定性存储同步引擎。

职责：
1. 解析 world_setting.md 中的实体
2. 检测 world_setting.md 的变更
3. 扫描 plot_structure.md / scene_plan / SQLite / ChromaDB 中受影响的引用
4. 生成同步计划并执行，确保整个存储链路一致
"""

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EntityChange:
    """单个实体的变更记录"""
    entity_type: str          # cultivation / region / faction / character / rule
    entity_name: str
    action: str               # added / modified / removed
    old_description: str = ""
    new_description: str = ""


@dataclass
class AffectedFile:
    """受影响的文件及其变更需求"""
    file_path: str            # 相对于 novel_dir 的路径
    category: str             # plot_structure / scene_plan / sqlite / chroma
    affected_sections: list[str] = field(default_factory=list)
    suggested_changes: list[str] = field(default_factory=list)


@dataclass
class ChangeReport:
    """完整的变更报告"""
    changes: list[EntityChange] = field(default_factory=list)
    summary: str = ""


@dataclass
class PropagationPlan:
    """同步计划"""
    change_report: ChangeReport
    affected_files: list[AffectedFile] = field(default_factory=list)
    world_setting: str = ""          # 新 world_setting 全文
    plot_structure: str = ""         # 更新后的 plot_structure（如需要）
    sqlite_actions: list[dict] = field(default_factory=list)
    chroma_actions: list[dict] = field(default_factory=list)


class SyncManager:
    """确定性存储同步引擎"""

    # world_setting.md 节头 → 实体类型映射
    SECTION_ENTITY_MAP = {
        "力量/修炼体系": "cultivation",
        "修炼体系": "cultivation",
        "力量体系": "cultivation",
        "地理与区域": "region",
        "势力格局": "faction",
        "种族/职业体系": "faction",  # 职业分化归入势力类
        "角色档案": "character",
        "核心禁忌": "rule",
        "世界铁律": "rule",
    }

    def __init__(self, file_store, sqlite_store=None, chroma_store=None):
        self.file_store = file_store
        self.sqlite = sqlite_store
        self.chroma = chroma_store

    # ─── 实体解析 ───

    def parse_entities(self, world_setting_text: str) -> dict[str, list[dict]]:
        """从 world_setting.md 提取所有命名实体。
        返回: {entity_type: [{name, description, section}, ...]}"""
        if not world_setting_text:
            return {}

        entities: dict[str, list[dict]] = {
            "cultivation": [], "region": [], "faction": [],
            "character": [], "rule": [],
        }

        # 分节解析
        sections = self._split_sections(world_setting_text)
        for section_header, section_body in sections:
            etype = self._classify_section(section_header)
            if etype is None:
                continue
            extracted = self._extract_entities_from_section(section_body, etype)
            for e in extracted:
                e["section"] = section_header.strip()
            entities[etype].extend(extracted)

        return entities

    def _split_sections(self, text: str) -> list[tuple[str, str]]:
        """将文本按 ## 和 ### 标题分割为节"""
        sections = []
        # 匹配 ## / ### 级别标题
        pattern = r'^(#{2,3}\s+.+)$'
        lines = text.split("\n")
        current_header = "(文档开头)"
        current_body: list[str] = []

        for line in lines:
            m = re.match(pattern, line)
            if m:
                if current_body:
                    body = "\n".join(current_body).strip()
                    if body:
                        sections.append((current_header, body))
                current_header = m.group(1).strip()
                current_body = []
            else:
                current_body.append(line)

        if current_body:
            body = "\n".join(current_body).strip()
            if body:
                sections.append((current_header, body))

        return sections

    def _classify_section(self, header: str) -> Optional[str]:
        """根据节头判断实体类型"""
        header_clean = re.sub(r'^#+\s*', '', header)
        # 去掉编号前缀如 "1. " "一、" "## "
        header_clean = re.sub(r'^[一二三四五六七八九十\d]+[、.．]\s*', '', header_clean)
        header_clean = re.sub(r'^\d+\.\s*', '', header_clean)

        for keyword, etype in self.SECTION_ENTITY_MAP.items():
            if keyword in header_clean:
                return etype
        return None

    def _extract_entities_from_section(self, section_body: str,
                                        etype: str) -> list[dict]:
        """从节内容中提取命名实体"""
        entities = []

        if etype == "character":
            entities = self._parse_character_section(section_body)
        elif etype == "cultivation":
            entities = self._parse_cultivation_section(section_body)
        else:
            entities = self._parse_general_section(section_body)

        return entities

    def _parse_character_section(self, body: str) -> list[dict]:
        """解析角色档案节 —— 每个角色以 '## 角色名' 或 '- **角色名**' 开头"""
        entities = []
        # 匹配 "## 角色名" 或 "### 角色名"
        char_blocks = re.split(r'\n(?=##\s+(?!.*(?:一|二|三|四|五|六|七|八|九|十|持续|后续|角色)))', body)
        # 也匹配 "- **角色名**"
        for block in char_blocks:
            # 提取角色名
            name_match = re.match(r'^#+\s*(.+)', block.strip())
            if not name_match:
                name_match = re.match(r'-\s*\*\*(.+?)\*\*', block.strip())
            if not name_match:
                continue
            name = name_match.group(1).strip()
            name = re.sub(r'\s*[（(].*?[）)]', '', name)  # 去掉括号注释
            if len(name) < 2 or len(name) > 20:
                continue
            entities.append({"name": name, "description": block.strip()[:300]})
        return entities

    def _parse_cultivation_section(self, body: str) -> list[dict]:
        """解析修炼体系节 —— 每个体系以 '**名称**' 或独立段落出现"""
        entities = []
        # 匹配粗体命名的体系
        pattern = r'\*\*(.+?)\*\*[：:]\s*(.+?)(?=\n\*\*|\n\n\*\*|\Z)'
        for m in re.finditer(pattern, body, re.DOTALL):
            name = m.group(1).strip()
            desc = m.group(2).strip()[:200]
            if len(name) >= 2:
                entities.append({"name": name, "description": desc})
        # 也匹配列表项格式
        if not entities:
            for line in body.split("\n"):
                m = re.match(r'-\s*\*\*(.+?)\*\*[：:]\s*(.+)', line)
                if m:
                    entities.append({"name": m.group(1).strip(),
                                     "description": m.group(2).strip()[:200]})
        return entities

    def _parse_general_section(self, body: str) -> list[dict]:
        """通用实体解析 —— 匹配 **名称**：描述 或 - **名称**：描述"""
        entities = []
        # 格式1: **名称**：描述
        for m in re.finditer(r'\*\*(.+?)\*\*[：:]\s*(.+?)(?=\n\*\*|\n-|\n\n\*\*|\Z)', body, re.DOTALL):
            name = m.group(1).strip()
            desc = m.group(2).strip()[:250]
            if 2 <= len(name) <= 30:
                entities.append({"name": name, "description": desc})
        # 格式2: - **名称**：描述 或 - 名称：描述
        for line in body.split("\n"):
            m = re.match(r'-\s*(?:\*\*)?(.+?)(?:\*\*)?[：:]\s*(.+)', line)
            if m:
                name = m.group(1).strip()
                if 2 <= len(name) <= 30:
                    entities.append({"name": name, "description": m.group(2).strip()[:200]})
        return entities

    # ─── 变更检测 ───

    def detect_changes(self, old_ws: str, new_ws: str) -> ChangeReport:
        """对比旧/新 world_setting，检测所有实体变更"""
        old_entities = self.parse_entities(old_ws)
        new_entities = self.parse_entities(new_ws)

        changes = []
        all_types = set(list(old_entities.keys()) + list(new_entities.keys()))

        for etype in all_types:
            old_map = {e["name"]: e for e in old_entities.get(etype, [])}
            new_map = {e["name"]: e for e in new_entities.get(etype, [])}

            old_names = set(old_map.keys())
            new_names = set(new_map.keys())

            # 新增
            for name in new_names - old_names:
                changes.append(EntityChange(
                    entity_type=etype,
                    entity_name=name,
                    action="added",
                    new_description=new_map[name].get("description", ""),
                ))

            # 删除
            for name in old_names - new_names:
                changes.append(EntityChange(
                    entity_type=etype,
                    entity_name=name,
                    action="removed",
                    old_description=old_map[name].get("description", ""),
                ))

            # 修改（描述变化超过 10%）
            for name in old_names & new_names:
                old_desc = old_map[name].get("description", "")
                new_desc = new_map[name].get("description", "")
                if self._descriptions_differ(old_desc, new_desc):
                    changes.append(EntityChange(
                        entity_type=etype,
                        entity_name=name,
                        action="modified",
                        old_description=old_desc,
                        new_description=new_desc,
                    ))

        summary = self._summarize_changes(changes)
        return ChangeReport(changes=changes, summary=summary)

    def _descriptions_differ(self, old: str, new: str) -> bool:
        """判断两段描述是否有实质差异"""
        if not old and not new:
            return False
        if not old or not new:
            return True
        # 简单相似度：差异超过 15% 即视为修改
        shorter = min(len(old), len(new))
        if shorter == 0:
            return True
        same = sum(1 for a, b in zip(old, new) if a == b)
        return (same / max(len(old), len(new))) < 0.85

    def _summarize_changes(self, changes: list[EntityChange]) -> str:
        if not changes:
            return "无实体变更"
        type_names = {
            "cultivation": "修炼体系", "region": "地域", "faction": "势力/组织",
            "character": "角色", "rule": "世界规则",
        }
        lines = []
        for c in changes:
            action_cn = {"added": "新增", "modified": "修改", "removed": "删除"}
            tn = type_names.get(c.entity_type, c.entity_type)
            lines.append(f"- [{action_cn.get(c.action, c.action)}] {tn}: {c.entity_name}")
        return "\n".join(lines)

    # ─── 引用扫描 ───

    def scan_affected_files(self, changes: ChangeReport,
                            plot_structure: str = "",
                            scene_plans: dict[int, str] = None) -> list[AffectedFile]:
        """扫描哪些文件包含变更实体的引用"""
        affected = []
        scene_plans = scene_plans or {}

        for change in changes:
            name = change.entity_name

            # 扫描 plot_structure.md
            if plot_structure and name in plot_structure:
                sections = self._find_reference_sections(plot_structure, name)
                if sections:
                    affected.append(AffectedFile(
                        file_path="outlines/plot_structure.md",
                        category="plot_structure",
                        affected_sections=sections,
                        suggested_changes=self._suggest_plot_structure_change(change),
                    ))

            # 扫描场景规划
            for ci, plan_text in scene_plans.items():
                if name in plan_text:
                    af = AffectedFile(
                        file_path=f"outlines/scene_plan_ch{ci:04d}.md",
                        category="scene_plan",
                        affected_sections=self._find_reference_sections(plan_text, name),
                    )
                    affected.append(af)

        # 标记 SQLite / ChromaDB 需要同步
        if changes.changes:
            affected.append(AffectedFile(
                file_path="state.db", category="sqlite",
                suggested_changes=["实体表需要更新"],
            ))
            affected.append(AffectedFile(
                file_path="chroma_db/", category="chroma",
                suggested_changes=["相关段落需重建向量索引"],
            ))

        return affected

    def _find_reference_sections(self, text: str, entity_name: str) -> list[str]:
        """在文本中查找引用某实体的节标题"""
        sections = []
        # 找到所有包含实体名的行，向上查找最近的标题
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if entity_name in line:
                # 向上找最近标题
                for j in range(i, -1, -1):
                    if re.match(r'^#{1,4}\s+', lines[j]):
                        header = lines[j].strip()
                        if header not in sections:
                            sections.append(header)
                        break
        return sections[:5]

    def _suggest_plot_structure_change(self, change: EntityChange) -> list[str]:
        """根据变更类型给出 plot_structure.md 的建议修改"""
        if change.action == "added":
            return [f"卷大纲中的事件若涉及 {change.entity_name}，需补充相关描述"]
        elif change.action == "modified":
            return [f"卷大纲中对 {change.entity_name} 的描述需与新设定对齐"]
        elif change.action == "removed":
            return [f"卷大纲中所有对 {change.entity_name} 的引用需删除或替换"]
        return []

    # ─── 同步执行 ───

    def execute_propagation(self, changes: ChangeReport,
                            plot_structure: str = "",
                            scene_plans: dict[int, str] = None) -> PropagationPlan:
        """生成完整的同步计划（不直接修改文件，返回计划供人工或 Agent 确认）"""
        scene_plans = scene_plans or {}
        affected = self.scan_affected_files(changes, plot_structure, scene_plans)

        plan = PropagationPlan(
            change_report=changes,
            affected_files=affected,
            plot_structure=plot_structure,
        )

        # SQLite 同步动作
        for change in changes.changes:
            if change.action == "added" and change.entity_type == "character":
                plan.sqlite_actions.append({
                    "table": "character_state",
                    "action": "upsert",
                    "name": change.entity_name,
                    "description": change.new_description,
                })
            elif change.action == "modified" and change.entity_type == "character":
                plan.sqlite_actions.append({
                    "table": "character_state",
                    "action": "update",
                    "name": change.entity_name,
                })

        # ChromaDB 同步动作
        if changes.changes:
            plan.chroma_actions.append({
                "action": "reindex",
                "reason": "world_setting 实体变更，相关向量索引需重建",
            })

        return plan

    def apply_sqlite_changes(self, plan: PropagationPlan, novel_id: str):
        """执行 SQLite 同步"""
        if not self.sqlite:
            return
        for action in plan.sqlite_actions:
            if action.get("action") in ("upsert", "update"):
                try:
                    self.sqlite.upsert_character_state(
                        novel_id,
                        f"sync_{action.get('name', 'unknown')}",
                        action["name"],
                        {"description": action.get("description", ""),
                         "source": "sync_manager"},
                    )
                except Exception:
                    pass

    def apply_chroma_changes(self, plan: PropagationPlan, novel_id: str):
        """执行 ChromaDB 同步 —— 重建 world_setting 索引"""
        if not self.chroma:
            return
        for action in plan.chroma_actions:
            if action.get("action") == "reindex":
                try:
                    self.chroma.mark_dirty(novel_id, "world_setting")
                except Exception:
                    pass

    # ─── 报告生成 ───

    def generate_report(self, plan: PropagationPlan) -> str:
        """生成人类可读的同步报告"""
        cr = plan.change_report
        lines = [
            "# 设定同步报告",
            "",
            "## 变更摘要",
            cr.summary if cr.summary else "无变更",
            "",
            f"## 受影响文件 ({len(plan.affected_files)} 个)",
        ]

        for af in plan.affected_files:
            lines.append(f"\n### {af.file_path} ({af.category})")
            if af.affected_sections:
                lines.append("涉及节：")
                for s in af.affected_sections:
                    lines.append(f"  - {s}")
            if af.suggested_changes:
                lines.append("建议修改：")
                for sc in af.suggested_changes:
                    lines.append(f"  - {sc}")

        if plan.sqlite_actions:
            lines.append(f"\n## SQLite 同步动作 ({len(plan.sqlite_actions)} 项)")
            for sa in plan.sqlite_actions:
                lines.append(f"  - [{sa.get('action')}] {sa.get('table')}: {sa.get('name', '')}")

        if plan.chroma_actions:
            lines.append(f"\n## ChromaDB 同步动作")
            for ca in plan.chroma_actions:
                lines.append(f"  - {ca.get('action')}: {ca.get('reason', '')}")

        return "\n".join(lines)
