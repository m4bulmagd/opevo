# Backend Foundation Merge Readiness Design

## Summary

This spec defines the shortest safe path to merge `feature/backend-foundation-mvp` into `main` without expanding scope. The branch already contains the backend foundation for the MVP API and LiveKit agent worker, but it is not yet merge-ready because the worktree is dirty, one API test is failing, and external provider assumptions have not been validated end-to-end.

The design intentionally separates branch stabilization from product refinements. Phase 1 is limited to cleaning and verifying the backend foundation so it can land on `main`. Phase 2 is follow-up work for higher-level product behavior such as richer summary generation.

## Goal

Make `feature/backend-foundation-mvp` safe to merge into `main` by restoring a clean branch, reestablishing full automated verification, and explicitly validating the remaining external integration assumptions.

## Non-Goals

- Reworking the backend architecture.
- Adding new product features beyond what is required to stabilize the current foundation branch.
- Implementing full LLM-based post-call summaries in this merge path.
- Adding frontend or mobile work.

## Current State

- `main` remains docs-only.
- `feature/backend-foundation-mvp` contains the implemented API and agent skeletons and is 24 commits ahead of `main`.
- API tests are nearly green, but `apps/api/tests/test_deployment_readiness.py` fails because `apps/api/.env.example` drifted from the expected contract.
- The worktree still has uncommitted edits in the agent prompt/provider area and API env example.
- Manual staging smoke checks have not yet been executed with real Clerk, Stripe, Telnyx, and LiveKit credentials.

## Proposed Approach

### Approach A: Merge-readiness first, product refinements later

Stabilize the current backend branch as-is, fix only the blockers that prevent safe merge, run verification, then create a follow-up branch for summary generation and any prompt/provider refinements.

Pros:
- Fastest path to getting the backend foundation onto `main`
- Lowest scope risk
- Keeps unresolved product improvements from blocking branch integration

Cons:
- The first merge lands with a placeholder summary implementation
- A second follow-up branch is still required

### Approach B: Finish summary generation before merge

Expand the current branch to include real summary generation and related env/config updates before merging.

Pros:
- Fewer follow-up branches
- Closer to the original product behavior immediately after merge

Cons:
- Increases scope and merge delay
- Mixes branch stabilization with feature expansion
- Raises integration risk while the current branch is still not clean

### Recommendation

Use Approach A. The branch already carries enough backend implementation value that the priority should be integrating it safely. Summary generation should be tracked as follow-up product work after the foundation branch is merged and stable.

## Design

### Phase 1: Branch Stabilization

The merge path should focus on four workstreams:

1. Resolve branch drift:
   - Decide whether API-side AI provider env vars belong in `apps/api/.env.example`.
   - Make the documentation/test contract match the intended architecture.
   - Remove ambiguity in the current uncommitted changes by either finishing them or reverting them intentionally.

2. Restore full automated verification:
   - API test suite must pass completely.
   - Agent test suite must pass completely.
   - Any branch-specific local config files such as `.env` remain untracked.

3. Validate high-risk external assumptions:
   - Confirm the LiveKit SIP attribute names used by `LiveKitDispatchService`.
   - Confirm the deployment docs still match the compose and Docker contracts.

4. Execute staging smoke verification:
   - Run the existing checklist against a real staging setup.
   - Record what was verified and any remaining blockers in `docs/architecture/backend-context.md`.

### Phase 2: Follow-Up Product Refinements

After the merge, open a separate branch for:

- Replacing the placeholder `SummaryService` implementation with the real summary generation path.
- Any prompt quality changes in the agent.
- Any additional provider options that are not required for the merge itself.

## Acceptance Criteria

Phase 1 is complete when all of the following are true:

- `feature/backend-foundation-mvp` has no unintended uncommitted source changes.
- API tests pass.
- Agent tests pass.
- The deployment readiness docs/tests align with the intended env contract.
- The LiveKit dispatch attribute mapping is either validated in staging or narrowed to a documented, tested payload contract.
- The staging checklist has been executed or every unexecuted step is blocked only by missing credentials/environment access and is recorded explicitly.
- The branch is ready for code review and merge to `main`.

## Testing Strategy

- Run the full API pytest suite from `apps/api`.
- Run the full agent pytest suite from `apps/agent`.
- Re-run targeted tests whenever the env contract, dispatch logic, or prompt/provider edits change.
- Use the staging smoke checklist for the external systems that local tests cannot prove.

## Branch Strategy

- Keep all Phase 1 work on `feature/backend-foundation-mvp`.
- Merge Phase 1 into `main` once verification is complete.
- Create a separate feature branch from updated `main` for Phase 2 summary and product refinement work.
