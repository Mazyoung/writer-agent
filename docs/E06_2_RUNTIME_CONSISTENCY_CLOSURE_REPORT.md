# E06.2 Runtime Consistency Closure — 实施报告

日期：2026-08-02
前置：E06.1 (Closure) 已完成。
范围：E06.2 — Canonical State 边界、失败传播、CLI 清理、Derived State 一致性收束。

---

## 1. 修改文件（5 改 0 增）

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/storage/document_formats.py` | 修改 | 新增 `StateCommitResult` dataclass |
| `src/agents/state_manager/state_manager.py` | 修改 | 导入 `StateCommitResult`；重写 `_commit_all_tracking_docs` 为原子化事务含回滚；`update_tracking_docs` 返回 `_commit_result` |
| `src/core/orchestrator.py` | 修改 | `review_chapter`: 检查 commit result → 失败时 block Fact Digest + RAG；styled chapter 强制要求；移除 `snapshot_all`/`rollback_all` |
| `src/storage/chroma_store.py` | 修改 | `index_chapter` stale cleanup / `rebuild_branch` 不再静默吞异常 |
| `main.py` | 修改 | 移除 `snapshot`/`rollback` CLI 子命令及函数 |
| `tests/test_e06.py` | 修改 | 新增 14 个 E06.2 测试；更新 1 个 E06.1 测试适配新 contract |

---

## 2. P0 — Canonical Multi-File Atomic Transaction

### 变更

重写 `_commit_all_tracking_docs()` 为真实的原子化事务：

```
Phase 4a: SNAPSHOT originals — 读取所有 4 个 canonical tracking docs 当前内容
Phase 4b: BUILD candidates — 内存中已构建完毕
Phase 4c: COMMIT with rollback — 依次写入，任意失败 → 回滚所有已写文件
Phase 4d: SQLite — 独立缓存，错误不触发回滚
```

### 原子性保证

- 任何 canonical file 写入失败 → 立即回滚所有已写文件到 OLD 内容
- 不产生 PARTIAL NEW 状态
- 使用原始内容快照回滚，不依赖 .bak 文件
- 返回 `StateCommitResult` 而非 `bool`

### 实现方式

方案 A（snapshot → write → rollback）：
```text
originals[name] = read current file
↓
for each file:
    write new content
    if fail:
        for each already-written:
            restore from originals
        return FAILED
return SUCCESS
```

---

## 3. P0 — Commit 返回明确成功/失败结果

### 新增 `StateCommitResult`

```python
@dataclass
class StateCommitResult:
    success: bool = False          # fail-closed 默认
    warnings: list[str] = []       # 非致命警告（含 SQLite 错误）
    changed_files: list[str] = []  # 成功提交的文件列表
    error_message: str = ""        # 失败原因
```

### 传播路径

```
_commit_all_tracking_docs() → StateCommitResult
↓
update_tracking_docs() → changes["_commit_result"]
↓
Orchestrator.review_chapter() → 检查 commit_result.success
```

---

## 4. P0 — Commit Failure 阻止 Fact Digest 与 RAG

### 变更

`Orchestrator.review_chapter()` PASS 路径新增 commit result 检查：

```text
PASS decision
↓
update_tracking_docs()
↓
check _commit_result.success
↓
if FAILED:
    ❌ NO fact_digest_chNNNN.md
    ❌ NO RAG index
    ✅ return {"decision": "PASS", "commit_status": "FAILED",
               "workflow_status": "ERROR"}
    ✅ print [ERROR] messages for CLI
else:
    ✅ extract_fact_digest_from_analysis()
    ✅ _index_chapter_to_rag()
```

### 原则

> Review semantic PASS 不代表 persistence commit 成功。
> PASS 只是语义审核通过，commit 失败必须阻断下游 canonical 提交。

---

## 5. State Commit Failure 的 Workflow Decision

### 返回值

Commit 失败时 `review_chapter` 返回：

```python
{
    "decision": "PASS",
    "commit_status": "FAILED",
    "workflow_status": "ERROR",
    "error": "<failure reason>",
    "warnings": [...]
}
```

### CLI 输出

```
[ERROR] Review semantic PASS but canonical state commit FAILED
[ERROR] 原因: items_equipment: OSError: ...
[ERROR] 未提交 Fact Digest / RAG。workflow halted。
[ERROR] 本章尚未完成 canonical commit，请检查文件系统权限后重新 review。
```

不重新调用 LLM。不修改 L2/L3 Planning。这是纯 Runtime/persistence failure。

---

## 6. P0 Test — 真正验证 Rollback

### 新增测试

| 测试 | 覆盖 |
|---|---|
| `test_second_save_failure_rolls_back_first` | 第二个文件写入失败 → 第一个回滚到 OLD，所有文件保持 OLD |
| `test_all_files_committed_when_no_failure` | 全部成功 → ALL NEW，changed_files 正确 |
| `test_double_save_failure_reported` (更新) | E06.2 contract: 失败 → 回滚所有，不保留 PARTIAL NEW |

### 测试构造

```text
relationships save = success
items save = raises OSError
↓
assert: relationships == OLD (回滚后)
assert: items == OLD
assert: cultivation == OLD
assert: commit_result.success == False
```

---

## 7. Fact Digest 作为 Derived State 层

### 当前三层

```
Canonical Planning State
  Book Plan / Volume Plan / Chapter Plan

Canonical Story State
  Structured Memory + accepted styled chapters

Derived State
  Fact Digest / Chroma RAG / traces
```

### E06.2 保证

- 只有 accepted + committed chapter 才能生成 canonical Fact Digest
- Commit 失败 → no Fact Digest
- Fact Digest 在 commit 成功后、RAG 之前生成

---

## 8. Review 强制要求 Styled Chapter

### 变更

`review_chapter` 不再从 styled fallback 到 raw/draft：

```python
chapter_text = load_latest("chapters", "chapter_NNNN_styled")
if not chapter_text:
    raise ValueError(
        "第N章 styled 文件不存在。"
        "请先运行: python main.py write <novel> --chapter N"
        "\nReview 只接受 styled 章节（经过 ClaudeStylist 编辑）。")
```

### 原因

Review 需要评估经过风格编辑的最终文本。未经过 ClaudeStylist 的 raw draft 不应该进入 Review → commit 流程。

---

## 9. P1 — CLI 清理 Snapshot/Rollback

### 变更

- 移除 `main.py` 中的 `cmd_snapshot`、`cmd_rollback` 函数
- 移除 `snapshot`、`rollback` subparser 注册
- 移除 `orchestrator.py` 中的 `snapshot_all`、`rollback_all` 方法
- 更新文档字符串

### 原因

当前完整 runtime rollback 未实现。旧的 Markdown .bak 机制误导用户以为系统支持完整状态回滚，但实际只覆盖 Markdown 文件，不覆盖 SQLite/Chroma/Story State。

LangGraph checkpoint ≠ Story canonical rollback。完整 rollback 属于后续阶段。

---

## 10. P1 — Chroma Rebuild/Delete Failure 不再静默

### 变更

#### `index_chapter` stale chunk cleanup

```python
# 旧：except Exception: pass
# 新：
except Exception as e:
    print(f"  [CHROMA WARNING] 清理第{chapter_index}章旧chunks失败: "
          f"{type(e).__name__}: {e}")
```

#### `rebuild_branch`

```python
# 旧：except Exception: pass
# 新：
except Exception as e:
    print(f"  [CHROMA WARNING] rebuild_branch 清理失败: "
          f"{type(e).__name__}: {e}")
```

### 原则

Derived State failure 不 rollback canonical chapter（保持 E04）。
但 Derived State failure 不能伪装成成功（silent success）。

---

## 11. 测试清单

### E06.2 新增测试（14 个）

| 类别 | 测试数 | 说明 |
|---|---|---|
| P0 — Atomic Rollback | 2 | `test_second_save_failure_rolls_back_first`, `test_all_files_committed_when_no_failure` |
| P0 — Commit Failure Blocks Downstream | 2 | `test_commit_failure_no_fact_digest_no_rag`, `test_commit_success_proceeds_normally` |
| P0 — Styled Chapter Enforcement | 2 | `test_review_without_styled_raises`, `test_review_with_styled_proceeds` |
| P0 — StateCommitResult Propagation | 3 | `test_success_result`, `test_failure_result`, `test_default_is_failure` |
| P1 — CLI Snapshot/Rollback Cleanup | 3 | `test_help_does_not_contain_snapshot_subcommand`, `test_snapshot_command_does_not_exist`, `test_rollback_command_does_not_exist` |
| P1 — Chroma Warning | 2 | `test_index_chapter_stale_cleanup_logs_warning`, `test_rebuild_branch_logs_warning` |

### E06.1 适配测试（1 个）

| 测试 | 适配 |
|---|---|
| `test_double_save_failure_reported` | 期望从 "第一个文件保留新数据" 改为 "第一个文件回滚到 OLD" |

### 既有测试回归

| 套件 | 测试数 | 状态 |
|---|---|---|
| `test_e06.py` (E06 + E06.1 + E06.2) | 55 | ✅ |
| `test_e05.py` (E05) | 11 | ✅ |
| `test_chapter_plan.py` (E01/E02) | 9 | ✅ |
| `test_planning_foundation.py` (E03) | 11 | ✅ |
| `test_planning_hierarchy.py` (E03.1) | 15 | ✅ |
| `test_rag.py` (E04/E04.1) | 38 | ✅ |
| **Total** | **139** | **✅ 0 failures** |

---

## 12. E06.2 Explicit Non-Goals 验收

| 约束 | 状态 |
|---|---|
| 不引入数据库 | ✅ |
| 不引入复杂 transaction framework | ✅ |
| 不做大型 FileStore 重构 | ✅ |
| 不重新调用 LLM 处理 persistence error | ✅ |
| 不把 persistence error 修改成 L2/L3 Planning Issue | ✅ |
| 不新增 LLM 调用 | ✅ |
| 不新增 Agent | ✅ |
| 不实现 Rewrite Loop | ✅ |
| 不实现 L2/L3 approval workflow | ✅ |
| 不实现 automatic PlanRevision | ✅ |
| 不实现完整 runtime rollback | ✅ |
| 不进入 E07 LangGraph | ✅ |
| 不为保住 CLI 重新实现旧 Markdown snapshot 机制 | ✅ |

---

## 13. 核心不变量（E06.2 建立）

```text
1. ALL OLD or ALL NEW — canonical tracking docs 不会 PARTIAL NEW
2. Commit FAILED → no Fact Digest → no RAG
3. Review requires styled chapter
4. StateCommitResult.success is the single programmatic signal
5. Chroma derived state failure is logged, never silent
6. CLI --help does not advertise broken snapshot/rollback
7. E01–E06.1 regression = 0
```

---

## 14. E06.2 E06.1 闭合确认

| E06.1 标注 | E06.2 收束 |
|---|---|
| Atomic commit 缺少回滚 | ✅ 实现：SNAPSHOT → WRITE → ROLLBACK |
| commit 失败后未检查 | ✅ 实现：StateCommitResult → block downstream |
| Chroma except:pass 静默 | ✅ 修复：[CHROMA WARNING] 显式输出 |
| snapshot/rollback CLI 误导 | ✅ 移除 |
| styled fallback 允许 review raw | ✅ 强制要求 styled |

---

## 15. Inputs for E07 LangGraph Migration

以下以当前真实代码为准，不实现、只总结。

### Candidate Nodes

```text
load_context          — Orchestrator 构造 + FileStore/PlanningStore 加载
retrieve_history      — Orchestrator._retrieve_evidence() + ChromaStore.search()
plan_chapter          — Orchestrator.plan_chapter() → ChapterPlanner.plan_chapter()
write_chapter         — Orchestrator.write_chapter() → DeepSeekWriter + ClaudeStylist
style_chapter         — Orchestrator.style_edit() → ClaudeStylist.edit_chapter()
review_chapter        — Orchestrator.review_chapter() → StateManager.review_chapter()
parse_decision        — StateManager.parse_review_decision() → ReviewDecision.from_analysis()
parse_state_delta     — StateManager._parse_state_deltas()
commit_state          — StateManager._commit_all_tracking_docs() (atomic, with rollback)
save_fact_digest      — StateManager.extract_fact_digest_from_analysis()
rag_index             — Orchestrator._index_chapter_to_rag() → ChromaStore.index_chapter()
```

### 当前真实 State Flow

```text
review_chapter(input: styled_chapter, plan, tracking_docs, world_setting,
               book_plan, volume_plan)
  → StateManager.review_chapter()        [1 LLM call]
  → ReviewDecision.from_analysis()       [0 LLM, deterministic]
  → if PASS:
      → StateManager.update_tracking_docs()
        → _parse_state_deltas()          [0 LLM, deterministic]
        → _commit_all_tracking_docs()    [atomic, with rollback]
      → check StateCommitResult.success
      → if success:
          → extract_fact_digest_from_analysis()  [0 LLM, deterministic]
          → _index_chapter_to_rag()              [ChromaDB]
      → if failed:
          → return ERROR (no Fact Digest, no RAG)
  → if NEEDS_REVISION / HALT / UNKNOWN:
      → no commit, no Fact Digest, no RAG
```

### 当前关键约束

1. `commit failure → no Fact Digest → no RAG` (E06.2)
2. `review requires styled candidate` (E06.2)
3. `PASS / NEEDS_REVISION / HALT / UNKNOWN / runtime failure` — 5 种 Workflow 终态
4. `StateCommitResult.success` 是唯一 programmatic commit 信号
5. Chroma 是 rebuildable derived state
6. Fact Digest 是 derived state，但只从 committed chapter 生成
7. Snapshot/rollback CLI 已移除 — 完整 rollback 属于 LangGraph checkpoint 之后

### E07 注意事项

- `_commit_all_tracking_docs` 的内部回滚逻辑（snapshot → write → rollback）可能与 LangGraph checkpoint 机制重叠
- 当前 4 个 canonical tracking docs 的原子化可映射为 LangGraph 的单步 transactional node
- SQLite (foreshadowing) 和 Chroma 不在 canonical multi-file transaction 范围内
- CLI workflow state 呈现（PASS/REVISION/HALT/UNKNOWN/ERROR）已在 orchestrator 中字符串化

---

**E06.2 Runtime Consistency Closure 完成。不进入 E07 LangGraph。**
