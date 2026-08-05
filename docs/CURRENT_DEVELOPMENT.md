# Current Development

## Current Stage

E07.6 closure and E07.7 are complete. The Chapter Graph retains the reviewed single-chapter loop, while long-term historical retrieval now uses Markdown Atomic Facts plus a fact-only Chroma collection and on-demand source-prose expansion.

## Production Path

```text
main.py write
→ ChapterWorkflowRunner (deterministic novel/chapter thread)
→ preflight → load_chapter_intent → plan_chapter
  → Atomic Fact search → bounded source-paragraph expansion
→ review_plan → parse_plan_decision
  → PASS: write_draft → style_edit → save_styled
    → Chapter Review #1
      → PASS: canonical commit
        → chapter_sources.md → Fact Digest → Atomic Facts → Chroma
      → NEEDS_REVISION (L1): Auto Revision ×1 → save → Review #2
        → PASS: same commit/derived path
        → non-PASS: Human prose edit → new Review #1 cycle
  → non-PASS: Human plan edit → Plan Review again

UNKNOWN/malformed decisions and runtime/API/database/disk errors → ERROR / fail-closed
post-commit source/digest/index error → completed_with_warnings (no rollback)
```

## Completed

- E07.6 closure: runner checks the completion marker before checkpoint reuse. Pending nodes/interrupts and Resume continue the original thread; terminal `ERROR`/`STOPPED_NON_PASS` without a marker is automatically deleted with `SqliteSaver.delete_thread()` so ordinary Generate can start again.
- A completion marker always blocks ordinary Generate. No manual checkpoint SQLite deletion is required.
- Review now receives stable `[Pxxxx]` paragraph anchors. The same Review call emits the structured Fact Digest; no additional Fact Digest LLM call was added.
- Markdown Fact Digest records stable `FACT-NNNN-NNN` entries with Chapter, Fact Type, Entities, Paragraph Range, and Fact Text.
- Chroma production collection `atomic_facts_v2` embeds Fact Text only. Full styled chapters are source material, not a global vector corpus.
- Retrieval searches only earlier FACTs under novel/branch isolation, then expands only a valid matched paragraph range plus one adjacent paragraph on each side.
- Planner sees long-term plans, future constraints, candidate facts, and bounded source excerpts. Writer/Auto Revision see only the approved Chapter Plan, selected current context, Planner-adopted facts/excerpts, and curated constraints; they never load full Book/Volume plans.
- `sources/chapter_NNNN/chapter_sources.md` is generated after canonical commit and records Intent, Book/Volume source paths, adopted FACT IDs, future constraints, and actual expanded prose locations.
- Fact Digest, `chapter_sources.md`, and Chroma are post-commit derived data. Failure returns `completed_with_warnings`, populates `derived_state_errors`, and is printed by CLI without deleting the completion marker.
- `rag-index` backfills/rebuilds from completed chapters' Markdown Fact Digests. New/old collection names prevent mixed retrieval; rebuild also removes that novel/branch's legacy `chapter_chunks` records.
- Legacy six-section Fact Digests are deterministically projected to Atomic Facts without a JSON copy. Their paragraph range remains unknown, so they do not expand source prose until regenerated.

## Important Files

- `src/workflows/chapter_runner.py` — closure-safe checkpoint start/resume behavior.
- `src/workflows/chapter_workflow.py` — Chapter Graph, provenance node, and non-rollback derived pipeline.
- `src/storage/document_formats.py` — `AtomicFact`, `FactDigest`, and approved Chapter Plan context fields.
- `src/storage/atomic_fact_store.py` — versioned fact-only Chroma storage.
- `src/workflows/retrieval_service.py` — FACT search, local source expansion, and trace persistence.
- `src/storage/rag_maintenance_v2.py` — Fact Digest backfill/rebuild and legacy cleanup.
- `src/agents/state_manager/state_manager.py` / `src/config/prompts/state_manager.txt` — paragraph anchors and Atomic Fact extraction contract.
- `src/agents/author/chapter_planner.py` / planner prompt — fact adoption and Writer boundary.
- `tests/test_e07_6.py`, `tests/test_e07_7.py` — focused checkpoint/RAG invariants without paid calls.
- `README.md`, `ARCHITECTURE.md` — current production semantics.

## Known Issues

- Human collaboration is file-based CLI UX; there is no interactive editor or UI.
- Automatic prose revision is limited to L1 and deliberately does not repair Chapter/Volume/Book plans.
- Plan Review and each prose Review remain LLM calls; Atomic Facts rely on the existing deterministic Markdown parser rather than structured-output API enforcement.
- Legacy digests can be indexed, but unknown paragraph ranges deliberately disable prose expansion.
- `src/storage/chroma_store.py` and `src/storage/rag_maintenance.py` remain legacy E04 compatibility code; production imports use `AtomicFactStore` and `rag_maintenance_v2`.

## Verification

Executed with the Conda `writer` environment on 2026-08-05:

- `python -m py_compile` for changed Python modules — passed.
- Chapter Graph build/topology smoke — passed, including post-commit `save_chapter_sources` and fact-only `rag_index` nodes.
- `python -m unittest tests.test_e07_6 tests.test_e07_7 -v` — 19 focused tests passed (mocked/no paid generation).
- Closure checks cover terminal ERROR retry, terminal STOPPED_NON_PASS retry, completed marker blocking, pending interrupt, and Resume behavior.
- RAG checks cover required Atomic Fact fields, legacy projection, Fact Text-only embedding payload, local paragraph expansion, Planner/Writer boundary, provenance filtering, maintenance path, and non-rollback derived failure.

## Next Task

E07.8 — current state / persistence 2.0, as defined in `docs/E07_REMAINING_PLAN.md`.

Do not begin E07.8 as part of this checkpoint.

## Out of Scope

- Do not implement E07.8 current-state/persistence 2.0 or change Markdown/SQLite ownership early.
- Do not implement E07.9 Story Savepoint/Rollback or move novel/volume management into the Chapter Graph.
- Do not let human edits bypass their relevant Review gate.
- Do not use the closure retry reset for pending checkpoints, interrupts, completed chapters, or explicit Resume.

## Last Verified

- Base revision: `aee215d` (`E07.6`), 2026-08-05.
- E07.6 closure + E07.7 working tree: uncommitted implementation, verified on 2026-08-05.
