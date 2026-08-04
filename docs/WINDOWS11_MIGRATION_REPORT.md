# Windows 11 环境迁移与验收报告

- 项目：小说作者 Agent（RAG + LangGraph）
- 项目路径：`E:\agent\writer`
- 验收日期：2026-08-04
- 操作系统：Microsoft Windows 11，内核版本 `10.0.26200.8037`
- 结论：**通过**。现有 Conda `writer` 环境已恢复依赖，项目入口可启动，完整测试套件 203 项全部通过。

## 1. 当前 Python 环境

### 终端初始状态

| 检查项 | 结果 |
|---|---|
| `python --version` | Python 3.14.6 |
| `where python` 首选路径 | `E:\code\miniconda\python.exe` |
| 其他 Python shim | `C:\Users\12084\AppData\Local\Microsoft\WindowsApps\python.exe` |
| 初始激活环境 | `base` |
| Conda | 26.5.3 |
| Conda 根目录 | `E:\code\miniconda` |

初始终端激活的是 `base`，不是 `writer`。为避免把包安装到错误环境，本次安装和验收均通过 `conda run -n writer` 或直接调用 `writer` 环境解释器完成。

### writer 环境

| 检查项 | 结果 |
|---|---|
| 环境路径 | `E:\code\miniconda\envs\writer` |
| Python | 3.14.6 |
| Python 解释器 | `E:\code\miniconda\envs\writer\python.exe` |
| pip | 26.2 |

未创建新环境，未删除或重建现有环境。

## 2. 已安装依赖

项目存在 `requirements.txt`；不存在 `pyproject.toml` 和 `environment.yml`。

执行结果：

```text
pip install -r requirements.txt
Exit code: 0
```

首次安装因命令执行器超时未及时返回，但安装进程实际已完成；随后再次执行时全部显示 `Requirement already satisfied`。`pip check` 返回 `No broken requirements found.`。

主要直接依赖版本：

| 依赖 | 已安装版本 | 声明约束 |
|---|---:|---|
| openai | 2.53.0 | `>=2.0.0` |
| chromadb | 1.5.9 | `>=1.0.0` |
| python-dotenv | 1.2.2 | `>=1.0.0` |
| PyYAML | 6.0.3 | `>=6.0` |
| pydantic | 2.13.4 | `>=2.0.0` |
| langgraph | 1.2.10 | `>=1.2,<2` |

测试运行器 `pytest` 未包含在 `requirements.txt` 中，首次执行测试时无法识别命令。为完成验收，仅在现有 `writer` 环境额外安装了 `pytest 9.1.1`，未修改依赖文件。

### Python 3.14 兼容性判断

当前解析出的依赖均成功安装，并且完整测试通过，因此本次恢复**没有出现 Python 3.14 兼容性阻断**。ChromaDB 运行时产生一条关于 `asyncio.iscoroutinefunction` 将在 Python 3.16 移除的弃用警告；它不影响 Python 3.14 当前运行。

## 3. 配置与数据检查

### `.env`

仅检查变量名称和是否非空，未读取或记录真实密钥内容。

| 环境变量 | 状态 | 与代码匹配 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 已配置且非空 | 是 |
| `DEEPSEEK_BASE_URL` | 已配置且非空 | 是 |
| `ANTHROPIC_API_KEY` | 未配置 | 是；代码允许为空 |

`src/config/settings.py` 实际解析结果：

| 配置 | 路径 |
|---|---|
| `project_root` | `E:\agent\writer` |
| `data_dir` | `E:\agent\writer\data` |
| `prompts_dir` | `E:\agent\writer\src\config\prompts` |
| Chroma 持久化目录 | `E:\agent\writer\data\chroma_db` |

Chroma 目录存在，检查时包含 9 个文件，总大小 3,264,840 字节。小说数据、SQLite 文件及历史文档均保留，未删除数据文件。

活动 Python 代码未发现硬编码旧盘符。旧路径 `D:\agent\writer` 仅存在于历史资料：

- `CONVERSATION_LOG.md`
- `对话命令.txt`
- `docs/审计报告_格式化.md`

这些引用不参与当前运行。

项目内遗留的 `venv\pyvenv.cfg` 指向 `C:\Python314\python.exe` 和 `D:\agent\writer\venv`，已因 Windows 重装和项目迁移失效。该旧 venv 未被修改或删除；本次验收不使用它。

## 4. 项目启动结果

按要求执行 `python main.py`。通过 `conda run -n writer` 启动时，Conda 包装器在 GBK 控制台转发 Unicode 输出发生 `UnicodeEncodeError`。随后直接使用同一 `writer` 环境解释器，并临时设置 UTF-8 输出：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
E:\code\miniconda\envs\writer\python.exe main.py
```

结果为退出码 0，正常显示 CLI 帮助及以下命令：`init`、`status`、`plan`、`write`、`style`、`review`、`new-volume`、`rag-index`。

结论：项目入口启动正常；故障位于 Windows/Conda 输出编码层，不是项目依赖导入或业务代码错误。

## 5. 测试结果

使用 `writer` 环境执行：

```powershell
E:\code\miniconda\envs\writer\python.exe -m pytest
```

结果：

```text
collected 203 items
203 passed, 1 warning in 29.56s
Exit code: 0
```

覆盖的测试文件包括章节规划、E05/E06/E07、Planning、Proposal Override 和 RAG。唯一警告来自 ChromaDB 的未来弃用 API，不影响当前验收。

## 6. 发现的问题

| 分类 | 问题 | 当前影响 | 状态 |
|---|---|---|---|
| A. 环境问题 | 初始终端激活 `base`，不是 `writer` | 直接运行 `pip` 可能装错环境 | 已通过显式解释器规避 |
| A. 环境问题 | Conda `run` 在 GBK 控制台转发项目 Unicode 输出失败 | `conda run -n writer python main.py` 报编码错误 | 使用 UTF-8 + 直接解释器可正常启动 |
| A. 环境问题 | Git 检测到重装前后 SID 所有权变化（dubious ownership） | 常规 `git status` 被阻止 | 本次用一次性 `safe.directory` 参数完成只读检查，未改全局配置 |
| A. 环境问题 | 旧 `venv` 指向不存在的 `C:\Python314` 和旧项目路径 | 误用旧 venv 会失败 | 未使用、未删除 |
| B. 依赖问题 | `requirements.txt` 未声明 pytest | 初次无法运行测试 | 环境中已安装 pytest 9.1.1 |
| C. 配置问题 | 历史文档仍引用 `D:\agent\writer` | 不影响活动代码；可能误导人工操作 | 已记录，未修改历史资料 |
| D. 代码问题 | 未发现导致启动或测试失败的代码问题 | 无 | 203 项测试通过 |

## 7. 修复与维护建议

1. 日常开发前执行 `conda activate writer`，并用 `where python` 确认首项为 `E:\code\miniconda\envs\writer\python.exe`。
2. 在 PowerShell 配置中使用 UTF-8（例如设置 `PYTHONUTF8=1`），或直接调用 `writer` 解释器，避免 Conda GBK 输出转发问题。
3. 将 `pytest` 纳入正式的开发依赖清单，例如新增独立开发依赖文件；本次遵守约束，没有修改 `requirements.txt`。
4. 确认仓库可信后，可由用户执行 `git config --global --add safe.directory E:/agent/writer`，解决 Windows 重装导致的 Git SID 所有权告警。本次未修改用户全局 Git 配置。
5. 不再使用项目内旧 `venv`。确认其无保留价值后再由用户单独决定是否删除；本次未删除任何文件。
6. 历史文档中的 `D:\agent\writer` 可在单独的文档维护任务中更新；活动代码已使用相对项目根路径，无需修改 RAG 或 LangGraph 实现。
7. 保留当前通过验收的主要版本记录；未来升级 ChromaDB 或 Python 时关注该弃用警告。

## 8. 约束遵守情况

- 未修改核心业务代码。
- 未修改 LangGraph 架构。
- 未修改 RAG 实现。
- 未修改 `requirements.txt`。
- 未删除任何环境或数据文件。
- 未重构项目。
- 项目内唯一新增文件为本迁移报告。
