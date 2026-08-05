# E07.8 Current State / Persistence 2.0 — Implementation Report

## Scope

E07.8 separates present truth from historical memory without adding an Agent or LLM call:

```text
one prose Review
  → Review Decision + State Delta + Fact Digest

State Delta
  → tracking/current_state.md
  → state.db exact query projection

Fact Digest
  → Markdown Atomic Facts
  → Chroma atomic_facts_v2
```

LangGraph checkpoints remain unfinished single-chapter execution recovery. E07.9 story savepoints/rollback were not implemented.

## What changed

### Unified generated current state

`src/storage/document_formats.py` now defines strict typed models for characters, relationships, items, cultivation, foreshadows, current chapter metadata, a complete `CurrentState`, and typed State Delta records.

`CurrentState.to_markdown()` produces one deterministic schema-v2 report with fixed sections, stable ordering, escaped table cells, duplicate validation, and a schema/through-chapter header. `CurrentState.from_markdown()` rejects missing sections, malformed tables, invalid statuses/IDs, duplicates, and chapter-order inconsistencies.

The report is automatic output. `FileStore.load_generated_tracking_doc()` reads only the plain Markdown and deliberately ignores `current_state_edited.md`.

### Deterministic State Delta application

The Reviewer still emits one analysis containing Decision, State Delta, and Fact Digest. `StateDelta.from_analysis()` requires all five state domains and all three item action subsections; explicit `- 无` is a valid no-op while missing/malformed content fails closed.

Stable foreshadow references use `Fxxxx`; new entries use `NEW`. Existing legacy description references are exact-match compatibility only. Substring matching is no longer used by production current-state persistence.

`StateManager.update_tracking_docs()` now parses the delta, loads the checkpointed base state, applies updates deterministically, renders the candidate report, and delegates commit to `CurrentStateStore`. The incomplete working-tree design that expected an LLM-authored complete Current State was removed.

### SQLite v2 exact projection

`src/storage/sqlite_store.py` adds novel-isolated tables for:

- current-state metadata/hash;
- character current state;
- normalized relationships;
- item/current-holder state;
- cultivation state;
- foreshadow status and last-progress chapter;
- current chapter metadata.

A complete snapshot replaces one novel's rows inside a SQLite transaction. Exact APIs support character, relationship, item holder, cultivation, unresolved/stale foreshadow, and current chapter queries. The exact canonical Markdown SHA-256 is stored in `current_state_meta`; a mismatch triggers deterministic projection rebuild before production reads.

Legacy tables and methods remain available for migration compatibility and were not destructively changed.

### Migration and initialization

`src/storage/current_state_store.py` performs a one-time, no-LLM migration when `current_state.md` is absent:

- current relationship/item/cultivation/character fields come from the four legacy tracking Markdown documents;
- factual legacy SQLite foreshadows are assigned stable `Fxxxx` IDs;
- completion markers establish `through_chapter`;
- weak heuristic `last_seen` JSON rows and planned Book/Volume foreshadows are not promoted to factual truth;
- legacy files/tables remain untouched.

New-novel initialization creates an empty chapter-0 schema-v2 report and matching SQLite projection after world/Book/Volume plans succeed.

### Chapter Graph and context boundaries

A `load_current_state` node now runs immediately after preflight. It initializes/migrates/validates the report, repairs SQLite if needed, and checkpoints the exact report text plus hash.

That same snapshot is supplied to:

- historical retrieval query construction;
- Chapter Planner;
- Plan Reviewer;
- prose Reviewer;
- optimistic base-hash validation during commit.

Planner curates relevant present-state content into Chapter Plan Part B. Writer still receives only the reviewed Chapter Plan and does not receive full current state or Book/Volume plans directly.

The unrelated uncommitted Author RAG/context-governance files were not refactored; retrieval integration was merged around them.

## Commit and failure contract

A successful E07.8 commit requires:

1. explicit prose `PASS`;
2. complete, valid State Delta;
3. unchanged checkpointed base-state hash;
4. valid complete candidate state;
5. canonical Markdown write;
6. SQLite projection transaction;
7. completion marker containing chapter, schema, and current-state hash.

Handled failure rolls back SQLite, restores previous Markdown, and removes a newly written marker. Rollback problems are reported as degraded errors; they are never converted into success or `NEEDS_REVISION`.

Commit failure blocks `chapter_sources.md`, Fact Digest, and Chroma. Post-commit failures in those historical/diagnostic artifacts retain `completed_with_warnings` and do not revoke accepted prose/current state.

## Tests

`tests/test_e07_8.py` covers:

- deterministic/escaped Markdown round trips;
- strict missing/duplicate rejection and generated-report edit policy;
- all State Delta domains, no-op deltas, malformed/duplicate failure, holder mismatch, and unknown foreshadow IDs;
- exact SQLite queries, novel isolation, stale-foreshadow queries, and hash repair;
- deterministic legacy migration and stable foreshadow IDs;
- matching Markdown/SQLite/marker hashes;
- base-hash and SQLite failure rollback;
- StateManager fail-closed behavior;
- checkpointed snapshot loading and Plan Reviewer context.

CI now includes the live E07.6 checkpoint, E07.7 fact-RAG, and E07.8 current-state suites instead of relying only on historical E06/E07.2 selections.

## Verification

Executed with the Conda `writer` environment on 2026-08-05:

- pre-change `tests/test_e07_6.py tests/test_e07_7.py`: 19 passed;
- focused `tests/test_e07_8.py`: 17 passed;
- combined E07.6–E07.8: 36 passed;
- applicable E06 decision/parser/fail-closed/snapshot tests: 21 passed;
- chapter-plan and planning-foundation tests: 20 passed;
- `python -m compileall -q main.py src`: passed.

The configured CI regression selection passed 84 tests (with one Chroma dependency deprecation warning). The final complete suite reproduced the pre-change collection error because historical `tests/test_proposal_override.py` imports removed `src.core.orchestrator`; this is independent of E07.8 and prevents repository-wide collection.

## Remaining limitations

- Cross-filesystem/SQLite atomicity cannot be guaranteed across abrupt process death; Markdown plus marker hash remain the recovery authority and SQLite is hash-repaired on the next read.
- Legacy migration can only preserve fields represented in existing data; it does not use an LLM to reinterpret prose or plans.
- Item names remain item identity because the existing State Delta contract has no item IDs.
- Human collaboration remains file-based; generated `current_state.md` is deliberately not an edit override surface.
- E07.9 broad story savepoint/rollback, branch timelines, and Chroma snapshots remain out of scope.
