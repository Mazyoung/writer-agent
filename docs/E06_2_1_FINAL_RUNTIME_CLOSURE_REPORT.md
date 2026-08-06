# E06.2.1 Final Runtime Closure — 实施报告

日期：2026-08-02
前置：E06.2 (Runtime Consistency Closure) 已完成主体。
范围：E06.2.1 — 最终三个 runtime consistency 漏洞修复。

---

## 1. 修改文件（3 改 0 增）

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/agents/state_manager/state_manager.py` | 修改 | Parse failure → 设置 `_commit_result` (StateCommitResult) |
| `src/core/orchestrator.py` | 修改 | Volume Plan rollback 保护原始异常不丢失 |
| `tests/test_e06.py` | 修改 | 新增 3 个 E06.2.1 Final Patch 测试 |

---

## 2. P0 — State Delta Parse Failure 阻止 Fact Digest / RAG

### 漏洞

`update_tracking_docs()` 在 parse_errors 存在时直接 `return changes`，但 `changes` 中**没有** `_commit_result` 键。Orchestrator 中：

```python
commit_result = changes.get("_commit_result")  # → None
if commit_result and not commit_result.success:  # → False (None is falsy)
    return ERROR  # ← 永远不执行
```

结果：parse 失败 + Supervisor PASS → Fact Digest 和 RAG 照常提交，形成 canonical-state leakage。

### 修复

```python
if parse_errors:
    ...
    changes["_commit_result"] = StateCommitResult(
        success=False,
        error_message=f"State Delta 解析错误 ({len(parse_errors)} 项)",
        warnings=parse_errors)
    return changes
```

### 效果

```
parse failure
↓
_commit_result.success = False
↓
orchestrator detects failure
↓
no Fact Digest
no RAG
return ERROR
```

---

## 3. P1 — Volume Plan Commit Failure 保护原始异常

### 漏洞

`start_new_volume` 的 except 块直接调用 `rollback_canonical()` 和 `archive_path.unlink()`。如果 rollback 本身失败，其异常会**替换**原始异常，根因丢失。

### 修复

```python
except Exception as e:
    rollback_errors = []
    if canonical_attempted:
        try:
            self.file_store.rollback_canonical(...)
        except Exception as re:
            rollback_errors.append(f"rollback_canonical 也失败: ...")
    if archive_written and archive_path.exists():
        try:
            archive_path.unlink()
        except Exception as ue:
            rollback_errors.append(f"删除归档也失败: ...")
    detail = f"...\n根因: {type(e).__name__}: {e}"
    if rollback_errors:
        detail += "\n回滚错误: " + "; ".join(rollback_errors)
    raise RuntimeError(detail) from e
```

### 效果

- 原始异常始终在 `RuntimeError` 中以 `根因:` 前缀出现
- Rollback 失败以 `回滚错误:` 附录，不掩盖根因
- `from e` 保留异常链

---

## 4. 已有修复确认（E06.2.1 之前轮次）

| 修复项 | 状态 |
|---|---|
| Snapshot fail-closed: 现有文件读取失败 → 中止 | ✅ |
| CLI cmd_review: 6 种 workflow state 正确输出 | ✅ |
| RAG rebuild: clear failure → abort | ✅ |
| `_sync_sqlite`: `except:pass` → `[STATE WARNING]` | ✅ |
| `rebuild_branch`: 返回 `bool`，失败 `[CHROMA ERROR]` | ✅ |
| Snapshot/rollback CLI 移除 | ✅ |
| Styled chapter enforcement in review | ✅ |
| Atomic multi-file commit with rollback | ✅ |

---

## 5. 测试清单

### E06.2.1 Final Patch 新增（3 个）

| 测试 | 覆盖 |
|---|---|
| `test_parse_failure_sets_commit_result_false` | parse_errors → changes 包含 `_commit_result` with success=False |
| `test_parse_failure_orchestrator_no_fact_digest_no_rag` | Orchestrator 检测 parse failure → 不调用 RAG，不生成 fact_digest |
| `test_volume_plan_commit_failure_preserves_old_state` | commit 失败 → 根因保留 + 旧卷保持 ACTIVE |

### 完整测试清单

| 套件 | 测试数 | 状态 |
|---|---|---|
| `test_e06.py` (E06 + E06.1 + E06.2 + E06.2.1) | 66 | ✅ |
| `test_e05.py` (E05) | 11 | ✅ |
| `test_chapter_plan.py` (E01/E02) | 9 | ✅ |
| `test_planning_foundation.py` (E03) | 11 | ✅ |
| `test_planning_hierarchy.py` (E03.1) | 15 | ✅ |
| `test_rag.py` (E04/E04.1) | 38 | ✅ |
| **Total** | **150** | **✅ 0 failures** |

---

## 6. E06–E06.2.1 完整不变量

```text
1.  ALL OLD or ALL NEW — canonical tracking docs 不会 PARTIAL NEW
2.  snapshot read failure → abort before any writes
3.  write failure → ALL OLD restored
4.  parse failure → StateCommitResult(success=False) → block downstream
5.  commit failure → no Fact Digest → no RAG
6.  review requires styled chapter
7.  StateCommitResult.success is the single programmatic signal
8.  CLI only prints "next chapter" after PASS + successful commit
9.  CLI presents correct status for all 6 workflow states
10. Chroma derived state failure is logged, never silent
11. RAG rebuild clear failure → abort → no re-index → no false success
12. SQLite updates only after canonical Markdown success
13. Volume Plan rollback failures don't mask root cause
14. CLI --help does not advertise broken snapshot/rollback
15. E01–E06.2.1 regression = 0
```

---

## 7. Inputs for E07 LangGraph Migration

### Candidate Nodes（最终版）

```text
load_context          — Orchestrator 构造 + FileStore/PlanningStore 加载
retrieve_history      — Orchestrator._retrieve_evidence() + ChromaStore.search()
plan_chapter          — Orchestrator.plan_chapter() → ChapterPlanner.plan_chapter()
write_chapter         — Orchestrator.write_chapter() → DeepSeekWriter + ClaudeStylist
style_chapter         — Orchestrator.style_edit() → ClaudeStylist.edit_chapter()
review_chapter        — Orchestrator.review_chapter() → StateManager.review_chapter()
parse_decision        — StateManager.parse_review_decision() → ReviewDecision.from_analysis()
parse_state_delta     — StateManager._parse_state_deltas()
commit_state          — StateManager._commit_all_tracking_docs()
                        (PREPARE→COMMIT→ROLLBACK, snapshot fail-closed, parse fail→StateCommitResult)
save_fact_digest      — StateManager.extract_fact_digest_from_analysis()
rag_index             — Orchestrator._index_chapter_to_rag() → ChromaStore.index_chapter()
rag_rebuild           — Orchestrator.rag_index_backfill() → ChromaStore.rebuild_branch()
                        (clear failure→abort, returns bool)
```

### 当前真实 State Flow

```text
review_chapter(input: styled_chapter, plan, tracking_docs, world_setting,
               book_plan, volume_plan)
  → StateManager.review_chapter()        [1 LLM call]
  → ReviewDecision.from_analysis()       [0 LLM, deterministic]
  → if PASS:
      → StateManager.update_tracking_docs()
        → LOAD originals (Phase 1)
        → PARSE state deltas (Phase 2, 0 LLM)
          → if parse_errors: set _commit_result(success=False) + return
        → BUILD candidates (Phase 3)
        → _commit_all_tracking_docs()    [PREPARE snapshot → COMMIT → ROLLBACK]
          → snapshot fail → abort (no writes)
          → write fail → rollback all
        → _sync_sqlite()                 [after canonical Markdown success]
      → check StateCommitResult.success
      → if success:
          → extract_fact_digest_from_analysis()  [0 LLM, deterministic]
          → _index_chapter_to_rag()              [ChromaDB]
      → if failed (commit/parse/snapshot):
          → return ERROR (no Fact Digest, no RAG)
  → if NEEDS_REVISION / 旧停机状态 / UNKNOWN:
      → no commit, no Fact Digest, no RAG
```

### 6 种 Workflow 终态

| 终态 | 触发条件 | CLI 行为 |
|---|---|---|
| PASS + committed | decision=PASS + commit success | 提示继续下一章 |
| PASS + commit FAILED | decision=PASS + any commit failure | halted, 不提示下一章 |
| NEEDS_REVISION | T1 errors or quality MAJOR | 提示修复后重新 review |
| 旧停机状态 + L2 | planning-level issue | 提示规划层处理 |
| 旧停机状态 + L3 | strategic issue (Book Plan violation) | 提示战略层修复 |
| UNKNOWN | missing/invalid decision section | halted, 提示人工判断 |

### E07 关键映射点

- `_commit_all_tracking_docs` 的 PREPARE→COMMIT→ROLLBACK 三阶段 → LangGraph transactional node
- Parse failure → StateCommitResult → conditional edge (not exception-based routing)
- RAG rebuild clear→abort → conditional edge pattern
- Volume Plan rollback with root-cause preservation → LangGraph error handling node
- All 6 workflow states already have deterministic string outputs in `cmd_review`

---

**E06.2.1 Final Runtime Closure 完成。不进入 E07 LangGraph。等待人工验收。**
