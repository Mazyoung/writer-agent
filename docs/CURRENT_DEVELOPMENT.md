# Current Development

## Current Stage

E07 Story Savepoint + Load Savepoint、自动 Savepoint、四模型槽位与小说级
Embedding 配置、小说级运行策略隔离、Prose Agent Edit 约束及阶段 Live Timer 已完成。`smoke_test` Chapter 1-2 已完成；Chapter 3 保持 Prose Review #3 / `NEEDS_REVISION` / `WAITING_HUMAN`，本轮未推进该 checkpoint。

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
- API 模式由 Writer-Agent 显式生成向量并传给 Chroma `add/query`；`NovelEmbeddingRuntime` 按单请求最多 10 条自动分批，保持输入/向量顺序，并继续校验每批数量、最终数量和固定维度；任一批失败即传播原错误，不会 fallback 到 local 或返回部分结果。
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

## Real Smoke Remediation Closure

- 最近提交：`a4af8fe 修复空响应并收口章节入口`，已推送到 `origin/main`；当前工作树应保持干净。
- `ModelProviderClient` 对 OpenAI-compatible、DeepSeek 和 Anthropic 的 None、空字符串、纯空白或无文本响应 fail closed；错误仅保留 provider、model、finish/stop reason 等非敏感诊断。`BaseAgent` 在任何 save/save_canonical 和 interceptor 前保留 provider-independent 非空最后防线，空响应不得创建或覆盖文件。
- 正式章节入口统一为 `python main.py write <novel> --chapter N`；`init --confirm` 直接引导 write。`plan` 保留为 standalone/debug 工具，其 Chapter Plan 不会被正式 write 接续或采用，不写 generation_events 或独立 provenance。
- Chapter Planner、Writer 与 Query Intent 提示词已移除旧项目的固定题材、文风、范文和节奏硬编码。Query Intent 明确 Current State/Canonical History 是已发生历史，Book/Volume Plan 仅是未来规划参考，不得将未来事件包装成历史检索目标。
- 正式 Workflow 的 `PLAN_CREATED.details.context_sources` 直接记录 Planner 本次实际输入的 World Setting、Book Plan、Volume Plan、Current State、Previous Chapter End、Human Intent 与 RAG flags；`render_chapter_sources()` 仅机械投影这些 flags，不再从 RAG 或其他 state 推断来源。旧 checkpoint 无 flags 时显示未记录。
- 已删除本次 smoke 产生的 0-byte `data/novels/smoke_test/outlines/chapter_plan_ch0001.md`；Proposal、World Setting、Book Plan、Volume Plan 和 Current State 保持不变。本轮未调用真实模型 API。

## Canonical 后 Derivation / RAG Closure

- `tracking/current_state.md` 现为面向 Human/LLM 的 Raw Markdown；正式路径由独立 SYSTEM Current State Updater 读取 Previous Current State + Canonical Chapter，生成完整 Updated Current State，Python 只做非空、checkpoint、hash compare 与原子保存。`StateDelta.from_analysis`、`CurrentState.from_markdown` 与 SQLite semantic projection 仅保留 legacy compatibility，不再控制正式 Derivation。
- Atomic Fact Deriver 是与 Updater 分离的 SYSTEM 请求，只输出 `## Atomic Facts` 下的一 fact 一 bullet：`- [P0001-P0002; P0010-P0011] 自包含自然语言事实。` LLM 不再生成 fact_id、chapter、path、type、entities、relationship/status 等 metadata。
- Source Range parser 机械兼容补零、可选方括号、空格、`- / ~ / — / –` 与合理多范围分隔；统一成 `[{start, end}]`。Python 严格验证 start >= 1、end >= start、地址存在，并从当前 Canonical/source path 构造 source metadata；自然语言位置描述不会被猜测，非法地址只做一次定向 LLM repair 或 DROP。
- 新 Fact 先由 Python按 range 提取 Canonical excerpt，再用独立 SYSTEM Verifier 一次 batch 审核。只解析明确 `VERIFIED / INCORRECT / INSUFFICIENT`；Verifier 协议非法最多一次纯协议纠正，不扫描自由分析猜 verdict，也不审核 importance/worth keeping。
- `INCORRECT` 最多一次 targeted repair 后复核；`INSUFFICIENT` 先机械扩展每个 source range 的 ±1 段落邻域再复核，仍不足时才做未使用过的 targeted repair。每条 Fact 最多三轮真实性 verification、一次 repair；明确 DROP、repair 后无可靠独立事实或最终仍未 VERIFIED 均丢弃，不强制数量。
- 只有 VERIFIED Fact 写入最终 Fact Digest 与 `atomic_facts_v2`。Chroma document 仅为 `fact_text`；新 metadata 为 deterministic `fact_id / chapter_index / source_path / source_ranges / canonical_hash`（另含 novel/branch/source_type/digest path 系统字段），旧 type/entities/start/end 只在 legacy data 时兼容保存/读取。
- Chroma chapter replacement 会先准备全部新文档，并在 API 模式下完成全部新 embeddings，随后才删除旧 ids 并写入；Embedding 生成失败时旧索引保持不变，成功重试不会 duplicate。零个可靠 Fact 也可完成明确空 Digest。`quarantine_fact` 可立即把后续确认错误的 ACTIVE Fact 从正常向量索引移除，再复用 Canonical source + targeted repair + verification，fact_text 变化才需要新 embedding。
- Derivation checkpoint 现依次为 Current State 生成/保存、Atomic Fact 生成、Fact Verification/保存、Volume Progress、Chapter Sources、Chroma。每阶段幂等；Current State 成功后 Fact 失败不会重写 Current State；write/continue 自动从首个未完成派生阶段继续，`repair-derivation` 仅保留 maintenance/debug。
- active derivation failure 按 stage 单值保存；同 stage 同错误不重复，错误变化替换当前 active failure，stage 成功即清除。CLI 显示 Chapter、Canonical Commit、Failed Stage、真实异常、Recovery State Saved，并提示 write/continue 自动恢复。
- TokenGuard 生产代码保持已确定 warning-only 策略；旧阻断语义测试已迁移为严格验证超限估算、文档诊断、warning、配置上限和继续完整发送提示。
- Real Smoke 暴露的 `input.contents` batch size > 10 已在 Embedding Runtime transport 层收口；AtomicFactStore 不承担 Provider batch 业务规则。
- 本轮未再次执行 Real Smoke、未重新生成 Chapter 1/2、未调用真实模型 API；既有 Chapter 1 `DERIVED_READY` 与 Chapter 2 Plan Review PASS / `WAITING_HUMAN` checkpoint 保持不变。

## Real Smoke WAITING / Provenance Closure

- `write`、`continue`、`restart` 的前台单章结果统一把 `WAITING_HUMAN` 交给同一个 in-process interactive handler；helper 使用明确的 novel/chapter/result 参数，不依赖 `args.chapter`，并可在 Plan Review、Prose Review 等多次 interrupt 间保持同一进程与章号。
- `continue` 仍由 `NovelContinuationService.route()` 优先返回现有 interrupt；已有 Chapter 2 WAITING checkpoint 不运行 Planning、Retrieval、Review，也不会自动 approve。CLI 显示的 action 使用 parser 可直接接受的 `agent_edit / human_edit / regenerate_prose / confirm_override` 名称。
- `sync_chroma` 成功写入 durable marker 后会用已合并的 checkpoint events 刷新 `chapter_sources.md`，清除已恢复 RAG 的当前错误字段，并保留 `DERIVATION_FAILED / DERIVATION_RECOVERED` 历史。对本轮之前已经 READY 的 stale report，`continue` 仅在内容不同的时候按现有 checkpoint 幂等修正，不推进节点或新增 event。
- Derivation recovery 日志不再把 `update_state(..., as_node=predecessor)` 称为“首个未完成阶段”，改为明确显示恢复到哪个 checkpoint 并继续后续 Derivation；恢复算法未变。
- `status` 新增最新相关章节、Canonical 正文、派生/WAITING 状态、失败阶段或人工检查点及下一动作。状态读取使用不建目录的 FileStore、SQLite `mode=ro` 与只读 checkpoint 连接，不运行或恢复 workflow。

## Novel Runtime Policy & Observability Closure

- 新小说初始化后创建 `data/novels/<novel_id>/.env`，materialize Root `.env` 当前有效的 `CHAPTER_MODE / AGENT_EXECUTION / AUTO_SAVEPOINT_EVERY / RAG_TOP_K`。既有小说缺少该文件时不自动创建或迁移。
- `NovelRuntimePolicy` 是 immutable command-level snapshot；使用 allowlist `dotenv_values` 解析，不调用 `load_dotenv(..., override=True)`，不修改 `os.environ` 或 global Settings，也不能覆盖 Provider、Model、Key、Base URL 或 immutable Embedding identity。
- 优先级为 Novel `.env` > Root `.env` 的有效 Settings > 代码默认值。`write / continue / restart / run --to-chapter` 每次命令读取一次；同一次 continuous run 的所有新章节共享同一 snapshot，不热加载。
- 新章节把 `chapter_mode / agent_execution`（以及本章 RAG/Savepoint 策略值）写入初始 checkpoint。已有 checkpoint 恢复时直接 `invoke(None)`，继续使用已冻结模式；restart 删除 pre-canonical workflow 后使用该命令读取的最新策略。
- Query Intent、Retrieval、Planning、Plan Review、Writing、Styling、Prose Review、Canonical Commit、Current State、Atomic Fact Derivation、Fact Verification、RAG/Embedding 均输出带 Chapter Index 的开始/完成提示。
- 计时使用 `time.perf_counter()`，`duration_ms` 复用 checkpointed `generation_events`。Plan/Prose Review 的耗时先进入 state，再由对应 Review event 持久化；多轮 Writing/Styling/Review 使用稳定 attempt identity 幂等累计。
- CLI 在 `DERIVED_READY` 后按 event 汇总系统节点执行时间。LangGraph interrupt 外部等待不在任何 node timing 区间内，因此 `WAITING_HUMAN` 停留时间不会污染阶段或 Total。
- 本轮未增加并发、第二套 tracing、热加载或 Provider/Embedding 配置迁移，也未修改 Canonical、Derivation、Review、TokenGuard 和章节生成语义。

## Prose Agent Edit & Live Timer Closure

- Prose Agent Edit 仍使用 `deepseek_writer` 的 WRITE slot。输入链为 checkpoint 最新 `styled_text` + approved Chapter Plan + 完整 `t1_issues / review_issues / review_reasons` + 可选 human feedback；输出继续回写 `styled_text`、保存 revision/styled 文件，再由原 Prose Reviewer 审阅。
- 真实缺口是旧链路没有显式传入完整 `review_issues`，prompt 也只要求泛化的 L1 修订，未把 Reviewer 已给出的正确正式事实定义为不可改写的 MUST FIX。现在按稳定顺序去重合并全部 Reviewer issues；`human_feedback=""` 不会删除任何审阅要求。
- Editor prompt 明确其职责是受约束的局部修订，不是重新生成；Canonical/Current State/时间线/人物地点物品连续性、元叙述泄漏与明确逻辑错误必须逐项解决。Reviewer 已给出正确事实时禁止第三种解释、新设定绕行或反改正式事实，并要求模型内部完成修订前识别、修订后逐项自检，不把清单写入 prose。
- 未额外塞入完整 Current State：当前 Reviewer issue 已携带冲突位置、正确事实和修复方向，扩展上下文没有证据基础；本轮也未新增 LLM 调用或第二套 consistency checker。
- Autonomous Prose Review 只在 `review_round <= 2` 时自动 Agent Edit，之后进入人工 checkpoint；supervised 模式每轮都需用户显式选择，因而不存在无人值守无限循环，但用户可以人工重复发起修订，当前没有额外硬上限。
- Agent Prose Edit 与 Agent Plan Edit 已接入现有 `perf_counter()` / `generation_events.duration_ms` 体系；CLI 最终汇总新增对应阶段。每秒刷新只存在于 terminal UI，不写 event、SQLite 或 tracking 文件。
- `src/utils/live_timer.py` 只在 `sys.stdout.isatty()` 时用一个 daemon UI thread 约每秒刷新 `\r + ANSI clear-line`。临时 stdout proxy 使用锁，在现有日志写入前清除动态行、原样输出后重绘；停止时清行并恢复 stdout。非 TTY/CI 不安装 proxy、不输出控制字符，仅保留原开始/完成日志与最终 duration。
- Timer cleanup 接入 node exception guard 与 Derivation failure path；UI 自身异常只关闭动态显示，不能吞掉或改写业务异常。WAITING_HUMAN 位于 node 计时区间之外，仍不进入任何阶段耗时。


## Verification

- Supervised Plan/Prose Review 的 `PASS + agent_edit` 现在于统一 runner resume seam 在 `Command(resume=...)` 前强制要求非空 feedback；拒绝时 checkpoint 不消费、Graph/LLM 不执行。`NEEDS_REVISION` 仍允许空 feedback 并只使用 Reviewer issues，PASS 中 advisory/T3 notes 不替代作者修改意见。
- Embedding batching、WAITING_HUMAN 前台交互、既有 checkpoint 恢复、chapter_sources finalization、recovery 日志、只读 status 及既有 Derivation/RAG/TokenGuard 回归通过。
- 完整 pytest suite：264 passed，37 subtests passed，1 warning。
- 唯一 warning 是 ChromaDB 依赖的既有 `asyncio.iscoroutinefunction` DeprecationWarning；本轮未处理无关技术债。
- 本轮未调用真实模型 API；仅完成代码整改和本地回归验证。

## Next Task

后续真实凭证模式隔离 Smoke 直接新建 `smoke_auto`（agent + autonomous）与 `smoke_human`（human），分别编辑其小说级 `.env`；原 `smoke_test` 保留为 supervised 已验收样本，Chapter 3 的现有 Prose Review / `WAITING_HUMAN` checkpoint 不得被本地测试推进。未经明确任务，不调用真实 API。
