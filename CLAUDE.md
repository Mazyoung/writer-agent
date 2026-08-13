# Writer-Agent AI 开发入口

## 1. Source of Truth

- 事实来源优先级：生产代码 > 自动测试 > `docs/DEVELOPER_GUIDE.md` > `README.md`。
- `README.md` 是使用者手册；`docs/DEVELOPER_GUIDE.md` 是当前生产架构与维护规范。
- `examples/smoke_final_demo/` 是通过 Real Smoke 验收的只读参考样例，不是可加载 Savepoint。
- 文档与实现冲突时，以代码和测试为准，并在同一变更中修正文档。

## 2. Stable Architecture Invariants

- LangGraph 只负责单章生产；小说管理、卷管理与 Story Savepoint 不进入 Chapter Graph。
- 正式规划层级为 Book Plan → Volume Plan → Chapter Plan → execution。
- Canonical Markdown / durable story state 高于 SQLite、Chroma、checkpoint 与诊断数据。
- 普通生产流程不得覆盖已 Canonical 的章节；Canonical 后失败应恢复 Derivation，不得重写正文。
- 正常提交要求 Review `PASS`（Human Mode 为 Consistency `CLEAN`）并获得 Final Human Approval。
- 非 `PASS` / `WARN` 只能经显式 Review Override 与 `confirm_override` 授权提交。
- `UNKNOWN`、解析失败与运行时错误一律 fail closed，不得伪装成可接受 verdict。
- Review、Canonical、Derivation、supervised / autonomous 的职责边界不得静默改变。
- Agent Mode 要求 Query Intent 与 RAG；Human Mode 可无 Intent 直接写作，但必须记录 Intent/RAG `SKIPPED`。
- `continue` 只推进合法状态；`restart` 重做当前章并保留 Intent；`clean` 放弃未完成章；Savepoint Load 恢复整个小说世界。

## 3. Engineering Rules

- 做满足需求的最小一致变更，不新增未要求的 Agent、LLM 调用、架构层或未来功能。
- 不做无关重构，不迁移已退出的旧规则，不静默改动正式故事数据。
- Runtime、Provider、API、数据库错误保持技术错误语义；可恢复失败不得消费 durable checkpoint。
- 使用 Conda `writer` 环境；不要使用仓库内 `venv`。
- 用户可见输出、开发文档和提交信息优先使用中文；协议字段、标识符和兼容文本除外。
- 测试强度与变更风险匹配；提交前至少运行相关测试、`git diff --check`，任务要求时运行全量测试。
- 保留用户已有改动，先检查 `git status`，不要使用破坏性 Git 命令。

## 4. Current Documentation Entry

- 使用与运维：[`README.md`](README.md)
- 架构、状态机、恢复与测试：[`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md)
- 可读数据样例：[`examples/smoke_final_demo/`](examples/smoke_final_demo/)

不要以阶段报告、历史迁移文档、固定测试数量或旧提交快照作为当前规范。

## 5. Continuing Development Checklist

1. 阅读本文件及与任务相关的 `README.md` / `docs/DEVELOPER_GUIDE.md` 章节。
2. 运行 `git status --short --branch` 与 `git log -5 --oneline`。
3. 定位生产入口、状态协议和现有测试，只读取任务相关文件。
4. 先补或更新能证明目标语义的测试，再做最小实现。
5. 运行定向测试、全量测试（如任务要求）和 `git diff --check`。
6. 检查最终 diff 未引入架构扩张、正式数据变更或过期文档引用。
