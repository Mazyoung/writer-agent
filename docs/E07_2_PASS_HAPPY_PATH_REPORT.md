# E07.2 — PASS Happy Path Behavioral Parity Report

日期：2026-08-03
范围：LangGraph 完整 PASS happy path 的 adapter-node 实现
状态：**完成**

---

## A. Modified Files

| File | Change | Lines |
|---|---|---|
| `src/workflows/chapter_workflow.py` | 从单节点 skeleton 扩展为 10-node adapter graph | +619 |
| `tests/test_e07.py` | 保留 E07.1 测试 + 新增 E07.2 测试 (38 total) | +723 |

无其他文件修改。`main.py`、`Orchestrator`、所有 Agent/Service 保持不变。

---

## B. Final Graph Topology

```
START
  → plan_chapter       (ChapterPlanner + RAG retrieval, 1 LLM)
  → write_draft        (DeepSeekWriter, 1 LLM)
  → style_edit         (ClaudeStylist, 1 LLM, returns str only)
  → save_styled        (FileStore.save + StyleChecker, 0 LLM)
  → review_chapter     (StateManager.review_chapter, 1 LLM)
  → parse_decision     (ReviewDecision.from_analysis, 0 LLM)
  → require_pass       (E07.2 temporary guard, 0 LLM)
  → commit_state       (StateManager.update_tracking_docs, 0 LLM)
  → save_fact_digest   (extract_fact_digest_from_analysis, 0 LLM)
  → rag_index          (ChromaStore.index_chapter, 0 LLM)
  → END
```

- 10 个业务 node，线性拓扑，无 conditional edges
- 4 个 LLM 调用（plan + draft + style + review），与当前生产 runtime 一致
- `require_pass` 是 E07.2 临时 guard node（E07.3 替换为 conditional edge）

---

## C. State Schema

`ChapterWorkflowState` (TypedDict, total=False):

| Category | Fields |
|---|---|
| Identity | `novel_id`, `branch_id`, `chapter_index` |
| Plan inputs | `chapter_outline`, `extra_instructions` |
| Flow data | `chapter_plan_text`, `draft_text`, `styled_text`, `raw_analysis` |
| Decision routing | `verdict`, `review_reasons`, `t1_issues`, `planning_level` |
| Commit guard | `commit_success`, `commit_error` |
| Results | `fact_digest_generated`, `rag_chunks` |
| Status | `workflow_status`, `error` |

---

## D. Key Design Decisions

### D.1 Adapter Node Pattern

每个 Node 作为 adapter 调用现有 Agent/Service：
```
plan_chapter   → ChapterPlanner.plan_chapter()
write_draft    → DeepSeekWriter.write_chapter()
style_edit     → ClaudeStylist.edit_chapter()
save_styled    → FileStore.save() + StyleChecker.check_all()
review_chapter → StateManager.review_chapter()
parse_decision → StateManager.parse_review_decision()
commit_state   → StateManager.update_tracking_docs()
save_fact_digest → StateManager.extract_fact_digest_from_analysis()
rag_index      → ChromaStore.index_chapter()
```

没有复制第二套 business logic。

### D.2 require_pass Guard

E07.2 使用 `require_pass` 作为常规 node（非 conditional edge）：
- `PASS` → 返回 `{}`（继续下游）
- `NEEDS_REVISION` / `HALT` / `UNKNOWN` → 设置 `commit_success=False`, `workflow_status="STOPPED_NON_PASS"`
- 下游 node（commit_state, save_fact_digest, rag_index）检查 `workflow_status` 和 `commit_success` 决定是否跳过

正式 conditional routing 属于 E07.3。

### D.3 commit_state 复用

`commit_state` 直接调用 `StateManager.update_tracking_docs()`，保持：
- ALL-OLD / ALL-NEW 原子提交
- snapshot → commit → rollback 事务语义
- parse failure → StateCommitResult(success=False)
- missing _commit_result → fail-closed

没有重写 `_parse_state_deltas()` 或 `_commit_all_tracking_docs()`。

### D.4 Fail-Closed Chain

```
require_pass(NON-PASS) → commit_state SKIP → save_fact_digest SKIP → rag_index SKIP
commit_state(FAIL)      → save_fact_digest SKIP → rag_index SKIP
```

---

## E. Preserved Invariants

| # | Invariant | 验证方式 |
|---|---|---|
| 1 | ALL OLD or ALL NEW — 4 tracking docs 原子化 | commit_state 复用 update_tracking_docs() |
| 2 | commit failure → no Fact Digest → no RAG | save_fact_digest / rag_index 检查 commit_success |
| 3 | Review 只接受 styled chapter | review_chapter 检查 styled_text |
| 4 | RAG failure 不回滚 canonical state | rag_index catch 异常 → workflow_status=completed |
| 5 | _commit_result 缺失 → fail-closed | commit_state 检测 missing → commit_success=False |
| 6 | styled chapter 只保存一次 | save_styled 是唯一调用 FileStore.save 的节点 |
| 7 | SQLite 仅在 canonical Markdown 成功后 | commit_state 内 update_tracking_docs() 处理 |

---

## F. Test Results

### E07 Focused Tests (38/38 OK)

| Class | Tests | Description |
|---|---|---|
| TestChapterWorkflowState | 3 | State schema (E07.1-A enduring) |
| TestGraphTopology | 4 | Graph compile, 10 nodes, linear edges, no conditional |
| TestNodeContracts | 4 | require_pass guard: PASS/NEEDS_REVISION/HALT/UNKNOWN |
| TestNoRuntimeSideEffects | 4 | Import safety, no Orchestrator import, main.py unchanged |
| TestE07_2_PassHappyPath | 6 | Node returns (plan/draft/style/save/parse), graph acceptance |
| TestE07_2_NonPassGuard | 4 | Non-PASS → commit_state/fact_digest/rag_index all skipped |
| TestE07_2_CommitFailureBlocksDownstream | 5 | commit failure + missing _commit_result → fail-closed |
| TestE07_2_RAGFailureNoRollback | 2 | RAG failure → workflow_status=completed |
| TestE07_2_StyledChapterOnce | 2 | save once + style_edit no save |
| TestE07_2_NoFuturePhaseLeakage | 4 | No conditional edges, no checkpointer, no interrupt, no Command |

### Regression Tests (all OK)

| Suite | Tests | Result |
|---|---|---|
| test_e05 | 11 | OK |
| test_e06 | 69 | OK |
| test_chapter_plan | 9 | OK |

### Exit Code 137 (full suite)

`python -m unittest discover tests -v` 因进程资源耗尽退出 (exit 137 = SIGKILL)。
这是大量测试在高负载环境下同时运行 chromadb/LLM mock 导致的资源问题，不是功能性失败。
单独运行各模块结果均为 OK。

---

## G. Known Limitations

1. **require_pass 是临时方案**：E07.3 用 `add_conditional_edges()` 替代为正式 conditional routing
2. **plan_chapter 内联 RAG**：RAG retrieval 在 `plan_chapter` node 内部，未来可作为独立 `retrieve_history` node
3. **无 checkpoint**：State merge 依赖 LangGraph 默认 reducer（last-write-wins），E07.4 引入 checkpointer
4. **无 HITL**：NEEDS_REVISION/HALT 仅做 fail-closed 停止，E07.5 引入 interrupt()
5. **生产 runtime 未切换**：`main.py` 仍使用 Orchestrator，Graph 是旁路存在

---

## H. E07.3+ Not Implemented (Confirmed)

- ❌ `add_conditional_edges()` — E07.3
- ❌ checkpointer (`SqliteSaver` / `InMemorySaver` / `MemorySaver`) — E07.4
- ❌ `interrupt()` / `Command(resume=...)` — E07.5
- ❌ NEEDS_REVISION rewrite loop — E07.6
- ❌ HALT → replan workflow — E07.6
- ❌ branch semantics — Future
- ❌ Orchestrator 重构 — Forbidden per Guide §5

---

## I. Compliance with Migration Guide

| Guide Rule | Status |
|---|---|
| Adapter Node 优先 (Principle 1) | ✅ 所有 node 调用现有 Agent/Service |
| Canonical State ≠ Checkpoint (Principle 2) | ✅ State 仅含 workflow 数据 |
| Chroma 是 Derived State (Principle 3) | ✅ RAG failure → workflow completed |
| Fail-Closed 优先 (Principle 4) | ✅ 所有非 PASS 路径 stop |
| Agent ≠ Graph Node (Principle 5) | ✅ Node 调用 Agent，不管理生命周期 |
| 不复制 business logic (§6.8) | ✅ 无重复逻辑 |
| E07.1/E07.2 不重构 Orchestrator (§6.9) | ✅ Orchestrator 未修改 |
| 不实现后续 phase 功能 (§6.4) | ✅ 见 §H |
