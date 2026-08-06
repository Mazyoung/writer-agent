# E07.5 Human-in-the-loop Integration Report

日期：2026-08-05  
范围：为现有 checkpointed LangGraph chapter workflow 增加 NEEDS_REVISION/旧停机状态 人工暂停与恢复。

## 1. 修改文件

| 文件 | 修改 |
|---|---|
| `src/workflows/chapter_workflow.py` | 新增 `await_human_review` interrupt 节点及 HITL state fields；更新 review conditional routing |
| `src/workflows/chapter_runner.py` | 检测 pending interrupts；新增 `Command(resume=...)` 恢复接口 |
| `main.py` | `write --resume` 人工恢复入口和 WAITING_HUMAN 展示 |
| `README.md` | 当前 HITL 路由和 CLI 使用说明 |
| `ARCHITECTURE.md` | 当前节点拓扑、checkpoint/HITL 边界和 E07.6 预留点 |
| `docs/E07_5_HITL_INTEGRATION_REPORT.md` | 本报告 |

未修改 `ReviewDecision.from_analysis()`、`StateManager.update_tracking_docs()`、Fact Digest、RAG 或 canonical transaction。

## 2. 新 review 路由

```text
parse_decision
  ├─ PASS
  │    → commit_state
  │    → save_fact_digest
  │    → rag_index
  │    → END
  │
  ├─ NEEDS_REVISION / 旧停机状态
  │    → await_human_review
  │    → interrupt(payload)
  │    → WAITING_HUMAN
  │    → Command(resume={action, feedback})
  │    → STOPPED_NON_PASS
  │    → END
  │
  └─ UNKNOWN / workflow error
       → END (fail-closed)
```

PASS 仍是唯一可进入 `commit_state` 的 route。HITL 节点没有 commit edge；人工 resume 不能将 non-PASS verdict 强制提升为 PASS。

## 3. Interrupt payload 与 resume

`await_human_review` 暴露：

- `novel_id`
- `chapter_index`
- `verdict`
- `planning_level`
- `reasons`
- `t1_issues`
- E07.5 可用 action：`acknowledge`、`stop`

Runner 使用已有：

```text
thread_id = chapter:<novel_id>:<chapter_index padded to 4 digits>
checkpoint = data/novels/<novel_id>/workflow_checkpoints.sqlite
```

没有新增恢复数据库、token 或 session registry。

当 `StateSnapshot.interrupts` 非空，`ChapterWorkflowRunner.run()` 返回：

```text
workflow_status = WAITING_HUMAN
interrupts = [{id, value}, ...]
```

恢复调用：

```python
resume_chapter_workflow(
    novel_id,
    chapter_index,
    {"action": "acknowledge", "feedback": "..."},
)
```

内部使用同一 config/thread ID 调用：

```python
graph.invoke(Command(resume=resume_value), config=self.config)
```

CLI：

```bash
python main.py write <novel> --chapter N --resume "人工反馈"
```

## 4. 安全边界

- `interrupt()` 是 HITL 节点的第一个运行操作；resume 从节点开头重执行时，不会重放 review、文件保存或 canonical side effects。
- UNKNOWN 和 parser/runtime error 继续直接 END。
- `commit_state` 仍二次检查 `verdict == PASS`。
- canonical commit failure 仍阻断 Fact Digest 和 RAG。
- E07.5 resume 仅记录 human decision/feedback 并终止 non-PASS workflow。
- 没有自动 rewrite、style rerun、re-review 或 planning repair。

## 5. E07.6 预留位置

E07.6 可在 `await_human_review` 收到 resume value 后增加 action routing：

```text
NEEDS_REVISION + revise
  → rewrite/style
  → re-review
  → parse_decision

旧停机状态 + approved planning action
  → future planning repair entry

stop/acknowledge
  → END
```

E07.5 没有创建上述节点或循环，也没有让人工输入绕过 ReviewDecision 进入 commit。

## 6. 验证说明

依任务要求，没有编写、修改或运行测试。只执行 Python AST、interrupt/Command 使用点、route/edge、thread_id/checkpointer、受保护逻辑 diff 和 `git diff --check` 等静态核对。静态核对不等同于回归测试通过。
