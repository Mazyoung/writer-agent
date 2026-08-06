"""Chapter Plan review gate for the E07.6 production workflow."""

from src.core.agent_base import BaseAgent
from src.core.token_guard import guard_planning_context


class PlanReviewer(BaseAgent):
    """Review one Chapter Plan against authoritative planning context."""

    def __init__(self, novel_id: str):
        super().__init__("plan_reviewer", novel_id, "plan_reviewer.txt")

    def review_plan(
        self,
        chapter_index: int,
        plan_text: str,
        chapter_intent: str = "",
        world_setting: str = "",
        book_plan: str = "",
        volume_plan: str = "",
        current_state: str = "",
        historical_evidence: str = "",
        review_attempt: int = 1,
    ) -> str:
        """Return a review analysis with an explicit ReviewDecision section."""
        parts = [
            f"## 第 {chapter_index} 章规划审阅",
            f"## 待审 Chapter Plan\n{plan_text}",
        ]
        if chapter_intent:
            parts.append(f"## Chapter Intent（作者本章意图）\n{chapter_intent}")
        if world_setting:
            parts.append(f"## 世界观设定\n{world_setting}")
        if book_plan:
            parts.append(f"## Book Plan（战略约束）\n{book_plan}")
        if volume_plan:
            parts.append(f"## Volume Plan（当前卷战术约束）\n{volume_plan}")
        if current_state:
            parts.append(f"## Current State\n{current_state}")
        if historical_evidence:
            parts.append(f"## Relevant Historical Facts\n{historical_evidence}")
        parts.append("---\n请按输出格式审阅规划。")
        guard_planning_context(self.model_slot, {
            "chapter_plan.md": plan_text,
            "human_intent": chapter_intent,
            "world_setting.md": world_setting,
            "book_plan.md": book_plan,
            "volume_plan.md": volume_plan,
            "current_state.md": current_state,
            "historical_evidence": historical_evidence,
        })

        result = self.run(
            user_message="\n\n".join(parts),
            save_category="states",
            save_prefix=(
                f"plan_review_ch{chapter_index:04d}_attempt{review_attempt}"
            ),
        )
        return result.content
