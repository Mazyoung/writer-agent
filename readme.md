# Writer-Agent

Writer-Agent is a long-form novel Agent workflow for exploring hierarchical planning, durable story memory, historical evidence retrieval, review gates, and human–Agent collaboration across stories that may span hundreds of chapters.

The project prioritizes engineering correctness, explicit state transitions, testability, and observable artifacts over maximizing the number of Agents.

## Current runtime

The production CLI entry point is [`main.py`](main.py). It constructs and calls [`src.core.orchestrator.Orchestrator`](src/core/orchestrator.py); LangGraph has **not** replaced this runtime.

Current core components:

- `WorldBuilder` — world-setting generation
- `PlotDesigner` — Book Plan and active Volume Plan generation
- `ChapterPlanner` — chapter-level planning with canonical planning state and RAG evidence
- `DeepSeekWriter` — draft generation
- `ClaudeStylist` — chapter style editing
- `StyleChecker` — deterministic style checks
- `StateManager` — review analysis, decision parsing, atomic structured-memory updates, and Fact Digest extraction
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

### Review and memory

A styled chapter is reviewed by `StateManager`. Its deterministic `ReviewDecision` controls the production path:

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

Fact Digests are derived from the review analysis without another LLM call. SQLite and Chroma are derived or rebuildable state; canonical Markdown remains the story-state source of truth.

### RAG

Only finalized/styled chapters are indexed. Chapter planning retrieves filtered historical chunks through `ChromaStore`, prevents future-chapter leakage, and writes JSON retrieval traces under `tracking/rag_traces/`. The CLI can backfill or rebuild the current branch index.

## LangGraph migration

[`src/workflows/chapter_workflow.py`](src/workflows/chapter_workflow.py) contains the E07 migration workflow. At the current E07.2 stage it implements the PASS happy path as adapter nodes over existing Agents and services.

It remains a migration/behavioral-parity path:

- production `main.py` still uses `Orchestrator`;
- conditional routing, checkpoint/resume, HITL interrupts, and revision loops belong to later E07 stages;
- canonical memory commit semantics remain independent from LangGraph execution state.

Read [`docs/E07_LANGGRAPH_MIGRATION_GUIDE.md`](docs/E07_LANGGRAPH_MIGRATION_GUIDE.md) before changing workflow code.

## Basic usage

The current test environment uses Python 3.14.6. Install the dependencies in `requirements.txt`, then configure `.env` locally. At minimum, production generation requires `DEEPSEEK_API_KEY`; `.env` is ignored by Git.

```bash
python main.py init my_novel "A concise story premise"
# Review proposal.md and optionally save proposal_edited.md
python main.py init my_novel --confirm

python main.py plan my_novel --chapter 1
python main.py write my_novel --chapter 1
python main.py review my_novel --chapter 1
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

The migration utility is not connected to the production runtime.

## Tests

The authoritative regression command is:

```bash
python -m unittest discover -s tests -v
```

When `pytest` is installed, the same suite can also be run with:

```bash
python -m pytest
```

## Documentation

- [`docs/claude.md`](docs/claude.md) — project engineering rules and source-of-truth policy
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — current runtime architecture and state boundaries
- [`docs/E07_LANGGRAPH_MIGRATION_GUIDE.md`](docs/E07_LANGGRAPH_MIGRATION_GUIDE.md) — mandatory E07 migration constraints
- `docs/E0*_*.md` — stage-specific implementation and closure reports
