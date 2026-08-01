# 项目总体架构图

> 最后更新: 2026-05-23

---

## ⚠️ 当前实现状态（2026-08-01, E03）

本文档主体描述的是**旧版架构**（scene/replan 命令、BriefGenerator、SyncManager、
ChromaDB 检索等），其中大部分已重构或移除，仅作历史参考。
当前真实实现以代码与 `E03_IMPLEMENTATION_REPORT.md` 为准：

**已经实现（正向链路）**：
```text
Book Plan (tracking/book_plan.md, 战略层, 默认稳定)
   ↓
Volume Plan (tracking/volume_plan.md, 战术层, Rolling Horizon)
   ↓   new-volume 命令显式滚动：旧卷归档 tracking/volumes/ + PlanRevision
Chapter Plan (outlines/chapter_plan_chNNNN.md, 每章生成)
   ↓
Scene / Writer (Execution)
```

**只是 Foundation、尚未接通**：
```text
Execution → L2/L3 判断 → Human Review → Replanning / Rollback
```
已建立数据模型与持久化接口（`src/planning/`）：PlanRevision、
PlanningModificationReport、StrategicRepairCase、StoryBranch、
ChapterCheckpoint、ReplanTriggerPolicy。**没有任何自动 L2/L3 判断、
自动 Plan Revision 或自动 Rollback**——这些结构当前等待后续阶段接入。

---

## 一、系统全景图（旧版，历史参考）

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           人机交互层                                      │
│                                                                          │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│   │ 初始创意  │    │ 编辑设定  │    │ 修改大纲  │    │ 审阅章节  │          │
│   │ (CLI)    │    │(_edited) │    │(_edited) │    │(_edited) │          │
│   └────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘          │
│        │               │               │               │                │
│        ▼               ▼               ▼               ▼                │
│   ┌──────────────────────────────────────────────────────────────┐      │
│   │                     Interceptor (拦截器)                       │      │
│   │   auto_pass / notify / require_approval                      │      │
│   │   文件级介入: *_edited.md 优先读取                             │      │
│   └──────────────────────────────────────────────────────────────┘      │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          Orchestrator (编排器)                            │
│                                                                          │
│   ┌─────────────────────┐    ┌─────────────────────┐                     │
│   │  initialize_novel() │    │  write_chapter()    │                     │
│   │  世界观 + 大纲创建   │    │  9步创作循环         │                     │
│   └─────────┬───────────┘    └─────────┬───────────┘                     │
│             │                          │                                  │
│   ┌─────────▼───────────┐    ┌─────────▼───────────┐                     │
│   │   回退与快照         │    │   独立接口            │                     │
│   │   rollback_all()    │    │   build_chapter_plan│                     │
│   │   snapshot_all()    │    │   push_chapter_to_  │                     │
│   │   rollback_chapter()│    │   storage()         │                     │
│   └─────────────────────┘    └─────────────────────┘                     │
└──────────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Agent 层 (9 个 Agent)                           │
│                                                                         │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────┐│
│  │   架构者      │   │    作者       │   │   监督者      │   │状态管理师 ││
│  │  Architect   │   │   Author     │   │  Supervisor  │   │State Mgr  ││
│  │              │   │              │   │              │   │           ││
│  │ 1A 世界观    │   │ 2A 章节规划  │   │ 3A 一致性    │   │4A 状态更新││
│  │   构建师     │   │    师        │   │    守护者    │   │   师      ││
│  │              │   │              │   │              │   │           ││
│  │ 1B 情节      │   │ 2B 场景写手  │   │ 3B 质量      │   │4B 简报    ││
│  │   设计师     │   │              │   │   审阅者     │   │   生成师  ││
│  │（增量更新）   │   │ 2C 章节润色  │   │ (+事实摘要)  │   │           ││
│  └──────────────┘   └──────────────┘   └──────────────┘   └───────────┘│
└─────────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           存储层                                         │
│                                                                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │   File Store    │  │  SQLite Store   │  │  Chroma Store   │         │
│  │                 │  │                 │  │                 │         │
│  │ Canonical(无ts) │  │ character_state │  │ chapters (vec)  │         │
│  │ world_setting.md│  │ foreshadowing   │  │ settings (vec)  │         │
│  │ plot_structure  │  │ world_state     │  │ characters(vec) │         │
│  │ scene_plan_ch*  │  │ active_conflict │  │                 │         │
│  │ fact_digest_ch* │  │ chapter_meta    │  │                 │         │
│  │                  │  │                 │  │                 │         │
│  │ Timestamped(存档)│  │                 │  │                 │         │
│  │ chapters/scenes  │  │                 │  │                 │         │
│  │ briefs/reviews   │  │                 │  │                 │         │
│  │                  │  │                 │  │                 │         │
│  │ Rollback(.bak)   │  │                 │  │                 │         │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 二、初始化流程 (initialize_novel)

```
 用户
  │  python main.py init <小说名> "故事前提"
  ▼
 Orchestrator.initialize_novel()
  ├── Step 1: 1A 世界观构建师 → settings/world_setting.md (canonical)
  └── Step 2: 1B 情节设计师 → outlines/plot_structure.md (canonical)
 输出: world_setting + plot_structure (含前5章大纲 + 角色档案)
```

---

## 三、章节写作流程 (write_chapter) — 9步循环

```
 用户: python main.py write <小说名>
  │
  ▼
 Step 0: 加载设定 (canonical)
   load_canonical("settings", "world_setting")   ← 优先 _edited.md
   load_canonical("outlines", "plot_structure")   ← 优先 _edited.md
  │
 Step 1: 4B 简报生成师 → briefs/brief_chNNNN_*.md
  │
 Step 2: 2A 章节规划师 → outlines/scene_plan_chNNNN.md (canonical)
   │  输入: 上一章结尾 + 事实摘要 + 禁止清单 + 卷大纲 + 简报
   │  输出: 3-5个场景的详细大纲 + 设定与伏笔汇总
  │
 Step 3: 3A 一致性守护者 (写前审核)
  │
 Step 4-5: 逐场景写+查 (最多5场景，硬限制)
   │  每场景后: 自动 replan 更新剩余规划 + 重新解析场景列表
   │  写手输入: 事实摘要(最高优先级) + 世界观 + 卷大纲 + ChromaDB验证
   │           + 角色档案 + 角色状态 + 前文场景(必读) + 上一场景结尾(强制衔接)
  │
 Step 6: 2C 章节润色师 → chapters/chapter_NNNN_*.md
  │
 Step 7: 3B 质量审阅者 → briefs/review_chNNNN.md
   │  + 提取 fact_digest_chNNNN.md (事实摘要)
  │
 Step 8: 4A 状态更新师 → SQLite (四张状态表)
  │
 Step 9: 设定同步
   ├── 4A extract_new_entities() → 检测新角色/地点/设定/文化
   ├── _merge_world_setting() → world_setting.md (智能合并+角色状态同步)
   ├── _update_plot_structure_marker() → plot_structure.md (章节完成标记+META)
   └── _replan_volume_outline() → 增量更新卷大纲 (锁定已完成，3A审核)
```

---

## 四、事实一致性防御系统 (核心新增)

### 六层防护

```
第1章完成
  │
  ├── [层1] 3B 质量审阅 → fact_digest_chNNNN.md
  │     ├── 确定的物品/角色/事件/数字
  │     ├── 明确未出现的内容 (后续不得引用)
  │     └── 待解悬念
  │
  ├── [层2] 1B 卷大纲增量更新
  │     ├── 已完成章节 → 锁定，只追加
  │     ├── 未完成事件 → 可调整，但细节必须从 fact_digest 提取
  │     └── 3A 审核后才保存
  │
  ├── [层3] 2A 章节规划
  │     ├── 输入: 上一章结尾 + fact_digest + 禁止清单 + 卷大纲
  │     ├── 禁止清单: 解析 fact_digest "明确未出现" → 规划不得引用
  │     └── "如与事实摘要冲突，以事实摘要为准"
  │
  ├── [层4] 2B 场景写手
  │     ├── 最高优先级: fact_digest (前情必须有依据)
  │     ├── ChromaDB 反向验证: 检索前文章节确认 spec 声明
  │     ├── 卷大纲背景 + 前文场景(必读) + 上一场景结尾(强制衔接)
  │     └── 不合格判定: 编造行为特征(口哨/歌声/习惯)→直接不合格
  │
  ├── [层5] 写手上下文顺序
  │     1. fact_digest (最高优先级)
  │     2. 世界观设定
  │     3. 卷大纲背景
  │     4. 角色档案
  │     5. ChromaDB 原文验证
  │     6. ChromaDB 设定检索
  │     7. 上一章审阅报告
  │     8. 角色与世界状态
  │     9. 已完成场景正文 (必读)
  │     10. 上一场景结尾 (强制衔接)
  │
  └── [层6] 存储层 canonical 文件
        world_setting.md / plot_structure.md / scene_plan_chNNNN.md
        每次保存: .bak 备份 → 可回退
```

### Fact Digest 格式

```
## 事实摘要
### 确定的物品
### 确定的角色状态
### 确定的事件
### 确定的数字/数据
### 明确未出现的内容 (后续章节不得引用)
### 待解悬念
```

### 章规划中的设定与伏笔追踪

```
### 场景 N：名称 [状态：已完成]
├── 已完成概括
├── 实际引入设定 (基于正文，非预测)
├── 实际新埋伏笔
└── 实际回收伏笔

### 累计新增设定/实体 (汇总表)
### 累计新埋伏笔
### 累计已回收伏笔
```

---

## 五、回退系统

### 文件级回退
```python
# FileStore
save_canonical()  → 自动 .bak 备份
rollback_canonical() → .bak 恢复为 .md

# Orchestrator
snapshot_all()      → 全体 canonical 文件创建快照
rollback_all()      → 全体回退到上一快照
rollback_chapter(N) → 删除指定章的场景/规划/简报
```

### 独立接口
```python
build_chapter_plan(N)     # 接口A: 已完成场景→重建章节规划(含设定伏笔追踪)
push_chapter_to_storage(N) # 接口B: 章节→设定集/大纲/SQLite/ChromaDB
```

---

## 六、可用命令一览

| 命令 | Agent | 作用 |
|------|-------|------|
| `init` | 1A+1B | 创建新小说项目 |
| `status` | — | 查看进度和状态表 |
| `plan` | 2A | 生成场景级写作计划 |
| `scene` | 2B | 写单个场景 (自动触发 replan) |
| `replan` | 2A | 根据已写内容重新规划剩余场景 |
| `done` | 2C+3B+4A+sync | 章节收尾 (润色+审阅+状态更新+设定回流) |
| `write` | 全流程 | 一步到位完整写一章 |

---

## 七、Canonical 文件约定

| 数据 | 文件名 | 更新频率 | 回退支持 |
|------|--------|---------|---------|
| 世界设定 | `settings/world_setting.md` | 每章完成后合并 | ✅ .bak |
| 情节大纲 | `outlines/plot_structure.md` | 每章完成后增量更新 | ✅ .bak |
| 第N章规划 | `outlines/scene_plan_chNNNN.md` | 每次 replan | ✅ .bak |
| 事实摘要 | `briefs/fact_digest_chNNNN.md` | 每章审阅后 | — |
| 场景正文 | `chapters/scene_chNNNN_sSS_*.md` | 每场景 | 时间戳存档 |
| 章节全文 | `chapters/chapter_NNNN_*.md` | 每章完成后 | 时间戳存档 |
| 审阅报告 | `briefs/review_chNNNN_*.md` | 每章审阅后 | 时间戳存档 |

人工介入: `_edited.md` 优先于 canonical `.md`

---

## 八、模型调用策略

```
  Agent                 模型                thinking   温度    每章调用次数
  ─────────────────────────────────────────────────────────────────────
  1A 世界观构建师       v4-pro             ✓          0.5     全书1次
  1B 情节设计师(增量)   v4-pro             ✓          0.5     每章1次
  2A 章节规划师         v4-flash           ✓          0.5     1次+每场景replan
  2B 场景写手           v4-pro             ✗          0.9     3-5次 ⚡
  2C 章节润色师         v4-pro             ✗          0.6     1次
  3A 一致性守护者       v4-flash           ✗          0.3     N+1+卷大纲审核
  3B 质量审阅者         v4-flash           ✓          0.3     1次 (+事实摘要)
  4A 状态更新师         v4-flash           ✗          0.2     1次 (+新实体提取)
  4B 简报生成师         v4-flash           ✓          0.3     1次

  每章最多5个场景 (硬限制)，更快触发卷大纲更新保证一致性
```

---

## 九、SyncManager — 确定性存储同步引擎（2026-05-23 新增）

**文件**: `src/storage/sync_manager.py`

```
用户编辑 world_setting.md
        │
        ▼
  SyncManager.parse_entities()  → 提取所有命名实体（修炼体系/地域/势力/角色/规则）
  SyncManager.detect_changes()  → 对比新旧 world_setting，识别增/删/改
  SyncManager.scan_affected_files() → 扫描 plot_structure / scene_plan / SQLite / ChromaDB
        │
        ▼
  PropagationPlan → 人工或 Agent 确认 → execute
```

**配套 Agent**: `SettingsEditor` (5号Agent, `src/agents/state_manager/settings_editor.py`)
- CLI: `python main.py setting <小说名> "修改指令"` (dry-run) / `--commit` (写入)
- 修改世界设定后自动检测下游文件影响范围，生成同步报告

## 十、场景规划增强（2026-05-23）

新增三个必填字段：
- **本场景的戏剧功能**：推进主线/揭示关键信息/建立角色关系/制造悬念或威胁/情感收束
- **对话必须达成的信息增量**：不超过2条，必须具体
- **角色微时刻**：不依赖对话的、展现角色内心的动作瞬间

"发生什么"字段从1-2句扩充为3-5句（足够丰满的戏剧骨架）。

## 十一、3A 分层一致性检查（2026-05-23）

输出格式从单一 `FLAG` 改为三级：
- **T1**（硬错误）：境界编号/角色名/时间线矛盾 → 自动修复+复核（最多2轮）
- **T2**（软问题）：对话回环/已知设定复述 → 记录警告，不自动修
- **T3**（观察项）：风格/节奏 → 只标记

## 十二、写手 Prompt 更新（2026-05-23）

- 新增"作者视角 vs 读者视角"区分——禁止在正文中复述已知设定
- 新增"对话推进规则"——每话轮必须有信息增量，禁止回环对话
- 新增"正面陈述优先"——禁止"不是A，是B"句式反复使用（全场景不超过1次）
- 新增"网文风格要求"——10条（节奏/对话/角色/描写）
- Context 拆分为"活跃写作上下文"+"仅供查阅（禁止复述）"

## 十三、kunlun_ruins（2026-05-23）

科幻考古题材《门》。第1章完成，第2章场景1完成。后续改为 ash_walker 项目。

## 十四、新小说项目：《都在捡垃圾，只有我在挖前文明》（2026-05-24）

### 核心设定
- 主角柯林，部落普通青年，无特殊身世
- 双重文明：前文明（200-300年工业史）+ 古文明（几亿年，深埋地下）
- 三轨力量：变异者（刻痕人/辐射觉醒）| 灰铁匠（义体改造）| 溯源计划（主角独有基因药剂）
- 金手指：喝下前文明"溯源计划"基因药剂 → 排斥前文明遗物，兼容古文明仿制品
- 开局直接从部落遇袭后地下第三天开始，不写遇袭过程
- 第一卷：独活→结伴→初建基地

### 已生成文件
- proposal_edited.md（创作提案）
- world_setting_edited.md（手工编辑，系统优先读取）
- plot_structure.md（6卷，仅第一卷14事件+5章展开，后续卷待设计）
- scene_plan_ch0001.md（5场景）
- chapter_0001（4293字，已通过调性编辑）

### 写作风格要求
- 番茄/起点网文，嘴说不是笔写，轻松直接
- 叙事者是轻松旁观者，偶尔插嘴但不说"我"
- 环境描写只给一句话钩子
- 禁止：数步子、数秒数、数列举式流水账
- Few-shot 模板注入 Prompt（用户提供的真实网文正文）

### 技术改动（2026-05-24）
- 新增 ToneEditor Agent（pro模型，调性编辑，写入流水线）
- 整章一次写入模式 write_chapter_full
- 关闭 auto-replan，改为人工触发
- _parse_scene_plan 修复：丢弃章头部前导内容
- cmd_replan 修复：full_texts=True + 可信规划继承 + [不可信]标记
- 1B 优先读取 world_setting_edited.md
- 新增 _extract_chapter_outline 方法
- rollback_chapter 写入 [不可信] 标记
- 两阶段 init：proposal → --confirm

### 明天继续命令
```bash
python main.py plan "都在捡垃圾，只有我在挖前文明" --chapter 2
python main.py write "都在捡垃圾，只有我在挖前文明" --chapter 2
```
