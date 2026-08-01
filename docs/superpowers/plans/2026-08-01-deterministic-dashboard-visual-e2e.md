# Deterministic Dashboard Visual E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the full dashboard visual E2E suite deterministic by passing a fail-closed local/test reference time through the existing dashboard metrics `now` seam, then refresh and verify only intentionally stale workspace snapshots.

**Architecture:** `Settings` owns an optional, timezone-aware dashboard reference time and rejects it outside development/test. The dashboard route forwards that value to the service's existing `now` parameter. The local E2E runner supplies one fixed instant to the API service only, so normal runtime behavior continues to use the real UTC clock.

**Tech Stack:** Python 3.13, Pydantic Settings, FastAPI, pytest, Docker Compose, Next.js 16, Playwright 1.61, Node 22.22.

## Global Constraints

- Follow the approved design in `docs/superpowers/specs/2026-08-01-deterministic-dashboard-visual-e2e-design.md`.
- Use test-driven development for every behavior change: write the focused failing test, observe the expected failure, implement the smallest complete behavior, and rerun the focused test green.
- Preserve default production behavior: when the setting is absent, dashboard metrics use the real current UTC time.
- Permit the override only when `APP_ENV` is exactly `development` or `test`; fail closed for every other value.
- Accept only timezone-aware datetimes. Never silently interpret a naive timestamp.
- Pass the setting to the API service only. Do not expose it to the worker, voice agent, web service, migration service, or production Compose file.
- Do not add a general clock abstraction, a dependency, DOM rewriting, API mocking, relative SQL timestamps, or broad snapshot masking.
- Do not change API response schemas or `scripts/seed-local-e2e-calls.sql`.
- Keep realtime disabled and preserve accepted review choices 11C, 12C, and 18A.
- Refresh only snapshots proven stale because of the fixed-time correction or already-approved workspace header/navigation changes. Entry/activation snapshots remain byte-for-byte unchanged. The original expectation that every configuration snapshot remain unchanged is superseded only for the four images named in the approved execution addendum below.
- Do not weaken API, agent, or shared-library coverage ratchets.
- Preserve the user's untracked `Presvo_frontend/` directory. Never add, inspect recursively, modify, delete, or commit it.
- Use `UV_CACHE_DIR=/tmp/uv-cache` for local uv commands; this changes only uv's disposable cache location.
- Commit after each task only when its focused tests and static checks pass.

---

### Task 1: Add the fail-closed API reference-time boundary

**Files:**

- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/app/routers/dashboard.py`
- Add: `apps/api/tests/dashboard/test_dashboard_reference_time.py`
- Modify: `apps/api/tests/dashboard/test_dashboard_metrics.py`

- [ ] **Step 1: Write focused setting-validation tests**

Create `test_dashboard_reference_time.py` with an explicit fixed value and a minimal valid settings factory:

```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.config import Settings


REFERENCE_TIME = datetime(2026, 7, 29, 12, tzinfo=UTC)
BASE_SETTINGS = {
    "database_url": "sqlite+aiosqlite://",
    "redis_url": "redis://localhost:6379/0",
}


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_dashboard_reference_time_is_accepted_only_in_safe_environments(
    app_env: str,
) -> None:
    settings = Settings(
        app_env=app_env,
        dashboard_metrics_reference_time=REFERENCE_TIME,
        **BASE_SETTINGS,
    )

    assert settings.dashboard_metrics_reference_time == REFERENCE_TIME


@pytest.mark.parametrize("app_env", ["staging", "production", "preview"])
def test_dashboard_reference_time_is_rejected_outside_safe_environments(
    app_env: str,
) -> None:
    with pytest.raises(
        ValidationError,
        match="DASHBOARD_METRICS_REFERENCE_TIME",
    ):
        Settings(
            app_env=app_env,
            dashboard_metrics_reference_time=REFERENCE_TIME,
            **BASE_SETTINGS,
        )


def test_dashboard_reference_time_rejects_a_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="test",
            dashboard_metrics_reference_time=datetime(2026, 7, 29, 12),
            **BASE_SETTINGS,
        )


def test_invalid_dashboard_reference_time_is_redacted() -> None:
    sentinel = "do-not-echo-this-reference-time"

    with pytest.raises(ValidationError) as caught:
        Settings(
            app_env="test",
            dashboard_metrics_reference_time=sentinel,
            **BASE_SETTINGS,
        )

    assert sentinel not in str(caught.value)
```

Also assert that omitting the setting yields `None`; this proves that production's real-clock behavior remains opt-in and unchanged.

- [ ] **Step 2: Replace the route test's hidden monkeypatch with a forwarding assertion**

In `test_dashboard_metrics_resolve_owner_and_return_exact_contract`, add the FastAPI `test_app` fixture and override `get_settings` with a copied test setting whose `dashboard_metrics_reference_time` is `current`. Keep a small method spy only to record the received keyword and call the original implementation with that received value:

```python
observed_now: datetime | None = None

async def get_metrics_at_configured_time(
    service: DashboardMetricsService,
    user_id: UUID,
    *,
    now: datetime | None = None,
):
    nonlocal observed_now
    observed_now = now
    return await original_get_metrics(service, user_id, now=now)
```

Use `test_app.dependency_overrides[get_settings]` rather than mutating the cached global setting. Assert `observed_now == current` after the existing exact response assertion. This test must fail before the route is wired because it will receive `None`.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/dashboard/test_dashboard_reference_time.py \
  tests/dashboard/test_dashboard_metrics.py::test_dashboard_metrics_resolve_owner_and_return_exact_contract \
  -q
```

Expected: the new settings tests fail because the field does not exist, and the route-forwarding assertion fails because the route does not pass `now`.

- [ ] **Step 4: Implement the smallest explicit boundary**

In `config.py`, import `AwareDatetime`, add the optional field, and add a separate validator so the security policy is visible and independently testable:

```python
dashboard_metrics_reference_time: AwareDatetime | None = None

@model_validator(mode="after")
def validate_dashboard_metrics_reference_time(self) -> Self:
    if (
        self.dashboard_metrics_reference_time is not None
        and self.app_env not in {"development", "test"}
    ):
        raise ValueError(
            "DASHBOARD_METRICS_REFERENCE_TIME is supported only in "
            "development or test"
        )
    return self
```

In `dashboard.py`, inject `Settings` through `Depends(get_settings)` and forward only the approved value:

```python
metrics = await service.get_metrics(
    identity.internal_user_id,
    now=settings.dashboard_metrics_reference_time,
)
return DashboardMetricsResponse.model_validate(asdict(metrics))
```

Do not change `DashboardMetricsService`; its existing `now: datetime | None` seam already has the correct default behavior.

- [ ] **Step 5: Run focused and local API quality gates**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/dashboard/test_dashboard_reference_time.py \
  tests/dashboard/test_dashboard_metrics.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check \
  app/core/config.py app/routers/dashboard.py \
  tests/dashboard/test_dashboard_reference_time.py \
  tests/dashboard/test_dashboard_metrics.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy \
  app/core/config.py app/routers/dashboard.py
```

Expected: all commands pass; no existing dashboard metric assertion changes.

- [ ] **Step 6: Commit the API seam**

```bash
git add apps/api/app/core/config.py \
  apps/api/app/routers/dashboard.py \
  apps/api/tests/dashboard/test_dashboard_reference_time.py \
  apps/api/tests/dashboard/test_dashboard_metrics.py
git diff --cached --check
git commit -m "fix(api): support deterministic dashboard metrics time"
```

---

### Task 2: Scope the fixed time to the disposable E2E API

**Files:**

- Modify: `apps/api/tests/test_deployment_readiness.py`
- Modify: `compose.dev.yaml`
- Modify: `scripts/run-local-e2e.sh`

- [ ] **Step 1: Write a deployment-boundary regression test**

Add a test near the existing local-E2E runner assertions. Parse the service sections using the same explicit section boundaries already used in this test module and assert:

```python
reference_setting = "DASHBOARD_METRICS_REFERENCE_TIME"
reference_export = (
    "export DASHBOARD_METRICS_REFERENCE_TIME=2026-07-29T12:00:00Z"
)

assert reference_export in runner
assert f"{reference_setting}:" in api_service
for service in (worker_service, agent_service, web_service):
    assert reference_setting not in service
assert reference_setting not in production_compose
```

Include the migration service section in the negative assertions. Avoid a new YAML parser dependency: the repository already treats these exact source boundaries as deployment contracts.

- [ ] **Step 2: Run the deployment test and verify RED**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_deployment_readiness.py \
  -k dashboard_metrics_reference_time -q
```

Expected: failure because neither the runner export nor API Compose pass-through exists.

- [ ] **Step 3: Add the API-only E2E setting**

In `scripts/run-local-e2e.sh`, add alongside the fixed disposable ports:

```sh
export DASHBOARD_METRICS_REFERENCE_TIME=2026-07-29T12:00:00Z
```

In only the `api.environment` mapping in `compose.dev.yaml`, add:

```yaml
DASHBOARD_METRICS_REFERENCE_TIME:
```

The null Compose value intentionally means "pass through from the invoking environment when present." Leaving it unset keeps ordinary local development on the real clock. Do not add a fallback in Compose and do not add the key to any other service.

- [ ] **Step 4: Prove source and rendered Compose scoping**

Run:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest \
  tests/test_deployment_readiness.py \
  -k 'dashboard_metrics_reference_time or local_e2e_runner_is_disposable' -q
cd ../..
sh -n scripts/run-local-e2e.sh
DASHBOARD_METRICS_REFERENCE_TIME=2026-07-29T12:00:00Z \
  docker compose -f compose.dev.yaml config --format json
env -u DASHBOARD_METRICS_REFERENCE_TIME \
  docker compose -f compose.dev.yaml config --format json
```

Inspect both JSON documents. With the variable set, exactly the API service contains the fixed value. Without it, no service contains a non-empty override. `compose.yaml` remains untouched and contains no matching key.

- [ ] **Step 5: Commit E2E wiring**

```bash
git add apps/api/tests/test_deployment_readiness.py compose.dev.yaml \
  scripts/run-local-e2e.sh
git diff --cached --check
git commit -m "test(e2e): pin dashboard metrics reference time"
```

---

### Task 3: Refresh and verify only intentionally stale visual baselines

**Files:**

- Modify: `apps/web/tests/e2e/dashboard-visual.spec.ts-snapshots/dashboard-desktop-dark.png`
- Modify: `apps/web/tests/e2e/dashboard-visual.spec.ts-snapshots/dashboard-desktop-light.png`
- Modify: `apps/web/tests/e2e/dashboard-visual.spec.ts-snapshots/dashboard-mobile-dark.png`
- Modify: `apps/web/tests/e2e/dashboard-visual.spec.ts-snapshots/dashboard-mobile-light.png`
- Modify if visibly stale: `apps/web/tests/e2e/dashboard-calls-visual.spec.ts-snapshots/call-detail-desktop-light.png`
- Modify if visibly stale: `apps/web/tests/e2e/dashboard-calls-visual.spec.ts-snapshots/call-detail-mobile-light.png`
- Modify if visibly stale: `apps/web/tests/e2e/dashboard-calls-visual.spec.ts-snapshots/calls-desktop-light.png`
- Modify if visibly stale: `apps/web/tests/e2e/dashboard-calls-visual.spec.ts-snapshots/calls-mobile-light.png`
- Modify if visibly stale: `apps/web/tests/e2e/dashboard-calls-visual.spec.ts-snapshots/live-call-preview-desktop-light.png`
- Modify if visibly stale: `apps/web/tests/e2e/dashboard-calls-visual.spec.ts-snapshots/live-call-preview-mobile-dark.png`

- [ ] **Step 1: Install locked web dependencies with the repository's supported Node version**

Run from the repository root:

```bash
NPM_CONFIG_CACHE=/tmp/presvo-npm-cache \
  npm exec --yes --package=node@22.22.0 -- \
  node /home/mo/.nvm/versions/node/v26.3.0/lib/node_modules/npm/bin/npm-cli.js \
  ci --prefix apps/web
```

This uses Node 22.22 for the install without changing `package.json` or the lockfile. If the hard-coded system npm path is unavailable, resolve the installed npm CLI path read-only before continuing; do not change dependency versions.

- [ ] **Step 2: Run the complete non-update E2E suite and preserve RED evidence**

Run:

```bash
NPM_CONFIG_CACHE=/tmp/presvo-npm-cache \
  npm exec --yes --package=node@22.22.0 -- sh scripts/run-local-e2e.sh
```

Expected: application/API assertions pass. Any remaining failures are image mismatches attributable to the already-approved stable `Agent` navigation label or active-caller header. Confirm the fixed metrics are stable at the seeded reference instant (including the expected 4/2/1m40 summary and activity-bar placement) before updating any PNG.

- [ ] **Step 3: Inspect every failing visual diff before accepting it**

For each Playwright failure, inspect the expected, actual, and diff PNGs. Reject the update if it shows missing data, layout breakage, clipped text, loading/error states, inconsistent headers, or any unexplained pixel change. Record the exact list of justified snapshot paths before running update mode.

- [ ] **Step 4: Generate candidate baselines**

Run:

```bash
E2E_UPDATE_SNAPSHOTS=1 NPM_CONFIG_CACHE=/tmp/presvo-npm-cache \
  npm exec --yes --package=node@22.22.0 -- sh scripts/run-local-e2e.sh
```

Expected: all E2E phases pass while updating baselines. The four dashboard snapshots may change for both deterministic metrics and the navigation/header updates. Up to six calls/detail/preview snapshots may change only for the same navigation/header updates.

- [ ] **Step 5: Audit the exact snapshot delta**

Run:

```bash
git diff --name-only -- apps/web/tests/e2e
```

Inspect every changed PNG. Assert that these directories have no changes:

```text
apps/web/tests/e2e/entry-activation-visual.spec.ts-snapshots/
apps/web/tests/e2e/configuration-visual.spec.ts-snapshots/
```

Do not stage or retain any update outside the ten workspace snapshot paths listed in this task. If a non-workspace baseline changes, stop and diagnose rather than accepting it.

- [ ] **Step 6: Prove the refreshed suite passes without update mode**

Run the full command again without either update environment variable:

```bash
NPM_CONFIG_CACHE=/tmp/presvo-npm-cache \
  npm exec --yes --package=node@22.22.0 -- sh scripts/run-local-e2e.sh
```

Expected: every Playwright test passes and the runner removes its Compose project and volumes through its cleanup trap.

- [ ] **Step 7: Commit only reviewed baselines**

Stage the exact justified PNG paths, then verify the cached name list before committing:

```bash
git add apps/web/tests/e2e/dashboard-visual.spec.ts-snapshots/*.png \
  apps/web/tests/e2e/dashboard-calls-visual.spec.ts-snapshots/*.png
git diff --cached --name-only
git diff --cached --check
git commit -m "test(web): refresh deterministic workspace visuals"
```

It is harmless if `git add` names an unchanged file; Git stages only changed content. The cached name list must contain no unreviewed files.

## Approved Execution Addendum — 21A / 22A / 23A

Task 3 produced new evidence after the original diagnosis and the user approved
three narrow follow-ons. This section explicitly supersedes the conflicting
Task 3 snapshot restrictions and affected assertions above; the original RED
procedure and diagnosis remain as historical context.

- **21A:** install and pause Playwright's clock at
  `2026-07-29T12:00:00Z` before navigating only the two live-preview visual
  cases, and assert exact seeded `01:42` in `Preview call overview`. Do not mask
  the timer or alter production timer behavior.
- **22A:** use route-specific semantic sentinels, substantive main-content and
  geometry checks, and saved/default-state assertions before configuration
  screenshots. Refresh exactly `assistant-desktop-light.png`,
  `assistant-preview-mobile-dark.png`, `billing-desktop-light.png`, and
  `billing-mobile-dark.png`; account and entry/activation configuration images
  remain unchanged.
- **23A:** replace the invalid inactive-heading locator with exact lifecycle
  assertions inside the semantic `Account status` region. Keep the browser
  back/forward assertion. Because its failure repeated, fix the production
  URL-owned state defect by synchronizing call-history draft query, status, and
  range from changed server props.

The final accepted update therefore contains fourteen PNGs: four dashboard,
six calls/detail/live-preview, and the four configuration images above. After
the successful update run, acceptance requires two consecutive complete
non-update runner invocations; interrupted or partial runs do not count. Both
proofs passed all 46 checks and performed project-scoped cleanup.

Task 4's initially written `mypy app tests` and `mypy agent tests` commands also
overreached the repository's configured static-analysis boundaries. The
authoritative CI gates are `mypy app` and `mypy agent`; tests are exercised by
their complete pytest/coverage gates. No package markers, ignores, flags, or
test-double rewrites are authorized to make the exploratory commands pass.

---

### Task 4: Record the decision, run full regression gates, review, and clean up

**Files:**

- Modify: `docs/engineering/2026-07-30-agent-api-review-decisions.md`
- Update ignored working records under: `.superpowers/sdd/2026-07-31-shared-wire-contracts/`

- [ ] **Step 1: Update the durable review record**

Document Issue 20 with:

- the calendar-time drift root cause;
- options 20A/20B/20C and their concrete tradeoffs;
- the user's selection of 20A;
- the fail-closed development/test-only API boundary;
- why realtime, production clocks, SQL fixtures, and other services remain unchanged;
- the exact updated snapshot scope and passing non-update E2E evidence.

Keep temporary commands/logs in ignored SDD records, not in the durable decision document.

- [ ] **Step 2: Run shared-package gates**

Run:

```bash
cd libs/shared
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check .
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy src tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

Expected: the existing 184-test shared suite passes with no coverage or static-analysis regression.

- [ ] **Step 3: Start disposable API test dependencies**

First confirm the exact names are unused. Then start only:

```bash
docker run --rm -d --name presvo-contracts-test-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ai_call \
  -p 127.0.0.1:55432:5432 postgres:17.8-bookworm
docker run --rm -d --name presvo-contracts-test-redis \
  -p 127.0.0.1:56379:6379 redis:7.4.7-alpine
```

Wait for PostgreSQL and Redis readiness. Do not attach persistent volumes and do not reuse or remove similarly named user containers.

- [ ] **Step 4: Run full API gates**

Run:

```bash
cd apps/api
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/ai_call \
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/ai_call \
REDIS_URL=redis://127.0.0.1:56379/0 \
TEST_REDIS_URL=redis://127.0.0.1:56379/0 \
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check .

DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/ai_call \
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/ai_call \
REDIS_URL=redis://127.0.0.1:56379/0 \
TEST_REDIS_URL=redis://127.0.0.1:56379/0 \
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app

DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/ai_call \
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/ai_call \
REDIS_URL=redis://127.0.0.1:56379/0 \
TEST_REDIS_URL=redis://127.0.0.1:56379/0 \
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

Expected: at least the existing 2,172 API tests plus the new tests pass, no tests skip, and the API coverage ratchet remains green.

- [ ] **Step 5: Run full agent gates without enabling credentialed evaluation**

Run:

```bash
cd apps/agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check .
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

Expected: the existing 664 agent tests pass, the four explicitly accepted credentialed LiveKit evaluations remain deselected, and the coverage ratchet stays green. Realtime remains disabled.

- [ ] **Step 6: Run deployment and build-boundary verification**

From the repository root, run:

```bash
sh -n scripts/run-local-e2e.sh
docker compose -f compose.dev.yaml config --quiet
docker compose -f compose.yaml config --quiet
docker build -f apps/api/Dockerfile --target runtime .
docker build -f apps/agent/Dockerfile --target runtime .
```

Also rerun the existing root-context/Docker deployment-readiness tests. Confirm production Compose contains no dashboard reference-time setting and both runtime images still build from the monorepo root.

- [ ] **Step 7: Review the implementation against the approved baseline**

Use the `code-review` skill against base commit `43bd3cb`. Check standards and spec compliance, with special attention to environment fail-closed behavior, dependency override cleanup, Compose service scoping, snapshot overreach, and error-value redaction.

Fix every Important or Critical finding test-first, rerun affected focused and full gates, and record the evidence. Do not accept a workaround that bypasses the settings boundary or visual assertions.

- [ ] **Step 8: Commit the durable decision record**

```bash
git add docs/engineering/2026-07-30-agent-api-review-decisions.md
git diff --cached --check
git commit -m "docs: record deterministic dashboard e2e decision"
```

- [ ] **Step 9: Clean only disposable artifacts created by this plan**

Stop and remove the exact two temporary test containers after confirming their names. Verify the `presvo-e2e` Compose project has no remaining containers, networks, or volumes. Remove only the task-created `apps/web/node_modules`, Playwright output, `/tmp/presvo-npm-cache`, and task-specific temporary Docker images if they are no longer needed.

Do not use a global Docker prune. Do not remove user containers, images, volumes, caches, or `Presvo_frontend/`.

- [ ] **Step 10: Final invariant and worktree checks**

Run:

```bash
git diff --check 43bd3cb..HEAD
git status --short
git log --oneline 43bd3cb..HEAD
rg -n "DASHBOARD_METRICS_REFERENCE_TIME|REALTIME_ENABLED|NEXT_PUBLIC_REALTIME_ENABLED" \
  apps/api compose.yaml compose.dev.yaml scripts/run-local-e2e.sh
```

Expected:

- the worktree is clean;
- only planned commits exist after the design checkpoint;
- the fixed time is accepted in development/test and appears in rendered E2E API configuration only;
- production Compose and all non-API services remain free of the setting;
- the full E2E suite passed in non-update mode after the snapshot refresh;
- realtime remains disabled;
- no dependency or lockfile changed;
- no disposable test resource remains.
