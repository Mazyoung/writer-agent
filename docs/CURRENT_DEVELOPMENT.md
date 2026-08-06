# Current Development

## Current Stage

E07 Story Savepoint + Load Savepoint 已完成。

正式支持两种完整章节创作模式：

~~~text
agent: Intent(optional) → Current State / Historical RAG → Planner → Plan Review
       → Writer → Stylist → Full Prose Review → Final Human Approval
       → Canonical → existing Derivation → DERIVED_READY

human: Intent(required) → Current State / Historical RAG → Writing Context
       → Human Candidate → Consistency-only Review → Final Human Approval
       → Canonical → existing Derivation → DERIVED_READY
~~~

作者拥有最终决定权。系统 Review/Consistency 是决策辅助：非 PASS/WARN 不会自动提交，但作者可以在看到明确警告后通过独立二次确认 override。原始 verdict 保持不变，不会伪造成 PASS/CLEAN。

Story Savepoint 将正式章节完成后的完整小说创作世界保存为 immutable READY 快照；Load 可在任意 READY Savepoint 之间双向恢复，不删除或修改其他 Savepoint。当前仅支持 `branch_id=main`，Branch/Fork/Merge 未实现。

## E07 Story Savepoint Closure

- `StorySavepointManager.create/list/verify/load` 提供中性底层接口；Savepoint ID 按最新正式章节生成，例如 `S0040`。
- Create 只接受最新 canonical、Current State、derived marker 和 LangGraph terminal status 全部一致且达到 `DERIVED_READY` 的当前世界；pending interrupt、未完成 execution、derivation error 和补建过去章节均 fail closed。
- `story_savepoints/<ID>/` 使用 staging → 文件/SQLite/Chroma hash 与 integrity verify → READY 流程。READY Load 路径不会修改目标或其他 Savepoint。
- 文件快照覆盖 novel creative/project tree，并排除 `story_savepoints/`、temp/staging/cache、operation lock、`LOAD_ERROR` 和 workflow checkpoint infrastructure。
- `state.db` 使用 SQLite online backup API 快照并执行 `PRAGMA integrity_check`；Load 恢复同一份 Markdown、SQLite projection 与 derived marker。
- Chroma 对 `atomic_facts_v2` 与 `author_knowledge_v1` 按 `novel_id + branch_id=main` 逻辑导出 ids/documents/metadatas/embeddings；Load 原样恢复 embeddings，不调用 LLM、embedding 或 Markdown rebuild。
- Create/Load 与章节 run/resume/repair 共用 novel-level exclusive operation lock。双重恢复失败会写入 `LOAD_ERROR.json`，并由 lock 与 FileStore 阻断后续创作写入。
- Load 修改工作区前创建隐藏 internal safety snapshot；中途失败自动恢复。成功后删除 safety snapshot，并仅删除当前 novel 中目标章节之后的 LangGraph threads。
- CLI 提供 `savepoint create|list|verify|load`。Load 没有 `--yes/--force` 绕过，并强制依次输入 novel name 与精确 `LOAD <ID>`。
- 自动 Savepoint、Branch/Fork/Merge、压缩/去重、云同步和真实 API smoke test 均未扩展。
## E07.9 Production Closure

- The Python and Markdown call chain uses canonical_source_path / Canonical Source from Chapter Workflow through StateManager and CurrentStateStore.
- SQLite retains the compatibility column name styled_source_path; its value is the canonical source path and no naming-only migration was added.
- Derivation receives Canonical Prose, Previous Current State, and the current ACTIVE Volume Plan.
- Canonical Prose remains the only source for StateDelta, Fact Digest / Atomic Facts, and Current State. Volume Plan is restricted to the advisory VolumeProgress decision.
- close-volume ignores CONTINUE / READY_TO_CLOSE / UNKNOWN advice, but refuses closure unless the latest canonical chapter checkpoint is DERIVED_READY; the error directs the user to derivation repair.
- Volume Plan validation rejects structural chapter assignment fields/headings/tables, while preserving arbitrary human sections and notes, including prose that merely mentions “逐章”.

## E07.9.1 Human Author Mode Closure

- `CHAPTER_MODE=agent|human` 是唯一创作模式配置；默认 `agent`，非法值 fail fast，新执行把 mode 固定进 checkpoint，老 checkpoint 缺字段时保持 Agent 语义。
- `ChapterRetrievalService` 不再导入或实例化 `ChapterPlanner`。Agent Planner 只存在于 `plan_chapter`；Human 使用同一 Atomic Fact RAG、同一 `RAG_TOP_K` 和有限 Canonical 段落展开。
- Human 将 Intent、Current State、召回 facts、有限历史原文和 supplemental Author Knowledge 写入 `tracking/writing_context_chNNNN.md`，随后在 `human_writing` interrupt 等待 `--action submit --file <正文文件>`。
- 人工正文以 `candidate_text` / candidate staging 文件进入现有 checkpoint；不调用 Planner、Writer、Stylist，也不会在提交时直接写 Canonical。
- Consistency-only Review 复用 `StateManager` 的 LLM client、模型、保存和调用基础设施，只检查硬连续性，并复用已生成 Writing Context；不会执行第二轮 Historical RAG，也不评价文学质量。
- Consistency `CLEAN` 与 Agent Review `PASS` 的正常 approve 直接进入统一 Candidate → Canonical seam，不要求 override。
- Consistency `WARN` 或 Agent Review `NEEDS_REVISION` / `HALT` 的 approve 只进入 `review_override_confirmation` interrupt；只有 `confirm_override` 才设置 `review_override_confirmed=True` 并允许 Canonical。原 `verdict` / warnings 始终保留。
- Human manual edit 会清空旧 Consistency 结果并重新执行 Consistency Check；override confirmation 不会重跑昂贵 Review。
- Human 和 Agent 最终共用 `commit_canonical_prose` 以及完整 Derivation、Current State、Fact Digest、Volume Progress、chapter_sources、Chroma sync 和 repair-derivation 路径。
- Human `chapter_sources` 记录系统实际提供的 historical context 与 Canonical paragraph/source，不依赖或伪造 Chapter Plan/adopted facts；Agent adopted-facts 语义保持不变。两种报告都记录原始审阅结论与 override 审计事实。
## Architecture CI Baseline

The push/PR gate is frozen around stable functional contracts and safety invariants:

- planning hierarchy, plan review, and human interrupt/resume;
- Review non-PASS remains human-controlled; Review PASS requires final author approval;
- canonical create-once/overwrite protection and canonical-only historical reading;
- canonical commit followed by derivation, visible derivation failure, idempotent repair, and DERIVED_READY;
- deterministic Current State, Fact Digest / Atomic Fact RAG, and Author RAG fail-closed behavior;
- advisory VolumeProgress, close-volume consistency guard, next-volume, approve-volume, and non-chapterized Volume Plans.

Tests tied to the retired src.core.orchestrator, automatic revision, PASS-to-direct-commit, styled-as-canonical, E07.2 graph node names/topology, and old multi-Markdown tracking rollback behavior were removed. Reusable parser, storage, RAG, FakeLLM, mock, and fixture coverage was retained or updated to current entry points.

## Verification

- Story Savepoint destructive isolated integration / CLI tests：11 passed。
- Savepoint + existing chapter workflow focused regression：47 passed。
- 完整保留 unittest suite：160 passed。
- `py_compile` 与 `git diff --check` 通过。

## Next Task

E07 Real End-to-End Smoke Test。未经明确任务，不调用真实 API。
