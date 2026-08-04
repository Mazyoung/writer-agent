# writer-agent：E07 后续开发规划

> 目标：在当前 LangGraph 基础设施已经基本完成的前提下，减少过细的阶段拆分，把剩余工作压缩为 4 个完整能力闭环。
>
> 当前原则：**不再单独规划测试阶段或测试对齐阶段。** 现有测试保留，但每个任务只做必要的针对性验证；除非出现明确 bug，否则不主动扩建测试体系。

---

## 一、当前项目进度概览

目前已经完成的核心能力：

- 生产章节入口已经切换到 LangGraph。
- 当前章节生产路径为：

```text
main.py
  ↓
ChapterWorkflowRunner
  ↓
ChapterWorkflow（LangGraph）
```

- 旧 Orchestrator 已退出章节生产主路径。
- 已实现章节生成前检查（Preflight）。
- 普通 Generate 不允许覆盖已经正式完成的章节。
- 已实现条件路由与明确的 Review Decision 分支。
- Review 缺失、截断、解析失败等情况保持 fail-closed。
- 只有 PASS 才允许进入正式 canonical commit。
- 已实现 LangGraph checkpoint。
- 已实现基于固定 thread_id 的章节执行恢复。
- 已实现 `interrupt()` / `Command(resume=...)` 人工中断恢复机制。
- 已实现 `WAITING_HUMAN` 等待人工状态。
- 当前 HITL 已能暂停和恢复，但尚未形成完整的“人工修改 → 重新审阅 → 自动修订”闭环。

因此，后续工作的重点已经从“迁移到 LangGraph”转变为：

1. 完成单章创作闭环；
2. 重构长期记忆与 RAG；
3. 整理当前状态与持久化职责；
4. 实现几十章级别的大范围剧情回档。

---

# 二、E07.6 —— 完整单章创作闭环

## 目标

把当前 E07.5 的基础 HITL 扩展为真正完整的一章小说生产流程。

目标流程：

```text
Generate
↓
Preflight
↓
读取可选“本章创作意图”
↓
读取当前状态与相关历史事实
↓
Chapter Planning
↓
Plan Review
├─ PASS → Writer
└─ non-PASS → Human

Writer
↓
Style Edit
↓
Chapter Review #1
├─ PASS → Commit
└─ NEEDS_REVISION
      ↓
   自动修订一次
      ↓
   Chapter Review #2
      ├─ PASS → Commit
      └─ non-PASS → Human
```

## 本阶段主要内容

### 1. 本章创作意图

正式建立 `chapter_intent` 概念。

Planner 的主要输入统一为：

```text
世界观
+ Book Plan
+ Volume Plan
+ Current State
+ 历史事实
+ Chapter Intent
```

本章创作意图是可选的人类创作入口，用于表达：

- 本章重点事件；
- 希望强调的人物变化；
- 希望埋设或推进的伏笔；
- 当前不能提前揭露的信息；
- 特殊写作要求。

### 2. 规划 Review

章节规划生成后必须经过 Review。

```text
Plan
↓
Plan Review
├─ PASS → Writer
└─ FAIL → Human
```

规划不通过时不进行自动无限重规划。

人工修改规划后：

```text
人工规划
↓
重新 Plan Review
```

不能绕过 Review 直接进入 Writer。

### 3. 正文最多自动修订一次

固定规则：

```text
Review #1 FAIL
↓
Auto Revision × 1
↓
Review #2
↓
仍然不通过
↓
Human
```

不允许模型自行决定无限 revision loop。

### 4. 人工正文恢复

人工修改正文后开启一轮新的 Review：

```text
人工正文
↓
Review #1
↓
最多自动修订一次
```

### 5. Planner / Writer 信息权限分离

Planner 可以看到更完整的长期规划和未来剧情约束。

Writer 默认只接收：

```text
已通过 Review 的 Chapter Plan
+ 当前必要状态
+ 相关历史事实
+ 必要历史原文
```

避免未来重大剧情信息直接暴露给 Writer，降低提前泄露风险。

## 完成标准

完成后，一章小说应能够从用户意图开始，经规划、规划审核、正文、正文审核、一次自动修订、必要人工介入，最终安全进入 canonical commit。

完成 E07.6 后，Chapter Graph 主骨架原则上不再进行大规模改动。

---

# 三、E07.7 —— 长期记忆与 RAG 2.0

## 目标

废除“整章正文切 Chunk 后全部进入向量库”的长期设计，改为：

```text
正文
↓
Review
↓
Fact Digest
↓
Atomic Facts
↓
Chroma
```

Fact Digest 成为历史事实的唯一全局向量索引入口。

## 1. 原子事实

Fact Digest 不再以整章摘要作为单个向量。

例如：

```text
FACT-072-001
林默左臂受伤。

FACT-072-002
黑色芯片被赵诚夺走。

FACT-072-003
林默发现自己没有记忆的童年相机。
```

每条事实至少保留：

```text
FACT-ID
Chapter
Fact Type
Entities
Paragraph Range
Fact Text
```

真正进行 embedding 的主体主要是 `Fact Text`。

## 2. Fact → 原文漏斗检索

新 RAG 流程：

```text
当前规划需求
↓
搜索历史 FACT
↓
返回候选 FACT
↓
Planner 判断真正相关的 FACT
↓
只对需要细节的 FACT 读取对应正文段落
↓
交给 Writer
```

职责：

```text
Fact Digest
= 找历史方向

正文
= 补充原始细节
```

正文不再参与全局向量搜索。

## 3. chapter_sources.md

本阶段同时加入章节来源记录。

每章至少记录：

- 本章创作意图；
- 使用的 Book Plan 内容；
- 使用的 Volume Plan 内容；
- 使用的 FACT-ID；
- 使用的未来剧情约束；
- 按需展开过的历史正文段落。

作用是回答：

> “这一章为什么会这样设计？”

同时帮助 Reviewer 检查：

- 历史事实是否被误解；
- 是否违反世界观；
- 是否错误执行未来规划；
- 是否提前泄露未来事件。

## 完成标准

完成后，Chroma 中的长期小说记忆应以 Atomic Fact 为核心，而不是以完整正文 Chunk 为核心。

---

# 四、E07.8 —— 当前状态与持久化 2.0

## 目标

把“当前世界状态”整理为真正的派生状态系统，并明确 Markdown、SQLite、Chroma、LangGraph checkpoint 的职责。

核心原则：

> **语义理解一次，后续持久化尽量确定性处理。**

## 1. Review 一次产生语义结果

正文 Review 最好一次得到：

```text
Review Decision
+ State Delta
+ Fact Digest
```

例如：

```text
林默位置：
医院 → 旧城区

黑色芯片持有人：
林默 → 赵诚

F023：
未解决 → 已解决
```

然后由普通程序负责更新：

```text
SQLite
current_state.md
fact_digest.md
Chroma
```

避免：

```text
一次 LLM 总结人物
一次 LLM 总结物品
一次 LLM 总结伏笔
一次 LLM 生成数据库数据
一次 LLM 再生成 Markdown
```

## 2. 当前状态采用 SQLite + Markdown

```text
State Delta
├─→ SQLite
└─→ current_state.md
```

其中：

### SQLite

负责确定性的高频查询，例如：

- 某角色当前位置；
- 某物品当前持有人；
- 未完成伏笔；
- 某伏笔多久没有推进；
- 当前章节元数据。

### current_state.md

供：

- 用户阅读；
- Planner 阅读；
- Reviewer 阅读。

无需再额外建立复杂的动态上下文渲染器。

## 3. 自动报告不可直接作为生产源修改

明确区分：

### 生产内容

- 世界观；
- Book Plan；
- Volume Plan；
- Chapter Intent；
- Chapter Plan；
- 正文。

### 自动报告

- current_state.md；
- fact_digest.md；
- chapter_sources.md；
- Review 报告；
- 检索记录。

如果自动报告错误，应修改其生产来源，然后重新派生，而不是直接修改报告本身。

## 4. 最终持久化职责

项目长期尽量只保留四类主要数据形式：

| 形式 | 职责 |
|---|---|
| Markdown | 创作内容 + 人/LLM 阅读的报告 |
| SQLite | 当前状态的高频精确查询 |
| Chroma | Atomic Fact 语义索引 |
| LangGraph checkpoint | 单章未完成执行恢复 |

JSON 不再作为主要长期持久化格式。

## 完成标准

完成后，系统中“现在是什么”和“过去发生过什么”必须明确分离：

```text
SQLite + current_state.md
= 现在是什么

Fact Digest + Chroma
= 过去发生过什么
```

---

# 五、E07.9 —— 大范围剧情 Savepoint / Rollback

## 目标

实现几十章级别的剧情整体恢复机制。

必须与 LangGraph checkpoint 明确区分：

```text
LangGraph checkpoint
= 一章写到一半程序退出

Story Savepoint
= 已经写了几十章后发现整体剧情方向错误
```

## 1. Savepoint

推荐默认：

```text
每 50 章自动建立一次
```

同时允许人工建立保存点。

默认只保留最近一个保存点，未来有需要再扩展到 2～3 个。

保存内容至少包括：

```text
世界观
Book Plan
Volume Plan
正式生产内容
Current State
SQLite
Fact Digest
Chroma
其他必要正式文件
```

目标是恢复到：

> “保存点章节正式完成以后，整个 Agent 所看到的世界。”

## 2. Rollback

例如：

```text
完成第 50 章
↓
建立 S50
↓
生成第 51～100 章
↓
发现整体方向错误
↓
Rollback → S50
↓
重新从第 51 章开始
```

回档必须：

- 删除保存点之后的当前生产章节及相关产物；
- 恢复规划、状态、SQLite、Fact Digest、Chroma；
- 清理不再有效的 LangGraph checkpoint；
- 恢复后允许重新从下一章 Generate。

## 3. 尽量零 Token 恢复

Savepoint 应直接保存当时的正式数据和向量库状态。

原则上：

```text
恢复文件
+ 恢复数据库
+ 恢复 Chroma
```

而不是：

```text
重新读取几十/几百章
+ LLM 重新总结
+ 重新生成大量 embedding
```

## 4. 不建立废弃剧情 archive

回档后的废弃正文不在生产项目中继续保留。

避免未来：

- RAG 检索到废弃剧情；
- 状态重建混入旧世界线；
- 每个查询都要判断 archive 标记。

需要保留废弃文本时，由用户在回档前自行备份。

## 5. 强确认

Rollback 是高破坏性操作，必须明确展示：

```text
即将恢复至第 XX 章。

将删除：
第 XX+1 ～ 当前章节及相关生成产物。

将恢复：
世界观
Book Plan
Volume Plan
Current State
SQLite
Fact Digest
RAG

系统不会自动保存被删除的废弃正文。
```

用户明确确认后才能执行。

## 完成标准

完成后，应能够在不重新调用大量 LLM / Embedding 的情况下，将整个小说 Agent 恢复到某个历史正式世界状态。

---

# 六、后续阶段总览

| 阶段 | 核心目标 | 完成后得到什么 |
|---|---|---|
| **E07.6** | 完整单章创作闭环 | 一章从 Intent → Plan → Review → Write → Revision → Human → Commit 完整运行 |
| **E07.7** | 长期记忆 / RAG 2.0 | Atomic Fact 唯一向量索引 + FACT → 原文漏斗检索 + chapter_sources |
| **E07.8** | 当前状态与持久化 2.0 | SQLite 管现在、Fact Digest 管历史，语义理解一次后确定性落盘 |
| **E07.9** | Story Savepoint / Rollback | 几十章剧情方向错误时能够整体恢复 |

---

# 七、测试与验收策略调整

后续不再设置：

```text
Test Alignment
Test Closure
独立测试阶段
```

已有测试代码保留，但开发主线不再围绕测试拆任务。

每个阶段完成后仅进行必要验证，例如：

- Python import / compile；
- 当前核心 CLI 能启动；
- 当前阶段关键路径 smoke check；
- 检查 Git diff 是否存在无关修改；
- 出现明确 bug 时再补针对性测试。

除非明确要求，否则不主动扩展大规模 contract test 或重复运行昂贵的完整生成测试。

---

# 八、最终目标架构

```text
                 CLI / Future UI
                       │
       ┌───────────────┼────────────────┐
       │               │                │
 Novel Lifecycle   Volume Mgmt     Story Savepoint
                                        │
                                  大范围剧情恢复

                Chapter LangGraph
                       │
                   Preflight
                       │
                 Chapter Intent
                       │
                 Fact Retrieval
                       │
                    Planning
                       │
                  Plan Review
                       │
                    Writing
                       │
                     Style
                       │
                 Chapter Review
                       │
               Auto Revision × 1
                       │
                Human if needed
                       │
                    Commit
                   /      \
                  /        \
          Current State    Fact Digest
          SQLite + MD          │
                              Chroma
                               │
                        FACT → Source Text
```

最终项目理念可以概括为：

> **人和 LLM 主要围绕 Markdown 创作与阅读；SQLite 管“现在是什么”，Fact Digest + Chroma 管“过去发生过什么”，正文保留原始细节，LangGraph 管“一章怎么安全走完”，Story Savepoint 管“几十章走错以后怎么整体回来”。**

---

# 九、当前下一步

当前直接进入：

> **E07.6 —— 完整单章创作闭环**

不再单独进行 E07.5 Test Alignment。

E07.6 完成后依次推进：

```text
E07.6 单章闭环
↓
E07.7 长期记忆 / RAG 2.0
↓
E07.8 当前状态与持久化 2.0
↓
E07.9 Story Savepoint / Rollback
```

E07.9 完成后，writer-agent 第二阶段核心架构可以视为基本收口，后续开发重点转向真实长篇生成质量、上下文效果、模型组合与使用体验。
