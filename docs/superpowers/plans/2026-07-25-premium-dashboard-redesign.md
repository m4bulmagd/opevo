# Premium Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Presvo's authenticated dashboard, Calls, configured-agent, Billing, and Account surfaces into the approved premium “Quiet Confidence” product while preserving every existing functional, lifecycle, billing, security, and accessibility contract.

**Architecture:** Keep authenticated layouts and pages server rendered, add one owner-scoped aggregate metrics endpoint, and isolate pathname state, sheets, forms, and restrained Motion effects in small client components. Introduce a reusable product layer (`WorkspaceShell`, `PageIntro`, `StatusSurface`, `MetricBand`, `ProductSurface`, `DataLedger`, `SettingsSection`, and `ActionState`) above the existing accessible UI primitives. Use one request-scoped cached agent read for the shell and pages, one curated light/dark theme, and no dashboard-wide client store.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2 async, SQLite/PostgreSQL, pytest; Next.js 16 App Router, React 19 Server Components, TypeScript 5.9, Tailwind CSS 4, shadcn/base primitives, Zustand theme state, Motion 12 (`motion/react`), selectively adapted BeUI source, Vitest/Testing Library, and Playwright.

## Global Constraints

- Work directly on `main` as requested, but do not push, deploy, or operate real providers during this plan.
- Use the approved design at `docs/superpowers/specs/2026-07-25-premium-dashboard-redesign-design.md` as the source of truth.
- Do not redesign the landing page, auth pages, or activation journey.
- Do not change onboarding, number provisioning, call removal, recording, billing, subscription cancellation, account deactivation, or reactivation behavior.
- Account deactivation remains immediate, stops serving, cancels billing immediately, and releases the assigned number. Subscription-only cancellation remains effective at the paid-period end.
- Do not add tags, notes, filters, charts, export, live updates, follow-up completion, migrations, persisted analytics, caches, or background jobs.
- Use the configured `agent_name` in customer-facing navigation and headings; normalize a blank value to `Receptionist`.
- Keep complete agent names in accessible labels and tooltips even when the visual label truncates.
- Keep server data authoritative. Do not fetch route data in effects and do not add a global dashboard client store.
- Copy only the approved BeUI patterns, retain the `beui.dev` source attribution comments, and adapt them to Presvo tokens and accessibility requirements.
- Import Motion from `motion/react`. Wrap authenticated motion with `MotionConfig reducedMotion="user"` and give every bespoke transform animation a reduced-motion fallback.
- Never animate metrics from zero on first render. Animate only a value that changes after the component has mounted.
- Use opacity and transform for motion; avoid bounce, elastic overshoot, parallax, continuous effects, card-by-card entrances, and layout-property animation.
- Preserve one page-level `h1`, semantic landmarks, 44-by-44-pixel mobile targets, visible focus rings, focus trapping/restoration, Escape dismissal, and safe-area spacing.
- Add tests before implementation in every task. Confirm the intended RED failure, implement the minimum cohesive change, confirm GREEN, then commit.
- Before every commit run `git diff --check` and inspect `git diff --stat` so unrelated user changes are never swept into a commit.

---

## Task 1: Add the owner-scoped dashboard metrics domain query

**Files:**

- Create: `apps/api/tests/dashboard/test_dashboard_metrics.py`
- Create: `apps/api/app/services/dashboard_metrics_service.py`
- Modify: `apps/api/app/repositories/call_repository.py`
- Read/reuse: `apps/api/app/schemas/call_summary_projection.py`
- Read/reuse: `apps/api/app/repositories/business_profile_repository.py`
- Read/reuse: `apps/api/app/repositories/customer_activation_repository.py`

### Contract

The service boundary is:

```python
@dataclass(frozen=True)
class DashboardMetrics:
    timezone: str
    calls_today: int
    calls_last_7_days: int
    calls_previous_7_days: int
    calls_change_from_previous_7_days: int
    follow_up_flagged_last_7_days: int
    average_duration_seconds_last_7_days: int | None
```

The public service method is
`DashboardMetricsService.get_metrics(user_id: UUID, *, now: datetime | None = None) -> DashboardMetrics`.

The repository result is:

```python
@dataclass(frozen=True)
class DashboardMetricsAggregate:
    calls_today: int
    calls_last_7_days: int
    calls_previous_7_days: int
    follow_up_flagged_last_7_days: int
    average_duration_seconds_last_7_days: int | None
```

The repository method is
`CallRepository.dashboard_metrics(user_id: UUID, *, today_start_utc: datetime,
current_window_start_utc: datetime, previous_window_start_utc: datetime,
now_utc: datetime) -> DashboardMetricsAggregate`.

- [ ] Write test helpers against `client_database_url` that seed an owner, a second owner, an optional `BusinessProfile`, its matching `CustomerActivation`, and precisely timestamped calls. Open a session from that URL and call `DashboardMetricsService` directly so the same cases execute against SQLite by default and PostgreSQL when `CLIENT_TEST_DATABASE_URL` is set; do not use the always-SQLite `db_session` fixture for these cross-database cases.
- [ ] Write `test_metrics_use_confirmed_business_timezone_and_local_day_boundaries`.
  Freeze `now` at `2026-07-25T00:30:00+02:00`, use `Europe/Paris`, and place calls immediately before and after local midnight. Assert only the latter is in `calls_today`.
- [ ] Write `test_metrics_fall_back_to_europe_paris_without_a_confirmed_profile`.
  Cover no profile, a draft profile with no confirmation, and a stale confirmation revision. Only a profile whose `profile_confirmed_revision == profile.content_revision` may supply its timezone.
- [ ] Write `test_metrics_compare_adjacent_seven_local_day_windows`.
  Seed current-window, exact-boundary, previous-window, and older calls. Assert half-open boundaries: current `[current_start, now]`, previous `[previous_start, current_start)`.
- [ ] Write `test_metrics_handle_paris_dst_boundaries`.
  Parameterize spring-forward (`2026-03-29`) and fall-back (`2026-10-25`) local midnights. Assert the service constructs boundaries from local calendar dates with `ZoneInfo`, then converts each boundary to UTC; it must not subtract a fixed 24-hour duration from an aware instant.
- [ ] Write `test_metrics_exclude_other_owner_deleted_and_null_started_calls`.
- [ ] Write `test_metrics_count_only_valid_true_follow_up_summaries`.
  Include a valid `CallSummaryProjection` payload with `true`, valid payload with `false`, missing fields, oversized strings, an invalid action-item array, and string `"true"`. Assert only the valid boolean-true projection counts.
- [ ] Write `test_metrics_average_only_terminal_calls_with_durations`.
  Include `completed` and `failed` calls with durations, an active call with a duration, a terminal call with `duration_seconds=None`, and a deleted terminal call. Assert nearest-integer rounding; add an empty case that returns `None`.
- [ ] Run the new tests and confirm RED because the service and repository method do not exist:

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 \
  UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync \
  python -m pytest tests/dashboard/test_dashboard_metrics.py -q
```

Expected: collection/import failure naming `dashboard_metrics_service`.

- [ ] Add `DashboardMetricsAggregate` and `CallRepository.dashboard_metrics`.
  Build a single `SELECT` containing conditional `CASE` expressions inside `sum` and `avg`, always constrained by `Call.user_id == user_id` and `Call.deleted_at.is_(None)`.
- [ ] Keep all time comparisons on `Call.started_at`; a null `started_at` naturally fails every window predicate.
- [ ] Restrict average duration to `status in ("completed", "failed")`, the current window, and non-null duration. Convert a non-null SQL result with `Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)` and return `None` for an empty eligible set.
- [ ] Build a private valid-follow-up SQL expression for each supported dialect:
  - SQLite: use `json_type`, `json_extract`, `json_array_length`, and a correlated `json_each` anti-exists check.
  - PostgreSQL: use `json_typeof`, `json_array_length`, and a safely guarded `json_array_elements` anti-exists check.
  - In both, enforce the same `CallSummaryProjection` bounds: nonblank caller intent up to 200 characters, at most ten nonblank action strings up to 300 characters, nonblank sentiment up to 32 characters, and a JSON boolean `true`.
  - Raise a clear `RuntimeError` for an unsupported dialect rather than silently weakening validation.
- [ ] Add `DashboardMetricsService`.
  Resolve the profile and activation concurrently only if doing so does not share one `AsyncSession` across simultaneous queries; otherwise read them sequentially. Use the profile timezone only when confirmation exists and its revision equals the profile content revision. Normalize `now` to aware UTC.
- [ ] Construct local boundaries with:

```python
local_now = now_utc.astimezone(ZoneInfo(timezone_name))
today = local_now.date()
today_start = datetime.combine(today, time.min, tzinfo=zone).astimezone(UTC)
current_start = datetime.combine(
    today - timedelta(days=6), time.min, tzinfo=zone
).astimezone(UTC)
previous_start = datetime.combine(
    today - timedelta(days=13), time.min, tzinfo=zone
).astimezone(UTC)
```

- [ ] Re-run the focused SQLite test and confirm GREEN.
- [ ] Run the same test file against the repository's PostgreSQL test database:

```bash
cd apps/api
env CLIENT_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ai_call_test \
  REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/dashboard/test_dashboard_metrics.py -q
```

Expected: all metric and JSON-validation cases pass on PostgreSQL as well as SQLite.

- [ ] Run API static checks for the touched modules:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync \
  ruff check app/repositories/call_repository.py \
  app/services/dashboard_metrics_service.py \
  tests/dashboard/test_dashboard_metrics.py
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync \
  mypy app/repositories/call_repository.py app/services/dashboard_metrics_service.py
```

- [ ] Commit:

```bash
git add apps/api/app/repositories/call_repository.py \
  apps/api/app/services/dashboard_metrics_service.py \
  apps/api/tests/dashboard/test_dashboard_metrics.py
git commit -m "feat(api): aggregate dashboard metrics"
```

---

## Task 2: Expose the metrics endpoint and typed web client

**Files:**

- Create: `apps/api/app/schemas/dashboard.py`
- Create: `apps/api/app/routers/dashboard.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/dashboard/test_dashboard_metrics.py`
- Create: `apps/web/src/lib/types/dashboard.ts`
- Create: `apps/web/src/lib/api/dashboard.ts`
- Create: `apps/web/tests/lib/dashboard-api.test.ts`

- [ ] Add API tests for:
  - unauthenticated `GET /api/dashboard/metrics` returns `401`;
  - a valid Clerk token resolves the internal owner and returns exactly the seven response fields;
  - calls owned by a different authenticated user never affect the payload;
  - the response serializes an empty average as JSON `null`;
  - the route is read-only and remains available for inactive owners.
- [ ] Confirm RED:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync \
  python -m pytest tests/dashboard/test_dashboard_metrics.py -q
```

Expected: `404` for `/api/dashboard/metrics`.

- [ ] Define `DashboardMetricsResponse` with all fields nonnegative except the signed change, and a nullable nonnegative average.
- [ ] Add a router with prefix `/api/dashboard`, tag `dashboard`, the existing `require_user_identity` dependency, the normal session dependency, and a `60/minute` limiter:

```python
@router.get("/metrics", response_model=DashboardMetricsResponse)
@limiter.limit("60/minute")
async def get_dashboard_metrics(
    request: Request,
    identity: AuthenticatedUserIdentity = Depends(require_user_identity),
    service: DashboardMetricsService = Depends(get_dashboard_metrics_service),
) -> DashboardMetricsResponse:
    return DashboardMetricsResponse.model_validate(
        asdict(await service.get_metrics(identity.internal_user_id))
    )
```

- [ ] Register `dashboard_router` in `create_app` beside the existing authenticated routers.
- [ ] Re-run the API tests and confirm GREEN.
- [ ] Add this exact TypeScript contract:

```ts
export type DashboardMetrics = {
  timezone: string;
  calls_today: number;
  calls_last_7_days: number;
  calls_previous_7_days: number;
  calls_change_from_previous_7_days: number;
  follow_up_flagged_last_7_days: number;
  average_duration_seconds_last_7_days: number | null;
};
```

- [ ] In `dashboard-api.test.ts`, mock `backendFetch`, call `getDashboardMetrics`, and assert it requests `"/api/dashboard/metrics"` without mutation options.
- [ ] Confirm RED because `@/lib/api/dashboard` does not exist:

```bash
cd apps/web
npm run test:ci -- tests/lib/dashboard-api.test.ts
```

- [ ] Implement `getDashboardMetrics(): Promise<DashboardMetrics>` as a thin `backendFetch` call and confirm GREEN.
- [ ] Run web typecheck and commit:

```bash
cd apps/web
npm run typecheck
cd ../..
git add apps/api/app/main.py apps/api/app/routers/dashboard.py \
  apps/api/app/schemas/dashboard.py \
  apps/api/tests/dashboard/test_dashboard_metrics.py \
  apps/web/src/lib/api/dashboard.ts apps/web/src/lib/types/dashboard.ts \
  apps/web/tests/lib/dashboard-api.test.ts
git commit -m "feat(dashboard): expose metrics contract"
```

---

## Task 3: Establish the curated theme and restrained motion foundation

**Files:**

- Modify: `apps/web/src/app/globals.css`
- Modify: `apps/web/src/app/layout.tsx`
- Modify: `apps/web/src/lib/fonts/registry.ts`
- Modify: `apps/web/src/lib/preferences/preferences-config.ts`
- Modify: `apps/web/src/lib/preferences/preferences-storage.ts`
- Modify: `apps/web/src/lib/preferences/theme.ts`
- Modify: `apps/web/src/stores/preferences/preferences-provider.tsx`
- Modify: `apps/web/src/stores/preferences/preferences-store.ts`
- Modify: `apps/web/src/scripts/theme-boot.tsx`
- Modify: `apps/web/tests/lib/preferences-store.test.ts`
- Create: `apps/web/src/lib/motion/tokens.ts`
- Create: `apps/web/src/components/motion/presvo-motion-provider.tsx`
- Create: `apps/web/src/components/motion/animated-status-badge.tsx`
- Create: `apps/web/src/components/motion/changed-number.tsx`
- Create: `apps/web/src/components/motion/action-state.tsx`
- Create: `apps/web/tests/components/motion-primitives.test.tsx`
- Delete: `apps/web/src/lib/preferences/layout.ts`
- Delete: `apps/web/src/lib/preferences/layout-utils.ts`
- Delete: `apps/web/src/styles/presets/brutalist.css`
- Delete: `apps/web/src/styles/presets/soft-pop.css`
- Delete: `apps/web/src/styles/presets/tangerine.css`

- [ ] Rewrite the preference tests first. Assert:
  - only `light`, `dark`, and `system` are accepted;
  - the store exposes only `themeMode`, `resolvedThemeMode`, their setters, and sync state;
  - legacy DOM attributes/cookies such as `data-theme-preset`, `data-font`, and `sidebar_variant` are ignored;
  - changing theme mode still toggles `.dark`, updates `color-scheme`, follows the system media query in system mode, and persists `theme_mode`.
- [ ] Add motion tests asserting:
  - `PresvoMotionProvider` renders a `MotionConfig` with `reducedMotion="user"`;
  - `AnimatedStatusBadge` renders icon plus text and has no pulse/loop animation;
  - `ChangedNumber` shows the initial value immediately and calls no numeric animation on mount;
  - a later value prop change animates from the previous value, while mocked reduced motion swaps immediately;
  - `ActionState` exposes pending and error copy through an `aria-live="polite"` region.
- [ ] Confirm RED:

```bash
cd apps/web
npm run test:ci -- tests/lib/preferences-store.test.ts \
  tests/components/motion-primitives.test.tsx
```

- [ ] Reduce the font registry to `Inter`, `Figtree`, and `Geist_Mono`.
  Keep Inter as the public/global body font so the landing page is not redesigned; export a Figtree variable that `WorkspaceShell` will apply only to authenticated product content.
- [ ] Replace preset imports and generic default colors in `globals.css` with the approved semantic Presvo tokens in coordinated light/dark blocks:
  `surface`, `surface-elevated`, `surface-subtle`, `sidebar`, `sidebar-foreground`, `sidebar-active`, `brand-detail`, `success`, `warning`, destructive subtle surfaces, three text tiers, two restrained shadows, shell/surface/control radii, and motion durations/easings.
- [ ] Map legacy shadcn variables such as `card`, `popover`, `muted`, `accent`, and `sidebar-*` onto those semantic tokens so activation and public primitives remain compatible.
- [ ] Remove every preset import, `[data-theme-preset]` shadow override, and `[data-font]` selector. Keep public body typography unchanged.
- [ ] Simplify `PreferenceValueMap`, defaults, persistence, store, provider, and boot script to `theme_mode` only. The boot script must validate the cookie, resolve system mode before paint, toggle `.dark`, set `data-theme-mode`, and set `colorScheme`.
- [ ] Remove unused layout preference files only after `rg` confirms no import remains. Leave old browser cookies/storage untouched and ignored.
- [ ] Adapt BeUI's `lib/ease.ts` into `lib/motion/tokens.ts`, retaining a source comment and only the approved non-bouncy tokens:

```ts
export const EASE_OUT = [0.16, 1, 0.3, 1] as const;
export const EASE_DRAWER = [0.32, 0.72, 0, 1] as const;
export const SPRING_LAYOUT = {
  type: "spring",
  stiffness: 360,
  damping: 32,
  mass: 0.6,
} as const;
```

- [ ] Implement `PresvoMotionProvider` as the single authenticated `MotionConfig reducedMotion="user"` boundary.
- [ ] Adapt BeUI `animated-badge` into the smaller `AnimatedStatusBadge` API:

```ts
type StatusTone =
  | "neutral"
  | "live"
  | "ready"
  | "processing"
  | "paused"
  | "warning"
  | "attention"
  | "inactive";

type AnimatedStatusBadgeProps = {
  tone: StatusTone;
  label: string;
  icon?: ReactNode;
  className?: string;
};
```

  Remove continuous pulse/spin behavior. Use `AnimatePresence initial={false}` only when `tone` or `label` changes, and use opacity-only under reduced motion.
- [ ] Adapt BeUI `number` into `ChangedNumber`. Initialize its display and previous ref from `value`, track whether mounting has completed, and animate only later prop changes. Render a plain tabular number for reduced motion.
- [ ] Adapt BeUI `action-swap` into `ActionState`, preserving semantic button content and `aria-live`, but do not duplicate button styling or mutation policy:

```ts
type ActionPhase = "idle" | "pending" | "success" | "error";

type ActionStateProps = {
  phase: ActionPhase;
  idle: ReactNode;
  pending: ReactNode;
  success: ReactNode;
  error: ReactNode;
};
```

- [ ] Re-run focused tests, then:

```bash
cd apps/web
npm run lint
npm run typecheck
npm run build
```

- [ ] Confirm the build has no references to deleted preset CSS or removed font/layout attributes. Commit:

```bash
git add apps/web/src/app/globals.css apps/web/src/app/layout.tsx \
  apps/web/src/lib/fonts/registry.ts apps/web/src/lib/preferences \
  apps/web/src/lib/motion/tokens.ts apps/web/src/stores/preferences \
  apps/web/src/scripts/theme-boot.tsx apps/web/src/components/motion \
  apps/web/src/styles/presets apps/web/tests/lib/preferences-store.test.ts \
  apps/web/tests/components/motion-primitives.test.tsx
git commit -m "feat(web): establish Presvo product theme"
```

---

## Task 4: Build the reusable product component layer

**Files:**

- Create: `apps/web/src/components/product/page-intro.tsx`
- Create: `apps/web/src/components/product/product-surface.tsx`
- Create: `apps/web/src/components/product/status-surface.tsx`
- Create: `apps/web/src/components/product/metric-band.tsx`
- Create: `apps/web/src/components/product/data-ledger.tsx`
- Create: `apps/web/src/components/product/settings-section.tsx`
- Create: `apps/web/tests/components/product-components.test.tsx`

- [ ] Write contract tests before components:
  - `PageIntro` produces the page's only `h1`, optional eyebrow/description/action, and no decorative heading icon.
  - `ProductSurface` renders optional header, action, body, and footer slots without requiring nested cards.
  - `StatusSurface` always exposes icon/text in addition to semantic color, supports the approved tones, and renders an optional corrective link/button.
  - `MetricBand` is one labelled region containing metric items, comparison context, nullable unavailable copy, and tabular values.
  - `DataLedger` renders a semantic list by default, a table only when `mode="table"`, mobile labels through real text/`aria-label`, empty/error/pagination slots, and keyboard-accessible row links.
  - `SettingsSection` associates heading, description, controls, validation, and action/status regions.
- [ ] Confirm RED because the product modules do not exist.
- [ ] Implement these stable interfaces:

```ts
type PageIntroProps = {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  dynamicContext?: boolean;
};

type ProductSurfaceProps = {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  as?: "section" | "article" | "div";
  tone?: "default" | "subtle" | "danger";
};

type MetricItemProps = {
  label: string;
  value: ReactNode;
  context?: ReactNode;
  state?: "default" | "positive" | "warning" | "negative";
};

type DataLedgerProps = {
  label: string;
  mode?: "list" | "table";
  header?: ReactNode;
  children?: ReactNode;
  empty?: ReactNode;
  error?: ReactNode;
  pagination?: ReactNode;
};
```

- [ ] Make `DataLedger.Row`, `DataLedger.Cell`, and `DataLedger.Action` compound children so Calls and Billing share row rhythm without page-specific prop growth. `Cell` accepts `label`, `primary`, and optional `hideAt`.
- [ ] Keep all components server-compatible except the imported small motion islands. Do not add `"use client"` to the product layer.
- [ ] Use warm surfaces, controlled borders/shadows, a 4/6/8 spacing rhythm, and responsive two-column metric layout below `md`. Avoid nesting `ProductSurface` inside itself.
- [ ] Re-run focused tests and the web typecheck.
- [ ] Commit:

```bash
git add apps/web/src/components/product \
  apps/web/tests/components/product-components.test.tsx
git commit -m "feat(web): add reusable product surfaces"
```

---

## Task 5: Replace the dashboard frame with the responsive WorkspaceShell

**Files:**

- Create: `apps/web/src/lib/api/request-data.ts`
- Create: `apps/web/src/navigation/dashboard-items.ts`
- Create: `apps/web/src/components/workspace/workspace-shell.tsx`
- Create: `apps/web/src/components/workspace/workspace-header.tsx`
- Create: `apps/web/src/components/workspace/command-rail.tsx`
- Create: `apps/web/src/components/workspace/workspace-navigation.tsx`
- Create: `apps/web/src/components/workspace/mobile-command-bar.tsx`
- Create: `apps/web/src/components/workspace/mobile-more-sheet.tsx`
- Create: `apps/web/src/components/motion/bottom-sheet.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/layout.tsx`
- Delete: `apps/web/src/navigation/sidebar/sidebar-items.ts`
- Modify: `apps/web/src/app/(app)/dashboard/_components/sidebar/theme-switcher.tsx`
- Modify: `apps/web/tests/app/app-shell.test.tsx`
- Delete: `apps/web/src/app/(app)/dashboard/_components/sidebar/app-sidebar.tsx`
- Delete: `apps/web/src/app/(app)/dashboard/_components/sidebar/nav-main.tsx`
- Delete: `apps/web/src/app/(app)/dashboard/_components/sidebar/layout-controls.tsx`

- [ ] Rewrite shell tests first, mocking both `getAccount` and the cached agent read. Assert:
  - desktop destinations are Overview, Calls, configured agent name, Billing, and Account;
  - a blank configured name renders `Receptionist`;
  - a long name is visually truncated but remains complete in the link's accessible name and tooltip;
  - the command rail uses labelled desktop mode at `lg` and icon-only compact mode from `md` to `lg`;
  - mobile renders exactly Overview, Calls, configured agent, and More;
  - Billing and Account exist in the More sheet, not as fifth/sixth bottom-bar items;
  - opening More announces its title, traps focus, closes with Escape, and restores focus to More;
  - the active route has `aria-current="page"`;
  - inactive/deactivating lifecycle banners and all retained read-only destinations still render.
- [ ] Confirm RED against the current shadcn sidebar.
- [ ] Add `getAgentConfigForRequest = cache(getAgentConfig)` in the server-only `request-data.ts`. Use it from the layout, dashboard, and agent page wherever the same request could otherwise read the config twice.
- [ ] Replace the static item list with:

```ts
export function dashboardItems(agentName: string): NavItem[] {
  return [
    { title: "Overview", href: "/dashboard", icon: House },
    { title: "Calls", href: "/dashboard/calls", icon: Phone },
    { title: normalizeAgentName(agentName), href: "/dashboard/agent", icon: Bot },
    { title: "Billing", href: "/dashboard/billing", icon: CreditCard },
    { title: "Account", href: "/dashboard/account", icon: UserRound },
  ];
}
```

  Route matching must treat `/dashboard` as exact and nested routes as prefix matches.
- [ ] Adapt BeUI `shared-layout-bg` into the active navigation marker. Namespace each `layoutId` with `useId` so desktop and mobile navigation can coexist without cross-projecting. Use a static background under reduced motion.
- [ ] Adapt BeUI `bottom-sheet` motion into `components/motion/bottom-sheet.tsx`, but keep the existing `Drawer`/dialog primitive as the semantic owner of portal, focus trap, Escape, outside click, and focus restoration. Retain the BeUI source comment and `EASE_DRAWER`, remove draggable snap points/inertia, and use one non-bouncy transform/opacity entrance.
- [ ] Build `WorkspaceShell`:
  - `<aside>` deep-ink command rail at `md+`;
  - 256-pixel labelled rail at `lg+`, 72-pixel icon rail at `md` through `lg-1`;
  - mobile top header and fixed bottom command bar below `md`;
  - content max width and readable gutters;
  - `padding-bottom: calc(command-bar-height + env(safe-area-inset-bottom))` on mobile;
  - account lifecycle banner above page content;
  - Figtree applied to the authenticated wrapper only;
  - `PresvoMotionProvider` around motion islands;
  - theme switcher and existing account/session control in the compact header.
- [ ] Preserve tooltips for compact icons and complete screen-reader labels. Use at least 44-pixel hit targets.
- [ ] Simplify the dashboard layout to fetch `account` and cached agent config, normalize the name, and pass both to `WorkspaceShell`; remove cookies and visual-preference server reads.
- [ ] Delete old sidebar components only after `rg` proves they are unused.
- [ ] Re-run shell tests, then all existing lifecycle page tests:

```bash
cd apps/web
npm run test:ci -- tests/app/app-shell.test.tsx \
  tests/app/account-page.test.tsx tests/app/calls-page.test.tsx \
  tests/app/billing-page.test.tsx tests/app/agent-page.test.tsx
npm run lint
npm run typecheck
```

- [ ] Commit:

```bash
git add apps/web/src/app/'(app)'/dashboard/layout.tsx \
  apps/web/src/app/'(app)'/dashboard/_components/sidebar \
  apps/web/src/components/workspace apps/web/src/components/motion/bottom-sheet.tsx \
  apps/web/src/lib/api/request-data.ts apps/web/src/navigation \
  apps/web/tests/app/app-shell.test.tsx
git commit -m "feat(web): add responsive workspace shell"
```

---

## Task 6: Recompose the dashboard as an Operational Ledger

**Files:**

- Modify: `apps/web/src/app/(app)/dashboard/page.tsx`
- Modify: `apps/web/tests/app/home-page.test.tsx`
- Create: `apps/web/src/components/dashboard/dashboard-metrics.tsx`
- Create: `apps/web/src/components/dashboard/dashboard-call-ledger.tsx`
- Create: `apps/web/src/components/dashboard/attention-surface.tsx`
- Create: `apps/web/src/components/dashboard/plan-usage-surface.tsx`
- Rewrite: `apps/web/src/components/dashboard/answering-status-banner.tsx`
- Rewrite or delete after replacement: `apps/web/src/components/dashboard/status-summary-cards.tsx`
- Rewrite or delete after replacement: `apps/web/src/components/dashboard/recent-calls-list.tsx`
- Rewrite or delete after replacement: `apps/web/src/components/dashboard/usage-summary-card.tsx`
- Preserve/recompose: `apps/web/src/components/dashboard/onboarding-status-card.tsx`
- Preserve/recompose: `apps/web/src/components/dashboard/setup-checklist.tsx`
- Preserve/recompose: `apps/web/src/components/dashboard/agent-snapshot-card.tsx`

- [ ] Extend dashboard tests with `getDashboardMetricsMock` and exact assertions for:
  - `PageIntro` heading “Operations overview” and a timezone-aware date context;
  - configured agent name in the live status copy;
  - five visible metric items: Calls today, Last 7 days, Follow-up flagged, Avg duration, Minutes remaining;
  - the seven-day comparison expressed as signed integer context, never a fabricated percentage;
  - recent calls retain caller, intent, follow-up flag, duration, and start time;
  - Needs attention includes only recent calls whose `follow_up_required === true` and never says completed/resolved;
  - metrics rejection renders “Metrics temporarily unavailable” while status, calls, setup state, and plan usage still render;
  - empty metrics render zero and unavailable average correctly;
  - first-run setup content and activation handoff copy remain intact.
- [ ] Confirm RED against the current cards.
- [ ] Keep the activation gate as the first critical read. After it passes, fetch agent, onboarding, five calls, usage, and metrics in parallel.
- [ ] Make only metrics noncritical:

```ts
const metricsPromise = getDashboardMetrics()
  .then((value) => ({ status: "ready" as const, value }))
  .catch(() => ({ status: "unavailable" as const }));
```

  Do not swallow failures from activation, onboarding, calls, agent config, or billing.
- [ ] Compose:
  1. `PageIntro`;
  2. `StatusSurface`-based answering state;
  3. one five-item `MetricBand`;
  4. `DashboardCallLedger`;
  5. `AttentionSurface`;
  6. `PlanUsageSurface`.
- [ ] Put `calls_change_from_previous_7_days` in the Last 7 days item's context; keep `calls_previous_7_days` available to screen-reader explanatory text.
- [ ] Use `ChangedNumber` only as a changed-value island. The SSR value must render immediately and must not count up.
- [ ] Reuse the current setup checklist/onboarding status before live activation; do not hide setup blockers in pursuit of the new hierarchy.
- [ ] Remove old dashboard-only card components only when their behavior and tests have moved.
- [ ] Re-run:

```bash
cd apps/web
npm run test:ci -- tests/app/home-page.test.tsx \
  tests/app/dashboard-onboarding.test.tsx \
  tests/app/onboarding-status-card.test.tsx
npm run lint
npm run typecheck
```

- [ ] Commit:

```bash
git add apps/web/src/app/'(app)'/dashboard/page.tsx \
  apps/web/src/components/dashboard apps/web/tests/app/home-page.test.tsx
git commit -m "feat(web): recompose operational dashboard"
```

---

## Task 7: Recompose Calls list and detail with DataLedger

**Files:**

- Modify: `apps/web/src/app/(app)/dashboard/calls/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/calls/[callId]/page.tsx`
- Rewrite: `apps/web/src/components/calls/calls-table.tsx`
- Rewrite: `apps/web/src/components/calls/call-detail-card.tsx`
- Modify: `apps/web/src/components/calls/call-history-controls.tsx`
- Modify: `apps/web/src/components/calls/call-outcome.tsx`
- Modify only as styling requires: `apps/web/src/components/calls/recording-panel.tsx`
- Modify only as styling requires: `apps/web/src/components/calls/transcript-panel.tsx`
- Preserve behavior: `apps/web/src/components/calls/delete-call-dialog.tsx`
- Modify: `apps/web/tests/app/calls-page.test.tsx`

- [ ] Extend existing tests before implementation. Preserve every search, query normalization, pagination, out-of-range redirect, 404, recording, transcript, active-call, inactive-account, and removal assertion, then add:
  - calls page has one `h1` and URL-driven native GET search;
  - list rows expose mobile labels for Caller, Intent, Follow-up, Duration, and Started;
  - empty history and no-match states remain distinct, with the query preserved and a clear reset link;
  - no filters, tags, notes, export, or client-side fetching controls appear;
  - detail uses ProductSurface sections for summary, recording, transcript, and metadata;
  - terminal removal confirmation and read-only inactive behavior are unchanged.
- [ ] Confirm RED on the new component/semantic assertions.
- [ ] Add `PageIntro` to list and detail without changing route or fetch behavior.
- [ ] Rebuild `CallsTable` on `DataLedger`. Use a real row link for the primary navigation target, explicit labelled cells on mobile, and progressive column hiding at compact widths.
- [ ] Keep `CallHistoryControls` as a server-navigation GET form. Restyle search and pagination, but do not convert to controlled client state.
- [ ] Rebuild call detail from existing data only. Use status text plus icon; never infer a business outcome beyond stored status/summary fields.
- [ ] Keep `DeleteCallDialog` authorization, server action, exact active-call conflict, destructive semantics, and focus behavior untouched.
- [ ] Re-run:

```bash
cd apps/web
npm run test:ci -- tests/app/calls-page.test.tsx \
  tests/app/call-handoff.test.tsx
npm run lint
npm run typecheck
```

- [ ] Commit:

```bash
git add apps/web/src/app/'(app)'/dashboard/calls \
  apps/web/src/components/calls apps/web/tests/app/calls-page.test.tsx
git commit -m "feat(web): recompose call history workspace"
```

---

## Task 8: Recompose the configured-agent page with SettingsSection

**Files:**

- Modify: `apps/web/src/app/(app)/dashboard/agent/page.tsx`
- Rewrite: `apps/web/src/components/agent/agent-settings-form.tsx`
- Rewrite: `apps/web/src/components/agent/agent-runtime-card.tsx`
- Modify: `apps/web/tests/app/agent-page.test.tsx`

- [ ] Add tests before implementation for:
  - configured name appears in the page `h1`; blank name falls back to “Receptionist”;
  - the full long name renders in the heading;
  - runtime/readiness state leads the page;
  - existing fields are grouped into Identity, Call handling, Business context, and Instructions;
  - current validation and server-action payloads are unchanged;
  - the save button presents idle/pending/success/error through `ActionState`;
  - deactivating and inactive accounts retain the current read-only restrictions;
  - unsupported pipeline choices remain absent.
- [ ] Confirm RED for new headings/groups while the original action tests continue to pass.
- [ ] Use `getAgentConfigForRequest` and the existing account read in the server page.
- [ ] Add `PageIntro` with the normalized agent name, then a `StatusSurface` runtime summary.
- [ ] Recompose, rather than reimplement, the form state and action:
  - retain the same input names, labels, bounds, payload shape, transitions, error copy, and disabled policy;
  - wrap related fields in `SettingsSection`;
  - use `ActionState` only for presentation inside the existing submit button.
- [ ] Keep `pipeline_mode` read-only/hidden according to the current supported behavior; do not expose a runtime architecture selector.
- [ ] Re-run:

```bash
cd apps/web
npm run test:ci -- tests/app/agent-page.test.tsx
npm run lint
npm run typecheck
```

- [ ] Commit:

```bash
git add apps/web/src/app/'(app)'/dashboard/agent/page.tsx \
  apps/web/src/components/agent apps/web/tests/app/agent-page.test.tsx
git commit -m "feat(web): recompose configured agent settings"
```

---

## Task 9: Recompose Billing without changing billing policy

**Files:**

- Modify: `apps/web/src/app/(app)/dashboard/billing/page.tsx`
- Rewrite: `apps/web/src/components/billing/billing-summary-cards.tsx`
- Rewrite: `apps/web/src/components/billing/billing-actions-card.tsx`
- Rewrite: `apps/web/src/components/billing/usage-ledger-list.tsx`
- Modify: `apps/web/tests/app/billing-page.test.tsx`

- [ ] Extend billing tests first:
  - one `h1` and subscription state first;
  - a compact metric band shows Minutes remaining, Minutes used (`max(allocated - remaining, 0)`), and Plan;
  - period-end cancellation still says it ends at the paid-period end and still calls the account active;
  - active/nonterminal statuses use Manage billing according to backend `can_start_checkout`;
  - terminal eligible states use Start starter plan according to the same backend field;
  - inactive owners retain read-only history and permitted portal behavior;
  - action pending/error presentation uses `ActionState` without changing redirect/toast behavior;
  - usage rows expose labelled mobile values through `DataLedger`.
- [ ] Confirm RED for new composition while current policy tests remain GREEN.
- [ ] Add `PageIntro`, lead with subscription `StatusSurface`, and use one three-item `MetricBand`.
- [ ] Compute used minutes for presentation only; do not make it a new billing authority or persist it.
- [ ] Use separate `ProductSurface` regions for usage, actions, and the ledger. Do not visually merge subscription cancellation with account deactivation.
- [ ] Rebuild `UsageLedgerList` with `DataLedger` and keep empty state/event formatting unchanged.
- [ ] Preserve `createCheckoutSessionAction`, `createPortalSessionAction`, backend `can_start_checkout`, toast copy, and `window.location.assign`.
- [ ] Re-run:

```bash
cd apps/web
npm run test:ci -- tests/app/billing-page.test.tsx
npm run lint
npm run typecheck
```

- [ ] Commit:

```bash
git add apps/web/src/app/'(app)'/dashboard/billing/page.tsx \
  apps/web/src/components/billing apps/web/tests/app/billing-page.test.tsx
git commit -m "feat(web): recompose billing workspace"
```

---

## Task 10: Recompose Account while preserving immediate deactivation

**Files:**

- Modify: `apps/web/src/app/(app)/dashboard/account/page.tsx`
- Rewrite: `apps/web/src/components/account/account-status-card.tsx`
- Modify: `apps/web/src/components/account/account-lifecycle-banner.tsx`
- Modify only for visual integration: `apps/web/src/components/account/deactivate-account-dialog.tsx`
- Modify only for visual integration: `apps/web/src/components/account/reactivate-account-button.tsx`
- Modify: `apps/web/tests/app/account-page.test.tsx`
- Preserve: `apps/web/tests/app/account-actions.test.ts`

- [ ] Extend account tests before implementation:
  - one `h1`, account state first, calm state/settings rows, and a separately labelled danger zone;
  - active, deactivating, attention-required, and inactive states retain exact meaning;
  - every one of the six approved deactivation consequences remains verbatim;
  - exact, case-sensitive `DEACTIVATE` remains required;
  - the bounded internal-scroll dialog, focus trap, Escape, cancel, and focus restoration remain intact;
  - inactive copy says data is retained and requires a new subscription/new number;
  - no copy describes deactivation as deletion or presents the released old number as assigned;
  - reactivation eligibility and server-action behavior remain unchanged.
- [ ] Confirm RED for new composition while `account-actions.test.ts` remains GREEN.
- [ ] Add `PageIntro`, rebuild `AccountStatusCard` with `StatusSurface`, and group non-destructive account state/security/session entry points in `SettingsSection`/`ProductSurface`.
- [ ] Keep the danger zone `tone="danger"` separate from ordinary settings and preserve the exact dialog behavior. Styling changes must not change the `CONSEQUENCES` array or confirmation comparison.
- [ ] Keep the global lifecycle banner concise and truthful; do not expose provider identifiers or internal cleanup states beyond the existing mapped copy.
- [ ] Re-run:

```bash
cd apps/web
npm run test:ci -- tests/app/account-page.test.tsx \
  tests/app/account-actions.test.ts
npm run lint
npm run typecheck
```

- [ ] Commit:

```bash
git add apps/web/src/app/'(app)'/dashboard/account/page.tsx \
  apps/web/src/components/account apps/web/tests/app/account-page.test.tsx
git commit -m "feat(web): recompose account lifecycle workspace"
```

---

## Task 11: Add deterministic visual coverage and complete regression gates

**Files:**

- Create: `apps/web/tests/e2e/dashboard-visual.spec.ts`
- Modify: `apps/web/playwright.config.ts`
- Modify: `scripts/run-local-e2e.sh`
- Generate: `apps/web/tests/e2e/dashboard-visual.spec.ts-snapshots/*.png`

- [ ] Add a Playwright test that runs after `activation.spec.ts` and before deactivation in the disposable local stack.
- [ ] In the visual test:
  - visit `/dashboard`;
  - assert “Operations overview”, configured name, status, metric band, and command navigation are visible before capturing;
  - capture desktop light at `1440×1100`;
  - capture desktop dark at `1440×1100`;
  - capture mobile light at `390×844`;
  - capture mobile dark at `390×844`;
  - use `animations: "disabled"` and `caret: "hide"`;
  - mask only date/time nodes marked `data-visual-dynamic`, never entire metrics or content surfaces;
  - open the More sheet once on mobile and assert Billing/Account focus and Escape behavior outside the screenshot baseline.
- [ ] Add a reduced-motion Playwright assertion using `page.emulateMedia({ reducedMotion: "reduce" })`; navigate among Overview, Calls, and the configured agent and assert no element reports a nonzero transform transition/animation duration.
- [ ] Update `scripts/run-local-e2e.sh` to run the visual spec after activation and before deactivation, reusing the existing disposable stack and local owner. Add an `E2E_UPDATE_SNAPSHOTS=1` branch that passes `--update-snapshots` only to the visual spec; the default runner must never update baselines.
- [ ] Confirm RED because the screenshots do not exist:

```bash
./scripts/run-local-e2e.sh
```

Expected: activation succeeds, then `dashboard-visual.spec.ts` fails with missing screenshot baselines; the disposable stack still cleans itself up.

- [ ] Generate baselines once from the repository's pinned Chromium:

```bash
E2E_UPDATE_SNAPSHOTS=1 ./scripts/run-local-e2e.sh
```

- [ ] Review every generated PNG manually at full size. Reject clipping, covered controls, illegible muted text, broken safe-area spacing, unexpected scrollbars, font fallback, theme flash, or over-dense mobile rows.
- [ ] Run the visual spec again without update mode and confirm GREEN.
- [ ] Run the complete API SQLite suite:

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 \
  UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync \
  python -m pytest -q -ra
```

- [ ] Run API lint and types:

```bash
cd apps/api
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy app
```

- [ ] Run the complete PostgreSQL/Redis API matrix using the repository's test services:

```bash
cd apps/api
env CLIENT_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ai_call_test \
  DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_call_test \
  REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest -q -ra
```

- [ ] Run the complete web suite:

```bash
cd apps/web
npm run test:ci
npm run lint
npm run typecheck
npm run build
```

- [ ] Run the disposable local lifecycle plus visual journey:

```bash
./scripts/run-local-e2e.sh
```

- [ ] Inspect at browser zoom 200%, keyboard-only navigation, `390×844`, `768×1024`, `1024×768`, and `1440×1100`, in light/dark and reduced-motion modes.
- [ ] Scan for forbidden or stale implementation:

```bash
rg -n "theme_preset|data-theme-preset|data-font|content_layout|navbar_style|sidebar_variant|sidebar_collapsible" apps/web/src
rg -n "TODO|FIXME|placeholder|lorem|coming soon" \
  apps/api/app/routers/dashboard.py \
  apps/api/app/services/dashboard_metrics_service.py \
  apps/web/src/components/product \
  apps/web/src/components/workspace \
  apps/web/src/components/dashboard
git diff --check
git status --short
```

Expected: both `rg` commands return no production matches, `git diff --check` is silent, and status contains only intentional visual/verification files.

- [ ] If any gate exposes a production defect, return to the task that owns that component, add a focused regression assertion to its named test file, make the smallest correction in that task's named production files, re-run that task's gates, and commit the correction before resuming this final gate.
- [ ] Commit the verified visual assets and runner changes:

```bash
git add apps/web/tests/e2e/dashboard-visual.spec.ts \
  apps/web/tests/e2e/dashboard-visual.spec.ts-snapshots \
  apps/web/playwright.config.ts scripts/run-local-e2e.sh
git commit -m "test(web): verify premium dashboard experience"
```

- [ ] Review the final range and ensure no deployment/provider operation was introduced:

```bash
git log --oneline fe40acf..HEAD
git diff --stat fe40acf..HEAD
git diff --check fe40acf..HEAD
```

Expected commit sequence:

1. `docs: plan premium dashboard redesign`
2. `feat(api): aggregate dashboard metrics`
3. `feat(dashboard): expose metrics contract`
4. `feat(web): establish Presvo product theme`
5. `feat(web): add reusable product surfaces`
6. `feat(web): add responsive workspace shell`
7. `feat(web): recompose operational dashboard`
8. `feat(web): recompose call history workspace`
9. `feat(web): recompose configured agent settings`
10. `feat(web): recompose billing workspace`
11. `feat(web): recompose account lifecycle workspace`
12. `test(web): verify premium dashboard experience`

## Final Acceptance Checklist

- [ ] All five authenticated destinations share the reusable product layer.
- [ ] Desktop, compact tablet, and mobile navigation match the approved route hierarchy.
- [ ] Configured agent name/fallback works everywhere without accessible truncation.
- [ ] Dashboard metrics match local-calendar, owner, deletion, validity, and terminal-duration definitions on SQLite and PostgreSQL.
- [ ] Metrics failure is isolated; critical dashboard data still uses established error behavior.
- [ ] Theme presets/font/layout customization are gone; light/dark/system remain.
- [ ] Figtree is limited to the authenticated product so public/activation scope is preserved.
- [ ] BeUI-derived components retain source attribution and are adapted to Presvo semantics.
- [ ] Motion is restrained, initial metrics do not count up, and reduced-motion behavior is complete.
- [ ] Search, pagination, call detail, recordings, transcripts, and removal behavior are preserved.
- [ ] Billing continues to distinguish paid-period cancellation from immediate account deactivation.
- [ ] Account deactivation copy, exact confirmation, immediate semantics, number release, data retention, and reactivation rules are preserved.
- [ ] Unit, integration, cross-database, accessibility, screenshot, lint, type, build, and local lifecycle gates pass.
- [ ] No push or deployment occurs until the user requests it.
