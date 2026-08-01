"""
新系统文档格式定义 —— Markdown 可读文档的 dataclass + parse/generate。

每个文档类型都是: dataclass → from_markdown(text) → to_markdown()。
Markdown 是权威数据源。解析容忍格式偏差。生成产出规范格式。
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional


# ── 工具函数 ──────────────────────────────────────────────

def _extract_section(text: str, header: str) -> str:
    """提取 ## 或 ### 标题下的内容，止于同级或更高级标题（保留更深子节）。

    两个历史陷阱：
    1. f-string 中必须写 {{1,N}}，否则 {1,4} 被求值为元组，lookahead 永不命中；
    2. 停止级别必须按 header 自身的 # 数量计算——'## 事件链' 的内容以
       '### 事件1' 开头，若停止集包含 '###' 会立即返回空。
    """
    level = len(header) - len(header.lstrip('#'))
    level = max(level, 1)
    pattern = rf'^{re.escape(header)}\s*\n(.*?)(?=^#{{1,{level}}}\s|\Z)'
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _parse_key_value(text: str) -> dict[str, str]:
    """解析 **键**: 值 格式的行（容忍 '- ' 列表前缀）。"""
    result = {}
    for line in text.strip().split("\n"):
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        m = re.match(r'^\*\*(.+?)\*\*\s*[:：]\s*(.*)$', stripped)
        if m:
            result[m.group(1).strip()] = m.group(2).strip()
    return result


def _parse_table(text: str) -> list[dict[str, str]]:
    """解析 Markdown 表格为 dict 列表。"""
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return []
    headers = [h.strip() for h in lines[0].strip("|").split("|")]
    rows = []
    for line in lines[2:]:  # 跳过表头和分隔行
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
    return rows


def _parse_bullet_list(text: str) -> list[str]:
    """解析 - 开头的无序列表。"""
    items = []
    for line in text.strip().split("\n"):
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


# ── BookPlan ───────────────────────────────────────────────

@dataclass
class VolumeFramework:
    title: str = ""
    core_conflict: str = ""
    protagonist_arc: str = ""
    key_characters: str = ""
    estimated_chapters: str = ""


@dataclass
class GlobalForeshadow:
    description: str = ""
    planted_chapter: str = ""
    expected_resolve_volume: str = ""
    status: str = "pending"
    resolved_chapter: str = ""


@dataclass
class BookPlan:
    """Book Plan — 战略规划层（E03）。

    只保存长期有效的内容：核心目标/矛盾、结局方向、卷职责、
    主角长期成长、战略约束、全局伏笔。不保存章级执行细节。
    生命周期：初始化生成一次 → 长期稳定 → 仅 L3 Strategic Issue 可修改。
    """
    title: str = ""
    version: str = "v1"                # 规划版本，PlanRevision 以此追踪变更
    core_goal: str = ""                # 故事核心目标
    core_conflict: str = ""            # 核心矛盾
    protagonist_growth: str = ""       # 主角长期成长方向
    strategic_constraints: str = ""    # 不允许轻易破坏的战略约束
    premise: str = ""
    themes: list[str] = field(default_factory=list)
    ending_direction: str = ""
    volumes: list[VolumeFramework] = field(default_factory=list)
    global_foreshadows: list[GlobalForeshadow] = field(default_factory=list)

    @classmethod
    def from_markdown(cls, text: str) -> "BookPlan":
        bp = cls()
        raw_title = _extract_title(text)
        # 解包 '全书规划：《标题》'，避免 round-trip 嵌套
        m = re.search(r'全书规划\s*[：:]\s*《(.+?)》', raw_title)
        bp.title = m.group(1) if m else raw_title
        bp.version = _extract_version(text)
        bp.core_goal = _extract_section(text, "## 核心目标")
        bp.core_conflict = _extract_section(text, "## 核心矛盾")
        bp.protagonist_growth = _extract_section(text, "## 主角长期成长方向")
        bp.strategic_constraints = _extract_section(text, "## 战略约束")
        bp.premise = _extract_section(text, "## 核心梗概")
        bp.themes = _parse_bullet_list(_extract_section(text, "## 全书主题"))
        bp.ending_direction = _extract_section(text, "## 结局方向")

        vol_framework = _extract_section(text, "## 卷框架")
        for m in re.finditer(r'###\s+(.+?)\n(.*?)(?=###|\Z)', vol_framework, re.DOTALL):
            # 剥去 '第N卷：' 前缀，避免 to_markdown 重新添加后嵌套
            vf_title = re.sub(r'^第\s*\d+\s*卷\s*[：:]\s*', '', m.group(1).strip())
            vf = VolumeFramework(title=vf_title)
            kv = _parse_key_value(m.group(2))
            vf.core_conflict = kv.get("核心冲突", "")
            vf.protagonist_arc = kv.get("主角弧光", "")
            vf.key_characters = kv.get("关键角色", "")
            vf.estimated_chapters = kv.get("章数预估", "")
            bp.volumes.append(vf)

        for row in _parse_table(_extract_section(text, "## 全局伏笔追踪")):
            bp.global_foreshadows.append(GlobalForeshadow(
                description=row.get("伏笔描述", ""),
                planted_chapter=row.get("埋伏章节", ""),
                expected_resolve_volume=row.get("预计回收卷", ""),
                status=row.get("状态", "pending"),
                resolved_chapter=row.get("回收章节", ""),
            ))
        return bp

    def to_markdown(self) -> str:
        lines = [f"# 全书规划：《{self.title}》", ""]
        lines.append(f"- **版本**: {self.version}")
        lines.append("")
        lines.append("## 核心目标")
        lines.append(self.core_goal or "待填写")
        lines.append("")
        lines.append("## 核心矛盾")
        lines.append(self.core_conflict or "待填写")
        lines.append("")
        lines.append("## 主角长期成长方向")
        lines.append(self.protagonist_growth or "待填写")
        lines.append("")
        lines.append("## 战略约束")
        lines.append(self.strategic_constraints or "待填写")
        lines.append("")
        lines.append("## 核心梗概")
        lines.append(self.premise or "待填写")
        lines.append("")
        lines.append("## 全书主题")
        for t in self.themes:
            lines.append(f"- {t}")
        if not self.themes:
            lines.append("- 待填写")
        lines.append("")
        lines.append("## 结局方向")
        lines.append(self.ending_direction or "待填写")
        lines.append("")
        lines.append("## 卷框架")
        for i, v in enumerate(self.volumes, 1):
            lines.append(f"### 第{i}卷：{v.title}")
            lines.append(f"- **核心冲突**: {v.core_conflict}")
            lines.append(f"- **主角弧光**: {v.protagonist_arc}")
            lines.append(f"- **关键角色**: {v.key_characters}")
            lines.append(f"- **章数预估**: {v.estimated_chapters}")
            lines.append("")
        if not self.volumes:
            lines.append("待规划")
            lines.append("")
        lines.append("## 全局伏笔追踪")
        if self.global_foreshadows:
            lines.append("| 伏笔描述 | 埋伏章节 | 预计回收卷 | 状态 | 回收章节 |")
            lines.append("|---------|---------|-----------|------|---------|")
            for f in self.global_foreshadows:
                lines.append(f"| {f.description} | {f.planted_chapter} | {f.expected_resolve_volume} | {f.status} | {f.resolved_chapter} |")
        else:
            lines.append("暂无全局伏笔")
        return "\n".join(lines)


# ── VolumePlan ─────────────────────────────────────────────

@dataclass
class VolumeEvent:
    name: str = ""
    trigger: str = ""
    content: str = ""
    characters: str = ""
    emotion: str = ""
    result: str = ""
    transition: str = ""
    chapter: str = ""


@dataclass
class VolumeCharacter:
    name: str = ""
    current_state: str = ""
    arc: str = ""
    key_relations: str = ""
    items: str = ""


@dataclass
class VolumeForeshadow:
    description: str = ""
    planted_chapter: str = ""
    expected_resolve: str = ""
    status: str = "pending"


@dataclass
class ChapterSummary:
    chapter_index: int = 0
    title: str = ""
    actual_content: str = ""
    deviation: str = ""
    new_introduced: str = ""


@dataclass
class VolumePlan:
    """Volume Plan — 战术规划层（E03），Rolling Horizon。

    只描述当前卷：核心目标/冲突、章节范围、关键里程碑、
    必须发生的事件链、伏笔布置与回收、角色阶段性成长、节奏约束。
    不复制 Book Plan 内容。status 取值：PLANNED / ACTIVE / COMPLETED。
    """
    volume_number: int = 1
    version: str = "v1"                # 规划版本
    status: str = "ACTIVE"             # PLANNED / ACTIVE / COMPLETED
    chapter_range: str = ""            # 如 "第1章-第14章"
    title: str = ""
    core_conflict: str = ""
    character_goal: str = ""
    obstacle: str = ""
    milestones: list[str] = field(default_factory=list)      # 关键里程碑
    pacing_constraints: str = ""                              # 节奏与事件顺序约束
    events: list[VolumeEvent] = field(default_factory=list)
    characters: list[VolumeCharacter] = field(default_factory=list)
    foreshadows: list[VolumeForeshadow] = field(default_factory=list)
    completed_chapters: list[ChapterSummary] = field(default_factory=list)

    @classmethod
    def from_markdown(cls, text: str) -> "VolumePlan":
        vp = cls()
        raw_title = _extract_title(text)
        # 从标题行恢复卷号：'# 第N卷规划：...'（与 chapter_index 同类 round-trip 修复）
        m = re.search(r'第\s*(\d+)\s*卷', raw_title)
        if m:
            vp.volume_number = int(m.group(1))
        # 解包 '第N卷规划：《标题》'，避免 round-trip 嵌套
        m = re.search(r'第\s*\d+\s*卷规划\s*[：:]\s*《(.+?)》', raw_title)
        vp.title = m.group(1) if m else raw_title
        vp.version = _extract_version(text)
        m = re.search(r'\*\*状态\*\*\s*[:：]\s*(\S+)', text)
        if m:
            vp.status = m.group(1)
        m = re.search(r'\*\*章节范围\*\*\s*[:：]\s*(.+)', text)
        if m:
            vp.chapter_range = m.group(1).strip()

        overview = _extract_section(text, "## 卷概述")
        kv = _parse_key_value(overview)
        vp.core_conflict = kv.get("核心冲突", "")
        vp.character_goal = kv.get("角色目标", "")
        vp.obstacle = kv.get("障碍", "")

        vp.milestones = _parse_bullet_list(_extract_section(text, "## 关键里程碑"))
        vp.pacing_constraints = _extract_section(text, "## 节奏约束")

        events_text = _extract_section(text, "## 事件链")
        for m in re.finditer(r'###\s+(.+?)\n(.*?)(?=###|\Z)', events_text, re.DOTALL):
            # 剥去 '事件N：' 前缀，避免 to_markdown 重新添加后嵌套
            ev_name = re.sub(r'^事件\s*\d+\s*[：:]\s*', '', m.group(1).strip())
            ev = VolumeEvent(name=ev_name)
            ekv = _parse_key_value(m.group(2))
            ev.trigger = ekv.get("触发条件", "")
            ev.content = ekv.get("核心内容", "")
            ev.characters = ekv.get("涉及角色", "")
            ev.emotion = ekv.get("情感基调", "")
            ev.result = ekv.get("结果与影响", "")
            ev.transition = ekv.get("衔接", "")
            ev.chapter = ekv.get("对应章节", "")
            vp.events.append(ev)

        chars_text = _extract_section(text, "## 卷内角色档案")
        for m in re.finditer(r'###\s+(.+?)\n(.*?)(?=###|\Z)', chars_text, re.DOTALL):
            vc = VolumeCharacter(name=m.group(1).strip())
            ckv = _parse_key_value(m.group(2))
            vc.current_state = ckv.get("当前状态", "")
            vc.arc = ckv.get("本卷弧光", "")
            vc.key_relations = ckv.get("关键关系", "")
            vc.items = ckv.get("携带物品", "")
            vp.characters.append(vc)

        for row in _parse_table(_extract_section(text, "## 卷内伏笔表")):
            vp.foreshadows.append(VolumeForeshadow(
                description=row.get("伏笔描述", ""),
                planted_chapter=row.get("埋伏章节", ""),
                expected_resolve=row.get("预计回收位置", ""),
                status=row.get("状态", "pending"),
            ))

        completed = _extract_section(text, "## 已完成章节摘要")
        for m in re.finditer(r'###\s+(.+?)\[已完.*?\n(.*?)(?=###|\Z)', completed, re.DOTALL):
            cs = ChapterSummary()
            # 标题行格式 '第N章 标题'：拆出章号与标题，避免 round-trip 嵌套
            m2 = re.match(r'第\s*(\d+)\s*章\s*(.*)', m.group(1).strip())
            if m2:
                cs.chapter_index = int(m2.group(1))
                cs.title = m2.group(2).strip()
            else:
                cs.title = m.group(1).strip()
            ckv = _parse_key_value(m.group(2))
            cs.actual_content = ckv.get("实际写了", "")
            cs.deviation = ckv.get("偏离原计划", "")
            cs.new_introduced = ckv.get("新引入", "")
            vp.completed_chapters.append(cs)

        return vp

    def to_markdown(self) -> str:
        lines = [f"# 第{self.volume_number}卷规划：《{self.title}》", ""]
        lines.append(f"- **版本**: {self.version}")
        lines.append(f"- **状态**: {self.status}")
        lines.append(f"- **章节范围**: {self.chapter_range or '待定'}")
        lines.append("")
        lines.append("## 卷概述")
        lines.append(f"- **核心冲突**: {self.core_conflict}")
        lines.append(f"- **角色目标**: {self.character_goal}")
        lines.append(f"- **障碍**: {self.obstacle}")
        lines.append("")
        lines.append("## 关键里程碑")
        for ms in self.milestones:
            lines.append(f"- {ms}")
        if not self.milestones:
            lines.append("- 待规划")
        lines.append("")
        lines.append("## 事件链")
        for i, ev in enumerate(self.events, 1):
            lines.append(f"### 事件{i}：{ev.name}")
            lines.append(f"- **触发条件**: {ev.trigger}")
            lines.append(f"- **核心内容**: {ev.content}")
            lines.append(f"- **涉及角色**: {ev.characters}")
            lines.append(f"- **情感基调**: {ev.emotion}")
            lines.append(f"- **结果与影响**: {ev.result}")
            lines.append(f"- **衔接**: {ev.transition}")
            lines.append(f"- **对应章节**: {ev.chapter}")
            lines.append("")
        lines.append("## 卷内角色档案")
        for vc in self.characters:
            lines.append(f"### {vc.name}")
            lines.append(f"- **当前状态**: {vc.current_state}")
            lines.append(f"- **本卷弧光**: {vc.arc}")
            lines.append(f"- **关键关系**: {vc.key_relations}")
            lines.append(f"- **携带物品**: {vc.items}")
            lines.append("")
        lines.append("## 卷内伏笔表")
        if self.foreshadows:
            lines.append("| 伏笔描述 | 埋伏章节 | 预计回收位置 | 状态 |")
            lines.append("|---------|---------|------------|------|")
            for f in self.foreshadows:
                lines.append(f"| {f.description} | {f.planted_chapter} | {f.expected_resolve} | {f.status} |")
        lines.append("")
        lines.append("## 节奏约束")
        lines.append(self.pacing_constraints or "无特殊约束")
        lines.append("")
        lines.append("## 已完成章节摘要")
        for cs in self.completed_chapters:
            lines.append(f"### 第{cs.chapter_index}章 {cs.title} [已完成]")
            lines.append(f"- **实际写了**: {cs.actual_content}")
            lines.append(f"- **偏离原计划**: {cs.deviation}")
            lines.append(f"- **新引入**: {cs.new_introduced}")
            lines.append("")
        return "\n".join(lines)


# ── ChapterPlan ────────────────────────────────────────────

# build_writer_prompt 注入世界观的字符上限：
# 简单的头部截断方案——长度可预测、规则可解释，后续可替换为语义筛选。
WORLD_SETTING_PROMPT_LIMIT = 2500


@dataclass
class SceneSpec:
    """Part A: 单个场景的写作规格。"""
    scene_number: int = 0
    name: str = ""
    status: str = "待规划"
    what_happens: str = ""
    dramatic_function: str = ""
    dialogue_info_gain: str = ""
    character_micro_moment: str = ""
    characters_involved: str = ""
    emotion_curve: str = ""
    word_estimate: str = ""
    transition: str = ""


@dataclass
class ContextPackage:
    """Part B: 给写手的丰富上下文。"""
    character_relations: str = ""     # 角色关系描述
    items_tracking: str = ""           # 物品追踪表格
    cultivation_status: str = ""       # 修炼体系现状
    foreshadow_nodes: str = ""         # 本章伏笔节点
    emotion_palette: str = ""          # 情感调色板
    forbidden_list: str = ""           # 禁止清单


@dataclass
class ChapterPlan:
    chapter_index: int = 1
    title: str = ""
    chapter_outline: str = ""
    chapter_type: str = "延续型"
    total_scenes: int = 0
    context: ContextPackage = field(default_factory=ContextPackage)
    scenes: list[SceneSpec] = field(default_factory=list)

    @classmethod
    def from_markdown(cls, text: str) -> "ChapterPlan":
        cp = cls()
        cp.title = _extract_title(text)
        cp.chapter_index = _extract_chapter_index(text)

        info = _extract_section(text, "## 一、章节信息")
        if not info:
            info = _extract_section(text, "## 章节信息")
        kv = _parse_key_value(info)
        cp.chapter_outline = kv.get("章大纲", "")
        cp.chapter_type = kv.get("章节类型", "延续型")
        cp.total_scenes = int(kv.get("总场景数", 0))

        # Part B: 上下文包
        ctx_text = _extract_section(text, "## 二、写作上下文包")
        if not ctx_text:
            ctx_text = _extract_section(text, "## 写作上下文包")
        cp.context.character_relations = _extract_section(ctx_text, "### 角色关系图")
        cp.context.items_tracking = _extract_section(ctx_text, "### 物品/装备追踪")
        cp.context.cultivation_status = _extract_section(ctx_text, "### 修炼/力量体系现状")
        cp.context.foreshadow_nodes = _extract_section(ctx_text, "### 关键伏笔节点")
        cp.context.emotion_palette = _extract_section(ctx_text, "### 情感调色板")
        cp.context.forbidden_list = _extract_section(ctx_text, "### 禁止清单")

        # Part A: 场景计划
        scenes_text = _extract_section(text, "## 三、场景级写作计划")
        if not scenes_text:
            scenes_text = _extract_section(text, "## 场景级写作计划")
        for m in re.finditer(r'###\s*场景\s*(\d+)[：:]\s*(.+?)\[状态[：:]\s*(.+?)\].*?\n(.*?)(?=###\s*场景|\Z)',
                             scenes_text, re.DOTALL):
            ss = SceneSpec(
                scene_number=int(m.group(1)),
                name=m.group(2).strip(),
                status=m.group(3).strip(),
            )
            skv = _parse_key_value(m.group(4))
            ss.what_happens = skv.get("发生什么", "")
            ss.dramatic_function = skv.get("本场景的戏剧功能", skv.get("戏剧功能", ""))
            ss.dialogue_info_gain = skv.get("对话必须达成的信息增量", skv.get("信息增量", ""))
            ss.character_micro_moment = skv.get("角色微时刻", skv.get("微时刻", ""))
            ss.characters_involved = skv.get("涉及角色", "")
            ss.emotion_curve = skv.get("情绪曲线", "")
            ss.word_estimate = skv.get("字数预估", "")
            ss.transition = skv.get("与前后衔接", skv.get("前后衔接", ""))
            cp.scenes.append(ss)

        return cp

    def to_markdown(self) -> str:
        lines = [f"# 第{self.chapter_index}章规划：《{self.title}》", ""]
        lines.append("## 一、章节信息")
        lines.append(f"- **章大纲**: {self.chapter_outline}")
        lines.append(f"- **章节类型**: {self.chapter_type}")
        lines.append(f"- **总场景数**: {self.total_scenes or len(self.scenes)}")
        lines.append("")

        lines.append("## 二、写作上下文包")
        lines.append("")
        lines.append("### 角色关系图")
        lines.append(self.context.character_relations or "待生成")
        lines.append("")
        lines.append("### 物品/装备追踪")
        lines.append(self.context.items_tracking or "待生成")
        lines.append("")
        lines.append("### 修炼/力量体系现状")
        lines.append(self.context.cultivation_status or "暂无")
        lines.append("")
        lines.append("### 关键伏笔节点")
        lines.append(self.context.foreshadow_nodes or "暂无")
        lines.append("")
        lines.append("### 情感调色板")
        lines.append(self.context.emotion_palette or "待填写")
        lines.append("")
        lines.append("### 禁止清单")
        lines.append(self.context.forbidden_list or "暂无")
        lines.append("")

        lines.append("## 三、场景级写作计划")
        lines.append("")
        for ss in self.scenes:
            lines.append(f"### 场景 {ss.scene_number}：{ss.name} [状态：{ss.status}]")
            lines.append(f"- **发生什么**：{ss.what_happens}")
            lines.append(f"- **本场景的戏剧功能**：{ss.dramatic_function}")
            lines.append(f"- **对话必须达成的信息增量**：{ss.dialogue_info_gain}")
            lines.append(f"- **角色微时刻**：{ss.character_micro_moment}")
            lines.append(f"- **涉及角色**：{ss.characters_involved}")
            lines.append(f"- **情绪曲线**：{ss.emotion_curve}")
            lines.append(f"- **字数预估**：{ss.word_estimate}")
            lines.append(f"- **与前后衔接**：{ss.transition}")
            lines.append("")
        return "\n".join(lines)

    def build_writer_prompt(self, world_setting: str = "",
                            prev_chapter_end: str = "") -> str:
        """组装 DeepSeekWriter 的完整写作提示词。"""
        parts = []

        if world_setting:
            parts.append(
                "## 【世界观与硬规则】\n"
                + world_setting[:WORLD_SETTING_PROMPT_LIMIT]
                + "\n\n（以上为世界观的权威设定，属于高优先级约束："
                  "你不得与其中已有规则冲突。若本章规划与世界观发生硬冲突，"
                  "优先遵守世界观，不要自行创造新设定。）"
            )
        if self.context.character_relations:
            parts.append("## [必读] 角色关系图\n" + self.context.character_relations)
        if self.context.items_tracking:
            parts.append("## [必读] 物品/装备追踪\n" + self.context.items_tracking)
        if self.context.cultivation_status:
            parts.append("## [必读] 修炼/力量体系现状\n" + self.context.cultivation_status)
        if self.context.foreshadow_nodes:
            parts.append("## [必读] 关键伏笔节点\n" + self.context.foreshadow_nodes)
        if self.context.emotion_palette:
            parts.append("## [必读] 情感调色板\n" + self.context.emotion_palette)
        if self.context.forbidden_list:
            parts.append("## [禁止清单] 以下内容绝对不得出现\n" + self.context.forbidden_list)
        if prev_chapter_end:
            parts.append("## [必读] 上一章结尾（第一句话必须衔接此内容）\n" + prev_chapter_end[-500:])

        parts.append(f"## 第{self.chapter_index}章写作指令")
        parts.append(f"章大纲：{self.chapter_outline}")
        parts.append(f"章节类型：{self.chapter_type}")
        parts.append("")
        parts.append("以下是你必须实现的场景清单。严格遵守每个场景的「发生什么」字段。")
        parts.append("场景之间自然过渡，不要跨越场景边界。")
        parts.append("")
        for ss in self.scenes:
            parts.append(f"### 场景 {ss.scene_number}：{ss.name}")
            parts.append(f"发生什么：{ss.what_happens}")
            parts.append(f"戏剧功能：{ss.dramatic_function}")
            parts.append(f"信息增量：{ss.dialogue_info_gain}")
            parts.append(f"角色微时刻：{ss.character_micro_moment}")
            parts.append(f"涉及角色：{ss.characters_involved}")
            parts.append(f"情绪曲线：{ss.emotion_curve}")
            parts.append(f"字数预估：{ss.word_estimate}")
            parts.append("")

        return "\n".join(parts)


# ── CharacterRelationships ─────────────────────────────────

@dataclass
class RelationshipEntry:
    characters: str = ""        # "角色A ↔ 角色B"
    relation_type: str = ""     # 不信任/盟友/敌对/...
    current_state: str = ""
    attitude: str = ""
    last_interaction: str = ""


@dataclass
class RelationshipChange:
    chapter: str = ""
    characters: str = ""
    change: str = ""


@dataclass
class CharacterRelationships:
    entries: list[RelationshipEntry] = field(default_factory=list)
    change_log: list[RelationshipChange] = field(default_factory=list)

    @classmethod
    def from_markdown(cls, text: str) -> "CharacterRelationships":
        cr = cls()
        rel_section = _extract_section(text, "## 关系详情")
        if not rel_section:
            rel_section = text
        for m in re.finditer(r'####?\s*(.+?)\n(.*?)(?=####?\s|\Z)', rel_section, re.DOTALL):
            entry = RelationshipEntry(characters=m.group(1).strip())
            kv = _parse_key_value(m.group(2))
            entry.relation_type = kv.get("关系类型", "")
            entry.current_state = kv.get("当前状态", "")
            entry.attitude = kv.get("态度", "")
            entry.last_interaction = kv.get("上一章互动", "")
            cr.entries.append(entry)

        log_text = _extract_section(text, "## 关系变更日志")
        for m in re.finditer(r'###\s*(.+?)\n(.*?)(?=###|\Z)', log_text, re.DOTALL):
            for line in m.group(2).strip().split("\n"):
                stripped = line.strip()
                if stripped.startswith("- "):
                    parts = stripped[2:].split(":", 1)
                    if len(parts) == 2:
                        cr.change_log.append(RelationshipChange(
                            chapter=m.group(1).strip(),
                            characters=parts[0].strip(),
                            change=parts[1].strip(),
                        ))
        return cr

    def to_markdown(self) -> str:
        lines = ["# 角色关系图", ""]
        lines.append("## 关系详情")
        for e in self.entries:
            lines.append(f"#### {e.characters}")
            lines.append(f"- **关系类型**: {e.relation_type}")
            lines.append(f"- **当前状态**: {e.current_state}")
            lines.append(f"- **态度**: {e.attitude}")
            lines.append(f"- **上一章互动**: {e.last_interaction}")
            lines.append("")
        lines.append("## 关系变更日志")
        current_ch = ""
        for cl in self.change_log:
            if cl.chapter != current_ch:
                current_ch = cl.chapter
                lines.append(f"### {cl.chapter}")
            lines.append(f"- **{cl.characters}**: {cl.change}")
        if not self.change_log:
            lines.append("暂无变更记录")
        return "\n".join(lines)


# ── ItemsEquipment ─────────────────────────────────────────

@dataclass
class ItemEntry:
    name: str = ""
    owner: str = ""
    source: str = ""
    acquired_chapter: str = ""
    attributes: str = ""
    status: str = ""
    notes: str = ""


@dataclass
class ItemLog:
    chapter: str = ""
    gained: list[str] = field(default_factory=list)
    consumed: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)


@dataclass
class ItemsEquipment:
    protagonist_items: list[ItemEntry] = field(default_factory=list)
    world_items: list[ItemEntry] = field(default_factory=list)
    item_logs: list[ItemLog] = field(default_factory=list)

    @classmethod
    def from_markdown(cls, text: str) -> "ItemsEquipment":
        ie = cls()
        for row in _parse_table(_extract_section(text, "## 主角持有")):
            ie.protagonist_items.append(ItemEntry(
                name=row.get("物品", ""), owner="主角",
                source=row.get("来源", ""), acquired_chapter=row.get("获得章", ""),
                attributes=row.get("属性", ""), status=row.get("状态", ""),
                notes=row.get("备注", ""),
            ))
        for row in _parse_table(_extract_section(text, "## 已引入的世界物品")):
            ie.world_items.append(ItemEntry(
                name=row.get("物品", ""), owner=row.get("拥有者", ""),
                source=row.get("首次出现", ""), attributes=row.get("已知属性", ""),
            ))
        log_text = _extract_section(text, "## 物品流转日志")
        for m in re.finditer(r'###\s*(.+?)\n(.*?)(?=###|\Z)', log_text, re.DOTALL):
            il = ItemLog(chapter=m.group(1).strip())
            body = m.group(2)
            il.gained = _parse_bullet_list(_extract_section(body, "获得"))
            il.consumed = _parse_bullet_list(_extract_section(body, "消耗"))
            il.lost = _parse_bullet_list(_extract_section(body, "失去"))
            ie.item_logs.append(il)
        return ie

    def to_markdown(self) -> str:
        lines = ["# 物品与装备系统", ""]
        lines.append("## 主角持有")
        if self.protagonist_items:
            lines.append("| 物品 | 来源 | 获得章 | 属性 | 状态 | 备注 |")
            lines.append("|------|------|--------|------|------|------|")
            for it in self.protagonist_items:
                lines.append(f"| {it.name} | {it.source} | {it.acquired_chapter} | {it.attributes} | {it.status} | {it.notes} |")
        lines.append("")
        lines.append("## 已引入的世界物品")
        if self.world_items:
            lines.append("| 物品 | 拥有者 | 首次出现 | 已知属性 |")
            lines.append("|------|--------|---------|---------|")
            for it in self.world_items:
                lines.append(f"| {it.name} | {it.owner} | {it.source} | {it.attributes} |")
        lines.append("")
        lines.append("## 物品流转日志")
        for il in self.item_logs:
            lines.append(f"### {il.chapter}")
            if il.gained:
                lines.append("#### 获得")
                for g in il.gained:
                    lines.append(f"- {g}")
            if il.consumed:
                lines.append("#### 消耗")
                for c in il.consumed:
                    lines.append(f"- {c}")
            if il.lost:
                lines.append("#### 失去")
                for l in il.lost:
                    lines.append(f"- {l}")
            lines.append("")
        return "\n".join(lines)


# ── CultivationSystem ──────────────────────────────────────

@dataclass
class CultivationStage:
    level: int = 0
    name: str = ""
    core_change: str = ""
    breakthrough_condition: str = ""
    known_characters: str = ""
    first_revealed: str = ""


@dataclass
class CharacterCultivation:
    name: str = ""
    current_stage: str = ""
    distance_to_next: str = ""
    special_ability: str = ""
    limitation: str = ""
    updated_chapter: str = ""


@dataclass
class CultivationSystem:
    name: str = ""
    overview: str = ""
    stages: list[CultivationStage] = field(default_factory=list)
    character_states: list[CharacterCultivation] = field(default_factory=list)
    rule_changes: str = ""

    @classmethod
    def from_markdown(cls, text: str) -> "CultivationSystem":
        cs = cls()
        cs.name = _extract_title(text)
        cs.overview = _extract_section(text, "## 体系总览")

        stages_text = _extract_section(text, "## 境界详情")
        for m in re.finditer(r'####?\s*(.+?)\n(.*?)(?=####?\s|\Z)', stages_text, re.DOTALL):
            stage = CultivationStage(name=m.group(1).strip())
            kv = _parse_key_value(m.group(2))
            stage.core_change = kv.get("核心变化", "")
            stage.breakthrough_condition = kv.get("突破条件", "")
            stage.known_characters = kv.get("已知角色", "")
            stage.first_revealed = kv.get("首次揭示", "")
            cs.stages.append(stage)

        for row in _parse_table(_extract_section(text, "## 角色修炼状态")):
            cs.character_states.append(CharacterCultivation(
                name=row.get("角色", ""), current_stage=row.get("境界", ""),
                distance_to_next=row.get("距下一阶", ""),
                special_ability=row.get("特殊能力", ""),
                limitation=row.get("限制", ""),
                updated_chapter=row.get("更新章", ""),
            ))
        cs.rule_changes = _extract_section(text, "## 体系规则变更日志")
        return cs

    def to_markdown(self) -> str:
        lines = [f"# 修炼/力量体系：{self.name}", ""]
        lines.append("## 体系总览")
        lines.append(self.overview or "待填写")
        lines.append("")
        lines.append("## 境界详情")
        for s in self.stages:
            lines.append(f"#### {s.name}")
            lines.append(f"- **核心变化**: {s.core_change}")
            lines.append(f"- **突破条件**: {s.breakthrough_condition}")
            lines.append(f"- **已知角色**: {s.known_characters}")
            lines.append(f"- **首次揭示**: {s.first_revealed}")
            lines.append("")
        lines.append("## 角色修炼状态")
        if self.character_states:
            lines.append("| 角色 | 境界 | 距下一阶 | 特殊能力 | 限制 | 更新章 |")
            lines.append("|------|------|---------|---------|------|--------|")
            for cc in self.character_states:
                lines.append(f"| {cc.name} | {cc.current_stage} | {cc.distance_to_next} | {cc.special_ability} | {cc.limitation} | {cc.updated_chapter} |")
        lines.append("")
        lines.append("## 体系规则变更日志")
        lines.append(self.rule_changes or "暂无变更")
        return "\n".join(lines)


# ── FactDigest (现有格式，保持兼容) ─────────────────────────

@dataclass
class FactDigest:
    chapter_index: int = 0
    confirmed_items: str = ""
    confirmed_character_states: str = ""
    confirmed_events: str = ""
    confirmed_numbers: str = ""
    explicitly_absent: str = ""
    pending_suspense: str = ""

    @classmethod
    def from_markdown(cls, text: str) -> "FactDigest":
        fd = cls()
        fd.chapter_index = _extract_chapter_index(text)
        fd.confirmed_items = _extract_section(text, "### 确定的物品")
        fd.confirmed_character_states = _extract_section(text, "### 确定的角色状态")
        fd.confirmed_events = _extract_section(text, "### 确定的事件")
        fd.confirmed_numbers = _extract_section(text, "### 确定的数字/数据")
        fd.explicitly_absent = (
            _extract_section(text, "### 明确未出现的内容（后续章节不得引用）") or
            _extract_section(text, "### 明确未出现的内容")
        )
        fd.pending_suspense = _extract_section(text, "### 待解悬念")
        return fd

    def to_markdown(self) -> str:
        lines = [f"# 第{self.chapter_index}章 事实摘要", ""]
        for title, content in [
            ("确定的物品", self.confirmed_items),
            ("确定的角色状态", self.confirmed_character_states),
            ("确定的事件", self.confirmed_events),
            ("确定的数字/数据", self.confirmed_numbers),
            ("明确未出现的内容（后续章节不得引用）", self.explicitly_absent),
            ("待解悬念", self.pending_suspense),
        ]:
            lines.append(f"### {title}")
            lines.append(content or "暂无")
            lines.append("")
        return "\n".join(lines)


# ── 辅助函数 ──────────────────────────────────────────────

def _extract_title(text: str) -> str:
    """提取 # 标题中的小说名/章节名。"""
    first = text.strip().split("\n")[0]
    return first.lstrip("#").strip()


def _extract_chapter_index(text: str) -> int:
    """从标题行恢复真实章号：'# 第N章规划：《...》'。

    与 ChapterPlan.to_markdown() 的标题格式互为逆操作，
    保证 ChapterPlan -> Markdown -> ChapterPlan 后 chapter_index 不变。
    找不到章号时回退为 1（保持旧行为）。
    """
    first = text.strip().split("\n")[0]
    m = re.search(r'第\s*(\d+)\s*章', first)
    return int(m.group(1)) if m else 1


def _extract_version(text: str) -> str:
    """从 '- **版本**: vN' 行恢复规划版本号。缺失时回退为 'v1'。"""
    m = re.search(r'\*\*版本\*\*\s*[:：]\s*(\S+)', text)
    if m:
        return m.group(1)
    m = re.search(r'^版本\s*[:：]\s*(\S+)', text, re.MULTILINE)
    return m.group(1) if m else "v1"
