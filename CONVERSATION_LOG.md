# 对话记录 — 小说写作 Agent 项目

## 2026-05-20 完整开发记录

### 阶段一：方案设计
- 确定多 Agent 框架：9个Agent分4个模块（架构者/作者/监督者/状态管理师）
- LLM 选型：DeepSeek V4 Pro + V4 Flash
- 存储方案：SQLite（状态表）+ Markdown（文档）+ ChromaDB（向量检索）
- 详见：DESIGN_DISCUSSION.md

### 阶段二：代码框架搭建
- 项目结构：src/core, src/agents, src/storage, src/config
- 26个Python文件 + 9个Prompt模板
- CLI入口：main.py（init/write/status/plan/scene/polish/check/review/state/brief/replan/done）

### 阶段三：核战废土小说创作实战
- 创建小说：nuclear_cultivation（核战废土+修炼+经济系统+时间闭环）
- 解决的关键问题：双重文件保存、场景与设定无关、场景规划跳过触发条件、场景写飞、场景2+重新开局、replan跟不上实际内容、终端编码崩溃

### 阶段四：流程控制闭环
- 新增 `replan` / `done` / `is_chapter_complete()` 
- 固化工作流：plan → scene → replan → scene → ... → done

---

## 2026-05-21/22 工作流深度优化

### 阶段五：全局内容一致性与设定回流

**核心问题**：写手创作的新角色/设定/伏笔无法传递到后续章节规划和大纲中，导致前后矛盾。

**实施的解决方案**：

#### 1. Canonical 文件系统
- `save_canonical()` / `load_canonical()` — 固定文件名无时间戳
- `world_setting.md` / `plot_structure.md` / `scene_plan_chNNNN.md` — 唯一权威版本
- 自动 `.bak` 备份 + `rollback_canonical()` 回退
- `migrate_to_canonical()` — 从旧时间戳文件自动迁移
- `snapshot_all()` / `rollback_all()` — 编排器级快照回退

#### 2. 事实一致性六层防御
```
层1: 3B 审阅 → fact_digest_chNNNN.md (确定物品/角色/事件/未出现/悬念)
层2: 1B 卷大纲增量更新 (锁定已完成，只调未完成，3A审核)
层3: 2A 章节规划 (上一章结尾 + fact_digest + 禁止清单)
层4: 2B 写手约束 (fact_digest最高优先级 + 不合格判定)
层5: 写手上下文顺序 (10项，fact_digest置顶)
层6: Canonical回退 (每次保存.bak，可安全回退)
```

#### 3. 写手上下文重构
- 最高优先级：前文章节事实摘要 (防止凭空引用)
- 卷大纲背景 (章节在故事弧中的位置)
- ChromaDB 语义检索 + 反向原文验证
- 上一章结尾传入规划层 (防止重复已发生事件)
- 前文锁定为永久规则 (禁止修改)

#### 4. 卷大纲增量更新 (1B改造)
- 锁定已完成章节 → 不可修改
- 未完成事件可调整，但新增细节必须以 fact_digest 为依据
- 3A 审核 1B 输出后才保存 (`_audit_volume_outline`)
- 1B 从"创作者"降级为"整理者+规划者"

#### 5. 章节规划增强
- 输入新增：上一章结尾 + fact_digest + 禁止清单
- 已完成场景保留完整描述 + 三个实际字段 (引入设定/新埋伏笔/回收伏笔)
- 追踪节改为汇总格式
- 5场景硬上限 (快速触发卷大纲更新)

#### 6. 写手系统提示词改革
- "指令锁定"为最高优先级
- "前文锁定"为永久规则
- 不得给角色添加事实摘要中未记录的行为特征
- 每场景后自动 replan (write_chapter + cmd_scene)
- 禁止繁体字

#### 7. 回退与独立接口
- `rollback_chapter(N)` — 一键清理章节所有文件
- `build_chapter_plan(N)` — 已完成场景→重建章节规划
- `push_chapter_to_storage(N)` — 章节→设定集/大纲/SQLite/ChromaDB
- `_get_prev_chapter_end(N)` — 获取上一章结尾用于新章起点

#### 8. 数据流完整性
- 设定变更 → world_setting.md (智能合并+角色状态同步)
- 伏笔 → SQLite (跨章保留，pending自动出现在后续简报)
- META → plot_structure.md (章节标记含新增角色/伏笔/关键事件)
- 所有Agent均从 canonical 文件 + SQLite 获取最新状态

#### 9. 实战验证
- nuclear_cultivation 完整重建 (旧数据被污染，全面清理重来)
- 第1章场景1-2：清洁无污染，fact_digest + 禁止清单 + prev_chapter_end 三层防护生效
- 发现并修复：规划层编造口哨/摇篮曲/密钥分段/纪铭远死亡等系统性bug

### 已知可用命令
```
source venv/Scripts/activate
python main.py status nuclear_cultivation      # 查看进度
python main.py plan nuclear_cultivation --chapter N  # 生成章节规划
python main.py scene nuclear_cultivation --chapter N --scene S  # 写单个场景(自动replan)
python main.py replan nuclear_cultivation --chapter N  # 手动重新规划
python main.py done nuclear_cultivation --chapter N  # 章节收尾(润色+审阅+状态+设定回流)
python main.py write nuclear_cultivation --chapter N  # 一步到位完整写一章
python main.py init <小说名> "故事前提"  # 创建新小说
```

### 当前项目状态
- 小说：nuclear_cultivation (第1章场景1-2已完成，场景3-5待写)
- 场景1：锈骨荒原→阵列触发→207坐标植入 (~5000字)
- 场景2：回营地→罗凡→弦迹尘缓冲→告别→猎手追踪 (~3500字)
- 核心系统：全部6层防御 + 回退 + canonical + 独立接口已就绪
- 架构文档：ARCHITECTURE.md (已更新至2026-05-22)

---

## 2026-05-23 系统深度优化与第二小说项目

### 阶段六：跨章节一致性危机与修复

**核心问题**：第2章场景出现多处与第1章不一致——
1. 境界编号错误（"六境烬痕者"不存在，设定共五境）
2. 时间线编造（"三天前"与第1章同日连续事件矛盾）
3. 对话逻辑回环（影子问"谁杀的"→纪年反问"是不是你们干的"）
4. 写手机械复述已知设定（铅制项圈、烬痕反复解释）
5. 场景规划太薄，写手只能逐条翻译动作清单

**根因分析**：
- 场景规划仅1-2句"发生什么"，无法支撑写手创作
- 写手 context 中事实摘要被当作"最高优先级"→写手理解成"在正文中复述"
- 3A 检查未验证境界编号/时间线/对话信息增量
- 无自动修复循环——标记 FLAG 后只重写不复核

**实施的修复**：

#### 1. 场景规划增强
- 新增三个必填字段：戏剧功能/信息增量/角色微时刻
- "发生什么"从1-2句改为3-5句
- replan prompt 同步更新
- 文件：`chapter_planner.txt`, `chapter_planner.py`

#### 2. 写手 Prompt 重构
- "作者视角 vs 读者视角"区分规则
- "对话推进规则"（信息增量/禁止回环/禁止复述）
- "正面陈述优先"——禁止"不是A，是B"句式（全场景不超过1次）
- "网文风格要求"10条（节奏/对话/角色/描写）
- Context 拆为"活跃写作上下文"+"仅供查阅·禁止复述"
- 文件：`scene_writer.txt`, `scene_writer.py`

#### 3. 3A 分层一致性检查
- T1（硬错误）：境界编号/角色名/时间线矛盾 → 自动修复+复核（最多2轮）
- T2（软问题）：对话回环/已知设定复述 → 记录不修
- T3（观察项）：风格/节奏 → 只标记
- check_scene 新增 world_setting + fact_context 参数
- 文件：`consistency_guard.txt`, `consistency_guard.py`, `orchestrator.py`

#### 4. 事实摘要正则修复
- `_save_fact_digest` 标题匹配过窄（只匹配`##`），导致3B输出`# 事实摘要`时匹配失败
- 修复：检测标题实际层级，止步于同级或更高级标题
- 文件：`orchestrator.py`

#### 5. 已回收伏笔归档机制
- 1B prompt 新增规则：日常性已回收伏笔→"已归档伏笔"节（一行摘要），保留级伏笔→"累计已回收伏笔"表
- 文件：`orchestrator.py`

### 阶段七：SyncManager + SettingsEditor Agent

**SyncManager** (`src/storage/sync_manager.py`)：
- 实体解析：从 world_setting.md 提取所有命名实体
- 变更检测：对比新旧 world_setting，识别增/删/改
- 引用扫描：scan plot_structure / scene_plan / SQLite / ChromaDB
- 同步计划生成与执行

**SettingsEditor Agent** (`src/agents/state_manager/settings_editor.py`)：
- 接收自然语言修改请求
- 生成新 world_setting.md + 受影响文件清单
- CLI: `python main.py setting <小说名> "修改指令"` (dry-run) / `--commit`

### 阶段八：第二小说项目 kunlun_ruins（《门》）

测试跨章节一致性修复效果的新项目：
- 科幻考古题材，现代背景+精确时间戳
- 科学解释体系（L0-L5信息场分级），无修炼元素
- 七人考察队，72小时倒计时
- 已虚化所有真实地名/机构名（审查安全）
- 第1章完成（5场景/23079字），第2章场景1完成
- 简介与引子已写入 `data/novels/kunlun_ruins/简介与引子.md`

### 阶段八附：写手句式多样性规则（2026-05-23）

kunlun_ruins 场景2-1发现9处"不是…是…"句式（69行），用户指出此模式严重影响阅读体验。进一步发现写手也会使用反向变体"是B，不是A"。新增全面句式多样性规则：

- **否定衬托**："不是A，是B" / "是B，不是A" 全场景合计≤1次
- **比喻泛滥**：相邻三段不出现两个"像"字比喻
- **主谓宾连续排布**：同结构连三次则第三句必须变形
- **"在…的时候/同时/瞬间"开头**：每段最多1次
- **段尾泄力**：禁"无论如何""至少现在""也许吧"等自我消解短语
- **句式回看原则**：同一句式骨架500字内不重复
- **核心原则**：最好的句子是读者注意不到句子本身的句子

文件：`scene_writer.txt`

### 当前项目状态
- 小说1：nuclear_cultivation — 第1章完成，第2章场景1-2完成（已暂停）
- 小说2：kunlun_ruins — 第1章完成，第2章场景1完成，简介与引子已写
- 核心系统：SyncManager + 3A分层检查(T1/T2/T3) + T1自动修复+复核 + 写手Prompt全面重构 + 规划三字段增强 + fact_digest正则修复 + 句式多样性规则

---

> 下次继续时，读取 ARCHITECTURE.md + CONVERSATION_LOG.md 即可恢复完整上下文。
> 继续命令：
>   nuclear_cultivation: `python main.py scene nuclear_cultivation --chapter 2 --scene 3`
>   kunlun_ruins: `python main.py scene kunlun_ruins --chapter 2 --scene 2`

---

## 2026-05-24 系统深度重构与第三小说项目启动

### 阶段九：写手文风优化（十几次Prompt迭代）

**核心发现**：DeepSeek V4 默认输出偏"写"不偏"说"。Prompt 规则只能影响不能根本改变其写作DNA。最有效的干预是 Few-shot 示例注入——在 Prompt 中直接放一段目标风格的完整正文作为模板。

**迭代过程**：
1. 抽象规则（"轻松""幽默""口语化"）→ 无效
2. 角色扮演（"废土老兵讲述者"）→ 微小改善
3. 写作速查卡（对照表）→ 中等改善
4. 用嘴说不用笔写 → 改善但不够
5. 用户提供真实网文正文作为模板 → 输出质量出现实质提升

**最终方案**：在 scene_writer.txt 中嵌入真实网文段落作为风格参考

### 阶段十：第三小说项目《都在捡垃圾，只有我在挖前文明》

核废土+遗迹探索+双重文明题材。主角柯林，金手指为"溯源计划"基因药剂。

**项目状态**：第1章完成（4293字），第2章待规划。

**核心设定**见 ARCHITECTURE.md 第十四节。

### 阶段十一：系统架构重大修改

1. **新增 ToneEditor Agent**：pro模型，负责调性编辑（调整叙事语气、注入叙事声音、检查规划对齐）
2. **流水线变更**：SceneWriter → ToneEditor → 保存。调性编辑在场景快照之前执行
3. **整章写入模式**：`write_chapter_full` 一次LLM调用写完整章，避免场景边界越界
4. **关闭 auto-replan**：改为人工触发，防止脏数据传播
5. **新增 `_extract_chapter_outline`**：从 plot_structure 提取章级大纲
6. **replan 信任继承**：可信规划继承+更新，不可信（`[不可信]`标记）则空白重建
7. **`rollback_chapter`**：删除后自动写入 `[不可信]` 标记文件
8. **两阶段 init**：Phase1 生成提案 → Phase2 `--confirm` 生成大纲
9. **1B 优先读取 `world_setting_edited.md`**：保证人工编辑版被情节设计师使用
10. **`_parse_scene_plan` 修复**：丢弃章头部前导内容，只返回场景块

### Bug修复清单
- `_parse_scene_plan` 将章头部当作"场景0"→ 修复为丢弃前导行
- auto-replan 用 `full_texts=False` → 改为 `True`
- cmd_replan 用 `full_texts=False` → 改为 `True`
- cmd_replan 加载脏 original_plan → 改为干净 dummy_plan + 信任继承
- 1B 传入1A自动生成的 world_setting 而非人工编辑版 → 改为优先 _edited.md
- 写手越界写其他场景内容 → 整章写入模式解决
- 写手数步子（第一步第二步）→ Prompt 明确禁止

### 明天继续命令
```bash
cd D:\agent\writer
venv\Scripts\activate
python main.py plan "都在捡垃圾，只有我在挖前文明" --chapter 2
python main.py write "都在捡垃圾，只有我在挖前文明" --chapter 2
```

### 环境
- Python: `D:\agent\writer\venv\Scripts\python.exe`
- 激活: `D:\agent\writer\venv\Scripts\Activate.ps1`（PowerShell）或 `source venv/Scripts/activate`（Bash）
- 模型: DeepSeek V4 Pro（写手+调性编辑+设计师）/ V4 Flash（规划师+简报等）
- 关键文件都在 `data/novels/都在捡垃圾，只有我在挖前文明/` 下
- Prompt: `src/config/prompts/*.txt`
