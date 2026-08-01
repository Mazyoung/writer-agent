"""
交互式 CLI 工具 — plan --interactive 的 6 轮 Q&A 对话引擎。

设计: 模板驱动的 Q&A，所有答案收集完毕后一次 LLM 调用生成规划。
"""

import re
from src.storage.file_store import FileStore


# ── 6 轮 Q&A 模板 ────────────────────────────────────────

INTERACTIVE_QUESTIONS = [
    {
        "id": "chapter_core",
        "title": "第 1/6 轮：章核心确认",
        "template": """## 卷规划中本章对应的事件

{volume_event}

## 上一章结尾

{prev_chapter_end}

---
请确认或修改以下内容：

**本章要达成的核心目标**（一句话）：
{chapter_goal}

**本章类型**（延续型/新展开型/高潮型/收束型）：
{chapter_type}

**是否有特别想在这一章做的事**（引入新角色/揭示关键信息/制造特定氛围等）：
""",
    },
    {
        "id": "characters",
        "title": "第 2/6 轮：角色关系",
        "template": """## 当前角色关系图

{character_relations}

---
请确认或修改：

**本章出场的角色**（列出所有，包括只出现名字不出现的也标出来）：
{suggested_characters}

**本章中角色关系会发生什么变化**（谁对谁的态度变了/新关系建立/旧关系破裂）：
""",
    },
    {
        "id": "items",
        "title": "第 3/6 轮：物品追踪",
        "template": """## 当前主角持有物品

{items_tracking}

---
请确认或修改：

**本章中主角会获得什么新物品**（来源、属性、怎么得到的）：
""",
    },
    {
        "id": "foreshadow",
        "title": "第 4/6 轮：伏笔推进",
        "template": """## 当前未回收的伏笔

{pending_foreshadows}

## 前章待解悬念

{suspense}

---
请确认或修改：

**本章需要推进或暗示的伏笔**（选1-3个，说明怎么推进）：
""",
    },
    {
        "id": "emotion",
        "title": "第 5/6 轮：情感调色板",
        "template": """**本章整体基调**（一句话）：
{suggested_tone}

**每个场景的情感轨迹**（从X情绪 → Y情绪）：
场景1: 从 →
场景2: 从 →
场景3: 从 →
（如场景数不同请调整）

---
请确认或修改以上内容：
""",
    },
    {
        "id": "scenes",
        "title": "第 6/6 轮：场景拆分",
        "template": """请确认本章的场景拆分：

**场景数量**（3-5个）：
{suggested_scene_count}

**每个场景的一句话概括**：
{suggested_scenes}

---
确认场景拆分，或修改场景数量/顺序/边界：
""",
    },
]


# ── 交互式规划引擎 ──────────────────────────────────────

class InteractivePlanEngine:
    """管理 6 轮 Q&A 对话，收集答案后生成规划。"""

    def __init__(self, file_store: FileStore, chapter_index: int):
        self.fs = file_store
        self.chapter_index = chapter_index
        self.answers: dict[str, str] = {}
        self.context: dict[str, str] = {}

    def load_context(self) -> dict:
        """加载所有追踪文档，构建 Q&A 上下文。"""
        ctx = {}

        # 卷规划
        vp = self.fs.load_tracking_doc("volume_plan") or ""
        ctx["volume_event"] = self._extract_chapter_event(vp) or "（卷规划未找到本章对应事件）"

        # 上一章结尾
        prev = self._load_prev_end()
        ctx["prev_chapter_end"] = prev[-400:] if prev else "（第一章，无上一章）"

        # 角色关系
        ctx["character_relations"] = self.fs.load_tracking_doc("character_relationships") or "暂无"

        # 物品
        ctx["items_tracking"] = self.fs.load_tracking_doc("items_equipment") or "暂无"

        # 伏笔
        ctx["pending_foreshadows"] = self._load_pending_foreshadows()
        ctx["suspense"] = self._load_suspense()

        # 从卷规划推断默认值
        ctx["chapter_goal"] = self._guess_chapter_goal(ctx["volume_event"])
        ctx["chapter_type"] = "延续型"
        ctx["suggested_characters"] = self._extract_characters_from_rels(ctx["character_relations"])
        ctx["suggested_tone"] = "从压抑生存转向试探性行动"
        ctx["suggested_scene_count"] = "3"
        ctx["suggested_scenes"] = self._guess_scenes(ctx["volume_event"])

        self.context = ctx
        return ctx

    def get_question(self, round_index: int) -> str:
        """生成第 N 轮的问题文本。"""
        if round_index < 0 or round_index >= len(INTERACTIVE_QUESTIONS):
            return ""
        q = INTERACTIVE_QUESTIONS[round_index]
        return q["template"].format(**self.context)

    def get_question_title(self, round_index: int) -> str:
        if round_index < 0 or round_index >= len(INTERACTIVE_QUESTIONS):
            return ""
        return INTERACTIVE_QUESTIONS[round_index]["title"]

    def record_answer(self, round_index: int, answer: str):
        qid = INTERACTIVE_QUESTIONS[round_index]["id"]
        self.answers[qid] = answer

    def build_final_prompt(self) -> str:
        """将所有答案组装为 LLM 规划 prompt。"""
        parts = []
        parts.append(f"## 第 {self.chapter_index} 章规划任务")

        q = INTERACTIVE_QUESTIONS
        parts.append(f"### 章核心\n{self.answers.get('chapter_core', '')}")
        parts.append(f"### 角色关系\n{self.answers.get('characters', '')}")
        parts.append(f"### 物品追踪\n{self.answers.get('items', '')}")
        parts.append(f"### 伏笔推进\n{self.answers.get('foreshadow', '')}")
        parts.append(f"### 情感调色板\n{self.answers.get('emotion', '')}")
        parts.append(f"### 场景拆分\n{self.answers.get('scenes', '')}")

        # 附上追踪文档
        parts.append(f"## 角色关系文档\n{self.context.get('character_relations', '')[:2000]}")
        parts.append(f"## 物品装备文档\n{self.context.get('items_tracking', '')[:1500]}")
        parts.append(f"## 卷规划事件\n{self.context.get('volume_event', '')}")

        parts.append("\n---\n请根据以上所有信息，按输出格式生成完整的章规划（Part A + Part B）。")
        return "\n\n".join(parts)

    # ── 辅助 ──────────────────────────────────────────

    def _load_prev_end(self) -> str:
        if self.chapter_index <= 1:
            return ""
        prev = self.fs.load_latest("chapters",
                                    f"chapter_{self.chapter_index - 1:04d}_styled")
        if not prev:
            prev = self.fs.load_latest("chapters",
                                        f"chapter_{self.chapter_index - 1:04d}")
        return prev or ""

    def _load_pending_foreshadows(self) -> str:
        bp = self.fs.load_tracking_doc("book_plan") or ""
        if not bp:
            return "暂无"
        m = re.search(r'## 全局伏笔追踪\s*\n(.*?)(?=##|\Z)', bp, re.DOTALL)
        return m.group(1).strip() if m else "暂无"

    def _load_suspense(self) -> str:
        parts = []
        for ch in range(max(1, self.chapter_index - 3), self.chapter_index):
            fd = self.fs.load_latest("states", f"fact_digest_ch{ch:04d}")
            if fd:
                m = re.search(r'### 待解悬念\s*\n(.*?)(?=###|\Z)', fd, re.DOTALL)
                if m and m.group(1).strip():
                    parts.append(f"第{ch}章: {m.group(1).strip()[:200]}")
        return "\n".join(parts) if parts else "暂无"

    def _extract_chapter_event(self, volume_plan: str) -> str:
        if not volume_plan:
            return ""
        pattern = rf'(### 事件\d+[：:].*?\n.*?对应章节[：:]\s*第{self.chapter_index}章.*?)(?=### 事件|\Z)'
        m = re.search(pattern, volume_plan, re.DOTALL)
        if m:
            return m.group(1)[:1500]
        events = re.findall(r'### 事件\d+[：:].*?\n.*?(?=### 事件|\Z)',
                            volume_plan, re.DOTALL)
        if self.chapter_index <= len(events):
            return events[self.chapter_index - 1][:1500]
        return volume_plan[:1500]

    def _guess_chapter_goal(self, vol_event: str) -> str:
        if not vol_event or vol_event.startswith("（"):
            return "（请描述本章核心目标）"
        lines = vol_event.strip().split("\n")
        for line in lines:
            if "核心内容" in line or "发生什么" in line:
                return line.split("：", 1)[-1].split(":")[-1].strip()[:100]
        return lines[0][:100] if lines else "（请描述本章核心目标）"

    def _extract_characters_from_rels(self, rels: str) -> str:
        if not rels or rels == "暂无":
            return "（请列出本章出场角色）"
        names = re.findall(r'\*\*(.{2,8})\*\*', rels)
        unique = list(dict.fromkeys(names))[:10]
        return ", ".join(unique) if unique else "（请列出本章出场角色）"

    def _guess_scenes(self, vol_event: str) -> str:
        if not vol_event:
            return "场景1: 开场\n场景2: 发展\n场景3: 收尾"
        return "场景1: 开场建立\n场景2: 核心事件\n场景3: 后果与过渡"
