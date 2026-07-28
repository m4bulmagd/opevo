# Presvo Dashboard, Calls, and Live-Call Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the Presvo overview, call history, call detail, and live-call experience into the production application while preserving tenant-safe backend truth and making every simulated interaction unmistakably Preview-only.

**Architecture:** Keep the existing authenticated Next.js workspace shell, request/session boundaries, call detail retrieval, recording URLs, and confirmed deletion action. Extend the dashboard metrics response with seven tenant-scoped local-day buckets and extend call listing with server-owned status/date filters. Render route state from URL parameters, keep production call data server-backed, and place all simulated live-call behavior inside a client component that owns only in-memory state and imports no API or server action.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, Tailwind CSS 4, shadcn/Radix primitives, Recharts 3, FastAPI, SQLAlchemy async, Pydantic, Pytest, Vitest, Testing Library, Playwright.

## Global Constraints

- `apps/web` remains the only deployed frontend; `/home/mo/code/ai/bmad-opevo/Presvo_frontend` stays read-only.
- Preserve the exact Phase 1 Presvo sage/off-white tokens, system typography, spacing, borders, shadows, cards, and hierarchy.
- Preserve the workspace shell, its search action, authentication, activation guard, account state, backend client, and route URLs.
- Dashboard totals, usage, number, runtime state, calls, transcripts, recording links, and deletion results remain backend-authoritative.
- All dashboard metrics and call filters must remain tenant-scoped and must exclude soft-deleted calls.
- Call deletion remains available only for terminal calls on active accounts and redirects only after backend acknowledgement.
- `/dashboard/live-call` must display `Preview` in the page heading and explanatory copy.
- The live-call Preview may use timers, state switches, transcript simulation, and local notes, but it must import no API client or server action and must issue no telephony, billing, lifecycle, or call-history request.
- Do not claim that a simulated call, note, summary, recording, or status was saved.
- Do not add `transition-all`; preserve reduced-motion behavior and 44px mobile targets.
- Keep one `main` landmark, hierarchical headings, accessible chart text, table/card semantics, visible focus, and no horizontal overflow at 390px.
- Follow red-green-refactor for every task and commit only after its listed checks pass.

---

## File Map

### Create

- `apps/web/src/components/dashboard/activity-chart.tsx`
- `apps/web/src/components/calls/call-history-list.tsx`
- `apps/web/src/components/calls/transcript-viewer.tsx`
- `apps/web/src/components/live-call/live-call-preview.tsx`
- `apps/web/src/app/(app)/dashboard/live-call/page.tsx`
- `apps/web/tests/app/live-call-preview.test.tsx`
- `apps/web/tests/components/activity-chart.test.tsx`
- `apps/web/tests/e2e/dashboard-calls-visual.spec.ts`
- `apps/web/tests/e2e/dashboard-calls-visual.spec.ts-snapshots/*`

### Modify

- `apps/api/app/repositories/call_repository.py`
- `apps/api/app/services/dashboard_metrics_service.py`
- `apps/api/app/schemas/dashboard.py`
- `apps/api/tests/dashboard/test_dashboard_metrics.py`
- `apps/api/app/routers/calls.py`
- `apps/api/app/services/call_history_service.py`
- `apps/api/tests/calls/test_call_history_search.py`
- `apps/api/tests/calls/test_call_history_api.py`
- `apps/web/src/lib/types/dashboard.ts`
- `apps/web/src/lib/types/calls.ts`
- `apps/web/src/lib/api/calls.ts`
- `apps/web/src/lib/calls/call-history-navigation.ts`
- `apps/web/src/app/(app)/dashboard/page.tsx`
- `apps/web/src/app/(app)/dashboard/calls/page.tsx`
- `apps/web/src/app/(app)/dashboard/calls/[callId]/page.tsx`
- `apps/web/src/components/dashboard/dashboard-metrics.tsx`
- `apps/web/src/components/dashboard/dashboard-call-ledger.tsx`
- `apps/web/src/components/dashboard/plan-usage-surface.tsx`
- `apps/web/src/components/dashboard/setup-checklist.tsx`
- `apps/web/src/components/calls/call-history-controls.tsx`
- `apps/web/src/components/calls/calls-table.tsx`
- `apps/web/src/components/calls/call-detail-card.tsx`
- `apps/web/src/components/calls/recording-panel.tsx`
- `apps/web/src/components/calls/transcript-panel.tsx`
- `apps/web/tests/app/home-page.test.tsx`
- `apps/web/tests/app/calls-page.test.tsx`
- `apps/web/tests/app/call-handoff.test.tsx`
- `apps/web/tests/lib/call-history-navigation.test.ts`
- `apps/web/tests/lib/calls-api.test.ts`
- `apps/web/tests/e2e/dashboard-visual.spec.ts`
- `scripts/run-local-e2e.sh`

### Preserve Without Behavioral Changes

- `apps/web/src/app/(app)/dashboard/calls/actions.ts`
- `apps/web/src/lib/api/backend-client.ts`
- `apps/api/app/services/recording_service.py`
- `apps/api/app/services/recording_lifecycle_service.py`
- account access policy and tenant identity resolution

---

## Task 1: Add Authoritative Seven-Day Dashboard Activity

**Interfaces:**

```py
@dataclass(frozen=True)
class DashboardActivityPoint:
    date: str
    label: str
    calls: int
```

`DashboardMetrics.daily_activity` is always seven ascending local-calendar-day points ending today. Calls after `now`, soft-deleted calls, and calls owned by another user do not contribute.

- [ ] **Step 1: Write failing repository/service/API tests**

Cover an empty seven-day window, calls on multiple local days, Europe/Paris offset handling, a DST boundary, tenant isolation, deleted calls, and the exact JSON contract.

- [ ] **Step 2: Run focused API tests and confirm failure**

```bash
cd apps/api
uv run pytest tests/dashboard/test_dashboard_metrics.py -q
```

- [ ] **Step 3: Implement cross-database bucket aggregation**

Build seven UTC boundary pairs in `DashboardMetricsService` from the resolved IANA timezone. Pass those pairs into a single repository aggregation query using conditional sums so SQLite and PostgreSQL share the same semantics. Map counts back to stable ISO dates and short English labels.

- [ ] **Step 4: Add the response schema and frontend type**

Add the nested Pydantic response model and the matching TypeScript type. Keep all existing fields unchanged.

- [ ] **Step 5: Run focused API and web contract tests**

```bash
cd apps/api
uv run pytest tests/dashboard/test_dashboard_metrics.py -q
cd ../web
npm run test:ci -- tests/lib/dashboard-api.test.ts
npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(api): add dashboard activity buckets"
```

---

## Task 2: Add Tenant-Safe Call Status and Date Filters

**Interfaces:**

```py
CallStatusFilter = Literal["completed", "in_progress", "failed"]
CallDateRange = Literal["7d", "30d"]
```

```ts
type CallHistoryNavigation = {
  query: string;
  status: "all" | "completed" | "in_progress" | "failed";
  range: "all" | "7d" | "30d";
  page: number;
  limit: 20;
  offset: number;
};
```

- [ ] **Step 1: Write failing API and navigation tests**

Assert terminal and grouped in-progress statuses, inclusive rolling date bounds, combinations with text search, matching totals, tenant isolation, deletion exclusion, invalid query validation, canonical URL parsing, and preservation of every active filter in pagination links.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
cd apps/api
uv run pytest tests/calls/test_call_history_search.py tests/calls/test_call_history_api.py -q
cd ../web
npm run test:ci -- tests/lib/call-history-navigation.test.ts tests/lib/calls-api.test.ts
```

- [ ] **Step 3: Implement backend filters**

Accept optional `status` and aliased `range` query parameters. Normalize them in the service, compute the rolling UTC cutoff from an injectable `now`, and add predicates in the repository while retaining the owner and `deleted_at IS NULL` predicates for both results and count.

- [ ] **Step 4: Extend frontend URL and API contracts**

Parse only allow-listed single values, reset invalid values to `all`, keep `page` canonical, and serialize filters in stable `q`, `status`, `range`, `page` order. Pass the normalized fields through `listCalls`.

- [ ] **Step 5: Run focused API/web verification**

```bash
cd apps/api
uv run pytest tests/calls/test_call_history_search.py tests/calls/test_call_history_api.py -q
cd ../web
npm run test:ci -- tests/lib/call-history-navigation.test.ts tests/lib/calls-api.test.ts
npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(calls): add production history filters"
```

---

## Task 3: Port the Presvo Operational Overview

- [ ] **Step 1: Add failing dashboard and chart tests**

Require the template hierarchy: compact header, four-stat band, `Call activity` surface, assistant/assigned-number surfaces, usage/setup row, and recent-call cards. For non-empty activity, require Recharts output plus a screen-reader text summary containing all seven dates and values. For unavailable metrics, render an honest unavailable chart state.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
cd apps/web
npm run test:ci -- tests/app/home-page.test.tsx tests/components/activity-chart.test.tsx
```

- [ ] **Step 3: Implement the chart and overview composition**

Port the template `ActivityChart` visual treatment using semantic CSS variables and Recharts. Give the visualization an accessible region name and include an ordered, visually hidden textual dataset. Recompose the dashboard into the template's `space-y-5`, `surface-card`, two-column activity/status, two-column usage/setup, and recent-calls hierarchy without inventing answer rate or missed-call metrics absent from the API.

- [ ] **Step 4: Preserve authoritative status and first-run behavior**

Keep `AnsweringStatusBanner`, activation routing, provisioning retry, setup checklist truth, current usage, and metric-failure isolation. Do not add a fake enable switch or fake active call.

- [ ] **Step 5: Verify focused behavior and quality**

```bash
cd apps/web
npm run test:ci -- tests/app/home-page.test.tsx tests/app/dashboard-onboarding.test.tsx tests/components/activity-chart.test.tsx tests/app/call-handoff.test.tsx
npm run typecheck
npm run check
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(web): port Presvo operations overview"
```

---

## Task 4: Port Call History Filters, Table, and Mobile Cards

- [ ] **Step 1: Add failing route/component tests**

Require one GET filter surface with search, status, and date controls; visible result count; stable URL-owned values; clear-filters behavior; desktop table; mobile call cards; semantic empty state; and filter-preserving pagination.

- [ ] **Step 2: Run calls-page tests and confirm failure**

```bash
cd apps/web
npm run test:ci -- tests/app/calls-page.test.tsx
```

- [ ] **Step 3: Implement the Presvo filter surface**

Use native GET controls so reload/back/forward behavior requires no duplicated client state. Keep labels and 44px targets. Pass all normalized filter fields to the backend and redirect out-of-range pages to the last canonical filtered page.

- [ ] **Step 4: Split desktop and mobile call presentations**

Render the template-aligned desktop table from `md` upward and linked, rounded card rows below `md`. Both versions expose caller, status, started time, duration, recording availability, summary/intent, and one unambiguous call-detail destination.

- [ ] **Step 5: Run focused verification**

```bash
cd apps/web
npm run test:ci -- tests/app/calls-page.test.tsx tests/lib/call-history-navigation.test.ts tests/lib/calls-api.test.ts
npm run typecheck
npm run check
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(web): port Presvo call history"
```

---

## Task 5: Port the Production Call Detail Experience

- [ ] **Step 1: Add failing call-detail tests**

Require a back link, generated-summary surface, native recording surface, locally searchable speaker-labelled transcript, metadata card, truthful unavailable/processing states, and the existing confirmed removal rules.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
cd apps/web
npm run test:ci -- tests/app/calls-page.test.tsx tests/app/call-handoff.test.tsx
```

- [ ] **Step 3: Implement the template composition**

Use a two-column desktop layout with summary, recording, and transcript on the primary column and metadata/removal on the side. Style the native audio element rather than simulating playback. Add a client transcript viewer whose only state is its filter string.

- [ ] **Step 4: Preserve deletion truth**

Keep the existing server action untouched. Keep non-terminal and inactive-account blocks, retry feedback, 404 behavior, and redirect-after-success.

- [ ] **Step 5: Run focused verification**

```bash
cd apps/web
npm run test:ci -- tests/app/calls-page.test.tsx tests/app/call-handoff.test.tsx
npm run typecheck
npm run check
```

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(web): port Presvo call detail"
```

---

## Task 6: Add the Isolated Live-Call Preview

- [ ] **Step 1: Write failing isolation and interaction tests**

Require the page heading and persistent `Preview` badge, an explanatory local-only notice, caller/status/elapsed presentation, transcript, caller details, notes, state controls, reset, and end-preview behavior. Mock `fetch` and assert it is never called while using every control.

- [ ] **Step 2: Run the preview test and confirm failure**

```bash
cd apps/web
npm run test:ci -- tests/app/live-call-preview.test.tsx
```

- [ ] **Step 3: Implement the local-only component**

Port the template layout and in-memory timer/transcript behavior with France-first fictional content. Keep stable initial data, clear timeouts/intervals, stop motion under reduced-motion preference, and label state-selector controls as Preview controls. Notes remain in memory and confirmation copy says `Saved in this preview only`.

- [ ] **Step 4: Add the production route**

Create `/dashboard/live-call` with route metadata and no data loader. The component must not import from `@/lib/api`, app actions, billing, account lifecycle, or telephony modules.

- [ ] **Step 5: Verify isolation and shell routing**

```bash
cd apps/web
npm run test:ci -- tests/app/live-call-preview.test.tsx tests/app/app-shell.test.tsx
rg -n '@/lib/api|actions|billing|telephony' src/components/live-call src/app/'(app)'/dashboard/live-call
npm run typecheck
npm run check
```

Expected: tests pass and the import scan is empty.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(web): add live-call Preview"
```

---

## Task 7: Add Visual and Browser Regression Coverage

- [ ] **Step 1: Add failing Playwright coverage**

Cover:

- overview desktop light and mobile dark;
- call history desktop light and mobile light;
- call detail desktop light and mobile light;
- live-call Preview desktop light and mobile dark;
- call filter submission and browser back/forward restoration;
- transcript search;
- preview state, notes, reset, and zero observed API requests after initial navigation;
- horizontal overflow at 390, 768, 1024, and 1440;
- reduced-motion route transitions.

- [ ] **Step 2: Run snapshots in update mode**

```bash
UPDATE_SNAPSHOTS=1 bash scripts/run-local-e2e.sh
```

- [ ] **Step 3: Inspect every new screenshot**

Confirm exact tokens, typography, spacing, borders, shadows, hierarchy, table/card breakpoints, clipped text, chart legibility, Preview labelling, and dark-mode contrast.

- [ ] **Step 4: Run the complete disposable browser lifecycle**

```bash
bash scripts/run-local-e2e.sh
```

- [ ] **Step 5: Commit**

```bash
git commit -m "test(web): lock Presvo dashboard and calls visuals"
```

---

## Task 8: Run the Phase 3 Production Gate

- [ ] **Step 1: Scan for migration regressions**

```bash
rg -n 'slate-|transition-all|TODO|FIXME|hardcoded.*date' \
  apps/web/src/app/'(app)'/dashboard/page.tsx \
  apps/web/src/app/'(app)'/dashboard/calls \
  apps/web/src/app/'(app)'/dashboard/live-call \
  apps/web/src/components/dashboard \
  apps/web/src/components/calls \
  apps/web/src/components/live-call
```

- [ ] **Step 2: Run API verification**

```bash
cd apps/api
uv run ruff check .
uv run mypy app
uv run pytest tests/dashboard/test_dashboard_metrics.py tests/calls/test_call_history_search.py tests/calls/test_call_history_api.py -q
```

- [ ] **Step 3: Run web verification**

```bash
cd apps/web
npm run check
npm run typecheck
npm run test:ci
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_Y2xlcmsuZXhhbXBsZS5jb20k \
CLERK_SECRET_KEY=ci-build-only-placeholder \
API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 \
NEXT_PUBLIC_APP_URL=http://127.0.0.1:3000 \
NEXT_PUBLIC_REALTIME_ENABLED=false \
  npm run build
```

- [ ] **Step 4: Verify phase invariants**

Confirm dashboard chart truth/accessibility, URL-owned filters, tenant-safe result counts, signed recording access, confirmed deletion, explicit Preview labelling, local-only preview state, and no preview network mutation.

- [ ] **Step 5: Mark this plan complete and commit**

```bash
git commit -m "docs: complete Presvo dashboard and calls phase"
```

---

## Phase 3 Completion Checklist

- [ ] Dashboard matches the Presvo overview hierarchy without invented production metrics.
- [ ] Seven-day activity is backend-authoritative, tenant-safe, timezone-correct, and accessible as text.
- [ ] Search/status/date/page state is URL-owned and survives reload/back/forward.
- [ ] Call history uses desktop table and mobile cards with truthful empty states.
- [ ] Call detail preserves private recording links, transcript order, status truth, and confirmed removal.
- [ ] Live call is persistently labelled Preview and all its controls remain local-only.
- [ ] Dashboard, history, detail, and Preview pass visual, dark-mode, reduced-motion, and overflow checks.
- [ ] Focused API tests, full web tests, production build, and complete Playwright lifecycle pass.
