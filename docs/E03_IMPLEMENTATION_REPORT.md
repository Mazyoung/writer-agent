# E03 实施报告 — Hierarchical Planning / Planning Revision / Rollback Foundation

日期：2026-08-01
前置：E01（chapter_index round-trip）、E02（Writer world_setting 注入）已完成。
范围：E03-Core + E03-Replanning-Foundation。未实现 E04+（RAG、自动 L2/L3、自动 Rollback 等）。

---

## 0. 重要附带发现与修复（E03 测试驱动暴露）

在编写 E03 round-trip 测试时暴露了 **3 个影响全项目的预存在解析 bug**，均已修复
（不修复则 E03 的 Book/Volume round-trip 验收无法通过）：

| Bug | 根因 | 影响面 | 修复 |
|---|---|---|---|
| `_extract_section` 永不按标题截停 | f-string 中 `#{1,4}` 被 Python 求值为元组 `(1, 4)`，lookahead 变成匹配字面 `#(1, 4)` | **所有** `from_markdown` 的节提取都吞并到文件末尾：ChapterPlan Part B 各字段互相污染并混入 Part A 场景（Writer prompt 内容重复膨胀）、FactDigest 六节连写、追踪文档每次 review 后日志标题被误解析为关系条目 | 改为 `{{1,N}}` 字面量，并按 header 自身 `#` 数量计算停止级别（`##` 节止于 `#{1,2}`，保留 `###` 子节） |
| `_parse_key_value` 不容忍 `- ` 前缀 | 正则要求行首必须是 `**` | **所有场景的「发生什么/戏剧功能/信息增量/情绪曲线」等 kv 字段在生产中一直被解析为空字符串**——Writer 实际只拿到场景名，从未拿到场景规格 | 解析前剥去 `- ` 列表前缀 |
| 标题/事件名 round-trip 嵌套 | `from_markdown` 把整行（含「全书规划：《》」「事件N：」包装）当作 name/title | 每次 to_markdown→from_markdown 循环标题嵌套一层 | from_markdown 解包：BookPlan/VolumePlan 标题、VolumeFramework 卷名、VolumeEvent 事件名、ChapterSummary 章号+标题 |

这些修复包含在 E03 内，因为「ChapterPlanner 正确消费 Book Plan + Active Volume Plan」
与「Markdown round-trip」验收直接依赖它们。

---

## 1. 最终规划架构

```text
Book Plan        tracking/book_plan.md        战略层：初始化一次、默认稳定、仅 L3 可改
   ↓
Volume Plan      tracking/volume_plan.md      战术层：ACTIVE 当前卷（唯一运行时真相）
   ↓             tracking/volumes/volume_NN.md  已完成卷归档（历史，非运行时真相）
Chapter Plan     outlines/chapter_plan_chNNNN.md  执行层：每章生成一次，L2 可重生成
   ↓
Scene / Writer   chapters/                    执行：无规划修改权，只能报告问题
```

反向链路（Foundation 已建、未接通）：
```text
Execution → Supervisor → Planning Problem(L2/L3)
         → 旧停机状态 → Human Review → PlanRevision / StrategicRepairCase
         → (未来) Forward Repair / Rollback & Rewrite(StoryBranch + ChapterCheckpoint)
```

## 2. Book / Volume / Chapter 职责

| 层 | 保存 | 不保存 | 生命周期 |
|---|---|---|---|
| Book Plan | 核心目标、核心矛盾、结局方向、主角长期成长、**战略约束**、卷职责框架、全局伏笔、version | 章级事件、场景、对白、临时执行细节 | 初始化生成 → 长期稳定 → 仅 L3 Strategic Issue 可修改（高门槛） |
| Volume Plan | 本卷核心冲突/目标/障碍、**章节范围、关键里程碑、节奏约束**、事件链（含对应章节）、角色阶段成长、伏笔表、version、status | 不复制 Book Plan 内容 | Rolling Horizon：初始化只生成第 1 卷；卷完成后 `new-volume` 生成下一卷 |
| Chapter Plan | 章目标、场景规格、戏剧功能、信息增量、情绪曲线、Part B 上下文包 | 长期规划内容 | 每章生成一次；L2 问题可重新生成当前章 |

## 3. plot_structure.md 最终定位

**从运行时退休。** E03 起 `init --confirm` 不再生成 plot_structure.md；
PlotDesigner 直接产出 canonical `tracking/book_plan.md` + `tracking/volume_plan.md`
（任务书允许的实现路径：「PlotDesigner 更适合直接生成 Book Plan + Volume 1 Plan」）。
旧小说目录中的 plot_structure.md 保留为历史设计稿，运行时无消费者；
`migrate.py` 仍以它为迁移源（见 §18）。

运行时规则：ChapterPlanner 只消费 **一个 Book Plan + 一个 ACTIVE Volume Plan**，
不存在第二个表达同一规划状态的 canonical。

## 4. 修改文件

| 文件 | 变更 |
|---|---|
| `src/storage/document_formats.py` | BookPlan/VolumePlan 扩展字段 + 三个解析根因修复（§0） |
| `src/core/orchestrator.py` | `initialize_novel` 重写为 3 步分层生产链；新增 `start_new_volume`、`_recent_fact_digests`；status 显示活跃卷 |
| `src/agents/author/chapter_planner.py` | `_require_long_term_plans` 硬性检查、`load_active_volume`、prompt 按优先级重排、事实冲突 `[PLANNING CONFLICT]` 标注指令、交互路径同样加硬性检查 |
| `main.py` | 新增 `new-volume` 命令；plan 缺规划时友好报错；docstring 更新 |
| `ARCHITECTURE.md` | 顶部增加「当前实现状态（E03）」声明，主体标记为历史参考 |

## 5. 新增数据模型（`src/planning/`）

| 模型 | 关键字段 | 说明 |
|---|---|---|
| `PlanRevision` | revision_id, plan_type, base/new_version, trigger_chapter, reason, old/new_content(_ref), affected_nodes, status, approved_by/decision | 有业务语义的规划修改记录 ≠ .bak |
| `PlanningModificationReport` | report_id, problem, severity, affected_plan, current_plan, conflicting_actual_state, evidence, proposed_change, affected_future_nodes, risk_if_accept/reject, status | L2 修改报告，人工审批前置 |
| `StrategicRepairCase` | case_id, problem_summary, affected_scope/chapters/plan_nodes, evidence, last_safe_checkpoint, repair_options, selected_strategy, human_decision, branch_id, **requires_human 恒 True** | L3 战略修复案，`__post_init__` 强制 requires_human=True |
| `StoryBranch` | branch_id, parent_branch, fork_checkpoint, status(ACTIVE/ABANDONED/ARCHIVED), created_reason | 逻辑分支，非 Git 级版本系统 |
| `ChapterCheckpoint` | checkpoint_id, chapter_index, active_branch, 三个 plan version, chapter_file, memory/tracking/fact_digest version, status | 一章完成后整个世界状态的稳定提交 |
| `ReplanTriggerPolicy` | ALLOWED: fact_conflict / prerequisite_invalid / node_preempted / character_state_block / user_request / supervisor_l3；FORBIDDEN: writer_preference / more_exciting / style_change / scene_difficulty / speculation | 长期规划默认 Stable |
| `PlanningStore` | 五种模型的 JSON save/load/list | 每记录一文件，可 diff 可手改 |

## 6. canonical 文件路径

```text
novels/<novel_id>/
├── settings/world_setting.md          # 世界设定（_edited.md 优先）
├── tracking/
│   ├── book_plan.md                   # Book Plan（唯一战略真相）
│   ├── volume_plan.md                 # ACTIVE Volume Plan（唯一战术真相）
│   ├── volumes/volume_NN.md           # 已完成卷归档（COMPLETED）
│   ├── revisions/<id>.json            # PlanRevision
│   ├── replan_requests/<id>.json      # PlanningModificationReport
│   ├── strategic_repairs/<id>.json    # StrategicRepairCase
│   ├── checkpoints/<id>.json          # ChapterCheckpoint
│   └── branches/<id>.json             # StoryBranch
├── outlines/chapter_plan_chNNNN.md    # Chapter Plan（每章一个 canonical）
└── chapters/                          # 正文（时间戳版本）
```

取舍说明：按任务书「优先兼容现有路径」原则，保留 `tracking/` 而非新建 `planning/`；
foundation 子目录置于 `tracking/` 下，保持规划状态单一根目录。
`_edited.md` 人工覆盖机制（load_canonical 优先读 `_edited`）对所有规划文档继续有效。

## 7. init 数据流

```text
python main.py init <名> [前提]            # Phase 1: 创作提案 (proposal.md)
python main.py init <名> --confirm         # Phase 2:
  [1/3] WorldBuilder   → settings/world_setting.md
  [2/3] PlotDesigner   → tracking/book_plan.md      (Book Plan v1)
  [3/3] PlotDesigner   → tracking/volume_plan.md    (第1卷, v1, ACTIVE)
```

两个 PlotDesigner 调用在 user message 中给出与数据模型完全一致的
Markdown 格式模板（版本/状态/章节范围/里程碑/事件链含对应章节），
确保 `from_markdown` 可解析。

## 8. ChapterPlanner 新数据流

```text
plan → _require_long_term_plans()        # 缺 book/volume 即 FileNotFoundError + 迁移指引
     → 打印活跃卷 (第N卷 / status / 章节范围)
     → prompt 按优先级组装:
       1. 世界观设定（最高优先级·硬规则）[:2000]
       2. 全书战略规划 Book Plan（战略约束层）[:2000]
       3. 当前卷规划中本章对应事件（或 --outline 覆盖）
       4. 追踪文档（Part B 原材料）
       5. 前章事实摘要（已发生事实，优先于未来计划）
       6. 上一章结尾
       7. 作者额外指示
     → 尾部硬指令：规划与事实冲突时以事实为准，
       在「关键伏笔节点」标注 [PLANNING CONFLICT]，不得自行重写历史
```

## 9. Volume Rolling Planning 接口

```bash
python main.py new-volume <小说名> [--volume N] [--notes "..."]
```

`Orchestrator.start_new_volume()`：
1. 校验 book_plan/volume_plan 存在（缺失明确报错）；新卷号必须大于当前卷；
2. 旧卷 status→COMPLETED，归档 `tracking/volumes/volume_NN.md`；
3. PlotDesigner 以 Book Plan + 旧卷历史 + 近期事实摘要（Memory）+ 作者指示
   生成下一卷 → `tracking/volume_plan.md`（ACTIVE，唯一活跃卷）；
4. 写入 `PlanRevision`（plan_type=volume_plan, status=APPLIED,
   approved_by=human, trigger=user_request）——卷切换是有业务语义的规划变更。

不做自动卷结束识别（任务书允许：显式 new-volume 即可）。

## 10. Replanning Foundation

只有数据结构与持久化接口，**无自动消费者**。已接入的唯一真实生产者：
`new-volume` 写 PlanRevision（人工显式触发，非自动）。
`src/planning/models.py` 模块 docstring 固化：权限模型、L1/L2/L3 规则、
Rollback=Workflow State Rollback、Chroma invalidate+rebuild 决策。

## 11. L1/L2/L3 权限规则

| 级别 | 范围 | 处理 |
|---|---|---|
| L1 Execution Issue | 场景表达/文本逻辑 | Writer 自动重写；无需审批；**禁改任何 Plan** |
| L2 Planning Issue | Chapter Plan 不可执行 / Volume 局部节点调整 | 必须生成 Report → 旧停机状态 → **Human Review** → Accept/Edit+Confirm 后才创建 PlanRevision |
| L3 Strategic Issue | Volume/Book 战略失效、多章受影响、无唯一解 | **旧停机状态 PIPELINE**，禁止 Agent 自动修复 → 人机协同 Strategic Repair |

权限表（实现于代码注释与 models.py docstring）：Writer 只报告；
ChapterPlanner 只重生成当前章；StateManager 只可创建 Report/RepairCase（未来）；
PlotDesigner 经批准后改 Volume、L3 协同后改 Book；Human 拥有最终决定权。
未新增任何 Agent。

## 12. L2 Human Approval

`PlanningModificationReport.status` 状态机：PENDING → ACCEPTED / EDIT_CONFIRMED / REJECTED。
只有 ACCEPTED 或 EDIT_CONFIRMED 才允许派生 PlanRevision 并改 canonical Plan。
本轮实现状态机与持久化；审批 UI/命令属后续阶段。

## 13. L3 Human-Agent Collaborative Repair

`StrategicRepairCase`：`requires_human` 在 `__post_init__` 强制为 True（测试验证），
repair_options 支持 FORWARD_REPAIR / ROLLBACK_REWRITE / MANUAL_CUSTOM，
预留 last_safe_checkpoint 与 branch_id 字段。不实现自动检测与完整 workflow。

## 14. Checkpoint / Branch / Rollback Foundation

- `ChapterCheckpoint` 记录一章完成后的**完整 Workflow State**位置
  （三个 plan version、正文文件、memory/tracking/fact_digest 版本），
  是未来恢复的最小单位。
- `StoryBranch` 提供 ACTIVE/ABANDONED/ARCHIVED 逻辑分支语义；
  废弃分支内容只标记、不物理删除。
- Rollback 设计决策（models.py docstring）：Rollback ≠ restore markdown file；
  必须恢复正文+三层规划+角色/物品/修炼/伏笔状态+FactDigest+SQLite+未来 RAG 索引；
  ChromaDB 采用 invalidate future records + 从 active branch 重建索引，
  不做向量库 binary snapshot。

## 15. Plan Revision 与 Rollback 区别

| | Plan Revision | Rollback |
|---|---|---|
| 过去的故事 | **不变** | 某段被逻辑废弃 |
| 改变对象 | 仅未来 Planning State | 恢复旧 Checkpoint 完整世界状态 |
| 产物 | PlanRevision 记录 | 新 StoryBranch（旧分支 ABANDONED） |

## 16. 测试列表（新增 19，共 28）

`tests/test_planning_hierarchy.py`：
- BookPlan round-trip（version / 战略约束 / 核心目标 / 卷框架 / 伏笔保留）
- BookPlan 缺失字段默认值
- VolumePlan round-trip（volume_index / version / status / chapter_range / milestones / pacing / events 保留）
- VolumePlan 卷号从标题恢复
- init 生产链（mock LLM）：book_plan.md + volume_plan.md 生成、plot_structure.md 不再生成、字段可解析
- ChapterPlanner：prompt 含 World Setting / Book Plan / Current Volume Plan / 事实优先指令；识别 ACTIVE 第 2 卷；缺规划明确报错（含 migrate 指引）
- new-volume：旧卷归档 COMPLETED、新卷 ACTIVE、PlanRevision 落盘

`tests/test_planning_foundation.py`：
- 五个模型 to_dict/from_dict round-trip
- StrategicRepairCase.requires_human 强制为 True
- PlanningStore save/load/list + 路径约定 + 缺失返回 None
- ReplanTriggerPolicy allowed / forbidden

## 17. 测试结果

```
Ran 28 tests in 15.3s
OK   (28/28，含 E01/E02 既有 9 个测试，无回归)
```

附加验证：ChapterPlan Part B 字段隔离（角色关系不再混入后续节）、
场景 kv 字段（发生什么/情绪曲线）正确解析、CLI `new-volume` 出现在 help。

## 18. 对旧数据兼容情况

| 场景 | 行为 |
|---|---|
| 旧小说已有 tracking/book_plan.md + volume_plan.md（如「都在捡垃圾」） | 直接可用；新字段（version/status/章节范围/里程碑）取默认值（v1/ACTIVE/空） |
| 旧小说只有 plot_structure.md（如 kunlun_ruins） | `plan` 明确报错并指引 `python migrate.py <小说名>` 迁移（迁移脚本生成新格式文件，新字段取默认值）；**不再静默用空字符串继续** |
| 新小说 | init --confirm 直接产出完整分层规划 |
| plan/write/review 三命令 | 行为不变（write/review 路径未改动） |

## 19. 尚未实现的功能

- 自动 L2 / L3 检测（StateManager → Report/RepairCase 的生产者）
- L2 审批 CLI（Accept / Edit / Reject 操作入口）
- L3 Strategic Repair 完整 workflow（Forward Repair / Rollback & Rewrite）
- ChapterCheckpoint 的真实生产（review 完成后自动生成）与恢复
- StoryBranch 的真实分叉 / 切换
- RAG / ChromaDB 接入与重建
- ChapterPlan 标题 round-trip 嵌套（`plan.title` 无消费者，遗留）
- `plan_from_interactive_answers` 上下文仍比非交互路径窄（P1 #15，未在本轮范围）

## 20. 下一阶段 E04 RAG 需要的接口

- **写入侧**：章节定稿（write/review 完成点）需要稳定 hook 把正文/设定写入索引；
  建议在 review 完成处（未来 ChapterCheckpoint 生成点）统一触发。
- **检索侧**：ChapterPlanner prompt 组装点（`plan_chapter` 的 parts 列表）
  是检索结果的唯一注入点，按 §8 优先级插入「RAG 证据」区。
- **证据字段**：`PlanningModificationReport.evidence`、
  `StrategicRepairCase.evidence` 已预留，L2/L3 判断时由 RAG 提供历史证据。
- **重建接口**：Rollback 时按 models.py 决策 invalidate future records +
  从 active branch 重建；需要 ChromaStore 增加按 novel+branch/章节范围删除的能力。
- **去留前提**：E04 开始前需先决策审计问题 #3（ChromaStore 接线或移除）。
