from pathlib import Path
from dataclasses import dataclass, field

from src.config.settings import get_settings
from src.storage.file_store import FileStore
from src.storage.sqlite_store import SQLiteStore
from src.storage.chroma_store import ChromaStore
from src.storage.document_formats import ChapterPlan, BookPlan, VolumePlan
from src.planning.models import PlanRevision, PlanType, RevisionStatus
from src.planning.store import PlanningStore
from src.planning.trigger_policy import ReplanTrigger

from src.agents.architect.world_builder import WorldBuilder
from src.agents.architect.plot_designer import PlotDesigner
from src.agents.author.chapter_planner import ChapterPlanner
from src.agents.author.deepseek_writer import DeepSeekWriter
from src.agents.author.claude_stylist import ClaudeStylist
from src.agents.author.style_checker import StyleChecker
from src.agents.state_manager.state_manager import StateManager


class Orchestrator:
    """简化的编排器——人工主导、AI辅助的章节写作工作流"""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.settings = settings

        self.file_store = FileStore(novel_id, settings.data_dir)
        self.sqlite = SQLiteStore(settings.data_dir / "novels" / novel_id / "state.db")
        self._chroma = None  # Lazy: created on first RAG access (E04 P0 #1)

        # 架构层
        self.world_builder = WorldBuilder(novel_id)
        self.plot_designer = PlotDesigner(novel_id)

        # 创作层
        self.chapter_planner = ChapterPlanner(novel_id)
        self.writer = DeepSeekWriter(novel_id)
        self.stylist = ClaudeStylist(novel_id)

        # 质量层
        self.state_manager = StateManager(novel_id, self.sqlite)

        self._maybe_migrate()

    def _maybe_migrate(self):
        if not self.file_store.has_canonical("settings", "world_setting"):
            if self.file_store.load_latest("settings", "world_setting"):
                result = self.file_store.migrate_to_canonical()
                if result:
                    print(f"  [migration] canonical copies created: {list(result.keys())}")

    # ── Lazy Chroma (E04 P0 #1) ──────────────────────────

    @property
    def chroma(self) -> "ChromaStore":
        """Lazy ChromaStore — client + collection created on first RAG access."""
        if self._chroma is None:
            self._chroma = ChromaStore(self.settings.data_dir / "chroma_db")
        return self._chroma

    # ── RAG: Indexing ────────────────────────────────────

    def _index_chapter_to_rag(self, chapter_index: int) -> int:
        """Index a finalized/styled chapter into the RAG vector store.

        ONLY styled chapters are indexed (E04 P0 #2 corpus constraint).
        No fallback to raw/draft chapters — missing styled → [RAG WARNING] + skip.

        Chapter text is the Source of Truth; Chroma is Derived State.
        Indexing failure MUST NOT rollback chapter state (E04 P0 #12).
        Caller (review_chapter) has already committed all canonical state.
        """
        from src.storage.chroma_store import DEFAULT_BRANCH_ID

        styled_prefix = f"chapter_{chapter_index:04d}_styled"
        chapter_text = self.file_store.load_latest("chapters", styled_prefix)

        if not chapter_text:
            print(f"  [RAG WARNING] 第{chapter_index}章 styled 文件不存在，"
                  f"跳过索引（不 fallback 到 raw/draft）")
            return 0

        # source_path must match the actual file that was loaded
        styled_files = sorted(
            (self.file_store.root / "chapters").glob(f"{styled_prefix}_*.md"),
            reverse=True)
        if styled_files:
            source_path = f"chapters/{styled_files[0].name}"
        else:
            source_path = f"chapters/{styled_prefix}"

        branch_id = DEFAULT_BRANCH_ID

        try:
            count = self.chroma.index_chapter(
                novel_id=self.novel_id,
                branch_id=branch_id,
                chapter_index=chapter_index,
                content=chapter_text,
                source_path=source_path,
                chunk_size=self.settings.rag_chunk_size,
                chunk_overlap=self.settings.rag_chunk_overlap,
            )
            print(f"  [RAG] 第{chapter_index}章已索引: {count} chunks")
            return count
        except Exception as e:
            print(f"  [RAG WARNING] 第{chapter_index}章索引失败（章节状态不受影响）: {e}")
            return 0

    # ── RAG: Retrieval ───────────────────────────────────

    def _build_retrieval_query(self, chapter_index: int,
                                chapter_outline: str = "",
                                extra_instructions: str = "") -> str:
        """Build a deterministic retrieval query from available context.

        No LLM call — uses chapter outline, volume events, character/item names,
        and author instructions (E04 spec: deterministic query, no Query Rewrite).

        Volume event extraction reuses ChapterPlanner._extract_chapter_from_volume
        (E04.1: single canonical parser — no second regex copy in Orchestrator).
        """
        import re
        parts: list[str] = []

        # Volume events for this chapter — reuse ChapterPlanner parser
        vp_text = self.file_store.load_tracking_doc("volume_plan") or ""
        if vp_text:
            vol_context = self.chapter_planner._extract_chapter_from_volume(
                vp_text, chapter_index)
            if vol_context:
                parts.append(vol_context[:1000])

        # Chapter outline
        if chapter_outline:
            parts.append(chapter_outline[:500])

        # Character names from relationship tracking
        rels = self.file_store.load_tracking_doc("character_relationships") or ""
        char_names: set[str] = set()
        for m in re.finditer(r'\*\*(.+?)\*\*', rels):
            name = m.group(1).strip()
            if 2 <= len(name) <= 6 and not any(
                kw in name for kw in ["状态", "关系", "类型", "态度", "互动",
                                       "变更", "物品", "体系", "检查"]
            ):
                char_names.add(name)
        if char_names:
            parts.append("角色: " + ", ".join(sorted(char_names)[:10]))

        # Item names
        items_text = self.file_store.load_tracking_doc("items_equipment") or ""
        item_names: set[str] = set()
        for m in re.finditer(r'\|\s*(.+?)\s*\|', items_text):
            name = m.group(1).strip()
            if name and 2 <= len(name) <= 10 and not any(
                kw in name for kw in ["物品", "来源", "获得", "属性", "状态", "备注",
                                       "拥有者", "首次出现", "已知属性", "---"]
            ):
                item_names.add(name)
        if item_names:
            parts.append("物品: " + ", ".join(sorted(item_names)[:10]))

        # Author instructions
        if extra_instructions:
            parts.append(extra_instructions[:500])

        if parts:
            return " ".join(parts)
        return f"第{chapter_index}章 剧情"

    def _retrieve_evidence(self, chapter_index: int,
                           chapter_outline: str = "",
                           extra_instructions: str = "") -> tuple[str, "RetrievalTrace"]:
        """Run RAG retrieval and return (formatted evidence text, trace).

        Graceful degradation (E04 P0 #11): any error (query build or Chroma search)
        produces a failed trace + [RAG WARNING] — never crashes ChapterPlanner.

        E04.1: trace is created before query build so that query-builder
        exceptions also produce a failed trace (not silent degradation).
        """
        from src.storage.chroma_store import (
            RetrievalTrace, DEFAULT_BRANCH_ID,
        )
        from datetime import datetime

        branch_id = DEFAULT_BRANCH_ID
        top_k = self.settings.rag_top_k
        filters = {
            "novel_id": self.novel_id,
            "branch_id": branch_id,
            "chapter_index <": chapter_index,
            "source_type": "chapter",
        }

        # Create trace BEFORE query build — failures in either step
        # must produce a failed RetrievalTrace (E04.1 Fix 3)
        trace = RetrievalTrace(
            chapter_index=chapter_index,
            branch_id=branch_id,
            query="",       # populated below; empty on query-builder failure
            top_k=top_k,
            filters=filters,
            timestamp=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        )

        try:
            query = self._build_retrieval_query(chapter_index, chapter_outline,
                                                extra_instructions)
            trace.query = query

            results = self.chroma.search(
                novel_id=self.novel_id,
                branch_id=branch_id,
                query=query,
                chapter_index=chapter_index,
                top_k=top_k,
            )
        except Exception as e:
            trace.success = False
            trace.error_message = f"{type(e).__name__}: {e}"
            print(f"  [RAG WARNING] 检索/查询构建失败: {e}")
            return "", trace

        trace.results = results
        if not results:
            return "", trace  # empty retrieval is not a failure

        # Format evidence for prompt injection
        lines = [
            f"（从 {len(results)} 个历史章节片段中检索到以下相关内容，"
            f"距离越近越相关）\n"
        ]
        for i, r in enumerate(results, 1):
            lines.append(
                f"**[证据{i}]** 第{r.chapter_index}章 "
                f"chunk-{r.chunk_index} "
                f"(distance={r.distance:.4f}):"
            )
            lines.append(f"> {r.text[:600]}")
            lines.append("")
        evidence = "\n".join(lines)
        return evidence, trace

    def _save_retrieval_trace(self, trace: "RetrievalTrace") -> Path:
        """Persist retrieval trace as JSON (E04 P0 #10)."""
        import json
        from datetime import datetime

        traces_dir = self.file_store.root / "tracking" / "rag_traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = traces_dir / f"retrieval_trace_ch{trace.chapter_index:04d}_{ts}.json"
        path.write_text(
            json.dumps(trace.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        return path

    # ── RAG: Backfill ────────────────────────────────────

    def rag_index_backfill(self, rebuild: bool = False) -> dict:
        """Backfill or rebuild the RAG index from finalized/styled chapters.

        Args:
            rebuild: If True, clear the current branch index first, then
                     re-index all finalized chapters from scratch.

        Returns:
            dict with keys: indexed_chapters, total_chunks, errors.
        """
        import re
        from src.storage.chroma_store import DEFAULT_BRANCH_ID

        branch_id = DEFAULT_BRANCH_ID
        print(f"\n{'='*60}")
        print(f"RAG 索引{'重建' if rebuild else '补齐'}: {self.novel_id}")
        print(f"{'='*60}\n")

        if rebuild:
            print("  [RAG] 清理当前分支索引...")
            try:
                self.chroma.rebuild_branch(self.novel_id, branch_id)
            except Exception as e:
                print(f"  [RAG WARNING] 清理索引失败: {e}")

        # Scan for finalized/styled chapters
        chapters_dir = self.file_store.root / "chapters"
        styled_files = sorted(chapters_dir.glob("chapter_*_styled_*.md"))

        # Deduplicate: keep latest timestamp per chapter_index
        latest: dict[int, Path] = {}
        for f in styled_files:
            m = re.match(r'chapter_(\d{4})_styled_(\d{8}_\d{6})', f.name)
            if m:
                ci = int(m.group(1))
                ts = m.group(2)
                if ci not in latest or ts > re.match(
                    r'chapter_(\d{4})_styled_(\d{8}_\d{6})',
                    latest[ci].name
                ).group(2):  # type: ignore[union-attr]
                    latest[ci] = f

        stats = {"indexed_chapters": 0, "total_chunks": 0, "errors": 0}
        for ci in sorted(latest):
            f = latest[ci]
            content = f.read_text(encoding="utf-8")
            source_path = f"chapters/{f.name}"
            try:
                count = self.chroma.index_chapter(
                    novel_id=self.novel_id,
                    branch_id=branch_id,
                    chapter_index=ci,
                    content=content,
                    source_path=source_path,
                    chunk_size=self.settings.rag_chunk_size,
                    chunk_overlap=self.settings.rag_chunk_overlap,
                )
                stats["indexed_chapters"] += 1
                stats["total_chunks"] += count
                print(f"  [RAG] 第{ci}章: {count} chunks")
            except Exception as e:
                stats["errors"] += 1
                print(f"  [RAG WARNING] 第{ci}章索引失败: {e}")

        print(f"\n  完成: {stats['indexed_chapters']} 章, "
              f"{stats['total_chunks']} chunks"
              + (f", {stats['errors']} 错误" if stats["errors"] else ""))
        return stats

    # ═══ 初始化 ═══════════════════════════════════════════════

    def generate_proposal(self, hint: str = "") -> str:
        """Phase 1: 生成创作提案。"""
        print(f"\n{'='*60}")
        print(f"创建新小说: {self.novel_id}")
        print(f"{'='*60}\n")

        prompt = f"""你是一位资深网文编辑，正在帮助作者策划一部新的网络小说。请根据以下提示（可能为空），生成一份创作提案。

## 要求
1. 每个部分提供 2-3 个具体选项或建议
2. 建议要符合当前网文市场的流行趋势
3. 给出具体的、有画面感的描述

## 输出格式

# 创作提案

## 一、题材选择（选一个，可修改）
- **选项A**: [题材名] — [一句话描述]
- **选项B**: [题材名] — [一句话描述]
- **选项C**: [题材名] — [一句话描述]

## 二、核心设定
### 世界观基调
[建议 2-3 种不同的世界观方向]

### 力量/能力体系
[建议 2-3 种体系]

### 时代背景
[建议 1-2 种]

## 三、剧情方向
### 主线冲突
[2-3 个选项]

### 主角设定
[身份、性格、动机，2-3 个方向]

### 核心悬念/金手指
[主角的特殊优势或故事最大的钩子]

## 四、故事风格
### 文风
- **选项A**: [如：硬核严谨] — [说明]
- **选项B**: [如：轻松爽文] — [说明]

### 节奏
- **选项A**: [如：快节奏强冲突]
- **选项B**: [如：慢热铺垫型]

### 篇幅预期
- **选项A**: 中篇
- **选项B**: 长篇

## 五、一句话核心梗概
[最吸引人的一句话梗概]

## 六、作者补充
[留空]

---
作者提示: {hint if hint else '（无特殊要求，请自由发挥）'}
"""
        result = self.world_builder.run(
            user_message=prompt,
            save_category="",
            save_prefix="proposal",
            use_canonical=True,
        )
        print(f"\n提案已生成 -> data/novels/{self.novel_id}/proposal.md")
        print(f"请编辑后保存为 proposal_edited.md，然后运行: python main.py init {self.novel_id} --confirm")
        return result.content

    def initialize_novel(self, proposal: str) -> dict:
        """Phase 2: 提案 → 世界观 → Book Plan v1 + Volume 1 Plan v1（分层规划）。

        E03 起 PlotDesigner 直接产出 canonical 长期规划：
        - tracking/book_plan.md   （战略层，初始化一次、默认稳定）
        - tracking/volume_plan.md （战术层，ACTIVE 当前卷）
        不再生成 plot_structure.md（旧中间产物，运行时无消费者）。
        """
        print(f"\n{'='*60}")
        print(f"确认提案，生成分层规划: {self.novel_id}")
        print(f"{'='*60}\n")

        print("[1/3] 世界观构建师工作中...")
        world_prompt = f"""## 已确认的创作提案
{proposal}

---
请根据以上提案，生成完整的世界观设定文档。要求：
1. 铁律层：不可变的基础规则
2. 设定层：地理、势力、历史、文化
3. 力量/修炼体系详细说明
4. 所有设定必须与提案中的题材、风格一致"""
        world_setting = self.world_builder.run(
            user_message=world_prompt,
            save_category="settings",
            save_prefix="world_setting",
            use_canonical=True,
        ).content

        ws = self.file_store.load_canonical("settings", "world_setting") or world_setting

        print("[2/3] 情节设计师工作中... (Book Plan v1 / 战略层)")
        book_prompt = f"""## 已确认的创作提案
{proposal}

## 世界观设定
{ws[:5000]}

---
请根据以上内容，生成全书战略规划（Book Plan）。本次输出格式以本消息为准。

Book Plan 是整本书的长期战略，只写长期有效的内容：
- 不要写每章详细事件、具体场景或对白
- 「战略约束」部分列出不允许轻易破坏的设定与走向
- 「卷框架」只描述各卷的大致职责，后续卷 1-2 句方向即可

严格按以下 Markdown 格式输出：

# 全书规划：《书名》
- **版本**: v1

## 核心目标
## 核心矛盾
## 主角长期成长方向
## 战略约束
## 核心梗概
## 全书主题
## 结局方向
## 卷框架
### 第1卷：卷名
- **核心冲突**: ...
- **主角弧光**: ...
- **关键角色**: ...
- **章数预估**: ...
（每卷一节）
## 全局伏笔追踪
| 伏笔描述 | 埋伏章节 | 预计回收卷 | 状态 | 回收章节 |
|---------|---------|-----------|------|---------|"""
        book_plan = self.plot_designer.run(
            user_message=book_prompt,
            save_category="tracking",
            save_prefix="book_plan",
            use_canonical=True,
        ).content

        # Book Plan 必须先成功解析，Volume 1 才允许生成——
        # 不允许 Book/Volume 从 proposal/world_setting 并行独立生成。
        bp = BookPlan.from_markdown(book_plan)
        if not bp.title.strip() or not bp.volumes:
            raise ValueError(
                "Book Plan 解析失败（缺少标题或卷框架），分层规划链中断。"
                "\n请检查 tracking/book_plan.md 后重新运行 init --confirm。")
        print(f"  Book Plan 已解析: 《{bp.title}》v{bp.version}，{len(bp.volumes)} 卷框架")

        print("[3/3] 情节设计师工作中... (Volume 1 Plan v1 / 战术层)")
        volume_prompt = f"""## 已确认的创作提案
{proposal}

## 世界观设定（节选）
{ws[:3000]}

## 【全书战略规划 Book Plan】
{bp.to_markdown()[:4000]}

服从性约束（必须遵守）：
- Volume Plan 必须服从 Book Plan 的战略方向；
- 只能细化当前卷（第 1 卷），不得展开后续卷细节；
- 不得重新定义 Book Plan 的故事终局、核心矛盾或战略约束。

---
请生成第一卷的战术卷规划（Volume Plan）。本次输出格式以本消息为准。

要求（Rolling Horizon：只详细规划第一卷，不要写后续卷细节）：
- 事件链覆盖第一卷章节范围，每个事件必须标注「对应章节」
- 里程碑标注大致章节位置
- 所有命名严格沿用世界观设定与 Book Plan

严格按以下 Markdown 格式输出：

# 第1卷规划：《卷名》
- **版本**: v1
- **状态**: ACTIVE
- **章节范围**: 第1章-第N章

## 卷概述
- **核心冲突**: ...
- **角色目标**: ...
- **障碍**: ...

## 关键里程碑
- ...

## 事件链
### 事件1：事件名
- **触发条件**: ...
- **核心内容**: ...
- **涉及角色**: ...
- **情感基调**: ...
- **结果与影响**: ...
- **衔接**: ...
- **对应章节**: 第1章
（每事件一节）

## 卷内角色档案
### 角色名
- **当前状态**: ...
- **本卷弧光**: ...
- **关键关系**: ...
- **携带物品**: ...

## 卷内伏笔表
| 伏笔描述 | 埋伏章节 | 预计回收位置 | 状态 |
|---------|---------|------------|------|

## 节奏约束
...

## 已完成章节摘要
（留空）"""
        volume_plan = self.plot_designer.run(
            user_message=volume_prompt,
            save_category="tracking",
            save_prefix="volume_plan",
            use_canonical=True,
        ).content

        print(f"\n初始化完成！")
        print(f"  settings/world_setting.md")
        print(f"  tracking/book_plan.md   (Book Plan v1 / 战略层)")
        print(f"  tracking/volume_plan.md (Volume 1 Plan v1 / ACTIVE)")
        print(f"\n下一步: 人工审阅上述文件（可保存为 *_edited.md 覆盖），")
        print(f"        然后运行: python main.py plan {self.novel_id} --chapter 1")
        return {"world_setting": world_setting, "book_plan": book_plan,
                "volume_plan": volume_plan}

    # ═══ 规划 ═══════════════════════════════════════════════════

    def plan_chapter(self, chapter_index: int,
                     chapter_outline: str = "",
                     extra_instructions: str = "") -> ChapterPlan:
        """生成章规划（Part A + Part B）。E04: + RAG retrieval into prompt。"""
        print(f"\n{'='*60}")
        print(f"第 {chapter_index} 章规划")
        print(f"{'='*60}\n")

        # ── E04: RAG retrieval (before ChapterPlanner) ──
        rag_evidence = ""
        rag_trace = None
        try:
            print("  [RAG] 检索历史证据...")
            rag_evidence, rag_trace = self._retrieve_evidence(
                chapter_index, chapter_outline, extra_instructions)
            if rag_evidence:
                result_count = len(rag_trace.results) if rag_trace else 0
                print(f"  [RAG] 检索到 {result_count} 个相关历史片段")
            else:
                print(f"  [RAG] 未检索到相关历史片段（或数据库为空）")
        except Exception as e:
            print(f"  [RAG WARNING] 检索异常: {e}")

        print("  [ChapterPlanner] 加载追踪文档 + 生成规划...")
        plan = self.chapter_planner.plan_chapter(
            chapter_index, chapter_outline, extra_instructions,
            rag_evidence=rag_evidence)

        # ── Save retrieval trace ──
        if rag_trace:
            try:
                trace_path = self._save_retrieval_trace(rag_trace)
                print(f"  [RAG] 检索追踪已保存: {trace_path.name}")
            except Exception as e:
                print(f"  [RAG WARNING] 追踪保存失败: {e}")

        print(f"  Part A: {len(plan.scenes)} 个场景")
        ctx = plan.context
        has_rels = bool(ctx.character_relations and ctx.character_relations != "暂无")
        has_items = bool(ctx.items_tracking and ctx.items_tracking != "暂无")
        has_cult = bool(ctx.cultivation_status and ctx.cultivation_status != "暂无")
        print(f"  Part B: 角色关系{'Y' if has_rels else 'N'} 物品{'Y' if has_items else 'N'} 修炼{'Y' if has_cult else 'N'}")
        print(f"  已保存: outlines/chapter_plan_ch{chapter_index:04d}.md")
        return plan

    # ═══ 新卷（Rolling Horizon） ═════════════════════════════

    def start_new_volume(self, volume_number: int | None = None,
                         notes: str = "") -> str:
        """当前卷完成后，生成下一卷规划（显式 Rolling Horizon 接口）。

        事务式切换语义：Generate → Validate → Commit。
        在新 Volume 完成生成、解析与全部校验之前，不修改当前 ACTIVE
        Volume 的 canonical 状态；Commit 阶段失败会回滚，
        任何失败路径下当前卷都保持 ACTIVE，不产生半提交状态。
        """
        # ── 0. 读取当前状态（只读，不修改） ──
        old_text = self.file_store.load_tracking_doc("volume_plan")
        book_plan = self.file_store.load_tracking_doc("book_plan")
        missing = []
        if not old_text:
            missing.append("tracking/volume_plan.md")
        if not book_plan:
            missing.append("tracking/book_plan.md")
        if missing:
            raise FileNotFoundError(
                "缺少长期规划文件: " + ", ".join(missing) +
                "\n新小说: 先运行 python main.py init <小说名> --confirm 生成。"
                "\n旧数据: 运行 python migrate.py <小说名> 从 plot_structure.md 迁移。")

        old_vp = VolumePlan.from_markdown(old_text)
        new_index = volume_number if volume_number else old_vp.volume_number + 1
        if new_index <= old_vp.volume_number:
            raise ValueError(
                f"新卷号必须大于当前卷（当前第{old_vp.volume_number}卷，请求第{new_index}卷）")

        print(f"\n{'='*60}")
        print(f"新卷规划: 第{old_vp.volume_number}卷 → 第{new_index}卷（事务式切换）")
        print(f"{'='*60}\n")

        # ── 1. Generate：产出候选（写入时间戳候选文件，不触碰 canonical） ──
        memory = self._recent_fact_digests()
        prompt = f"""## 【全书战略规划 Book Plan】
{book_plan[:4000]}

服从性约束（必须遵守）：
- Volume Plan 必须服从 Book Plan 的战略方向；
- 只能细化当前卷（第 {new_index} 卷）；
- 不得重新定义 Book Plan 的故事终局、核心矛盾或战略约束。

## 已完成卷历史（第{old_vp.volume_number}卷，已锁定，不得修改）
{old_text[:4000]}

## 近期实际事实摘要（Memory — 已发生的事情，优先级高于计划）
{memory[:3000] if memory else "（暂无）"}

## 作者补充指示
{notes or "（无）"}

---
请生成第{new_index}卷的战术卷规划（Volume Plan）。本次输出格式以本消息为准。

要求（Rolling Horizon）:
- 只详细规划第{new_index}卷，承接已完成卷的真实结局，不要重复或改写历史
- 若 Book Plan 与已完成事实存在冲突，以事实为准，并在「节奏约束」中标注 [PLANNING CONFLICT] 说明
- 事件链每个事件必须标注「对应章节」，章节号接续已完成卷

严格按以下 Markdown 格式输出：

# 第{new_index}卷规划：《卷名》
- **版本**: v1
- **状态**: ACTIVE
- **章节范围**: 第X章-第Y章

## 卷概述
- **核心冲突**: ...
- **角色目标**: ...
- **障碍**: ...

## 关键里程碑
- ...

## 事件链
### 事件1：事件名
- **触发条件**: ...
- **核心内容**: ...
- **涉及角色**: ...
- **情感基调**: ...
- **结果与影响**: ...
- **衔接**: ...
- **对应章节**: 第X章

## 卷内角色档案
### 角色名
- **当前状态**: ...
- **本卷弧光**: ...
- **关键关系**: ...
- **携带物品**: ...

## 卷内伏笔表
| 伏笔描述 | 埋伏章节 | 预计回收位置 | 状态 |
|---------|---------|------------|------|

## 节奏约束
...

## 已完成章节摘要
（留空）"""
        print(f"  [Generate] 情节设计师生成第{new_index}卷候选...")
        candidate_text = self.plot_designer.run(
            user_message=prompt,
            save_category="tracking",
            save_prefix=f"candidate_volume_{new_index:02d}",
            use_canonical=False,   # 时间戳候选文件，绝不触碰 canonical
        ).content

        # ── 2. Parse + Validate（失败即中止，当前卷不受影响） ──
        new_vp = self._validate_volume_candidate(candidate_text, new_index)
        print(f"  [Validate] 候选通过: 《{new_vp.title}》第{new_vp.volume_number}卷，"
              f"{len(new_vp.events)} 个事件，{new_vp.chapter_range}")

        # ── 3. Commit（归档旧卷 → 新卷 ACTIVE → PlanRevision；失败回滚） ──
        old_vp.status = "COMPLETED"
        vol_dir = self.file_store.root / "tracking" / "volumes"
        vol_dir.mkdir(parents=True, exist_ok=True)
        archive_path = vol_dir / f"volume_{old_vp.volume_number:02d}.md"
        archive_written = False
        canonical_attempted = False
        try:
            archive_path.write_text(old_vp.to_markdown(), encoding="utf-8")
            archive_written = True
            canonical_attempted = True
            self.file_store.save_canonical("tracking", "volume_plan", candidate_text)

            rev = PlanRevision(
                plan_type=PlanType.VOLUME_PLAN,
                base_version=old_vp.version,
                new_version=new_vp.version,
                trigger_chapter=f"第{self.get_status().get('completed_chapters', 0)}章后",
                reason=f"{ReplanTrigger.USER_REQUEST}: new-volume 命令，"
                       f"第{old_vp.volume_number}卷 COMPLETED → 第{new_index}卷 ACTIVE"
                       + (f"；作者指示: {notes}" if notes else ""),
                old_content_ref=f"tracking/volumes/volume_{old_vp.volume_number:02d}.md",
                new_content_ref="tracking/volume_plan.md",
                affected_nodes=[f"volume_{old_vp.volume_number:02d}",
                                f"volume_{new_index:02d}"],
                status=RevisionStatus.APPLIED,
                approved_by="human",
                decision="显式 new-volume 命令",
            )
            PlanningStore(self.file_store.root).save_revision(rev)
        except Exception as e:
            # 回滚：恢复 canonical（save_canonical 留有 .bak），删除归档
            if canonical_attempted:
                self.file_store.rollback_canonical("tracking", "volume_plan")
            if archive_written and archive_path.exists():
                archive_path.unlink()
            raise RuntimeError(
                f"新卷提交失败，已回滚: 第{old_vp.volume_number}卷仍为 ACTIVE。"
                f"\n原因: {type(e).__name__}: {e}") from e

        print(f"  [Commit] 第{old_vp.volume_number}卷 COMPLETED → "
              f"tracking/volumes/volume_{old_vp.volume_number:02d}.md")
        print(f"  [Commit] 第{new_index}卷 ACTIVE → tracking/volume_plan.md")
        print(f"  [Commit] PlanRevision 已记录: {rev.revision_id}")

        if self.file_store.has_human_edit("tracking", "volume_plan"):
            print(f"  [!] 注意: 存在 volume_plan_edited.md，它会覆盖新卷内容。"
                  f"如不再需要请人工处理。")

        print(f"\n下一步: python main.py plan {self.novel_id} --chapter <接续章号>")
        return candidate_text

    @staticmethod
    def _validate_volume_candidate(text: str, expected_index: int) -> VolumePlan:
        """解析并校验新卷候选。任何一项失败都抛异常，调用方保证不提交。"""
        if not text or not text.strip():
            raise ValueError(
                "新卷候选生成失败：LLM 输出为空。当前卷保持 ACTIVE，未做任何修改。")
        vp = VolumePlan.from_markdown(text)
        problems = []
        if not vp.title.strip():
            problems.append("缺少卷标题（Markdown 无法解析）")
        if vp.volume_number != expected_index:
            problems.append(f"卷号错误：期望第{expected_index}卷，实际第{vp.volume_number}卷")
        if vp.status.upper() != "ACTIVE":
            problems.append(f"状态必须为 ACTIVE，实际为 {vp.status}")
        if not vp.chapter_range.strip():
            problems.append("缺少章节范围")
        if not vp.core_conflict.strip():
            problems.append("缺少卷概述（核心冲突）")
        if not vp.events:
            problems.append("事件链为空")
        if problems:
            raise ValueError(
                "新卷候选校验失败:\n  - " + "\n  - ".join(problems) +
                "\n当前卷保持 ACTIVE，未做任何修改。")
        return vp

    def _recent_fact_digests(self, count: int = 3) -> str:
        """扫描 states/ 取最近 N 个事实摘要（按文件名排序即按章号+时间排序）。"""
        states_dir = self.file_store.root / "states"
        files = sorted(states_dir.glob("fact_digest_ch*_*.md"))[-count:]
        parts = []
        for f in files:
            parts.append(f"## {f.stem}\n{f.read_text(encoding='utf-8')[:1200]}")
        return "\n\n".join(parts)

    # ═══ 写作 ═══════════════════════════════════════════════════

    def _save_and_check_styled(self, chapter_index: int, styled: str) -> str:
        """E05: 保存 styled chapter 一次 + StyleChecker 一次。

        消除 ClaudeStylist 内部重复副作用 + write/style-edit 行为漂移。
        两条路径共享此 helper，保证 single ownership of side effects。
        """
        self.file_store.save("chapters",
                             f"chapter_{chapter_index:04d}_styled", styled)

        report = StyleChecker(styled).check_all(file_path=f"第{chapter_index}章")
        print(report.summary())
        if report.errors > 0:
            print(f"\n  [!] {report.errors} 个错误 + {report.warnings} 个警告，请人工复核。")
        return styled

    def write_chapter(self, chapter_index: int) -> str:
        """写一章：DeepSeekWriter → ClaudeStylist → StyleChecker。"""
        print(f"\n{'='*60}")
        print(f"第 {chapter_index} 章写作")
        print(f"{'='*60}\n")

        # 加载章规划
        plan_text = self.file_store.load_canonical("outlines",
                                                    f"chapter_plan_ch{chapter_index:04d}")
        if not plan_text:
            plan_text = self.file_store.load_latest("outlines",
                                                     f"chapter_plan_ch{chapter_index:04d}")
        if not plan_text:
            raise ValueError(f"第 {chapter_index} 章规划不存在，请先运行 plan")

        plan = ChapterPlan.from_markdown(plan_text)
        world_setting = self.file_store.load_canonical("settings", "world_setting") or ""
        prev_end = self._get_prev_chapter_end(chapter_index)

        # 1. DeepSeek 写作
        print(f"  [DeepSeekWriter] 创作中（{len(plan.scenes)} 个场景）...")
        draft = self.writer.write_chapter(plan, world_setting, prev_end)

        # 2. Claude 风格编辑
        print(f"  [ClaudeStylist] 调性编辑...")
        emotion = plan.context.emotion_palette
        styled = self.stylist.edit_chapter(draft, chapter_index,
                                            emotion_palette=emotion,
                                            scene_plan_text=plan_text[:3000])

        # 3. 保存 + 风格检测（E05: single ownership — save once, check once）
        print(f"  [StyleChecker] 扫描AI句式...")
        self._save_and_check_styled(chapter_index, styled)

        print(f"\n  第 {chapter_index} 章完成（{len(styled)} 字符）")
        return styled

    # ═══ 审阅 ═══════════════════════════════════════════════════

    def review_chapter(self, chapter_index: int) -> dict:
        """章节后复盘：StateManager 分析 + 更新追踪文档。"""
        print(f"\n{'='*60}")
        print(f"第 {chapter_index} 章复盘")
        print(f"{'='*60}\n")

        chapter_text = self.file_store.load_latest("chapters",
                                                    f"chapter_{chapter_index:04d}_styled")
        if not chapter_text:
            chapter_text = self.file_store.load_latest("chapters",
                                                        f"chapter_{chapter_index:04d}")
        if not chapter_text:
            raise ValueError(f"第 {chapter_index} 章正文不存在")

        plan_text = self.file_store.load_canonical("outlines",
                                                    f"chapter_plan_ch{chapter_index:04d}") or ""

        # 加载当前追踪文档
        rels = self.file_store.load_tracking_doc("character_relationships") or ""
        items = self.file_store.load_tracking_doc("items_equipment") or ""
        cult = self.file_store.load_tracking_doc("cultivation_system") or ""

        print("  [StateManager] 分析章节...")
        analysis = self.state_manager.review_chapter(
            chapter_text, chapter_index, plan_text, rels, items, cult)

        print("  [StateManager] 更新追踪文档...")
        changes = self.state_manager.update_tracking_docs(
            chapter_index, chapter_text, analysis["raw_analysis"])

        # E05: Fact Digest via deterministic extraction from raw_analysis (no 2nd LLM)
        print("  [StateManager] 提取事实摘要...")
        self.state_manager.extract_fact_digest_from_analysis(
            analysis["raw_analysis"], chapter_index)

        # ── E04: Index chapter into RAG (derived state; failure does NOT rollback) ──
        try:
            print("  [RAG] 索引本章到向量库...")
            self._index_chapter_to_rag(chapter_index)
        except Exception as e:
            print(f"  [RAG WARNING] 索引失败（章节状态不受影响，可稍后通过 rag-index 修复）: {e}")

        print(f"\n  复盘完成。变更日志: states/post_chapter_update_ch{chapter_index:04d}.md")
        for key, val in changes.items():
            if key != "change_log" and val:
                print(f"    {key}: 已更新")
        return changes

    def style_edit(self, chapter_index: int, feedback: str = "") -> str:
        """定向风格修改——用人工反馈重新编辑。 E05: save + StyleChecker via shared helper。"""
        print(f"\n  [ClaudeStylist] 定向风格修改...")
        chapter_text = self.file_store.load_latest("chapters",
                                                    f"chapter_{chapter_index:04d}_styled")
        if not chapter_text:
            chapter_text = self.file_store.load_latest("chapters",
                                                        f"chapter_{chapter_index:04d}")
        if not chapter_text:
            raise ValueError(f"第 {chapter_index} 章正文不存在")

        plan_text = self.file_store.load_canonical("outlines",
                                                    f"chapter_plan_ch{chapter_index:04d}") or ""
        plan = ChapterPlan.from_markdown(plan_text) if plan_text else ChapterPlan()

        # 保存反馈
        if feedback:
            self.file_store.save_feedback(f"style_feedback_ch{chapter_index:04d}", feedback)

        styled = self.stylist.edit_chapter(
            chapter_text, chapter_index,
            emotion_palette=plan.context.emotion_palette,
            scene_plan_text=plan_text[:3000],
            style_feedback=feedback)

        # E05: save once, StyleChecker once — shared with write_chapter
        self._save_and_check_styled(chapter_index, styled)
        print(f"  风格修改完成（{len(styled)} 字符）")
        return styled

    # ═══ 状态 ═══════════════════════════════════════════════════

    def get_status(self) -> dict:
        """查看当前进度。"""
        status = {"novel": self.novel_id}

        # 卷规划（ACTIVE 当前卷）
        vp = self.file_store.load_tracking_doc("volume_plan")
        status["has_volume_plan"] = bool(vp)
        status["has_book_plan"] = bool(self.file_store.load_tracking_doc("book_plan"))
        if vp:
            active_vp = VolumePlan.from_markdown(vp)
            status["active_volume"] = active_vp.volume_number
            status["active_volume_status"] = active_vp.status

        # 章节
        chapters_dir = self.file_store.root / "chapters"
        styled_chapters = sorted(chapters_dir.glob("chapter_*_styled*.md"))
        status["completed_chapters"] = len(styled_chapters)

        # 追踪文档
        for doc in ["character_relationships", "items_equipment", "cultivation_system"]:
            status[f"has_{doc}"] = self.file_store.has_tracking_doc(doc)

        # SQLite 缓存
        status["sqlite_chapter_count"] = self.sqlite.get_chapter_count(self.novel_id)
        status["pending_foreshadows"] = len(self.sqlite.get_pending_foreshadows(self.novel_id))

        return status

    def print_status(self):
        """打印人类可读的状态摘要。"""
        s = self.get_status()
        print(f"\n小说: {s['novel']}")
        if s.get("active_volume"):
            print(f"当前卷: 第{s['active_volume']}卷 ({s['active_volume_status']})"
                  f"  全书规划: {'有' if s['has_book_plan'] else '无'}")
        else:
            print(f"卷规划: {'有' if s['has_volume_plan'] else '无'}"
                  f"  全书规划: {'有' if s.get('has_book_plan') else '无'}")
        print(f"已完成章节: {s['completed_chapters']}")
        rel_ok = 'Y' if s.get('has_character_relationships') else 'N'
        item_ok = 'Y' if s.get('has_items_equipment') else 'N'
        cult_ok = 'Y' if s.get('has_cultivation_system') else 'N'
        print(f"追踪文档: 角色关系{rel_ok} 物品装备{item_ok} 修炼体系{cult_ok}")
        print(f"未回收伏笔: {s['pending_foreshadows']}")

    # ═══ 回退 ═══════════════════════════════════════════════════

    def snapshot_all(self):
        for cat in ["settings", "outlines", "tracking"]:
            for f in (self.file_store.root / cat).glob("*.md"):
                if f.suffix == ".md" and not f.name.endswith(".bak.md"):
                    bak = f.with_suffix(".bak.md")
                    bak.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        print("快照已保存 (*.bak.md)")

    def rollback_all(self) -> list[str]:
        restored = []
        for cat in ["settings", "outlines", "tracking"]:
            for bak in sorted((self.file_store.root / cat).glob("*.bak.md")):
                main = bak.with_suffix(".md")
                main.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
                restored.append(str(main.relative_to(self.file_store.root)))
        return restored

    def rollback_chapter(self, chapter_index: int):
        """删除指定章的所有文件。"""
        for cat in ["chapters", "outlines", "briefs", "states"]:
            for f in (self.file_store.root / cat).glob(f"*ch{chapter_index:04d}*"):
                f.unlink()
                print(f"  已删除: {f}")
        # 标记卷大纲中该章需要重建
        vp = self.file_store.load_tracking_doc("volume_plan")
        if vp:
            self.file_store.save_canonical("tracking", "volume_plan",
                                            vp + f"\n\n[第{chapter_index}章已回退，需重建]")

    # ═══ 工具方法 ═════════════════════════════════════════════

    def _parse_scene_plan(self, scene_plan: str) -> list[str]:
        """解析场景规划为场景块列表。"""
        scenes = []
        lines = scene_plan.split("\n")
        current = []
        import re
        for line in lines:
            if re.match(r'^#{1,4}\s*场景\s*\d+', line):
                if current:
                    scenes.append("\n".join(current))
                current = [line]
            elif current:
                current.append(line)
        if current:
            scenes.append("\n".join(current))
        return scenes if scenes else [scene_plan]

    def _extract_ending(self, text: str) -> str:
        return text[-200:] if len(text) > 200 else text

    def _extract_title(self, text: str, chapter_index: int) -> str:
        first_line = text.strip().split("\n")[0].strip()
        if first_line.startswith("#"):
            return first_line.lstrip("#").strip()
        return f"第{chapter_index}章"

    def _get_prev_chapter_end(self, chapter_index: int) -> str:
        if chapter_index <= 1:
            return ""
        prev = self.file_store.load_latest("chapters",
                                            f"chapter_{chapter_index - 1:04d}_styled")
        if not prev:
            prev = self.file_store.load_latest("chapters",
                                                f"chapter_{chapter_index - 1:04d}")
        if prev:
            return prev[-500:] if len(prev) > 500 else prev
        return ""
