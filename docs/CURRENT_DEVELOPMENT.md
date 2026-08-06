# Current Development

## Current Stage

E07.9.1-A — Human Author Mode front half and configuration foundation is complete in the working tree.

The two supported chapter modes now share one checkpointed Chapter Graph:

~~~text
agent: Intent(optional) → Current State / Historical RAG → existing Plan / Review /
       Writer / Stylist / Full Prose Review / Final Approval → Canonical → Derivation

human: Intent(required) → Current State / Historical RAG → Relevant Writing Context
       → human_writing interrupt → WAITING_HUMAN
~~~

Human Candidate submission, consistency-only review, canonical commit, and derivation are not implemented yet. No Story Snapshot, Jump, Branch, Savepoint, Restore, or Rollback work has started.

## E07.9 Production Closure

- The Python and Markdown call chain uses canonical_source_path / Canonical Source from Chapter Workflow through StateManager and CurrentStateStore.
- SQLite retains the compatibility column name styled_source_path; its value is the canonical source path and no naming-only migration was added.
- Derivation receives Canonical Prose, Previous Current State, and the current ACTIVE Volume Plan.
- Canonical Prose remains the only source for StateDelta, Fact Digest / Atomic Facts, and Current State. Volume Plan is restricted to the advisory VolumeProgress decision.
- close-volume ignores CONTINUE / READY_TO_CLOSE / UNKNOWN advice, but refuses closure unless the latest canonical chapter checkpoint is DERIVED_READY; the error directs the user to derivation repair.
- Volume Plan validation rejects structural chapter assignment fields/headings/tables, while preserving arbitrary human sections and notes, including prose that merely mentions “逐章”.

## E07.9.1-A Human Author Mode Front Half

- `CHAPTER_MODE=agent|human` is the only creation-mode setting; it defaults to `agent`, rejects invalid values, and is frozen into new checkpoint state. Missing mode in old checkpoints retains Agent semantics.
- Agent mode routes from `load_chapter_intent` to the unchanged `plan_chapter` path.
- Human mode requires a non-empty Chapter Intent, reuses `ChapterRetrievalService`, and makes Intent the primary query input. Current State entities and the Volume Plan remain supplemental retrieval context.
- `RAG_TOP_K` now configures the existing shared positive-integer top-k, with the compatible default of 5 for both modes.
- Human retrieval persists `tracking/writing_context_chNNNN.md` as a generated, non-canonical report containing Intent, Current State, relevant Atomic Facts, bounded canonical prose expansion, and supplemental Author Knowledge.
- The Human front half stops at a `human_writing` interrupt and returns `WAITING_HUMAN` with the report path. Planner, Plan Review, Writer, Stylist, prose review, canonical commit, and derivation are not entered.

## Architecture CI Baseline

The push/PR gate is frozen around stable functional contracts and safety invariants:

- planning hierarchy, plan review, and human interrupt/resume;
- Review non-PASS remains human-controlled; Review PASS requires final author approval;
- canonical create-once/overwrite protection and canonical-only historical reading;
- canonical commit followed by derivation, visible derivation failure, idempotent repair, and DERIVED_READY;
- deterministic Current State, Fact Digest / Atomic Fact RAG, and Author RAG fail-closed behavior;
- advisory VolumeProgress, close-volume consistency guard, next-volume, approve-volume, and non-chapterized Volume Plans.

Tests tied to the retired src.core.orchestrator, automatic revision, PASS-to-direct-commit, styled-as-canonical, E07.2 graph node names/topology, and old multi-Markdown tracking rollback behavior were removed. Reusable parser, storage, RAG, FakeLLM, mock, and fixture coverage was retained or updated to current entry points.

## Verification

- E07.9.1-A focused mocked/no-paid-call tests: 8 passed.
- Combined E07.9.1-A + existing Agent workflow / RAG / E07.8 / E07.9 / Chapter Plan focused regression: 59 passed (8 new + 51 existing).
- Previous Architecture CI Baseline remains: 96 passed, 8 subtests passed.
- Previous full retained suite remains: 130 passed, 8 subtests passed.

## Next Task

E07.9.1-B — Human Candidate Submission + Consistency-only Review + Final Approval → existing Canonical/Derivation
