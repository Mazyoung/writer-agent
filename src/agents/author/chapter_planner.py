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
                "\n旧数据: 运行 python scripts/migrate_legacy_data.py <小说名> "
                "从 plot_structure.md 迁移。")
        active = VolumePlan.from_markdown(volume_plan)
        if active.status.upper() == "DRAFT":
            pattern = r'(\*\*状态\*\*\s*[:：]\s*)DRAFT\b'
            volume_plan = re.sub(
                pattern, r'\g<1>ACTIVE', volume_plan, count=1,
                flags=re.IGNORECASE,
            )
            (self.fs.root / "tracking" / "volume_plan.md").write_text(
                volume_plan, encoding="utf-8"
            )
            active = VolumePlan.from_markdown(volume_plan)
            print(
                f"  [Volume] 第{active.volume_number}卷已在 Chapter Planning "
                "开始时确认为 ACTIVE。"
            )
        if active.status.upper() != "ACTIVE":
            raise ValueError(
                "章节规划需要 ACTIVE 状态的 volume_plan.md；"
                f"当前状态为 {active.status}"
            )
        return book_plan, volume_plan

    def plan_chapter(self, chapter_index: int,
                     chapter_outline: str = "",
                     extra_instructions: str = "",
                     rag_evidence: str = "",
                     chapter_intent: str = "",
                     current_state_text: str = "") -> ChapterPlan:
        """生成完整章规划（Part A + Part B）。

        消费链：Book Plan + Active Volume Plan + Memory（追踪文档/事实摘要）。
        上下文优先级：World Setting → RAG Evidence → Book 战略约束 → Current Volume Plan
        → Actual Memory/Facts → Recent Chapter Context → 本章任务。

        E04: rag_evidence 来自 ChromaDB 历史检索结果，注入【历史检索证据（RAG）】区域。
        E07.6: chapter_intent 是可选的人类本章创作意图，独立于兼容参数。
        """
        # 硬性依赖：长期规划（缺失即明确报错）
        book_plan, volume_plan = self._require_long_term_plans()

        # 加载上下文
        active_vp = VolumePlan.from_markdown(volume_plan)
        print(f"  [ChapterPlanner] 活跃卷: 第{active_vp.volume_number}卷（{active_vp.status}）")
        world_setting = self.fs.load_canonical("settings", "world_setting") or ""
        prev_chapter_end = self._load_prev_chapter_end(chapter_index)
        fact_context = ""  # E07.7: global history enters only through retrieved FACTs.
        if not current_state_text:
            from src.storage.current_state_store import CurrentStateStore
            from src.storage.sqlite_store import SQLiteStore

            sqlite = SQLiteStore(self.fs.root / "state.db")
            try:
                _state, current_state_text, _digest = CurrentStateStore(
                    self.novel_id, self.fs, sqlite
                ).ensure_initialized()
            finally:
                sqlite.close()

        # 构建 prompt —— 按优先级从高到低排列
        parts = []
        parts.append(f"## 第 {chapter_index} 章规划任务")

        # 1. World Setting / Hard Rules
        if world_setting:
            parts.append(f"### 世界观设定（最高优先级·硬规则）\n{world_setting[:2000]}")

        # 1.5. E07.7 FACT candidates plus narrowly expanded source prose.
        if rag_evidence:
            parts.append(f"## 【历史检索证据（RAG）】\n{rag_evidence}")
            parts.append(
                "只采用与本章设计真正相关的 FACT。将采用项（保留 FACT-ID）写入"
                "「采用的历史事实」；仅将实际使用的局部原文写入「历史原文局部」。"
                "未采用的候选事实和原文不得进入 Chapter Plan。"
            )

        # 2. Book Strategic Constraints
        parts.append(f"### 全书战略规划 Book Plan（战略约束层，方向性参考）\n{book_plan[:2000]}")

        # 3. Volume-level path. The Planner chooses this chapter's events.
        parts.append(f"### 当前卷大故事路径（Volume Plan）\n{volume_plan[:3000]}")
        if chapter_outline:
            parts.append(f"### 作者提供的本章补充意图\n{chapter_outline}")

        # 4. Actual present state — one generated snapshot (Part B material)
        parts.append("## 当前状态（tracking/current_state.md；Part B 原材料）")
        parts.append(current_state_text or "暂无")
        if fact_context:
            parts.append(f"### 前章事实摘要（已发生的实际事实，优先于未来计划）\n{fact_context[:2500]}")

        # 5. Recent Chapter Context
        if prev_chapter_end:
            parts.append(f"### [必读] 上一章结尾\n{prev_chapter_end[-800:]}")

        # 6. 本章任务层
        if chapter_intent:
            parts.append(f"## Chapter Intent（作者本章创作意图）\n{chapter_intent}")
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
        if context.get("volume_plan"):
            parts.append(f"### 当前卷大故事路径\n{context['volume_plan']}")

        parts.append("\n---\n请根据以上所有信息（特别是作者的指示），按输出格式生成完整的章规划（Part A + Part B）。作者指示中的内容优先于追踪文档。")

        user_msg = "\n\n".join(parts)
        result = self.run(
            user_message=user_msg,
            save_category="outlines",
            save_prefix=f"chapter_plan_ch{chapter_index:04d}",
            use_canonical=True,
        )
        return ChapterPlan.from_markdown(result.content)

    def revise_plan(
        self,
        *,
        chapter_index: int,
        current_plan: str,
        review_issues: list[str],
        planning_context: str,
        chapter_intent: str = "",
        human_feedback: str = "",
    ) -> str:
        issues = "\n".join(
            f"- {issue}" for issue in review_issues if str(issue).strip()
        )
        prompt = f"""## 当前 Chapter Plan
{current_plan}

## Plan Review 明确问题
{issues or "- 未提供结构化问题；请保持原规划并修复 Review 指出的缺陷"}

## 原 Planning Context
{planning_context}

## Chapter Intent
{chapter_intent or "无"}

## 作者补充反馈
{human_feedback or "无"}

---
保留当前 Plan 中没有问题的部分，只修改 Review 明确指出的问题。
输出完整修订版 Chapter Plan，不写解释。"""
        result = self.run(
            user_message=prompt,
            save_category="outlines",
            save_prefix=f"chapter_plan_ch{chapter_index:04d}",
            use_canonical=True,
        )
        return result.content

    def assemble_context_package(self, chapter_index: int) -> ContextPackage:
        """从追踪文档组装 Part B 上下文包（无需 LLM 调用）。

        适合在交互式规划中快速生成上下文包初稿供人工审阅。
        """
        from src.storage.current_state_store import CurrentStateStore
        from src.storage.sqlite_store import SQLiteStore

        sqlite = SQLiteStore(self.fs.root / "state.db")
        try:
            _state, current_state, _digest = CurrentStateStore(
                self.novel_id, self.fs, sqlite
            ).ensure_initialized()
        finally:
            sqlite.close()
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
            character_relations=current_state,
            items_tracking=current_state,
            cultivation_status=current_state,
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
