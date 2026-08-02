# API Provisioning Coverage Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace incidental async-scheduling coverage of four phone-number provisioning repository decisions with direct, deterministic behavioral tests and restore stable clean/poisoned full-suite coverage.

**Architecture:** Add one focused repository test module that reuses the existing SQLite `db_session`, real ORM models, and the real `PhoneNumberProvisioningRepository`. Keep the existing PostgreSQL concurrency tests and every production path unchanged; use controlled temporary mutations to prove the characterization tests can catch each broken decision, then verify two clean reports and one controlled-poison report have identical per-file coverage sets.

**Tech Stack:** Python 3.13, pytest 9, pytest-anyio, SQLAlchemy 2 async ORM, aiosqlite, PostgreSQL 17.8, Redis 7.4.7, pytest-cov, Ruff, mypy, uv.

## Global Constraints

- Implement approved decisions **3A** and **3A-1A** exactly as specified in `docs/superpowers/specs/2026-08-02-api-provisioning-coverage-stabilization-design.md`.
- Do not modify provisioning state-machine behavior, locking, transaction boundaries, worker scheduling, provider behavior, or any other production code. If a direct test demonstrates a real production defect, stop and ask the user before changing production code.
- Preserve the existing PostgreSQL concurrency and lifecycle tests unchanged.
- Reuse the existing function-scoped SQLite `db_session`; do not add another engine or schema fixture.
- Keep each state test explicit. Share only repeated model seeding and commit/reload mechanics; do not introduce parameterized method dispatch or test-only production APIs.
- Prove test strength with temporary mutations, restore `apps/api/app/repositories/phone_number_provisioning_repository.py` byte-for-byte after every mutation, and commit no production mutation.
- Keep realtime and activation flow disabled. Do not enable or implement realtime.
- Do not alter deployment, frontend, agent, dependencies, lockfiles, Compose service declarations, or production configuration.
- Do not touch `/home/mo/code/ai/bmad-opevo/Presvo_frontend/` or `.worktrees/shadcn-activation-preview`.
- Never inspect, rewrite, or delete a developer's real `.env`. The controlled poison proof may create only a pre-checked absent `apps/api/.env` inside this isolated worktree and must remove that exact file afterward.
- Use `UV_CACHE_DIR=/tmp/uv-cache` for every `uv` command. Do not push, deploy, open a PR, or modify remote branches.
- Never lower either value in `apps/api/coverage-baseline.json`. Raise a value only to a two-decimal `ROUND_DOWN` floor shared by all three identical reports.
- Create and clean up only the exact disposable PostgreSQL/Redis containers and coverage artifacts named in Task 2. Do not prune Docker or remove any differently named resource.

## File Map

- Create `apps/api/tests/repositories/test_phone_number_provisioning_repository.py`: four direct, deterministic behavioral tests plus two small setup/reload helpers.
- Modify conditionally `apps/api/coverage-baseline.json`: raise a ratchet only when two clean reports and one poison report prove the same higher floor.
- Modify `docs/superpowers/specs/2026-08-02-api-provisioning-coverage-stabilization-design.md`: record implementation evidence only after every acceptance gate passes.
- Modify this plan only to mark completed steps after their evidence exists.
- Do not modify `apps/api/app/repositories/phone_number_provisioning_repository.py`; temporary mutation checks must always restore it before moving to the next step.

---

### Task 1: Add deterministic provisioning repository state tests

**Files:**
- Create: `apps/api/tests/repositories/test_phone_number_provisioning_repository.py`
- Temporarily mutate and fully restore: `apps/api/app/repositories/phone_number_provisioning_repository.py:109-178`
- Test: `apps/api/tests/repositories/test_phone_number_provisioning_repository.py`

**Interfaces:**
- Consumes: `db_session: AsyncSession`, `PhoneNumberProvisioningRepository.mark_running`, `mark_succeeded`, `mark_pending`, and `mark_failed`.
- Produces: four always-running tests that deterministically protect the stable-key conflict and the three missing-row fallback contracts.

- [ ] **Step 1: Create the complete direct repository test module**

Use `apply_patch` to create this exact test boundary:

```python
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning
from app.models.user import User
from app.repositories.phone_number_provisioning_repository import (
    PhoneNumberProvisioningRepository,
    ProvisioningStateConflictError,
)


async def _user(db_session: AsyncSession, label: str) -> User:
    marker = f"{label}-{uuid4().hex}"
    user = User(
        clerk_user_id=f"provisioning-repository-{marker}",
        email=f"{marker}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _commit_and_reload(
    db_session: AsyncSession,
    user_id: UUID,
) -> PhoneNumberProvisioning:
    await db_session.commit()
    db_session.expunge_all()
    stored = await PhoneNumberProvisioningRepository(db_session).get_by_user_id(
        user_id
    )
    assert stored is not None
    return stored


@pytest.mark.anyio
async def test_mark_running_rejects_changed_stable_key_without_persisting_state(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "running-conflict")
    user_id = user.id
    db_session.add(
        PhoneNumberProvisioning(
            user_id=user_id,
            target_country_code="FR",
            status="queued",
            attempt_count=0,
            can_retry=False,
            last_error_reason="prior-state",
            last_error_payload={"source": "prior-state"},
            provider_operation_key="provider-operation-original",
        )
    )
    await db_session.commit()

    with pytest.raises(ProvisioningStateConflictError):
        await PhoneNumberProvisioningRepository(db_session).mark_running(
            user_id=user_id,
            target_country_code="BE",
            provider_operation_key="provider-operation-conflicting",
        )
    await db_session.rollback()
    db_session.expunge_all()

    stored = await PhoneNumberProvisioningRepository(db_session).get_by_user_id(
        user_id
    )
    assert stored is not None
    assert stored.target_country_code == "FR"
    assert stored.status == "queued"
    assert stored.attempt_count == 0
    assert stored.can_retry is False
    assert stored.last_error_reason == "prior-state"
    assert stored.last_error_payload == {"source": "prior-state"}
    assert stored.provider_operation_key == "provider-operation-original"


@pytest.mark.anyio
async def test_mark_succeeded_creates_and_persists_a_missing_provisioning_row(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "succeeded-fallback")
    phone_number = PhoneNumber(
        user_id=user.id,
        e164="+33123456789",
        country_code="FR",
        provider="telnyx",
        provider_number_id="provider-number-succeeded-fallback",
        is_active=True,
    )
    db_session.add(phone_number)
    await db_session.flush()

    created = await PhoneNumberProvisioningRepository(db_session).mark_succeeded(
        user_id=user.id,
        phone_number_id=phone_number.id,
        target_country_code="FR",
    )
    created_id = created.id
    stored = await _commit_and_reload(db_session, user.id)

    assert stored.id == created_id
    assert stored.target_country_code == "FR"
    assert stored.status == "succeeded"
    assert stored.attempt_count == 1
    assert stored.can_retry is False
    assert stored.phone_number_id == phone_number.id
    assert stored.last_error_reason is None
    assert stored.last_error_payload is None
    assert stored.provider_operation_key is None


@pytest.mark.anyio
async def test_mark_pending_creates_and_persists_a_missing_provisioning_row(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "pending-fallback")
    error_payload = {"provider_state": "pending"}

    created = await PhoneNumberProvisioningRepository(db_session).mark_pending(
        user_id=user.id,
        target_country_code="BE",
        reason="provider_pending",
        payload=error_payload,
    )
    created_id = created.id
    stored = await _commit_and_reload(db_session, user.id)

    assert stored.id == created_id
    assert stored.target_country_code == "BE"
    assert stored.status == "running"
    assert stored.attempt_count == 1
    assert stored.can_retry is False
    assert stored.phone_number_id is None
    assert stored.last_error_reason == "provider_pending"
    assert stored.last_error_payload == error_payload
    assert stored.provider_operation_key is None


@pytest.mark.anyio
async def test_mark_failed_creates_and_persists_a_missing_provisioning_row(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "failed-fallback")
    error_payload = {"error_class": "timeout"}

    created = await PhoneNumberProvisioningRepository(db_session).mark_failed(
        user_id=user.id,
        target_country_code="BE",
        reason="provider_timeout",
        payload=error_payload,
        can_retry=True,
    )
    created_id = created.id
    stored = await _commit_and_reload(db_session, user.id)

    assert stored.id == created_id
    assert stored.target_country_code == "BE"
    assert stored.status == "failed"
    assert stored.attempt_count == 1
    assert stored.can_retry is True
    assert stored.phone_number_id is None
    assert stored.last_error_reason == "provider_timeout"
    assert stored.last_error_payload == error_payload
    assert stored.provider_operation_key is None
```

Every expectation is hand-derived. The tests use real ORM persistence and the
real repository; no mock or source-text assertion is permitted.

- [ ] **Step 2: Run the unmutated focused tests and establish the characterization baseline**

From `apps/api`, run outside the restricted sandbox if the known aiosqlite
stream limitation prevents execution:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/repositories/test_phone_number_provisioning_repository.py
```

Expected: four tests pass with zero skips. Because the production behavior
predates the test, this is the characterization baseline; the controlled
mutation checks below provide the required proof that each test can fail.

- [ ] **Step 3: Prove the stable-key conflict test detects a removed conflict**

Use `apply_patch` to replace only the `mark_running` conflict line:

```python
                raise ProvisioningStateConflictError
```

with:

```python
                pass  # temporary mutation: conflict incorrectly accepted
```

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/repositories/test_phone_number_provisioning_repository.py::test_mark_running_rejects_changed_stable_key_without_persisting_state
```

Expected: fail because `ProvisioningStateConflictError` was not raised. Restore
the exact `raise` line with `apply_patch`, rerun the same node, expect pass, and
prove the production file is restored:

```bash
git diff --exit-code -- app/repositories/phone_number_provisioning_repository.py
```

- [ ] **Step 4: Prove each missing-row test detects removal of its fallback**

For `mark_succeeded`, temporarily use `apply_patch` to change only:

```python
        if provisioning is None:
```

to:

```python
        if False and provisioning is None:  # temporary mutation
```

Run its exact node and expect failure because a missing row cannot be updated:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/repositories/test_phone_number_provisioning_repository.py::test_mark_succeeded_creates_and_persists_a_missing_provisioning_row
```

Restore the exact condition with `apply_patch`, rerun the node, expect pass,
and run the production-file `git diff --exit-code` proof. Repeat this one-method
mutation/restore cycle independently for `mark_pending` and `mark_failed`,
running only the matching exact test node each time. Never leave more than one
mutation applied, and do not continue until the source diff is empty.

- [ ] **Step 5: Run the restored focused and static gates**

From `apps/api`, run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q \
  tests/repositories/test_phone_number_provisioning_repository.py \
  tests/test_settings_sources.py \
  tests/test_collection_environment.py \
  tests/auth/test_clerk_auth_config.py \
  tests/test_deployment_readiness.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
UV_CACHE_DIR=/tmp/uv-cache uv lock --check
git diff --check
git diff --exit-code -- app/repositories/phone_number_provisioning_repository.py
```

Expected: 187 focused tests pass with zero skips; Ruff, mypy, lock validation,
and both diff checks pass. The production repository has no diff.

- [ ] **Step 6: Commit the independently passing deterministic tests**

From the worktree root, verify the staged scope contains only the new test:

```bash
git status --short
git diff -- apps/api/tests/repositories/test_phone_number_provisioning_repository.py
git add apps/api/tests/repositories/test_phone_number_provisioning_repository.py
git diff --cached --name-only
git commit -m "test(api): stabilize provisioning repository coverage"
```

Expected staged path:

```text
apps/api/tests/repositories/test_phone_number_provisioning_repository.py
```

---

### Task 2: Prove stable clean and poisoned coverage and close the record

**Files:**
- Modify conditionally: `apps/api/coverage-baseline.json`
- Modify: `docs/superpowers/specs/2026-08-02-api-provisioning-coverage-stabilization-design.md`
- Modify: `docs/superpowers/plans/2026-08-02-api-provisioning-coverage-stabilization.md`
- Disposable and always removed: `apps/api/.env`, `apps/api/.coverage`, `apps/api/coverage.json`, `/tmp/presvo-api-provisioning-coverage-clean-1.json`, `/tmp/presvo-api-provisioning-coverage-clean-2.json`, `/tmp/presvo-api-provisioning-coverage-poison.json`

**Interfaces:**
- Consumes: Task 1 tests, the existing coverage checker, and exact disposable PostgreSQL/Redis services.
- Produces: two identical clean reports, one matching controlled-poison report, passing non-decreased coverage ratchets, exact cleanup proof, and an implemented design record.

- [ ] **Step 1: Verify isolated-worktree and resource preconditions**

From the worktree root, run:

```bash
git status --short --branch
git diff --check
test ! -e apps/api/.env
test ! -e apps/api/.coverage
test ! -e apps/api/coverage.json
test ! -e /tmp/presvo-api-provisioning-coverage-clean-1.json
test ! -e /tmp/presvo-api-provisioning-coverage-clean-2.json
test ! -e /tmp/presvo-api-provisioning-coverage-poison.json
test -z "$(docker ps -a --filter name=^/presvo-api-provisioning-coverage-postgres$ --format '{{.Names}}')"
test -z "$(docker ps -a --filter name=^/presvo-api-provisioning-coverage-redis$ --format '{{.Names}}')"
```

Expected: the worktree is clean, every exact artifact and container is absent,
and `apps/api/.env` is absent. If that dotenv exists, stop without reading,
modifying, or deleting it.

- [ ] **Step 2: Start and bound readiness for only the named services**

Start:

```bash
docker run -d \
  --name presvo-api-provisioning-coverage-postgres \
  -e POSTGRES_DB=ai_call_test \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 127.0.0.1:55460:5432 \
  postgres:17.8-bookworm
docker run -d \
  --name presvo-api-provisioning-coverage-redis \
  -p 127.0.0.1:56390:6379 \
  redis:7.4.7-alpine
```

Poll the PostgreSQL `pg_isready -U postgres -d ai_call_test` and Redis
`redis-cli ping` commands once per second for at most 60 attempts. Expected:
PostgreSQL accepts connections and Redis returns `PONG`. On failure, capture
logs only from the exact failing container and proceed directly to Step 8.

- [ ] **Step 3: Run and retain clean full-suite coverage report 1**

From `apps/api`, run outside the restricted sandbox with
`CLIENT_TEST_DATABASE_URL` explicitly absent:

```bash
env -u CLIENT_TEST_DATABASE_URL \
  APP_ENV=test \
  DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55460/ai_call_test \
  TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55460/ai_call_test \
  REDIS_URL=redis://127.0.0.1:56390/0 \
  TEST_REDIS_URL=redis://127.0.0.1:56390/0 \
  CLERK_ISSUER=https://clerk.example.com \
  CLERK_AUTHORIZED_PARTIES=https://app.example.com \
  CLERK_JWKS_URL=https://clerk.example.com/.well-known/jwks.json \
  UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest -q \
    --cov=app \
    --cov-report=term-missing \
    --cov-report=json:coverage.json
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python \
  ../../scripts/check_python_coverage.py check \
  --report coverage.json \
  --baseline coverage-baseline.json
cp coverage.json /tmp/presvo-api-provisioning-coverage-clean-1.json
```

Expected: 2,401 tests pass, zero skip, only the already-known Starlette/httpx
deprecation warning remains, and both coverage ratchets pass.

- [ ] **Step 4: Run and retain clean full-suite coverage report 2**

Repeat the exact Step 3 pytest and coverage-check commands, then copy the new
report to:

```bash
cp coverage.json /tmp/presvo-api-provisioning-coverage-clean-2.json
```

Compare every file's `executed_lines`, `missing_lines`, `executed_branches`,
and `missing_branches` as sets, plus report totals. Use a Python command that
loads both JSON files, asserts identical file-key sets and identical normalized
values for all four fields, asserts identical `totals`, and exits nonzero with
the differing filenames/fields in its assertion payload. Expected: equality.
If any value differs, preserve the reports, diagnose, and stop for a new user
decision; do not retry or lower a ratchet.

- [ ] **Step 5: Create only the approved controlled poison dotenv**

From the worktree root, rerun `test ! -e apps/api/.env`, then use `apply_patch`
to create exactly:

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://poison:poison@127.0.0.1:1/poison
REDIS_URL=redis://127.0.0.1:1/0
REALTIME_ENABLED=true
ACTIVATION_FLOW_ENABLED=true
AUTH_MODE=clerk
CLERK_ISSUER=https://poison.example.invalid
CLERK_AUTHORIZED_PARTIES=https://poison.example.invalid
CLERK_JWT_KEY=poison-static-key
CLERK_JWKS_URL=https://poison.example.invalid/jwks.json
```

Confirm only existence and ignored status with `test -e apps/api/.env` and
`git check-ignore apps/api/.env`. Do not inspect any other dotenv.

- [ ] **Step 6: Run and retain the controlled-poison full-suite report**

Repeat the exact Step 3 pytest and coverage-check commands while the controlled
dotenv exists, then copy:

```bash
cp coverage.json /tmp/presvo-api-provisioning-coverage-poison.json
```

Expected: 2,401 tests pass, zero skip, the same one known warning, and both
ratchets pass. Extend the Step 4 comparison to all three reports and require
identical file-key sets, identical normalized per-file line/branch sets, and
identical totals. Any difference is a gate failure; do not retry.

- [ ] **Step 7: Apply only a shared higher coverage floor**

Load all three reports through `scripts.check_python_coverage.py`, compute the
minimum exact line percentage and minimum exact branch percentage across the
three, and quantize each down with:

```python
value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
```

Compare those floors independently with `apps/api/coverage-baseline.json`.
Use `apply_patch` to raise only a strictly higher value; preserve an equal
value and never lower either one. Run the coverage checker separately against
all three retained reports and expect all three to pass.

- [ ] **Step 8: Remove only exact disposable resources and prove absence**

Use `apply_patch` to delete only the controlled `apps/api/.env` created in
Step 5. Remove the exact generated coverage files and exact named containers:

```bash
rm -f \
  apps/api/.coverage \
  apps/api/coverage.json \
  /tmp/presvo-api-provisioning-coverage-clean-1.json \
  /tmp/presvo-api-provisioning-coverage-clean-2.json \
  /tmp/presvo-api-provisioning-coverage-poison.json
docker stop \
  presvo-api-provisioning-coverage-postgres \
  presvo-api-provisioning-coverage-redis
docker rm \
  presvo-api-provisioning-coverage-postgres \
  presvo-api-provisioning-coverage-redis
```

Verify every listed file is absent and exact-name `docker ps -a` filters return
no container. Do not remove any other file, container, image, volume, network,
or cache.

- [ ] **Step 9: Record verified implementation evidence**

Only after Steps 1-8 pass, use `apply_patch` to change the stabilization design
status from `Approved design` to `Implemented and verified`, and append a
concise `## Implementation evidence` section recording:

- the four deterministic repository behaviors and mutation checks;
- focused/static/lock results;
- exact full-suite pass, skip, and warning counts for both clean runs and the
  poison run;
- identical per-file coverage-set proof and final line/branch measurements;
- the final ratchet values and the fact that neither decreased;
- exact cleanup scope and the unchanged production/realtime/deployment
  boundaries.

Mark every completed checkbox in this plan `[x]` only after its evidence exists.

- [ ] **Step 10: Run final checks and commit the verified record**

From the worktree root, run:

```bash
git diff --check
git status --short --branch
git diff --name-only e824f86
git diff --stat e824f86
```

Expected tracked scope after the design commit:

```text
apps/api/tests/repositories/test_phone_number_provisioning_repository.py
apps/api/coverage-baseline.json  # only if a shared higher floor was proved
docs/superpowers/specs/2026-08-02-api-provisioning-coverage-stabilization-design.md
docs/superpowers/plans/2026-08-02-api-provisioning-coverage-stabilization.md
```

Commit only verified documentation and any justified ratchet change:

```bash
git add \
  docs/superpowers/specs/2026-08-02-api-provisioning-coverage-stabilization-design.md \
  docs/superpowers/plans/2026-08-02-api-provisioning-coverage-stabilization.md
git add apps/api/coverage-baseline.json  # only when Step 7 changed it
git commit -m "docs: verify provisioning coverage stability"
```

- [ ] **Step 11: Perform one scoped final review**

Review `e824f86..HEAD` for correctness, test strength, DRY/explicit balance,
scope control, coverage-ratchet integrity, documentation accuracy, and exact
cleanup. Re-run any focused check needed to resolve a concrete concern. If the
review is clean, finish with `git status --short --branch` and report the exact
commits and evidence. If the review finds an issue, present it with options and
obtain user direction before another implementation change.
