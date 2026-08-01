"""ReplanTriggerPolicy — 长期规划修改的触发规则定义（E03）。

核心原则：长期规划默认 Stable。只有以下允许原因才可能触发修订，
且 L2 必须人工审批、L3 必须人机协同（本轮不实现自动判断）。

允许触发（ALLOWED_TRIGGERS）：
- fact_conflict            已发生事实与未来 Plan 不可调和冲突
- prerequisite_invalid     Plan 的关键前置条件已经失效
- node_preempted           原规划节点已经提前不可逆发生
- character_state_block    当前实际角色状态使未来节点不可能
- user_request             用户主动要求修改
- supervisor_l3            Supervisor 判断为 L3（预留，本轮无自动检测）

禁止触发（FORBIDDEN_TRIGGERS）：
- writer_preference        Writer 临时觉得另一种写法更好
- more_exciting            模型认为剧情可以更"精彩"
- style_change             单纯文风变化
- scene_difficulty         普通场景执行困难（属 L1，自动重写即可）
- speculation              没有实际证据的猜测
"""


class ReplanTrigger:
    """允许触发长期规划修改的原因类目。"""
    FACT_CONFLICT = "fact_conflict"
    PREREQUISITE_INVALID = "prerequisite_invalid"
    NODE_PREEMPTED = "node_preempted"
    CHARACTER_STATE_BLOCK = "character_state_block"
    USER_REQUEST = "user_request"
    SUPERVISOR_L3 = "supervisor_l3"


ALLOWED_TRIGGERS = frozenset({
    ReplanTrigger.FACT_CONFLICT,
    ReplanTrigger.PREREQUISITE_INVALID,
    ReplanTrigger.NODE_PREEMPTED,
    ReplanTrigger.CHARACTER_STATE_BLOCK,
    ReplanTrigger.USER_REQUEST,
    ReplanTrigger.SUPERVISOR_L3,
})

FORBIDDEN_TRIGGERS = frozenset({
    "writer_preference",
    "more_exciting",
    "style_change",
    "scene_difficulty",
    "speculation",
})


class ReplanTriggerPolicy:
    """最小触发策略接口：判断一个触发原因是否允许启动规划修订流程。

    注意：is_allowed=True 只表示"可以进入 L2/L3 人工流程"，
    不代表允许自动修改规划。
    """

    @staticmethod
    def is_allowed(trigger: str) -> bool:
        return trigger in ALLOWED_TRIGGERS

    @staticmethod
    def is_forbidden(trigger: str) -> bool:
        return trigger in FORBIDDEN_TRIGGERS
