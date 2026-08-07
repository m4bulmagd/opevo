# Environment and API Cancellation Corrections Plan

> Owner-approved corrections: **51A + 52A**. Execute test-first with fresh
> independent Spec and Standards reviews before the complete final gate.

**Goal:** Make runtime environment selection one canonical, fail-closed
settings contract in both executables and directly prove API partial-startup
cancellation cleanup.

**Scope:** `apps/api`, `apps/agent`, their tests, and durable review evidence.
Realtime enablement, deployment, provider accounts, queue policy, database
contracts, worker extraction, coverage thresholds, and protected paths remain
out of scope.

## Task 20: Implement 51A at both settings boundaries

**Production files:**

- `apps/api/app/core/config.py`
- `apps/agent/agent/config.py`

**Test files:** use the existing API settings/composition/router suites and
agent settings/runtime-validation suites; do not create a broad new abstraction.

1. Add RED tests proving:
   - `development`, `test`, `staging`, and `production` are accepted;
   - case and surrounding whitespace canonicalize to those exact lowercase
     values from constructor and process sources;
   - empty, whitespace-only, misspelled, and custom values fail settings
     construction;
   - API test variants ignore dotenv and remain hermetic through composition,
     including no ARQ pool or realtime fanout;
   - a normalized development variant registers development-only routes; and
   - the agent cannot bypass production validation through case, whitespace, or
     a misspelling.
2. In each executable, define a local explicit `Literal` environment domain and
   a `mode="before"` Pydantic field validator that applies `strip().casefold()`
   to strings. Reuse the API normalization helper in pre-dotenv source
   selection. Do not add a cross-app package or normalize again in composition.
3. Run the focused API and agent settings, validation, composition, and router
   suites; then Ruff, mypy, diff, protected-path, and zero-stale-reference
   checks. Commit only 51A.
4. Require fresh Spec and Standards approvals with zero findings.

## Task 21: Implement 52A as a focused contract test

**Test file:** `apps/api/tests/composition/test_api_composition.py`

1. Add a RED/characterization test that raises one retained
   `asyncio.CancelledError` from a late construction seam after all preceding
   owned resources have been registered.
2. Assert the exact exception object and absent cause escape. Assert every
   earlier resource closes exactly once in reverse registration order. Avoid
   message-only assertions and sleeps.
3. Do not change production code unless the test exposes a real defect; if it
   does, stop and return the finding to the owner before editing production.
4. Run the focused composition suite, Ruff, mypy, diff, and protected-path
   checks. Commit only 52A and require fresh Spec and Standards approvals with
   zero findings.

## Task 22: Reverify and record the final endpoint

1. Run frozen lock, full Ruff, and full mypy gates for API and agent.
2. Use uniquely named disposable PostgreSQL and Redis containers on preflight-
   free ports. Capture the original seven local services before startup and
   remove only the exact disposable pair afterward.
3. Run the complete API and agent suites with unchanged strict line/branch
   ratchets, exactly the four approved agent credential skips, cross-runtime
   tests, and API/agent architecture guards.
4. Confirm protected paths, real `.env`, fixed `/tmp` overrides, deploy state,
   provider accounts, and non-isolated databases were untouched. Retain all six
   approved local ignored Task 3-8 reports.
5. Record 51A/52A, commit hashes, exact counts, coverage, cleanup, and review
   results in the original plan, correction plan, and engineering ledger.
6. Run fresh complete-range Spec and Standards reviews. Both must report zero
   findings before integration choices are offered.

If any gate fails, stop and present the concrete issue and options. Never lower
coverage thresholds, add coverage-only assertions, or silently broaden scope.
