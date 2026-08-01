import json

from src.core.agent_base import BaseAgent
from src.storage.sqlite_store import SQLiteStore


class StateUpdater(BaseAgent):
    """4A — 状态更新师"""

    def __init__(self, novel_id: str, sqlite_store: SQLiteStore):
        super().__init__("state_updater", novel_id, "state_updater.txt")
        self.sqlite = sqlite_store

    def update(self, chapter_index: int, chapter_text: str) -> dict:
        """从新章节提取变化并更新状态表"""
        current_states = self.sqlite.export_all_states(self.novel_id)
        chapter_id = f"ch{chapter_index:04d}"

        user_msg = f"""## 当前状态表
```json
{json.dumps(current_states, ensure_ascii=False, indent=2)}
```

## 第 {chapter_index} 章正文
{chapter_text}

请提取本章中的所有状态变化，按 JSON 格式输出。"""

        result = self.run(
            user_message=user_msg,
            save_category="states",
            save_prefix=f"state_update_ch{chapter_index:04d}",
        )

        # 解析 JSON 结果并写入 SQLite
        changes = self._parse_changes(result.content)
        self._apply_changes(changes, chapter_id)
        return changes

    def extract_new_entities(self, chapter_index: int, chapter_text: str) -> list[dict]:
        """检测本章中首次出现的新角色/地点/组织/设定"""
        current_ws = self.file_store.load_canonical("settings", "world_setting") or ""
        current_states = self.sqlite.export_all_states(self.novel_id)

        # 构建已有实体清单供 LLM 对照
        known_chars = [c["name"] for c in current_states.get("characters", [])]

        user_msg = f"""## 已有世界观设定
{current_ws[:3000]}

## 已知角色清单
{', '.join(known_chars) if known_chars else '（暂无）'}

## 本章正文（最后3000字）
{chapter_text[-3000:]}

请提取本章中**首次出现**且**不在已有设定中**的新实体。不只提取有名有姓的东西，也要提取通过角色行为和叙述中透露的隐性世界观元素。

提取范围：
- 有名有姓的角色（非路人）
- 有具体名称的地点/设施
- 有具体名称的组织/势力
- 新的规则/设定/力量体系细节
- **废土生存经验/文化习俗**：角色展示的生存技巧、经验法则、风俗习惯、口头传统——即使没有正式名称，也需要作为文化设定记录。例如："辐射风暴中的标准骑行姿态"、"核爆闪光三秒计数法"、"废土拾荒者的经验法则"

以 JSON 格式输出：
```json
{{
  "new_entities": [
    {{"type": "character", "name": "角色名", "description": "身份/特征一句话描述"}},
    {{"type": "location", "name": "地名", "description": "位置/特征一句话描述"}},
    {{"type": "organization", "name": "组织名", "description": "性质/特征一句话描述"}},
    {{"type": "setting", "name": "设定名", "description": "规则/特征一句话描述"}},
    {{"type": "culture", "name": "习俗/经验名称", "description": "具体内容一句话描述"}}
  ]
}}
```
如果没有新实体，返回空的 new_entities 数组。"""

        result = self.run(
            user_message=user_msg,
            save_category="briefs",
            save_prefix=f"new_entities_ch{chapter_index:04d}",
        )

        try:
            data = json.loads(result.content)
            return data.get("new_entities", [])
        except json.JSONDecodeError:
            import re
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', result.content)
            if match:
                try:
                    data = json.loads(match.group(1))
                    return data.get("new_entities", [])
                except json.JSONDecodeError:
                    pass
        return []

    def _parse_changes(self, content: str) -> dict:
        """从 LLM 输出中提取 JSON"""
        try:
            # 尝试直接解析
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取 ```json 代码块
            import re
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
        return {}

    def _apply_changes(self, changes: dict, chapter_id: str):
        """将解析出的变化写入 SQLite"""
        # 角色状态变化
        for cc in changes.get("character_changes", []):
            status = {cc.get("field", "unknown"): cc.get("new_value", "")}
            self.sqlite.upsert_character_state(
                self.novel_id, chapter_id, cc.get("name", "unknown"), status
            )

        # 新伏笔
        for fs in changes.get("new_foreshadows", []):
            self.sqlite.add_foreshadowing(
                self.novel_id,
                fs.get("description", ""),
                fs.get("planted_chapter", chapter_id),
                fs.get("expected_resolve"),
            )

        # 回收伏笔
        for fid in changes.get("resolved_foreshadows", []):
            self.sqlite.resolve_foreshadowing(int(fid), chapter_id)

        # 世界变化
        for wc in changes.get("world_changes", []):
            self.sqlite.add_world_state_change(
                self.novel_id, chapter_id,
                wc.get("category", "general"),
                wc.get("description", ""),
            )

        # 冲突更新
        for cu in changes.get("conflict_updates", []):
            self.sqlite.upsert_conflict(
                self.novel_id,
                cu.get("name", ""),
                cu.get("involved_characters", ""),
                cu.get("status", ""),
                chapter_id,
            )
