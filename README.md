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
| `agent_edit` | 告诉 Agent 哪里需要修改 |
| `manual_edit` | 自己直接修改 |
| `regenerate` | 保留这一章的方向，重新生成正文 |
| `pause` | 暂停，之后继续 |
| `discard` | 放弃本次生成 |

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

在项目根目录创建 `.env`，填写你使用的模型 API 信息。

例如：

```dotenv
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

具体模型可以根据自己的需求调整。

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
  --resume "减少解释性对白，让人物通过动作表现紧张感"

# 自己修改后继续
python main.py write my_novel --chapter 1 --action manual_edit

# 重新生成
python main.py write my_novel --chapter 1 --action regenerate

# 暂停
python main.py write my_novel --chapter 1 --action pause

# 放弃本次生成
python main.py write my_novel --chapter 1 --action discard
```

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

确认后：

```bash
python main.py approve-volume my_novel
```

然后继续下一卷的章节创作。

---

# 常用命令

| 命令 | 用途 |
|---|---|
| `init` | 创建小说 |
| `plan` | 生成章节规划 |
| `write` | 创作章节 |
| `status` | 查看当前状态 |
| `repair-derivation` | 完成未完成的章节信息整理 |
| `close-volume` | 完成当前卷 |
| `new-volume` | 创建下一卷规划 |
| `approve-volume` | 确认下一卷规划 |

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
