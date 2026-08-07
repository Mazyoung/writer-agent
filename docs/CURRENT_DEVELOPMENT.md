# Current Development

## Current Stage

E07 Story Savepoint + Load Savepoint、自动 Savepoint、四模型槽位与小说级
Embedding 配置已完成。

正式支持两条底层章节创作链与三种用户运行体验：

~~~text
agent + autonomous:
       Intent(optional) → Planner → Plan Review → finite agent edit
       → Writer → Stylist → Prose Review → finite agent edit
       → Canonical → Derivation → DERIVED_READY

agent + supervised:
       Intent(optional) → Planner → Plan Review → Human Checkpoint
       → Writer → Stylist → Prose Review → Human Checkpoint
       → Canonical → Derivation → DERIVED_READY

human / data management:
       Intent(required) → Current State / Historical RAG → Writing Context
       → Human Candidate → Consistency-only Review → Final Human Approval
       → Canonical → Derivation → DERIVED_READY
~~~

作者拥有最终决定权。系统 Review/Consistency 是决策辅助：非 PASS/WARN 不会自动提交，但作者可以在看到明确警告后通过独立二次确认 override。原始 verdict 保持不变，不会伪造成 PASS/CLEAN。

Story Savepoint 将正式章节完成后的完整小说创作世界保存为 immutable READY 快照；Load 可在任意 READY Savepoint 之间双向恢复，不删除或修改其他 Savepoint。当前仅支持 `branch_id=main`，Branch/Fork/Merge 未实现。

## E07 Story Savepoint Closure

- `StorySavepointManager.create/list/verify/load` 提供中性底层接口；Savepoint ID 按最新正式章节生成，例如 `S0040`。
- Create 只接受最新 canonical、Current State 与最终 `chapter_NNNN_derived_ready.json` marker 一致的当前世界；LangGraph 只用于阻止仍在进行的 execution，不再作为历史章节完成事实。
- `story_savepoints/<ID>/` 使用 staging → 文件/SQLite/Chroma hash 与 integrity verify → READY 流程。READY Load 路径不会修改目标或其他 Savepoint。
- 文件快照覆盖 novel creative/project tree，并排除 `story_savepoints/`、temp/staging/cache、operation lock、`LOAD_ERROR` 和 workflow checkpoint infrastructure。
- `state.db` 使用 SQLite online backup API 快照并执行 `PRAGMA integrity_check`；Load 恢复同一份 Markdown、SQLite projection 与最终 DERIVED_READY marker。
- Chroma 对 `atomic_facts_v2` 与 `author_knowledge_v1` 按 `novel_id + branch_id=main` 逻辑导出 ids/documents/metadatas/embeddings；Load 原样恢复 embeddings，不调用 LLM、embedding 或 Markdown rebuild。
- Create/Load 与章节 run/resume/repair 共用 novel-level exclusive operation lock。双重恢复失败会写入 `LOAD_ERROR.json`，并由 lock 与 FileStore 阻断后续创作写入。
- Load 修改工作区前创建隐藏 internal safety snapshot；中途失败自动恢复。成功后删除 safety snapshot，并仅删除当前 novel 中目标章节之后的 LangGraph threads。
- CLI 提供 `savepoint create|list|verify|load`。Load 没有 `--yes/--force` 绕过，并强制依次输入 novel name 与精确 `LOAD <ID>`。
- `AUTO_SAVEPOINT_EVERY=N` 可在章节达到 `DERIVED_READY` 且章号整除 N 时复用正式 Create/Verify 路径自动创建；`0` 为关闭。run/resume/repair 一致执行，且自动创建发生在章节 operation lock 释放后。
- Branch/Fork/Merge、压缩/去重、云同步和真实 API smoke test 均未扩展。

## Runtime Configuration Closure

- LLM 配置保持 `ARCHITECT`、`PLAN`、`WRITE`、`SYSTEM` 四个槽位；每个槽位独立解析 provider、API key、Base URL、model 与 max_tokens，并支持 `deepseek`、`openai_compatible`、`anthropic`。
- SYSTEM 是默认 connection；子 slot connection 留空时逐字段继承。显式 provider 使用自己的 connection，model 不继承。默认 max_tokens 为 32768 / 16384 / 32768 / 16384。
- Writer 与 Stylist 共用 WRITE；StateManager 使用 SYSTEM。所有 provider 都使用 slot max_tokens，不发送 Thinking、Extended Thinking、reasoning_effort 或其他 reasoning-specific 参数。
- Query Intent Builder 是 PLAN 子配置；`QUERY_INTENT_*` 留空时逐字段继承 `PLAN_*`，实际调用时才执行独立 preflight。
- 正常 CLI 强制要求项目根目录 `.env` 存在，并在业务初始化前 fail fast；`.env.example` 覆盖全部正式配置项。真正调用 LLM 前只校验当前 slot，错误以中文列出字段和环境变量且不泄露 Key。
- `EMBEDDING_MODE=local|api` 只决定下一本新小说的初始化方式。local 保留 Chroma 内置 Embedding；api 使用通用 OpenAI-compatible Embedding API。
- init 在创建小说数据或调用初始化 LLM 前执行实际 probe 并要求显式确认。API Key、地址、model 或 dimensions 错误均 fail fast，拒绝确认不会留下部分初始化状态。
- mode、model、实际 dimensions 以及是否发送 dimensions 参数保存在小说内部不可变配置中，且位于 Story Savepoint Load 不覆盖的位置；API Key 与 API 地址不持久化。
- 已初始化小说只读取其内部 Embedding 配置，忽略之后的 `.env EMBEDDING_MODE/MODEL/DIMENSIONS` 变化。缺失内部配置视为不兼容状态，不做 legacy fallback。
- API 模式由 Writer-Agent 显式生成向量并传给 Chroma `add/query`；写入前校验向量数量和固定维度，失败不会 fallback 到 local。
## E07.9 Production Closure

- The Python and Markdown call chain uses canonical_source_path / Canonical Source from Chapter Workflow through StateManager and CurrentStateStore.
- SQLite retains the compatibility column name styled_source_path; its value is the canonical source path and no naming-only migration was added.
- Derivation receives Canonical Prose, Previous Current State, and the current ACTIVE Volume Plan.
- Canonical Prose remains the only source for StateDelta, Fact Digest / Atomic Facts, and Current State. Volume Plan is restricted to the advisory VolumeProgress decision.
- close-volume ignores CONTINUE / READY_TO_CLOSE / UNKNOWN advice, but refuses closure unless the latest canonical chapter has a valid durable DERIVED_READY marker; the error directs the user to derivation repair.
- Volume Plan validation 只保留非空、卷号、status enum/lifecycle 等机器约束；自然语言字段未被 parser 提取或正文出现“章节/逐章/chapter assignment”等词汇都不会被 Python 判为内容缺失或章纲化。

## E07.9.1 Human Author Mode Closure

- `CHAPTER_MODE=agent|human` 是唯一创作模式配置；默认 `agent`，非法值 fail fast，新执行把 mode 固定进 checkpoint，老 checkpoint 缺字段时保持 Agent 语义。
- `ChapterRetrievalService` 使用聚焦 Query Intent Builder；唯一 Embedding Query 是生成后的 Query Intent。Atomic Fact Top-K 与 Canonical paragraph range ±1 expansion 保持不变。
- Human 将 Intent、Current State、召回 facts、有限历史原文和 supplemental Author Knowledge 写入 `tracking/writing_context_chNNNN.md`，随后在 `human_writing` interrupt 等待 `--action submit --file <正文文件>`。
- 人工正文以 `candidate_text` / candidate staging 文件进入现有 checkpoint；不调用 Planner、Writer、Stylist，也不会在提交时直接写 Canonical。
- Consistency-only Review 复用 `StateManager` 的 LLM client、模型、保存和调用基础设施，只检查硬连续性，并复用已生成 Writing Context；不会执行第二轮 Historical RAG，也不评价文学质量。
- Consistency `CLEAN` 与 Agent Review `PASS` 的正常 approve 直接进入统一 Candidate → Canonical seam，不要求 override。
- Consistency `WARN` 或 Agent Review `NEEDS_REVISION` 的 approve 只进入 `review_override_confirmation` interrupt；只有 `confirm_override` 才设置 `review_override_confirmed=True` 并允许 Canonical。原 `verdict` / warnings 始终保留。
- Human manual edit 会清空旧 Consistency 结果并重新执行 Consistency Check；override confirmation 不会重跑昂贵 Review。
- Human 和 Agent 最终共用 `commit_canonical_prose` 以及完整 Derivation、Current State、Fact Digest、Volume Progress、chapter_sources、Chroma sync 和 repair-derivation 路径。
- Human 与 Agent 统一使用 `chapter_sources.md` 记录 Intent、Query Intent、Retrieval sources、正式上下文、Review/Edit/Regenerate/Override、Canonical 与 Derivation；报告只机械汇总 checkpointed `generation_events` 和 Retrieval 结构化结果，不再扫描 Chapter Plan 猜测 adopted/candidate-only。
- `generation_events` 位于 Chapter Workflow State，使用稳定 workflow counter/round/revision/retry 或固定 lifecycle stage 构造 event ID；checkpoint replay、continue 和 derivation recovery 按 ID 幂等合并。
- ReviewDecision 不再因 analysis prose 中的 major/minor/T1/T2/T3 推翻显式 verdict；Consistency 只消费 `## 一致性结论` 的明确 CLEAN/WARN，缺失或非法仍 fail closed。
- StyleChecker 已退出正式 Chapter Workflow，仅保留手动 lint/debug 用途；无生产引用的 `quality_reviewer.txt` 与 `consistency_guard.txt` 已删除。
## Architecture CI Baseline

The push/PR gate is frozen around stable functional contracts and safety invariants:

- planning hierarchy, plan review, and human interrupt/resume;
- Review non-PASS remains human-controlled; Review PASS requires final author approval;
- canonical create-once/overwrite protection and canonical-only historical reading;
- canonical commit followed by derivation, visible derivation failure, idempotent repair, and DERIVED_READY;
- deterministic Current State, Fact Digest / Atomic Fact RAG, and Author RAG fail-closed behavior;
- advisory VolumeProgress, close-volume consistency guard, next-volume, implicit DRAFT-to-ACTIVE activation, and non-chapterized Volume Plans.

Tests tied to the retired src.core.orchestrator, automatic revision, PASS-to-direct-commit, styled-as-canonical, E07.2 graph node names/topology, and old multi-Markdown tracking rollback behavior were removed. Reusable parser, storage, RAG, FakeLLM, mock, and fixture coverage was retained or updated to current entry points.

## Real Smoke Test Workflow Closure

- Proposal 初始化只使用当前 `proposal.md`；作者直接编辑原文件。Volume Plan 生成后只提示直接审阅并进入 Chapter Planning，不再提供或要求 `approve-volume` CLI。
- 初始化、新卷和 Chapter Planner 向下游传递完整正式 Markdown。Token guard 按 CJK 约 1 char/token、非 CJK 约 4 chars/token，再增加 15% margin，并与当前 slot `MAX_TOKENS` 合并判断；达到 100,000-token 安全预算时在 LLM 调用前中文拒绝，绝不静默截断。
- `AGENT_EXECUTION=autonomous|supervised` 只调整 Agent workflow 的人工检查策略；`CHAPTER_MODE=human` 继续复用同一 Canonical/Derivation seam。
- Review payload 与 CLI 显示完整 issues/reasons。Plan failure 使用 `agent_edit / human_edit / restart`；Prose failure额外支持 `regenerate_prose`。自主模式只做有限 `agent_edit`，不会自动 restart 或 regenerate。
- `restart` CLI 与 Review action 共用 `ChapterWorkflowRunner.restart()`：保留 Canonical Chapter Intent 和既有正式历史，删除本章 Plan、Prose、Review、Writing Context、RAG trace 与 checkpoint；Canonical 已存在时 fail closed。
- `plan`、`write`、`continue` 和 continuous `run` 共用章节进度/Derivation gate。Canonical 尚未达到 `DERIVED_READY` 时禁止后续章节。
- `python main.py continue <novel>` 是统一当前状态入口：优先返回 WAITING_HUMAN、恢复现有 Pre-Canonical checkpoint、调用同一 Derivation repair、处理 Volume/Human 边界，最后才创建下一章。
- `python main.py run <novel> --to-chapter N` 仅用于 Agent + autonomous，循环复用同一 continuation router；目标明确、可重复执行，并跳过已完成章节。
- 最终 DERIVED_READY marker 只在 `sync_chroma` 成功后原子写入。Savepoint Load 恢复 marker；`continue/plan/write/run/close-volume/Savepoint create` 统一读取该正式事实。
- Canonical 后的 `CANONICAL_COMMITTED / SEMANTICS_DERIVED / CURRENT_STATE_PERSISTED / FACT_DIGEST_PERSISTED / VOLUME_PROGRESS_PERSISTED / CHAPTER_SOURCES_PERSISTED` 均可从 checkpoint 的首个未完成阶段继续；无法安全确定位置时 fail-closed。
- Review verdict 仅保留 `PASS / NEEDS_REVISION / UNKNOWN`。supervised PASS 的 Plan checkpoint 提供 `approve/agent_edit/human_edit/restart`，Prose 额外提供 `regenerate_prose`；PASS 后 `agent_edit` 强制要求非空 human feedback。
- Query Intent Builder 输入完整 Volume Plan、共享的约 1500 字完整段落窗口、完整 Current State 和原始 Human Intent。Embedding 只使用 Query Intent；达到 10000 字时携带明确压缩反馈重试一次，第二次仍严重超长才 fail-closed。
- Writer、Planner、Query Intent Builder 共用 `text_windows.previous_chapter_end()`：从上一章 Canonical 末段向前按完整段落累计约 1500 字，允许为保留完整段落略超目标；Writer prompt 不再二次截断。
- Writer、Stylist、Plan Review、Prose/Consistency Review 不再静默裁剪 World Setting、Book Plan、Volume Plan、Chapter Plan、Current State 或 Human Intent；正式上下文过大时统一由 Token Guard 在模型调用前中文拒绝。
- Chapter Workflow / standalone Planning Service 先生成 Query Intent，再调用 `ChapterRetrievalService.retrieve(chapter_index, query_intent)`；Retrieval Service 仅负责 Embedding、Atomic Fact Top-K、Canonical paragraph ±1 expansion、Author Knowledge、trace 和 outcome。

## Verification

- 本轮规划/parser/raw Markdown 针对性测试：73 passed。
- 本轮 Chapter Workflow / Human / Derivation 针对性测试：25 passed，3 subtests passed。
- 完整 pytest suite：225 passed，20 subtests passed。
- 仅有 ChromaDB 依赖的既有 `asyncio.iscoroutinefunction` DeprecationWarning；本轮未处理无关技术债。

## Next Task

使用真实模型凭证执行 Real End-to-End Smoke Test。未经明确任务，不调用真实 API。
