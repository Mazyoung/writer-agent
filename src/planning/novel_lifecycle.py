"""Novel initialization and rolling-horizon lifecycle operations."""

import re

from src.agents.architect.plot_designer import PlotDesigner
from src.agents.architect.world_builder import WorldBuilder
from src.config.settings import get_settings
from src.core.novel_status import NovelStatusService
from src.planning.models import PlanRevision, PlanType, RevisionStatus
from src.planning.store import PlanningStore
from src.planning.trigger_policy import ReplanTrigger
from src.storage.current_state_store import CurrentStateStore
from src.storage.document_formats import BookPlan, VolumePlan
from src.storage.file_store import FileStore
from src.storage.sqlite_store import SQLiteStore


class NovelLifecycleService:
    """Manage proposal initialization and explicit volume transitions."""

    def __init__(self, novel_id: str):
        settings = get_settings()
        self.novel_id = novel_id
        self.file_store = FileStore(novel_id, settings.data_dir)
        migrated = self.file_store.migrate_legacy_canonical_if_needed()
        if migrated:
            print(f"  [migration] canonical copies created: {list(migrated.keys())}")
        self.world_builder = WorldBuilder(novel_id)
        self.plot_designer = PlotDesigner(novel_id)

    def _completed_chapters(self) -> int:
        status = NovelStatusService(self.novel_id).get_status()
        return status.get("completed_chapters", 0)

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
        - tracking/volume_plan.md （卷级路径，DRAFT 经人工确认后 ACTIVE）
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

## World Setting
{ws[:4000]}

## Book Plan
{bp.to_markdown()[:5000]}

---
生成第一卷的卷级大故事路径。不要规定章节范围，不要把事件分配到具体章节，
不要输出逐章事件链。严格输出：

# 第1卷规划：《卷名》
- **版本**: v1
- **状态**: DRAFT

## 起始状态
## 本卷目标
## 主要冲突
## 故事阶段/路径
- 阶段级路径，不绑定章节
## 关键转折
- 卷级转折，不绑定章节
## 限制条件
## 目标结束状态
"""
        volume_plan = self.plot_designer.run(
            user_message=volume_prompt,
            save_category="tracking",
            save_prefix="volume_plan",
            use_canonical=True,
        ).content
        self._validate_volume_candidate(volume_plan, 1, expected_status="DRAFT")

        sqlite = SQLiteStore(self.file_store.root / "state.db")
        try:
            CurrentStateStore(
                self.novel_id, self.file_store, sqlite
            ).ensure_initialized()
        finally:
            sqlite.close()

        print(f"\n初始化完成！")
        print(f"  settings/world_setting.md")
        print(f"  tracking/book_plan.md   (Book Plan v1 / 战略层)")
        print(f"  tracking/volume_plan.md (Volume 1 Plan v1 / DRAFT)")
        print(f"  tracking/current_state.md (generated present state)")
        print(f"\n下一步: 直接审阅并编辑上述原文件，然后 approve-volume，")
        print(f"        确认 ACTIVE 后运行: python main.py plan {self.novel_id} --chapter 1")
        return {"world_setting": world_setting, "book_plan": book_plan,
                "volume_plan": volume_plan}

    @staticmethod
    def _with_volume_status(text: str, status: str) -> str:
        pattern = r'(\*\*状态\*\*\s*[:：]\s*)\S+'
        if not re.search(pattern, text):
            raise ValueError("volume_plan.md is missing its status metadata")
        return re.sub(pattern, rf'\g<1>{status}', text, count=1)

    def close_volume(self) -> str:
        """Explicitly close the ACTIVE volume; progress advice is irrelevant."""
        text = self.file_store.load_tracking_doc("volume_plan") or ""
        if not text:
            raise FileNotFoundError("tracking/volume_plan.md does not exist")
        plan = VolumePlan.from_markdown(text)
        if plan.status.upper() == "COMPLETED":
            return text
        if plan.status.upper() != "ACTIVE":
            raise ValueError(f"Only an ACTIVE volume can close; current status is {plan.status}")
        chapters = self.file_store.list_chapters()
        if not chapters:
            raise ValueError(
                "close-volume requires a canonical chapter whose derivation is DERIVED_READY"
            )
        match = re.fullmatch(r"chapter_(\d{4})\.md", chapters[-1].name)
        if match is None:
            raise ValueError("Cannot determine the latest canonical chapter")
        latest_chapter = int(match.group(1))
        from src.workflows.chapter_runner import ChapterWorkflowRunner
        status = ChapterWorkflowRunner(
            self.novel_id, latest_chapter
        ).get_workflow_status()
        if status != "DERIVED_READY":
            raise ValueError(
                f"Latest canonical chapter {latest_chapter} is {status or 'UNKNOWN'}, "
                "not DERIVED_READY; run derivation repair before close-volume"
            )
        completed = self._with_volume_status(text, "COMPLETED")
        archive = (
            self.file_store.root / "tracking" / "volumes" /
            f"volume_{plan.volume_number:02d}.md"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(completed, encoding="utf-8")
        (self.file_store.root / "tracking" / "volume_plan.md").write_text(
            completed, encoding="utf-8")
        print(f"  [Close Volume] 第{plan.volume_number}卷已由人工命令关闭。")
        return completed

    def start_new_volume(self, volume_number: int | None = None,
                         notes: str = "") -> str:
        """Generate the next volume into the sole volume_plan.md as DRAFT."""
        previous_text = self.file_store.load_tracking_doc("volume_plan") or ""
        book_plan = self.file_store.load_tracking_doc("book_plan") or ""
        world_setting = self.file_store.load_canonical("settings", "world_setting") or ""
        current_state = self.file_store.load_generated_tracking_doc("current_state") or ""
        if not all([previous_text, book_plan, world_setting, current_state]):
            raise FileNotFoundError(
                "Next-volume planning requires World Setting, Book Plan, Previous "
                "Volume Plan, and Current State")
        previous = VolumePlan.from_markdown(previous_text)
        if previous.status.upper() != "COMPLETED":
            raise ValueError("Close the current volume before planning the next volume")
        new_index = volume_number or previous.volume_number + 1
        if new_index <= previous.volume_number:
            raise ValueError("Next volume number must be greater than the closed volume")

        prompt = f"""## World Setting
{world_setting[:5000]}

## Book Plan
{book_plan[:5000]}

## Previous Volume Plan (COMPLETED)
{previous_text[:5000]}

## Current State after Previous Volume
{current_state[:5000]}

## Optional User Next-Volume Intent
{notes or "（无）"}

---
Generate Volume {new_index} as a volume-level big story path. Do not assign
specific events to chapters, do not write chapter ranges, and do not create a
per-chapter outline. Output exactly this Markdown structure:

# 第{new_index}卷规划：《卷名》
- **版本**: v1
- **状态**: DRAFT

## 起始状态
## 本卷目标
## 主要冲突
## 故事阶段/路径
- 阶段级路径，不绑定章节
## 关键转折
- 卷级转折，不绑定章节
## 限制条件
## 目标结束状态
"""
        candidate = self.plot_designer.run(
            user_message=prompt,
            save_category="tracking",
            save_prefix=f"candidate_volume_{new_index:02d}",
            use_canonical=False,
        ).content
        self._validate_volume_candidate(candidate, new_index, expected_status="DRAFT")
        self.file_store.save_canonical("tracking", "volume_plan", candidate)
        print("  [Next Volume] tracking/volume_plan.md 已生成，状态 DRAFT；请直接编辑原文件后 approve-volume。")
        return candidate

    def approve_volume(self) -> str:
        """Validate the directly edited DRAFT and change only its status token."""
        path = self.file_store.root / "tracking" / "volume_plan.md"
        if not path.exists():
            raise FileNotFoundError("tracking/volume_plan.md does not exist")
        text = path.read_text(encoding="utf-8")
        plan = VolumePlan.from_markdown(text)
        if plan.status.upper() == "ACTIVE":
            return text
        self._validate_volume_candidate(text, plan.volume_number, expected_status="DRAFT")
        active = self._with_volume_status(text, "ACTIVE")
        path.write_text(active, encoding="utf-8")
        print(f"  [Approve Volume] 第{plan.volume_number}卷已切换为 ACTIVE。")
        return active

    @staticmethod
    def _validate_volume_candidate(text: str, expected_index: int,
                                   expected_status: str = "DRAFT") -> VolumePlan:
        if not text or not text.strip():
            raise ValueError("Volume Planner returned empty content")
        plan = VolumePlan.from_markdown(text)
        problems = []
        if plan.volume_number != expected_index:
            problems.append(f"expected volume {expected_index}, got {plan.volume_number}")
        if plan.status.upper() != expected_status:
            problems.append(f"status must be {expected_status}, got {plan.status}")
        if not plan.title.strip():
            problems.append("missing title")
        if not plan.starting_state.strip():
            problems.append("missing starting state")
        if not plan.volume_goal.strip():
            problems.append("missing volume goal")
        if not plan.core_conflict.strip():
            problems.append("missing primary conflict")
        if not plan.story_path:
            problems.append("missing story path")
        if not plan.target_end_state.strip():
            problems.append("missing target end state")
        structural_patterns = {
            "章节范围": (
                r"(?:^|\n)\s*(?:#{1,6}\s*章节范围\s*|"
                r"(?:[-*]\s*)?(?:\*\*)?章节范围(?:\*\*)?\s*[:：])"
            ),
            "逐章事件表": (
                r"(?:^|\n)\s*(?:#{1,6}\s*逐章事件表\s*|"
                r"(?:[-*]\s*)?(?:\*\*)?逐章事件表(?:\*\*)?\s*[:：])"
            ),
            "事件对应章节": (
                r"(?:^|\n)\s*(?:#{1,6}\s*(?:事件对应章节|对应章节)\s*|"
                r"(?:[-*]\s*)?(?:\*\*)?(?:事件对应章节|对应章节)"
                r"(?:\*\*)?\s*[:：])"
            ),
            "chapter assignment": (
                r"(?:^|\n)\s*(?:#{1,6}\s*chapter assignments?\s*|"
                r"(?:[-*]\s*)?(?:\*\*)?chapter assignments?"
                r"(?:\*\*)?\s*[:：])"
            ),
        }
        found = [
            label for label, pattern in structural_patterns.items()
            if re.search(pattern, text, re.IGNORECASE)
        ]
        table_rows = [
            line for line in text.splitlines()
            if line.lstrip().startswith("|")
        ]
        if any(
            re.search(r"\|\s*(?:章节|chapter)\s*\|", row, re.IGNORECASE)
            or re.search(
                r"\|\s*(?:对应章节|chapter assignment)\s*\|",
                row,
                re.IGNORECASE,
            )
            for row in table_rows
        ):
            found.append("chapter assignment table")
        if found:
            problems.append(
                "chapterized structures are forbidden: "
                + ", ".join(dict.fromkeys(found))
            )
        if problems:
            raise ValueError("Invalid Volume Plan:\n  - " + "\n  - ".join(problems))
        return plan

    def _recent_fact_digests(self, count: int = 3) -> str:
        """扫描 states/ 取最近 N 个事实摘要（按文件名排序即按章号+时间排序）。"""
        states_dir = self.file_store.root / "states"
        files = sorted(states_dir.glob("fact_digest_ch*_*.md"))[-count:]
        parts = []
        for f in files:
            parts.append(f"## {f.stem}\n{f.read_text(encoding='utf-8')[:1200]}")
        return "\n\n".join(parts)
