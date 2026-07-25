# Premium Dashboard Redesign Design

**Date:** 2026-07-25

**Status:** Approved in collaborative design review; awaiting written-spec review

**Scope:** Authenticated dashboard and its existing pages

## Summary

Presvo's authenticated product is functionally mature, but its current visual
language is split between a bespoke landing page and a largely standard
component-library dashboard. This redesign gives the logged-in product one
recognizable, premium visual system without changing its established account,
billing, onboarding, call, recording, or lifecycle behavior.

The approved direction is **Quiet Confidence**: warm paper-like neutrals, deep
ink navigation, restrained cobalt interaction accents, an amber brand detail,
precise typography, and purposeful motion. The interface should feel calm,
capable, and operational rather than decorative or futuristic.

The dashboard uses a persistent **Command Rail** on larger screens and a
four-item **bottom command bar** on phones. The main dashboard follows an
**Operational Ledger** structure: receptionist status first, a unified band of
useful statistics, recent calls, follow-up flags, and plan usage.

The redesign establishes reusable product-level components above the existing
low-level UI primitives. Existing components may be rewritten or replaced where
their current abstractions prevent a coherent system.

## Product Context

### Audience

Presvo serves professional individuals and small businesses in France. Owners
use the product between other responsibilities; they need to understand whether
their AI receptionist is working, what happened on recent calls, what requires
attention, and whether their plan is healthy without studying an admin console.

### Core jobs

- Confirm that the configured receptionist is live and accepting forwarded
  calls.
- Review recent conversations and identify follow-up work.
- Inspect recordings, transcripts, summaries, and call outcomes.
- Configure the receptionist safely.
- Understand subscription and minute usage.
- Manage account state, including immediate deactivation and later
  reactivation.

### Brand personality

The logged-in interface must feel:

- premium without luxury decoration;
- calm without looking empty;
- operational without looking like generic admin software;
- human without becoming playful;
- distinctive without relying on gradients, glow, glass, or excessive motion.

## Goals

1. Give the authenticated product a consistent and recognizable Presvo visual
   identity.
2. Make receptionist state and required action understandable within a few
   seconds.
3. Surface more useful operational statistics without turning the product into
   an analytics suite.
4. Replace page-specific visual composition with reusable product-level
   components.
5. Preserve server rendering and keep client-side motion in small, explicit
   islands.
6. Adapt the interface for phones rather than merely shrinking the desktop
   layout.
7. Preserve all current functional, security, accessibility, billing, and
   lifecycle contracts.

## Non-goals

- Redesigning the public landing page, authentication pages, or activation
  journey.
- Adding call tags, private notes, saved searches, export, transcript search,
  status/date filters, charts, or live updates.
- Adding follow-up completion tracking. The current structured summary only
  records whether a call was flagged for follow-up.
- Changing onboarding, billing, account-deactivation, number-provisioning,
  recording, or call-removal behavior.
- Adding a database migration or a new persisted analytics model.
- Installing a complete third-party theme or replacing the project with a
  wholesale BeUI template.
- Adding parallax, canvas backgrounds, magnetic interactions, tilt effects,
  bounce motion, or decorative number animation.
- Deploying the redesign or operating real providers as part of this work.

## Approved Design Decisions

### Scope

The first redesign covers:

- dashboard overview;
- call list and call detail;
- the agent configuration page;
- billing;
- account.

Activation and public surfaces remain unchanged except where a shared low-level
primitive must stay compatible.

### Visual direction

Use the approved **Quiet Confidence** direction:

- warm off-white application background;
- deep ink Command Rail;
- tinted white product surfaces;
- restrained cobalt for interactive emphasis;
- muted amber for the Presvo brand mark and limited attention accents;
- semantic green, amber, and red for success, warning, and destructive states;
- subtle borders and controlled shadows;
- moderate radii rather than fully rounded treatment everywhere.

Premium character comes from alignment, spacing rhythm, hierarchy, typography,
and material contrast. It must not come from card nesting, glassmorphism, large
blur fields, gradient text, or generic glow effects.

### Theme policy

Keep one curated Presvo theme with coordinated light and dark palettes.
Light, dark, and system modes remain valid. Remove user-selectable theme
presets, font selection, content-layout selection, navbar style, sidebar
variant, and sidebar-collapse preferences.

Legacy preference keys may remain in browser storage but are ignored. No
storage migration is required.

### Typography

Use Figtree as the authenticated product's fixed interface typeface. The
product should rely on a deliberate type scale, optical weight, and tighter
display tracking rather than expose font switching. Monospace remains reserved
for genuinely technical values and must not become a visual motif.

### Information priority

The dashboard priority is:

1. receptionist state and any required corrective action;
2. balanced operational statistics;
3. recent conversations and follow-up flags;
4. minute usage and plan context.

### Agent naming

Customer-facing navigation and page headings use the user's configured
`agent_name`. Before configuration exists, use **Receptionist** as the fallback.

Long names:

- remain complete in accessible names and tooltips;
- truncate visually in constrained navigation;
- render completely in the agent page heading where space permits.

## Responsive Product Frame

### Desktop: 1024px and wider

- Render a persistent, labelled Command Rail.
- Use a deep ink rail and warm neutral content canvas.
- Show the active destination with a restrained shared-layout marker.
- Keep account controls and light/dark mode in the compact top header.
- Display the configured agent and current runtime state near the bottom of the
  rail.

### Compact tablet: 768px to 1023px

- Keep the Command Rail but collapse it automatically to icons.
- Show accessible tooltips and complete screen-reader labels.
- Do not expose a user preference for expanded/collapsed state.
- Preserve the same route and information hierarchy as desktop.

### Mobile: below 768px

- Remove the sidebar.
- Render a fixed four-item command bar:
  1. Overview
  2. Calls
  3. configured agent name
  4. More
- Open Billing and Account from the More bottom sheet.
- Respect `env(safe-area-inset-bottom)` and reserve content space so the command
  bar never covers controls or call rows.
- Convert the metric band into a two-column grid.
- Convert dense data ledgers into readable stacked rows with explicit labels.
- Keep primary actions visible and move secondary actions into contextual
  menus.
- Maintain at least 44-by-44-pixel touch targets.

## Visual Tokens

Define semantic Tailwind v4 tokens in CSS rather than hardcoding page colors.
Exact OKLCH values will be tuned during implementation and checked for WCAG
contrast.

Required token families:

- `background`, `foreground`;
- `surface`, `surface-elevated`, `surface-subtle`;
- `sidebar`, `sidebar-foreground`, `sidebar-active`;
- `primary`, `primary-foreground`;
- `brand-detail`;
- `success`, `warning`, `destructive` and their subtle surfaces;
- `border`, `input`, `ring`;
- text tiers for primary, secondary, and tertiary content;
- shadow levels for raised interactive surfaces only;
- radii for control, surface, and shell;
- motion duration and easing tokens.

The existing default theme variables become the Presvo theme. The
`brutalist`, `soft-pop`, and `tangerine` presets and their imports are removed.

## Component Architecture

### Preserve low-level primitives

Retain compatible shadcn/base primitives for semantics and accessibility,
including buttons, inputs, labels, dialogs, sheets, tooltips, and switches.
Restyle or rewrite them only where the current surface treatment conflicts with
the new tokens.

### Add product-level primitives

#### `WorkspaceShell`

Owns:

- desktop Command Rail;
- compact tablet rail;
- mobile top header and bottom command bar;
- mobile More sheet;
- page content frame;
- theme toggle;
- account lifecycle banner placement;
- safe-area and responsive spacing.

The server layout resolves account state and agent name. Small client
navigation components own pathname awareness and sheet state.

#### `PageIntro`

Provides:

- optional eyebrow;
- page heading;
- short supporting description;
- one prioritized action slot;
- consistent responsive spacing.

It does not duplicate navigation labels or add decorative icons above headings.

#### `StatusSurface`

Displays a product state with:

- state label and supporting explanation;
- semantic icon;
- optional corrective action;
- animated status badge;
- variants for live, ready, processing, paused, inactive, warning, and
  attention-required states.

Status must never be communicated by color alone.

#### `MetricBand` and `MetricItem`

Provide a unified statistical surface rather than an identical grid of
unrelated cards. Each item supports:

- label;
- formatted value;
- optional comparison context;
- optional semantic state;
- optional value transition when fresh data replaces a prior value.

The initial server-rendered value does not count up from zero.

#### `ProductSurface`

A restrained content grouping primitive for meaningful sections. It supports
header, action, body, and footer slots without requiring every part. Avoid
nested `ProductSurface` elements and avoid wrapping plain page copy
unnecessarily.

#### `DataLedger`

A compound component for operational rows:

- supports semantic list or tabular modes;
- provides desktop column structure;
- adapts to labelled mobile rows;
- keeps row links and secondary actions keyboard accessible;
- supports empty, loading, error, and pagination regions;
- may animate insertion/removal but not static row mounting.

Use it for recent calls and billing activity. Do not add virtualization for the
current result sizes.

#### `SettingsSection`

Groups related form fields with:

- heading and concise supporting copy;
- control body;
- validation region;
- optional status or action;
- consistent disabled and read-only states.

It must preserve the current agent-form validation, mutation, and account-state
guards.

#### `ActionState`

Provides one reusable idle → pending → success/error presentation for save,
retry, copy, and similar actions. It must not replace existing server-side
authorization or error handling.

### BeUI usage

Use BeUI through the connected registry as a selective source, not as a theme.
Inspect and adapt only the approved patterns:

- `animated-badge`;
- `number`;
- `action-swap`;
- `tabs` when a real segmented view exists;
- `shared-layout-bg`;
- `bottom-sheet` for the mobile More surface.

Before copying source, confirm its current dependencies, accessibility,
licensing/source headers, React 19 compatibility, and compatibility with the
repository's Tailwind and utility helpers. Preserve required attribution.

Do not use the BeUI bounce sidebar, tilt card, magnetic button, shader
background, marquee, or decorative text animation.

## Page Composition

### Dashboard overview

Use the Operational Ledger composition:

1. `PageIntro` with date context and **Operations overview**.
2. `StatusSurface` stating whether the configured agent is answering calls.
3. One five-item `MetricBand`.
4. `DataLedger` with recent calls, caller intent, follow-up flag, duration, and
   start time.
5. A compact **Needs attention** surface showing recent calls flagged for
   follow-up. It must not imply that Presvo tracks whether follow-up was
   completed.
6. A plan-usage surface backed by the existing billing snapshot.

The dashboard still requests only the bounded recent-call set for its ledger.
Aggregate statistics come from the dedicated metrics endpoint.

### Calls

- Keep URL-driven search and pagination.
- Preserve the no-history and no-match states.
- Use `DataLedger` for desktop and mobile call rows.
- Display caller, intent, follow-up flag, duration, and date/time with
  progressive disclosure at narrow widths.
- Keep call-detail navigation server rendered.
- Recompose call detail using `PageIntro`, `StatusSurface`, and
  `ProductSurface` sections for summary, recording, transcript, and metadata.
- Preserve terminal-call removal behavior and destructive confirmation.

No tag, note, filter, or export control is added.

### Configured agent page

- Use the configured agent name in navigation and page heading.
- Lead with runtime state and any readiness blocker.
- Recompose existing settings into `SettingsSection` groups:
  identity, call handling, business context, and instructions.
- Preserve current save behavior, validation, enablement guards, and
  deactivating/inactive restrictions.
- Do not expose unsupported runtime pipeline options.

### Billing

- Lead with subscription state.
- Use a compact `MetricBand` for minutes remaining, used minutes, and plan.
- Use separate `ProductSurface` regions for subscription actions, usage
  progress, and ledger history.
- Preserve the distinction between period-end subscription cancellation and
  immediate account deactivation.
- Preserve Portal access rules, scheduled-cancellation copy, and inactive
  read-only access.

### Account

- Use calm settings rows for profile, session/security entry points, and
  account state.
- Keep the danger zone visually and semantically separate.
- Preserve the exact `DEACTIVATE` confirmation and every approved consequence.
- Do not present reversible deactivation as permanent deletion.

## Dashboard Metrics API

### Endpoint

Add an authenticated owner-scoped endpoint:

```http
GET /api/dashboard/metrics
```

Response contract:

```json
{
  "timezone": "Europe/Paris",
  "calls_today": 8,
  "calls_last_7_days": 34,
  "calls_previous_7_days": 28,
  "calls_change_from_previous_7_days": 6,
  "follow_up_flagged_last_7_days": 3,
  "average_duration_seconds_last_7_days": 162
}
```

### Metric definitions

- **Timezone:** confirmed business-profile timezone. Fall back to
  `Europe/Paris` only when no profile timezone exists.
- **Calls today:** non-deleted owner calls whose `started_at` falls within the
  current local calendar day.
- **Calls last 7 days:** non-deleted owner calls from the start of the local day
  six days ago through the current time.
- **Calls previous 7 days:** non-deleted owner calls from the seven local
  calendar days immediately before the current window.
- **Change:** integer difference between the current and previous seven-day
  counts. Avoid a misleading percentage when the previous period is zero.
- **Follow-up flagged:** calls in the current seven-day window with a valid
  structured summary whose `follow_up_required` value is `true`.
- **Average duration:** average non-null duration among terminal calls in the
  current seven-day window, rounded to an integer number of seconds. Return
  `null` when no eligible calls exist.

Calls with `started_at = null` are excluded from time-window metrics. Soft
deleted calls are excluded from every metric. Every query is owner scoped.

### Data implementation

- Add one repository/service query path for the complete metrics snapshot.
- Execute bounded aggregate SQL rather than fetching calls into application
  memory.
- Reuse the existing authentication and owner isolation boundary.
- Add no table, persisted rollup, cache, background job, or search extension.
- Keep the existing billing snapshot authoritative for minutes and plan state.

### Dashboard data flow

The server-rendered dashboard fetches, in parallel:

- account/activation and onboarding state;
- configured agent;
- five recent calls;
- usage snapshot;
- dashboard metrics.

Metric failure is non-critical. If the metrics request fails, the dashboard
still renders state, calls, and billing with an inline **Metrics temporarily
unavailable** region. Existing critical-read failures retain the route's
established error boundary.

## Server and Client Boundaries

Keep route layouts and pages as React Server Components.

Add small client boundaries for:

- `PresvoMotionProvider`;
- pathname-aware desktop and mobile navigation;
- mobile More sheet;
- animated badge;
- changed-value transition;
- action-state feedback;
- existing interactive forms and dialogs.

Do not move API fetching into effects. Do not introduce a dashboard-wide
client store. Server data remains authoritative and existing route
revalidation behavior remains intact.

If the agent configuration is fetched by both layout and page, use a
request-scoped memoized server helper to avoid duplicate backend reads without
creating cross-request user-data caching.

## Motion System

Motion is already installed and is imported from `motion/react`.

### Global policy

Wrap authenticated client motion islands with:

```tsx
<MotionConfig reducedMotion="user">
  {children}
</MotionConfig>
```

This follows Motion's current reduced-motion guidance. Layout and transform
motion must fall back to static or opacity-only feedback when the user requests
reduced motion.

Relevant references:

- [Motion for React](https://motion.dev/docs/react)
- [LayoutGroup](https://motion.dev/docs/react-layout-group)
- [AnimatePresence](https://motion.dev/docs/react-animate-presence)
- [Reduced-motion accessibility](https://motion.dev/docs/react-accessibility)

### Approved motion

- **Signature interaction:** active navigation marker glides between
  destinations through a namespaced shared layout ID.
- **Page entrance:** one content-region opacity and 6-8px vertical transition,
  220-280ms, without blocking navigation or waiting for exit.
- **Status change:** icon and concise copy crossfade when authoritative state
  changes.
- **Metric change:** transition only from a previously rendered value to a
  newly received value.
- **Action feedback:** idle/pending/success/error content swap, 120-200ms.
- **Mobile More sheet:** transform and opacity transition with no bounce or
  overshoot.
- **Ledger changes:** subtle opacity/position transition only for actual
  insertion or removal.

### Disallowed motion

- animation on every card at every route load;
- count-up from zero on initial render;
- parallax;
- continuous background animation;
- bounce or elastic easing;
- transform-heavy hover effects;
- motion that delays input, navigation, focus, validation, or error display;
- layout-property animation such as width, height, margin, or padding.

Use opacity and transform for animation. Use restrained ease-out-quart/quint
curves and faster exits. Test at 60fps on a representative mobile viewport.

## Loading, Empty, Error, and Account States

### Loading

- Preserve route-level loading boundaries.
- Skeletons match final geometry and do not pulse aggressively.
- Do not replace server-rendered content with an unnecessary client loading
  phase.

### Empty

- Dashboard/call empty states explain what will appear after the first call.
- Search-empty state preserves the query and gives a clear reset action.
- Empty metric windows render zero or unavailable according to the metric
  definition; they do not fabricate a trend.

### Error

- Metrics fail independently with a bounded inline retry-safe message.
- Mutations retain existing specific error copy and focus behavior.
- Motion does not shake large regions or hide an error while animating.

### Lifecycle

- Deactivating and inactive states preserve their global banner.
- Agent and mutation controls remain disabled according to existing policies.
- Inactive owners retain read-only call, recording, billing, and saved
  configuration access.
- Status surfaces use exact lifecycle meaning and never imply permanent
  deletion.

## Accessibility

- Maintain semantic landmarks and one page-level heading.
- Provide visible `:focus-visible` treatment through semantic ring tokens.
- Keep all navigation and primary controls keyboard operable.
- Give icon-only compact-rail controls accessible labels and tooltips.
- Expose complete configured agent names to assistive technology.
- Use text/icon/state combinations rather than color alone.
- Ensure dialogs and sheets trap focus, restore focus, close with Escape, and
  announce their titles.
- Respect reduced-motion preferences globally and with explicit fallbacks for
  bespoke transitions.
- Verify light and dark contrast at normal and muted text sizes.
- Do not let the mobile command bar obscure focused controls.

## Verification

### API tests

Add focused tests for:

- unauthenticated rejection;
- owner isolation;
- soft-deleted call exclusion;
- calls with null `started_at`;
- Europe/Paris local-day boundaries;
- daylight-saving-time boundaries;
- current and previous seven-day windows;
- valid structured follow-up flags only;
- average duration with eligible, ineligible, and empty sets;
- default timezone behavior;
- PostgreSQL behavior for JSON follow-up extraction and time aggregates.

Run complete SQLite and PostgreSQL/Redis API matrices because the endpoint
touches shared call-history repositories and JSON storage.

### Web tests

Preserve and adapt all existing dashboard, shell, calls, agent, billing,
account, onboarding, and lifecycle tests.

Add focused tests for:

- configured agent name and fallback in desktop/mobile navigation;
- long-name truncation without accessible-name loss;
- desktop, compact-rail, and mobile navigation composition;
- mobile More sheet keyboard and focus behavior;
- metric rendering and independent error fallback;
- responsive ledger labels;
- curated light/dark theme behavior;
- removed legacy visual controls;
- action-state transitions;
- reduced-motion configuration and bespoke fallbacks;
- inactive read-only behavior after recomposition.

### Visual and build gates

- Add deterministic Playwright screenshots for the dashboard at representative
  desktop and mobile widths in light and dark modes.
- Use deterministic local fixtures and stable fonts to avoid screenshot drift.
- Run complete web tests, Biome, TypeScript, production build, and
  `git diff --check`.
- Inspect the final UI at narrow, tablet, desktop, zoomed, dark, and
  reduced-motion settings.

## Implementation Slices

1. **Tokens and product frame**
   - Add Presvo theme tokens and fixed typography.
   - Add motion provider and approved motion primitives.
   - Build responsive `WorkspaceShell`.
   - Remove legacy visual controls and preset imports only after the new shell
     is proven.

2. **Dashboard metrics**
   - Add authenticated aggregate API, schemas, repository/service logic, and
     focused cross-database tests.
   - Add a typed web client and independent metric fallback.

3. **Dashboard composition**
   - Add product-level surface, status, metric, and ledger primitives.
   - Recompose the dashboard with the approved Operational Ledger hierarchy.

4. **Remaining logged-in pages**
   - Recompose calls list/detail, configured agent, billing, and account.
   - Preserve every current functional and lifecycle contract.

5. **Cleanup and verification**
   - Remove unused preset/font/layout preference code and dead page-specific
     styling.
   - Complete accessibility, screenshot, regression, and production-build
     gates.

Each slice receives focused tests and review before the next slice begins.

## Success Criteria

The redesign is successful when:

- the authenticated product has one coherent Presvo identity in light and dark
  modes;
- a user can identify receptionist health and required action immediately;
- the dashboard displays accurate, clearly defined balanced operational
  statistics;
- all five logged-in destinations share reusable product primitives;
- mobile navigation keeps Overview, Calls, and the configured agent one tap
  away;
- motion improves state comprehension without becoming a visual event;
- reduced-motion users receive complete, calm behavior;
- existing product and lifecycle behavior remains unchanged;
- API, web, accessibility, screenshot, and production-build gates pass.
