# Current Development

## Current Stage

E07.9 Final Closure + CI Baseline Freeze is complete in the working tree.

The chapter architecture remains:

~~~text
Planning → Creation / Review → Final Author Approval
→ CANONICAL_COMMITTED → Derivation → DERIVED_READY
~~~

No Story Snapshot, Jump, Branch, Savepoint, Restore, or Rollback work has started.

## E07.9 Production Closure

- The Python and Markdown call chain uses canonical_source_path / Canonical Source from Chapter Workflow through StateManager and CurrentStateStore.
- SQLite retains the compatibility column name styled_source_path; its value is the canonical source path and no naming-only migration was added.
- Derivation receives Canonical Prose, Previous Current State, and the current ACTIVE Volume Plan.
- Canonical Prose remains the only source for StateDelta, Fact Digest / Atomic Facts, and Current State. Volume Plan is restricted to the advisory VolumeProgress decision.
- close-volume ignores CONTINUE / READY_TO_CLOSE / UNKNOWN advice, but refuses closure unless the latest canonical chapter checkpoint is DERIVED_READY; the error directs the user to derivation repair.
- Volume Plan validation rejects structural chapter assignment fields/headings/tables, while preserving arbitrary human sections and notes, including prose that merely mentions “逐章”.

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

- E07.9 focused: 11 passed, 8 subtests passed.
- Architecture CI Baseline: 96 passed, 8 subtests passed.
- Full retained suite: 130 passed, 8 subtests passed.
- Full pytest collection completes without retired Orchestrator import errors.

## Next Task

E07.10 should use destructive/integration validation against real temporary novel projects for Story Snapshot / Jump / Branch / Savepoint / Restore. Keep only necessary local safety tests; do not expand another large implementation-detail contract suite.
