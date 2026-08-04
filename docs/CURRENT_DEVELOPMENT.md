# Current Development

## Current Stage

E07.5 is complete: the checkpointed single-chapter LangGraph now pauses for human review on `NEEDS_REVISION` or `HALT` and resumes through the same execution.

## Production Path

```text
main.py write
→ ChapterWorkflowRunner
→ build_chapter_workflow
→ plan → draft → style → review → decision
→ PASS: canonical commit → Fact Digest → RAG
→ non-PASS: human interrupt → resume → stop
```

## Completed

- E07 chapter production runs through `ChapterWorkflowRunner`; the legacy general Orchestrator has been removed.
- Checkpoints use one SQLite file per novel and deterministic chapter thread IDs.
- Review routing is fail closed: only explicit `PASS` can reach canonical commit.
- Structured-memory commit is transactional; commit failure blocks downstream derived state.
- E07.5 adds `WAITING_HUMAN`, interrupt payloads, and CLI resume with feedback.
- Resume continues the original checkpoint and cannot promote a non-PASS verdict to `PASS`.

## Important Files

- `docs/E07_REMAINING_PLAN.md` — authoritative plan for E07.6–E07.9.
- `src/workflows/chapter_workflow.py` — graph state, nodes, and decision routing.
- `src/workflows/chapter_runner.py` — checkpoint, interrupt, and resume behavior.
- `main.py` — production `write` and `--resume` entry points.
- `src/agents/state_manager/state_manager.py` — review and canonical commit owner.

## Known Issues

- E07.5 resume supports only `acknowledge`/`stop`; it does not rewrite, re-style, or re-review.
- `HALT` has no planning-repair route.
## Next Task

E07.6 — complete the single-chapter creation loop defined in `docs/E07_REMAINING_PLAN.md`:
- establish optional `chapter_intent` and add mandatory Plan Review after Chapter Planning;
- route failed Plan Review to Human, and require re-review after human plan edits;
- allow at most one automatic revision from Review #1 to Review #2, then route non-PASS to Human; human prose edits start a new Review #1 with one renewed revision allowance;
- separate Planner and Writer information boundaries so Writer receives only an approved Chapter Plan and necessary state/history.

After E07.6, follow the same plan through E07.7 (long-term memory/RAG 2.0), E07.8 (current state/persistence 2.0), and E07.9 (Story Savepoint/Rollback). The Chapter Graph backbone should then remain broadly stable.

## Out of Scope

- This handoff update does not implement E07.6 production code.
- Do not move novel management, volume management, or Story Savepoint/Rollback into the Chapter Graph.
- Do not implement E07.7 RAG changes, E07.8 persistence changes, or E07.9 rollback early.
- Do not replace Resume with a fresh Generate or allow normal Generate to overwrite completed chapters.

## Last Verified

- Runtime commit: `1952a68` (`0.75`), 2026-08-05.
- Handoff revision: `1e7b0a3` (`0.755`), 2026-08-05.
