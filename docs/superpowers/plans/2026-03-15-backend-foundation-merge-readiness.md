# Backend Foundation Merge Readiness Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `feature/backend-foundation-mvp` to a clean, verified, merge-ready state without expanding scope beyond backend foundation stabilization.

**Architecture:** This plan works inside the existing backend worktree and treats the current API and agent implementations as the baseline. The work is limited to cleaning config and documentation drift, resolving the remaining in-progress edits, restoring full automated verification, and running the external staging checklist already defined by the branch.

**Tech Stack:** Python, FastAPI, SQLAlchemy, LiveKit Agents, Redis, ARQ, Docker Compose, pytest, uv

---

Use `@superpowers/test-driven-development` while executing every code change in this plan. Use `@superpowers/systematic-debugging` before any fix attempt if a test or staging step fails unexpectedly. Before claiming completion, follow `@superpowers/verification-before-completion`.

## Scope

### In Scope

- Fixing the failing API deployment-readiness test.
- Reconciling `apps/api/.env.example` with the intended backend contract.
- Resolving current uncommitted source edits in the worktree.
- Re-running the API and agent suites until green.
- Verifying the LiveKit dispatch payload assumption.
- Executing and documenting staging smoke checks.
- Preparing the branch for review and merge.

### Out Of Scope

- Replacing the placeholder summary-generation implementation.
- Expanding the agent provider matrix beyond what already exists.
- Frontend or mobile changes.

## Files Likely To Change

### Tests And Docs

- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `README.md`
- Modify: `docs/architecture/backend-context.md`

### API Runtime

- Modify: `apps/api/.env.example`
- Modify: `apps/api/app/services/livekit_dispatch_service.py`

### Agent Runtime

- Modify: `apps/agent/agent/prompt_builder.py`
- Modify: `apps/agent/agent/providers.py`

### Local-Only Files

- Keep untracked: `apps/api/.env`
- Keep untracked: `apps/agent/.env`

## Chunk 1: Clean The Existing Branch State

### Task 1: Freeze The Current Source Of Truth

**Files:**
- Inspect: `apps/api/.env.example`
- Inspect: `apps/agent/agent/prompt_builder.py`
- Inspect: `apps/agent/agent/providers.py`
- Inspect: `apps/api/tests/test_deployment_readiness.py`

- [ ] **Step 1: Capture the exact worktree diff**

Run:

```bash
git status --short --branch
git diff -- apps/api/.env.example apps/agent/agent/prompt_builder.py apps/agent/agent/providers.py
```

Expected: shows only the currently known WIP edits and local `.env` files.

- [ ] **Step 2: Decide intended behavior for the API env contract**

Answer explicitly before editing:
- Does the API actually require any AI provider key today?
- If not, the test must change to match the architecture.
- If yes, restore the variable in `apps/api/.env.example` and document why the API needs it.

- [ ] **Step 3: Decide whether the agent prompt/provider edits are intentional**

If intentional:
- add or update tests first
- then keep and finish them

If not intentional:
- revert only those tracked edits

- [ ] **Step 4: Commit the branch-state cleanup**

Run:

```bash
git add apps/api/.env.example apps/agent/agent/prompt_builder.py apps/agent/agent/providers.py apps/api/tests/test_deployment_readiness.py
git commit -m "chore: reconcile backend merge-readiness branch state"
```

Expected: source edits are committed intentionally or removed intentionally.

## Chunk 2: Restore Full Automated Verification

### Task 2: Fix The Deployment Readiness Contract

**Files:**
- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `apps/api/.env.example`
- Modify: `README.md`

- [ ] **Step 1: Write the expected contract explicitly in the failing test**

Keep the test focused on runtime requirements that are actually consumed by the API and agent today.

- [ ] **Step 2: Run the targeted test first**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_deployment_readiness.py -v
```

Expected: FAIL before the contract is reconciled.

- [ ] **Step 3: Make the minimal doc/config fix**

Update only the source of truth needed to make the contract coherent:
- the test
- the `.env.example`
- any README wording that depends on that contract

- [ ] **Step 4: Re-run the targeted test**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/test_deployment_readiness.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/tests/test_deployment_readiness.py apps/api/.env.example README.md
git commit -m "test: align deployment readiness contract"
```

### Task 3: Reconfirm Full API And Agent Health

**Files:**
- Verify only

- [ ] **Step 1: Run the full API suite**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
```

Expected: PASS

- [ ] **Step 2: Run the full agent suite**

Run:

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
```

Expected: PASS

- [ ] **Step 3: If either suite fails, debug one failure at a time**

Follow:
- reproduce
- identify root cause
- add or update tests if the intended behavior changed
- re-run the smallest relevant test first

- [ ] **Step 4: Commit any required fixes**

```bash
git add apps/api apps/agent
git commit -m "fix: restore backend verification green"
```

## Chunk 3: Validate External Integration Risk

### Task 4: Confirm The LiveKit Dispatch Payload Contract

**Files:**
- Modify: `apps/api/app/services/livekit_dispatch_service.py`
- Modify: `apps/api/tests/livekit/test_dispatch_webhook.py`
- Modify: `apps/api/tests/livekit/test_dispatch_service.py`
- Modify: `docs/architecture/backend-context.md`

- [ ] **Step 1: Inspect the current LiveKit webhook assumption**

Review `sip.trunkPhoneNumber` and `sip.phoneNumber` handling in `LiveKitDispatchService`.

- [ ] **Step 2: Decide the correct contract source**

One of:
- real staging payload from LiveKit
- current official LiveKit webhook payload example already available locally
- a documented narrowed assumption if staging access is not yet available

- [ ] **Step 3: Update tests to reflect the confirmed contract**

Add the exact attribute names and fallback behavior the service must support.

- [ ] **Step 4: Apply the minimal implementation or comment cleanup**

Remove the `UNVERIFIED` state once the contract is proven or replace it with a precise documented limitation.

- [ ] **Step 5: Run the targeted LiveKit tests**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest tests/livekit/test_dispatch_service.py tests/livekit/test_dispatch_webhook.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/services/livekit_dispatch_service.py apps/api/tests/livekit/test_dispatch_service.py apps/api/tests/livekit/test_dispatch_webhook.py docs/architecture/backend-context.md
git commit -m "fix: verify livekit dispatch payload contract"
```

## Chunk 4: Run Staging Smoke And Prepare Merge

### Task 5: Execute The Existing Staging Checklist

**Files:**
- Modify: `docs/architecture/backend-context.md`
- Reference: `README.md`

- [ ] **Step 1: Prepare staging credentials and environment**

Required inputs:
- Clerk issuer, JWT secret, webhook secret
- Stripe webhook secret and subscription objects
- Telnyx API key and active/disabled connection IDs
- LiveKit URL, API key, API secret
- Agent provider credentials
- reachable Postgres, Redis, and object storage

- [ ] **Step 2: Run the documented smoke path**

At minimum verify:
- API boot with real Postgres and Redis
- agent worker boot with real LiveKit credentials
- Clerk webhook delivery
- Stripe renewal reset path
- Telnyx number provisioning and active/disabled switch
- LiveKit dispatch from participant webhook
- one end-to-end forwarded call with transcript, recording metadata, notification persistence, and usage deduction

- [ ] **Step 3: Record exact results**

Update `docs/architecture/backend-context.md` with:
- what was executed
- date of execution
- what passed
- what failed
- what remains blocked by missing credentials or services

- [ ] **Step 4: Re-run the full test suites after any staging-driven fixes**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v

cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv run python -m pytest -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/architecture/backend-context.md apps/api apps/agent README.md
git commit -m "docs: record backend staging smoke status"
```

### Task 6: Final Merge Gate

**Files:**
- Verify only

- [ ] **Step 1: Confirm clean worktree**

Run:

```bash
git status --short --branch
```

Expected: no tracked source changes pending; local `.env` files may remain untracked.

- [ ] **Step 2: Review branch delta against `main`**

Run:

```bash
git diff --stat main..HEAD
git log --oneline --decorate main..HEAD
```

Expected: backend foundation commits only, with cleanup commits on top.

- [ ] **Step 3: Request code review before merge**

Review scope:
- API foundation
- agent foundation
- merge-readiness fixes
- staging notes

- [ ] **Step 4: Merge to `main` only after review and verification are complete**

Recommended:

```bash
git checkout main
git merge --no-ff feature/backend-foundation-mvp
```

Expected: `main` now contains the backend foundation branch.

## Follow-Up Branch After Merge

Create a separate branch for product refinements:

- Replace placeholder `SummaryService` behavior with the real summary-generation path.
- Revisit agent prompt quality changes after the foundation branch has landed.
- Add any additional provider or runtime refinements that are not required for merge readiness.
