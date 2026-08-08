# LiveKit Dispatch Error Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unreachable `LiveKitDispatchConfigurationError` path and
reverify the final Issue 6A clean-cut endpoint.

**Architecture:** Configuration fails before provider composition. Reachable
LiveKit dispatch failures use the typed `ProviderFailure` contract; unknown
implementation defects propagate without being relabeled. No replacement
compatibility exception or provider-interface member is introduced.

**Tech Stack:** Python 3.13, FastAPI, ARQ, SQLAlchemy, pytest, Ruff, mypy,
PostgreSQL 17, and Redis 7.

## Global Constraints

- Owner decision 49A is the controlling direction.
- Preserve provider selection, retry policy, queue topology, realtime,
  deployment, external accounts, database contracts, and worker source layout.
- Never inspect or modify real `.env` files or the fixed `/tmp` voice, Telnyx,
  and Clerk override files.
- Do not touch `Opevo_frontend/` or
  `.worktrees/shadcn-activation-preview`.
- Use test-first characterization, explicit code, aggressive but genuine DRY,
  and no compatibility alias.
- Each implementation task receives independent Spec and Standards review.

---

### Task 16: Remove the dead dispatch configuration exception

**Files:**
- Modify: `apps/api/app/providers/livekit_dispatch/livekit.py`
- Modify: `apps/api/app/workers/outbox/_livekit_delivery.py`
- Modify: `apps/api/tests/workers/test_livekit_delivery.py`
- Modify: `apps/api/tests/integration/test_outbox_delivery.py`

**Interfaces:**
- Removes: `LiveKitDispatchConfigurationError` and its two translations to
  `OutboxDeliveryError("dispatch_configuration")`.
- Preserves: reachable terminal/retryable `ProviderFailure` mapping, dispatch
  reconciliation, account revalidation, retry recovery, and legitimate
  `dispatch_configuration` outcomes from token/configuration validation.
- Preserves: unknown implementation errors as their original exception.

- [x] **Step 1: Prove the compatibility path has no producer**

```bash
rg -n "LiveKitDispatchConfigurationError" apps/api/app apps/api/tests
! rg -n "raise LiveKitDispatchConfigurationError" apps/api/app
```

Expected: production contains only the class, one import, and two catch
branches; tests are the only producers. Any production raise stops the task.

- [x] **Step 2: Characterize unknown implementation errors explicitly**

Remove the compatibility-exception import from
`tests/workers/test_livekit_delivery.py`. Retarget its initial-list, create, and
recovery-list synthetic tests to `RuntimeError` and object-identity assertions:

```python
untyped_error = RuntimeError("UNTYPED_PROVIDER_DEFECT_SENTINEL")
provider = _Provider(trace, list_results=[untyped_error])

with pytest.raises(RuntimeError) as caught:
    await _ensure(provider, trace)

assert caught.value is untyped_error
assert trace == ["validate", "list"]
```

The create test retains the validate/list/reconcile/create trace. The recovery
test retains two list calls following a retryable create failure. Do not assert
the exception message and do not add a broad production catch.

Delete the four-case integration test
`test_livekit_dispatch_configuration_errors_are_durable_terminal_failures`.
Keep `test_untyped_livekit_provider_value_error_is_durable_internal_defect`,
which already covers customer and verification dispatch across list and create
phases. Keep all reachable terminal/retryable `ProviderFailure` matrices.

- [x] **Step 3: Run characterization before deleting production branches**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_livekit_delivery.py \
  tests/providers/test_livekit_dispatch_provider.py
```

Expected: unknown-error and `ProviderFailure` behavior pass before production
deletion, proving they do not depend on the compatibility catches.

- [x] **Step 4: Delete only the dead production surface**

Delete from the concrete provider:

```python
class LiveKitDispatchConfigurationError(RuntimeError):
    pass
```

Delete the import and these two branches from `_livekit_delivery.py`:

```python
except LiveKitDispatchConfigurationError:
    raise OutboxDeliveryError(
        "dispatch_configuration",
        retryable=False,
    ) from None
```

Leave adjacent `ProviderFailure` handling unchanged. Add no alias, interface
type, broad catch, or replacement error.

- [x] **Step 5: Run focused GREEN and static checks**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_livekit_delivery.py \
  tests/providers/test_livekit_dispatch_provider.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/providers/livekit_dispatch/livekit.py \
  app/workers/outbox/_livekit_delivery.py \
  tests/workers/test_livekit_delivery.py \
  tests/integration/test_outbox_delivery.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
! rg -n "LiveKitDispatchConfigurationError" app tests
```

Run the focused integration cases against separately named disposable services:

```bash
! ss -ltn | rg -q ':55469|:56399'
docker run --detach --rm --name opevo-issue6a-task16-postgres \
  --env POSTGRES_DB=ai_call_test --env POSTGRES_USER=postgres \
  --env POSTGRES_PASSWORD=postgres --publish 127.0.0.1:55469:5432 \
  --health-cmd='pg_isready -U postgres -d ai_call_test' \
  --health-interval=5s --health-timeout=5s --health-retries=10 \
  postgres:17.8-bookworm
docker run --detach --rm --name opevo-issue6a-task16-redis \
  --publish 127.0.0.1:56399:6379 --health-cmd='redis-cli ping' \
  --health-interval=5s --health-timeout=5s --health-retries=10 \
  redis:7.4.7-alpine
until test "$(docker inspect --format '{{.State.Health.Status}}' \
  opevo-issue6a-task16-postgres)" = healthy; do sleep 1; done
until test "$(docker inspect --format '{{.State.Health.Status}}' \
  opevo-issue6a-task16-redis)" = healthy; do sleep 1; done
export APP_ENV=test
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55469/ai_call_test
export TEST_DATABASE_URL="$DATABASE_URL"
export REDIS_URL=redis://127.0.0.1:56399/0
export TEST_REDIS_URL="$REDIS_URL"
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/integration/test_outbox_delivery.py \
  -k 'dispatch_provider_failure or untyped_livekit_provider'
docker rm --force opevo-issue6a-task16-postgres opevo-issue6a-task16-redis
test -z "$(docker ps -a --filter name=opevo-issue6a-task16 --format '{{.Names}}')"
```

Cleanup runs even when a test fails; only these exact container names may be
removed.

- [x] **Step 6: Commit Task 16**

```bash
git add apps/api/app/providers/livekit_dispatch/livekit.py \
  apps/api/app/workers/outbox/_livekit_delivery.py \
  apps/api/tests/workers/test_livekit_delivery.py \
  apps/api/tests/integration/test_outbox_delivery.py
git commit -m "refactor(api): remove dead LiveKit dispatch error path"
```

The commit must contain exactly these four files. `git diff --check`, protected
path scans, and the post-commit normal worktree status must be clean.

### Task 17: Reverify the final clean-cut endpoint

**Files:**
- Modify: `docs/superpowers/plans/2026-08-06-explicit-runtime-composition.md`
- Modify: `docs/engineering/2026-07-30-agent-api-review-decisions.md`
- Modify: this plan only to check completed steps and record its endpoint

**Interfaces:**
- Consumes: independently approved Task 16.
- Produces: exact post-49A tests, coverage, cleanup, durable evidence, and final
  complete-range Standards and Spec verdicts.

- [x] **Step 1: Run frozen and static gates**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
cd ../agent
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
```

Any lockfile mutation, lint error, or type error stops the task.

- [x] **Step 2: Start exact isolated dependencies**

```bash
! ss -ltn | rg -q ':55469|:56399'
docker run --detach --rm --name opevo-issue6a-final3-postgres \
  --env POSTGRES_DB=ai_call_test --env POSTGRES_USER=postgres \
  --env POSTGRES_PASSWORD=postgres --publish 127.0.0.1:55469:5432 \
  --health-cmd='pg_isready -U postgres -d ai_call_test' \
  --health-interval=5s --health-timeout=5s --health-retries=10 \
  postgres:17.8-bookworm
docker run --detach --rm --name opevo-issue6a-final3-redis \
  --publish 127.0.0.1:56399:6379 --health-cmd='redis-cli ping' \
  --health-interval=5s --health-timeout=5s --health-retries=10 \
  redis:7.4.7-alpine
until test "$(docker inspect --format '{{.State.Health.Status}}' \
  opevo-issue6a-final3-postgres)" = healthy; do sleep 1; done
until test "$(docker inspect --format '{{.State.Health.Status}}' \
  opevo-issue6a-final3-redis)" = healthy; do sleep 1; done
```

Export `APP_ENV=test` and test-only database and Redis URLs using ports 55469
and 56399. Record the original seven-service state.

- [x] **Step 3: Run the complete API gate**

```bash
cd apps/api
export APP_ENV=test
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55469/ai_call_test
export TEST_DATABASE_URL="$DATABASE_URL"
export REDIS_URL=redis://127.0.0.1:56399/0
export TEST_REDIS_URL="$REDIS_URL"
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=app --cov-report=term-missing --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json --baseline coverage-baseline.json
```

All API tests must pass with zero skips/failures. Both the stored ratchet and
the stricter 91.93% line / 80.39% branch endpoint must pass. If removal of fully
covered dead code lowers either percentage, stop and present a meaningful
uncovered path; never lower the target or add a coverage-only assertion.

- [x] **Step 4: Run complete agent and cross-runtime gates**

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  --cov=agent --cov-report=term-missing --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json --baseline coverage-baseline.json
cd ../api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/integration/test_agent_runtime_transcript_durability.py \
  tests/integration/test_local_activation_to_number.py \
  tests/integration/test_outbox_delivery.py \
  tests/integration/test_worker_queue_isolation.py \
  tests/livekit/test_dispatch_webhook.py \
  tests/workers/test_outbox_architecture.py \
  tests/test_composition_architecture.py
```

Exactly four credentialed agent evaluations may skip; no other skip is allowed.
Agent coverage may not regress from 89.44% line / 74.62% branch.

- [x] **Step 5: Clean exact resources and audit final state**

```bash
docker rm --force opevo-issue6a-final3-postgres opevo-issue6a-final3-redis
test -z "$(docker ps -a --filter name=opevo-issue6a-final3 --format '{{.Names}}')"
docker compose -f compose.dev.yaml ps
! rg -n "LiveKitDispatchConfigurationError" apps/api/app apps/api/tests
test -z "$(git ls-files '.superpowers/sdd/2026-08-06-explicit-runtime-composition/task-*-report.md')"
git diff --check
git status --short
```

The original seven services must match preflight. All six Task 3–8 reports must
still exist locally as ignored/untracked files. Protected-path scans must be
empty; no fixed `/tmp` override or real environment file may be used.

- [x] **Step 6: Commit exact durable evidence separately**

Record Task 16's hash, exact final counts/coverage, cleanup, and owner decision
49A in the original Issue 6 plan and engineering ledger. Keep Issue 6A
Implemented and preserve 1A/14A/16A, realtime, deployment, database, and worker
extraction decisions. Check completed steps in this plan. Commit only those
three documentation files.

- [ ] **Step 7: Run the definitive complete-range two-axis review**

Two fresh read-only reviewers compare
`c56187794d3c12e0daca833f5f8f2e729e98eead...HEAD`. Standards uses repository
rules plus the Fowler smell baseline. Spec uses the approved design, both plans,
the engineering ledger, and every owner decision through 49A. Both must report
zero findings before integration is offered. Any new finding returns to the
owner; no unapproved fix loop begins.

#### Task 17 final verification evidence before definitive review

Owner decision **49A** selected the clean removal of the unreachable
`LiveKitDispatchConfigurationError` compatibility path without an alias,
replacement catch, provider-policy change, or broad exception translation.
Commit `d7f2a0d942844ff500f884ab29106cb01ac5bf15` implements that exact removal.
Its focused characterization, provider, worker, integration, Ruff, mypy, and
zero-reference gates passed, and fresh independent Spec and Standards reviews
both reported zero findings.

The first complete post-49A API gate exposed a genuine list/create contract
coverage asymmetry rather than a product defect. The owner approved **50A**:
parameterize the existing malformed LiveKit response contract across both list
and create operations, preserving explicit provider, operation, disposition,
error-class, context, and exception-cause assertions. Test-only commit
`6dc96ff6ee6f5e6272573f2b0315b46a34ea9637` implements that DRY parity test;
no production behavior changed and no coverage threshold was lowered.

Fresh final verification at `6dc96ff6ee6f5e6272573f2b0315b46a34ea9637`
produced:

- both frozen lock checks, complete API/agent Ruff checks, and mypy checks for
  187 API and 16 agent source files passed;
- 3,089 API tests passed with zero skips or failures, covering 11,817 of 12,853
  statements (91.939625%, reported as 91.94%) and 2,685 of 3,340 branches
  (80.389222%, reported as 80.39%); the stored ratchet passed;
- 714 agent tests passed with exactly the four approved credentialed LiveKit
  evaluation skips, covering 1,347 of 1,506 statements (89.442231%, reported
  as 89.44%) and 297 of 398 branches (74.623116%, reported as 74.62%); the
  stored ratchet passed;
- the exact cross-runtime slice passed 104 tests; and
- the API and agent architecture guards passed 34 and seven tests,
  respectively.

Ports 55469 and 56399 and the exact `opevo-issue6a-final3-postgres` and
`opevo-issue6a-final3-redis` names were clear before startup. Both disposable
services became healthy and only those two exact containers were removed after
the gates. No `final3` container remained. The original seven `bmad-opevo`
services matched preflight afterward: web, API, PostgreSQL, Redis, and MinIO
were healthy, while the existing worker and agent remained running.

The obsolete-exception, tracked-report, protected-path, `git diff --check`, and
normal-status audits were clean. All six Task 3-8 reports remain present
locally, ignored, and untracked. No protected path, real environment file,
fixed `/tmp` voice/Telnyx/Clerk override, deployment, provider account, or
non-isolated database was read or changed. The complete post-50A code endpoint
is
`c56187794d3c12e0daca833f5f8f2e729e98eead...6dc96ff6ee6f5e6272573f2b0315b46a34ea9637`;
this evidence update is documentation-only and intentionally follows that
endpoint. Step 7 remains pending until two fresh read-only reviewers assess the
complete range including this durable evidence commit.

#### Post-51A/52A/53A final6 evidence

The owner-approved correction plan at commit `7624f34` followed the post-50A
endpoint. **51A** closed and canonicalized the API and agent environment domains;
commit `e591478f7f23e8dad621b6197fff2a55eed6a7a2` completed its test-first RED/GREEN
cycle and received independent Spec and Standards approvals with zero findings.
**52A** added the test-only late-composition-cancellation characterization in
commit `219155c674cab3d5cc3f64d587c76cd2b68d9bfb`; existing production behavior
already retained the exact cancellation and closed earlier resources once in
reverse order, and independent Spec and Standards/test-quality reviews approved
the commit with zero findings.

The first complete post-52A API run exposed one stale dashboard-test parameter:
3,106 tests passed and only the `preview` dashboard-reference-time case failed
because 51A correctly rejected that custom environment first. **53A** removed
only that dashboard-specific overlap while retaining explicit API and agent
constructor/process `preview` rejection. Test-only commit
`7c93cb521b8e98bbf51b5e4c2226da943b5142e5` completed GREEN and received
independent Spec and Standards/test-quality approvals with zero findings.

Fresh final6 verification at `7c93cb521b8e98bbf51b5e4c2226da943b5142e5`
passed both frozen lock checks, complete Ruff, and mypy for 187 API and 16 agent
source files. The API passed 3,106 tests with zero skips or failures and one
dependency warning, covering 11,826/12,862 statements (91.945265%) and
2,688/3,342 branches (80.430880%). The agent passed 732 tests with exactly four
approved credentialed skips, covering 1,360/1,517 statements (89.650626%) and
299/400 branches (74.75%). Both stored ratchets and the stricter API
91.93%/80.39% and agent 89.44%/74.62% endpoints passed. The exact cross-runtime
slice passed 104 tests; API and agent architecture guards passed 38 and seven;
and explicit canonical/invalid environment slices passed 16 and 16.

Ports 55472/56402 and the exact final6 names were clear before startup. Both
isolated services became healthy, only those exact containers were removed,
and final container/network/volume/port checks were empty. The original seven
running services and pre-existing exited-success one-shots matched preflight by
ID and state. Obsolete-error/runtime, tracked-report, protected-path, hash,
diff, and status audits were clean; all six Task 3-8 reports remain local,
ignored, and untracked. No protected environment, fixed override, deployment,
provider account, non-isolated database, lock, baseline, or threshold changed.
All Python test/coverage evidence was produced outside the filesystem sandbox;
the discarded in-sandbox timeout attempt is not product-failure evidence.

The corrected code range is
`c56187794d3c12e0daca833f5f8f2e729e98eead...7c93cb521b8e98bbf51b5e4c2226da943b5142e5`.
This four-file evidence update follows it. Step 7 remains pending until fresh
complete-range Spec and Standards reviews include the evidence commit and both
report zero findings.
