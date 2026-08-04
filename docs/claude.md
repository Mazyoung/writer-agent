# Long-Form Novel Multi-Agent System — Project Instructions

## 1. Project Goal

This project is a long-form novel Agent Workflow system designed for approximately 100–500 chapter stories.

Its main engineering problems are:

- hierarchical planning
- long-term memory
- historical evidence retrieval
- context management
- consistency
- human-agent collaboration
- error recovery
- replanning
- observability

The project is primarily intended for learning and demonstrating Agent engineering.

Engineering correctness, explainability, testing, state consistency, and observability are more important than increasing the number of Agents.

------

## 2. Source of Truth

The CURRENT SOURCE CODE is always the highest source of truth.

Priority:

1. Current source code
2. Current tests
3. Current canonical runtime state
4. Recent implementation reports in `docs/`
5. README / ARCHITECTURE
6. Historical documents

If documentation conflicts with current source code:

- follow the current source code;
- explicitly report the documentation drift;
- DO NOT restore an older architecture simply because it appears in old documentation.

Do not treat obsolete prompts, old README content, or historical architecture as current runtime design.

------

## 3. Current Architecture Principle

The project originally contained more Agents, but several responsibilities have intentionally been merged.

Current runtime components roughly include:

- WorldBuilder
- PlotDesigner
- ChapterPlanner
- DeepSeekWriter
- ClaudeStylist
- StyleChecker
- StateManager

Do NOT recreate the old nine-Agent architecture merely to make the system look more "multi-agent".

The goal is a correct Agent Workflow, not a larger Agent count.

Before changing architecture, inspect the actual current implementation.

------

## 4. Planning Architecture

Canonical hierarchy:

Book Plan
→ Volume Plan
→ Chapter Plan
→ Scene / Execution

Canonical runtime planning state:

- `tracking/book_plan.md`
- `tracking/volume_plan.md`
- `outlines/chapter_plan_chNNNN.md`

Completed historical volume plans:

- `tracking/volumes/volume_NN.md`

`plot_structure.md` is NOT canonical runtime planning state.

Do not reconnect ChapterPlanner to `plot_structure.md`.

------

## 5. Planning Modification Authority

Planning issues use three levels.

### L1 — Execution Issue

Local prose, dialogue, scene execution, or similar issues.

Writer may repair these without modifying higher-level plans.

### L2 — Planning Issue

If a Chapter Plan or part of a Volume Plan requires modification:

Agent must NOT silently modify planning state.

Expected future flow:

Planning Problem
→ PlanningModificationReport
→ HALT
→ Human Review
→ PlanRevision

### L3 — Strategic Issue

If established history invalidates strategic planning:

HALT PIPELINE.

Do not autonomously repair Book-level strategy.

L3 repair is a Human-Agent collaborative workflow.

------

## 6. Rollback Principle

Rollback means Workflow State Rollback, not restoring one Markdown file.

Future rollback may involve:

- chapter text
- Book Plan
- Volume Plan
- Chapter Plan
- character/item/cultivation state
- foreshadowing
- Fact Digest
- tracking state
- SQLite
- RAG derived state

Chroma is derived/rebuildable state.

Do NOT design Chroma binary snapshot rollback.

------

## 7. Engineering Rules

For every implementation stage:

1. Inspect current source before modifying it.
2. Read relevant reports in `docs/`.
3. Run the existing test suite before major changes.
4. Preserve already-correct behavior.
5. Make the smallest coherent change.
6. Prefer explicit and testable data flow.
7. Do not perform unrelated refactors.
8. Do not silently change canonical state.
9. Do not add unnecessary Agents.
10. Do not add new LLM calls unless explicitly required.
11. Do not implement future stages early.
12. Add tests for every critical invariant.
13. Run the complete test suite before declaring completion.
14. Generate the requested implementation report.
15. Stop when the requested engineering stage is complete.

------

## 8. Model-Performance Warning

This project may be executed using different underlying models.

If you encounter or produce an issue, distinguish between:

**普通实现问题**

and, when justified:

**疑似模型能力下降导致的问题**

Potential signs of model-performance degradation include:

- ignoring explicit requirements;
- losing cross-file context;
- restoring obsolete architecture;
- duplicating functionality that already exists;
- silently changing canonical state;
- deleting required behavior during refactoring;
- implementing features explicitly excluded from the current stage;
- claiming functionality exists without tracing the real runtime path;
- passing narrow tests while breaking established architecture;
- misunderstanding long dependency chains despite explicit documentation.

Do not automatically blame the model.

Only use the second label when there is concrete evidence.

------

## 9. Required Working Method

For a new engineering task:

### Step 1 — Inspect

Read:

- this file;
- the current task specification;
- relevant `docs/` reports;
- relevant source files;
- relevant tests.

Do not start from assumptions.

### Step 2 — Reconstruct Actual Runtime

Trace the real call chain and state flow.

Verify whether documented components are actually connected.

### Step 3 — Compare

Compare:

Current implementation
vs.
Task specification

Identify:

- already implemented behavior;
- missing behavior;
- conflicting behavior;
- documentation drift;
- dead/obsolete paths.

### Step 4 — Implement

Make only changes required by the current stage.

### Step 5 — Verify

Run focused tests and the complete regression suite.

Do not claim success merely because code was written.

### Step 6 — Report

Document:

- what changed;
- why;
- affected files;
- data flow;
- tests;
- remaining limitations;
- anything intentionally not implemented.

------

## 10. Anti-Hallucination Rule

Never infer that a feature is implemented only because:

- a class exists;
- a method exists;
- a configuration exists;
- documentation describes it.

Trace whether the feature is actually used in the runtime pipeline.

When uncertain, inspect the code.