"""One state router shared by continue and autonomous batch run."""

from __future__ import annotations

import re
from typing import Any

from src.config.settings import get_settings
from src.storage.document_formats import VolumePlan
from src.storage.file_store import FileStore
from src.workflows.chapter_runner import ChapterWorkflowRunner


class NovelContinuationService:
    def __init__(self, novel_id: str):
        self.novel_id = novel_id
        self.settings = get_settings()
        self.fs = FileStore(novel_id, self.settings.data_dir)

    def _canonical_indexes(self) -> list[int]:
        indexes = []
        for path in self.fs.list_chapters():
            match = re.fullmatch(r"chapter_(\d{4})\.md", path.name)
            if match:
                indexes.append(int(match.group(1)))
        return sorted(indexes)

    def _latest_canonical(self) -> int:
        indexes = self._canonical_indexes()
        return indexes[-1] if indexes else 0

    def route(self) -> dict[str, Any]:
        latest = self._latest_canonical()
        # Fail closed on the first incomplete canonical derivation, even if a
        # damaged/manual workspace somehow contains later chapter files.
        for canonical_index in self._canonical_indexes():
            canonical_state = ChapterWorkflowRunner(
                self.novel_id, canonical_index
            ).inspect()
            canonical_status = str(
                canonical_state["values"].get("workflow_status", "")
            ).upper()
            if canonical_status != "DERIVED_READY":
                return {
                    "action": "repair_derivation",
                    "chapter_index": canonical_index,
                    "status": canonical_status or "UNKNOWN",
                }

        chapter_index = latest + 1
        runner = ChapterWorkflowRunner(self.novel_id, chapter_index)
        state = runner.inspect()
        if state["interrupts"]:
            return {
                "action": "waiting_human",
                "chapter_index": chapter_index,
                "result": {
                    **state["values"],
                    "workflow_status": "WAITING_HUMAN",
                    "interrupts": state["interrupts"],
                },
            }
        if state["values"] and state["next"]:
            return {
                "action": "resume_workflow",
                "chapter_index": chapter_index,
            }

        volume_text = self.fs.load_tracking_doc("volume_plan") or ""
        if not volume_text:
            return {
                "action": "blocked",
                "message": "缺少 tracking/volume_plan.md，当前没有合法的下一步。",
            }
        volume = VolumePlan.from_markdown(volume_text)
        if volume.status.upper() == "COMPLETED":
            return {
                "action": "volume_boundary",
                "message": (
                    f"第{volume.volume_number}卷已经 COMPLETED。\n"
                    "请先生成、审阅并编辑下一卷 Volume Plan。"
                ),
            }
        if self.settings.chapter_mode == "human":
            return {
                "action": "waiting_human_intent",
                "chapter_index": chapter_index,
                "message": (
                    f"数据管理模式正在等待第 {chapter_index} 章 Chapter Intent。\n"
                    f"请运行：python main.py write {self.novel_id} "
                    f"--chapter {chapter_index} --intent <本章意图>"
                ),
            }
        return {"action": "start_chapter", "chapter_index": chapter_index}

    def continue_once(self) -> dict[str, Any]:
        decision = self.route()
        action = decision["action"]
        chapter = decision.get("chapter_index", 0)
        if action == "waiting_human":
            return decision["result"]
        if action == "repair_derivation":
            print(
                f"第 {chapter} 章正文已经 Canonical Commit，派生过程尚未完成。\n"
                "正在继续未完成的 Derivation……"
            )
            return ChapterWorkflowRunner(
                self.novel_id, chapter
            ).repair_derivation()
        if action == "resume_workflow":
            print(f"检测到第 {chapter} 章存在未完成工作流。从现有 checkpoint 继续……")
            return ChapterWorkflowRunner(self.novel_id, chapter).run()
        if action == "start_chapter":
            print(f"继续第 {chapter} 章规划……")
            return ChapterWorkflowRunner(self.novel_id, chapter).run()
        return {
            "workflow_status": "BLOCKED",
            "continuation_action": action,
            "error": decision.get("message", "当前没有合法的下一步。"),
        }

    def run_to_chapter(self, target: int) -> dict[str, Any]:
        if target <= 0:
            raise ValueError("--to-chapter 必须是正整数")
        if (
            self.settings.chapter_mode != "agent"
            or self.settings.agent_execution != "autonomous"
        ):
            raise ValueError(
                "run --to-chapter 仅适用于 Agent Mode + autonomous execution"
            )
        while True:
            latest = self._latest_canonical()
            if latest >= target:
                status = ChapterWorkflowRunner(
                    self.novel_id, target
                ).get_workflow_status()
                if status != "DERIVED_READY":
                    return self.continue_once()
                return {
                    "workflow_status": "DERIVED_READY",
                    "chapter_index": target,
                    "message": f"已连续完成至第 {target} 章。",
                }
            result = self.continue_once()
            if result.get("workflow_status") != "DERIVED_READY":
                return result
