# Deterministic Dashboard Visual E2E Design

**Date:** 2026-08-01
**Decision:** 20A
**Status:** Approved direction; written specification awaiting review

## Problem

The dashboard visual fixtures store calls at fixed July 24–28, 2026
timestamps, while `DashboardMetricsService` evaluates its seven-day window
against the real UTC clock. The dashboard screenshot therefore changes as
those calls age out of the window. The committed dashboard snapshots are also
legitimately stale because the stable `Agent` navigation label and active-call
workspace header were implemented after the snapshots were captured.

A snapshot-only refresh would pass temporarily and then fail again. Relative
SQL dates or DOM rewriting would couple unrelated call-history fixtures to the
dashboard snapshot and conceal real chart output. The selected design instead
uses the dashboard service's existing explicit `now` seam.

## Goals

- Make dashboard metrics and chart output deterministic in the isolated local
  E2E run.
- Keep ordinary development, staging, and production behavior based on the
  real current UTC time.
- Fail closed if a fixed dashboard time is configured outside an explicitly
  permitted local/test environment.
- Preserve the fixed SQL fixtures used by call-history and call-detail visual
  tests.
- Update only workspace baselines that changed intentionally, then prove the
  complete non-update E2E run passes.

## Non-goals

- Do not add a general application-wide clock framework.
- Do not change dashboard aggregation semantics or API response shapes.
- Do not mask, rewrite, or mock dashboard metrics in Playwright.
- Do not change call-history fixtures or baselines unless verification exposes
  a separate defect and the user approves it.
- Do not enable realtime or alter the accepted 11C, 12C, or 18A risks.

## Architecture

### Configuration boundary

`Settings` gains one optional, timezone-aware datetime:
`dashboard_metrics_reference_time`. Its environment name is
`DASHBOARD_METRICS_REFERENCE_TIME`.

The value is absent by default. When present, validation requires
`APP_ENV` to be `development` or `test`; staging, production, and unknown
environment names reject startup. Timezone-naive values are rejected rather
than silently interpreted.

The production Compose file does not expose the setting. Development Compose
passes it through to the API service only when the caller defines it; the
worker, agent, web, and migration services never receive it.

### Request data flow

The dashboard metrics route passes the validated optional reference time to
the existing `DashboardMetricsService.get_metrics(..., now=...)` parameter.
If the setting is absent, the service retains its current real-clock fallback.
An explicit method argument remains the sole time override; no module global,
monkeypatch, or alternate response path is introduced.

The isolated E2E runner exports the fixed aware instant
`2026-07-29T12:00:00Z` before rendering/starting its Compose project. That
instant includes all four fixed call fixtures in the same seven-day positions
used when the original dashboard metrics baseline was created.

```text
run-local-e2e.sh
  -> DASHBOARD_METRICS_REFERENCE_TIME
  -> development Compose API environment only
  -> Settings validation
  -> GET /api/dashboard/metrics
  -> DashboardMetricsService.get_metrics(now=fixed instant)
  -> stable real API response and stable rendered chart
```

## Error Handling and Safety

- A malformed or timezone-naive reference time fails settings validation with
  no raw environment value echoed.
- A configured reference time in staging, production, or an unknown
  environment fails startup.
- Normal API startup without the setting is unchanged.
- The E2E runner keeps its existing exact-project cleanup and primary exit
  status behavior.
- The fixed clock affects dashboard metrics only; call history, call detail,
  workers, and all provider behavior continue using their normal clocks.

## Test-Driven Implementation

Before production changes, focused tests will prove these failures:

1. Settings do not yet accept an aware local/test dashboard reference time or
   reject unsafe environment use with the intended policy.
2. The dashboard route does not yet forward the configured instant to the
   existing service seam.
3. The isolated E2E configuration does not yet scope and supply the fixed
   instant to the API only.

The smallest implementation will then make those tests green. Focused API
tests, Ruff, and mypy run before any snapshot update.

## Visual Baseline Procedure

1. Run the isolated E2E suite without update mode and retain the known failing
   dashboard actual/expected/diff as RED evidence.
2. Run update mode with the fixed API reference time.
3. Inspect every changed image and `git diff --name-only`. The four dashboard
   light/dark desktop/mobile baselines are expected to change. The six
   dashboard call/detail/preview baselines were also captured before the same
   workspace header/navigation commits and may change only where visual
   comparison proves that direct relationship. Entry/activation and later
   configuration baselines must remain unchanged.
4. Run the complete E2E suite again without update mode. All visual, semantic,
   overflow, navigation, activation, deactivation, and restart/resume cases
   must pass.

## Final Verification

- API lock check, Ruff, mypy, complete pytest suite, and line/branch coverage
  ratchets.
- Provider-free agent and shared-package gates, because this closes the full
  shared-contract branch.
- Compose render with realtime still disabled.
- Docker context safety/ownership probes and runtime image import checks.
- Full non-update E2E suite with zero failures.
- Clean Git worktree and no disposable containers, volumes, images,
  `node_modules`, Playwright output, or temporary npm/uv artifacts retained.

The credentialed LiveKit evaluation and a real agent-process E2E remain
explicitly excluded under accepted decisions 11C and 12C.
