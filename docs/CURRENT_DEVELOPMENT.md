# Current Development

## Current Stage

E07.7/E07.8 Architecture Closure is implemented in the working tree. The E07.8 deterministic persistence substrate is retained, while Chapter Workflow now has three explicit phases and three distinct boundaries.

~~~text
Planning → Creation/Review → Final Author Approval
→ CANONICAL_COMMITTED → Derivation → DERIVED_READY
~~~

## Production Path

~~~text
PHASE 1 PLANNING
Intent → Current State / Atomic Fact RAG / Author RAG
→ Planner → Plan Review → Human if needed → Approved Plan

PHASE 2 CREATION
Writer → Stylist → Prose Review
→ Human Decision (PASS also pauses)
→ explicit approve → chapters/chapter_NNNN.md
→ CANONICAL_COMMITTED

PHASE 3 DERIVATION
Canonical Prose + previous Current State
→ Deriver (StateDelta + Fact Digest / Atomic Facts)
→ deterministic Current State Markdown + SQLite
→ Volume Progress hook (no lifecycle mutation)
→ chapter_sources.md → Atomic Fact Chroma
→ DERIVED_READY
~~~

The governing invariants are:

- Review FAIL never invokes revision automatically.
- Review PASS never commits automatically.
- Canonical prose existence is independent from successful derivation.

## Human Actions

After prose Review, the checkpoint accepts:

- agent_edit: reuse the revision capability for a local feedback-guided edit, then Review again.
- manual_edit: load the declared edited prose file, save it as a candidate, then Review again.
- regenerate: retain the Approved Plan and restart at Writer.
- pause: retain the current interrupt/checkpoint without consuming it.
- discard: pre-canonical only; remove this chapter's candidate/checkpoint and preserve Chapter Intent.
- approve: available only after the latest Review PASS; enter canonical commit.

Every prose mutation clears the prior verdict and must pass a new Review.

## Canonical and Derived Identity

- Formal prose: chapters/chapter_NNNN.md, create-once and never overwritten by Generate.
- Candidate prose: timestamped chapter_NNNN_styled_*.md and _edited.md.
- Current State derivation marker: states/chapter_NNNN_derived.
- Workflow status: CANONICAL_COMMITTED, DERIVED_READY, or DERIVATION_ERROR.

Completed-chapter counts, previous-chapter continuity, Atomic Fact source expansion, and RAG rebuild source paths use the unique canonical prose. Legacy styled files remain compatibility artifacts only.

## Preserved E07.8 Substrate

The following are reused rather than redesigned:

- strict CurrentState / StateDelta document contracts;
- CurrentStateStore deterministic apply, optimistic base hash, Markdown/SQLite transaction and rollback;
- SQLite v2 exact projection, novel isolation and hash reconciliation;
- generated tracking/current_state.md;
- Markdown Fact Digest / Atomic Facts and atomic_facts_v2 Chroma.

Reviewer and Deriver are now separate LLM contracts. Reviewer outputs only quality decision/issues/feedback. Deriver runs only after canonical commit and outputs StateDelta plus Fact Digest / Atomic Facts.

## Author RAG

tracking/author_rag.md is the sole authority. Production sync reads the plain file and never prefers retired author_rag_edited.md. Existing scoped hash-check/rebuild/re-embedding/fail-closed behavior remains.

## Out of Scope

- E07.9 Volume Plan schema/lifecycle, volume close/create, and Markdown round-trip.
- E07.10 Story Snapshot, Jump, Branch, or broad historical restore.
- Migration of the full historical test suite to the new workflow contract.

## Verification

Focused no-paid-call verification is recorded in tests/test_architecture_closure.py, plus the E07.7 and E07.8 persistence/RAG suites. Historical E07.6 workflow tests still encode retired auto-revision/PASS-to-commit semantics and are not the Architecture Closure contract.
