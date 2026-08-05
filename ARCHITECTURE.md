# Writer-Agent Current Architecture

> Current source code and tests are authoritative. Stage reports provide implementation history; they do not override the runtime.

## 1. Runtime boundary

The full chapter production path is:

```text
main.py:cmd_write
  → src.workflows.chapter_runner.ChapterWorkflowRunner
  → src.workflows.chapter_workflow.build_chapter_workflow
  → existing Agents / services
  → canonical Markdown + derived stores
```

The legacy `src.core.orchestrator.Orchestrator` layer has been removed. `main.py` invokes scoped services for non-chapter operations rather than routing through a general orchestration facade:

```text
Initialization / volumes       Standalone operations        Chapter execution
────────────────────────       ─────────────────────        ─────────────────
NovelLifecycleService          ChapterPlanningService       ChapterWorkflowRunner
WorldBuilder / PlotDesigner    ChapterEditingService          → LangGraph
                               NovelStatusService
                               RAGMaintenanceService
```

## 2. CLI operations

| Command | Production operation |
|---|---|
| `init` | Generate a proposal, then on `--confirm` create world setting, Book Plan, and Volume Plan |
| `status` | Report current volume, completed chapters, and tracking-document availability |
| `plan` | Standalone historical retrieval and Chapter Plan generation |
| `write` | Run the complete checkpointed chapter workflow, including review and commit |
| `style` | Re-style an existing chapter with optional human feedback, then save/check it; no review or commit |
| `new-volume` | Explicitly archive the completed volume and generate the next ACTIVE Volume Plan |
| `rag-index` | Backfill/rebuild Atomic Facts from completed chapters' Markdown Fact Digests |

There is no independent `review` command. This prevents a second review/commit path outside the LangGraph state machine.

## 3. Planning architecture

The canonical hierarchy is:

```text
Book Plan                     strategic, long-lived
  → Active Volume Plan        tactical rolling horizon
    → Chapter Plan            per-chapter execution specification
      → Draft / styled text   execution output
```

Canonical locations inside `data/novels/<novel_id>/`:

```text
settings/world_setting.md
tracking/book_plan.md
tracking/volume_plan.md
tracking/volumes/volume_NN.md
outlines/chapter_plan_chNNNN.md
```

`NovelLifecycleService` creates Book and initial Volume plans, and owns the explicit Generate → Validate → Commit transition for `new-volume`. `ChapterPlanningService` provides optional standalone planning. The full `write` workflow also plans through its `plan_chapter` node.

`plot_structure.md` and `scene_plan_chNNNN.md` are legacy data formats. They are not current production planning truth. The standalone `scripts/migrate_legacy_data.py` utility can convert old data.

## 4. Full chapter workflow

```text
START → preflight → load_chapter_intent → plan_chapter
  → review_plan → parse_plan_decision
      PASS → write_draft → style_edit → save_styled
        → review_chapter #1 → parse_chapter_decision
            PASS → commit_state
            NEEDS_REVISION (L1, allowance available)
              → auto_revise_chapter → save_styled
              → review_chapter #2 → parse_chapter_decision
                  PASS → commit_state
                  non-PASS → await_human_chapter → interrupt()
                      human prose edit → review_chapter #1
      non-PASS → await_human_plan → interrupt()
          human plan edit → review_plan
  → commit success → chapter_sources → save_fact_digest → Atomic Fact rag_index → END
  → pre-commit error blocks; post-commit derived error remains observable without rollback
```

Node ownership:

| Node | Existing business owner |
|---|---|
| `load_chapter_intent` | `FileStore`; optional canonical/human intent |
| `plan_chapter` | `ChapterRetrievalService` + `ChapterPlanner` |
| `review_plan` | `PlanReviewer` |
| `parse_plan_decision` | `ReviewDecision.from_analysis` |
| `await_human_plan` | LangGraph `interrupt()`; edited plan returns to Plan Review |
| `write_draft` | `DeepSeekWriter`; approved Chapter Plan boundary |
| `style_edit` | `ClaudeStylist` |
| `auto_revise_chapter` | `DeepSeekWriter.revise_chapter`; deterministic ×1 allowance |
| `save_styled` | `FileStore` + `StyleChecker` |
| `review_chapter` | `StateManager.review_chapter` |
| `parse_chapter_decision` | `ReviewDecision.from_analysis` |
| `await_human_chapter` | LangGraph `interrupt()`; edited prose starts Review #1 |
| `commit_state` | `StateManager.update_tracking_docs` |
| `save_fact_digest` | `StateManager.extract_fact_digest_from_analysis` |
| `save_chapter_sources` | stable automatic provenance report under `sources/chapter_NNNN/` |
| `rag_index` | `AtomicFactStore.index_facts`; embeds Fact Text only |

Graph nodes remain adapters over existing business logic. There is no second implementation of ReviewDecision parsing or canonical commit.

## 5. Checkpoint and canonical-state boundary

`ChapterWorkflowRunner` persists LangGraph checkpoints to:

```text
data/novels/<novel_id>/workflow_checkpoints.sqlite
```

A deterministic thread ID identifies one chapter execution:

```text
chapter:<novel_id>:<chapter_index padded to 4 digits>
```

Runner behavior:

1. no snapshot → invoke with initial state;
2. incomplete snapshot with `next` nodes → invoke with `None` and resume;
3. pending interrupt → wait for `Command(resume=...)` on the same thread;
4. terminal `ERROR`/`STOPPED_NON_PASS` without completion marker → delete only that thread's obsolete execution checkpoint and start a new Generate;
5. completion marker present → reject ordinary Generate even if a terminal checkpoint exists;
6. other terminal snapshot → return stored state without replaying nodes.

LangGraph checkpoint is workflow execution state. It does not replace or roll back canonical planning/story state. Canonical state remains managed by `FileStore` and the `StateManager` transaction.

## 6. Review and Structured Memory

The decision path is fail-closed:

```text
styled chapter + planning/context
  → StateManager.review_chapter()                 # one LLM analysis
  → ReviewDecision.from_analysis()                # deterministic
  → conditional graph routing
```

PASS is necessary but not sufficient. Downstream nodes proceed only after an explicit successful `StateCommitResult` and completion marker.

The atomic Structured Memory commit covers:

```text
tracking/character_relationships.md
tracking/items_equipment.md
tracking/cultivation_system.md
tracking/character_states.md
states/chapter_NNNN_completed
```

The transaction preserves ALL OLD or ALL NEW when snapshot and rollback succeed:

1. snapshot existing files;
2. parse/build all candidate documents in memory;
3. write candidates;
4. write the completion marker;
5. roll back every written artifact if any write fails;
6. sync rebuildable SQLite state only after Markdown success.

Commit failure blocks chapter sources, Fact Digest, and RAG. Once the completion marker exists, any source-report/Fact Digest/Chroma failure is a derived-state error: it is returned and printed, but never rolls back canonical state.

## 7. Persistence classification

### Canonical planning state

```text
settings/world_setting.md
tracking/book_plan.md
tracking/volume_plan.md
tracking/volumes/volume_NN.md
outlines/chapter_plan_chNNNN.md
```

### Canonical story state

```text
chapters/chapter_NNNN_styled_*.md
tracking/character_relationships.md
tracking/items_equipment.md
tracking/cultivation_system.md
tracking/character_states.md
states/chapter_NNNN_completed
```

### Derived and diagnostic state

```text
states/fact_digest_chNNNN_*.md
sources/chapter_NNNN/chapter_sources.md
state.db
ChromaDB `atomic_facts_v2` (Fact Text documents + source metadata)
tracking/rag_traces/*.json
states/review_chNNNN_*.md
states/post_chapter_update_*.md
workflow_checkpoints.sqlite        # execution recovery, not story truth
```

## 8. RAG

Production long-term memory is:

```text
completed styled chapter → Review → Markdown Fact Digest → Atomic Facts → Chroma
```

Each Atomic Fact stores a stable FACT-ID, chapter, type, entities, paragraph range, and Fact Text. Chroma embeds only Fact Text in versioned collection `atomic_facts_v2`. Retrieval is constrained by:

```text
novel_id
branch_id = main
chapter_index < current chapter
source_type = atomic_fact
```

`ChapterRetrievalService` searches FACT candidates, then reads only valid matched paragraph ranges from the referenced styled chapter, with one neighboring paragraph on each side. Planner selects the facts/excerpts it adopts into the Chapter Plan; Writer never receives the full Book/Volume plans or unselected candidates.

Legacy `chapter_chunks` remain in a separate, unqueried collection until maintenance. `rag-index --rebuild` recreates Atomic Facts from Markdown digests and removes the selected novel/branch's legacy chunks. Legacy six-section digests are deterministically projected into facts with unknown source ranges; they are searchable but cannot trigger prose expansion until regenerated with paragraph metadata.

## 9. Human authority and future E07 work

Planning authority remains:

- **L1** — local execution/prose issue; repair without changing higher plans.
- **L2** — Chapter/Volume planning issue; requires a modification report and human approval.
- **L3** — strategic Book-level issue; halt for human–Agent repair.

E07.6's plan/prose review loop and HITL checkpoint semantics are complete. E07.7's fact-only long-term memory is complete. The next architecture stage is E07.8 current-state/persistence 2.0; it is not implemented here.
