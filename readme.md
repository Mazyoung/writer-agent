| 谁产出        | 产出什么    | 谁消费             |
| ------------- | ----------- | ------------------ |
| 1A/1B + _sync | 设定 + 大纲 | 4B→2A→2B（全链路） |
| 4B            | 简报        | 2A, 3A, 人工       |
| 2A            | 场景规划    | 2B, 3A             |
| 2B            | 场景正文    | 2C, 3A, replan     |
| 2C            | 完整章节    | 3B, 4A, _sync      |
| 4A            | SQLite 状态 | 4B, 2B, _sync      |
| 3B            | 审阅        | 写手 (下章), 人工  |



章节完成后

plaintext

```
│
├── _sync_settings()
│   ├── 新角色/地点/组织/设定 → world_setting.md（智能合并到对应章节）
│   ├── 角色状态变更 → world_setting.md（角色档案节 [第N章: ...]标记）
│   └── 章节完成标记 → plot_structure.md
│
└── StateUpdater (4A)
    ├── 角色状态 → SQLite character_state 表
    ├── 新伏笔 → SQLite foreshadowing 表（status='pending'）
    ├── 伏笔回收 → SQLite（status='resolved'）
    ├── 世界变化 → SQLite world_state 表
    └── 冲突线 → SQLite active_conflicts 表
```

## 二、取回层（下一章 Step 0→1→2）

write_chapter(N+1)

plaintext

```
│
Step 0: load_canonical("world_setting") ─────────────┐
        load_canonical("plot_structure") ───────────┐ │
                                                    │ │
Step 1: 4B BriefGenerator                           │ │
        ├── 输入: world_setting[:3000] ←────────────┼─┘
        ├── 输入: plot_structure[:3000] ←───────────┘
        └── 输入: SQLite.export_all_states() ←── state.db
            ├── characters (当前所有角色状态)
            ├── pending_foreshadows (所有未回收伏笔)
            ├── world_state (世界变化历史)
            └── active_conflicts (活跃冲突线)
                  │
                  ▼
        └── 输出: brief →包含出场角色+待推进伏笔+需查阅设定
                  │
                  ▼
Step 2: 2A ChapterPlanner
        ├── 输入: brief（来自4B，已包含设定摘要+伏笔提示）
        └── 输入: world_setting[:2000]（直接传入，兜底）
                  │
                  ▼
        └── 输出: scene_plan →每个场景标注需查阅的设定
```

## 三、逐层保证

表格

|  层  |                 设定变更                 |                   伏笔                   |
| :--: | :--------------------------------------: | :--------------------------------------: |
| 存储 |        world_setting.md 合并写入         |         SQLite foreshadowing 表          |
| 取回 |     Step 0 load_canonical 直接读文件     |  export_all_states 查 status='pending'   |
| 传递 |       4B 简报 →2A 规划 两层都收到        |      4B 简报明确列出 "需推进的伏笔"      |
| 执行 | 2B 写手 context 含 world_setting [:2500] | 2B 写手 context 含 export_states_summary |