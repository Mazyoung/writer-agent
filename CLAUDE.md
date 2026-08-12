# Writer-Agent Project Instructions

## Stable Architecture Rules

- Current source code and tests are the highest sources of truth; verify runtime paths before trusting documentation.
- LangGraph owns only the single-chapter production workflow.
- Novel management, volume management, and broad rollback do not belong in the Chapter Graph.
- Canonical planning hierarchy is Book Plan → Volume Plan → Chapter Plan → execution.
- `plot_structure.md` is legacy data and must not be restored as canonical planning state.
- Normal Generate must never overwrite a completed chapter.
- Review `UNKNOWN` or any decision parse failure must fail closed and must never reach commit.
- Runtime, API, and database errors must remain errors; never disguise them as `NEEDS_REVISION`.
- `PASS` is required for canonical commit; commit failure must block Fact Digest and RAG.
- Resume restores the original checkpointed execution; it is not a new Generate operation.
- Canonical Markdown/story state outranks derived SQLite, Chroma, and diagnostic state.
- Planning changes must respect L1/L2/L3 human-authority boundaries; never silently alter higher-level plans.

## Engineering Rules

- Make the smallest coherent change and preserve existing correct behavior.
- Do not add Agents, LLM calls, architectural layers, or future-stage behavior without an explicit requirement.
- Do not perform unrelated refactors or silently change canonical state.
- Verify changes at a level appropriate to their scope, and state clearly what was or was not tested; do not default to expanding the test suite or creating a separate Test Alignment stage.
- `docs/E07_REMAINING_PLAN.md` is the authoritative roadmap for current E07 development; the old migration guide is historical migration reference only.
- Use the Conda `writer` environment for Python; do not use the repository-local `venv`.
- Do not scan the entire repository by default; inspect only files required by the current task.
- 本项目的开发文档和用户可见输出应尽量使用中文，包括 CLI 输出、日志提示和 Git 提交信息；代码标识符、协议字段、第三方原文及必须保持兼容的既有内容除外。

## Current Project Snapshot

- 当前已完成 E07 Story Savepoint、自动 Savepoint、四模型槽位、小说级 Embedding 配置，以及 Agent/Human 两条章节创作链。
- 章节正式边界为：明确 Review 结果 → 人工批准或显式 Override → Canonical Commit → Derivation → `DERIVED_READY`。
- Book Plan / Volume Plan 的完整原始 Markdown 是正式下游上下文；parser 只读取机器元数据，不判断自然语言规划质量或完整性。
- Volume Plan 校验只保留非空、卷号、status enum/lifecycle 等机器约束，不再用字段提取结果或关键词判断“内容缺失/写成章纲”。
- Plan/Prose/Consistency Review 的显式 verdict 是唯一语义结论；Python 不再根据 major/minor/T1/T2/T3 或自然语言正文二次改写 verdict。缺失或非法 verdict 仍 fail closed。
- StyleChecker 及独立手动 style 入口已删除；正式链保持 Writer → Stylist → Prose Reviewer，Review 后修改只由 Writer revision 处理。
- Chapter State 已包含 checkpointed、幂等的 `generation_events`；`chapter_sources.md` 统一记录 Intent、Query Intent、Retrieval 来源、正式上下文、Review/Edit/Regenerate/Override、Canonical 和 Derivation/recovery，并在 `DERIVED_READY` 后按 durable checkpoint 幂等刷新最终状态，不扫描 Markdown 猜测事实。
- Current State 是 Human/LLM Raw Markdown，由独立 SYSTEM Updater 基于 Previous Current State + Canonical 生成完整文档；StateDelta/semantic parser 已退出正式 Current State 路径。
- Atomic Facts 新协议仅含 source ranges + 自包含 fact_text；Python 校验地址并生成 deterministic metadata，独立 batch Verifier 与有限 corrective pass 后才进入 Chroma。API Embedding 由 Runtime 按最多 10 条分批，全部成功后才替换章节旧索引；write/continue 自动恢复首个未完成 Derivation stage。
- 小说级运行策略保存在 `data/novels/<novel_id>/.env`，仅允许 Chapter Mode、Agent Execution、Auto Savepoint 与 RAG Top-K；命令启动时形成只读 snapshot，不污染全局环境，已开始章节仍以 checkpoint 冻结模式为准。
- 主要 Chapter Workflow 阶段使用 `perf_counter()` 计时并把 `duration_ms` 写入 `generation_events`；CLI 汇总节点实际耗时，人工 checkpoint 外部等待不计入。
- TTY 阶段计时由单一 daemon UI thread 原地刷新，非 TTY 安全降级；Prose Agent Edit 使用 WRITE slot，完整接收 Reviewer issues，并按 MUST FIX 与局部修改约束执行后重新 Review。
- Proposal 已不再承诺生成“前50章章纲”；Book Plan 初始化输出的 `vv1` 已修复为 `v1`。
- 2026-08-11 最近一次完整测试结果：`258 passed, 31 subtests passed, 1 warning`；唯一 warning 是未处理的 ChromaDB 依赖弃用提示，无测试失败。
- 当前详细状态、验证记录和 `Next Task` 仍以 `docs/CURRENT_DEVELOPMENT.md` 为准；不要在本文件继续累积阶段历史。

## Continuing Development

1. Read `CLAUDE.md`.
2. Read `docs/CURRENT_DEVELOPMENT.md`.
3. Run `git status`.
4. Run `git log -5 --oneline`.
5. Read only files directly relevant to `Next Task`.

Do not scan the entire repository unless necessary. After each development stage, update `docs/CURRENT_DEVELOPMENT.md`; only refresh the compact snapshot above when the current architecture or handoff baseline materially changes.
