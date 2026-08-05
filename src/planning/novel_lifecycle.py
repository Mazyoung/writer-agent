"""Novel initialization and rolling-horizon lifecycle operations."""

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
        print(f"  tracking/volume_plan.md (Volume 1 Plan v1 / ACTIVE)")
        print(f"  tracking/current_state.md (generated present state)")
        print(f"\n下一步: 人工审阅上述文件（可保存为 *_edited.md 覆盖），")
        print(f"        然后运行: python main.py plan {self.novel_id} --chapter 1")
        return {"world_setting": world_setting, "book_plan": book_plan,
                "volume_plan": volume_plan}

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
                "\n旧数据: 运行 python scripts/migrate_legacy_data.py <小说名> "
                "从 plot_structure.md 迁移。")

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
                trigger_chapter=f"第{self._completed_chapters()}章后",
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
            # E06.2.1: 回滚时保护原始异常——rollback 失败不得掩盖根因。
            rollback_errors: list[str] = []
            if canonical_attempted:
                try:
                    self.file_store.rollback_canonical("tracking", "volume_plan")
                except Exception as re:
                    rollback_errors.append(
                        f"rollback_canonical 也失败: {type(re).__name__}: {re}")
            if archive_written and archive_path.exists():
                try:
                    archive_path.unlink()
                except Exception as ue:
                    rollback_errors.append(
                        f"删除归档也失败: {type(ue).__name__}: {ue}")
            detail = (f"新卷提交失败，已回滚: 第{old_vp.volume_number}卷仍为 ACTIVE。"
                      f"\n根因: {type(e).__name__}: {e}")
            if rollback_errors:
                detail += "\n回滚错误: " + "; ".join(rollback_errors)
            raise RuntimeError(detail) from e

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
