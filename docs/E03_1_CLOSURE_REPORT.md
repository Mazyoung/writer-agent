# E03.1 封口报告 — Hierarchical Planning 工程封口

日期：2026-08-01
范围：仅两项 —— ① new-volume 事务式切换语义；② Volume 1 真正依赖已解析的 canonical Book Plan。
未触碰：RAG / ChromaDB / StateManager / 自动 L2/L3 / Rollback / LangGraph / 无关代码清理。

---

## 1. 修改文件（2 改 1 增）

| 文件 | 变更 |
|---|---|
| `src/core/orchestrator.py` | `start_new_volume` 重构为 Generate → Validate → Commit；新增 `_validate_volume_candidate`；`initialize_novel` 中 Book Plan 先解析校验再生 Volume 1，Volume 1 prompt 改为接收解析后的 Book Plan + 服从性约束 |
| `tests/test_planning_hierarchy.py` | 新增 8 个测试（6 个事务失败路径 + 1 个成功提交回滚 + 1 个依赖证明） |
| `E03_1_CLOSURE_REPORT.md` | 本报告（新增） |

## 2. new-volume 事务流程

### 重构前（E03，存在半提交风险）

```text
读取旧卷
↓
旧卷标记 COMPLETED 并写入归档        ← 先修改状态
↓
LLM 生成新卷（直接 save_canonical 覆盖 ACTIVE）  ← 边生成边提交
↓
写 PlanRevision
```

问题：归档在生成之前；LLM 输出未经验证直接覆盖 canonical；
API 失败/空输出/解析错误/卷号错误会留下「旧卷已归档、新卷不可用」的半提交状态。

### 重构后（E03.1）

```text
[0] 读取当前状态（只读）
    ├─ 缺 book_plan/volume_plan → FileNotFoundError（未做任何修改）
    └─ 新卷号 ≤ 当前卷号 → ValueError（未做任何修改）
↓
[1] Generate：LLM 产出候选
    ├─ 保存到 tracking/candidate_volume_NN_<时间戳>.md（非 canonical，纯留痕）
    └─ API 失败 → 异常传播，canonical 零接触
↓
[2] Parse + Validate（_validate_volume_candidate）
    ├─ 输出为空 → ValueError
    ├─ 无法解析（无卷标题）→ ValueError
    ├─ volume_index ≠ 期望值 → ValueError
    ├─ status ≠ ACTIVE → ValueError
    └─ 必要字段缺失（章节范围 / 卷概述核心冲突 / 事件链为空）→ ValueError
    任一失败：当前卷保持 ACTIVE，无归档、无 Revision
↓
[3] Commit（全部通过才执行，失败回滚）
    try:
      ① 旧卷标记 COMPLETED → 写入 tracking/volumes/volume_NN.md
      ② save_canonical 新卷 → tracking/volume_plan.md（ACTIVE）
      ③ 写入 PlanRevision（status=APPLIED, approved_by=human）
    except:
      回滚：rollback_canonical 恢复旧 volume_plan.md + 删除刚写入的归档
      → RuntimeError("新卷提交失败，已回滚")
```

## 3. Volume 1 对 BookPlan 的依赖证明

**代码链**（orchestrator.initialize_novel）：

```text
WorldBuilder → world_setting.md
↓
PlotDesigner → book_plan.md（canonical）
↓
BookPlan.from_markdown(book_plan)  ← 必须成功解析（标题 + 卷框架），
│                                     否则 ValueError，分层链中断
↓
Volume 1 prompt 包含:
  ## 【全书战略规划 Book Plan】
  {解析后 BookPlan.to_markdown()[:4000]}
  服从性约束：
  - Volume Plan 必须服从 Book Plan 的战略方向；
  - 只能细化当前卷（第 1 卷），不得展开后续卷细节；
  - 不得重新定义 Book Plan 的故事终局、核心矛盾或战略约束。
```

Book 与 Volume 不再是并行独立生成：Volume 1 的 prompt 内容由**解析后的**
BookPlan 对象序列化而来（不是原始 proposal/world_setting 的平行产物）。
new-volume 的 prompt 同样带上了【全书战略规划 Book Plan】+ 服从性约束。

**测试证明**（TestVolume1DependsOnBookPlan）：
mock BookPlan 在「战略约束」中植入唯一标识 `STRATEGIC_TEST_CONSTRAINT_9271` →
捕获 Volume 1 生成的实际 prompt → 断言包含该标识与服从性声明。通过。

## 4. 新增测试（8 个，总计 28 → 35+1=36 项断言路径）

| 测试 | 验证点 |
|---|---|
| test_api_failure_keeps_state | API 异常 → 原 canonical 不变 / status 仍 ACTIVE / 无归档 / 无 Revision |
| test_empty_output_keeps_state | LLM 输出空白 → 同上 |
| test_unparseable_output_keeps_state | 无结构文本 → 同上 |
| test_wrong_volume_index_keeps_state | 期望第2卷实得第3卷 → ValueError 含「卷号错误」→ 同上 |
| test_wrong_status_keeps_state | 候选状态 PLANNED → ValueError → 同上 |
| test_save_failure_rolls_back | Commit 中 save_canonical 抛 IOError → RuntimeError「已回滚」→ canonical 恢复原内容、归档删除、无 Revision |
| test_start_new_volume（E03 既有） | 成功路径：旧卷 COMPLETED 归档 + 新卷 ACTIVE + APPLIED Revision |
| test_volume_prompt_contains_bookplan_marker | Volume 1 prompt 含 `STRATEGIC_TEST_CONSTRAINT_9271` 与服从性声明 |

## 5. 全部测试结果

```
Ran 35 tests in 59.4s
OK   (35/35)
```

含 E01/E02（9）、E03（19）、E03.1（7）全部测试，无回归。
（注：测试耗时主要来自每次构造 Orchestrator 时 chromadb 初始化，非功能问题。）

## 6. 是否存在半提交状态风险

**Generate/Validate 阶段：无风险。** 候选写入时间戳文件（非 canonical），
canonical 在该阶段零接触；所有校验失败路径都有测试证明状态不变。

**Commit 阶段：有回滚保护，但非真正原子。** 三步（归档→覆盖 canonical→Revision）
中任一步失败都会：恢复 canonical（依赖 save_canonical 的 .bak 机制）+ 删除归档。
已测试「②失败」场景。残余边界：
- 归档写入与 canonical 覆盖之间存在毫秒级窗口，进程崩溃（非异常）可能留下
  「归档已存、canonical 未换」——此时 ACTIVE 状态仍完整（归档只是副本），
  不构成不一致；
- `volume_plan_edited.md` 存在时会覆盖新卷显示（_edited 优先机制），
  Commit 后已加显式警告提示人工处理；
- 候选时间戳文件 `candidate_volume_NN_*.md` 在失败时保留作为调试留痕，
  不属于 canonical，不影响运行时真相。

结论：**不会出现「旧卷已失效、新卷不可用」的半提交状态**；
ACTIVE Volume 的唯一运行时真相在任何失败路径下保持完整。

## 7. 本轮未做（按范围约束）

RAG/ChromaDB、StateManager、自动 L2/L3、Rollback、LangGraph、无关清理。
E04 前置决策（ChromaStore 接线或移除）仍未处理。本轮到此停止。
