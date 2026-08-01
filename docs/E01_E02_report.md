# E01 + E02 修复报告

日期：2026-08-01
范围：仅修复审计问题表中的 P0 #1（ChapterPlan chapter_index round-trip）与 P0 #2（Writer world_setting 上下文）。未触碰 E03 及后续任务。

---

## 1. 修改内容

### 修改文件列表（3 改 1 增）

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/storage/document_formats.py` | 修改 | E01 章号恢复 + E02 世界观注入 |
| `src/agents/author/deepseek_writer.py` | 修改 | 同步 `write_chapter` docstring（原描述"用于验证，不是创作素材"已不符实际） |
| `tests/test_chapter_plan.py` | 新增 | 9 个最小单元测试（项目此前无测试目录） |

### 关键 diff

#### E01 — `ChapterPlan.from_markdown` 恢复真实章号

数据对象自身的序列化/反序列化一致性修复，非调用方覆盖。`to_markdown()` 的标题行是稳定字段（`# 第N章规划：《...》`），且与 `chapter_planner.txt` 约定的 LLM 输出格式一致，因此从标题行逆解析章号：

```python
# from_markdown() 中新增一行：
cp.chapter_index = _extract_chapter_index(text)   # 此前缺失，恒为默认值 1

# 新增辅助函数（与 _extract_title 并列）：
def _extract_chapter_index(text: str) -> int:
    """从标题行恢复真实章号：'# 第N章规划：《...》'。
    与 ChapterPlan.to_markdown() 的标题格式互为逆操作，
    保证 ChapterPlan -> Markdown -> ChapterPlan 后 chapter_index 不变。
    找不到章号时回退为 1（保持旧行为）。"""
    first = text.strip().split("\n")[0]
    m = re.search(r'第\s*(\d+)\s*章', first)
    return int(m.group(1)) if m else 1
```

#### E02 — `build_writer_prompt` 注入【世界观与硬规则】

```python
# 新增模块级常量（简单、明确、可解释的长度限制方案：头部截断）：
WORLD_SETTING_PROMPT_LIMIT = 2500

# build_writer_prompt() 开头新增（置于 Part B 之前，最高优先级位置）：
if world_setting:
    parts.append(
        "## 【世界观与硬规则】\n"
        + world_setting[:WORLD_SETTING_PROMPT_LIMIT]
        + "\n\n（以上为世界观的权威设定，属于高优先级约束："
          "你不得与其中已有规则冲突。若本章规划与世界观发生硬冲突，"
          "优先遵守世界观，不要自行创造新设定。）"
    )
```

#### `deepseek_writer.py` — docstring 同步

```diff
-            world_setting: 世界观设定（用于验证，不是创作素材）
+            world_setting: 世界观设定（截断后注入 prompt 的【世界观与硬规则】区域，高优先级约束）
```

---

## 2. 测试结果

命令：`venv/Scripts/python.exe -m unittest discover -s tests -v`

```
Ran 9 tests in 0.002s
OK
```

| 测试 | 覆盖点 | 结果 |
|---|---|---|
| test_chapter_1 | E01 round-trip chapter=1 | ✅ |
| test_chapter_10 | E01 round-trip chapter=10 | ✅ |
| test_chapter_100 | E01 round-trip chapter=100 | ✅ |
| test_llm_style_title | E01 对 LLM 直接产出的规划文件同样生效 | ✅ |
| test_writer_draft_prefix_uses_real_chapter | E01 草稿路径使用真实章号 | ✅ |
| test_section_present | E02【世界观与硬规则】区域 + 内容进入 prompt | ✅ |
| test_priority_instruction | E02 高优先级/不得冲突/优先遵守世界观指示 | ✅ |
| test_truncated_at_limit | E02 超长世界观在 2500 字符处截断 | ✅ |
| test_empty_world_setting_no_section | E02 空世界观不产生空区域 | ✅ |

**既有测试回归**：项目此前不存在任何测试套件，无既有测试可失败。作为替代，对全部依赖 `document_formats` 的模块做了导入冒烟测试：`orchestrator`、`chapter_planner`、`deepseek_writer`、`claude_stylist`、`state_manager`、`cli_helpers`、`migrate` —— 全部 `ALL IMPORTS OK`。

## 3. chapter=10 模拟验证

```
1) from_markdown 后 chapter_index = 10
2) Writer 草稿保存前缀 = chapter_0010_draft
3) Prompt 含【世界观与硬规则】: True
4) Prompt 含高优先级指示: True
5) 世界观截断生效(不超过上限): True
6) 写作指令章号行: ['## 第10章写作指令']
```

修复前第 6 项恒为 `## 第1章写作指令`、第 2 项恒为 `chapter_0001_draft`；现均使用真实章号。

## 4. 尚未解决的问题（不在本轮范围）

- **P0 #3**：ChromaStore/RAG 完全未接线（本轮明确禁止接入 ChromaDB）。
- **P0 #4**：主流程无人创建 `tracking/book_plan.md` / `tracking/volume_plan.md`，`plot_structure.md` 无消费者。
- `_extract_chapter_index` 的已知边界：若标题行完全不含「第N章」（LLM 严重偏离输出格式），章号回退为 1。这是刻意保留的旧行为兜底，未做全文档扫描。
- 标题文本本身的 round-trip 仍不完美（`第N章规划：《》` 包装会在二次序列化时嵌套），但 `plan.title` 当前无任何下游消费者，不影响功能；chapter_index 的 round-trip 已保证。
- world_setting 采用头部截断，若关键规则写在文档 2500 字符之后则不会进入 prompt（按要求，语义筛选留待后续）。

## 5. 验收结论

| 验收条件 | 状态 |
|---|---|
| E01: 从标题/稳定字段恢复真实 chapter_index | ✅ |
| E01: 非调用方覆盖，修复数据对象自身一致性 | ✅（改 `from_markdown` 本身） |
| E01: `ChapterPlan -> Markdown -> ChapterPlan` 章号不变 | ✅（chapter 1/10/100 测试通过） |
| E01: 最小测试覆盖 chapter 1/10/100 | ✅ |
| E01: Writer 草稿路径使用真实章节号 | ✅（`chapter_0010_draft`，测试+模拟双重验证） |
| E02: world_setting 真正进入 Writer Prompt | ✅ |
| E02: 简单明确可解释的长度限制 | ✅（`WORLD_SETTING_PROMPT_LIMIT = 2500` 头部截断） |
| E02: 明确划分【世界观与硬规则】区域 | ✅ |
| E02: 高优先级/不得冲突/硬冲突优先遵守世界观的明确指示 | ✅ |
| E02: 未做 RAG、未做语义筛选 | ✅ |
| 禁止项：未接 ChromaDB / 未动 StateManager / 未改架构 / 未动 init·book_plan·volume_plan / 未清理死代码 / 未动 HumanInterceptor / 未引入 LangGraph / 无大规模重构 | ✅ |
| 运行相关测试 | ✅ 9/9 通过 |
| 既有测试无回归 | ✅（无既有测试；导入冒烟全部通过） |

**E01/E02 均达到验收条件。**
