# Repository Agent Instructions

## Documentation lifecycle

- Treat `README.md`, `CONTEXT.md`, `docs/PROJECT_STATUS.md`, current architecture documents, runbooks, CI guidance, and security records as durable documentation.
- Store feature designs, implementation plans, checklists, handoff notes, review reports, and verification transcripts under `.work/` while they are active.
- Never add planning artifacts beneath `docs/superpowers/` and never force-add ignored working files.
- When work finishes, delete its working artifacts. Update a durable document only when current behavior, operating procedure, product status, or long-lived architectural rationale changed.
- Git history, commits, issues, and pull requests preserve completed-work history; do not preserve it in the current documentation tree.
- Preserve unrelated tracked and untracked user changes.
