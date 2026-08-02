# E07 LangGraph Migration Guide — 长期指导文件

版本：v2.0（E07.0 Preflight 产出，已修正）
受众：E07.1–E07.6 各轮实施 Agent
状态：**E07 各轮执行前必须读取本文件**

---

## 1. 核心迁移原则

### Principle 1: Adapter Node 优先

优先使用 Adapter Node 调用现有已经稳定的 Agent / Service / workflow component。

LangGraph migration 与 business logic refactor 尽量分离。

```
Graph Node = workflow adapter / orchestration boundary
Existing Agent / Service = business logic owner
```

如果某个现有 method 含有会影响 checkpoint/retry safety 的非幂等 side effect，
在对应 persistence 阶段前再进行有针对性的拆分。

不要为了 Graph 节点"更纯"而在 E07.1/E07.2 提前重构整个 Orchestrator 或 BaseAgent。

```text
write_node → DeepSeekWriter.write_chapter()
style_node → ClaudeStylist.edit_chapter()
review_node → StateManager.review_chapter()
```

**禁止**复制第二套 business logic。

### Principle 2: Canonical State ≠ Checkpoint

LangGraph checkpoint 是 Workflow Execution State。
Canonical Story State 是本项目的业务状态（Structured Memory + styled chapters）。

```
LangGraph checkpoint → 恢复 workflow 执行位置
Canonical Story State → 驱动 Planning 和 RAG
```

两者独立管理。不要用 checkpoint 替代 canonical state commit。

### Principle 3: Chroma 是 Derived State

```
Chroma RAG → 可从 styled chapters 完整重建
SQLite → 可从 canonical tracking docs 重建
Fact Digest → 可从 raw_analysis 重建
```

Canonical Markdown files 是唯一的不可重建状态。
Derived state 失败不触发 canonical rollback。

### Principle 4: Fail-Closed 优先

所有 Node 默认 fail-closed。缺失的结果（missing result）≠ 成功。
这是 E06.2.1 建立的核心约束，LangGraph 中由 conditional edge 实现。

### Principle 5: Agent / Service ≠ Graph Node

```
Agent / Service instance
!=
Graph Node
```

Graph Node 可以调用已有 Agent：

```text
write_node → DeepSeekWriter
style_node → ClaudeStylist
review_node → StateManager
```

但 LangGraph 不负责要求"每个 Agent 都是一个 Node"，
也不要求 Graph 管理 Agent 生命周期。

Agent 可以作为 ChapterWorkflow / Orchestrator 的依赖被注入。
不要因此引入新的 DI framework 或 lifecycle framework。

---

## 2. Stable Architecture Constraints

以下约束在 E07 全阶段不得改变：

### 2.1 Canonical State Hierarchy（不可改变）

```
Book Plan → Volume Plan → Chapter Plan → Scene/Execution
```

### 2.2 Structured Memory Atomicity（不可改变）

4 个 canonical tracking docs 必须在单一事务中提交：
```
character_relationships, items_equipment, cultivation_system, character_states
```

约束：ALL OLD or ALL NEW。绝不 PARTIAL NEW。

### 2.3 Commit Failure Blocking（不可改变）

```
commit failure → no Fact Digest → no RAG → workflow ERROR
```

### 2.4 Review Gate（不可改变）

Review 只接受 styled chapter。Review semantic PASS 不等于 commit success。

### 2.5 Planning Modification Authority（不可改变）

```
L1: Writer 可自修复，不修改 Plan
L2: 需要 Human Review → PlanRevision
L3: HALT PIPELINE，Human-Agent 协同修复
```

---

## 3. Current Side Effects — Node Mapping

基于 E07.0 Preflight 的 Candidate Nodes：

| Node | Calls | Writes | Canonical? | Idempotency |
|---|---|---|---|---|
| `retrieve_history` | ChromaStore.search() | 1 trace JSON | Workflow | timestamp→retry 重复可接受 |
| `plan_chapter` | ChapterPlanner.plan_chapter() [1 LLM] | 1 plan .md | Plan | timestamp→`load_latest()` 取最新 |
| `write_draft` | DeepSeekWriter.write_chapter() [1 LLM] | 1 draft .md | Working | timestamp→`load_latest()` 取最新 |
| `style_edit` | ClaudeStylist.edit_chapter() [1 LLM] | —（仅返回 str） | — | 纯函数化 |
| `save_styled` | StyleChecker.check_all() [0 LLM] | 1 styled .md | Story | 同内容幂等写入 |
| `review_chapter` | StateManager.review_chapter() [1 LLM] | 1 analysis .md | Workflow | 拆分 LLM 与 save |
| `parse_decision` | ReviewDecision.from_analysis() [0 LLM] | — | — | ✅ 天然幂等 |
| `parse_state_delta` | _parse_state_deltas() [0 LLM] | — | — | ✅ 天然幂等 |
| `commit_state` | _commit_all_tracking_docs() [0 LLM] | 4 canonical .md | Story | 原子事务+回滚 |
| `save_fact_digest` | extract_fact_digest_from_analysis() [0 LLM] | 1 fact_digest .md | Derived | timestamp→retry 重复可接受 |
| `sync_sqlite` | upsert_foreshadow + _sync_sqlite [0 LLM] | SQLite × N | Cache | ✅ upsert 幂等 |
| `rag_index` | ChromaStore.index_chapter() | ChromaDB | Derived | ✅ stable chunk IDs |

---

## 4. Migration Phase Plan（固定路线）

### E07.1 — Graph State + StateGraph Skeleton

- 定义 `GraphState` TypedDict
- 构建 StateGraph skeleton
- **旁路运行**（不替换当前 runtime）
- 验证 graph 可编译、可 invoke（空 Node 或 echo）
- 保留 Orchestrator 不变

### E07.2 — PASS Happy Path Behavioral Parity

- 迁移正常成功路径：`plan → write → style → review → commit → fact_digest → rag_index`
- 每个 Node 作为 adapter 调用现有 Agent/Service
- 证明与现有 Orchestrator workflow 行为等价
- 运行完整 test suite 验证

### E07.3 — Supervisor Conditional Routing

- 基于 `ReviewDecision.verdict` 添加 conditional edges：
  - PASS → commit → fact_digest → rag_index → END
  - NEEDS_REVISION → END
  - HALT → END
  - UNKNOWN → END
  - ERROR（commit failure）→ END
- 五种终态在 Graph 中可区分

### E07.4 — Checkpoint / Resume

- 引入 LangGraph checkpointer
- 区分 InMemory checkpointer（dev）与 SqliteSaver（production）
- 非幂等 Node 的 retry 策略
- Chroma/SQLite 不纳入 checkpoint
- 验证：workflow 中断后可从上一 Node 恢复

### E07.5 — HITL Interrupt

- NEEDS_REVISION → `interrupt()`（等待人工 feedback）
- HALT → `interrupt()`（等待人工 decision）
- 实现 `Command(resume=...)` 恢复路径
- 依赖 E07.4 的 checkpoint/persistence 语义

### E07.6 — Revision Loop

- NEEDS_REVISION 后的 rewrite + re-review 循环
- 人工 feedback 注入 style_edit / write_draft Node
- HALT 后的人工 decision → replan / resume

### Future Work（不在 E07.1–E07.6 范围内）

- Rollback / Branch — LangGraph checkpoint 驱动的多分支 workflow 回退
- Observability — LangGraph tracing 集成
- CLI 完全迁移为 LangGraph 驱动
- Orchestrator 降级或移除

---

## 5. Forbidden Actions（全阶段）

- ❌ 不得创建第二套 Canonical State 表示
- ❌ 不得绕过 `_commit_all_tracking_docs()` 的原子事务
- ❌ 不得在 commit failure 后继续 Fact Digest / RAG
- ❌ 不得将 Chroma 二进制快照作为 checkpoint
- ❌ 不得修改 Planning 分层架构
- ❌ 不得修改 ReviewDecision contract
- ❌ 不得修改 Structured Memory schema
- ❌ 不得复制第二套 business logic
- ❌ E07.1/E07.2 不得大规模重构 Orchestrator 或 BaseAgent

---

## 6. Implementation Rules（每轮必须遵守）

1. 执行前读取 `docs/E07_0_MIGRATION_PREFLIGHT_REPORT.md` 和本文件
2. 修改 runtime 前运行完整 test suite，记录 baseline
3. 每轮结束后运行完整 test suite，确认 regression = 0
4. 不实现后续 phase 的功能
5. 保持 Orchestrator 可用（作为 adapter / fallback）
6. 每个新增 Node 必须有对应测试
7. E07.1/E07.2 使用 Adapter Node 调用现有 Agent，不做业务逻辑重构

---

## 7. API Stability Note

本文件记录的 LangGraph API 基于编写时的理解。
后续各轮执行前，**必须重新检查 LangGraph 官方文档**，
不得仅依赖本文件中的 API 描述。

如果实现阶段 LangGraph API 发生变化，
以官方文档为准，更新本文件对应章节。

---

## 8. State Layer Classification（来自 Preflight）

| Layer | Contents | Rebuildable? | Checkpoint? |
|---|---|---|---|
| **Canonical Planning State** | `book_plan.md`, `volume_plan.md`, `chapter_plan_chNNNN.md` | 否（LLM 产出） | 否 |
| **Canonical Story State** | 4 tracking docs + `styled` chapters | 否 | 否 |
| **Derived State** | Fact Digest, ChromaDB, SQLite | ✅ 可从 canonical 重建 | 否 |
| **Workflow Execution State** | `review_ch*.md`, `post_chapter_update*.md`, `retrieval_trace*.json`, `revisions/*.json` | — | ✅ LangGraph checkpoint |
| **Working / Draft State** | `chapter_NNNN_draft_*.md` | 否（中间产物） | 否 |

---

## 9. Reference

- LangGraph 官方文档：https://langchain-ai.github.io/langgraph/
- Preflight Report：`docs/E07_0_MIGRATION_PREFLIGHT_REPORT.md`
- 所有 E06–E06.2.1 Reports：`docs/E06*.md`
- 当前 test baseline：153 tests, 0 failures
