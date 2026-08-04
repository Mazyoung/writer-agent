# Writer-Agent Current Architecture

> Current source code and tests are authoritative. Stage reports provide implementation history; they do not override the runtime.

## 1. Runtime boundary

The current production path is:

```text
main.py
  → src.core.orchestrator.Orchestrator
  → existing Agents / services
  → canonical Markdown + derived stores
```

`main.py` owns CLI parsing and user-facing status. `Orchestrator` owns the production workflow and composes the current components:

```text
Architecture / planning       Authoring                 Review / state
────────────────────────      ─────────────────────     ──────────────────
WorldBuilder                  ChapterPlanner            StateManager
PlotDesigner                 DeepSeekWriter            ReviewDecision
                              ClaudeStylist             StyleChecker

Storage
─────────────────────────────────────────────────────────────────────────
FileStore (canonical/artifacts)  SQLiteStore (cache)  ChromaStore (RAG)
```

The LangGraph workflow under `src/workflows/chapter_workflow.py` is an E07 migration path. It does not currently drive `main.py`.

## 2. CLI and production flows

`main.py` exposes these production commands:

| Command | Production operation |
|---|---|
| `init` | Generate a proposal, then on `--confirm` create world setting, Book Plan, and Volume Plan |
| `status` | Report current volume, completed chapters, and tracking-document availability |
| `plan` | Retrieve historical evidence and generate one Chapter Plan |
| `write` | Generate a draft, style it, save one styled chapter, and run `StyleChecker` |
| `style` | Re-style an existing chapter with optional human feedback, then save/check it |
| `review` | Review the styled chapter and route through the decision/commit pipeline |
| `new-volume` | Explicitly archive the completed volume and generate the next ACTIVE Volume Plan |
| `rag-index` | Backfill or rebuild finalized chapter chunks in Chroma |

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
tracking/volumes/volume_NN.md       # completed-volume archives
outlines/chapter_plan_chNNNN.md
```

`PlotDesigner` creates Book and initial Volume plans during confirmed initialization. `ChapterPlanner` requires those plans, loads the current ACTIVE volume, and generates a `ChapterPlan`. `new-volume` is the explicit rolling-horizon transition and records a `PlanRevision`.

`plot_structure.md` and `scene_plan_chNNNN.md` are legacy data formats. They are not current production planning truth. The standalone `scripts/migrate_legacy_data.py` utility can convert old data, but production code does not import or invoke it.

Planning foundation models under `src/planning/` define `PlanRevision`, `PlanningModificationReport`, `StrategicRepairCase`, `StoryBranch`, and `ChapterCheckpoint`. Most are foundation for future human-approved replanning and rollback; their existence does not mean those workflows are connected to production.

## 4. Authoring flow

```text
Orchestrator.plan_chapter()
  → historical RAG retrieval
  → ChapterPlanner.plan_chapter()
  → outlines/chapter_plan_chNNNN.md

Orchestrator.write_chapter()
  → load canonical Chapter Plan
  → DeepSeekWriter.write_chapter()
  → timestamped draft
  → ClaudeStylist.edit_chapter()
  → save timestamped styled chapter exactly once
  → StyleChecker.check_all()
```

`ClaudeStylist` returns transformed text; `Orchestrator` owns the styled save and deterministic check. Review accepts only a styled chapter.

## 5. Review and state-management flow

The production review path is:

```text
styled chapter + planning/context
  → StateManager.review_chapter()                 # one LLM analysis
  → StateManager.parse_review_decision()          # deterministic
  → ReviewDecision
```

Decision routing:

```text
PASS
  → StateManager.update_tracking_docs()
      → load all canonical tracking state
      → parse all state deltas in memory
      → build candidates
      → atomic multi-file commit with rollback
      → sync rebuildable SQLite cache after Markdown success
  → extract_fact_digest_from_analysis()            # deterministic, no LLM
  → index finalized chapter in Chroma

NEEDS_REVISION / HALT / UNKNOWN
  → stop before canonical commit
  → no Fact Digest
  → no RAG index
```

A semantic review PASS is not sufficient on its own. The downstream path proceeds only after an explicit successful `StateCommitResult`.

## 6. Structured Memory and persistence classes

### Canonical story state

The atomic Structured Memory commit covers four Markdown documents:

```text
tracking/character_relationships.md
tracking/items_equipment.md
tracking/cultivation_system.md
tracking/character_states.md
```

When snapshot and rollback operations succeed, the commit preserves **ALL OLD or ALL NEW** semantics:

1. snapshot existing files;
2. parse/build all candidate documents in memory;
3. write candidates;
4. roll back every already-written document if any write fails;
5. expose success/failure through `StateCommitResult`.

If rollback itself fails, the failure is surfaced as an explicit warning and canonical tracking files may be inconsistent. This is an exceptional degraded state that requires manual inspection; the runtime does not claim atomicity after a failed rollback.

Styled chapters and the four tracking documents are canonical story state. Book, Volume, and Chapter plans are canonical planning state.

### Derived and diagnostic state

```text
states/fact_digest_chNNNN_*.md       derived from review analysis
state.db                             rebuildable SQLite cache
ChromaDB chapter chunks              rebuildable RAG index
tracking/rag_traces/*.json           retrieval diagnostics
states/review_chNNNN_*.md             workflow artifact
states/post_chapter_update_*.md       commit change log
```

Fact Digest or RAG failure does not rewrite previously committed canonical Markdown. Commit failure blocks both Fact Digest and RAG.

## 7. RAG indexing and retrieval

`ChromaStore` is lazy: constructing `Orchestrator` does not initialize Chroma until retrieval or indexing is requested.

### Indexing

```text
successful review + canonical commit
  → load latest chapter_NNNN_styled_*.md
  → deterministic overlapping chunks
  → stable ID: <novel>_<branch>_chNNNN_chunkNNN
  → remove stale chunks for that chapter
  → insert chapter chunks with metadata
```

Only finalized/styled chapters enter the corpus. Index failures are logged and do not roll back canonical state.

### Retrieval

Before planning chapter N:

```text
Volume event + optional outline/instructions + tracked characters/items
  → deterministic query
  → Chroma search constrained by:
      novel_id
      branch_id = main
      chapter_index < N
      source_type = chapter
  → formatted evidence in ChapterPlanner prompt
  → JSON RetrievalTrace
```

The chapter bound prevents future leakage. Retrieval failure degrades to empty evidence while preserving a failed trace.

## 8. E07 LangGraph migration status

E07 is an incremental behavioral migration governed by `docs/E07_LANGGRAPH_MIGRATION_GUIDE.md`.

Current implemented stage: **E07.2 PASS Happy Path Behavioral Parity**.

```text
START
  → plan_chapter
  → write_draft
  → style_edit
  → save_styled
  → review_chapter
  → parse_decision
  → require_pass
  → commit_state
  → save_fact_digest
  → rag_index
  → END
```

The graph uses adapter nodes over existing Agents and services; it does not own a second business implementation. E07.2 remains deliberately linear and uses a guard node for non-PASS results.

Not yet part of the migration runtime:

- E07.3 conditional edges and explicit terminal routing
- E07.4 checkpoint/resume
- E07.5 human-in-the-loop interrupt/resume
- E07.6 revision loop
- replacement of `main.py` / `Orchestrator`

### Production/migration boundary

```text
Production today                         Migration validation today
─────────────────────────────            ─────────────────────────────
main.py                                  tests / direct graph invocation
  → Orchestrator                           → build_chapter_workflow()
  → production side effects               → adapter-node parity path
```

Canonical state remains independent from LangGraph checkpoint state. Future migration work must preserve atomic Structured Memory commit and treat Chroma/SQLite as derived state.

## 9. Human authority and future planning repair

Planning issues retain three levels:

- **L1** — local execution/prose issue; repair without changing higher plans.
- **L2** — Chapter/Volume planning issue; requires a modification report and human approval before plan changes.
- **L3** — strategic Book-level issue; halt for human–Agent repair.

The data models for these concepts exist, but automatic L2/L3 repair, rollback, and branching are not current production behavior.

## 10. Historical architecture

Earlier versions used a larger nine-Agent, scene-by-scene runtime with BriefGenerator, consistency/replan components, and synchronization paths centered on `plot_structure.md`. Those modules and commands are no longer the production architecture and have been removed from the working tree. Git history and historical audit documents preserve that context where needed.
