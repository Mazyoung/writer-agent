# Writer-Agent 使用手册

> 面向 Writer-Agent 的日常使用者。本文只介绍如何配置、创作、恢复和维护小说项目。
>
> 当前文档基线：`f5d1697` 及 2026-08-13 Real Smoke 验收结果。
>
> 需要了解系统架构、状态机、RAG、Canonical/Derivation、Savepoint 内部实现或继续开发时，请阅读：[开发技术文档](docs/DEVELOPER_GUIDE.md)。

---

## 1. Writer-Agent 是什么

Writer-Agent 是一个面向长篇小说创作的本地 Agent 工作流。它不要求作者把整本小说完全交给 AI，而是提供三种不同自动化程度的使用方式。

| 使用方式 | 谁规划 | 谁写正文 | 系统主要负责 | 推荐用途 |
|---|---|---|---|---|
| 自主创作 | Agent | Agent | RAG、规划、写作、审阅、状态维护、自动推进 | 批量测试、低人工干预创作 |
| 监督创作 | Agent | Agent | 同上，但在关键 Review 节点等待作者 | 日常精细创作 |
| 数据管理 | 作者 | 作者 | 可选历史检索、连续性检查、正式提交、Current State、长期记忆、Savepoint | 作者自己写正文，只让系统管理小说数据 |

所有模式最终都维护同一套正式故事历史：

```text
章节输入
→ 章节生产 / 人工正文
→ Review
→ 作者批准
→ 正式正文（Canonical）
→ Current State / Atomic Facts / RAG 等派生
→ DERIVED_READY
```

---

## 2. 安装与基础配置

### 2.1 安装依赖

建议使用独立 Python / Conda 环境。

```bash
python -m pip install -r requirements.txt
```

### 2.2 创建项目根 `.env`

Windows：

```powershell
copy .env.example .env
```

Linux / macOS：

```bash
cp .env.example .env
```

根目录没有 `.env` 时 CLI 会拒绝运行。

### 2.3 模型槽位

系统使用四个主模型槽位：

| Slot | 主要职责 |
|---|---|
| `ARCHITECT` | Proposal、World Setting、Book Plan、Volume Plan |
| `PLAN` | Query Intent、Chapter Planner、Plan Review |
| `WRITE` | Writer、Stylist、正文修改 |
| `SYSTEM` | Prose / Consistency Review、Current State、Atomic Facts、派生任务 |

Query Intent 可以单独配置；未填写 `QUERY_INTENT_*` 时继承 `PLAN_*`。

常见配置项：

```dotenv
SYSTEM_PROVIDER=deepseek
SYSTEM_API_KEY=
SYSTEM_BASE_URL=https://api.deepseek.com
SYSTEM_MODEL=
SYSTEM_MAX_TOKENS=16384

ARCHITECT_PROVIDER=
ARCHITECT_API_KEY=
ARCHITECT_BASE_URL=
ARCHITECT_MODEL=
ARCHITECT_MAX_TOKENS=32768

PLAN_PROVIDER=
PLAN_API_KEY=
PLAN_BASE_URL=
PLAN_MODEL=
PLAN_MAX_TOKENS=16384

QUERY_INTENT_PROVIDER=
QUERY_INTENT_API_KEY=
QUERY_INTENT_BASE_URL=
QUERY_INTENT_MODEL=
QUERY_INTENT_MAX_TOKENS=

WRITE_PROVIDER=
WRITE_API_KEY=
WRITE_BASE_URL=
WRITE_MODEL=
WRITE_MAX_TOKENS=32768
```

### 2.4 Embedding

新小说初始化时会固定 Embedding 模型与维度。

```dotenv
EMBEDDING_MODE=local
```

或：

```dotenv
EMBEDDING_MODE=api
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=
EMBEDDING_MODEL=
EMBEDDING_DIMENSIONS=
```

**Embedding 模型和向量维度属于小说长期数据身份。初始化后不要随意更换。** API Key 和连接地址可以调整，但应继续指向兼容的同一模型。

---

## 3. 初始化一本小说

### 第一步：生成 Proposal

```bash
python main.py init my_novel "一句话故事前提"
```

生成：

```text
data/novels/my_novel/proposal.md
```

直接人工阅读、修改 `proposal.md`。

### 第二步：确认 Proposal 并生成长期规划

```bash
python main.py init my_novel --confirm
```

主要生成：

```text
settings/world_setting.md
tracking/book_plan.md
tracking/volume_plan.md
tracking/current_state.md
```

建议正式写第一章前至少检查：

| 文件 | 用途 |
|---|---|
| `world_setting.md` | 世界观与长期硬约束 |
| `book_plan.md` | 全书方向 |
| `volume_plan.md` | 当前卷路线 |
| `current_state.md` | 当前已经发生的故事状态 |

---

## 4. 选择运行模式

新小说创建时会在小说目录下生成自己的 `.env`，只允许覆盖运行策略：

```text
CHAPTER_MODE
AGENT_EXECUTION
AUTO_SAVEPOINT_EVERY
RAG_TOP_K
```

因此不同小说可以使用不同运行模式，不需要反复修改项目根 `.env`。

### 4.1 监督创作（推荐 Agent 模式）

```dotenv
CHAPTER_MODE=agent
AGENT_EXECUTION=supervised
```

流程：

```text
Query Intent / RAG
→ Chapter Plan
→ Plan Review
→ 作者检查点
→ Writer
→ Stylist
→ Prose Review
→ 作者检查点
→ Canonical
→ Derivation
```

### 4.2 自主创作

```dotenv
CHAPTER_MODE=agent
AGENT_EXECUTION=autonomous
```

PASS 时系统会自动推进；无法自动解决的 Review、错误或卷边界会停止。

适合连续测试或作者愿意接受较高自动化程度的场景。正式长篇创作前建议先熟悉 Savepoint。

### 4.3 数据管理模式

```dotenv
CHAPTER_MODE=human
```

系统不负责 Chapter Plan、Writer 或 Stylist。作者自己提供正文，系统负责连续性检查、正式提交和长期状态维护。

**Chapter Intent 在数据管理模式中是可选的：**

| Human Mode 输入 | 系统行为 |
|---|---|
| 提供 Intent | 生成 Query Intent → RAG → Writing Context → 等待作者正文 |
| 不提供 Intent | 直接进入 Human Writing；跳过 Query Intent 和 RAG |

无 Intent 直写时，系统会明确记录：

```text
intent_status=SKIPPED
rag_status=SKIPPED
skip_reason=human_direct_write
```

不会生成 Retrieval Trace。

---

## 5. 最常用命令

| 命令 | 作用 | 是否推进故事 |
|---|---|---|
| `python main.py status <novel>` | 查看小说当前状态 | 否 |
| `python main.py continue <novel>` | 执行当前唯一合法的下一步 | **是** |
| `python main.py write <novel> --chapter N` | 开始 / 恢复指定章节 | 是 |
| `python main.py write <novel> --chapter N --intent "..."` | 带作者创作意图开始章节 | 是 |
| `python main.py restart <novel> --chapter N` | 放弃当前章 Pre-Canonical 工作并重做，保留 Intent | 是 |
| `python main.py clean <novel>` | 放弃所有当前未完成章节 workflow，恢复 durable boundary | 是 |
| `python main.py repair-derivation <novel> --chapter N` | 修复 Canonical 后未完成的派生 | 是 |
| `python main.py run <novel> --to-chapter N` | Autonomous 连续运行到目标章节 | 是 |
| `python main.py rag-index <novel>` | 补齐 RAG 索引 | 维护 |
| `python main.py rag-index <novel> --rebuild` | 重建当前小说 / branch 的 RAG 索引 | 维护 |
| `python main.py close-volume <novel>` | 人工关闭当前卷 | 是 |
| `python main.py new-volume <novel>` | 生成下一卷 DRAFT | 是 |

### 最重要的区别

```text
status   = 只看
continue = 执行
restart  = 重做当前章，保留 Intent
clean    = 放弃未完成章节，连 Intent 一起清理
```

**不要把 `continue` 当成状态查询命令。** 如果最新章节已经 `DERIVED_READY`，`continue` 会直接开始下一章。

---

## 6. 如何开始和完成一章

### 6.1 Agent Mode

```bash
python main.py write my_novel --chapter 1
```

也可以给作者 Intent：

```bash
python main.py write my_novel --chapter 1 \
  --intent "本章推进两人的信任，但不能揭露幕后主使"
```

Agent Mode 即使没有 Human Intent，也仍会根据 Volume Plan、Current State、上一章结尾等生成 Query Intent 并执行历史 RAG。

### 6.2 Human Mode：带 Intent

```bash
python main.py write my_novel --chapter 1 \
  --intent "本章让主角进入旧城区，并发现仓库已经被提前搜过"
```

系统生成 Writing Context 后进入人工正文检查点。

你可以在交互菜单里选择提交正文文件，也可以使用兼容命令：

```bash
python main.py write my_novel --chapter 1 \
  --action submit \
  --file ./my_chapter_1.md
```

### 6.3 Human Mode：不使用 Intent / RAG

直接：

```bash
python main.py write my_novel --chapter 1
```

或在上一章完成后：

```bash
python main.py continue my_novel
```

系统会直接进入 Human Writing，不调用 Query Intent Builder，不执行历史 RAG。

---

## 7. 人工检查点常见操作

CLI 会显示当前 Review、问题、可执行操作和需要编辑的文件。

| 操作 | 含义 |
|---|---|
| `approve` | 当前 Review 通过时批准并继续；未通过时进入 Override 二次确认 |
| `agent_edit` | 让 Agent 按 Reviewer 问题 / Human Feedback 修改 |
| `human_edit` | 作者直接编辑指定 Markdown，再重新 Review |
| `regenerate_prose` | 保留已批准 Plan，从头重新生成正文 |
| `restart` | 清掉本章 Pre-Canonical 工作并重启，保留 Intent |
| `confirm_override` | 明确忽略未通过 Review 并正式提交 |
| `back` | 从 Override 确认返回 |

Review 未通过时直接 `approve` **不会把 Review 结果篡改成 PASS**，而是先进入独立 Override 确认。

---

## 8. 正式正文、Canonical 与 DERIVED_READY

### Canonical

正式章节路径：

```text
data/novels/<novel>/chapters/chapter_NNNN.md
```

Canonical Commit 后，普通生成流程不能覆盖这份正文。

### DERIVED_READY

Canonical 成立后，系统继续更新：

```text
Current State
→ Atomic Facts
→ Fact Verification
→ Volume Progress
→ Chapter Sources
→ Chroma / RAG
→ DERIVED_READY
```

只有 `DERIVED_READY` 才表示“本章正文和长期数据都已完整完成”。

如果 Canonical 已成立但派生失败：

```bash
python main.py continue my_novel
```

通常会自动从未完成派生阶段恢复。也可以显式：

```bash
python main.py repair-derivation my_novel --chapter 5
```

**不要因为 Derivation 失败重新生成已经 Canonical 的正文。**

---

## 9. `restart` 与 `clean`

| 场景 | 用什么 | Intent 是否保留 | Canonical 是否允许 |
|---|---|---|---|
| 当前章写坏了，想按原意图重新做 | `restart` | 保留 | 否 |
| 误进入下一章 / workflow 污染 / 想彻底放弃未完成章 | `clean` | 删除 | 不处理 Canonical |
| 已经 Canonical，但派生失败 | `continue` / `repair-derivation` | 不涉及 | 保留 Canonical |
| 想回到过去完整世界 | `savepoint load` | 随 Savepoint 恢复 | 整体恢复 |

`clean` 会清理 Pre-Canonical 的 checkpoint、Intent、Context、Trace、Plan、Draft、Review 等临时产物，并回到最近的 durable boundary。重复执行 `clean` 是安全的；没有待清理 workflow 时会直接提示。

---

## 10. Story Savepoint

Story Savepoint 是**整个小说创作世界的长期存档**，不是单章执行 checkpoint。

```text
LangGraph Checkpoint = 本章执行到了哪里
Story Savepoint      = 某个 DERIVED_READY 章节结束后的完整小说世界
```

### 10.1 创建

```bash
python main.py savepoint create my_novel
```

Savepoint ID 与当前最新完成章节对应，例如 Chapter 40：

```text
S0040
```

只有最新正式章节达到 `DERIVED_READY` 且不存在未结束章节 workflow 时才能创建。

### 10.2 列出

```bash
python main.py savepoint list my_novel
```

### 10.3 验证

`verify` 必须提供 ID：

```bash
python main.py savepoint verify my_novel S0040
```

### 10.4 加载

```bash
python main.py savepoint load my_novel S0040
```

Load 是破坏性操作，CLI 会要求两次人工确认：

```text
小说名称
LOAD S0040
```

加载旧 Savepoint 不会删除其他 READY Savepoint，因此可以在多个已保存世界之间来回切换。

### 10.5 自动 Savepoint

小说级 `.env`：

```dotenv
AUTO_SAVEPOINT_EVERY=0
```

`0` 表示关闭。

例如：

```dotenv
AUTO_SAVEPOINT_EVERY=50
```

表示 Chapter 50、100、150……真正达到 `DERIVED_READY` 后自动创建对应 Savepoint。

---

## 11. RAG：使用者需要知道什么

当章节启用 RAG 时，系统大致执行：

```text
Volume Plan
+ 上一章结尾（约 1500 中文字符，完整段落）
+ Current State
+ Human Intent（如果有）
        ↓
Query Intent
        ↓
Atomic Facts Top-K
        ↓
回读对应 Canonical 原文段落
        ↓
Planner / Human Writing Context
```

几个重要原则：

| 原则 | 说明 |
|---|---|
| Query Intent 只负责“查什么” | 不应该变成 Chapter Plan |
| 只查当前章之前的历史 | 不检索未来章节 |
| Chroma 中嵌入的是 Atomic Fact 文本 | 不是整章正文 |
| 命中 Fact 后再回读 Canonical 原文 | 默认扩展事实段落及前后各一段 |
| `RAG_TOP_K` 控制初始召回数量 | 越大召回更广，也可能更噪 |

Query Intent 严重异常达到 10000 字时会自动尝试压缩一次；连续超长会报错，不会静默截断。

---

## 12. Token Warning

项目不会为了 Token 预算静默截断 World Setting、Book Plan、Volume Plan、Current State、Human Intent 等正式上下文。

当前 Token Guard 的行为是：

```text
估算输入 Token
→ 如果超过配置指导值，打印 [Token Warning]
→ 不截断
→ 不自动压缩
→ 不阻断
→ 继续把完整上下文交给远端模型 API
```

因此 `*_MAX_TOKENS` 当前更接近上下文容量提示 / 诊断基准，不是本地硬拒绝线。

---

## 13. 卷生命周期

关闭当前卷：

```bash
python main.py close-volume my_novel
```

关闭卷是人工决定。系统的 `Volume Progress` 只是建议，不会自动关闭。

生成下一卷：

```bash
python main.py new-volume my_novel
```

可附加方向：

```bash
python main.py new-volume my_novel \
  --notes "下一卷加强政治压力，并开始让两条支线汇合"
```

下一卷规划会读取 World Setting、Book Plan、Current State 和上一卷结果，以实际故事状态为起点。

---

## 14. 当前测试项目与已验证基线

### `smoke_final`

当前推荐的真实 Smoke 测试小说。

截至 2026-08-13 已验证：

| 项目 | 结果 |
|---|---|
| Chapter 1-3 完整闭环 | PASS |
| Human Mode 无 Intent 直写 | PASS |
| Human Mode 有 Intent → Query Intent / RAG | PASS |
| `clean` 与重复 `clean` | PASS |
| Canonical → Current State → Atomic Facts → Verification → RAG → DERIVED_READY | PASS |
| Savepoint `S0002` / `S0003` | PASS |
| `S0003 → load S0002 → load S0003` 双向恢复 | PASS |
| Savepoint 恢复后的 Chapter 4 RAG | PASS |
| 恢复后 Atomic Facts / Canonical 原文回读 | PASS |
| 恢复后 `clean` 回到 Chapter 3 durable boundary | PASS |

### `smoke_test`

早期 Real Smoke / 故障注入工作区。它可能保留人为制造的中断或历史测试状态，不建议把它当作“干净演示小说”。遇到不需要的 Pre-Canonical workflow 时可使用：

```bash
python main.py clean smoke_test
```

### 自动测试基线

当前整改验收记录：

```text
python -m pytest -q
327 passed, 86 subtests passed
```

另有：

```bash
git diff --check
```

通过。

---

## 15. 常见问题速查

| 现象 | 应做什么 |
|---|---|
| 只想看现在写到哪里 | `python main.py status <novel>` |
| 最新章已完成，想开始下一章 | `python main.py continue <novel>` |
| `continue` 误进入新章，不想保留 | `python main.py clean <novel>` |
| 当前章想从头重做，但保留 Intent | `python main.py restart <novel> --chapter N` |
| Canonical 已经存在但派生失败 | `continue` 或 `repair-derivation` |
| Savepoint Load 被拒绝，提示有未结束 workflow | 先 `clean` 或完成当前 workflow，再 Load |
| `savepoint verify` 提示缺少 ID | 使用 `savepoint verify <novel> Sxxxx` |
| Token Warning | 当前只是警示；检查上下文规模和远端模型容量 |
| Human Mode 不想使用 RAG | 不传 `--intent`，直接进入人工正文 |
| Human Mode 希望系统帮忙找历史 | 提供 `--intent` |

### 已知的非阻断显示限制

极端情况下，如果存在“远于下一合法章节”的 stale workflow，`continue` 会正确检测并要求 `clean`；`status` 的摘要可能仍只显示基于 durable chapter 推导出的下一章。遇到 `continue` 明确提示 stale workflow 时，以 `continue` 的安全路由提示为准，执行：

```bash
python main.py clean <novel>
```

---

## 16. 进一步阅读

如果你只是写小说，到这里已经够用。

需要继续开发或理解内部实现时，请阅读：

**[Writer-Agent 开发技术文档](docs/DEVELOPER_GUIDE.md)**

其中包括：

- LangGraph 章节状态机；
- Canonical / Derivation 边界；
- Current State 2.0；
- Query Intent 与 Atomic Fact RAG；
- Review / Override；
- checkpoint / continue / restart / clean；
- Story Savepoint 的完整快照与事务式恢复；
- generation events 与 chapter_sources；
- 测试架构与 Real Smoke；
- 当前技术债与 Chapter-level Timeline Rewind Future Design。
