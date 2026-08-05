# Current Development

## Current Stage

E07.6 is complete: the checkpointed Chapter Graph now owns the full single-chapter loop from optional Chapter Intent through Plan Review, writing, at most one automatic prose revision per review cycle, human edit/re-review, and safe canonical commit.

## Production Path

```text
main.py write
→ ChapterWorkflowRunner (deterministic novel/chapter thread)
→ preflight → load_chapter_intent → plan_chapter
→ review_plan → parse_plan_decision
  → PASS: write_draft → style_edit → save_styled
    → Chapter Review #1
      → PASS: canonical commit → Fact Digest → RAG
      → NEEDS_REVISION (L1): Auto Revision ×1 → save → Review #2
        → PASS: canonical commit → Fact Digest → RAG
        → non-PASS: Human prose edit → new Review #1 cycle
  → non-PASS: Human plan edit → Plan Review again

UNKNOWN/malformed decisions and runtime/API/database/disk errors → ERROR / fail-closed
```

## Completed

- Optional `chapter_intent` is accepted by `write --intent` (`--instructions` is a compatibility alias), persisted under `briefs/`, included in retrieval, and passed to ChapterPlanner.
- Every generated or human-edited Chapter Plan passes through `PlanReviewer` and deterministic `ReviewDecision` parsing before Writer.
- Failed Plan Review interrupts the same checkpoint; `outlines/chapter_plan_chNNNN_edited.md` is validated before resume and always returns to Plan Review.
- Prose Review #1 may route one L1 `NEEDS_REVISION` result through `DeepSeekWriter.revise_chapter`; Review #2 cannot route to automatic revision again.
- `HALT`, L2/L3 findings, or Review #2 non-PASS route to Human rather than automatic prose repair.
- `chapters/chapter_NNNN_styled_edited.md` is validated before resume; human prose starts a new Review #1 and renews exactly one automatic revision allowance.
- Planner/Plan Reviewer retain long-term planning context. Writer and Auto Revision consume only the approved Chapter Plan, limited world rules/current execution context, and previous prose continuity; they do not load Book Plan or Volume Plan.
- Normal Generate still cannot overwrite a completed chapter. UNKNOWN/parse/runtime errors remain errors. Only final prose PASS can reach canonical commit, and commit failure still blocks Fact Digest and RAG.
- Resume continues the original checkpoint/thread and does not replay terminal executions.

## Important Files

- `src/workflows/chapter_workflow.py` — E07.6 state, nodes, routing, review counters, and typed HITL interrupts.
- `src/workflows/chapter_runner.py` — persistent checkpoint and pre-resume edited-file validation.
- `src/agents/author/plan_reviewer.py` — mandatory Chapter Plan Review adapter.
- `src/agents/author/deepseek_writer.py` — initial writing and one bounded L1 prose revision.
- `src/agents/author/chapter_planner.py` — Chapter Intent in Planner context.
- `src/workflows/retrieval_service.py` — Chapter Intent in the existing historical retrieval query.
- `main.py` — `--intent`, edited-file resume, and explicit `--stop` UX.
- `tests/test_e07_6.py` — focused mocked/checkpointed E07.6 invariants.
- `docs/E07_REMAINING_PLAN.md` — authoritative roadmap for E07.7–E07.9.

## Known Issues

- Human collaboration is file-based CLI UX; there is no interactive editor or UI.
- Automatic prose revision is limited to L1 and deliberately does not repair Chapter/Volume/Book plans.
- Plan Review and each prose Review are LLM calls; this stage does not add structured-output API enforcement beyond the existing deterministic Markdown parser.
- Existing `tests/test_e07.py` contains historical E07.2 phase-freeze assertions (for example, forbidding conditional edges/checkpoints/interrupts) and obsolete topology contracts. It is not a valid full E07.6 acceptance suite; production logic was not reverted to satisfy it.
- E07.7 Atomic Fact indexing, FACT→source funnel retrieval, and `chapter_sources.md` remain unimplemented.

## Verification

Executed with the Conda `writer` environment on 2026-08-05:

- `python -m py_compile` for all changed Python modules — passed.
- Chapter Graph build/topology smoke — passed; all E07.6 nodes compile.
- `python -m unittest tests.test_e07_6 -v` — 8 focused tests passed (mocked LLMs; no paid generation).
- Checkpointed plan-edit/re-review smoke — passed; missing edit leaves interrupt pending, valid edit re-reviews without replanning.
- Checkpointed prose cycle smoke — passed; initial and human-edited cycles each receive exactly one revision allowance.
- `python main.py write --help` — passed; `--intent/--instructions`, `--resume`, and `--stop` exposed.
- Historical `tests.test_e07` was attempted. It fails expected obsolete E07.1/E07.2 phase-lock assertions and old E07.5 topology/contracts; these conflict with the authorized E07.6 graph and were not used to change production behavior.
- `git diff --check` — passed.

## Next Task

E07.7 — long-term memory / RAG 2.0, as defined in `docs/E07_REMAINING_PLAN.md`:

- replace full-chapter global vector indexing with Atomic Fact indexing;
- implement FACT → source-text funnel retrieval;
- add `chapter_sources.md` provenance;
- preserve canonical Markdown authority and the now-stable Chapter Graph backbone.

## Out of Scope

- Do not implement E07.7 Atomic Fact/RAG changes as part of E07.6 cleanup.
- Do not implement E07.8 current-state/persistence 2.0 or change Markdown/SQLite ownership early.
- Do not implement E07.9 Story Savepoint/Rollback or move novel/volume management into the Chapter Graph.
- Do not let human edits bypass their relevant Review gate.
- Do not replace checkpoint Resume with a fresh Generate or allow normal Generate to overwrite completed chapters.

## Last Verified

- Base revision: `b71b357` (`上下文延续修改`), 2026-08-05.
- E07.6 working tree: uncommitted implementation, verified on 2026-08-05.
