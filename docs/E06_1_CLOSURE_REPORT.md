# E06.1 Closure — 实施报告

日期：2026-08-02
前置：E06 (Structured Memory & Supervisor Decision Foundation) 已完成。
范围：E06.1 — 5 项 P0/P1 修复 + 扩展。

---

## 1. 修改文件（6 改 1 增）

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/storage/document_formats.py` | 修改 | `ReviewDecision.from_analysis()` 真 fail-closed；新增 `CharacterStateEntry`/`CharacterStateList` dataclass |
| `src/agents/state_manager/state_manager.py` | 修改 | 原子化 commit (LOAD→PARSE ALL→BUILD→COMMIT)；新增 `review_chapter` book_plan/volume_plan/character_states 入参；新增角色当前状态解析 |
| `src/core/orchestrator.py` | 修改 | REVISION/旧停机状态/UNKNOWN 不保存 fact_digest；加载并传入 book_plan/volume_plan/character_states |
| `src/agents/author/chapter_planner.py` | 修改 | Planner 加载 `character_states.md` 进入 Structured Tracking Context |
| `src/config/prompts/state_manager.txt` | 修改 | 新增角色当前状态 format；新增 Book/Volume strategic context 感知指令 |
| `tests/test_e06.py` | 修改 | 新增 19 个 E06.1 测试；更新 3 个 E06 测试适配新 contract |
| `tests/test_e05.py` | 修改 | MOCK 数据增加 `## 审阅决策` section（E06.1 兼容） |
| `tests/test_rag.py` | 修改 | MOCK 数据增加 `## 审阅决策` section（E06.1 兼容） |

---

## 2. P0 #1 — Don't save fact_digest for rejected chapters

### 变更

NEEDS_REVISION、旧停机状态、UNKNOWN 三条路由均不再调用 `extract_fact_digest_from_analysis()`。

```
REVISION / 旧停机状态 / UNKNOWN:
  ❌ NO fact_digest_chNNNN.md
  ✅ raw_analysis 仍在 states/review_ch* （诊断记录）
```

### 效果

- Planner 的 `_recent_fact_digests()` / `_load_recent_fact_digests()` 不会读取到 rejected chapter 的事实
- 避免未修复的章节事实污染后续 Planner 上下文

### 测试

| 测试 | 覆盖 |
|---|---|
| `test_needs_revision_no_fact_digest_file` | NEEDS_REVISION → fact_digest_ch0002 不存在 |
| `test_halt_no_fact_digest_file` | 旧停机状态 → fact_digest_ch0005 不存在 |
| `test_rejected_chapter_not_in_recent_fact_digests` | `_recent_fact_digests()` 不包含 rejected 内容 |

---

## 3. P0 #2 — True Fail-Closed Decision Contract

### 变更

`ReviewDecision.from_analysis()` 新规则：

| 场景 | 旧行为 | E06.1 行为 |
|---|---|---|
| 无 `## 审阅决策` + clean | 推断 PASS | **UNKNOWN** |
| 无 `## 审阅决策` + T1 | 推断 NEEDS_REVISION | **UNKNOWN** |
| 有 `## 审阅决策` + 无效值 | UNKNOWN | **UNKNOWN** |
| 有 `## 审阅决策` + 显式 PASS | PASS | **PASS** |
| 显式 PASS + parser 发现 T1 | PASS | **NEEDS_REVISION** (safety override) |
| 显式 PASS + quality MAJOR | PASS | **NEEDS_REVISION** (safety override) |

### 核心原则

1. 缺少合法明确的 `## 审阅决策` → **UNKNOWN**，不推断 PASS
2. Safety override: LLM 声明 PASS 但 parser 同时发现 T1/MAJOR → 提升到 NEEDS_REVISION
3. 禁止从「缺失 decision」推导 PASS

### 测试

| 测试 | 覆盖 |
|---|---|
| `test_no_decision_clean_analysis_returns_unknown` | clean 但无决策 → UNKNOWN |
| `test_no_decision_with_t1_returns_unknown` | 有 T1 但无决策 → UNKNOWN（不推断 NEEDS_REVISION） |
| `test_invalid_decision_value_returns_unknown` | 决策值不可解析 → UNKNOWN |
| `test_explicit_pass_valid_section_returns_pass` | 显式合法 PASS → PASS |
| `test_explicit_pass_with_t1_promotes_to_needs_revision` | Safety override: PASS+T1 → NEEDS_REVISION |
| `test_explicit_pass_with_major_quality_promotes` | Safety override: PASS+MAJOR → NEEDS_REVISION |

---

## 4. P0/P1 #3 — Atomic Structured Memory Commit

### 变更

重写 `update_tracking_docs()` 为 5 阶段原子化流程：

```
Phase 1: LOAD     — 加载所有 canonical tracking docs
Phase 2: PARSE    — 解析所有 State Delta + Change Log (in memory)
Phase 3: BUILD    — 在内存中应用所有变更
Phase 4: COMMIT   — 全部解析成功后，一次性保存所有 canonical docs
Phase 5: SQLite   — 缓存同步（独立于 canonical，错误记录不静默）
```

### 原子性保证

- 如果 Phase 2 中任何 delta 解析失败 → 不进入 Phase 4，**零文件修改**
- 如果 Phase 4 中某文件保存失败 → `[STATE WARNING]` 记录失败组件，**不静默**
- SQLite 异常不再 `except: pass`，而是输出具体错误信息

### 测试

| 测试 | 覆盖 |
|---|---|
| `test_parse_error_in_delta_preserves_state` | 物品 delta 格式错误 → 跳过提交，关系未被部分更新 |
| `test_double_save_failure_reported` | 第二个 save 失败 → 第一个仍然保存，失败被报告 |

---

## 5. #4 — Character Current State

### 新增文件

`tracking/character_states.md` — Authoritative Character Current State

### 最小表达

| 字段 | 说明 |
|---|---|
| name | 角色名 |
| alive_status | 存活/死亡/失踪/未知 |
| location | 当前位置 |
| physical_state | 身体状态 |
| identity_status | 关键身份/状态 |
| updated_chapter | 最后更新章节 |

### 数据流

```
StateManager Prompt → LLM 输出 State Delta
  ### 角色当前状态
  - 柯林: 存活=存活, 位置=高架桥, 身体状态=轻伤, 身份=觉醒者 [依据: 第5段]
→ _parse_state_deltas() 确定性解析
→ CharacterStateList 写回 tracking/character_states.md
→ ChapterPlanner 读取 → Structured Tracking Context
```

### 测试

| 测试 | 覆盖 |
|---|---|
| `test_single_character_roundtrip` | to_markdown → from_markdown 往返 |
| `test_from_empty_markdown` | 空文档解析不崩溃 |
| `test_location_update` | 位置更新 |
| `test_alive_status_update` | 存活→死亡 |
| `test_character_state_delta_parsed` | State Delta 解析 → 写回文件 |
| `test_planner_context_contains_character_state` | Planner prompt 包含 character_states |

---

## 6. #5 — Review Strategic Context for L2/L3 Detection

### 变更

StateManager `review_chapter` prompt 现在包含：

```
全书战略规划 Book Plan (Strategic Context — L2/L3 检测)
  ...
当前卷规划 Volume Plan (Tactical Context — L2/L3 检测)
  ...
世界观设定 (一致性检查)
  ...
章规划 (对比)
  ...
当前追踪文档 (character_relationships / items / cultivation / character_states)
  ...
```

### Prompt 新增指令

- **L1**：问题仅影响当前章节（局部文笔、场景执行）
- **L2**：问题影响当前卷的战术事件链
- **L3**：问题违反 Book Plan 的战略约束
- **旧停机状态**：当发现 L3 问题时必须 旧停机状态

### 测试

| 测试 | 覆盖 |
|---|---|
| `test_book_plan_marker_in_prompt` | `E06_BOOK_STRATEGIC_RULE_9137` 出现在 prompt 中 |
| `test_volume_plan_marker_in_prompt` | `E06_VOLUME_RULE_4281` 出现在 prompt 中 |

---

## 7. 测试清单

### E06.1 新增测试（19 个）

| 类别 | 测试数 |
|---|---|
| P0 #1 — Rejected chapter no fact_digest | 3 |
| P0 #2 — Fail-closed decision contract | 6 |
| P0 #3 — Atomic commit | 2 |
| #4 — Character current state | 6 |
| #5 — Strategic context | 2 |

### E06 适配测试（3 个）

| 测试 | 适配 |
|---|---|
| `test_parse_no_explicit_decision_infers_from_t1` | 期望改为 UNKNOWN |
| `test_parse_no_t1_no_decision_section_returns_pass` | 期望改为 UNKNOWN |
| `test_severity_from_quality_review` | 期望改为 UNKNOWN |

### 既有测试回归

| 套件 | 测试数 | 状态 |
|---|---|---|
| `test_e06.py` (E06 + E06.1) | 41 | ✅ |
| `test_e05.py` (E05) | 11 | ✅ |
| `test_chapter_plan.py` (E01/E02) | 9 | ✅ |
| `test_planning_foundation.py` (E03) | 11 | ✅ |
| `test_planning_hierarchy.py` (E03.1) | 15 | ✅ |
| `test_rag.py` (E04/E04.1) | 38 | ✅ |
| **Total** | **125** | **✅ 0 failures** |

---

## 8. E06.1 Explicit Non-Goals 验收

| 约束 | 状态 |
|---|---|
| 不实现 Rewrite Loop | ✅ |
| 不实现 L2 approval workflow | ✅ |
| 不实现 automatic PlanRevision | ✅ |
| 不实现 L3 strategic repair | ✅ |
| 不实现 Rollback | ✅ |
| 不实现 Branch switching | ✅ |
| 不实现 RAG optimization | ✅ |
| 不引入 LangGraph | ✅ |
| 不新增 LLM 调用 | ✅ |
| 不新增 Agent | ✅ |
| 不设计 Knowledge Graph | ✅ |
| 不修改 markdown 列格式 | ✅ |

---

## 9. 与 E06 Report 的关系

E06 原 report 中标注的未实现项在此轮解决了：

| E06 标注 | E06.1 状态 |
|---|---|
| "L2/L3 自动检测未实现" | ✅ 实现：Book/Volume Plan 进入 StateManager prompt；L1/L2/L3 定义在 prompt 和 code 中 |
| "NEEDS_REVISION/旧停机状态 也保存 fact_digest（informational）" | ✅ 修复：不再保存 |
| "ReviewDecision 会从无决策 section 推断 PASS" | ✅ 修复：真 fail-closed |
| "StateDelta 增量保存" | ✅ 修复：原子化 commit |
| "缺少 Character Current State tracking" | ✅ 实现：tracking/character_states.md |

**E06.1 Closure 完成。**
