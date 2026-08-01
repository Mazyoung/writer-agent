"""SettingsEditor — 设定管理 Agent。

接收用户自然语言指令，修改 world_setting.md，
并利用 SyncManager 检测变更、扫描受影响文件、生成同步计划。
"""

import re
from dataclasses import dataclass, field

from src.core.agent_base import BaseAgent
from src.storage.sync_manager import SyncManager, ChangeReport, PropagationPlan


@dataclass
class EditResult:
    """设定编辑结果"""
    success: bool
    new_world_setting: str = ""
    change_report: ChangeReport = field(default_factory=ChangeReport)
    propagation_plan: PropagationPlan = None
    human_report: str = ""
    error: str = ""


class SettingsEditor(BaseAgent):
    """设定管理 Agent —— 修改世界设定并同步整个存储链路"""

    def __init__(self, novel_id: str):
        super().__init__("settings_editor", novel_id, "settings_editor.txt")

    def edit(self, user_request: str, current_world_setting: str,
             plot_structure: str = "",
             scene_plans: dict[int, str] = None) -> EditResult:
        """处理设定修改请求。返回包含新 world_setting 和同步计划的完整结果。"""
        scene_plans = scene_plans or {}

        user_msg = f"""## 当前 world_setting.md
{current_world_setting}

## 用户修改请求
{user_request}

请根据以上请求修改世界设定。记住：输出完整的 world_setting.md 全文。"""

        try:
            result = self.run(
                user_message=user_msg,
                save_category="settings",
                save_prefix="world_setting_edited",
            )
            raw_output = result.content
        except Exception as e:
            return EditResult(success=False, error=f"Agent 调用失败: {e}")

        # 解析 Agent 输出 —— 分离"修改说明""受影响文件""更新后的 world_setting"
        new_ws = self._extract_world_setting(raw_output)
        if not new_ws or len(new_ws) < 100:
            return EditResult(success=False, error="Agent 未输出有效的 world_setting 全文",
                              human_report=raw_output)

        # 运行 SyncManager 检测变更
        sm = SyncManager(self.file_store)
        change_report = sm.detect_changes(current_world_setting, new_ws)
        propagation_plan = sm.execute_propagation(change_report, plot_structure, scene_plans)

        # 生成人类可读报告
        human_report = self._build_human_report(raw_output, change_report, propagation_plan)

        return EditResult(
            success=True,
            new_world_setting=new_ws,
            change_report=change_report,
            propagation_plan=propagation_plan,
            human_report=human_report,
        )

    def _extract_world_setting(self, raw_output: str) -> str:
        """从 Agent 输出中提取 world_setting.md 全文"""
        # 优先匹配 "## 更新后的 world_setting.md" 之后的内容
        markers = [
            r'##\s*更新后的\s*world_setting\.md',
            r'##\s*新\s*world_setting',
            r'##\s*完整的\s*world_setting',
        ]
        for marker in markers:
            m = re.search(marker, raw_output, re.IGNORECASE)
            if m:
                after = raw_output[m.start():]
                # 去掉标记行本身
                lines = after.split("\n")
                # 找到第一个非空行之后的内容
                body_start = 1
                while body_start < len(lines) and not lines[body_start].strip():
                    body_start += 1
                return "\n".join(lines[body_start:]).strip()

        # 备选：尝试匹配 # 一、世界铁律
        ws_match = re.search(r'(#\s*一[、,]\s*世界铁律.*)', raw_output, re.DOTALL)
        if ws_match:
            return ws_match.group(1).strip()

        # 最后备选：返回全部内容（去掉开头的修改说明行）
        lines = raw_output.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("# ") and "世界铁律" in line:
                return "\n".join(lines[i:]).strip()

        return ""

    def _build_human_report(self, raw_agent_output: str,
                            change_report: ChangeReport,
                            plan: PropagationPlan) -> str:
        """构建完整的修改报告"""
        # 提取 Agent 的修改说明
        agent_summary = ""
        summary_match = re.search(
            r'##\s*修改说明\s*\n(.*?)(?=\n##\s)',
            raw_agent_output, re.DOTALL
        )
        if summary_match:
            agent_summary = summary_match.group(1).strip()

        lines = [
            "# 设定修改报告",
            "",
            "## Agent 修改说明",
            agent_summary or "(Agent 未提供修改说明)",
            "",
        ]

        # SyncManager 的检测结果
        sm_report = SyncManager(self.file_store).generate_report(plan)
        lines.append(sm_report)

        return "\n".join(lines)

    def commit(self, edit_result: EditResult, novel_id: str,
               sqlite_store=None, chroma_store=None):
        """确认提交：写入新 world_setting + 执行 SQLite/ChromaDB 同步。
        注意：plot_structure 和 scene_plan 的修改需要人工或后续 Agent 处理。"""
        if not edit_result.success:
            return False

        # 1. 保存 world_setting.md
        self.file_store.save_canonical("settings", "world_setting",
                                        edit_result.new_world_setting)

        # 2. SQLite 同步
        if sqlite_store and edit_result.propagation_plan:
            sm = SyncManager(self.file_store, sqlite_store, chroma_store)
            sm.apply_sqlite_changes(edit_result.propagation_plan, novel_id)

        # 3. ChromaDB 同步
        if chroma_store and edit_result.propagation_plan:
            sm = SyncManager(self.file_store, sqlite_store, chroma_store)
            sm.apply_chroma_changes(edit_result.propagation_plan, novel_id)

        return True
