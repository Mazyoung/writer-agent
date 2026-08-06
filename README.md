# Writer-Agent

> 你可以把整本小说交给它尝试生成；  
> 也可以只给它一章的方向；  
> 还可以自己完成真正的创作，把繁琐的长期记忆、状态整理和内容管理交给 Agent。
>
> **自动化到什么程度，由作者决定。**

**Writer-Agent 是一个面向长篇小说创作的 AI 写作与内容管理工具。**

它既可以作为简单的自动小说生成程序，也可以成为小说爱好者的人机共创工具，或者辅助长期创作者管理人物、事件、伏笔、物品和故事状态。

---

## Writer-Agent 能做什么？

### 一套系统，不同的创作方式

| 使用方式 | 适合谁 | Writer-Agent 可以做什么 |
|---|---|---|
| **自动写作** | 想快速生成故事 | 从整体规划到章节写作持续推进 |
| **轻度参与** | 小说爱好者 | 给某一章提出要求，让 Agent 完成具体创作 |
| **共同创作** | 希望控制剧情方向的作者 | 审阅规划、修改正文、要求重写或局部调整 |
| **深度控制** | 有明确创作思路的作者 | 自己维护全书与分卷规划，只让 Agent 辅助执行 |
| **内容管理** | 长篇作者 | 管理历史事实、人物状态、物品、伏笔和章节连续性 |

你不需要在开始时决定一种固定模式。

同一本小说可以从自动生成开始，之后随时逐渐增加人工参与。

---

## 分层控制故事

Writer-Agent 将长篇小说拆成三个层次：

| 层级 | 负责什么 |
|---|---|
| **全书规划** | 整本小说的长期方向、核心冲突和总体目标 |
| **分卷规划** | 当前一卷准备经历怎样的故事过程 |
| **章节规划** | 根据当前剧情，决定这一章具体发生什么 |

整体关系大致是：

```text
全书方向
   ↓
当前卷故事路径
   ↓
当前故事状态
   ↓
这一章应该发生什么
   ↓
生成正文
```

这样，小说不需要在第一天就把几十甚至上百章完全固定下来。

故事可以随着实际创作结果不断向前发展。

---

## 你可以随时介入

每一章都可以加入自己的 **Chapter Intent**。

例如：

```text
这一章增加林默和苏晴之间的信任，
但不要解释芯片真正的来源。
```

Agent 会把它作为当前章节的重要创作要求。

在正文生成之后，你还可以决定下一步：

| 操作 | 含义 |
|---|---|
| `approve` | 接受当前正文 |
| `agent_edit` | Agent 根据 Review 的具体问题修改当前内容 |
| `human_edit` | 作者直接修改当前 Plan 或 Prose |
| `regenerate_prose` | 保留 Approved Plan，只重新生成正文 |
| `restart` | 保留 Chapter Intent，放弃本章全部 Pre-Canonical 内容并重新规划 |

因此，Agent 负责提供方案和执行创作，但最终采用什么内容仍然由作者决定。

---

## 面向长篇创作的长期记忆

小说越长，需要记住的内容越多：

- 某个人物以前做过什么；
- 两个人目前是什么关系；
- 一件重要物品现在在哪里；
- 某个秘密什么时候被提到过；
- 哪些伏笔还没有回收；
- 某段剧情是否和几十章前发生的事情冲突。

Writer-Agent 会持续整理已经确认的故事事实，并在后续创作时重新使用这些信息。

因此，它不只是“写下一段文字”，而是尽量保持整部长篇小说的连续性。

---

## 记录故事“现在是什么状态”

除了记住过去发生过什么，Writer-Agent 还会持续维护故事当前状态。

例如：

| 类型 | 示例 |
|---|---|
| 人物 | 当前所在地、身份、关系、状态 |
| 物品 | 当前持有者、所在地、是否已经使用 |
| 剧情 | 当前主要矛盾、正在推进的任务 |
| 伏笔 | 已发现、未解决、已回收 |
| 进度 | 当前章节和当前卷的发展位置 |

这让后面的章节可以直接从“现在”继续，而不必每次重新推断整本小说的状态。

---

## 尽量避免长篇项目出现状态不一致

长篇小说不只有正文，还会伴随人物状态、历史事实、伏笔和故事进度等大量信息。

Writer-Agent 会把：

```text
正文确认
   ↓
正式保存
   ↓
更新相关故事信息
   ↓
进入下一章
```

作为一个完整流程处理。

如果后续的信息整理出现问题，已经确认的正文仍然保留，并可以单独继续完成尚未完成的整理。

这样可以尽量避免：

> 正文已经是新版本，但人物状态还停留在旧版本。

---

# 如何使用

## 1. 安装

```bash
git clone https://github.com/Mazyoung/writer-agent.git
cd writer-agent

python -m pip install -r requirements.txt
```

---

## 2. 配置模型

项目根目录的 `.env` 是正式配置入口，所有正常 CLI 命令执行前都会检查该文件。首次使用请运行：

```bat
copy .env.example .env
```

如果 `.env` 不存在，CLI 会在 Settings、LLM、Embedding 和任何小说状态写入之前直接退出。LLM 使用四个职责槽位：

- `ARCHITECT`：全书、世界观和分卷规划
- `PLAN`：章节规划与规划审阅
- `WRITE`：正文创作、风格处理、`agent_edit` 和 `regenerate_prose`
- `SYSTEM`：正文审阅、连续性检查、Derivation 和状态管理

每个 slot 都包含 `PROVIDER / API_KEY / BASE_URL / MODEL / MAX_TOKENS`。支持 `deepseek`、`openai_compatible` 和 `anthropic`。

`ARCHITECT`、`PLAN`、`WRITE` 的 connection 字段留空时继承 `SYSTEM`。如果显式填写某个 slot 的 `PROVIDER`，该 slot 使用自己的 connection，不再继承 SYSTEM 的 Key 或 Base URL。每个 slot 的 `MODEL` 必须单独填写，绝不继承 `SYSTEM_MODEL`。

例如：

```dotenv
# ----- System / shared default -----
SYSTEM_PROVIDER=deepseek
SYSTEM_API_KEY=your-key
SYSTEM_BASE_URL=https://api.deepseek.com
SYSTEM_MODEL=<system-model>
SYSTEM_MAX_TOKENS=16384

# ----- Large-scale planning -----
ARCHITECT_PROVIDER=
ARCHITECT_API_KEY=
ARCHITECT_BASE_URL=
ARCHITECT_MODEL=<architect-model>
ARCHITECT_MAX_TOKENS=32768

# ----- Chapter planning -----
PLAN_PROVIDER=
PLAN_API_KEY=
PLAN_BASE_URL=
PLAN_MODEL=<plan-model>
PLAN_MAX_TOKENS=16384

# ----- Query Intent Builder (PLAN sub-configuration) -----
# 留空时逐字段继承 PLAN_*
QUERY_INTENT_PROVIDER=
QUERY_INTENT_API_KEY=
QUERY_INTENT_BASE_URL=
QUERY_INTENT_MODEL=
QUERY_INTENT_MAX_TOKENS=

# ----- Prose creation -----
WRITE_PROVIDER=
WRITE_API_KEY=
WRITE_BASE_URL=
WRITE_MODEL=<write-model>
WRITE_MAX_TOKENS=32768

# ----- Embedding for NEW novels only -----
EMBEDDING_MODE=local
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=
EMBEDDING_MODEL=
EMBEDDING_DIMENSIONS=

# ----- Runtime -----
CHAPTER_MODE=agent
AGENT_EXECUTION=supervised
RAG_TOP_K=5
AUTO_SAVEPOINT_EVERY=50
```

普通用户只需配置一套 SYSTEM connection，再分别填写四个 model 和 max_tokens。四个默认输出上限分别为 ARCHITECT `32768`、PLAN `16384`、WRITE `32768`、SYSTEM `16384`，都可在 `.env` 中覆盖且必须为正整数。

多 Provider 也可以同时使用，例如：

```dotenv
SYSTEM_PROVIDER=deepseek
SYSTEM_API_KEY=your-deepseek-key
SYSTEM_BASE_URL=https://api.deepseek.com
SYSTEM_MODEL=<system-model>

ARCHITECT_MODEL=<architect-model>
PLAN_MODEL=<plan-model>

WRITE_PROVIDER=anthropic
WRITE_API_KEY=your-anthropic-key
WRITE_BASE_URL=
WRITE_MODEL=<write-model>
```

Writer 与 Stylist 始终共同使用 `WRITE`。Writer-Agent 不发送 Thinking、Extended Thinking、`reasoning_effort` 或其他 provider-specific reasoning 参数，模型使用当前 API/model 的默认推理行为。每次真正调用 LLM 前，系统只对本次需要的 slot 执行 preflight；错误会以中文列出 slot、缺失字段和对应环境变量，不输出 API Key 内容。

Query Intent Builder 属于 PLAN 职责，不是第五个主槽位。`QUERY_INTENT_*` 每个空字段分别继承对应的 `PLAN_*`；如需使用更便宜的快速模型，可单独填写 provider、connection、model 和 max_tokens。

章节检索只把 Query Intent 发送给 Embedding。Builder 输入完整 Volume Plan、上一章末尾约 1500 字的完整段落窗口、完整 Current State 和原始 Human Intent；Human Intent 仍会原样直接交给 Planner。Query Intent 通常应在 3000 字以内，5000–9999 字仍接受；达到 10000 字时仅自动压缩重写一次，再次严重超长则 fail-closed，绝不静默截断。

### Embedding 与 LLM 不同

LLM slot 的 provider、API 和 model 可以随时修改。Embedding vector space 则在 `init` 时确认并永久绑定当前小说：

- `local`：默认方式，继续使用 Chroma 内置本地 Embedding，无需额外 API。
- `api`：使用任意 OpenAI-compatible Embedding API，例如 Qwen Embedding。

API 模式示例：

```dotenv
EMBEDDING_MODE=api
EMBEDDING_API_KEY=your_embedding_key
EMBEDDING_BASE_URL=https://your-compatible-endpoint/v1
EMBEDDING_MODEL=qwen3-embedding-0.6b
EMBEDDING_DIMENSIONS=
```

`EMBEDDING_DIMENSIONS` 推荐留空，由 `init` 前的最小 probe 根据实际返回向量自动确定；如明确填写，probe 会验证实际维度是否一致。

确认 `init` 后，小说的 Embedding Mode、Model、Dimensions 永久固定。以后修改 `.env` 中这三个值只影响下一本新小说，不影响已有小说；API Key 和 Base URL 属于运行时连接信息，可以轮换，但必须继续访问同一个模型/vector space。内部配置不保存 API Key，也不会随 Story Savepoint Load 改变。

`AUTO_SAVEPOINT_EVERY=0` 关闭自动 Savepoint；正整数 `N` 表示每当正式章节达到 `DERIVED_READY` 且章节号可被 `N` 整除时，自动创建与手动 Savepoint 完全相同的 READY Savepoint。

`DERIVED_READY` 由小说 creative state 内的最终 marker 持久记录。它只在 Canonical、Current State、Fact Digest、Volume Progress、chapter_sources 和 Chroma 同步全部成功后写入，不依赖历史 LangGraph checkpoint，因此 Savepoint Load 后仍可正确选择下一章。

---

## 3. 创建一本小说

给它一个最初的故事想法：

```bash
python main.py init my_novel "近未来，一名记忆修复师发现了一枚不属于任何人的存储芯片"
```

Writer-Agent 会先生成故事提案。

你可以阅读、修改，然后确认：

```bash
python main.py init my_novel --confirm
```

确认之后，小说的基础设定和整体规划就建立好了。

---

## 4. 开始写章节

最简单的方式：

```bash
python main.py write my_novel --chapter 1
```

如果你对这一章有自己的想法：

```bash
python main.py write my_novel --chapter 1 \
  --intent "这一章建立两人的信任，但不要揭露芯片真正的来源"
```

不提供 `--intent` 也可以，Agent 会根据当前故事自行规划。

运行体验由两个现有配置组合得到，不会建立第三套 Chapter Workflow：

- 自主创作：`CHAPTER_MODE=agent` + `AGENT_EXECUTION=autonomous`。正常 PASS 自动完成 Canonical、Derivation，并只在有限自动修复仍失败时等待作者。
- 监督模式：`CHAPTER_MODE=agent` + `AGENT_EXECUTION=supervised`。固定在 Plan Review 和 Prose Review 后等待作者检查，即使 Review 为 PASS。
- 数据管理模式：`CHAPTER_MODE=human`。作者提供 Intent 和正文，系统负责 Writing Context、Consistency、Canonical 与长期状态管理；`AGENT_EXECUTION` 不会让系统代写。

不知道当前该执行哪个命令时，使用统一状态入口：

```bash
python main.py continue my_novel
```

`continue` 会优先恢复现有 checkpoint、显示当前人工等待、修复未完成 Derivation，或选择下一章；它每次只推进当前确定步骤，不会自动无限写作。

只有自主创作模式可以显式连续写到目标章节：

```bash
python main.py run my_novel --to-chapter 10
```

`run` 与 `continue` 共用同一状态路由，已达到 `DERIVED_READY` 的章节不会被覆盖。

---

## 5. 单独控制章节规划

如果你希望先看这一章准备写什么：

```bash
python main.py plan my_novel --chapter 1
```

也可以直接给出自己的章节方向：

```bash
python main.py plan my_novel --chapter 1 \
  --outline "林默第一次尝试读取芯片，并发现其中有一段属于自己的记忆"
```

然后再开始写：

```bash
python main.py write my_novel --chapter 1
```

---

## 6. 处理人工审阅

当 Writer-Agent 等待你的决定时，可以根据需要选择：

```bash
# 接受当前结果
python main.py write my_novel --chapter 1 --action approve

# 要求 Agent 修改
python main.py write my_novel --chapter 1 \
  --action agent_edit \
  --feedback "减少解释性对白，让人物通过动作表现紧张感"

# 自己修改后继续
python main.py write my_novel --chapter 1 --action human_edit

# 保留 Approved Plan，只重新生成正文
python main.py write my_novel --chapter 1 --action regenerate_prose

# 放弃本章全部 Pre-Canonical 内容，从 Planning 重来
python main.py write my_novel --chapter 1 --action restart

# 也可在任何 Pre-Canonical 阶段使用同一底层 restart
python main.py restart my_novel --chapter 1
```

Plan Review 未通过时可用 `agent_edit / human_edit / restart`；Prose Review 未通过时额外提供 `regenerate_prose`。CLI 会先列出完整 Review 问题。普通程序中断不使用这些 action：重新执行原 `write`，或执行 `continue`，都会从已有 checkpoint 恢复。

---

## 7. 查看小说状态

```bash
python main.py status my_novel
```

你也可以直接打开小说目录中的 Markdown 文件阅读和修改内容。

---

## 8. 如果章节已经确认，但后续整理没有完成

可以继续完成该章节的状态整理：

```bash
python main.py repair-derivation my_novel --chapter 1
```

不需要重新生成已经确认的正文。

---

## 9. 完成一卷并开始下一卷

完成当前卷：

```bash
python main.py close-volume my_novel
```

生成下一卷规划：

```bash
python main.py new-volume my_novel
```

也可以加入下一卷的要求：

```bash
python main.py new-volume my_novel \
  --notes "扩大外部冲突，但继续保留芯片来源这个核心悬念"
```

你可以直接修改生成的分卷规划。

确认当前规划可用后，直接开始下一卷 Chapter Planning；系统会在 Planning 入口完成必要的内部 ACTIVE 状态转换，不需要额外 approval 命令。

---

# 常用命令

| 命令 | 用途 |
|---|---|
| `init` | 创建小说 |
| `plan` | 生成章节规划 |
| `write` | 创作章节 |
| `continue` | 检查小说当前状态并从唯一合法位置继续 |
| `run --to-chapter N` | 自主模式连续创作到目标章节 |
| `restart` | 放弃指定章的 Pre-Canonical 内容并重新规划 |
| `status` | 查看当前状态 |
| `repair-derivation` | 完成未完成的章节信息整理 |
| `close-volume` | 完成当前卷 |
| `new-volume` | 创建下一卷规划 |

查看完整帮助：

```bash
python main.py --help
```

或者：

```bash
python main.py write --help
python main.py plan --help
python main.py new-volume --help
```

---

# 小说文件在哪里？

每一本小说都有独立目录：

```text
data/novels/<novel_id>/
```

常用内容包括：

| 内容 | 位置 |
|---|---|
| 小说正文 | `chapters/` |
| 世界观 | `settings/world_setting.md` |
| 全书规划 | `tracking/book_plan.md` |
| 当前卷规划 | `tracking/volume_plan.md` |
| 当前故事状态 | `tracking/current_state.md` |
| 作者补充知识 | `tracking/author_rag.md` |
| 章节意图 | `briefs/` |
| 章节规划 | `outlines/` |

这些内容大多可以直接使用 VS Code、Typora 或其他 Markdown 编辑器阅读和修改。

---

# 更多信息

README 主要介绍 **Writer-Agent 能做什么，以及怎样开始使用**。

如果你希望进一步了解项目内部的工作方式和设计思路，请阅读：

**[ARCHITECTURE.md](ARCHITECTURE.md)**
