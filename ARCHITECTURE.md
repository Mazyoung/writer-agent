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
| `rag-index` | Backfill or rebuild finalized chapter chunks in Chroma |

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
  → commit success → save_fact_digest → rag_index → END
  → UNKNOWN/runtime/commit/digest error → END
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
| `rag_index` | `ChromaStore.index_chapter` |

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
3. terminal snapshot → return stored state without replaying nodes.

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

Commit failure blocks Fact Digest and RAG. RAG failure does not roll back canonical state.

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
state.db
ChromaDB chapter chunks
tracking/rag_traces/*.json
states/review_chNNNN_*.md
states/post_chapter_update_*.md
workflow_checkpoints.sqlite        # execution recovery, not story truth
```

## 8. RAG

`ChapterRetrievalService` owns deterministic query construction, Chroma search, evidence formatting, and retrieval trace persistence. Retrieval is constrained by:

```text
novel_id
branch_id = main
chapter_index < current chapter
source_type = chapter
```

Only finalized/styled chapters enter the corpus. `RAGMaintenanceService` provides explicit backfill/rebuild operations. Rebuild aborts if branch clearing fails; per-chapter indexing failures are reported without changing canonical story state.

## 9. Human authority and future E07 work

Planning authority remains:

- **L1** — local execution/prose issue; repair without changing higher plans.
- **L2** — Chapter/Volume planning issue; requires a modification report and human approval.
- **L3** — strategic Book-level issue; halt for human–Agent repair.

E07.5 inserts HITL after `parse_decision`: PASS continues to `commit_state`; NEEDS_REVISION/HALT pause in `await_human_review` and resume through the same checkpointer/thread ID. The current resume action records human feedback and terminates the non-PASS execution; it cannot promote a verdict to PASS. E07.6 may add rewrite/style/re-review routing after the resumed HITL node.
