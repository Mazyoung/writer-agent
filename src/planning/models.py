"""Replanning Foundation — 规划修订/分支/检查点的基础数据模型（E03）。

设计硬约束（本轮只建立数据模型与持久化接口，不实现自动行为）：

L1 — Execution Issue
    场景表达/文本逻辑问题。Writer 自动重写处理，不需要人工审批，
    不允许修改 Chapter / Volume / Book Plan。

L2 — Planning Issue（Human Approval）
    当前 Chapter Plan 无法合理执行，或 Volume Plan 局部节点需调整。
    禁止 Agent 自动静默修改规划。必须生成 PlanningModificationReport，
    HALT 受影响操作 → Human Review → Accept / Edit+Confirm 后
    才创建 PlanRevision 并修改 canonical Plan。

L3 — Strategic Planning Issue（Human-Agent Collaborative Repair）
    Volume/Book 战略规划明显失效，影响多个未来章节，无唯一正确修复方案。
    必须 HALT PIPELINE，禁止 Agent 自动修复，进入 StrategicRepairCase 流程。

修改权限模型（最小权限）：
    Writer              : 任何 Plan 无修改权，只能报告问题
    ChapterPlanner      : 可重新生成当前章 Chapter Plan；对上层只能提交问题
    Supervisor/StateManager : 可建议 L2（创建 Report）/ L3（创建 RepairCase）
    Planning/Architect  : 经人工批准后修改 Volume Plan；L3 协同后修改 Book Plan
    Human               : 所有层的最终决定权

Plan Revision 与 Rollback 的严格区别：
    Plan Revision = 过去发生的故事不变，只改变未来 Planning State。
    Rollback      = 过去某段内容被逻辑废弃 → 恢复旧 Checkpoint → 产生新 StoryBranch。

Rollback 必须定义为 Workflow State Rollback（不是 restore markdown file）：
    回到 Chapter N 必须恢复：正文、Book/Volume/Chapter Plan、角色/物品/
    修炼/伏笔状态、Fact Digest、Tracking Docs、SQLite、未来 Chroma/RAG 索引。
    对 ChromaDB 的设计决策：invalidate future records + 从 active branch
    重建索引，而不是做向量数据库 binary snapshot。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now().isoformat()


# ── 状态常量（用字符串，保持 schema 简单） ─────────────────────

class PlanType:
    BOOK_PLAN = "book_plan"
    VOLUME_PLAN = "volume_plan"
    CHAPTER_PLAN = "chapter_plan"


class RevisionStatus:
    PROPOSED = "PROPOSED"      # 已提出，待人工审批
    APPLIED = "APPLIED"        # 已批准并应用到 canonical
    REJECTED = "REJECTED"      # 人工拒绝


class ReportStatus:
    PENDING = "PENDING"            # 待人工审阅
    ACCEPTED = "ACCEPTED"          # 人工接受
    EDIT_CONFIRMED = "EDIT_CONFIRMED"  # 人工编辑后确认
    REJECTED = "REJECTED"          # 人工拒绝


class RepairStrategy:
    FORWARD_REPAIR = "FORWARD_REPAIR"      # 接受已写历史，重规划未来
    ROLLBACK_REWRITE = "ROLLBACK_REWRITE"  # 回滚到安全检查点，废弃分支重写
    MANUAL_CUSTOM = "MANUAL_CUSTOM"        # 人工自定义修复


class RepairStatus:
    OPEN = "OPEN"
    WAITING_HUMAN = "WAITING_HUMAN"
    IN_REPAIR = "IN_REPAIR"
    RESOLVED = "RESOLVED"
    ABANDONED = "ABANDONED"


class BranchStatus:
    ACTIVE = "ACTIVE"
    ABANDONED = "ABANDONED"    # 被 Rollback & Rewrite 废弃（内容保留，不物理删除）
    ARCHIVED = "ARCHIVED"


class CheckpointStatus:
    STABLE = "STABLE"          # 章完成并通过 Review / State Update 的稳定提交
    SUPERSEDED = "SUPERSEDED"  # 被更新的检查点取代（仅同一章重复提交时）


# ── 数据模型 ─────────────────────────────────────────────────

@dataclass
class PlanRevision:
    """有业务语义的规划修改记录（≠ .bak 文件安全备份）。

    长期规划不得被无痕覆盖：每次对 Book/Volume/Chapter Plan 的
    授权修改都必须留下一条 PlanRevision。
    """
    revision_id: str = ""
    plan_type: str = ""              # PlanType.*
    base_version: str = ""           # 修改前规划版本
    new_version: str = ""            # 修改后规划版本
    trigger_chapter: str = ""        # 触发修改的章节（如 "第14章"）
    reason: str = ""                 # 修改原因（应对应 ReplanTrigger 允许项）
    old_content: str = ""            # 旧内容（可选内联；大文档用 old_content_ref）
    new_content: str = ""            # 新内容（可选内联）
    old_content_ref: str = ""        # 旧内容文件引用（如 volumes/volume_01.md）
    new_content_ref: str = ""        # 新内容文件引用（如 volume_plan.md）
    affected_nodes: list[str] = field(default_factory=list)  # 受影响的规划节点
    created_at: str = ""
    status: str = RevisionStatus.PROPOSED
    approved_by: str = ""            # human decision（如有）
    decision: str = ""               # 人工决定描述（accept / edit+confirm / 命令触发）

    def __post_init__(self):
        if not self.revision_id:
            self.revision_id = _new_id("rev")
        if not self.created_at:
            self.created_at = _now()

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "PlanRevision":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PlanningModificationReport:
    """L2 Planning Issue 的修改报告。只有人工 Accept / Edit+Confirm
    后才能据此创建 PlanRevision 并修改 canonical Plan。"""
    report_id: str = ""
    trigger_chapter: str = ""
    problem: str = ""                            # 问题描述
    severity: str = "L2"                         # L2（L3 走 StrategicRepairCase）
    affected_plan: str = ""                      # PlanType.*
    current_plan: str = ""                       # 当前相关规划内容/引用
    conflicting_actual_state: str = ""           # 与规划冲突的实际状态
    evidence: str = ""                           # 证据（为未来 RAG 预留）
    proposed_change: str = ""                    # 建议的修改
    affected_future_nodes: list[str] = field(default_factory=list)
    risk_if_accept: str = ""
    risk_if_reject: str = ""
    created_at: str = ""
    status: str = ReportStatus.PENDING
    approved_by: str = ""
    decision: str = ""

    def __post_init__(self):
        if not self.report_id:
            self.report_id = _new_id("rep")
        if not self.created_at:
            self.created_at = _now()

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "PlanningModificationReport":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class StrategicRepairCase:
    """L3 Strategic Planning Issue。requires_human 恒为 True：
    禁止 Agent 自动修复，必须 Human-Agent Collaborative Strategic Repair。"""
    case_id: str = ""
    trigger_chapter: str = ""
    problem_summary: str = ""
    affected_scope: str = ""                     # volume_plan / book_plan / both
    affected_chapters: list[str] = field(default_factory=list)
    affected_plan_nodes: list[str] = field(default_factory=list)
    evidence: str = ""                           # 为未来 RAG 预留
    last_safe_checkpoint: str = ""               # ChapterCheckpoint.checkpoint_id
    repair_options: list[str] = field(default_factory=list)  # RepairStrategy.*
    selected_strategy: str = ""                  # 人工选择的策略
    human_decision: str = ""
    branch_id: str = ""                          # Rollback 时产生的新 StoryBranch
    status: str = RepairStatus.OPEN
    created_at: str = ""
    requires_human: bool = True                  # 恒为 True，禁止自动修复

    def __post_init__(self):
        if not self.case_id:
            self.case_id = _new_id("case")
        if not self.created_at:
            self.created_at = _now()
        self.requires_human = True               # 硬约束，不允许置 False

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategicRepairCase":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class StoryBranch:
    """故事逻辑分支（为 Rollback & Rewrite 预留，非 Git 级版本系统）。

    废弃分支的旧内容不得物理删除，仅标记 status=ABANDONED。
    """
    branch_id: str = ""
    parent_branch: str = ""          # 父分支 branch_id（主分支为 "main" 或 ""）
    fork_checkpoint: str = ""        # 分叉点的 ChapterCheckpoint.checkpoint_id
    status: str = BranchStatus.ACTIVE
    created_reason: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.branch_id:
            self.branch_id = _new_id("branch")
        if not self.created_at:
            self.created_at = _now()

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "StoryBranch":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ChapterCheckpoint:
    """某一章完成并通过 Review / State Update 后，整个 Agent 世界状态的
    一次稳定提交。Rollback 的恢复单位是 Checkpoint，不是单个 markdown 文件。

    各 *_version / *_file 字段记录该时点每部分 Workflow State 的位置，
    为未来完整恢复（含 SQLite、Chroma 重建）预留接口。
    """
    checkpoint_id: str = ""
    chapter_index: int = 0
    active_branch: str = ""              # 提交时的 StoryBranch.branch_id
    book_plan_version: str = ""
    volume_plan_version: str = ""
    chapter_plan_version: str = ""
    chapter_file: str = ""               # 正文文件路径（相对 novel 根目录）
    memory_state_version: str = ""       # 记忆/事实层版本（fact digest 等）
    tracking_state_version: str = ""     # tracking 文档状态版本
    fact_digest_version: str = ""
    created_at: str = ""
    status: str = CheckpointStatus.STABLE

    def __post_init__(self):
        if not self.checkpoint_id:
            self.checkpoint_id = _new_id("ckpt")
        if not self.created_at:
            self.created_at = _now()

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterCheckpoint":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
