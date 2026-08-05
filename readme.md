# Writer-Agent

Writer-Agent is a long-form novel Agent workflow for exploring hierarchical planning, durable story memory, historical evidence retrieval, review gates, checkpoint recovery, and human–Agent collaboration across stories that may span hundreds of chapters.

The project prioritizes engineering correctness, explicit state transitions, testability, and observable artifacts over maximizing the number of Agents.

## Current runtime

The production CLI entry point is [`main.py`](main.py). Full chapter execution is owned by a checkpointed LangGraph workflow:

```text
main.py write
  → ChapterWorkflowRunner
  → LangGraph ChapterWorkflow
  → canonical Markdown + derived stores
```

The legacy `Orchestrator` chapter pipeline has been removed. There is no independent `review` command: `write` performs planning, drafting, style editing, review, canonical commit, Fact Digest extraction, and RAG indexing in one resumable workflow.

Current components:

- `WorldBuilder` — world-setting generation
- `PlotDesigner` — Book Plan and active Volume Plan generation
- `ChapterPlanner` — chapter-level planning with canonical planning state and RAG evidence
- `DeepSeekWriter` — draft generation
- `ClaudeStylist` — chapter style editing
- `StyleChecker` — deterministic style checks
- `StateManager` — review analysis, decision parsing, atomic structured-memory updates, and Fact Digest extraction
- `ChapterWorkflowRunner` — persistent SQLite checkpoint/resume boundary
- `ChapterRetrievalService` — deterministic retrieval query, evidence, and trace lifecycle
- scoped services for initialization, standalone planning/editing, status, and RAG maintenance
- `FileStore`, `SQLiteStore`, and `ChromaStore` — canonical files, rebuildable cache state, and vector retrieval

## Data flow

### Planning

```text
Book Plan
  → Active Volume Plan
  → Chapter Plan
  → Draft / styled chapter
```

Canonical planning files live under each runtime novel directory:

```text
tracking/book_plan.md
tracking/volume_plan.md
outlines/chapter_plan_chNNNN.md
```

Completed volumes can be archived under `tracking/volumes/` by the explicit `new-volume` command. `plot_structure.md` is legacy migration input, not current production planning state.

### Chapter execution

```text
START → preflight → load_chapter_intent → plan_chapter
  → review_plan → parse_plan_decision
      PASS → write_draft → style_edit → save_styled
        → review_chapter #1 → parse_chapter_decision
            PASS → commit_state → save_fact_digest → rag_index → END
            NEEDS_REVISION (L1) → auto_revise_chapter (once)
              → save_styled → review_chapter #2
                  PASS → commit path
                  non-PASS → await_human_chapter → interrupt()
                      human prose edit → review_chapter #1
      non-PASS → await_human_plan → interrupt()
          human plan edit → review_plan again

UNKNOWN/malformed decisions and runtime/API/database/disk errors → error → END
```

`ChapterWorkflowRunner` maps each novel/chapter pair to a deterministic LangGraph `thread_id` and persists execution checkpoints in `workflow_checkpoints.sqlite`. Human resume stays on that thread: a plan edit is reviewed again before Writer, while a prose edit starts a fresh Review #1 cycle with one renewed automatic revision allowance. Checkpoints recover workflow execution position and pending interrupts; they do not replace canonical story state or implement story rollback.

### Review and memory

A styled chapter is reviewed by `StateManager`. Its deterministic `ReviewDecision` controls the graph path:

```text
PASS
  → atomic Structured Memory commit
  → deterministic Fact Digest extraction
  → RAG indexing

NEEDS_REVISION / HALT / UNKNOWN
  → no memory commit
  → no Fact Digest
  → no RAG indexing
```

The atomic commit covers:

- `tracking/character_relationships.md`
- `tracking/items_equipment.md`
- `tracking/cultivation_system.md`
- `tracking/character_states.md`
- the chapter completion marker

Fact Digests are derived from the review analysis without another LLM call. SQLite and Chroma are derived or rebuildable state; canonical Markdown remains the story-state source of truth.

### RAG

Only finalized/styled chapters are indexed. Chapter planning retrieves filtered historical chunks through `ChromaStore`, prevents future-chapter leakage, and writes JSON retrieval traces under `tracking/rag_traces/`. The CLI can backfill or rebuild the main-branch index.

## CLI usage

The current test environment uses Python 3.14.6. Install the dependencies in `requirements.txt`, then configure `.env` locally. At minimum, production generation requires `DEEPSEEK_API_KEY`; `.env` is ignored by Git.

```bash
python main.py init my_novel "A concise story premise"
# Review proposal.md and optionally save proposal_edited.md
python main.py init my_novel --confirm

# Optional standalone planning; write also plans inside the full workflow
python main.py plan my_novel --chapter 1

# Full checkpointed chapter workflow, optionally with Chapter Intent
python main.py write my_novel --chapter 1 --intent "推进人物和解，但不能揭露终局"

# If Plan Review pauses, edit outlines/chapter_plan_ch0001_edited.md.
# If Chapter Review pauses, edit chapters/chapter_0001_styled_edited.md.
# Then resume the same checkpoint with optional feedback:
python main.py write my_novel --chapter 1 --resume "已按审阅意见修改"

# Or explicitly terminate the paused non-PASS execution
python main.py write my_novel --chapter 1 --stop

python main.py status my_novel
```

Additional commands:

```bash
python main.py style my_novel --chapter 1 --feedback "..."
python main.py new-volume my_novel --notes "..."
python main.py rag-index my_novel
python main.py rag-index my_novel --rebuild
```

Runtime data is written to `data/novels/` and is not version controlled. A curated, non-secret demonstration chain is available under [`examples/memory_anchor_demo/`](examples/memory_anchor_demo/).

Legacy novels that only contain `plot_structure.md` can be converted with the standalone utility:

```bash
python scripts/migrate_legacy_data.py <novel_id> --dry-run
python scripts/migrate_legacy_data.py <novel_id>
```

The migration utility is not connected to the chapter workflow.

## Tests

The authoritative regression command is:

```bash
E:\code\miniconda\envs\writer\python.exe -m unittest discover -s tests -v
```

When `pytest` is installed, the same suite can also be run with:

```bash
E:\code\miniconda\envs\writer\python.exe -m pytest
```

## Documentation

- [`docs/claude.md`](docs/claude.md) — project engineering rules and source-of-truth policy
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — current runtime architecture and state boundaries
- [`docs/E07_LANGGRAPH_MIGRATION_GUIDE.md`](docs/E07_LANGGRAPH_MIGRATION_GUIDE.md) — mandatory E07 migration constraints
- [`docs/E07_4_1_LEGACY_ORCHESTRATOR_CLEANUP_REPORT.md`](docs/E07_4_1_LEGACY_ORCHESTRATOR_CLEANUP_REPORT.md) — current cleanup report
- `docs/E0*_*.md` — stage-specific implementation and closure history
