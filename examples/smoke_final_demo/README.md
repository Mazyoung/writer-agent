# smoke_final_demo

这是以通过 Real Smoke 验收的 `smoke_final` 为来源构建的可读资料样例。Chapter 1–3 来自 Story Savepoint `S0003`，Chapter 4 及会随章节推进的状态文件来自同一小说随后完成的 Chapter 4 durable boundary；当前边界为 Chapter 4 `DERIVED_READY`，`tracking/current_state.md` 与其对齐。

本目录展示三类关键路径：

- Chapter 2：作者提供 Human Intent，系统生成 Query Intent、执行 Atomic Fact RAG，并把命中事实及回读原文写入 Writing Context。
- Chapter 3：作者直接提交正文，不提供 Intent；`chapter_sources.md` 与 Writing Context 明确记录 `Intent Status: SKIPPED`、`RAG Status: SKIPPED`、`Skip Reason: human_direct_write`。
- Chapter 4：Agent 生成路径，包含正式 Chapter Intent、Chapter Plan、Canonical 正文、来源记录、Fact Digest 与 `DERIVED_READY` 标记。

建议按以下顺序阅读：

1. `proposal.md`、`settings/world_setting.md`
2. `tracking/book_plan.md`、`tracking/volume_plan.md`
3. `chapters/chapter_0001.md` 至 `chapter_0004.md`
4. Chapter 2 的 `tracking/writing_context_ch0002.md` 与 `sources/chapter_0002/chapter_sources.md`
5. Chapter 3 的 `tracking/writing_context_ch0003.md` 与 `sources/chapter_0003/chapter_sources.md`
6. Chapter 4 的 `briefs/chapter_intent_ch0004.md`、`outlines/chapter_plan_ch0004.md` 与 `sources/chapter_0004/chapter_sources.md`
7. `tracking/current_state.md`、`states/fact_digest_ch0004_example.md`、`states/chapter_0004_derived_ready.json`

为保持仓库轻量且避免把运行态误当成示例协议，本目录没有包含 SQLite、LangGraph checkpoint、Chroma、锁文件、候选稿、draft / revision / styled 中间稿、attempt / repair 产物、临时文件或 Retrieval Trace JSON。它不是完整 Savepoint，不能用于 `savepoint load`。

文件只做示例层机械清理：时间戳 Fact Digest 使用稳定文件名；Chapter 2 的旧段落占位显示按 `S0003` trace 中的真实 `source_ranges` 规范化为 `Pxxxx` 地址；来源文件中的绝对 Retrieval Trace 路径改为说明性相对路径。生产数据未被修改。
