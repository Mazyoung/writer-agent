# Writer-Agent

Writer-Agent 是一个面向长篇小说创作的本地 Agent 工作流。

它的目标不是单纯“让 AI 自动写小说”，而是让作者自行决定自动化程度：

| 使用方式 | 谁规划 | 谁写正文 | 系统负责                                     |
| -------- | ------ | -------- | -------------------------------------------- |
| 自主创作 | Agent  | Agent    | RAG、规划、写作、审阅、状态维护              |
| 监督创作 | Agent  | Agent    | 同上，但关键阶段等待作者确认                 |
| 数据管理 | 作者   | 作者     | 检索历史信息、连续性检查、状态与长期记忆维护 |

所有模式最终都共享同一套正式故事状态：

```text
创作意图（Intent）
        ↓
历史信息召回（RAG）
        ↓
章节规划（Plan）
        ↓
规划审阅
        ↓
正文创作
        ↓
正文审阅
        ↓
作者确认
        ↓
正式章节提交
        ↓
更新故事状态与历史记忆
        ↓
进入下一章
```

正文只有经过正式批准并完成作者确认后，才属于小说的真正在维护的历史。

------

# 1. 核心概念

## Canonical Prose（正式章节）

正式章节位于：

```text
data/novels/<novel_id>/chapters/chapter_NNNN.md
```

Canonical 正文创建后，普通章节生成不能覆盖它。

Canonical Commit 是小说事实成立的边界。

------

## DERIVED_READY

Canonical 成立后，系统继续派生：

```text
Canonical Prose
→ Current State
→ Fact Digest
→ Atomic Facts
→ Volume Progress
→ Chapter Sources
→ Chroma
→ DERIVED_READY
```

只有全部完成后，本章才达到：

```text
DERIVED_READY
```

如果正文已经 Canonical，但派生中途失败，正文不会撤销。

会继续或修复 Derivation，而不是重新生成正文。

------

## Story Savepoint

Story Savepoint 是整个小说创作世界的长期存档。

它与 LangGraph 的章节执行 checkpoint 是两个不同概念：

```text
LangGraph Checkpoint
= 章节内部执行到哪里

Story Savepoint
= 整个小说在某个正式章节结束后的完整状态
```

可以从旧 Savepoint 跳到早期状态，也可以之后再次加载较新的 Savepoint。

------

# 2. 安装

建议使用独立 Python 环境。

安装依赖：

```bash
python -m pip install -r requirements.txt
```

项目正常 CLI 运行要求根目录存在：

```text
.env
```

可以从模板复制：

Windows：

```powershell
copy .env.example .env
```

Linux / macOS：

```bash
cp .env.example .env
```

然后填写自己的模型配置。

API Key 不应提交到 Git。

------

# 3. 模型配置

Writer-Agent 使用四个主模型槽位。

| Slot        | 主要职责                                        |
| ----------- | ----------------------------------------------- |
| `ARCHITECT` | Proposal、World Setting、Book Plan、Volume Plan |
| `PLAN`      | Chapter Planner、Plan Review                    |
| `WRITE`     | Writer、Stylist、正文改写                       |
| `SYSTEM`    | Prose Review、Consistency、Derivation、状态管理 |

支持 Provider：

```text
deepseek
openai_compatible
anthropic
```

典型配置：

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

WRITE_PROVIDER=
WRITE_API_KEY=
WRITE_BASE_URL=
WRITE_MODEL=
WRITE_MAX_TOKENS=32768
```

正式 World Setting、Book Plan、Volume Plan、Chapter Plan、Current State 等上下文不会为了节省 Token 被静默截断。

如果输入超过安全预算，系统会在模型调用前停止并提示需要精简的内容。

------

# 4. Query Intent 模型

历史 RAG 不再直接把大量原始上下文拼成 Embedding Query。

系统会先生成一个：

```text
Query Intent
```

流程：

```text
Volume Plan
+ 上一章结尾
+ Current State
+ Human Intent（如果存在）
        ↓
Query Intent Builder
        ↓
Query Intent
        ↓
Embedding Retrieval
```

Query Intent Builder 默认属于 `PLAN`。

如不填写以下配置，则继承 PLAN：

```dotenv
QUERY_INTENT_PROVIDER=
QUERY_INTENT_API_KEY=
QUERY_INTENT_BASE_URL=
QUERY_INTENT_MODEL=
QUERY_INTENT_MAX_TOKENS=
```

也可以单独指定一个更快或更便宜的模型。

Query Intent 应尽可能简短，只描述：

> 本章最需要检索哪些历史事实。

通常应在 1000～3000 字以内，越短越好。

只有出现严重异常的超长输出时，系统才会要求 Query Intent Builder 自动重新压缩。

------

# 5. Embedding 配置

Embedding 与 LLM Slot 独立。

支持：

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

初始化新小说时，系统会显示：

```text
Embedding Mode
Embedding Model
Embedding Dimensions
```

并要求人工确认。

## 重要

Embedding Model 和 Dimensions 属于小说长期数据身份。

小说初始化后：

> 不要随意更换 Embedding 模型或向量维度。

API Key 和连接地址可以改变，但必须继续访问兼容的同一 Embedding 模型。

------

# 6. 初始化小说

第一步：

```bash
python main.py init my_novel "一句话故事前提"
```

系统生成：

```text
data/novels/my_novel/proposal.md
```

先人工阅读并直接编辑：

```text
proposal.md
```

不需要创建其他 proposal 副本。

确认后执行：

```bash
python main.py init my_novel --confirm
```

系统生成主要长期规划：

```text
settings/world_setting.md
tracking/book_plan.md
tracking/volume_plan.md
tracking/current_state.md
```

建议在正式开始第一章前人工检查：

```text
World Setting
Book Plan
Volume Plan
```

其中 Volume Plan 是卷级故事路线，不要求提前规定每一章发生什么。

第一章真正开始规划时，当前 DRAFT Volume Plan 会进入正式使用状态。

不需要执行 `approve-volume`。

------

# 7. 三种运行方式

## 7.1 自主创作模式

由于本系统的回退完全依赖存档功能，所以如果想认真创作一本小说的话不要使用该模式，很可能会错误操作而自动化推进不给你审阅正文的机会。

`.env`：

```dotenv
CHAPTER_MODE=agent
AGENT_EXECUTION=autonomous
```

流程：

```text
Query Intent
→ RAG
→ Chapter Plan
→ Plan Review
→ Writer
→ Stylist
→ Prose Review
→ Canonical
→ Derivation
```

正常 PASS 会自动继续。

如果 Review 发现问题，系统会使用现有的有限 Agent Edit；如果有限修改后仍无法解决，则停止并等待人工。

适合：

- 自动连续创作；
- 大批量章节测试；
- 作者主要负责高层方向。

------

## 7.2 监督创作模式

`.env`：

```dotenv
CHAPTER_MODE=agent
AGENT_EXECUTION=supervised
```

Agent 仍负责规划和正文，但会在两个主要位置等待作者：

```text
Plan Review（核查写作计划）
→ Human Checkpoint

Prose Review（核查正文质量）
→ Human Checkpoint
```

适合日常精细创作。

------

## 7.3 数据管理模式

`.env`：

```dotenv
CHAPTER_MODE=human
```

流程：

```text
Human Intent
→ Query Intent
→ Historical RAG
→ Writing Context
→ 作者写正文
→ Consistency Review
→ Author Approval
→ Canonical
→ Derivation
```

系统不负责 Chapter Plan、Writer 或 Stylist。

它主要负责：

```text
历史检索
连续性检查
正式正文提交
Current State
Fact Digest
Atomic Facts
RAG
Savepoint
```

Human Mode 必须提供 Chapter Intent。

------

# 8. 开始一章

Agent Mode：

```bash
python main.py write my_novel --chapter 1
```

也可以提供作者 Intent：

```bash
python main.py write my_novel --chapter 1 \
  --intent "本章推进两人的信任，但不能揭露幕后主使"
```

强烈建议在使用本工具时给出每章的创作意图（不一定要很具体），这不仅能让剧情按照你需要的方式发展，还可以帮助系统实现历史数据的查询，更有的放矢的查找所需数据。同时在人工创作过程中这是必须输入的内容，没有这个内容则无法准确给出你需要的历史信息。

------

# 9. Supervised Mode 监督创作模式

工作流暂停时，CLI 会显示：

```text
当前 Review
具体问题
可执行操作
需要编辑的文件
```

## Plan Review

PASS 时可以：

```text
approve
agent_edit
human_edit
restart
```

未通过时可以：

```text
agent_edit
human_edit
restart
```

### 系统审核未通过你的文章可以使用下面的命令强行通过

```bash
python main.py write my_novel --chapter 1 --action confirm_override
```

### agent_edit

如果 Review 已经 PASS，但作者仍要求修改，必须给出反馈：

```bash
python main.py write my_novel --chapter 1 \
  --action agent_edit \
  --feedback "第二场不要这么快确认两人的合作关系"
```

修改后会重新 Review。

### human_edit

CLI 会显示需要编辑的 Markdown 文件。

人工修改并保存以后：

```bash
python main.py write my_novel --chapter 1 --action human_edit
```

系统会读取该编辑文件，然后重新 Review。

------

# 10. Prose Review

Agent Mode 的 Prose Review 可以：

```text
approve
agent_edit
human_edit
regenerate_prose
restart
```

### approve

Review PASS 后：

```bash
python main.py write my_novel --chapter 1 --action approve
```

随后才能 Canonical Commit。

### agent_edit

```bash
python main.py write my_novel --chapter 1 \
  --action agent_edit \
  --feedback "减少说明性文字，让冲突主要通过动作表达"
```

### regenerate_prose

```bash
python main.py write my_novel --chapter 1 \
  --action regenerate_prose
```

它会：

```text
保留 Approved Chapter Plan
→ 丢弃当前 Candidate Prose
→ Writer 从头生成
→ Stylist
→ Review
```

不会重新规划本章。

------

# 11. Human Mode 提交正文

首先：

```bash
python main.py write my_novel --chapter 1 \
  --intent "本章让主角第一次进入旧城区，并发现仓库已经被人提前搜过"
```

系统生成 Writing Context 后会等待作者。

作者完成正文，例如：

```text
my_chapter_1.md
```

提交：

```bash
python main.py write my_novel --chapter 1 \
  --action submit \
  --file ./my_chapter_1.md
```

随后执行：

```text
Consistency Review
→ Author Approval
→ Canonical
→ Derivation
```

如果 Consistency 出现 WARN，作者仍可以在明确看到警告后主动批准。

第一次：

```bash
python main.py write my_novel --chapter 1 --action approve
```

系统进入独立的 override 确认。

确认：

```bash
python main.py write my_novel --chapter 1 \
  --action confirm_override
```

原始 WARN 不会被篡改成 CLEAN。

------

# 12. continue：从当前状态继续

命令：

```bash
python main.py continue my_novel
```

`continue` 会自动判断小说当前真正的下一步。

可能执行：

```text
存在 WAITING_HUMAN
→ 显示当前人工检查点

Pre-Canonical workflow 未完成
→ 从 checkpoint 恢复

Canonical 已提交但 Derivation 未完成
→ 从未完成的 Derivation 阶段继续

最新章节已经 DERIVED_READY
→ 进入下一章

当前卷已经结束
→ 停在 Volume Boundary
```

## ⚠️ 重要：continue 不是状态查询命令

如果当前最新章节已经完全达到：

```text
DERIVED_READY
```

而当前卷仍然可以继续：

```bash
python main.py continue my_novel
```

会把：

```text
最新完成章节 N
```

解释为：

```text
下一合法动作 = 开始 Chapter N+1
```

并直接进入下一章工作流。

### 因此不要用 continue 查看当前状态

如果只是想看看小说目前写到哪里：

```bash
python main.py status my_novel
```

使用：

```text
status
```

而不是：

```text
continue
```

### 特别注意

如果误执行 `continue` 并已经进入下一章：

> 不要因为想“返回上一章”而批准新的 Plan 或 Prose。

Canonical 前的内容仍只是候选工作状态；Canonical 一旦成立，就进入正式小说历史。

`restart` 的含义是“重新开始当前章”，不是“退回上一章”。

------

# 13. restart：重新开始当前章

命令：

```bash
python main.py restart my_novel --chapter 5
```

仅允许：

```text
Pre-Canonical
```

它会删除当前章的候选执行内容，例如：

```text
Chapter Plan
Candidate Prose
Review
Writing Context
RAG Trace
Workflow Checkpoint
```

但保留：

```text
Chapter Intent
```

然后重新开始本章 Planning。

## Canonical 后禁止 restart

一旦：

```text
chapters/chapter_0005.md
```

已经正式存在：

```text
restart
```

会被拒绝。

------

# 14. Canonical 后 Derivation 失败

如果系统显示：

```text
Canonical Commit 已完成
Derivation 尚未完成
```

不要重新生成正文。

可以直接：

```bash
python main.py continue my_novel
```

或者显式执行：

```bash
python main.py repair-derivation my_novel --chapter 5
```

系统会从第一个未完成的派生阶段继续。

已经成功的阶段不会故意重新执行。

------

# 15. Autonomous 连续创作

只有：

```dotenv
CHAPTER_MODE=agent
AGENT_EXECUTION=autonomous
```

可以使用：

```bash
python main.py run my_novel --to-chapter 20
```

含义：

> 从当前小说真实状态继续，一直运行到 Chapter 20。

它与 `continue` 使用同一个状态路由。

遇到：

```text
Human Boundary
Volume Boundary
无法自动解决的 Review
错误
```

会停止。

使用目标章节而不是“再写 N 章”，可以保证中断后重新执行时目标保持不变。

------

# 16. Volume Lifecycle

Volume Plan 负责卷级故事路线。

它不需要提前指定：

```text
Chapter 1 做什么
Chapter 2 做什么
Chapter 3 做什么
```

章节细节由 Chapter Planner 根据当前故事状态决定。

卷的生命周期：

```text
DRAFT
→ ACTIVE
→ COMPLETED
```

初始化或 `new-volume` 后，新卷从 DRAFT 开始。

第一次真正进行章节规划时，它进入当前创作流程。

------

## 关闭当前卷

```bash
python main.py close-volume my_novel
```

关闭是显式人工决定。

`Volume Progress` 中：

```text
CONTINUE
READY_TO_CLOSE
UNKNOWN
```

都只是系统建议。

它不会自动关闭卷。

关闭前要求最新 Canonical Chapter 已完成 Derivation。

------

## 生成下一卷

当前卷关闭后：

```bash
python main.py new-volume my_novel
```

也可以提供额外方向：

```bash
python main.py new-volume my_novel \
  --notes "下一卷加强政治压力，并开始让两条支线汇合"
```

下一卷规划主要依据：

```text
World Setting
Book Plan
Current State
上一卷卷级结果
```

其中 Current State 告诉 ARCHITECT：

> 故事现在实际上停在哪里。

------

# 17. Historical RAG

章节 RAG 当前流程：

```text
Volume Plan
+ Previous Chapter End
+ Current State
+ Human Intent
        ↓
Query Intent Builder
        ↓
Query Intent
        ↓
Embedding
        ↓
Atomic Facts Top-K
        ↓
Canonical Paragraph Expansion
        ↓
Planner / Human Writing Context
```

上一章结尾使用：

```text
约 1500 中文字符
完整段落
```

不会从段落中间硬切。

Atomic Fact 命中后，系统找到对应 Canonical 原文，并展开：

```text
事实对应 paragraph range
+ 前一段
+ 后一段
```

Planner 最终决定哪些历史事实真正用于本章。

------

# 18. Chapter Sources

每章都会生成：

```text
sources/chapter_NNNN/chapter_sources.md
```

其中记录：

```text
Chapter Intent
Retrieval Query Intent
Retrieved Atomic Facts
Expanded Canonical Sources
Review / Approval Audit
```

因此以后可以追踪：

```text
这一章为什么检索这些资料
→ 找到了什么事实
→ 展开了什么历史正文
→ 最终进入了什么章节
```

------

# 19. RAG Top-K

配置：

```dotenv
RAG_TOP_K=5
```

控制 Query Intent 从 Atomic Fact RAG 中初步召回多少历史事实。

值越大：

```text
召回范围更广
上下文更多
噪声也可能更多
```

值越小：

```text
上下文更集中
但可能漏掉弱相关历史事实
```

------

# 20. Story Savepoint

## 创建

只有当前故事处于合法正式状态时才能创建：

```bash
python main.py savepoint create my_novel
```

例如 Chapter 40：

```text
S0040
```

------

## 查看

```bash
python main.py savepoint list my_novel
```

------

## 验证

```bash
python main.py savepoint verify my_novel S0040
```

------

## Load Savepoint

```bash
python main.py savepoint load my_novel S0040
```

Load 是破坏性操作。

系统会要求：

1. 输入小说名称；
2. 再输入准确的 `LOAD Sxxxx`。

Load 后：

```text
整个当前创作世界
→ 恢复为 Savepoint 对应状态
```

其他 READY Savepoint 不会因此消失。

因此可以：

```text
Load S0040
→ 测试另一条路线
→ 之后重新 Load S0080
```

前提是 S0080 本身仍是有效 READY Savepoint。

------

# 21. 自动 Savepoint

配置：

```dotenv
AUTO_SAVEPOINT_EVERY=0
```

`0`：

```text
关闭自动 Savepoint
```

例如：

```dotenv
AUTO_SAVEPOINT_EVERY=50
```

表示：

```text
Chapter 50 DERIVED_READY → S0050
Chapter 100 DERIVED_READY → S0100
Chapter 150 DERIVED_READY → S0150
```

自动 Savepoint 只在章节达到 `DERIVED_READY` 后执行。

------

# 22. status：只查看，不执行

```bash
python main.py status my_novel
```

如果你的目的只是：

- 看写到第几章；
- 看当前卷；
- 看当前状态；
- 确认是否完成；

优先使用：

```text
status
```

它和：

```text
continue
```

的区别非常重要：

```text
status
= 查看

continue
= 执行当前合法的下一步
```

------

# 23. 常用 CLI

| 命令                | 作用                                    |
| ------------------- | --------------------------------------- |
| `init`              | 创建 Proposal / 初始化小说              |
| `status`            | 查看状态，不推进工作流                  |
| `plan`              | 单独生成 Chapter Plan                   |
| `write`             | 开始或恢复指定章节                      |
| `continue`          | 从当前小说状态执行下一合法步骤          |
| `run --to-chapter`  | Autonomous 连续运行到目标章节           |
| `restart`           | 删除当前章 Pre-Canonical 工作并重新规划 |
| `repair-derivation` | 恢复 Canonical 后未完成的派生           |
| `style`             | 独立风格编辑                            |
| `close-volume`      | 人工关闭当前卷                          |
| `new-volume`        | 创建下一卷规划                          |
| `rag-index`         | 补齐或重建 RAG 索引                     |
| `savepoint create`  | 创建故事存档                            |
| `savepoint list`    | 查看存档                                |
| `savepoint verify`  | 验证存档                                |
| `savepoint load`    | 加载故事存档                            |

查看完整参数：

```bash
python main.py --help
python main.py write --help
python main.py savepoint --help
```

------

# 24. 数据目录

```text
data/novels/<novel_id>/
├── proposal.md
├── chapters/
│   ├── chapter_0001.md
│   └── ...
├── outlines/
├── briefs/
├── settings/
│   └── world_setting.md
├── tracking/
│   ├── book_plan.md
│   ├── volume_plan.md
│   ├── current_state.md
│   ├── volume_progress.md
│   └── volumes/
├── states/
├── sources/
├── story_savepoints/
├── state.db
└── workflow_checkpoints.sqlite
```

其中：

```text
chapters/chapter_NNNN.md
```

是正式正文。

不要把 Candidate、Review 或临时文件当成小说正式历史。

------

# 25. 安全边界

整个系统最重要的边界：

```text
Pre-Canonical
→ 可以修改、重写、restart

Canonical
→ 正文已经正式成立

Post-Canonical
→ 只能继续 Derivation
→ 或通过 Story Savepoint 恢复历史世界
```

如果不确定当前处于什么状态：

```bash
python main.py status my_novel
```

不要用 `continue` 做试探。

------

# 26. 测试

运行完整测试：

```bash
python -m pytest -q
```

开发测试默认不应依赖真实收费 API。

真实模型与真实 Embedding 的端到端验证应使用独立 smoke-test 小说进行。

------

# 27. 当前设计原则

Writer-Agent 当前遵循几个简单原则：

```text
作者决定自动化程度。

Canonical 是正文事实边界。

Current State 描述现在。

Atomic Facts 保存历史。

Query Intent 决定查什么。

RAG 找回历史证据。

Planner 决定怎么写。

Savepoint 保存整个故事世界。

所有关键失败默认 fail-closed，而不是悄悄继续。
```
