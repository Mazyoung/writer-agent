# Writer-Agent 当前架构

本文面向开发者，只描述当前生产系统。源码与自动测试是最终事实来源。

## 1. 总体边界

系统分为三类入口：

| 入口 | 责任 |
|---|---|
| NovelLifecycleService | 初始化小说与 Volume Lifecycle |
| ChapterWorkflowRunner | 单章完整生产、Human interrupt/resume、derivation repair |
| 独立服务 | status、standalone plan/style、RAG maintenance |

main.py 是 CLI 组合层。LangGraph 只拥有单章生产工作流，不负责小说级 snapshot、分支或卷级生命周期。

~~~mermaid
flowchart LR
    CLI["main.py CLI"] --> NL["NovelLifecycleService"]
    CLI --> CW["ChapterWorkflowRunner"]
    CLI --> SS["Standalone Services"]

    NL --> PLAN["World Setting / Book Plan / Volume Plan"]
    CW --> GRAPH["Checkpointed Chapter Graph"]
    SS --> MAINT["Status / Plan / Style / RAG Maintenance"]

    GRAPH --> CANON["Canonical Markdown"]
    GRAPH --> DERIVED["Derived Markdown / SQLite / Chroma"]
~~~

## 2. Chapter 三阶段架构

~~~mermaid
flowchart TD
    subgraph P1["阶段 1：Planning"]
        I["Chapter Intent"] --> CS["Checkpointed Current State"]
        CS --> RET["Atomic Fact + Author RAG Retrieval"]
        RET --> CP["Chapter Plan"]
        CP --> PR["Plan Review"]
        PR -->|非 PASS| PH["Human Plan Edit / Stop"]
        PH -->|Edit| PR
    end

    subgraph P2["阶段 2：Creation / Review"]
        PR -->|PASS| W["Writer"]
        W --> S["Stylist + Style Check"]
        S --> R["Prose Review"]
        R --> H["Human Decision"]
        H -->|agent_edit / manual_edit / regenerate| W
        H -->|PASS 后 approve| CC["CANONICAL_COMMITTED"]
    end

    subgraph P3["阶段 3：Derivation"]
        CC --> D["Semantic Deriver"]
        D --> CSP["Current State Persistence"]
        CSP --> FD["Fact Digest / Atomic Facts"]
        FD --> VP["VolumeProgress"]
        VP --> SRC["Chapter Sources"]
        SRC --> CR["Atomic Fact Chroma Sync"]
        CR --> READY["DERIVED_READY"]
    end

    D -.失败.-> ERR["DERIVATION_ERROR\ncanonical 保留"]
    ERR --> REPAIR["repair-derivation"]
    REPAIR --> D
~~~

### 2.1 Planning

输入至少包括：

- Chapter Intent；
- checkpointed Previous Current State；
- Book Plan 与 ACTIVE Volume Plan；
- 当前章之前的 Atomic Facts；
- 采用事实对应的 canonical prose 局部；
- Author RAG。

PlanReviewer 负责检查 Chapter Plan。非 PASS 进入 Human interrupt；人工编辑后的规划必须重新 Review。任何 UNKNOWN 或解析失败都 fail-closed，Writer 不得运行。

### 2.2 Creation / Review

Writer 只消费经过 Review 的 Chapter Plan 上下文包。它不直接读取 Book Plan、Volume Plan 或未采用的检索候选。

Stylist 只编辑当前 draft，不拥有 canonical commit。Prose Reviewer 只输出质量决策、问题与反馈，不生成 StateDelta 或 Fact Digest。

PASS 和非 PASS 都进入 Human：

- 非 PASS 不允许 approve；
- PASS 仍需 Final Author Approval；
- agent_edit、manual_edit、regenerate 后都必须重新 Review；
- discard 只允许发生在 canonical commit 前。

### 2.3 Canonical Commit

正式正文路径固定为：

~~~text
chapters/chapter_NNNN.md
~~~

FileStore.commit_canonical_chapter 使用 create-once 语义。普通 Generate、候选稿、styled 文件和人工编辑文件都不能覆盖已存在的 canonical chapter。

CANONICAL_COMMITTED 表示正文已经成为故事事实，但派生数据尚不一定完成。

### 2.4 Derivation

Deriver 的输入是：

- Canonical Prose；
- Previous Current State；
- Current ACTIVE Volume Plan。

事实权限严格分离：

| 输出 | 可依据 Canonical Prose | 可依据 Volume Plan |
|---|---:|---:|
| StateDelta | 是 | 否 |
| Fact Digest / Atomic Facts | 是 | 否 |
| Current State | 是 | 否 |
| VolumeProgress | 是 | 是，仅用于卷进度判断 |

Volume Plan 中尚未发生的未来剧情不得进入任何事实或当前状态。

派生步骤按 checkpoint 顺序执行。任何阶段失败都进入 DERIVATION_ERROR，已提交 canonical 不回滚。repair-derivation 从第一个未完成步骤继续，已成功步骤不重复推进；全部完成后状态为 DERIVED_READY。

## 3. Canonical 与 Derived 边界

~~~mermaid
flowchart TB
    subgraph AUTH["Human / Canonical 权威"]
        WS["world_setting.md"]
        BP["book_plan.md"]
        VP["volume_plan.md"]
        CI["chapter_intent_chNNNN.md"]
        CP["chapter_plan_chNNNN.md"]
        PROSE["chapter_NNNN.md"]
        AR["author_rag.md"]
    end

    subgraph DER["派生与查询"]
        CUR["current_state.md"]
        SQL["state.db"]
        FD["fact_digest_chNNNN_*.md"]
        CH["Chroma atomic_facts_v2"]
        VPROG["volume_progress.md"]
        SOURCES["chapter_sources.md"]
    end

    subgraph EXEC["执行恢复"]
        CK["workflow_checkpoints.sqlite"]
    end

    PROSE --> CUR
    CUR --> SQL
    PROSE --> FD
    FD --> CH
    PROSE --> VPROG
    VP --> VPROG
    CP --> SOURCES
    PROSE --> SOURCES
    AUTH --> CK
    CK -.恢复执行，不是故事权威.-> DER
~~~

数据冲突时的优先级：

1. canonical Markdown 故事与规划；
2. generated Current State Markdown；
3. SQLite 精确投影；
4. Chroma 检索索引；
5. checkpoint 与诊断文件。

Checkpoint 记录执行位置，不替代 canonical 故事状态，也不提供广义 rollback。

## 4. 数据职责

| 数据 | 路径 | 类型 | 写入者 | 读取者 |
|---|---|---|---|---|
| World Setting | settings/world_setting.md | canonical | 初始化 / Human | Planner、Review |
| Book Plan | tracking/book_plan.md | canonical | 初始化 / Human | Planner、Plan Review、Volume Planner |
| Volume Plan | tracking/volume_plan.md | canonical lifecycle state | NovelLifecycle / Human | Planner、Plan Review、VolumeProgress |
| Chapter Intent | briefs/chapter_intent_chNNNN.md | canonical Human intent | CLI / Human | Planner |
| Chapter Plan | outlines/chapter_plan_chNNNN.md | canonical execution plan | ChapterPlanner / Human | Writer、Review |
| Candidate Prose | chapters/chapter_NNNN_styled_*.md | 候选 | Stylist / Human | Prose Review |
| Canonical Prose | chapters/chapter_NNNN.md | canonical | Final Author Approval | History、Deriver、RAG source expansion |
| Current State | tracking/current_state.md | generated authority | CurrentStateStore | Planner、Review、next-volume |
| Current State Projection | state.db | derived exact projection | SQLiteStore | 精确状态查询 |
| Fact Digest | states/fact_digest_chNNNN_*.md | derived history | Deriver | RAG rebuild |
| Atomic Facts | Chroma atomic_facts_v2 | derived index | AtomicFactStore | ChapterRetrievalService |
| Author RAG | tracking/author_rag.md | canonical knowledge source | Human / sync workflow | Retrieval |
| Volume Progress | tracking/volume_progress.md | advisory | Derivation | Human |
| Sources Report | sources/chapter_NNNN/chapter_sources.md | diagnostic provenance | Workflow | Human / audit |
| Checkpoint | workflow_checkpoints.sqlite | execution state | LangGraph | Runner / repair |

## 5. Current State 与 SQLite

~~~mermaid
sequenceDiagram
    participant D as Deriver
    participant C as CurrentStateStore
    participant M as current_state.md
    participant S as state.db
    participant K as derived marker

    D->>C: StateDelta + expected base SHA
    C->>C: 解析、校验、确定性 apply
    C->>S: BEGIN IMMEDIATE
    C->>M: 写入完整 Current State
    C->>S: 替换当前状态投影
    C->>K: 写 chapter_NNNN_derived
    C->>S: COMMIT
    alt 任一步失败
        C->>S: ROLLBACK
        C->>M: 恢复旧 Markdown
        C->>K: 删除本次 marker
    end
~~~

tracking/current_state.md 是当前状态的可读重建权威，包含角色、关系、物品、修炼、伏笔与最新 canonical chapter metadata。

state.db 提供 novel-isolated 的精确查询。若 SQLite 中保存的 SHA-256 与 Current State Markdown 不一致，系统从 Markdown 重建投影。

Python 与 Markdown 模型统一使用 canonical_source_path / Canonical Source。SQLite current_chapter_meta 仍保留兼容列名 styled_source_path；它保存的是 canonical source path，不为纯命名增加 migration。

## 6. RAG

### 6.1 Atomic Fact RAG

~~~mermaid
flowchart LR
    P["Canonical Prose"] --> D["Fact Digest"]
    D --> F["Atomic Facts\nFACT-ID / chapter / entities / paragraph range"]
    F --> C["Chroma atomic_facts_v2"]
    C --> Q["按 novel、branch、历史章节检索"]
    Q --> E["canonical prose 局部展开"]
    E --> PL["Chapter Plan 采用事实"]
~~~

约束：

- Chroma 只嵌入 Fact Text；
- 只检索 chapter_index 小于当前章的事实；
- 默认 branch_id 为 main；
- source expansion 只读取唯一 canonical chapter；
- 无有效 paragraph range 时不展开原文；
- Writer 只看到 Chapter Plan 采用的事实与片段。

### 6.2 Author RAG

tracking/author_rag.md 是唯一来源；author_rag_edited.md 不具备覆盖权。检索前执行 scoped hash 检查、必要的 rebuild/re-embedding。同步或读取失败时，Chapter Retrieval fail-closed，不允许 Planner 在不完整作者知识上继续。

### 6.3 Maintenance

rag-index 补齐 Markdown Fact Digest 对应的 Atomic Facts。rag-index --rebuild 重建当前 novel / branch 的事实索引。RAG maintenance 不改变 canonical prose。

## 7. Checkpoint 与 Human Resume

每个章节使用稳定 thread ID：

~~~text
chapter:<novel_id>:<chapter_index 四位数>
~~~

checkpoint 位于：

~~~text
data/novels/<novel_id>/workflow_checkpoints.sqlite
~~~

Runner 责任：

1. 启动新章节执行；
2. 返回 pending Human interrupt；
3. 使用 Command(resume=...) 恢复同一执行；
4. 终止可重试的 pre-canonical error 执行；
5. canonical 后保留 DERIVATION_ERROR 以供 repair；
6. 已完成 DERIVED_READY 时不重放节点；
7. 普通 Generate 发现 canonical 已存在时拒绝覆盖。

Checkpoint 不拥有 Book/Volume lifecycle，也不提供 Story Snapshot、Jump 或 Restore。

## 8. Planner 与 Writer 权限

| 组件 | 可以读取 | 不得做 |
|---|---|---|
| ChapterPlanner | Intent、World Setting、Book/Volume Plan、Current State、检索事实与原文局部 | 写 canonical prose；把 Volume Plan 强制分配到章节 |
| PlanReviewer | Chapter Plan 与规划/状态/历史上下文 | 自动批准或直接调用 Writer |
| Writer | 已批准 Chapter Plan、有限 World Setting、上一章结尾 | 直接读取长期计划；自行改变 L2/L3 规划 |
| Stylist | 当前 draft、风格上下文 | 写 canonical；引入新规划事实 |
| Prose Reviewer | 当前候选正文与一致性上下文 | 生成状态事实；自动 commit |
| Deriver | canonical prose、previous Current State、ACTIVE Volume Plan | 修改正文；从未来计划制造事实 |
| Human | 编辑计划/候选稿、批准正文、控制卷状态 | 无自动化限制；但 close-volume 仍受数据一致性 guard 约束 |

## 9. Volume Lifecycle

Volume Plan 状态机不属于 Chapter Graph。

~~~mermaid
flowchart LR
    INIT["init"] --> D1["Volume 1 DRAFT"]
    D1 -->|approve-volume| A["ACTIVE"]
    A -->|章节生产| PROG["VolumeProgress\nCONTINUE / READY_TO_CLOSE / UNKNOWN"]
    PROG -.仅建议.-> A
    A -->|close-volume| G{"最新 canonical chapter\n是否 DERIVED_READY？"}
    G -->|否| BLOCK["拒绝，先 repair derivation"]
    G -->|是| DONE["COMPLETED + archive"]
    DONE -->|new-volume| D2["Next Volume DRAFT"]
    D2 -->|Human 编辑 + approve-volume| A
~~~

规则：

- VolumeProgress 不触发生命周期变更；
- 三种建议值都不限制 Human 主动 close-volume；
- 数据一致性是硬条件：最新 canonical chapter 必须 DERIVED_READY；
- new-volume 读取 World Setting、Book Plan、Previous Volume Plan 和 Current State；
- 新卷先生成 DRAFT，人工直接编辑原文件，再 approve-volume；
- validator 允许任意自由 sections / notes，只拒绝结构化章节范围、逐章事件表、事件对应章节或 chapter assignment；
- Volume Plan 不规定事件发生在哪一章。

## 10. 失败语义

| 失败位置 | 结果 |
|---|---|
| Preflight / Planning / Retrieval | error，禁止 Writer |
| Plan Review UNKNOWN | fail-closed，禁止 Writer |
| Prose Review UNKNOWN | fail-closed，禁止 canonical commit |
| Final Approval 前 | 无 canonical，可编辑、停止或 discard |
| Canonical commit 失败 | 禁止 derivation |
| Derivation 任一步失败 | DERIVATION_ERROR，canonical 保留 |
| Current State transaction 失败 | Markdown、SQLite、marker 回滚 |
| Atomic Fact / Author RAG sync 失败 | 可观察错误；Author RAG 失败时 retrieval fail-closed |
| close-volume 时最新章未 DERIVED_READY | 拒绝关闭，要求 repair |

## 11. CI Baseline

Architecture CI Baseline 保护外部行为和安全 invariant：

- Human interrupt / resume；
- Review 非 PASS 不自动修订；
- Review PASS 不自动 commit；
- canonical create-once 与历史读取；
- canonical commit 后 derivation；
- derivation failure 保留 canonical；
- repair 达到 DERIVED_READY；
- Current State 原子持久化与 SQLite 投影；
- Atomic Fact RAG 和 Author RAG fail-closed；
- VolumeProgress 仅建议；
- close-volume consistency guard；
- next-volume / approve-volume；
- Volume Plan 不绑定章节。

自动测试通过 tests/conftest.py 统一注入 Fake OpenAI client，在零 credentials 环境中不得初始化真实 SDK client或访问模型 API。

CI 不把以下内容当作稳定契约：

- Graph node 名；
- 某个 helper 是否存在或调用次数；
- 临时 state 字段布局；
- candidate 时间戳格式；
- 某功能必须由某个 class 实现。

## 12. 尚未实现

E07.10 的以下能力尚未实现：

- Story Snapshot；
- Jump；
- Branch；
- Savepoint / Restore；
- 广义 rollback。

当前 checkpoint 只能恢复单章执行，不能替代这些未来能力。
