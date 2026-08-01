# E05 Cost & Duplicate Work Closure — 实施报告

日期：2026-08-01
前置：E01 (chapter_index round-trip)、E02 (Writer world_setting)、E03 (分层规划)、E03.1 (new-volume 事务式)、E04 (RAG MVP)、E04.1 (RAG Closure Fix) 已完成。
范围：E05-Core（消除 write/review 主流程中的确定性重复执行）。未实现 E06+。

---

## 1. 修改文件（4 改 1 增）

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/agents/author/claude_stylist.py` | 修改 | `edit_chapter()` 移除内部 `save` + `StyleChecker`，只返回 styled text；移除 `StyleChecker` import |
| `src/core/orchestrator.py` | 修改 | 新增 `_save_and_check_styled()` helper；`write_chapter()` 和 `style_edit()` 统一使用该 helper；`review_chapter()` 改用 `extract_fact_digest_from_analysis()` |
| `src/agents/state_manager/state_manager.py` | 修改 | 新增 `extract_fact_digest_from_analysis()`：从 raw_analysis 确定性提取 Fact Digest（无 LLM）；保留原 `extract_fact_digest()` |
| `src/storage/document_formats.py` | 修改 | `FactDigest.from_markdown()` 修复 `explicitly_absent` round-trip（优先匹配有后缀的 heading，再回退无后缀）；新增 `chapter_index` 从标题恢复 |
| `tests/test_rag.py` | 修改 | `TestIndexFailureWithoutRollback` 测试 mock 更新：LLM 输出包含 `## 事实摘要` 区域 |
| `tests/test_e05.py` | 新增 | 11 个 E05 focused tests |

---

## 2. E05-1 — Styled Chapter Single Ownership

### 修复前

```text
ClaudeStylist.edit_chapter()
    ├─ LLM call
    ├─ FileStore.save("chapters", ..., styled)   ← SAVE #1
    ├─ StyleChecker.check_all()                   ← CHECK #1
    └─ return styled text

Orchestrator.write_chapter()
    ├─ stylist.edit_chapter(...)
    ├─ FileStore.save("chapters", ..., styled)   ← SAVE #2
    └─ StyleChecker.check_all()                  ← CHECK #2

Orchestrator.style_edit()
    ├─ stylist.edit_chapter(...)
    └─ FileStore.save("chapters", ..., styled)   ← SAVE #3 (无 StyleChecker!)
```

style_edit 路径在 E04.1 修改后缺少 StyleChecker 保护（原来依赖 Stylist 内部的 check）。

### 修复后

```text
ClaudeStylist.edit_chapter()
    ├─ LLM call
    └─ return styled text                         ← 只负责转换

Orchestrator._save_and_check_styled(chapter_index, styled)
    ├─ FileStore.save("chapters", ..., styled)   ← SAVE exactly once
    ├─ StyleChecker.check_all()                  ← CHECK exactly once
    └─ print report

Orchestrator.write_chapter()
    ├─ stylist.edit_chapter(...)
    └─ _save_and_check_styled(...)               ← shared helper

Orchestrator.style_edit()
    ├─ stylist.edit_chapter(...)
    └─ _save_and_check_styled(...)               ← same shared helper
```

### 职责原则

| Agent | 职责 |
|---|---|
| `ClaudeStylist.edit_chapter()` | LLM 转换 → 返回 styled text |
| `Orchestrator._save_and_check_styled()` | 保存 + StyleChecker + 终端报告 |

### 效果

- 每章 write：少 1 次 save + 1 次 StyleChecker
- style_edit 路径：补齐缺失的 StyleChecker
- 两条路径共享同一 helper，防止行为漂移

---

## 3. E05-2 — Fact Digest Single LLM Pass

### 修复前

```text
StateManager.review_chapter()            ← LLM call #1
    └─ raw_analysis（已含完整 ## 事实摘要）
...
StateManager.extract_fact_digest()       ← LLM call #2（重复）
```

### 修复后

```text
StateManager.review_chapter()                          ← LLM call #1 (唯一)
    └─ raw_analysis（含 ## 事实摘要）
...
StateManager.extract_fact_digest_from_analysis()       ← 确定性提取（0 LLM）
    ├─ 提取 ## 事实摘要 section
    ├─ FactDigest.from_markdown() 解析六子节
    ├─ 设置 chapter_index
    ├─ FileStore.save() 落盘
    └─ 失败 → [STATE WARNING]（不崩溃、不回滚）


### 效果

- 每章 review：少 1 次整章 LLM 调用
- LLM 调用数从 2 → 1（稳定、可预测）

### Fact Digest 解析失败处理

`extract_fact_digest_from_analysis` 在以下情况输出 `[STATE WARNING]`：

1. raw_analysis 中不存在 `## 事实摘要` 区域
2. 六个子节全部为空

失败时返回 `FactDigest(chapter_index=chapter_index)`，不抛异常。
不因为 Fact Digest 派生失败回滚 canonical state。
RAG indexing 保持当前 Derived State 原则，不受影响。

**本轮为了失败解析不会自动再调用一次 LLM**（原因：E05 目标是让 normal review 的 LLM 调用数量稳定）。

### 附带修复

`FactDigest.from_markdown()` 两处修复：

1. **`explicitly_absent` round-trip**：`to_markdown()` 输出 `### 明确未出现的内容（后续章节不得引用）`，`from_markdown()` 原来只匹配无后缀版本。现在先尝试有后缀版本，再回退无后缀版本。

2. **`chapter_index` 恢复**：`from_markdown()` 原来不解析章号（恒为 0），现在从 `# 第N章 事实摘要` 标题恢复。

---

## 4. E05-3 — LLM Cost Invariant

review_chapter 在正常 StateManager 输出下必须恰好 1 次 LLM 调用。通过 `test_review_chapter_exactly_one_llm_call` 验证真实 `Orchestrator.review_chapter()` 调用链中的 `_call_llm` 计数。

---

## 5. E05-4 — Side Effect Invariants

| 不变条件 | 测试 |
|---|---|
| `write_chapter`: Stylist edit=1, save=1, StyleChecker=1, 1 styled file | `test_write_chapter_single_save` + `test_write_chapter_one_styled_file` |
| `style_edit`: Stylist edit=1, save=1, StyleChecker=1 | `test_style_edit_single_save_and_check` |
| `review_chapter`: StateManager LLM=1, Fact Digest LLM=0, fact_digest=1 file | `test_review_chapter_exactly_one_llm_call` + `test_fact_digest_content_from_raw_analysis` |
| Fact Digest 内容来自 raw_analysis | `test_fact_digest_content_from_raw_analysis`（唯一字符串 `FACT_DIGEST_SINGLE_PASS_5821`） |
| ClaudeStylist.edit_chapter 不保存不检查 | `test_edit_chapter_does_not_save` |

---

## 6. E05-5 — Preserve E04 Ordering

review 流程顺序不变：

```
LLM Review → Tracking Update → deterministic Fact Digest extraction/save → RAG Index
```

Chroma 仍是 Derived State，不在 Fact Digest 之前索引。

---

## 7. LangGraph Migration Readiness

E05 改动不增加未来迁移成本：

1. **Agent Method 保持 Transformation-Oriented**：
   - `ClaudeStylist.edit_chapter(input) → styled_text`
   - `StateManager.extract_fact_digest_from_analysis(analysis, chapter_index) → FactDigest`
   - Agent 内无新增 workflow side effects

2. **Workflow Side Effects 有明确 Ownership**：
   - Orchestrator 负责 save / StyleChecker / FactDigest save / RAG indexing
   - 共享 helper `_save_and_check_styled()` 避免路径漂移

3. **未提前引入 LangGraph**：没有 StateGraph / GraphState / node/edge abstraction

---

## 8. 测试清单

### E05 新增测试（11 个）

| 类别 | 测试 | 覆盖 |
|---|---|---|
| **Write** | `test_write_chapter_single_save` | Stylist edit=1, styled save 由 _save_and_check_styled 执行, StyleChecker=1 |
| | `test_write_chapter_one_styled_file` | 恰好 1 个 styled timestamp 文件 |
| **Style Edit** | `test_style_edit_single_save_and_check` | Stylist edit=1, save=1, StyleChecker=1 |
| **ClaudeStylist** | `test_edit_chapter_does_not_save` | edit_chapter 不调用 FileStore.save |
| **Review** | `test_review_chapter_exactly_one_llm_call` | 真实 review_chapter 链：恰好 1 次 LLM 调用 |
| | `test_fact_digest_content_from_raw_analysis` | 唯一字符串 `FACT_DIGEST_SINGLE_PASS_5821` 出现在保存的 Fact Digest 中 |
| **extract_fact_digest_from_analysis** | `test_successful_extraction` | 正确解析六个子节、返回 FactDigest、保存文件 |
| | `test_missing_section_returns_default` | 缺失 `## 事实摘要` → 返回默认 FactDigest |
| | `test_empty_subsections_returns_fd` | 空子节不崩溃 |
| **FactDigest round-trip** | `test_explicit_absent_roundtrip` | to_markdown → from_markdown explicitly_absent 保留 + chapter_index 恢复 |
| | `test_explicit_absent_from_old_format` | 旧格式（无后缀 heading）兼容解析 |

### 既有测试回归

| 套件 | 测试数 | 状态 |
|---|---|---|
| E01/E02 (`test_chapter_plan.py`) | 9 | ✅ |
| E03 Foundation (`test_planning_foundation.py`) | 11 | ✅ |
| E03 + E03.1 (`test_planning_hierarchy.py`) | 15 | ✅ |
| E04 + E04.1 (`test_rag.py`) | 38 | ✅ (1 个 mock 数据适配 E05) |
| **Total (existing)** | **73** | **✅** |

---

## 9. 全部测试结果

```
Ran 84 tests in 201.5s
OK   (73 existing + 11 E05 = 84/84，零回归)
```

---

## 10. 本轮每章减少的重复工作

| 操作 | 修复前 | 修复后 |
|---|---|---|
| Write：styled save | 2 次 | 1 次 |
| Write：StyleChecker | 2 次 | 1 次 |
| Review：Fact Digest LLM | 2 次 | 1 次 |
| Style Edit：StyleChecker | 0 次（依赖 Stylist 内部） | 1 次（Orchestrator 保证） |

---

## 11. 明确未处理的问题（不在 E05 范围）

- StateManager 状态语义修复（追踪文档现状表更新、物品解析器与 prompt 对齐等 — E06 范围）
- L2/L3 自动检测
- Supervisor Rewrite Loop
- Replanning / Rollback
- LangGraph 集成
- `ClaudeStylist.edit_scene()` 仍保留内部 save（非主流程，E05 明确不碰）

---

## 12. E05 Explicit Non-Goals 验收

| 约束 | 状态 |
|---|---|
| 不添加新 Agent | ✅ |
| 不修改 StateManager 状态语义 | ✅ |
| 不实现 Supervisor Rewrite Loop | ✅ |
| 不实现 Replanning / Rollback | ✅ |
| 不引入 LangGraph | ✅ |
| 不新增 LLM 调用 | ✅（反而减去 1 次/章） |
| Lorewriter 的 RAG indexing 不修改语义 | ✅ |
| Fact Digest 派生失败不回滚正文 | ✅ |
| ClaudeStylist 不新增 side effects | ✅ |
| style_edit 不丢失 StyleChecker | ✅ |

**E05 Cost & Duplicate Work Closure 完成。**
