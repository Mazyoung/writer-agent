# E07.0 — LangGraph Migration Preflight Report

日期：2026-08-02
范围：基于当前真实源码的 Chapter Workflow 迁移地图。
不修改 runtime。不安装 LangGraph。

---

## A. Current Real Call Chain

以下从 `main.py` 的 CLI 命令追踪完整运行时调用链。

### A.1 `plan` 命令

```
main.cmd_plan(args)
  → Orchestrator(novel_id).plan_chapter(chapter_index, outline, instructions)
    → Orchestrator._retrieve_evidence(chapter_index, ...)   [0 LLM, Chroma read]
      → Orchestrator._build_retrieval_query(chapter_index, ...)  [0 LLM, deterministic]
      → ChromaStore.search(query, ...)                       [ChromaDB query]
      → Orchestrator._save_retrieval_trace(trace)            [1 JSON file write]
    → ChapterPlanner.plan_chapter(chapter_index, ..., rag_evidence)
      → ChapterPlanner._require_long_term_plans()            [FileStore reads]
      → FileStore.load_canonical / load_tracking_doc × N     [FileStore reads]
      → ChapterPlanner._load_recent_fact_digests()            [FileStore reads]
      → ChapterPlanner._extract_chapter_from_volume()         [deterministic parser]
      → BaseAgent.run(prompt)                                 [1 LLM call]
        → FileStore.save("outlines", "chapter_plan_chNNNN")   [1 timestamp .md write]
    ← returns ChapterPlan
```

**Side effects**: 1 LLM call, 1 retrieval trace JSON, 1 chapter plan .md (timestamped)

### A.2 `write` 命令

```
main.cmd_write(args)
  → Orchestrator.write_chapter(chapter_index)
    → FileStore.load_canonical("outlines", "chapter_plan_chNNNN")  [FileStore read]
    → ChapterPlan.from_markdown(plan_text)                         [deterministic]
    → DeepSeekWriter.write_chapter(plan, world_setting, prev_end)
      → ChapterPlan.build_writer_prompt(...)                       [deterministic]
      → BaseAgent.run(prompt)                                      [1 LLM call]
        → FileStore.save("chapters", "chapter_NNNN_draft")         [1 timestamp .md write]
    → ClaudeStylist.edit_chapter(draft, chapter_index, ...)
      → OpenAI.chat.completions.create(...)                        [1 LLM call — no FileStore write]
    → Orchestrator._save_and_check_styled(chapter_index, styled)
      → FileStore.save("chapters", "chapter_NNNN_styled")          [1 timestamp .md write]
      → StyleChecker(styled).check_all(...)                        [0 LLM, local analysis]
    ← returns styled text
```

**Side effects**: 2 LLM calls, 2 timestamp .md files (draft + styled)

### A.3 `style` 命令（定向修改）

```
main.cmd_style(args)
  → Orchestrator.style_edit(chapter_index, feedback)
    → FileStore.load_latest("chapters", "chapter_NNNN_styled")   [FileStore read]
    → ClaudeStylist.edit_chapter(text, chapter_index, ..., feedback)
      → OpenAI.chat.completions.create(...)                       [1 LLM call]
    → Orchestrator._save_and_check_styled(chapter_index, styled)
      → FileStore.save("chapters", "chapter_NNNN_styled")         [1 timestamp .md write]
      → StyleChecker(styled).check_all(...)                       [0 LLM]
```

**Side effects**: 1 LLM call, 1 timestamp .md file

### A.4 `review` 命令

```
main.cmd_review(args)
  → Orchestrator.review_chapter(chapter_index)
    → FileStore.load_latest("chapters", "chapter_NNNN_styled")   [enforced: no fallback]
    → FileStore.load_canonical/load_tracking_doc × 7             [FileStore reads]
    → StateManager.review_chapter(chapter_text, chapter_index, ...)
      → BaseAgent.run(prompt)                                     [1 LLM call]
        → FileStore.save("states", "review_chNNNN")               [1 timestamp .md write]

    → StateManager.parse_review_decision(raw_analysis)            [0 LLM, deterministic]

    ├── if PASS:
    │   → StateManager.update_tracking_docs(chapter_index, chapter_text, raw_analysis)
    │     → Phase 1: LOAD — FileStore.load_tracking_doc × 4        [FileStore reads]
    │     → Phase 2: PARSE — _parse_state_deltas()                 [0 LLM, deterministic]
    │                    — _parse_change_logs()                     [0 LLM, deterministic]
    │     → Phase 3: BUILD — in-memory object mutation
    │     → Phase 4: COMMIT — _commit_all_tracking_docs()
    │         → 4a: PREPARE — snapshot originals                   [FileStore reads]
    │         → 4b: BUILD candidates — to_markdown() × 4
    │         → 4c: COMMIT with rollback                           [FileStore writes]
    │             → save_tracking_doc("character_relationships")
    │             → save_tracking_doc("items_equipment")
    │             → save_tracking_doc("cultivation_system")
    │             → save_tracking_doc("character_states")
    │         → on ANY write failure: ROLLBACK all to originals
    │     → Phase 5: SQLite cache (AFTER Markdown success)
    │         → sqlite.upsert_foreshadow() × N
    │         → _sync_sqlite() → sqlite.upsert_character_state() × N
    │
    │   → check StateCommitResult.success
    │   ├── if success:
    │   │   → StateManager.extract_fact_digest_from_analysis()     [0 LLM, deterministic]
    │   │     → FileStore.save("states", "fact_digest_chNNNN")     [1 timestamp .md write]
    │   │   → Orchestrator._index_chapter_to_rag(chapter_index)
    │   │     → ChromaStore.index_chapter(novel_id, branch_id, ...)
    │   │         → delete stale chunks                            [ChromaDB delete]
    │   │         → chunk_text()                                   [deterministic]
    │   │         → coll.add(ids, documents, metadatas)            [ChromaDB insert]
    │   └── if failed: return ERROR (no Fact Digest, no RAG)
    │
    ├── if NEEDS_REVISION: return {decision, t1_issues, ...}
    ├── if HALT:           return {decision, planning_level, ...}
    └── if UNKNOWN:        return {decision, ...}

    → main.cmd_review 检查 result，输出对应 workflow 状态
```

**Side effects (PASS path)**: 1 LLM call, 1 review analysis .md, 4 canonical tracking .md (atomic), 1 fact_digest .md, SQLite upserts, ChromaDB index. Total: 1 LLM + 6 file writes + SQLite + Chroma.

---

## B. Candidate Nodes

基于真实代码职责拆分，不做架构修改。

| # | Node | Origin | LLM | Canonical? | Idempotent? |
|---|---|---|---|---|---|
| 1 | `load_context` | `Orchestrator.__init__` + `FileStore`/`PlanningStore`/`SQLiteStore` 构造 | 0 | — | ✅ |
| 2 | `retrieve_history` | `Orchestrator._retrieve_evidence()` → `_build_retrieval_query()` + `ChromaStore.search()` + `_save_retrieval_trace()` | 0 | — | ⚠️ trace 文件 |
| 3 | `plan_chapter` | `Orchestrator.plan_chapter()` → `ChapterPlanner.plan_chapter()` | 1 | Plan | ❌ timestamp save |
| 4 | `write_draft` | `DeepSeekWriter.write_chapter()` | 1 | — | ❌ timestamp save |
| 5 | `style_edit` | `ClaudeStylist.edit_chapter()` | 1 | — | 仅返回 str |
| 6 | `save_styled` | `_save_and_check_styled()` → `FileStore.save()` + `StyleChecker` | 0 | Story | ❌ timestamp save |
| 7 | `review_chapter` | `StateManager.review_chapter()` | 1 | — | ❌ timestamp save |
| 8 | `parse_decision` | `parse_review_decision()` → `ReviewDecision.from_analysis()` | 0 | — | ✅ |
| 9 | `parse_state_delta` | `_parse_state_deltas()` + `_parse_change_logs()` | 0 | — | ✅ |
| 10 | `commit_state` | `_commit_all_tracking_docs()` — PREPARE→COMMIT→ROLLBACK | 0 | Story | ⚠️ 同内容幂等 |
| 11 | `save_fact_digest` | `extract_fact_digest_from_analysis()` | 0 | Derived | ❌ timestamp save |
| 12 | `sync_sqlite` | `upsert_foreshadow()` + `_sync_sqlite()` | 0 | Derived | ✅ upsert 幂等 |
| 13 | `rag_index` | `_index_chapter_to_rag()` → `ChromaStore.index_chapter()` | 0 | Derived | ⚠️ stable chunk IDs |

---

## C. State Layer Classification

### Canonical Planning State

```
tracking/book_plan.md           — Book Plan (战略层)
tracking/volume_plan.md         — Active Volume Plan (战术层)
tracking/volumes/volume_NN.md   — Archived completed volumes
outlines/chapter_plan_chNNNN.md — Chapter Plan (执行层)
```

- 写入者：PlotDesigner, ChapterPlanner
- 修改需要 PlanRevision 记录

### Canonical Story State

```
tracking/character_relationships.md  — Structured Memory
tracking/items_equipment.md          — Structured Memory
tracking/cultivation_system.md       — Structured Memory
tracking/character_states.md         — Structured Memory (E06.1)
chapters/chapter_NNNN_styled_*.md    — Accepted styled chapters
```

- 写入者：StateManager (commit_state node), ClaudeStylist (via save_styled)
- 原子化提交（4 个 tracking docs 在单一事务中）
- commit failure → rollback ALL to OLD

### Working / Draft State

```
chapters/chapter_NNNN_draft_*.md      — DeepSeekWriter 原始草稿（中间产物）
```

- 写入者：DeepSeekWriter (via BaseAgent.run() → FileStore.save())
- 非 canonical，review 前必须经过 style_edit → save_styled
- 时间戳文件，`load_latest()` 总是取最新

### Derived State

```
states/fact_digest_chNNNN_*.md       — Fact Digest (deterministic extraction)
ChromaDB collection "chapter_chunks" — RAG vector index
SQLite state.db                      — foreshadowing + character cache (secondary/cache)
```

- 重建性：Chroma 可从 styled chapters 完整重建；Fact Digest 可从 raw_analysis 重建
- SQLite 是缓存，canonical 状态不依赖它
- Fact Digest 只从 committed chapter 生成

### Workflow Execution / Diagnostic State

```
states/review_chNNNN_*.md            — raw_analysis (诊断记录)
states/post_chapter_update_chNNNN.md — change log
tracking/rag_traces/*.json           — Retrieval traces
tracking/revisions/*.json            — PlanRevision records
```

- 辅助诊断和审计，不驱动规划决策
- LangGraph checkpoint 应覆盖此层
- 不属于 canonical state

---

## D. LLM Call Inventory

| 调用 | 模型 | Agent | 可缓存? |
|---|---|---|---|
| ChapterPlanner.plan_chapter() | DeepSeek (config) | chapter_planner | prompt 含追踪文档+RAG，变化频繁 |
| DeepSeekWriter.write_chapter() | DeepSeek (config) | deepseek_writer | prompt 含章规划+世界观，每章不同 |
| ClaudeStylist.edit_chapter() | Claude/DeepSeek | stylist | draft 每章不同 |
| StateManager.review_chapter() | DeepSeek (config) | state_manager | prompt 含 chapter+tracking+book/volume plan |

共 3–4 个 LLM 调用 per chapter（plan + write×2 + review），style_edit 是额外的。

---

## E. Migration Risks

### Risk 1: `Orchestrator.review_chapter()` 承载 7 个未来 Node 职责

当前 `review_chapter()` 单方法包含：
- review LLM call → parse_decision → route → commit_state → fact_digest → rag_index → SQLite

迁移风险：拆分后需要保持 E06.2.1 的所有 fail-closed 不变量（parse failure→block, commit failure→block, missing _commit_result→block）。

### Risk 2: `BaseAgent.run()` 将 LLM 调用与文件写入耦合

`BaseAgent.run()` 自动调用 `FileStore.save()` 写入时间戳文件。在 LangGraph 中，如果 checkpoint 后重试，会产生重复的时间戳文件。需要将 LLM 调用与 save 解耦。

### Risk 3: `Orchestrator` 集中持有所有 Agent/Service 依赖

`Orchestrator.__init__` 构造了 WorldBuilder, PlotDesigner, ChapterPlanner, DeepSeekWriter, ClaudeStylist, StateManager, ChromaStore。

LangGraph 迁移中，**Graph Node 可以调用已有 Agent**（Adapter Node 模式）。
Agent 作为依赖被注入 Graph Node，但不要求 Graph 管理 Agent 生命周期。

### Risk 4 (低): 时间戳文件在 retry 时重复

`FileStore.save()` 产生 `_YYYYMMDD_HHMMSS.md` 文件。LangGraph checkpoint→retry 会创建多份，但 `load_latest()` 总是取最新的，功能上不冲突。清理策略属于后续优化。

### Risk 5 (低): `_index_chapter_to_rag` 在 Orchestrator 内部硬编码

逻辑适合作为独立 Node，但当前与 Orchestrator (构造 ChromaStore) 耦合。

---

## F. Current Invariants (必须保留)

```text
1. ALL OLD or ALL NEW — 4 canonical tracking docs 原子化
2. snapshot read failure → abort before writes
3. parse failure → StateCommitResult(success=False) → block downstream
4. missing _commit_result → fail-closed (not silent success)
5. commit failure → no Fact Digest → no RAG
6. review requires styled chapter
7. SQLite only after canonical Markdown success
8. Chroma failure does NOT rollback canonical state
9. RAG rebuild clear failure → abort
10. CLI only prints "next chapter" after PASS + successful commit
```

---

## G. Preflight Summary

| 指标 | 值 |
|---|---|
| Candidate Nodes | 13 |
| LLM calls per chapter (正常路径) | 4 (plan + draft + style + review) |
| Canonical Planning writes | 1 (chapter_plan .md) |
| Canonical Story writes | 5 (4 tracking docs atomic + 1 styled .md) |
| Working/Draft writes | 1 (draft .md) |
| Derived state writes | 2 (fact_digest .md + ChromaDB index) |
| Workflow/Diagnostic writes | 3 (review analysis + change log + retrieval trace) |
| SQLite tables | 2 (foreshadowing + character_states — cache) |
| 需要拆分的最大方法 | `review_chapter()` (7 职责，E07.3 解决) |
| 需要解耦的关键耦合 | `BaseAgent.run()` = LLM + save (E07.4 checkpoint 前解决) |
| 非幂等 side effects | 3 (timestamp saves via BaseAgent.run) |
