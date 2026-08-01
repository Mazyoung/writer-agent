# E04 RAG MVP / Historical Evidence Retrieval — 实施报告

日期：2026-08-01
前置：E01（chapter_index round-trip）、E02（Writer world_setting 注入）、E03（分层规划）、E03.1（事务式 new-volume）已完成。
范围：E04-Core（RAG MVP）。未实现 E05+（BM25、Hybrid Search、Reranker、Multi-query、Query Rewrite、HyDE、GraphRAG、Knowledge Graph、Agentic RAG 等）。

---

## 0. 实施前代码事实确认

按照 E04 规格要求，实施前逐一确认了：

| 事实点 | 确认结果 |
|---|---|
| ChromaStore 当前实现程度 | `add_chapter`/`add_setting`/`add_character_profile` 均为单文档模型，无 chunk 支持；`search_chapters`/`search_settings` 仅按 novel_id 过滤，无分支/未来泄漏防护 |
| Orchestrator 初始化 Chroma 位置 | `orchestrator.py:32` — `__init__` 中急切创建 `self.chroma = ChromaStore(...)` |
| ChapterPlanner prompt 构造 | `chapter_planner.py:77-128` — 按优先级组装：World Setting → Book Plan → Volume Plan → Tracking Docs → Fact Digests → Prev Chapter End → 任务层 |
| Chapter finalized/styled 文件路径 | `chapters/chapter_NNNN_styled_TIMESTAMP.md`（`FileStore.load_latest` 按前缀匹配） |
| review/StateManager/Fact Digest 调用链 | `Orchestrator.review_chapter()` → `StateManager.review_chapter()` → `StateManager.update_tracking_docs()` → `StateManager.extract_fact_digest()` |
| novel_id 传递方式 | Orchestrator 构造参数 → 各 Agent/Store 构造参数 |
| branch_id 当前状态 | `StoryBranch` 数据模型存在（planning/models.py），但运行时路径始终无分支切换；按 E04 规格默认 `"main"` |
| chapter_index 数据类型 | 全链路 `int` |
| CLI 注册方式 | `argparse` subparsers，handler → `cmds` dict |
| 配置系统 | `src/config/settings.py` — `Settings` dataclass + `get_settings()` 全局单例 |
| 测试 mock 方式 | `unittest.mock.patch.object(BaseAgent, "_call_llm", ...)` + tempdir 重定向 `settings.data_dir` |

---

## 1. 修改文件（4 改 1 增）

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/storage/chroma_store.py` | 重写 | 完整重写：chunk-based 索引、lazy client、deterministic chunk ID、stale removal、filtered retrieval、RetrievalTrace |
| `src/core/orchestrator.py` | 修改 | Lazy Chroma property、`_index_chapter_to_rag()`、`_retrieve_evidence()`、`_build_retrieval_query()`、`_save_retrieval_trace()`、`rag_index_backfill()`、`plan_chapter` 增加 RAG 检索步、`review_chapter` 末尾增加索引 hook |
| `src/agents/author/chapter_planner.py` | 修改 | `plan_chapter()` 接受 `rag_evidence` 参数，prompt 中注入 `【历史检索证据（RAG）】` 区域 |
| `main.py` | 修改 | 新增 `rag-index` 命令 + `cmd_rag_index` handler |
| `tests/test_rag.py` | 新增 | 31 个 E04 focused tests（单元 + 集成 + 系统） |

---

## 2. 核心数据流

### 写入流（索引）

```text
Orchestrator.review_chapter(chapter_index)
  ├─ StateManager.review_chapter()       # LLM 分析
  ├─ StateManager.update_tracking_docs() # 追踪文档更新
  ├─ StateManager.extract_fact_digest()  # 事实摘要 (LLM)
  └─ [E04 NEW] _index_chapter_to_rag(chapter_index)
       ├─ load_latest("chapters", "chapter_NNNN_styled")
       ├─ chroma.index_chapter(novel_id, branch_id="main", chapter_index, content)
       │    ├─ 删除该章节已有 chunks (stale removal)
       │    ├─ chunk_text(content, CHUNK_SIZE=800, CHUNK_OVERLAP=100)
       │    └─ coll.add(ids, documents, metadatas)
       └─ 异常 → print("[RAG WARNING]") → 不回滚章节状态
```

### 读取流（检索）

```text
Orchestrator.plan_chapter(chapter_index, outline, instructions)
  ├─ [E04 NEW] _retrieve_evidence(chapter_index, outline, instructions)
  │    ├─ _build_retrieval_query()       # deterministic: volume events + outline + characters + items
  │    ├─ chroma.search(novel_id, branch_id, query, chapter_index, top_k=5)
  │    │    └─ where: { novel_id, branch_id, chapter_index < current, source_type="chapter" }
  │    ├─ 异常 → print("[RAG WARNING]") → trace.success=False → 继续
  │    └─ 返回 (formatted_evidence_text, RetrievalTrace)
  ├─ ChapterPlanner.plan_chapter(..., rag_evidence=evidence)
  │    └─ prompt 中插入 "## 【历史检索证据（RAG）】" 区域
  └─ _save_retrieval_trace(trace) → tracking/rag_traces/retrieval_trace_chNNNN_ts.json
```

### Backfill CLI

```text
python main.py rag-index <novel_id>           # 普通模式：补齐未索引章节
python main.py rag-index <novel_id> --rebuild  # 清空分支索引后全部重建

→ Orchestrator.rag_index_backfill(rebuild)
   ├─ rebuild=True → chroma.rebuild_branch(novel_id, branch_id)
   ├─ 扫描 chapters/chapter_*_styled_*.md → 取每章最新时间戳
   └─ 逐章 chroma.index_chapter(...)
```

---

## 3. E04 P0 不变量验收

| # | 不变量 | 实现方式 | 测试 | 状态 |
|---|---|---|---|---|
| 1 | Lazy Chroma | `Orchestrator._chroma = None`，`chroma` property 首次访问创建；`ChromaStore.__init__` 不创建 client | `test_constructor_no_client` / `test_orchestrator_constructor_no_chroma_init` | ✅ |
| 2 | Corpus | `_index_chapter_to_rag` 只加载 `chapter_NNNN_styled`；`rag_index_backfill` 只扫描 `*_styled_*.md` | `test_draft_not_indexed` | ✅ |
| 3 | Stable Chunk ID | `make_chunk_id(novel_id, branch_id, chapter_index, chunk_index)` — 纯确定性拼接，无 UUID | `test_no_uuid_pattern` / `test_contains_all_components` | ✅ |
| 4 | Stale Chunk Removal | `index_chapter` 先 `coll.get(where)` → `coll.delete(ids)` 再 `coll.add` | `test_shorter_content_fewer_chunks` | ✅ |
| 5 | Metadata | `novel_id`, `branch_id`, `chapter_index`(int), `chunk_index`, `source_type`, `source_path` | `test_all_metadata_fields_present` | ✅ |
| 6 | Future Leakage | `search` where: `chapter_index < current_chapter` | `test_planning_ch5_cannot_see_ch5_or_later` / `test_future_chapters_not_leaked` | ✅ |
| 7 | Isolation | where: `novel_id` + `branch_id` + `chapter_index < current` + `source_type` | `test_novel_a_hidden_from_novel_b` / `test_main_branch_isolated_from_experiment` | ✅ |
| 8 | Planner Only | RAG 只在 `Orchestrator.plan_chapter` → `ChapterPlanner.plan_chapter` 注入；Writer 未修改 | 代码审查 + `test_rag_evidence_section_in_prompt` | ✅ |
| 9 | Prompt Injection | ChapterPlanner prompt 中新增 `## 【历史检索证据（RAG）】` 区域，插入在 World Setting 之后、Book Plan 之前 | `test_rag_evidence_section_in_prompt` | ✅ |
| 10 | Retrieval Trace | `RetrievalTrace` dataclass → JSON 保存到 `tracking/rag_traces/`。记录 chapter_index, branch_id, query, top_k, filters, results(含 doc_id, chapter_index, chunk_index, source_path, distance, text), timestamp, success | `test_to_dict_from_dict` / `test_failed_trace_serialization` | ✅ |
| 11 | Graceful Degradation | `_retrieve_evidence` 中 `except Exception` → `[RAG WARNING]` + failed trace + 返回空 evidence | `test_search_exception_does_not_crash_planning` | ✅ |
| 12 | Index Failure | `_index_chapter_to_rag` 中 `except Exception` → `[RAG WARNING]` + 返回 0；调用方 `review_chapter` 已先行完成所有 canonical 写入 | `test_index_failure_preserves_chapter_review` | ✅ |

---

## 4. 关键实现细节

### 4.1 Chunking

- 纯字符级 deterministic sliding window
- `CHUNK_SIZE = 800`（可配置）
- `CHUNK_OVERLAP = 100`（可配置）
- 空 chunk（纯空白）自动跳过
- `overlap >= chunk_size` 时安全 clamp 为 `chunk_size // 4`

### 4.2 Chunk ID 格式

```
{novel_id}_{branch_id}_ch{NNNN}_chunk{NNN}
```

例如：`kunlun_ruins_main_ch0003_chunk002`

保证同内容重复索引产生相同 ID（幂等写入）。

### 4.3 Deterministic Retrieval Query

从以下来源拼接（无 LLM 调用）：

1. Volume Plan 中对应本章的事件描述（`对应章节: 第N章`）
2. Chapter outline（`--outline` 参数）
3. 追踪文档中提取的角色名（`**粗体名**` 模式）
4. 追踪文档中提取的物品名（表格第一列）
5. Author extra instructions（`--instructions` 参数）

### 4.4 Retrieval Trace 存储

```text
tracking/rag_traces/retrieval_trace_chNNNN_TIMESTAMP.json
```

JSON 格式，完整记录 query、filters、results（含 distance 和 text）、success/error。

### 4.5 Lazy Chroma 对测试性能的影响

E03.1 报告记录测试耗时 ~59s，主要来自 `Orchestrator()` 构造时 ChromaDB 初始化。
E04 改为 lazy init 后，不使用 RAG 的测试（35 个既有测试）ChromaDB 从未初始化。
E04 新测试仅在实际调用 `index_chapter`/`search` 时创建 client。

完整 66 测试耗时 ~108s，其中 E04 31 测试含 ChromaDB embedding 耗时 ~50s。

---

## 5. 测试清单

### 5.1 E04 focused tests（31 个，test_rag.py）

| 类别 | 测试 | 覆盖点 |
|---|---|---|
| **Chunking** | `TestChunkSize` | chunks 全部在 chunk_size 内 |
| | `TestChunkOverlap` | 连续 chunks 正确重叠 |
| | `TestDeterministicChunking` (2) | 相同输入 → 相同输出；多次调用一致 |
| | `TestNoEmptyChunk` (3) | 空白字符串、纯空白文本、单字符 |
| **Chunk ID** | `TestChunkIdFormat` (3) | 组件完整、无 UUID、不同章不同 ID |
| **Trace** | `TestRetrievalTraceRoundTrip` (2) | to_dict/from_dict；失败 trace 序列化 |
| **Index** | `TestIndexIdempotency` | 重复索引 chunk 数不变 |
| | `TestStaleChunkRemoval` | 缩短内容后 chunk 数减少 |
| **Metadata** | `TestMetadata` | 所有字段正确存储 |
| **Future Leakage** | `TestFutureLeakage` (2) | ch5 看不到 ch5+；ch4 看不到 ch5+ch6 |
| **Isolation** | `TestNovelIsolation` | 小说A ≠ 小说B |
| | `TestBranchIsolation` | main ≠ experiment |
| **Degradation** | `TestEmptyRetrieval` (2) | 空 collection 返回空；无崩溃 |
| | `TestRetrievalExceptionDegradation` | search 异常不崩溃 Planner |
| | `TestIndexFailureWithoutRollback` | 索引失败 → [RAG WARNING] → 章节状态完整 |
| **Lazy Init** | `TestLazyInitialization` (3) | 构造函数不创建 client；index/search 触发创建 |
| | `TestLazyOrchestratorChroma` | Orchestrator() 后 `_chroma is None` |
| **Prompt Injection** | `TestPlannerPromptInjection` | `【历史检索证据（RAG）】` 出现在 LLM prompt |
| **Backfill** | `TestBackfillIdempotency` | 重复 backfill chunk 数相同 |
| | `TestRebuild` | rebuild 产生相同 chunk 数 |
| **Corpus** | `TestCorpusOnlyStyledChapters` | 草稿文件不被索引 |

### 5.2 既有测试（35 个，无回归）

- `test_chapter_plan.py`：9 个（E01/E02）
- `test_planning_foundation.py`：11 个（E03 foundation）
- `test_planning_hierarchy.py`：15 个（E03 + E03.1）

---

## 6. 全部测试结果

```
Ran 66 tests in 108.0s
OK   (66/66，零回归)
```

既有 35 测试全部通过。新增 31 测试全部通过。

ResourceWarning（unclosed SQLite database）为预存在问题（Orchestrator 构造 SQLiteStore 后未显式关闭），非本轮引入。

---

## 7. 尚未实现的功能（不在 E04 范围）

按 E04 规格明确排除：

- BM25 / Hybrid Search
- Reranker
- Multi-query / Query Rewrite / HyDE
- GraphRAG / Knowledge Graph
- Agentic RAG
- Writer-level Retrieval
- 自动 Replanning 触发（L2/L3 自动检测）
- Rollback 触发 Chroma 索引重建
- LangGraph 集成
- Semantic chunking
- `branch_id` 的动态切换（仅有 `StoryBranch` 数据模型，无运行时分支切换）

### Foundation 预留接口（非"已完成功能"）

- `ChromaStore.rebuild_branch()` — 用于未来 Rollback 时按分支删除+重建
- `RetrievalTrace` — 未来可扩展到 L2/L3 evidence 字段（`PlanningModificationReport.evidence` / `StrategicRepairCase.evidence`）
- `_retrieve_evidence` 返回 `(text, trace)` 双值 — 未来 L2/L3 判断可直接消费 trace 对象

---

## 8. 验收结论

| E04 P0 不变量 | 状态 |
|---|---|
| Lazy Chroma — Orchestrator() 不初始化 Chroma | ✅ |
| Corpus — 只索引 finalized/styled 章节 | ✅ |
| Stable Chunk ID — 无随机 UUID | ✅ |
| Stale Chunk Removal — reindex 章节先删后写 | ✅ |
| Metadata — 6 字段完整 | ✅ |
| Future Leakage — chapter_index < N 硬约束 | ✅ |
| Isolation — novel + branch + chapter_index < N + source_type | ✅ |
| Planner Only — RAG 仅进入 ChapterPlanner | ✅ |
| Prompt Injection — `【历史检索证据（RAG）】` 真实进入 LLM prompt | ✅ |
| Retrieval Trace — JSON 保存完整记录 | ✅ |
| Graceful Degradation — 检索/嵌入/collection 错误不崩溃 | ✅ |
| Index Failure — 不回滚章节/Memory | ✅ |
| Backfill CLI — `rag-index` + `--rebuild` 已注册 | ✅ |
| Deterministic query — 无 LLM Query Rewrite | ✅ |
| Tests ≥ spec 要求 | ✅ (31 个，覆盖全部 P0) |
| 完整测试 0 回归 | ✅ (66/66) |

**E04 RAG MVP 达到验收条件。**
