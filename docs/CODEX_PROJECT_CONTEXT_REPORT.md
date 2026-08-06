# Writer-Agent 项目上下文报告

生成日期：2026-08-05  
范围：只读接入与现状核对；除本报告外未修改项目代码或测试。

## 1. 结论摘要

当前生产入口仍是 `main.py`，由 CLI 直接构造并调用 `src.core.orchestrator.Orchestrator`。LangGraph 已以旁路工作流接入 `src/workflows/chapter_workflow.py`，但没有接管生产入口，也没有 checkpoint/resume、HITL interrupt 或 revision loop。

提交态的 E07 基线是 E07.2 PASS happy path；当前工作区存在一处未提交的 `chapter_workflow.py` 修改，已开始实现 E07.3 条件路由。该修改尚未与 `tests/test_e07.py` 同步，当前 E07 测试为 66 项中 6 failures、5 errors，因此不能把 E07.3 视为已完成或已验收。

核心业务闭环已经存在于生产 Orchestrator 中：styled chapter → 单次 review LLM → deterministic decision parsing → 仅 PASS 执行 canonical structured-memory 原子提交 → deterministic Fact Digest → Chroma RAG 索引。任何非 PASS、解析 UNKNOWN 或 canonical commit 失败都会阻断 Fact Digest 与 RAG；RAG 自身失败不回滚已成功的 canonical state。

## 2. 当前架构

### 2.1 生产入口与 Orchestrator

- `main.py` 是唯一生产 CLI 入口，提供 `init`、`status`、`plan`、`write`、`style`、`review`、`new-volume`、`rag-index`。
- `main.py` 不导入 LangGraph workflow；所有生产命令调用 `Orchestrator`。
- `Orchestrator` 负责装配 FileStore、SQLiteStore、各 Agent 与 lazy ChromaStore，并协调：
  - proposal 生成及 human override 读取优先级；
  - world setting、Book Plan、Volume Plan 初始化；
  - chapter plan、RAG retrieval trace；
  - draft → style → styled save → deterministic style check；
  - review、decision routing、canonical commit、Fact Digest、RAG index；
  - new-volume 的归档与事务式切换；
  - status 和 RAG backfill/rebuild。
- `Orchestrator` 仍偏重，尤其 `review_chapter()` 聚合了 review、parse、route、commit、digest、index 等职责；LangGraph 正在用 adapter node 逐步拆解编排边界，而不是复制业务逻辑。

### 2.2 Agent 与服务边界

- `WorldBuilder`：世界观生成。
- `PlotDesigner`：Book Plan / Volume Plan 生成。
- `ChapterPlanner`：消费 Book → Volume → Chapter 三层规划、structured memory、近期 Fact Digest、上一章结尾与 RAG evidence，生成 Chapter Plan。
- `DeepSeekWriter`：根据 ChapterPlan 写 draft。
- `ClaudeStylist`：风格编辑；整章 workflow 中返回文本，保存由上层统一负责。
- `StyleChecker`：确定性风格检查。
- `StateManager`：单次 review 分析、ReviewDecision 解析、State Delta/Change Log 解析、四份 structured-memory 文档原子提交、SQLite cache 同步、Fact Digest 确定性提取。
- `ChapterRetrievalService`：LangGraph 路径中的确定性 query、Chroma search、evidence 格式化与 retrieval trace 生命周期。

### 2.3 LangGraph 接入程度

已接入：

- `langgraph>=1.2,<2` 已列入依赖。
- `ChapterWorkflowState` TypedDict 与可编译、可 invoke 的 StateGraph 存在。
- E07.2 的 adapter nodes 覆盖 plan、draft、style、save styled、review、parse、commit、Fact Digest、RAG。
- 历史 E07.2 报告和测试包含 mocked 完整 `graph.invoke()` happy-path 验证；这是图运行验证，不是外部模型与真实持久化环境的生产端到端验证。

未接入：

- `main.py`/生产 CLI 尚未切换到 Graph。
- 没有 LangGraph checkpointer；不存在 `SqliteSaver`、`MemorySaver` 或 `InMemorySaver`。
- 没有 resume、`interrupt()`、`Command(resume=...)`、HITL 或 revision loop。
- PlanningStore 中的 `ChapterCheckpoint` 是 E03 业务模型/预留接口，不等于 LangGraph checkpoint，也未驱动生产恢复。

当前未提交工作区状态：

- Graph 已改为 E07.3 风格：增加 `preflight`、conditional edges、`stop_non_pass`，移除临时 `require_pass` node。
- 这只是未提交实现草稿；测试仍要求 E07.2 线性 topology 与 `require_pass`，尚未闭环。

### 2.4 当前 ChapterWorkflow topology

提交态 E07.2（文档化基线）：

```text
START → plan_chapter → write_draft → style_edit → save_styled
      → review_chapter → parse_decision → require_pass
      → commit_state → save_fact_digest → rag_index → END
```

当前工作区实际源码（未提交 E07.3 草稿）：

```text
START → preflight
  → plan_chapter → write_draft → style_edit → save_styled
  → review_chapter → parse_decision
      PASS                 → commit_state
      NEEDS_REVISION/旧停机状态  → stop_non_pass → END
      UNKNOWN/error        → END
  → commit success → save_fact_digest → rag_index → END
  → commit/digest error                         → END
```

前置及普通节点后均通过 conditional edge 在 `workflow_status == error` 时直接终止。

### 2.5 State schema

`ChapterWorkflowState` 为 `TypedDict(total=False)`，当前字段分组如下：

- identity：`novel_id`、`branch_id`、`chapter_index`
- plan inputs：`chapter_outline`、`extra_instructions`
- flow data：`chapter_plan_text`、`draft_text`、`styled_text`、`raw_analysis`
- decision：`verdict`、`review_reasons`、`t1_issues`、`planning_level`
- commit：`commit_success`、`commit_error`、`completion_marker_path`
- retrieval/results：`retrieval_success`、`retrieval_result_count`、`retrieval_trace_path`、`warnings`、`fact_digest_generated`、`rag_chunks`
- status：`workflow_status`、`error`

该 schema 是 workflow execution state，不替代 Markdown canonical state。

## 3. 数据设计

### 3.1 生产内容

- Proposal：小说根目录 `proposal.md`；作者直接审阅和编辑该文件，`init --confirm` 读取其当前内容。
- World Setting：`settings/world_setting.md`，canonical Markdown。
- Book Plan：`tracking/book_plan.md`，战略层、长期稳定。
- Volume Plan：`tracking/volume_plan.md`，当前 ACTIVE 战术层；历史卷归档至 `tracking/volumes/volume_NN.md`。
- Chapter Plan：`outlines/chapter_plan_chNNNN.md`，执行层 canonical planning state。
- Chapter Content：draft 为 `chapters/chapter_NNNN_draft_*.md`；styled 为 `chapters/chapter_NNNN_styled_*.md`。Review 只接受 styled 文本。

### 3.2 自动报告与诊断产物

- Review：`states/review_chNNNN_*.md`，保存 raw analysis；即使 verdict 非 PASS 也作为诊断记录保留。
- Fact Digest：`states/fact_digest_chNNNN_*.md`，从同一次 raw analysis 确定性提取，0 额外 LLM；仅在 PASS 且 canonical commit 成功后生成。
- Retrieval Trace：`tracking/rag_traces/retrieval_trace_chNNNN_*.json`，记录 query、filters、results、distance、success/error；retrieval 失败时尽量保存 failed trace。
- Post Chapter Update：`states/post_chapter_update_chNNNN_*.md`，由 StateManager 在 structured-memory 更新流程中保存 change log。
- Completion Marker：`states/chapter_NNNN_completed`，与四份 tracking docs 在 canonical transaction 中一并处理；Graph commit node 也核验 marker 存在。

### 3.3 Canonical structured memory

四份 Markdown 是 canonical story state，必须 ALL OLD 或 ALL NEW：

- `tracking/character_relationships.md`
- `tracking/items_equipment.md`
- `tracking/cultivation_system.md`
- `tracking/character_states.md`

提交过程为 LOAD/PARSE/BUILD/PREPARE snapshot/COMMIT/ROLLBACK；snapshot read、parse、任一写入失败均 fail-closed。SQLite 同步只发生在 Markdown 成功后，SQLite 失败不回滚 canonical Markdown。

### 3.4 程序状态

- SQLite：每部小说的 `state.db`，当前主要缓存 foreshadowing 与 character state；属于 secondary/cache，可由 canonical state 重建，不是 canonical source of truth。
- Chroma：全局 `data/chroma_db` 下的 `chapter_chunks` collection；只索引 styled 历史章节，使用 stable chunk IDs，并按 novel、branch、`chapter_index < current`、source type 过滤。当前 runtime 固定 `branch_id=main`，动态 branch semantics 尚未实现。
- LangGraph checkpoint：当前不存在。PlanningStore 的 JSON `checkpoints/` 与 `ChapterCheckpoint` 只是业务模型基础，不是 LangGraph saver。

## 4. Review → Commit → Fact Digest → RAG 闭环

生产 Orchestrator 的闭环：

```text
styled chapter
  → StateManager.review_chapter()               [1 LLM]
  → ReviewDecision.from_analysis()              [0 LLM, fail-closed]
  → PASS?
      no  → no memory commit / no Fact Digest / no RAG
      yes → update_tracking_docs()
              → parse state deltas/change logs  [0 LLM]
              → atomic 4-doc + marker commit
              → SQLite cache sync after Markdown success
           → StateCommitResult.success?
              no  → workflow ERROR / no Fact Digest / no RAG
              yes → Fact Digest                 [0 LLM]
                  → index styled chapter to Chroma
```

边界：RAG 是 derived state。RAG 写失败会记录 warning/error，但不会回滚已经成功的 canonical commit。Fact Digest 保存位于 commit 与 RAG 之间；当前 Graph 中若 digest node 自身报告 error，会在进入 RAG 前停止。

## 5. E07 状态核对

### E07.1 — StateGraph skeleton

已完成（提交态能力）：TypedDict state、StateGraph skeleton、compile/invoke 基础、旁路存在、不修改生产入口。

### E07.2 — PASS happy path

已完成的提交态能力：

- adapter-node PASS chain；
- PASS guard（E07.2 为 `require_pass`；当前未提交草稿改为 conditional routing + commit defense-in-depth）；
- canonical commit 复用 `StateManager.update_tracking_docs()`，保持 ALL OLD/ALL NEW；
- Fact Digest 从 raw analysis 确定性提取；
- RAG retrieval、trace 与 index；
- mocked 完整 `graph.invoke()` happy-path 测试，以及 commit failure、non-PASS guard、RAG non-blocking 等测试。

需要准确限定的“real runtime verification”：

- 有真实 LangGraph compiled graph 的 `invoke()`，但 Agent/存储依赖在测试中被 mock。
- 未发现使用真实外部 LLM、真实 Chroma embedding 与真实小说数据跑通整条 Graph 的受控证据。
- 生产 CLI 仍未调用 Graph，所以不能称为生产 runtime cutover 验证。

### E07.3 及以后

- E07.3 conditional routing：当前只有未提交源码草稿，测试未同步，不能视为完成。
- E07.4 checkpoint/resume：未实现。
- E07.5 HITL interrupt/resume：未实现。
- E07.6 revision loop：未实现。
- 自动 L2/L3 plan revision、strategic repair、rollback/branch workflow：只有模型、store、trigger policy 或 prompt 检测基础，没有完整自动 workflow。

## 6. 已完成能力

- Proposal 人工 override 优先级与初始化保护。
- World Setting → Book Plan → Active Volume Plan → Chapter Plan 分层规划。
- new-volume 归档/切换及失败回滚保护。
- Chapter draft、style、single-save、deterministic style check。
- Review 单次 LLM invariant，确定性 decision/fact digest parsing。
- UNKNOWN fail-closed；显式 PASS 遇 T1/MAJOR 可提升为 NEEDS_REVISION。
- 四份 structured-memory Markdown 原子提交与 rollback。
- commit failure 阻断 Fact Digest/RAG。
- SQLite secondary cache 同步与显式 warning。
- styled-only RAG corpus、deterministic chunking、stable IDs、future leakage prevention、novel/branch filtering、retrieval trace、backfill/rebuild abort semantics。
- E07.1 与提交态 E07.2 的旁路 Graph 基础。

## 7. 未完成能力

- LangGraph 生产入口切换。
- 已验收的 E07.3 conditional routing（当前仅未提交且测试失败的草稿）。
- LangGraph persistent checkpoint/resume。
- HITL interrupt/resume。
- NEEDS_REVISION 自动 rewrite/re-review loop。
- 旧停机状态 后的自动/协作 replan workflow。
- 完整 rollback、branch switching 与 future-state invalidation/rebuild。
- 自动应用 L2/L3 PlanningModificationReport / StrategicRepairCase。
- Hybrid Search、BM25、reranker、query rewrite、GraphRAG 等高级检索。

## 8. 当前风险点

1. **工作区不一致**：`src/workflows/chapter_workflow.py` 已改为 E07.3 草稿，`tests/test_e07.py` 仍是 E07.2 contract。实测 66 项 E07 测试中 6 failures、5 errors。
2. **文档滞后**：README 与 E07.2 report 仍表示 conditional routing 未实现；与未提交源码不一致。应先决定保留/完成还是撤回该草稿，再同步文档。
3. **生产/Graph 双路径漂移**：Orchestrator 与 Graph adapter 同时维护 retrieval、review、commit/index 编排，已有 `ChapterRetrievalService` 用于收敛一部分重复，但行为等价仍需持续测试。
4. **checkpoint 术语混淆**：PlanningStore 的 `ChapterCheckpoint` 容易被误认为 LangGraph checkpoint；二者目前没有连接。
5. **非幂等 side effects**：timestamped plan/draft/styled/review/digest/trace 在未来 replay/retry 中可能重复；E07.4 前必须定义 node replay 语义。
6. **SQLite 资源警告**：E07 测试输出多次 unclosed sqlite connection `ResourceWarning`。
7. **本地 venv 失效**：`venv` 指向不存在的 `C:\Python314\python.exe`；当前可用的是 `E:\code\miniconda\envs\writer\python.exe`。Conda wrapper 又会因 GBK 无法编码替换字符而报 `UnicodeEncodeError`，直接调用环境解释器可运行测试。
8. **编码显示风险**：PowerShell/Conda 输出存在 GBK/UTF-8 mojibake；源码本身可按 UTF-8 读取，但终端诊断可读性较差。
9. **旧危险接口残留**：`Orchestrator.rollback_chapter()` 仍直接删除章节相关文件并改写 volume plan，虽已从 CLI 移除但属于可调用代码；它不是完整 workflow rollback，后续不应误用。
10. **RAG stale cleanup 行为**：`index_chapter()` 对 stale delete 失败只 warning 后继续 add，可能造成 ID 冲突或旧数据残留；rebuild clear 已 fail/abort，但单章 re-index 的语义仍需关注。

## 9. Git 状态（报告生成前）

- 当前分支：`fix/review-decision-parser`
- 上游：`origin/fix/review-decision-parser`
- 未提交修改：`M src/workflows/chapter_workflow.py`
- 最近 10 个提交：
  1. `32d6a47 0.72结束`
  2. `b15d86b 0.72`
  3. `ae32fd0 E07.2 Final test`
  4. `65e4341 E07.2: close Graph RAG observability gaps`
  5. `b94958f fix: harden E07.2 graph failure handling`
  6. `84d98a2 chore: clean legacy runtime and align repository docs`
  7. `94e7c4c Add runtime demo artifacts for agent workflow showcase`
  8. `e2e4959 Fix truncated review decision parsing`
  9. `633df63 E07.2: Proposal Human Override 优先级修复`
  10. `3a16484 E07.2 Closure Patch: RAG parity, branch removal, commit guard, graph.invoke test`

本报告新增后，Git 状态还会多出未跟踪的 `docs/CODEX_PROJECT_CONTEXT_REPORT.md`；未执行 commit。

## 10. 后续开发注意事项与建议

1. 首先把当前未提交 E07.3 草稿作为独立工作项收口：更新 topology/node contract 测试，补齐 PASS、NEEDS_REVISION、旧停机状态、UNKNOWN、node error、commit failure、digest failure、RAG failure 的 graph-level invoke 验证；全部通过后再更新 README/E07 报告。
2. 在 E07.3 通过前，不切换 `main.py`，不引入 checkpoint/HITL，避免把多阶段问题混在一起。
3. E07.4 前先定义每个 timestamped side-effect node 的 replay/idempotency contract，并区分 workflow checkpoint 与 canonical state。
4. 保持 `StateManager.update_tracking_docs()` 为唯一 canonical structured-memory 提交入口，不能在 Graph 中复制 State Delta parser 或多文件事务。
5. 保持 `StateCommitResult.success` 为 downstream gate；commit/parse/snapshot/marker 任一失败都不得生成 Fact Digest 或进入 RAG。
6. 修复或统一项目运行环境与 UTF-8 输出后，再建立可重复的全量回归命令；同时处理 SQLite connection 生命周期警告。
7. 在声称“真实 runtime 验证”前，增加一个隔离测试小说的受控端到端 Graph smoke test，明确记录是否使用真实 LLM、真实 Chroma、真实 SQLite/文件系统以及产物清单。
