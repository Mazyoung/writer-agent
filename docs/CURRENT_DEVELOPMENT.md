# Current Development

## Current Stage

E07.8 current state / persistence 2.0 is implemented. Present truth is now a deterministic `tracking/current_state.md` report plus an exact SQLite projection; historical truth remains Markdown Atomic Facts plus fact-only Chroma.

## Production Path

```text
main.py write
→ ChapterWorkflowRunner (deterministic novel/chapter thread)
→ preflight → load_current_state (initialize/migrate/validate/hash-repair SQLite)
→ load_chapter_intent → plan_chapter
  → Atomic Fact search → bounded source-paragraph expansion
→ review_plan → parse_plan_decision
  → PASS: write_draft → style_edit → save_styled
    → Chapter Review #1 (one semantic Decision + State Delta + Fact Digest)
      → PASS: deterministic current-state commit
        → current_state.md + SQLite v2 + completion marker/hash
        → chapter_sources.md → Fact Digest → Atomic Facts → Chroma
      → NEEDS_REVISION (L1): Auto Revision ×1 → save → Review #2
        → PASS: same commit/derived path
        → non-PASS: Human prose edit → new Review #1 cycle
  → non-PASS: Human plan edit → Plan Review again

UNKNOWN/malformed decisions, malformed State Delta, base-hash conflict,
and runtime/API/database/disk errors → ERROR / fail-closed
post-commit source/digest/index error → completed_with_warnings (no rollback)
```

## Completed

- One prose Review remains the only semantic pass for Review Decision, State Delta, and Fact Digest; no state-summary or Markdown-generation LLM call was added.
- `tracking/current_state.md` is a strict schema-v2 generated snapshot for characters, normalized relationships, items, cultivation, stable-ID foreshadows, and current chapter metadata.
- `current_state.md` ignores `_edited.md` overrides. Corrections must be made through production inputs and deterministic rederivation.
- SQLite v2 stores an exact, novel-isolated projection and supports character location, relationship, item holder, cultivation, pending/stale foreshadow, and current-chapter queries.
- SQLite stores the exact Markdown SHA-256 and is rebuilt from canonical Markdown when missing/stale.
- Existing novels migrate deterministically from the four split tracking files plus factual legacy SQLite foreshadows. Legacy data remains in place; weak heuristic character JSON and planned Book/Volume foreshadows are not promoted to factual truth.
- New novels initialize an empty chapter-0 current-state report/projection.
- The Chapter Graph checkpoints one state snapshot/hash after preflight and routes that same snapshot through retrieval, Planner, Plan Reviewer, prose Reviewer, and optimistic commit validation.
- Successful commit requires Markdown, SQLite transaction, and completion marker with matching hash. Handled failures restore old Markdown, roll back SQLite, remove the marker, and block provenance/Fact Digest/RAG.
- Planner still curates Writer context through the reviewed Chapter Plan. Writer does not receive complete current state or full Book/Volume plans directly.
- Fact Digest/Chroma remain history-only; no current-state or full styled-chapter vectors were introduced.
- CI includes live E07.6 checkpoint, E07.7 Atomic Fact RAG, and E07.8 current-state suites.

## Important Files

- `src/storage/document_formats.py` — strict Current State and typed State Delta contracts.
- `src/storage/current_state_store.py` — migration, apply, hash reconciliation, and cross-store commit owner.
- `src/storage/sqlite_store.py` — v2 current-state schema/projection/exact queries.
- `src/agents/state_manager/state_manager.py` / prompt — one Review plus deterministic delta commit.
- `src/workflows/chapter_workflow.py` — checkpointed state snapshot and PASS-gated commit/derived routing.
- `src/agents/author/chapter_planner.py` — unified current-state consumption and Writer boundary.
- `src/workflows/retrieval_service.py` — unified current-state query hints plus Atomic Fact retrieval.
- `tests/test_e07_8.py` — focused no-paid-call E07.8 invariants.
- `docs/E07_8_CURRENT_STATE_PERSISTENCE_2_REPORT.md` — implementation and limitation report.

## Known Issues

- Human collaboration is file-based CLI UX; generated `current_state.md` is intentionally not directly editable.
- Abrupt process death cannot be a true atomic transaction across filesystem and SQLite. Markdown plus marker hash are authoritative; SQLite is hash-repaired on next access.
- Legacy migration preserves representable structured fields only and does not reinterpret prose/plans through an LLM.
- Item names remain current item identity because the Review State Delta does not define item IDs.
- `src/storage/chroma_store.py`, `src/storage/rag_maintenance.py`, and the four split state reports remain legacy compatibility paths, not production current-state ownership.
- Historical `tests/test_proposal_override.py` imports the removed `src.core.orchestrator`, so the repository-wide suite currently stops during collection independently of E07.8.

## Verification

Executed with the Conda `writer` environment on 2026-08-05:

- `python -m compileall -q main.py src` — passed.
- `python -m pytest -q tests/test_e07_8.py` — 17 passed.
- `python -m pytest -q tests/test_e07_6.py tests/test_e07_7.py tests/test_e07_8.py` — 36 passed.
- Applicable E06 decision/parser/fail-closed/snapshot tests — 21 passed.
- `tests/test_chapter_plan.py tests/test_planning_foundation.py` — 20 passed.
- Pre-change full-suite baseline — collection error in historical `tests/test_proposal_override.py` because `src.core.orchestrator` no longer exists.
- Configured CI regression selection — 84 passed (one Chroma dependency deprecation warning).
- Final complete-suite rerun — same pre-existing collection error in historical `tests/test_proposal_override.py`; no E07.8 test was collected before that repository-level stop.

## Next Task

E07.9 — broad Story Savepoint / Rollback, as defined in `docs/E07_REMAINING_PLAN.md`.

Do not begin E07.9 as part of this checkpoint.

## Out of Scope

- Multi-chapter Story Savepoint/Rollback, abandoned timelines, branch switching, or Chroma binary snapshots.
- Any additional Agent/LLM state summarizer or LLM-authored full Current State.
- Full-chapter RAG or current-state vectors.
- Silent L2/L3 Book/Volume plan repair or `plot_structure.md` restoration.
- Direct generated-report edits as production truth.

## Last Verified

- Base revision: `e08ed35` (`E07.7`), 2026-08-05.
- E07.8 working tree: uncommitted implementation, verification in progress on 2026-08-05.
