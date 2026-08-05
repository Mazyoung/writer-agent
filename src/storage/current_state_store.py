"""Deterministic owner for generated current-state Markdown and SQLite."""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path

from src.storage.document_formats import (
    CharacterRelationships,
    CharacterStateList,
    CultivationSystem,
    CurrentChapterMeta,
    CurrentCharacterState,
    CurrentCultivationState,
    CurrentForeshadowState,
    CurrentItemState,
    CurrentRelationshipState,
    CurrentState,
    ItemsEquipment,
    StateCommitResult,
    StateDelta,
)
from src.storage.file_store import FileStore
from src.storage.sqlite_store import SQLiteStore


class CurrentStateStore:
    """Keep Markdown authority and its exact SQLite query projection coherent."""

    REPORT_NAME = "current_state"

    def __init__(self, novel_id: str, file_store: FileStore,
                 sqlite: SQLiteStore):
        self.novel_id = novel_id
        self.fs = file_store
        self.sqlite = sqlite

    @staticmethod
    def content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def path(self) -> Path:
        return self.fs.root / "tracking" / "current_state.md"

    def load_text(self) -> str:
        return self.fs.load_generated_tracking_doc(self.REPORT_NAME) or ""

    def load(self) -> CurrentState:
        text = self.load_text()
        if not text:
            raise FileNotFoundError("tracking/current_state.md does not exist")
        return CurrentState.from_markdown(text)

    def _completed_through_chapter(self) -> int:
        """Infer a migration base; production completion comes from prose identity."""
        chapters = []
        for path in (self.fs.root / "states").glob("chapter_*_derived"):
            match = re.fullmatch(r"chapter_(\d{4})_derived", path.name)
            if match:
                chapters.append(int(match.group(1)))
        if chapters:
            return max(chapters)
        canonical = [
            int(match.group(1))
            for path in self.fs.list_chapters()
            if (match := re.fullmatch(r"chapter_(\d{4})\.md", path.name))
        ]
        if canonical:
            return max(canonical)
        # Read-only compatibility for pre-closure migrations. New production
        # never creates or interprets this as canonical completion.
        for path in (self.fs.root / "states").glob("chapter_*_completed"):
            match = re.fullmatch(r"chapter_(\d{4})_completed", path.name)
            if match:
                chapters.append(int(match.group(1)))
        return max(chapters, default=0)

    @staticmethod
    def _chapter_number(value: str) -> int:
        numbers = re.findall(r"\d+", str(value or ""))
        return int(numbers[-1]) if numbers else 0

    @staticmethod
    def _relationship_names(value: str) -> tuple[str, str] | None:
        names = [name.strip() for name in re.split(r"\s*(?:↔|<->)\s*", value)]
        return tuple(names) if len(names) == 2 and all(names) else None

    def _migrate_legacy_state(self) -> CurrentState:
        through = self._completed_through_chapter()
        state = CurrentState(through_chapter=through)

        relationships_text = self.fs.load_tracking_doc("character_relationships") or ""
        seen_relationships: set[tuple[str, str]] = set()
        for entry in CharacterRelationships.from_markdown(relationships_text).entries:
            pair = self._relationship_names(entry.characters)
            if not pair:
                continue
            pair = tuple(sorted(pair))
            if pair in seen_relationships:
                continue
            seen_relationships.add(pair)
            state.relationships.append(CurrentRelationshipState(
                character_a=pair[0], character_b=pair[1],
                relation_type=entry.relation_type,
                current_state=entry.current_state,
                attitude=entry.attitude,
                last_interaction_chapter=self._chapter_number(entry.last_interaction),
            ))

        items_text = self.fs.load_tracking_doc("items_equipment") or ""
        parsed_items = ItemsEquipment.from_markdown(items_text)
        combined = [*parsed_items.protagonist_items, *parsed_items.world_items]
        seen_items: set[str] = set()
        for entry in combined:
            if not entry.name or entry.name in seen_items:
                continue
            seen_items.add(entry.name)
            state.items.append(CurrentItemState(
                name=entry.name, holder=entry.owner, status=entry.status,
                source=entry.source,
                acquired_chapter=self._chapter_number(entry.acquired_chapter),
                attributes=entry.attributes, notes=entry.notes,
                updated_chapter=self._chapter_number(entry.acquired_chapter),
            ))

        cultivation_text = self.fs.load_tracking_doc("cultivation_system") or ""
        for entry in CultivationSystem.from_markdown(cultivation_text).character_states:
            state.cultivation.append(CurrentCultivationState(
                name=entry.name, current_stage=entry.current_stage,
                distance_to_next=entry.distance_to_next,
                special_ability=entry.special_ability,
                limitation=entry.limitation,
                updated_chapter=self._chapter_number(entry.updated_chapter),
            ))

        characters_text = self.fs.load_tracking_doc("character_states") or ""
        for entry in CharacterStateList.from_markdown(characters_text).entries:
            state.characters.append(CurrentCharacterState(
                name=entry.name, alive_status=entry.alive_status,
                location=entry.location, physical_state=entry.physical_state,
                identity_status=entry.identity_status,
                updated_chapter=self._chapter_number(entry.updated_chapter),
            ))

        for row in self.sqlite.get_legacy_foreshadows(self.novel_id):
            raw_status = str(row.get("status", "pending")).upper()
            status = "OPEN" if raw_status == "PENDING" else raw_status
            if status not in {"OPEN", "RESOLVED", "ABANDONED"}:
                continue
            state.foreshadows.append(CurrentForeshadowState(
                foreshadow_id=f"F{int(row['id']):04d}",
                description=str(row.get("description", "")),
                status=status,
                planted_chapter=self._chapter_number(row.get("planted_chapter", "")),
                expected_resolve=str(row.get("expected_resolve_chapter", "") or ""),
                last_progress_chapter=max(
                    self._chapter_number(row.get("planted_chapter", "")),
                    self._chapter_number(row.get("resolved_chapter", "")),
                ),
                resolved_chapter=self._chapter_number(row.get("resolved_chapter", "")),
            ))

        state.chapter = CurrentChapterMeta(chapter_index=through)
        state.validate()
        return state

    def initialize_empty(self) -> tuple[CurrentState, str, str]:
        if self.load_text():
            return self.ensure_initialized()
        state = CurrentState()
        text = state.to_markdown()
        digest = self.content_hash(text)
        self.fs.save_generated_tracking_doc(self.REPORT_NAME, text)
        self.sqlite.replace_current_state_projection(
            self.novel_id, state, digest, commit=True)
        return state, text, digest

    def ensure_initialized(self) -> tuple[CurrentState, str, str]:
        text = self.load_text()
        if not text:
            state = self._migrate_legacy_state()
            text = state.to_markdown()
            self.fs.save_generated_tracking_doc(self.REPORT_NAME, text)
        state = CurrentState.from_markdown(text)
        digest = self.content_hash(text)
        if not self.sqlite.current_state_projection_matches(self.novel_id, digest):
            self.sqlite.replace_current_state_projection(
                self.novel_id, state, digest, commit=True)
        return state, text, digest

    def ensure_sqlite_projection(self) -> CurrentState:
        return self.ensure_initialized()[0]

    @staticmethod
    def _set_changed(current: str, incoming: str) -> str:
        return incoming if incoming else current

    def apply_delta(self, base: CurrentState, delta: StateDelta,
                    chapter_index: int, title: str, word_count: int,
                    canonical_source_path: str) -> CurrentState:
        if chapter_index <= base.through_chapter:
            raise ValueError(
                f"Chapter {chapter_index} cannot advance Current State through "
                f"chapter {base.through_chapter}")
        candidate = copy.deepcopy(base)

        relationships = {
            entry.normalized_key(): entry for entry in candidate.relationships
        }
        for change in delta.relationships:
            key = tuple(sorted((change.character_a, change.character_b)))
            entry = relationships.get(key)
            if entry is None:
                entry = CurrentRelationshipState(character_a=key[0], character_b=key[1])
                candidate.relationships.append(entry)
                relationships[key] = entry
            entry.relation_type = self._set_changed(entry.relation_type, change.relation_type)
            entry.current_state = self._set_changed(entry.current_state, change.current_state)
            entry.attitude = self._set_changed(entry.attitude, change.attitude)
            entry.last_interaction_chapter = chapter_index

        items = {entry.name: entry for entry in candidate.items}
        for change in delta.items:
            entry = items.get(change.name)
            if change.action == "GAIN":
                if entry is None:
                    entry = CurrentItemState(name=change.name)
                    candidate.items.append(entry)
                    items[change.name] = entry
                entry.holder = change.holder
                entry.status = change.status or "可用"
                entry.source = change.source or entry.source
                if not entry.acquired_chapter:
                    entry.acquired_chapter = chapter_index
            else:
                if entry is None:
                    raise ValueError(f"Unknown item in State Delta: {change.name}")
                if change.old_holder and change.old_holder != entry.holder:
                    raise ValueError(
                        f"Item holder mismatch for {change.name}: "
                        f"expected {entry.holder}, got {change.old_holder}")
                entry.holder = ""
                entry.status = "已消耗" if change.action == "CONSUME" else "已失去"
                entry.notes = change.reason or entry.notes
            entry.updated_chapter = chapter_index

        cultivation = {entry.name: entry for entry in candidate.cultivation}
        for change in delta.cultivation:
            entry = cultivation.get(change.name)
            if entry is None:
                entry = CurrentCultivationState(name=change.name)
                candidate.cultivation.append(entry)
                cultivation[change.name] = entry
            entry.current_stage = self._set_changed(entry.current_stage, change.current_stage)
            entry.distance_to_next = self._set_changed(
                entry.distance_to_next, change.distance_to_next)
            entry.special_ability = self._set_changed(
                entry.special_ability, change.special_ability)
            entry.limitation = self._set_changed(entry.limitation, change.limitation)
            entry.updated_chapter = chapter_index

        characters = {entry.name: entry for entry in candidate.characters}
        for change in delta.characters:
            entry = characters.get(change.name)
            if entry is None:
                entry = CurrentCharacterState(name=change.name)
                candidate.characters.append(entry)
                characters[change.name] = entry
            entry.alive_status = self._set_changed(entry.alive_status, change.alive_status)
            entry.location = self._set_changed(entry.location, change.location)
            entry.physical_state = self._set_changed(
                entry.physical_state, change.physical_state)
            entry.identity_status = self._set_changed(
                entry.identity_status, change.identity_status)
            entry.updated_chapter = chapter_index

        foreshadows = {entry.foreshadow_id: entry for entry in candidate.foreshadows}
        descriptions = {entry.description: entry for entry in candidate.foreshadows}
        next_id = max(
            (int(re.search(r"\d+", entry.foreshadow_id).group())
             for entry in candidate.foreshadows), default=0) + 1
        for change in delta.foreshadows:
            if change.reference == "NEW":
                if not change.description:
                    raise ValueError("NEW foreshadow is missing 描述")
                if change.description in descriptions:
                    raise ValueError(
                        f"Foreshadow already exists: {change.description}")
                entry = CurrentForeshadowState(
                    foreshadow_id=f"F{next_id:04d}", description=change.description,
                    planted_chapter=chapter_index,
                )
                next_id += 1
                candidate.foreshadows.append(entry)
                foreshadows[entry.foreshadow_id] = entry
                descriptions[entry.description] = entry
            elif re.fullmatch(r"F\d{4,}", change.reference):
                entry = foreshadows.get(change.reference)
                if entry is None:
                    raise ValueError(
                        f"Unknown foreshadow ID: {change.reference}")
            else:
                entry = descriptions.get(change.reference)
                if entry is None:
                    raise ValueError(
                        f"Unknown legacy foreshadow description: {change.reference}")
            if change.description and change.description != entry.description:
                raise ValueError("Existing foreshadow descriptions cannot be renamed")
            entry.status = change.status
            entry.expected_resolve = change.expected_resolve or entry.expected_resolve
            entry.last_progress_chapter = chapter_index
            if change.status == "RESOLVED":
                entry.resolved_chapter = change.resolved_chapter or chapter_index
            elif change.resolved_chapter:
                raise ValueError("Only RESOLVED foreshadows may set 回收章节")

        candidate.through_chapter = chapter_index
        candidate.chapter = CurrentChapterMeta(
            chapter_index=chapter_index, title=title, word_count=word_count,
            canonical_source_path=canonical_source_path,
        )
        candidate.validate()
        return candidate

    def commit(self, base_sha256: str, candidate: CurrentState) -> StateCommitResult:
        result = StateCommitResult(success=False)
        marker = (
            self.fs.root / "states" /
            f"chapter_{candidate.through_chapter:04d}_derived"
        )
        previous_text = self.load_text()
        candidate_text = candidate.to_markdown()
        candidate_sha256 = self.content_hash(candidate_text)
        current_sha256 = self.content_hash(previous_text) if previous_text else ""

        if current_sha256 not in {base_sha256, candidate_sha256}:
            result.error_message = (
                "Current State base hash changed during checkpointed execution")
            return result
        if marker.exists() and current_sha256 != candidate_sha256:
            result.error_message = "Derived marker exists for a different Current State"
            return result

        wrote_markdown = False
        wrote_marker = False
        try:
            self.sqlite.begin_immediate()
            if current_sha256 != candidate_sha256:
                self.fs.save_generated_tracking_doc(self.REPORT_NAME, candidate_text)
                wrote_markdown = True
            self.sqlite.replace_current_state_projection(
                self.novel_id, candidate, candidate_sha256, commit=False)
            if not marker.exists():
                marker.write_text(
                    "Derivation success\n"
                    f"Chapter: {candidate.through_chapter}\n"
                    f"Current-State-SHA256: {candidate_sha256}\n"
                    f"Current-State-Schema: {candidate.schema_version}\n",
                    encoding="utf-8",
                )
                wrote_marker = True
            self.sqlite.commit()
        except Exception as exc:
            rollback_errors: list[str] = []
            try:
                self.sqlite.rollback()
            except Exception as rollback_exc:
                rollback_errors.append(
                    f"rollback SQLite failed: {type(rollback_exc).__name__}: {rollback_exc}")
            if wrote_markdown:
                try:
                    if previous_text:
                        self.fs.save_generated_tracking_doc(
                            self.REPORT_NAME, previous_text)
                    else:
                        self.path.unlink(missing_ok=True)
                except Exception as rollback_exc:
                    rollback_errors.append(
                        f"rollback current_state.md failed: "
                        f"{type(rollback_exc).__name__}: {rollback_exc}")
            if wrote_marker:
                try:
                    marker.unlink(missing_ok=True)
                except Exception as rollback_exc:
                    rollback_errors.append(
                        f"rollback derived marker failed: "
                        f"{type(rollback_exc).__name__}: {rollback_exc}")
            result.error_message = f"Current State commit failed: {type(exc).__name__}: {exc}"
            result.warnings.extend(rollback_errors)
            if rollback_errors:
                result.warnings.append("canonical state may be inconsistent")
            return result

        result.success = True
        result.changed_files = [
            "tracking/current_state.md",
            "state.db",
            f"states/chapter_{candidate.through_chapter:04d}_derived",
        ]
        return result
