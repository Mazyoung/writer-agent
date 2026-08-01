"""
ChapterPlanner — 章规划师（重写版）。

核心变更:
1. plan_chapter(): 加载全部追踪文档，生成 Part A + Part B 的完整章规划
2. assemble_context_package(): 从追踪文档组装 Part B 上下文包
3. 保留 replan() 用于已写部分场景后的重规划
"""

import re
from src.core.agent_base import BaseAgent
from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.storage.document_formats import ChapterPlan, ContextPackage, VolumePlan


class ChapterPlanner(BaseAgent):
    """生成含 Part A + Part B 的完整章规划"""

    def __init__(self, novel_id: str):
        super().__init__("chapter_planner", novel_id, "chapter_planner.txt")
        settings = get_settings()
        self.fs = FileStore(novel_id, settings.data_dir)

    # ── 主入口 ─────────────────────────────────────────

    def load_active_volume(self) -> VolumePlan:
        """加载当前 ACTIVE Volume Plan（Runtime Planning State 的唯一战术层）。"""
        text = self.fs.load_canonical("tracking", "volume_plan")
        return VolumePlan.from_markdown(text) if text else VolumePlan()

    def _require_long_term_plans(self) -> tuple[str, str]:
        """硬性要求 Book Plan + Active Volume Plan 存在。

        分层规划 (Book → Volume → Chapter) 是 plan 的必要输入；
        缺失时明确报错并给出迁移路径，不静默用空字符串继续。
        """
        book_plan = self.fs.load_canonical("tracking", "book_plan") or ""
        volume_plan = self.fs.load_canonical("tracking", "volume_plan") or ""
        missing = []
        if not book_plan.strip():
            missing.append("tracking/book_plan.md")
        if not volume_plan.strip():
            missing.append("tracking/volume_plan.md")
        if missing:
            raise FileNotFoundError(
                "缺少长期规划文件: " + ", ".join(missing) +
                "\n分层规划 (Book → Volume → Chapter) 是 plan 命令的必要输入。"
                "\n新小说: 先运行 python main.py init <小说名> --confirm 生成。"
                "\n旧数据: 运行 python migrate.py <小说名> 从 plot_structure.md 迁移。")
        return book_plan, volume_plan

    def plan_chapter(self, chapter_index: int,
                     chapter_outline: str = "",
                     extra_instructions: str = "",
                     rag_evidence: str = "") -> ChapterPlan:
        """生成完整章规划（Part A + Part B）。

        消费链：Book Plan + Active Volume Plan + Memory（追踪文档/事实摘要）。
        上下文优先级：World Setting → RAG Evidence → Book 战略约束 → Current Volume Plan
        → Actual Memory/Facts → Recent Chapter Context → 本章任务。

        E04: rag_evidence 来自 ChromaDB 历史检索结果，注入【历史检索证据（RAG）】区域。
        """
        # 硬性依赖：长期规划（缺失即明确报错）
        book_plan, volume_plan = self._require_long_term_plans()

        # 加载上下文
        active_vp = VolumePlan.from_markdown(volume_plan)
        print(f"  [ChapterPlanner] 活跃卷: 第{active_vp.volume_number}卷"
              f"（{active_vp.status}，{active_vp.chapter_range or '章节范围未定'}）")
        world_setting = self.fs.load_canonical("settings", "world_setting") or ""
        prev_chapter_end = self._load_prev_chapter_end(chapter_index)
        fact_context = self._load_recent_fact_digests(chapter_index)
        rels = self.fs.load_tracking_doc("character_relationships") or ""
        items = self.fs.load_tracking_doc("items_equipment") or ""
        cult = self.fs.load_tracking_doc("cultivation_system") or ""
        char_states = self.fs.load_tracking_doc("character_states") or ""

        # 构建 prompt —— 按优先级从高到低排列
        parts = []
        parts.append(f"## 第 {chapter_index} 章规划任务")

        # 1. World Setting / Hard Rules
        if world_setting:
            parts.append(f"### 世界观设定（最高优先级·硬规则）\n{world_setting[:2000]}")

        # 1.5. RAG Evidence — 历史章节检索结果（E04 P0 #8, #9）
        if rag_evidence:
            parts.append(f"## 【历史检索证据（RAG）】\n{rag_evidence}")

        # 2. Book Strategic Constraints
        parts.append(f"### 全书战略规划 Book Plan（战略约束层，方向性参考）\n{book_plan[:2000]}")

        # 3. Current Volume Plan（本章所属的战术层事件）
        if chapter_outline:
            parts.append(f"### 章大纲（来自卷规划）\n{chapter_outline}")
        else:
            vol_context = self._extract_chapter_from_volume(volume_plan, chapter_index)
            if vol_context:
                parts.append(f"### 当前卷规划中本章对应的事件（Volume Plan）\n{vol_context}")

        # 4. Actual Memory / Facts — 追踪文档 + 前章事实（Part B 原材料）
        parts.append("## 当前追踪文档（Part B 上下文包的原材料）")
        if rels:
            parts.append(f"### character_relationships.md\n{rels[:3000]}")
        if items:
            parts.append(f"### items_equipment.md\n{items[:2000]}")
        if cult:
            parts.append(f"### cultivation_system.md\n{cult[:1500]}")
        if char_states:
            parts.append(f"### character_states.md (Authoritative Current State)\n{char_states[:1500]}")
        if fact_context:
            parts.append(f"### 前章事实摘要（已发生的实际事实，优先于未来计划）\n{fact_context[:2500]}")

        # 5. Recent Chapter Context
        if prev_chapter_end:
            parts.append(f"### [必读] 上一章结尾\n{prev_chapter_end[-800:]}")

        # 6. 本章任务层
        if extra_instructions:
            parts.append(f"## 作者额外指示\n{extra_instructions}")

        parts.append(
            "\n---\n请按输出格式生成第 {} 章的完整规划（Part A + Part B）。\n"
            "重要：若全书规划/卷规划与已发生的事实（事实摘要、上一章结尾）存在冲突，"
            "以事实为准，不得假装事实没有发生；在「关键伏笔节点」中以 "
            "[PLANNING CONFLICT] 标注冲突说明，不要自行重写历史。".format(chapter_index))

        user_msg = "\n\n".join(parts)

        result = self.run(
            user_message=user_msg,
            save_category="outlines",
            save_prefix=f"chapter_plan_ch{chapter_index:04d}",
            use_canonical=True,
        )
        return ChapterPlan.from_markdown(result.content)

    def plan_from_interactive_answers(self, chapter_index: int,
                                       answers: dict,
                                       context: dict) -> ChapterPlan:
        """根据交互式 Q&A 收集的答案生成章规划。

        Args:
            chapter_index: 章序号
            answers: 6 轮 Q&A 的答案字典 {qid: answer}
            context: 追踪文档上下文字典 {key: text}
        """
        # 与非交互路径一致：硬性要求长期规划存在
        self._require_long_term_plans()

        parts = []
        parts.append(f"## 第 {chapter_index} 章规划任务")

        # 作者的回答
        parts.append("## 作者对本草的指示（最高优先级）")
        qa_map = {
            "chapter_core": "章核心",
            "characters": "角色关系",
            "items": "物品追踪",
            "foreshadow": "伏笔推进",
            "emotion": "情感调色板",
            "scenes": "场景拆分",
        }
        for qid, label in qa_map.items():
            if qid in answers and answers[qid].strip():
                parts.append(f"### {label}\n{answers[qid]}")

        # 追踪文档
        parts.append("## 追踪文档（Part B 原材料）")
        if context.get("character_relations"):
            parts.append(f"### character_relationships.md\n{context['character_relations'][:3000]}")
        if context.get("items_tracking"):
            parts.append(f"### items_equipment.md\n{context['items_tracking'][:2000]}")
        if context.get("volume_event"):
            parts.append(f"### 卷规划对应事件\n{context['volume_event']}")

        parts.append("\n---\n请根据以上所有信息（特别是作者的指示），按输出格式生成完整的章规划（Part A + Part B）。作者指示中的内容优先于追踪文档。")

        user_msg = "\n\n".join(parts)
        result = self.run(
            user_message=user_msg,
            save_category="outlines",
            save_prefix=f"chapter_plan_ch{chapter_index:04d}",
            use_canonical=True,
        )
        return ChapterPlan.from_markdown(result.content)

    def assemble_context_package(self, chapter_index: int) -> ContextPackage:
        """从追踪文档组装 Part B 上下文包（无需 LLM 调用）。

        适合在交互式规划中快速生成上下文包初稿供人工审阅。
        """
        rels = self.fs.load_tracking_doc("character_relationships") or "暂无"
        items = self.fs.load_tracking_doc("items_equipment") or "暂无"
        cult = self.fs.load_tracking_doc("cultivation_system") or "暂无"
        fact_context = self._load_recent_fact_digests(chapter_index)

        # 提取禁止清单
        forbidden = ""
        if fact_context:
            m = re.search(r'### 明确未出现的内容\s*\n(.*?)(?=###|\Z)',
                          fact_context, re.DOTALL)
            if m:
                forbidden = m.group(1).strip()

        # 提取待解悬念（作为伏笔节点候选）
        suspense = ""
        if fact_context:
            m = re.search(r'### 待解悬念\s*\n(.*?)(?=###|\Z)',
                          fact_context, re.DOTALL)
            if m:
                suspense = m.group(1).strip()

        foreshadow = ""
        if suspense:
            foreshadow = f"## 待解悬念（来自前章）\n{suspense}\n\n## 本章建议\n[待人工填写]"

        return ContextPackage(
            character_relations=rels[:3000] if rels != "暂无" else rels,
            items_tracking=items[:2000] if items != "暂无" else items,
            cultivation_status=cult[:1500] if cult != "暂无" else cult,
            foreshadow_nodes=foreshadow or "待人工填写",
            emotion_palette="待人工填写",
            forbidden_list=forbidden or "暂无",
        )

    # ── replan（保留，用于已写部分场景后的重规划） ──

    def replan(self, original_plan: str, written_scenes: list[str],
               event_context: str, world_setting: str = "",
               chapter_index: int = 1, full_texts: bool = False,
               prev_chapter_end: str = "",
               scene_fact_context: str = "") -> str:
        """根据已写内容重新规划剩余场景。保留原有逻辑。"""
        summaries = []
        for i, s in enumerate(written_scenes):
            if full_texts:
                summaries.append(f"### 已完成：场景 {i+1}\n{s}")
            elif len(s) <= 800:
                summaries.append(f"### 已完成：场景 {i+1}\n{s}")
            else:
                summaries.append(
                    f"### 已完成：场景 {i+1}\n"
                    f"[开头] {s[:200]}\n...\n[结尾] {s[-500:]}"
                )
        written_summary = "\n\n---\n\n".join(summaries)

        user_msg = ""
        if scene_fact_context:
            user_msg += f"""## 【最高优先级·不可违背】已完成场景的事实快照
{scene_fact_context}

---

"""

        user_msg += f"""## 原始场景规划
{original_plan}

## 已完成的场景正文
{written_summary}

## 本章必须覆盖的情节事件
{event_context}

## 世界观设定（参考）
{world_setting[:1500] if world_setting else ""}
"""
        if prev_chapter_end:
            user_msg += f"## 【必读】上一章结尾\n{prev_chapter_end}\n\n"

        user_msg += """---
请根据已完成的场景内容，重新规划本章剩余场景。总场景数不超过5个（含已完成场景）。
输出完整的更新后场景规划。"""

        result = self.run(
            user_message=user_msg,
            save_category="outlines",
            save_prefix=f"scene_plan_ch{chapter_index:04d}",
            use_canonical=True,
        )
        return result.content

    # ── 辅助方法 ─────────────────────────────────────

    def _load_prev_chapter_end(self, chapter_index: int) -> str:
        """加载上一章结尾。"""
        if chapter_index <= 1:
            return ""
        prev = self.fs.load_latest("chapters", f"chapter_{chapter_index - 1:04d}")
        if prev:
            return prev[-500:] if len(prev) > 500 else prev
        return ""

    def _load_recent_fact_digests(self, chapter_index: int, count: int = 3) -> str:
        """加载最近 N 章的事实摘要。"""
        parts = []
        for ch in range(max(1, chapter_index - count), chapter_index):
            fd = self.fs.load_latest("states", f"fact_digest_ch{ch:04d}")
            if fd:
                parts.append(f"## 第{ch}章事实摘要\n{fd[:1500]}")
        return "\n\n".join(parts)

    def _extract_chapter_from_volume(self, volume_plan: str,
                                     chapter_index: int) -> str:
        """从卷规划中提取本章对应的事件概要。"""
        if not volume_plan:
            return ""
        # 查找"对应章节: 第N章"的事件（容忍 ** 加粗标记）
        pattern = rf'(### 事件\d+[：:].*?\n.*?对应章节\**\s*[：:]\s*第{chapter_index}章.*?)(?=### 事件|\Z)'
        m = re.search(pattern, volume_plan, re.DOTALL)
        if m:
            return m.group(1)[:1500]
        # 回退：返回事件链中第 chapter_index 个事件
        events = re.findall(r'### 事件\d+[：:].*?\n.*?(?=### 事件|\Z)',
                            volume_plan, re.DOTALL)
        if chapter_index <= len(events):
            return events[chapter_index - 1][:1500]
        return volume_plan[:2000]
