# Writer-Agent

Writer-Agent 是一个面向长篇小说创作的本地 Agent 工作流。它把长期规划、章节写作、人工审阅、正式正文提交、状态派生、历史事实检索和卷生命周期拆成清晰边界，并把每一步结果保存为可检查的 Markdown、SQLite、Chroma 和 LangGraph checkpoint。

当前生产入口是 main.py。章节生产由可恢复的 LangGraph 工作流负责；不存在旧 Orchestrator，也没有绕过 Human Review 的独立自动提交路径。

## 当前能力

| 能力 | 当前行为 |
|---|---|
| 分层规划 | Book Plan → ACTIVE Volume Plan → Chapter Plan |
| 章节生产 | Planner、Writer、Stylist、Review、Final Author Approval |
| 人工控制 | Plan Review 和 Prose Review 都可暂停、编辑、恢复或停止 |
| Canonical 正文 | chapters/chapter_NNNN.md，创建一次，普通 Generate 不可覆盖 |
| Derivation | 从 canonical prose 派生 Current State、Fact Digest、Atomic Facts 和 VolumeProgress |
| 故障修复 | canonical 已提交但派生失败时，可从 checkpoint 继续 repair |
| 长期记忆 | Atomic Fact Chroma 检索 + canonical 原文局部展开 |
| 作者知识 | tracking/author_rag.md 为唯一来源，同步失败时 fail-closed |
| 卷生命周期 | DRAFT → ACTIVE → COMPLETED，关闭与新建都由显式命令触发 |
| 状态查询 | Markdown 为可读权威，SQLite 提供精确查询投影 |

## Chapter Workflow

~~~mermaid
flowchart TD
    A["Chapter Intent + Current State + Historical Facts"] --> B["Chapter Plan"]
    B --> C["Plan Review"]
    C -->|非 PASS| D["Human：编辑规划或停止"]
    D -->|编辑| C
    C -->|PASS| E["Writer → Stylist"]
    E --> F["Prose Review"]
    F --> G["Human Review / Final Author Approval"]
    G -->|agent_edit / manual_edit / regenerate| E
    G -->|pause| G
    G -->|discard，且尚未 canonical| X["删除候选执行，保留 Intent"]
    G -->|PASS 后 approve| H["Canonical Commit"]
    H --> I["Derivation"]
    I -->|失败| J["DERIVATION_ERROR\ncanonical 仍保留"]
    J --> K["repair-derivation"]
    K --> I
    I -->|完成| L["DERIVED_READY"]
~~~

关键规则：

- Review FAIL 不自动改稿，必须回到 Human。
- Review PASS 也不会自动提交，必须经过 Final Author Approval。
- 所有正文变更都要重新 Review。
- canonical 提交和 derivation 是两个独立边界。
- derivation 失败不会删除已经批准的 canonical 正文。
- 普通 write 不会覆盖已经存在的 canonical chapter。

## 快速开始

### 1. 环境

项目当前 CI 使用 Python 3.14。安装依赖：

~~~bash
python -m pip install -r requirements.txt
~~~

在项目根目录创建 .env：

~~~dotenv
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
ANTHROPIC_API_KEY=your_key_if_style_agent_requires_it
~~~

运行数据写入 data/novels/<novel_id>/，该目录不作为源码提交。

### 2. 初始化小说

~~~bash
python main.py init my_novel "一句话故事前提"
~~~

审阅 proposal.md 后确认初始化：

~~~bash
python main.py init my_novel --confirm
~~~

初始化会生成：

- settings/world_setting.md
- tracking/book_plan.md
- tracking/volume_plan.md，初始状态为 DRAFT
- tracking/current_state.md

直接编辑 volume_plan.md，确认后激活：

~~~bash
python main.py approve-volume my_novel
~~~

### 3. 规划与写作

可选：单独生成 Chapter Plan。

~~~bash
python main.py plan my_novel --chapter 1
~~~

运行完整、可恢复的章节工作流：

~~~bash
python main.py write my_novel --chapter 1 \
  --intent "推进两人的信任，但不能揭露幕后主使"
~~~

如果工作流等待人工处理，CLI 会打印 interrupt 类型、原因和编辑文件路径。可使用：

~~~bash
python main.py write my_novel --chapter 1 --action agent_edit --resume "缩短解释段"
python main.py write my_novel --chapter 1 --action manual_edit
python main.py write my_novel --chapter 1 --action regenerate
python main.py write my_novel --chapter 1 --action pause
python main.py write my_novel --chapter 1 --action discard
python main.py write my_novel --chapter 1 --action approve
python main.py write my_novel --chapter 1 --stop
~~~

其中 manual_edit 会读取 interrupt 中指定的编辑文件；approve 只在最新 Prose Review 为 PASS 时可用。

### 4. Derivation Repair

Final Author Approval 后，系统先写入唯一 canonical 正文：

~~~text
chapters/chapter_NNNN.md
~~~

随后才派生：

- tracking/current_state.md
- state.db 当前状态投影
- states/fact_digest_chNNNN_*.md
- Atomic Facts Chroma
- sources/chapter_NNNN/chapter_sources.md
- tracking/volume_progress.md
- states/chapter_NNNN_derived

如果派生失败，状态为 DERIVATION_ERROR，canonical 仍然存在。修复命令会从第一个未完成的派生步骤继续，不重复已成功的步骤：

~~~bash
python main.py repair-derivation my_novel --chapter 1
~~~

最终状态为 DERIVED_READY。

## Human Review

| 阶段 | Human 可做什么 | 约束 |
|---|---|---|
| Plan Review 非 PASS | 编辑规划、停止 | Writer 不能在规划未通过时运行 |
| Prose Review 非 PASS | agent_edit、manual_edit、regenerate、pause、discard | 不允许 approve |
| Prose Review PASS | 以上动作或 approve | PASS 只是必要条件，不是自动提交 |
| canonical 提交后 | repair derivation | 不允许 discard 或普通 Generate 覆盖 |

动作含义：

- agent_edit：按反馈局部修订当前候选正文，再 Review。
- manual_edit：读取人工编辑文件，再 Review。
- regenerate：保留已批准 Chapter Plan，从 Writer 重新生成。
- pause：保留当前 interrupt，不消费 checkpoint。
- discard：仅限 canonical 前；删除本次候选执行，保留 Chapter Intent。
- approve：将最新 PASS 正文提交为 canonical。

## Volume Lifecycle

Volume Plan 只描述卷级故事路径，不绑定具体章节。人工增加的 Notes 和自由 sections 会被保留；validator 只拒绝真实的旧式章节范围、逐章事件表或 chapter assignment 结构。

~~~mermaid
stateDiagram-v2
    [*] --> DRAFT: init / new-volume
    DRAFT --> ACTIVE: approve-volume
    ACTIVE --> COMPLETED: close-volume
    COMPLETED --> DRAFT: new-volume
~~~

常用命令：

~~~bash
python main.py approve-volume my_novel
python main.py close-volume my_novel
python main.py new-volume my_novel --notes "下一卷加强政治压力"
~~~

说明：

- tracking/volume_progress.md 中的 CONTINUE、READY_TO_CLOSE、UNKNOWN 都只是建议，不自动关闭卷，也不限制人工关闭。
- close-volume 的硬条件是最新 canonical chapter 已达到 DERIVED_READY；否则必须先 repair derivation。
- close-volume 会把当前卷标记为 COMPLETED，并归档到 tracking/volumes/。
- new-volume 只在当前卷已关闭后生成下一卷 DRAFT；人工编辑后再 approve-volume。

## RAG 与历史事实

生产长期记忆以 Atomic Facts 为单位：

~~~mermaid
flowchart LR
    A["Canonical Prose"] --> B["Fact Digest Markdown"]
    B --> C["Atomic Facts"]
    C --> D["Chroma: atomic_facts_v2"]
    D --> E["Chapter Retrieval"]
    E --> F["匹配事实对应的 canonical 原文局部"]
    F --> G["Chapter Plan"]
~~~

Chroma 只嵌入 Fact Text，不把整章正文作为生产向量语料。检索只允许读取当前章之前、当前 novel 和 main branch 的事实。Planner 选择采用哪些事实与原文片段；Writer 只接收已批准 Chapter Plan 中的上下文，不直接读取 Book Plan 或 Volume Plan。

维护命令：

~~~bash
python main.py rag-index my_novel
python main.py rag-index my_novel --rebuild
~~~

## 主要 CLI

| 命令 | 用途 |
|---|---|
| init | 生成提案；--confirm 后生成 World Setting、Book Plan、Volume Plan |
| status | 查看卷状态、canonical 章节数和当前状态 |
| plan | 独立生成 Chapter Plan |
| write | 运行或恢复完整章节工作流 |
| style | 独立风格编辑，不执行 Review 或 canonical commit |
| repair-derivation | 修复 canonical 后未完成的派生 |
| approve-volume | 将人工确认的 DRAFT Volume Plan 切换为 ACTIVE |
| close-volume | 人工关闭 ACTIVE 卷 |
| new-volume | 在当前卷关闭后生成下一卷 DRAFT |
| rag-index | 补齐或重建 Atomic Fact RAG |

查看完整参数：

~~~bash
python main.py --help
python main.py write --help
~~~

## 数据位置

~~~text
data/novels/<novel_id>/
├── chapters/                  # canonical 正文与候选正文
├── outlines/                  # Chapter Plan
├── settings/                  # World Setting
├── tracking/                  # Book/Volume/Current State/Author RAG
├── tracking/volumes/          # 已关闭卷归档
├── states/                    # Review、Derivation、Fact Digest、derived marker
├── sources/                   # 章节来源报告
├── state.db                   # Current State 精确查询投影
└── workflow_checkpoints.sqlite
~~~

详细职责见 ARCHITECTURE.md。

## 测试与 CI

GitHub CI 使用零 API credentials。tests/conftest.py 会统一替换 OpenAI client 构造器，自动测试不得访问真实模型或网络。

正式 CI 保护的是功能契约与安全 invariant，包括 Human Review、canonical 不覆盖、derivation failure/repair、Current State、Atomic Fact RAG、Author RAG fail-closed 和 Volume Lifecycle；不要求固定 Graph node 名、内部 helper 次数或临时 state 布局。

~~~bash
python -m pytest -q
~~~

## 当前项目状态

当前完成到 E07.9：Chapter 三阶段架构、Current State、Atomic Fact RAG、Human Review、Derivation Repair、Volume Lifecycle 和 Architecture CI Baseline 已投入当前生产路径。

E07.10 的 Story Snapshot、Jump、Branch、Savepoint / Restore 尚未实现。当前 CLI 不提供这些能力。
