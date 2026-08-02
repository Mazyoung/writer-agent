# E07 LangGraph Migration Guide — 长期指导文件

版本：v1.0（E07.0 Preflight 产出）
受众：E07.1–E07.6 各轮实施 Agent
状态：**E07 各轮执行前必须读取本文件**

---

## 1. 核心迁移原则

### Principle 1: 先解耦，再迁移

不要把当前的 `Orchestrator` 方法直接包装成 LangGraph Node。
必须先把 LLM 调用与 side effect 解耦，再定义 Node 边界。

```
❌  Node = Orchestrator.review_chapter()  ← 包含 7 个逻辑职责
✅  Node = 单一职责（LLM call OR parse OR commit OR index）
```

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

### Principle 5: 最小 Node 边界

每个 Node 应该只做一件事：
- 一次 LLM 调用，或
- 一次确定性解析，或
- 一次原子化提交

如果一个 Node 做了多件事，它不能被独立 retry/interrupt。

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

基于 E07.0 Preflight 的 Candidate Nodes，每个 Node 的副作用清单：

| Node | Side Effects | Canonical? | Idempotency Strategy |
|---|---|---|---|
| `retrieve_history` | 1 JSON trace file | 否 | trace 文件名含 timestamp，retry 产生重复 → 可接受 |
| `plan_chapter` | 1 LLM + 1 chapter plan .md | Plan | 拆分 LLM 与 save；retry 产生重复 → `load_latest()` 总是取最新 |
| `write_draft` | 1 LLM + 1 draft .md | 否 | 同 plan_chapter |
| `style_edit` | 1 LLM（仅返回 str） | 否 | 纯函数化 — Node 输出 styled text，由下游 Node save |
| `save_styled` | 1 styled .md + StyleChecker | Story | 幂等写入：同内容同文件名 |
| `review_chapter` | 1 LLM + 1 analysis .md | 否 | 拆分 LLM 与 save |
| `parse_decision` | 0（纯解析） | — | ✅ 天然幂等 |
| `parse_state_delta` | 0（纯解析） | — | ✅ 天然幂等 |
| `commit_state` | 4 canonical .md writes | Story | 原子事务 + 回滚；同内容重复写入结果相同 |
| `save_fact_digest` | 1 fact_digest .md | Derived | timestamp 文件，retry 产生重复 → 可接受 |
| `sync_sqlite` | SQLite upsert × N | Derived | ✅ upsert 天然幂等 |
| `rag_index` | ChromaDB delete + insert | Derived | ✅ stable chunk IDs → 幂等 |

---

## 4. Migration Phase Plan

### E07.1 — State Schema + Minimal Linear Graph

- 定义 `GraphState` TypedDict（不实现全量字段）
- 构建最小 linear graph：`plan → write → style → review`
- 不添加 conditional edge（先验证 linear 路径）
- 保留 `Orchestrator` 作为 adapter，不删除

### E07.2 — Conditional Routing

- 基于 `ReviewDecision.verdict` 添加 conditional edges
- PASS → fact_digest → rag_index → END
- NEEDS_REVISION / HALT / UNKNOWN → END（不同终态）
- commit failure → ERROR → END

### E07.3 — Interrupt + Human-in-the-Loop

- NEEDS_REVISION → interrupt（等待人工 feedback）
- HALT → interrupt（等待人工 decision）
- 实现 `Command.resume` 恢复路径

### E07.4 — Checkpoint + Retry

- 区分 InMemory checkpointer（dev）与 SqliteSaver（production）
- 非幂等 Node 的 retry 策略
- Chroma/SQLite 的 checkpoint 排除策略

### E07.5 — Rollback / Branch

- LangGraph checkpoint 驱动的 workflow 回退
- 区分"重做本章"与"回到第 N 章"
- StoryBranch 模型迁移

### E07.6 — Observability + Production

- LangGraph tracing 集成
- 迁移 CLI 为 LangGraph 驱动
- 移除 Orchestrator（或降级为 adapter）

---

## 5. Forbidden Actions（全阶段）

- ❌ 不得创建第二套 Canonical State 表示
- ❌ 不得绕过 `_commit_all_tracking_docs()` 的原子事务
- ❌ 不得在 commit failure 后继续 Fact Digest / RAG
- ❌ 不得将 Chroma 二进制快照作为 checkpoint
- ❌ 不得修改 Planning 分层架构
- ❌ 不得修改 ReviewDecision contract
- ❌ 不得修改 Structured Memory schema
- ❌ 不得在 LangGraph Node 中直接调用 `BaseAgent.run()`（耦合 LLM+save）
- ❌ 不得在 interrupt 前执行非幂等 side effect

---

## 6. Implementation Rules（每轮必须遵守）

1. 执行前读取 `docs/E07_0_MIGRATION_PREFLIGHT_REPORT.md` 和本文件
2. 修改 runtime 前运行完整 test suite，记录 baseline
3. 每轮结束后运行完整 test suite，确认 regression = 0
4. 不实现后续 phase 的功能
5. 保持 `Orchestrator` 可用（作为 adapter / fallback）
6. 每个新增 Node 必须有对应测试

---

## 7. API Stability Note

本文件记录的 LangGraph API 基于编写时的理解。
后续各轮执行前，**必须重新检查 LangGraph 官方文档**，
不得仅依赖本文件中的 API 描述。

如果实现阶段 LangGraph API 发生变化，
以官方文档为准，更新本文件对应章节。

---

## 8. Reference

- LangGraph 官方文档：https://langchain-ai.github.io/langgraph/
- Preflight Report：`docs/E07_0_MIGRATION_PREFLIGHT_REPORT.md`
- 所有 E06–E06.2.1 Reports：`docs/E06*.md`
- 当前 test baseline：151 tests, 0 failures
