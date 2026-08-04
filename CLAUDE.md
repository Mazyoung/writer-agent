# Writer-Agent Project Instructions

## Stable Architecture Rules

- Current source code and tests are the highest sources of truth; verify runtime paths before trusting documentation.
- LangGraph owns only the single-chapter production workflow.
- Novel management, volume management, and broad rollback do not belong in the Chapter Graph.
- Canonical planning hierarchy is Book Plan → Volume Plan → Chapter Plan → execution.
- `plot_structure.md` is legacy data and must not be restored as canonical planning state.
- Normal Generate must never overwrite a completed chapter.
- Review `UNKNOWN` or any decision parse failure must fail closed and must never reach commit.
- Runtime, API, and database errors must remain errors; never disguise them as `NEEDS_REVISION`.
- `PASS` is required for canonical commit; commit failure must block Fact Digest and RAG.
- Resume restores the original checkpointed execution; it is not a new Generate operation.
- Canonical Markdown/story state outranks derived SQLite, Chroma, and diagnostic state.
- Planning changes must respect L1/L2/L3 human-authority boundaries; never silently alter higher-level plans.

## Engineering Rules

- Make the smallest coherent change and preserve existing correct behavior.
- Do not add Agents, LLM calls, architectural layers, or future-stage behavior without an explicit requirement.
- Do not perform unrelated refactors or silently change canonical state.
- Add focused tests for critical invariants and run the relevant regression suite before declaring completion.
- For any E07 or LangGraph workflow change, read `docs/E07_LANGGRAPH_MIGRATION_GUIDE.md` before editing.
- Use the Conda `writer` environment for Python; do not use the repository-local `venv`.
- Do not scan the entire repository by default; inspect only files required by the current task.

## Continuing Development

1. Read `CLAUDE.md`.
2. Read `docs/CURRENT_DEVELOPMENT.md`.
3. Run `git status`.
4. Run `git log -5 --oneline`.
5. Read only files directly relevant to `Next Task`.

Do not scan the entire repository unless necessary. After each development stage, update `docs/CURRENT_DEVELOPMENT.md`; do not accumulate E07 history here.
