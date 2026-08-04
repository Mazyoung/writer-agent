# E07.4.1 Legacy Orchestrator Cleanup Report

日期：2026-08-05  
范围：删除旧章节 Orchestrator 控制路径；不修改 LangGraph topology、checkpoint、ReviewDecision、canonical commit、Fact Digest 或 RAG 语义。

## 1. 删除和迁移的旧代码

### 删除

- 删除 `src/core/orchestrator.py`。
- 删除 `main.py` 的独立 `review` 命令和 `cmd_review()`。
- 删除旧 Orchestrator 中重复的章节控制逻辑：
  - draft → style → save；
  - review → decision → commit → Fact Digest → RAG；
  - 重复的 retrieval query/trace 与 RAG chapter-index helper；
  - 非完整 workflow rollback 的 `rollback_chapter()`；
  - 仅服务旧主链的 save/check、previous-ending、scene/title helpers。

### 迁移

仍有效的非整章能力改由 scoped services 承担：

| 能力 | 新所有者 |
|---|---|
| Proposal、初始化、Rolling Horizon 新卷 | `src/planning/novel_lifecycle.py:NovelLifecycleService` |
| 独立 Chapter Plan | `src/planning/chapter_planning_service.py:ChapterPlanningService` |
| 独立人工风格修改 | `src/workflows/chapter_editing.py:ChapterEditingService` |
| 状态查询 | `src/core/novel_status.py:NovelStatusService` |
| RAG backfill/rebuild | `src/storage/rag_maintenance.py:RAGMaintenanceService` |
| legacy canonical 检查 | `src/storage/file_store.py:FileStore.migrate_legacy_canonical_if_needed` |

`ChapterPlanningService` 复用已有 `ChapterRetrievalService`，不迁移 Orchestrator 中重复的 query 和 trace 实现。

## 2. 当前唯一章节执行链路

```text
main.py:cmd_write
  → run_chapter_workflow
  → ChapterWorkflowRunner.run
  → SqliteSaver + deterministic thread_id
  → build_chapter_workflow(checkpointer=...)
  → LangGraph invoke/resume
```

完整章节节点仍是：

```text
preflight → plan_chapter → write_draft → style_edit → save_styled
          → review_chapter → parse_decision
              PASS → commit_state → save_fact_digest → rag_index
              NEEDS_REVISION/HALT → stop_non_pass
              UNKNOWN/error → END
```

独立 `style` 命令只做人工风格修改、保存和 deterministic StyleChecker，不执行 review 或 canonical commit。独立 `plan` 命令只生成 Chapter Plan。

## 3. 是否仍有模块依赖 Orchestrator

Production source 不再 import、实例化或调用 `Orchestrator`。`main.py` 直接依赖 scoped services 与 `ChapterWorkflowRunner`；LangGraph 节点直接依赖现有 Agent/Service/Store。

历史文档和旧测试可能仍出现 “Orchestrator” 字样，用于描述过往阶段或旧 contract；它们不构成 production dependency。本任务按要求未修改或运行测试。

## 4. E07.5 插入位置

HITL 的插入点是 `src/workflows/chapter_workflow.py` 中 `parse_decision` 后的 conditional routing：

- PASS：保持进入 `commit_state`；
- NEEDS_REVISION：未来进入 `interrupt()`，接收人工反馈后 resume；
- HALT：未来进入 `interrupt()`，接收人工决策后 resume；
- UNKNOWN/error：继续 fail-closed。

恢复仍应复用 `ChapterWorkflowRunner` 当前 deterministic `thread_id` 和 SQLite checkpointer；E07.5 再引入 `Command(resume=...)`。E07.4.1 未实现 interrupt、人工反馈或 revision loop。

## 5. 保持不变的边界

本轮没有重构以下业务所有者：

- `ReviewDecision.from_analysis()`；
- `StateManager.update_tracking_docs()` 与 ALL OLD / ALL NEW transaction；
- `StateManager.extract_fact_digest_from_analysis()`；
- `ChapterRetrievalService`；
- `ChromaStore.index_chapter()`；
- LangGraph node/edge/state topology；
- `ChapterWorkflowRunner` checkpoint/resume。

## 6. 验证说明

依任务要求：没有新增、修改或运行测试。仅执行静态核对，包括 production dependency 搜索、CLI 唯一路径检查、Python AST 解析和 `git diff --check`。静态核对结果记录在本次交付摘要中，不等同于回归测试通过。
