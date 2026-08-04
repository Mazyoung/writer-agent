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

- `src/workflows/chapter_workflow.py` — graph state, nodes, and decision routing.
- `src/workflows/chapter_runner.py` — checkpoint, interrupt, and resume behavior.
- `main.py` — production `write` and `--resume` entry points.
- `src/agents/state_manager/state_manager.py` — review and canonical commit owner.
- `docs/E07_LANGGRAPH_MIGRATION_GUIDE.md` — constraints for the next E07 change.

## Known Issues

- E07.5 resume supports only `acknowledge`/`stop`; it does not rewrite, re-style, or re-review.
- `HALT` has no planning-repair route.
- The E07.5 report records static verification only; no E07.5 regression tests were run in that stage.

## Next Task

E07.6: after reviewing the migration guide and current workflow/tests, add the explicitly approved revision loop for human-directed chapter correction without allowing review or commit bypasses.

## Out of Scope

- Do not start E07.6 until it is explicitly requested.
- Do not move novel management, volume management, or broad rollback into the Chapter Graph.
- Do not implement autonomous L2/L3 planning repair, broad state rollback, or RAG refactoring.
- Do not replace Resume with a fresh Generate or allow normal Generate to overwrite completed chapters.

## Last Verified

Commit `1952a68` (`0.75`), 2026-08-05. Working tree was clean before this documentation-only handoff update.
