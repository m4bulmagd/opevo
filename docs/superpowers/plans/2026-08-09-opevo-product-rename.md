# Opevo Product Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Opevo the sole product and package identity throughout repository-owned content and paths.

**Architecture:** Apply a casing-aware mechanical replacement across tracked source, configuration, tests, and documentation, then rename every repository-owned path that contains the legacy identifier. Coordinate the shared Python distribution name and import package across its manifest, consumers, lockfiles, Docker checks, and tests so all applications continue to use one contract package.

**Tech Stack:** Next.js 16, React 19, TypeScript, Python 3.13, uv, FastAPI, LiveKit Agents, Docker Compose, Markdown

## Global Constraints

- Product copy uses `Opevo`.
- Runtime identifiers, package names, domains, image tags, metrics, Redis keys, and filesystem paths use `opevo`.
- Environment variable names use `OPEVO`.
- No case-insensitive legacy-brand match may remain in repository-owned content or paths.
- Third-party dependency directories are regenerated artifacts and are not edited as source.
- `Opevo_frontend/` entered the workspace as an untracked local visual reference; preserve that status rather than adding its source, dependencies, or build output to Git.

---

### Task 1: Rename tracked product identifiers and paths

**Files:**
- Modify: every tracked text file returned by the initial case-insensitive legacy-brand audit
- Move: `libs/shared/src/opevo_contracts/`
- Move: `apps/web/src/components/landing/opevo-landing-page.tsx`
- Move: `apps/web/src/components/motion/opevo-motion-provider.tsx`
- Move: all matching documentation files to equivalent `opevo` destination names

**Interfaces:**
- Consumes: the existing product copy, Python distribution/import package, runtime identifiers, and internal component imports
- Produces: `opevo-contracts`, `opevo_contracts`, `OpevoLandingPage`, `OpevoMotionProvider`, and casing-consistent Opevo identifiers

- [ ] **Step 1: Capture the initial tracked content and path audit**

Run a case-insensitive Git content search and tracked-path search, recording the number of files and exact casing variants.

- [ ] **Step 2: Apply the casing-aware tracked-content replacement**

Replace the lowercase, title-case, and uppercase legacy forms with `opevo`, `Opevo`, and `OPEVO` respectively in every matching tracked text file.

- [ ] **Step 3: Rename all matching tracked paths**

Move the shared import package, the two named React components, and every matching documentation filename to their casing-consistent Opevo destinations.

- [ ] **Step 4: Validate package and import consistency**

Run:

```bash
cd libs/shared && UV_CACHE_DIR=/tmp/uv-cache uv lock --check
cd apps/api && UV_CACHE_DIR=/tmp/uv-cache uv lock --check
cd apps/agent && UV_CACHE_DIR=/tmp/uv-cache uv lock --check
```

Expected: all three lockfiles are current and every consumer resolves `opevo-contracts` / `opevo_contracts`.

### Task 2: Rename the local visual-reference project

**Files:**
- Modify locally: matching source and project metadata under `Opevo_frontend/`, excluding third-party dependencies
- Move locally: `Opevo_frontend/`

**Interfaces:**
- Consumes: the untracked local visual-reference project and its repository instructions
- Produces: an equivalently functioning Opevo-named visual reference without legacy source identifiers

- [ ] **Step 1: Replace matching reference-project source content**

Apply the same casing-aware replacement to matching project-owned files, excluding `.git`, `node_modules`, build output, and tool caches.

- [ ] **Step 2: Rename matching reference-project paths**

Move every matching nested path deepest-first, then move the project root to `Opevo_frontend/`.

- [ ] **Step 3: Verify the reference project**

Run its declared static checks without changing application behavior. Record any pre-existing unrelated failures instead of reformatting the local reference project, and run its production build as the compile gate.

Verification outcome: the production build passes. The declared lint command remains blocked by 32 pre-existing Prettier errors and 10 warnings in unrelated reference-project files; no unrelated formatting rewrite is included in this rename.

### Task 3: Verify and review the complete rename

**Files:**
- Verify: all repository-owned source, configuration, tests, documentation, and paths

**Interfaces:**
- Consumes: Tasks 1 and 2
- Produces: fresh test evidence and a zero-match audit

- [ ] **Step 1: Run focused shared-package checks**

Run the shared package tests, Ruff, and mypy against the renamed package.

- [ ] **Step 2: Run application checks**

Run API and agent import/package tests plus web lint, typecheck, and unit tests.

- [ ] **Step 3: Run repository-level rename audits**

Run both tracked and filesystem case-insensitive content/path scans, excluding `.git` internals and generated third-party dependency directories.

Expected: zero repository-owned content or path matches.

- [ ] **Step 4: Review the diff against the request**

Confirm that changes are rename-only, all old imports and paths have corresponding destinations, user-owned content is preserved, and no unrelated behavior changed.
