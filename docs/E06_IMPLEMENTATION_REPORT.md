# E06 Structured Memory & Supervisor Decision Foundation — 实施报告

日期：2026-08-02
前置：E01 (chapter_index round-trip)、E02 (Writer world_setting)、E03 (分层规划)、E03.1 (new-volume 事务式)、E04 (RAG MVP)、E04.1 (RAG Closure Fix)、E05 (Cost Closure) 已完成。
范围：E06 — Structured State Update (State Delta)、ReviewDecision 解析与路由、World Setting 进入 Review。

---

## 1. 修改文件（5 改 1 增）

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/agents/state_manager/state_manager.py` | 修改 | 新增 `world_setting` 入参；新增 `parse_review_decision()`；重写 `update_tracking_docs()` 双维护模式（State Delta + Change Log）；新增 `_parse_state_kv()`、`_apply_state_deltas()`、`_append_change_logs()` |
| `src/config/prompts/state_manager.txt` | 修改 | 新增「状态变更（State Delta）」section；新增「审阅决策」section；明确输出格式 |
| `src/core/orchestrator.py` | 修改 | `review_chapter()` 新增 world_setting 加载 + Decision 三级路由（PASS/NEEDS_REVISION/旧停机状态） |
| `src/storage/document_formats.py` | 修改 | 新增 `ReviewDecision` dataclass + `DecisionVerdict`/`DecisionSeverity` enum；`ItemsEquipment` 拥有者编码在备注字段（向后兼容） |
| `src/storage/sqlite_store.py` | 修改 | 新增 `upsert_foreshadow()` 方法 |
| `tests/test_e06.py` | 新增 | 22 个 E06 focused tests |

---

## 2. E06-1 — World Setting in Review Context

### 变更

StateManager.review_chapter 新增 `world_setting` 参数。Orchestrator 在调用 review_chapter 前加载 `settings/world_setting.md` 并传入。

### Prompt 结构

```
第 N 章正文
---
世界观设定（截断至 2000 字符）
---
章规划（用于对比）
---
当前追踪文档
  character_relationships.md
  items_equipment.md
  cultivation_system.md
---
请按输出格式分析本章。
```

### 效果

T1 一致性检查（硬错误）现在有真实世界观设定作为对照依据，不再是凭空检查。

---

## 3. E06-2 — State Delta → Deterministic Current State Update

### 问题

修复前 `update_tracking_docs` 只追加 Change Log，不更新 Current Structured State（关系表/物品表）。导致追踪文档的"当前状态"表格与实际剧情脱节。

### 方案

**双维护模式**：一次 LLM 输出同时驱动两项更新：

1. **「## 状态变更（State Delta）」** → `_apply_state_deltas()` → 确定性更新 Current State（关系 entries、物品 protagonist_items、修炼 character_states、伏笔 SQLite）
2. **「## 追踪文档变更建议」** → `_append_change_logs()` → 追加 Change Log（历史审计轨迹，保留原有行为）

### State Delta 格式

```
## 状态变更（State Delta）
### 角色关系当前状态
- 角色A ↔ 角色B: 关系类型=XX, 当前状态=XX, 态度=XX [依据: 第X段]

### 角色物品状态
#### 获得
- 物品名: 持有者=XX, 来源=XX, 状态=XX [依据: 第X段]
#### 消耗
- 物品名: 旧持有者=XX [依据: 第X段]
#### 失去
- 物品名: 原因=XX [依据: 第X段]

### 角色修炼状态
- 角色名: 当前境界=XX, 特殊能力=XX, 限制=XX [依据: 第X段]

### 伏笔状态
- 伏笔描述: 状态=OPEN/RESOLVED/ABANDONED, 回收章节=第N章 [依据: 第X段]
```

### 解析

`_parse_state_kv()` 解析 `key=value, key=value` 格式（非 `**key**: value`），支持 `[依据: ...]` 后缀自动剥离。

### 覆盖

| State Component | Current State Update | Change Log Append |
|---|---|---|
| 角色关系 | ✅ entries[] 确定性 upsert | ✅ change_log 追加 |
| 物品装备 | ✅ protagonist_items[] 确定性 upsert | ✅ item_logs 追加 |
| 修炼体系 | ✅ character_states[] 确定性 upsert | ✅ rule_changes 追加 |
| 伏笔 | ✅ SQLite upsert_foreshadow (OPEN→RESOLVED) | — |

---

## 4. E06-3 — ReviewDecision Parsing & Routing

### ReviewDecision Dataclass

```python
@dataclass
class ReviewDecision:
    verdict: str = "UNKNOWN"        # PASS / NEEDS_REVISION / 旧停机状态 / UNKNOWN
    severity: str = "PASS"          # PASS / MINOR / MAJOR
    reasons: list[str]              # 主要问题列表
    t1_issues: list[str]            # T1 硬错误
    t2_issues: list[str]            # T2 软问题
    t3_issues: list[str]            # T3 观察项
    quality_issues: list[str]       # 质量审阅发现
    planning_level: str = "L1"      # L1 / L2 / L3
```

### 解析策略

`ReviewDecision.from_analysis(text)` 确定性解析（0 LLM）：

1. 优先解析 `## 审阅决策` section（E06 新格式）
2. 回退到 `## 一致性检查` + `## 质量审阅` 推断
3. 完全无法解析 → fail-closed (UNKNOWN)

### 路由

```
review_chapter()
  ├─ Step 1: LLM Review (1 次调用，含 world_setting)
  ├─ Step 2: parse_review_decision() (0 LLM，确定性)
  └─ Step 3: Decision 路由
       ├─ PASS           → memory commit + fact digest + RAG index
       ├─ NEEDS_REVISION → [SUPERVISOR] + fact digest save (informational)
       │                    + no memory commit + no RAG
       ├─ 旧停机状态           → [SUPERVISOR 旧停机状态] + fact digest save
       │                    + no memory commit + no RAG
       └─ UNKNOWN        → fail-closed (same as 旧停机状态)
```

### 安全原则

- UNKNOWN (fail-closed) 不会自动 PASS
- 解析失败输出 `[SUPERVISOR WARNING]`，不崩溃
- NEEDS_REVISION / 旧停机状态 不提交 Structured Memory，不索引 RAG

---

## 5. E06-4 — E05 Single-Pass Invariant Preserved

E06 不回归 E05 的 1 次 LLM 核心不变条件：

- `review_chapter` 恰好 1 次 `_call_llm`
- `parse_review_decision` 是确定性解析（正则 + 字符串处理，无 LLM）
- Fact Digest 仍从同一 raw_analysis 确定性提取

---

## 6. E06-5 — Backward Compatible ItemsEquipment Format

### 问题

E06 初版在 `items_equipment.md` 的「主角持有」表中新增了「拥有者」列，改变了既有 markdown 格式。

### 修复

**保持旧列格式不变**：

```
| 物品 | 来源 | 获得章 | 属性 | 状态 | 备注 |
|------|------|--------|------|------|------|
```

**拥有者编码在备注字段中**：

- 书写：`it.owner != "主角"` 时 → 备注 = `拥有者=王长林; {原备注}`
- 解析：`from_markdown()` 从备注中提取 `拥有者=XXX` 前缀，还原 owner

此编码方式对已存在的 items_equipment.md 文件完全向后兼容（无拥有者信息 → owner 默认 "主角"）。

---

## 7. E06-6 — SQLite Foreshadowing Cache

新增 `upsert_foreshadow()` 方法，支持：

- 已有 pending 伏笔 → 更新状态（RESOLVED/ABANDONED）
- 新伏笔 → INSERT
- LIKE 模糊匹配 description，处理 LLM 输出中伏笔描述的微小变化

---

## 8. 测试清单

### E06 新增测试（22 个）

| 类别 | 测试 | 覆盖 |
|---|---|---|
| **A. Item Current State** | `test_item_holder_transfer_updates_current_state` | 物品持有者转移 → protagonist_items 更新 + item_logs 记录 + 拥有者备注往返 |
| **B. Relationship Current State** | `test_relationship_current_state_updated` | 关系状态变更 → entries[] 更新 + change_log 追加 |
| **C. Foreshadowing State** | `test_foreshadow_resolved_updates_sqlite` | 伏笔 OPEN→RESOLVED → SQLite upsert |
| **D. ReviewDecision Parsing** | `test_parse_pass` | PASS 解析（无 T1 错误） |
| | `test_parse_needs_revision` | NEEDS_REVISION 解析（有 T1 + MAJOR） |
| | `test_parse_halt` | 旧停机状态 解析（L3 strategic） |
| | `test_parse_no_explicit_decision_infers_from_t1` | 无显式审阅决策 section → 从 T1 推断 NEEDS_REVISION |
| | `test_parse_empty_returns_unknown` | 空输入 → UNKNOWN (fail-closed) |
| | `test_parse_no_t1_no_decision_section_returns_pass` | 无 T1 + 无显式决策 → 推断 PASS |
| | `test_severity_from_quality_review` | 质量审阅 MAJOR → severity=MAJOR |
| **E. World Setting in Review** | `test_world_setting_in_review_prompt` | world_setting 唯一标记字符串出现在 LLM prompt 中 |
| **F. Decision Routing** | `test_pass_commits_memory_and_rag` | PASS → RAG index 调用 + 关系文档更新 + Fact Digest 生成 |
| | `test_needs_revision_no_memory_commit_no_rag` | NEEDS_REVISION → 无 RAG、无 memory commit |
| | `test_halt_no_rag` | 旧停机状态 → 无 RAG、包含 planning_level |
| **G. E05 Invariant** | `test_review_chapter_exactly_one_llm_call` | 恰好 1 次 LLM 调用 |
| **H. StateManager.parse_review_decision** | `test_parse_decision_from_pass_analysis` | StateManager 入口 → PASS |
| | `test_parse_decision_from_needs_revision_analysis` | StateManager 入口 → NEEDS_REVISION + MAJOR |
| | `test_parse_decision_failure_no_crash` | 解析失败不崩溃，返回 UNKNOWN |
| **I. Prompt/Parser Contract** | `test_state_delta_section_parseable` | State Delta section 可解析 + _parse_state_kv 正常 |
| | `test_decision_section_parseable` | 审阅决策 section 可解析 |
| | `test_fact_digest_section_present` | 事实摘要 section 包含六子节 |
| | `test_fact_digest_from_analysis` | FactDigest.from_markdown 正确解析六子节内容 |

### 既有测试回归

| 套件 | 测试数 | 状态 |
|---|---|---|
| E01/E02 (`test_chapter_plan.py`) | 9 | ✅ |
| E03 Foundation (`test_planning_foundation.py`) | 11 | ✅ |
| E03 + E03.1 (`test_planning_hierarchy.py`) | 15 | ✅ |
| E04 + E04.1 (`test_rag.py`) | 38 | ✅ |
| E05 (`test_e05.py`) | 11 | ✅ |
| **Total (existing)** | **84** | **✅** |

---

## 9. 全部测试结果

```
tests/test_e06.py              22 tests  ✅
tests/test_e05.py              11 tests  ✅
tests/test_chapter_plan.py      9 tests  ✅
tests/test_planning_foundation.py  11 tests  ✅
tests/test_planning_hierarchy.py  15 tests  ✅
tests/test_rag.py              38 tests  ✅
─────────────────────────────────────────
Total                         106 tests  ✅  (0 failures)
```

---

## 10. E06 关键设计决策

### 决策 1：State Delta + Change Log 双维护

- **Current State 由 State Delta 确定性驱动**：表格/entries 直接更新
- **Change Log 继续追加**：保留完整历史审计轨迹
- 两次解析来自同一 raw_analysis 的不同 section，不需要额外 LLM

### 决策 2：Fail-Closed UNKNOWN

- 解析失败默认 UNKNOWN，行为同 旧停机状态（不提交、不索引）
- LLM 输出格式异常不会导致错误章节被标记为 PASS
- `[SUPERVISOR WARNING]` 终端输出确保人类可观测

### 决策 3：拥有者编码在备注字段

- 不改变 markdown 列格式（向后兼容）
- 对已有文件透明：无拥有者 → 默认 "主角"
- `拥有者=XXX;` 前缀编码，分号分隔其他备注内容

### 决策 4：0 额外 LLM

- `parse_review_decision` = 确定性解析（正则 + 字符串）
- `_apply_state_deltas` = 确定性解析（正则 + kv）
- `extract_fact_digest_from_analysis` = 确定性解析（E05 已有）
- 整条 review_chapter 路径：恰好 1 次 LLM 调用

---

## 11. E06 数据流总览

```
Orchestrator.review_chapter(chapter_index)
  │
  ├─ 加载: chapter_text, plan_text, world_setting,
  │        current_relationships, current_items, current_cultivation
  │
  ├─ StateManager.review_chapter(...)         ← LLM #1 (唯一)
  │     └─ raw_analysis (含所有分析 section)
  │
  ├─ StateManager.parse_review_decision()     ← 0 LLM
  │     └─ ReviewDecision {verdict, severity, t1_issues, ...}
  │
  ├─ Routing
  │   ├─ PASS:
  │   │   ├─ update_tracking_docs()
  │   │   │   ├─ _apply_state_deltas()        ← 0 LLM, 更新 Current State
  │   │   │   ├─ _append_change_logs()         ← 0 LLM, 追加 Change Log
  │   │   │   └─ _sync_sqlite()               ← 0 LLM, SQLite 缓存
  │   │   ├─ extract_fact_digest_from_analysis() ← 0 LLM
  │   │   └─ RAG index_chapter()              ← 0 LLM, Derived State
  │   │
  │   ├─ NEEDS_REVISION:
  │   │   ├─ extract_fact_digest_from_analysis() ← informational only
  │   │   ├─ NO memory commit (no tracking doc update)
  │   │   └─ NO RAG index
  │   │
  │   └─ 旧停机状态 / UNKNOWN:
  │       ├─ extract_fact_digest_from_analysis() ← informational only
  │       ├─ NO memory commit
  │       └─ NO RAG index
  │
  └─ return result {decision, t1_issues, reasons, planning_level, change_log}
```

---

## 12. 明确未处理的问题（不在 E06 范围）

- Supervisor Rewrite Loop (NEEDS_REVISION 后的自动重写流程)
- L2/L3 自动检测与 PlanningModificationReport 生成
- Replanning / Rollback
- LangGraph 集成
- 多 Agent 并发
- Chroma 写入原子性（保持 Derived State 原则）

---

## 13. E06 Explicit Non-Goals 验收

| 约束 | 状态 |
|---|---|
| 不添加新 Agent | ✅ |
| 不修改 StateManager 以外的 Agent 职责 | ✅ |
| 不增加 review_chapter LLM 调用数 | ✅（保持 1 次） |
| 不回归 E05 single-pass | ✅ |
| 不改变 markdown 列格式 | ✅（拥有者编码在备注） |
| 不实现自动重写循环 | ✅ |
| 不实现 Replanning / Rollback | ✅ |
| 不引入 LangGraph | ✅ |
| NEEDS_REVISION / 旧停机状态 不提交 memory/RAG | ✅ |
| UNKNOWN fail-closed 不自动 PASS | ✅ |

**E06 Structured Memory & Supervisor Decision Foundation 完成。**
