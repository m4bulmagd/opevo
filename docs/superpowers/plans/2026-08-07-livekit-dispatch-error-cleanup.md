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
- Do not touch `Presvo_frontend/` or
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

- [ ] **Step 1: Prove the compatibility path has no producer**

```bash
rg -n "LiveKitDispatchConfigurationError" apps/api/app apps/api/tests
! rg -n "raise LiveKitDispatchConfigurationError" apps/api/app
```

Expected: production contains only the class, one import, and two catch
branches; tests are the only producers. Any production raise stops the task.

- [ ] **Step 2: Characterize unknown implementation errors explicitly**

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

- [ ] **Step 3: Run characterization before deleting production branches**

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/workers/test_livekit_delivery.py \
  tests/providers/test_livekit_dispatch_provider.py
```

Expected: unknown-error and `ProviderFailure` behavior pass before production
deletion, proving they do not depend on the compatibility catches.

- [ ] **Step 4: Delete only the dead production surface**

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

- [ ] **Step 5: Run focused GREEN and static checks**

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

- [ ] **Step 6: Commit Task 16**

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

- [ ] **Step 1: Run frozen and static gates**

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

- [ ] **Step 2: Start exact isolated dependencies**

```bash
! ss -ltn | rg -q ':55468|:56398'
docker run --detach --rm --name opevo-issue6a-final2-postgres \
  --env POSTGRES_DB=ai_call_test --env POSTGRES_USER=postgres \
  --env POSTGRES_PASSWORD=postgres --publish 127.0.0.1:55468:5432 \
  --health-cmd='pg_isready -U postgres -d ai_call_test' \
  --health-interval=5s --health-timeout=5s --health-retries=10 \
  postgres:17.8-bookworm
docker run --detach --rm --name opevo-issue6a-final2-redis \
  --publish 127.0.0.1:56398:6379 --health-cmd='redis-cli ping' \
  --health-interval=5s --health-timeout=5s --health-retries=10 \
  redis:7.4.7-alpine
until test "$(docker inspect --format '{{.State.Health.Status}}' \
  opevo-issue6a-final2-postgres)" = healthy; do sleep 1; done
until test "$(docker inspect --format '{{.State.Health.Status}}' \
  opevo-issue6a-final2-redis)" = healthy; do sleep 1; done
```

Export `APP_ENV=test` and test-only database and Redis URLs using ports 55468
and 56398. Record the original seven-service state.

- [ ] **Step 3: Run the complete API gate**

```bash
cd apps/api
export APP_ENV=test
export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55468/ai_call_test
export TEST_DATABASE_URL="$DATABASE_URL"
export REDIS_URL=redis://127.0.0.1:56398/0
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

- [ ] **Step 4: Run complete agent and cross-runtime gates**

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

- [ ] **Step 5: Clean exact resources and audit final state**

```bash
docker rm --force opevo-issue6a-final2-postgres opevo-issue6a-final2-redis
test -z "$(docker ps -a --filter name=opevo-issue6a-final2 --format '{{.Names}}')"
docker compose -f compose.dev.yaml ps
! rg -n "LiveKitDispatchConfigurationError" apps/api/app apps/api/tests
test -z "$(git ls-files '.superpowers/sdd/2026-08-06-explicit-runtime-composition/task-*-report.md')"
git diff --check
git status --short
```

The original seven services must match preflight. All six Task 3–8 reports must
still exist locally as ignored/untracked files. Protected-path scans must be
empty; no fixed `/tmp` override or real environment file may be used.

- [ ] **Step 6: Commit exact durable evidence separately**

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
