# Presvo UI Migration and Production Hardening Design

**Date:** 2026-07-29

**Status:** Approved design

**Target application:** `apps/web`

**Visual reference:** `Presvo_frontend`

## 1. Objective

Adopt the `Presvo_frontend` template as the visual and interaction language for
the entire Presvo web experience while keeping `apps/web` as the sole
production frontend.

The migration must:

- preserve the template's colors, typography, spacing, borders, shadows, card
  styles, responsive behavior, and visual hierarchy wherever the current
  product flow permits;
- preserve all existing backend-connected behavior, authentication, tenant
  isolation, France-first product rules, activation state, and test coverage;
- add interactive UI previews for backend-pending capabilities;
- label every preview capability clearly so it cannot be mistaken for a
  persisted, billed, destructive, or telephony-backed action;
- leave the frontend organized around typed interfaces that real backend
  implementations can replace later without redesigning screens.

## 2. Product Truth and Scope

Presvo remains France-first. The migrated UI uses the real product's content and
contracts:

- French Presvo-provided phone numbers;
- the current five-milestone activation journey;
- the currently supported starter plan;
- readiness-gated call routing;
- the existing STT → LLM → TTS launch pipeline;
- existing Clerk authentication, FastAPI APIs, Stripe-hosted billing, Telnyx
  provisioning, LiveKit calls, private recordings, summaries, and account
  lifecycle behavior.

The US legal-business mock data, USD plan claims, and unsupported template
operations are not copied as product truth. Additional plans, live-call
monitoring, advanced assistant options, notifications, voice previews, and
other unsupported capabilities may appear only as explicit previews.

The visual system applies to the complete web experience:

- public landing page;
- sign-in and sign-up handoff;
- activation/onboarding;
- authenticated dashboard;
- call history and call detail;
- assistant configuration;
- billing and usage;
- account and settings;
- loading, empty, error, unauthorized, and not-found states.

## 3. Chosen Migration Strategy

Use a phased in-place migration inside `apps/web`.

`apps/web` remains authoritative for:

- Next.js App Router routing and rendering;
- Clerk and local-development authentication;
- server components and server actions;
- FastAPI clients and request authentication;
- production configuration;
- Vitest and Playwright coverage;
- the existing activation and account lifecycle state machines.

`Presvo_frontend` is a visual and component reference. It does not become a
second deployed application, router, state store, or API client.

This strategy avoids a big-bang rewrite and allows each migrated vertical slice
to retain working backend behavior and passing tests.

## 4. Route and Capability Map

| Experience | Production route | Capability status |
| --- | --- | --- |
| Public landing | `/` | Live |
| Sign in | `/sign-in/[[...sign-in]]` | Live |
| Sign up | `/sign-up/[[...sign-up]]` | Live |
| Activation | `/activate` | Live |
| Overview | `/dashboard` | Live |
| Call history | `/dashboard/calls` | Live |
| Call detail | `/dashboard/calls/[callId]` | Live |
| Assistant configuration | `/dashboard/agent` | Live, with preview-only extensions |
| Billing and usage | `/dashboard/billing` | Live, with preview-only plan comparison |
| Account and settings | `/dashboard/account` | Live, with preview-only extensions |
| Live-call monitor | `/dashboard/live-call` | Preview until a reliable realtime backend exists |
| Notifications | shell panel | Preview until a customer-facing API is approved |

Preview routes and panels must remain within the authenticated dashboard unless
the capability is intentionally public.

## 5. Visual Fidelity Contract

The template's visual system is a compatibility requirement, not loose
inspiration.

### 5.1 Core visual tokens

The light theme starts from the template values:

```css
--radius: 0.875rem;
--background: oklch(0.976 0.004 120);
--foreground: oklch(0.245 0.012 150);
--card: oklch(1 0 0);
--card-foreground: oklch(0.245 0.012 150);
--popover: oklch(1 0 0);
--popover-foreground: oklch(0.245 0.012 150);
--primary: oklch(0.42 0.045 152);
--primary-foreground: oklch(0.985 0.005 120);
--primary-soft: oklch(0.93 0.028 152);
--primary-glow: oklch(0.68 0.075 152);
--secondary: oklch(0.962 0.006 130);
--secondary-foreground: oklch(0.32 0.015 150);
--muted: oklch(0.962 0.006 130);
--muted-foreground: oklch(0.545 0.014 145);
--accent: oklch(0.945 0.018 150);
--accent-foreground: oklch(0.33 0.03 152);
--destructive: oklch(0.577 0.19 27.5);
--destructive-foreground: oklch(0.985 0.003 120);
--success: oklch(0.58 0.11 152);
--success-foreground: oklch(0.985 0.003 120);
--warning: oklch(0.72 0.13 75);
--warning-foreground: oklch(0.25 0.03 75);
--border: oklch(0.923 0.006 135);
--input: oklch(0.923 0.006 135);
--ring: oklch(0.62 0.06 152);
--shadow-card:
  0 1px 2px oklch(0.245 0.012 150 / 0.04),
  0 8px 24px oklch(0.245 0.012 150 / 0.04);
--shadow-raised:
  0 2px 4px oklch(0.245 0.012 150 / 0.05),
  0 16px 40px oklch(0.245 0.012 150 / 0.07);
```

Dark-theme values from the template are retained and made fully functional
through the existing theme infrastructure. Light remains the visual baseline.

### 5.2 Typography and sizing

- Use the template's observed
  `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`
  typography and metrics.
- Preserve heading sizes, weights, tracking, and line heights.
- Preserve the 11px uppercase label treatment with `600` weight and `0.08em`
  tracking.
- Preserve compact controls, 14px body text, 12px supporting text, and 20–24px
  page headings.
- Use tabular numerals for durations, balances, usage, dates where values align,
  and call metrics.

### 5.3 Layout and surfaces

- Keep the 256px desktop sidebar, 16px shell gap, and compact sticky header.
- Keep the off-white canvas, white card surfaces, hairline borders, soft
  shadows, and restrained sage emphasis.
- Preserve template card padding, grid proportions, badges, call rows,
  transcript bubbles, forms, tables, progress bars, and empty states.
- Preserve the sidebar-to-sheet and table-to-card mobile transformations.
- Add safe-area spacing and prevent clipped content on small screens.

Accessibility may change invisible semantics and visible focus treatments.
Those changes must fit the same palette and geometry rather than introduce a
different visual language.

### 5.4 Visual regression contract

Playwright snapshots cover:

- landing;
- activation;
- dashboard overview;
- calls list;
- call detail;
- assistant;
- billing;
- account/settings;

at `1440 × 1100` desktop and `390 × 844` mobile viewports. Light and dark
snapshots are captured wherever the theme switcher is available. Token-level
tests protect exact colors, radii, and shadows from accidental drift.

## 6. Component Architecture

### 6.1 Reuse before duplication

Existing `apps/web` shadcn primitives remain the base for buttons, inputs,
labels, dialogs, sheets, tables, tabs, selects, switches, sliders, tooltips,
toasts, and other controls.

Their variants and tokens are restyled to match the template. Equivalent
components are not copied into parallel directories.

### 6.2 Product components

Template-specific product vocabulary may be ported or adapted:

- application shell, sidebar, mobile navigation, and page header;
- stat cards and metric bands;
- status badges and preview badges;
- active-call banner;
- assistant status card;
- phone-number card;
- usage progress and plan cards;
- setup checklist;
- call table and mobile call item;
- transcript message;
- recording/audio player presentation;
- knowledge editor;
- voice selector;
- test-assistant drawer;
- save bar;
- confirmation dialog;
- empty, loading, and error surfaces.

These components accept typed view models and callbacks. They do not fetch data,
read authentication, or know FastAPI response shapes directly.

### 6.3 Future-use components

Future-use components may be retained in `apps/web` when they express a planned
product capability and:

- compile and pass lint/type checks;
- do not introduce a duplicate framework;
- remain tree-shakeable and absent from active route bundles;
- use the same tokens and primitives;
- have a named planned consumer.

Unused generic template primitives are not copied merely for completeness.

## 7. Data Flow and Backend Integration

Backend-backed data follows:

```text
FastAPI response
  → typed API client
  → domain/view-model mapper
  → server component or server action
  → Presvo presentation component
```

The existing API clients remain organized by domain:

- activation;
- dashboard;
- calls;
- agent;
- billing;
- account;
- onboarding.

View-model mappers prevent presentation components from depending directly on
wire-format names or nullable backend details.

Reads remain server-first. Authenticated mutations continue through server
actions with schema validation, explicit pending states, customer-safe inline
errors, truthful toasts, and path revalidation.

Optimistic behavior is allowed only for reversible local presentation. Billing,
telephony, routing, call removal, recording removal, and account lifecycle
commands display backend-confirmed state.

URL state owns shareable filters, pagination, tabs, and selections. Locale-aware
formatters own dates, durations, numbers, currency, and French phone numbers.
No product behavior depends on a hardcoded current date.

The shell search submits to `/dashboard/calls?q=<term>`. The call-history route
owns the query, calls the existing backend search contract, and renders the
matching list and no-results state.

## 8. Preview Capability Model

Every capability declares one of:

```ts
type CapabilityStatus = "live" | "preview" | "unavailable";
```

### Live

- Reads and writes real backend state.
- Uses authenticated production contracts.
- May show provider or readiness failures through bounded customer-safe states.

### Preview

- Displays a persistent `Preview` badge near the feature title or primary
  control.
- Uses isolated local client state.
- Includes concise copy explaining that changes reset on reload or through the
  supplied reset action.
- Never calls billing, telephony, deletion, routing, account lifecycle, or
  provider APIs.
- Never emits copy such as “saved,” “purchased,” “deleted,” “enabled,” or
  “call ended” unless the message explicitly states that it is a preview.

### Unavailable

- Represents a missing real prerequisite or blocked capability.
- Explains the next customer action when one exists.
- Does not silently fall back to preview behavior.

Preview features use typed interfaces with a local implementation today and a
future backend implementation at the same boundary. Initial preview candidates
include:

- live-call monitor and simulated transcript;
- notification center;
- advanced assistant providers and controls;
- extra plan comparison;
- voice preview and test assistant;
- backend-pending settings.

## 9. Backend Change Policy

Frontend integration may add backend support when a real screen needs data or a
mutation that the current API does not expose.

Backend changes must be narrowly scoped and additive:

- Pydantic request and response contracts;
- tenant-isolated authenticated routes;
- service and repository boundaries;
- Alembic migrations only when durable state is necessary;
- idempotent commands;
- bounded customer-safe errors;
- no provider secrets or internal identifiers in customer responses;
- API contract, authorization, and cross-tenant tests.

Backend work must not weaken:

- France-only launch constraints;
- starter-plan enforcement;
- activation and readiness gates;
- authoritative minute accounting;
- durable call state;
- private recording access;
- asynchronous provider cleanup;
- account lifecycle truth;
- existing webhook and outbox safety.

Realtime remains preview-only until its identity-key mismatch is fixed and the
customer-facing contract is explicitly approved.

## 10. Error Handling and Interaction States

Every major surface supports:

- loading skeleton;
- empty state;
- validation error;
- backend error with a next step;
- unavailable prerequisite;
- disabled and pending controls;
- success confirmation based on real backend acknowledgement;
- long and localized content;
- narrow mobile layout.

Forms:

- use semantic labels, names, types, autocomplete, and input modes;
- keep submit controls enabled until submission starts;
- focus the first invalid field;
- warn before navigation when real edits are unsaved;
- update the saved baseline after successful persistence;
- avoid fake persistence for preview state.

Destructive actions require confirmation and preserve the backend's exact
lifecycle semantics.

## 11. Accessibility and Motion

The migrated app must provide:

- one clear `main` landmark on every page;
- a visible-on-focus skip link;
- hierarchical headings;
- accessible names for every icon-only control;
- semantic links for navigation and buttons for actions;
- correct radio-group, tab, dialog, table, and form semantics;
- visible `focus-visible` treatment;
- appropriate live regions for bounded async updates;
- safe transcript announcement behavior;
- keyboard access for all interactive previews;
- minimum practical touch targets;
- reduced-motion alternatives for every custom animation;
- safe-area-aware mobile shell and overlays;
- working browser zoom.

Motion uses transform and opacity where possible and never relies on
`transition: all`.

## 12. Known Defects Included in the Migration

The work explicitly fixes:

- the empty activity chart;
- the test-assistant timer that remains stuck in a speaking phase;
- settings save state that remains dirty after a successful save;
- clipped and unpadded onboarding on mobile;
- missing landmarks and skip navigation;
- the non-functional global search affordance;
- the unlabeled mobile live-call action;
- local-only call filters and tabs that should be URL-addressable;
- hardcoded mock clock values;
- incomplete reduced-motion handling;
- invalid or incomplete custom radio semantics;
- private call details in public metadata;
- lint failures and stale generated template code.

## 13. Testing Strategy

Behavior changes follow test-driven development.

### Frontend tests

- Vitest component tests for product components and state transitions.
- Server-action tests for validation, errors, revalidation, and mutation truth.
- API-client and view-model mapping tests.
- Accessibility assertions for names, landmarks, form semantics, focus, and
  preview labeling.
- Token tests for visual fidelity.
- Playwright flows for activation, navigation, editing, calls, billing, account
  lifecycle, preview isolation, and restart/resume.
- Desktop and mobile visual snapshots in light and supported dark themes.

### Backend tests

When APIs change:

- schema and router contract tests;
- auth and cross-tenant tests;
- service and repository tests;
- migration tests where schema changes;
- idempotency and concurrency tests for commands that touch durable state.

### Verification gates

Before completion:

- Biome check;
- TypeScript type check;
- Vitest suite;
- Next.js production build;
- relevant Playwright tests and visual snapshots;
- relevant FastAPI lint/type/test commands when backend code changes;
- production dependency audit;
- route-bundle review for copied future components.

## 14. Migration Sequence

### Phase 1: Visual foundation and shell

- Install the exact token contract.
- Align typography and shared primitives.
- Replace the authenticated shell, header, sidebar, and mobile navigation.
- Add preview badge and capability-status primitives.
- Establish token and shell visual snapshots.

### Phase 2: Entry and activation experience

- Restyle landing and authentication handoff.
- Recompose the real five-milestone activation flow with the template
  onboarding hierarchy.
- Preserve autosave, carrier confirmation, payment/provisioning consent,
  forwarding verification, explicit go-live, restart, and resume behavior.

### Phase 3: Core dashboard and calls

- Migrate dashboard metrics, usage, answering state, checklist, and call ledger.
- Migrate call history filters, responsive table/cards, pagination, and empty
  states.
- Migrate call detail, transcript, summary, recording, and removal flow.
- Add the clearly labeled live-call preview.

### Phase 4: Configuration, billing, and account

- Migrate assistant configuration and add labeled preview tabs/controls.
- Migrate billing and usage; preview unsupported plan comparisons.
- Migrate account, settings, theme, and lifecycle controls.
- Add the preview notification center.

### Phase 5: Hardening

- Complete accessibility and reduced-motion review.
- Remove dead controls, stale mocks, and accidental private metadata.
- Prune duplicate or unused dependencies and components.
- Complete route-level tests, visual baselines, builds, audits, and regression
  checks.

Each phase must leave existing live routes functional and verified. A phase is
not complete merely because its visual snapshot matches.

## 15. Completion Criteria

The migration is complete when:

- `apps/web` is the sole production frontend;
- the complete experience uses the approved template visual system;
- exact visual tokens and representative layouts are regression-tested;
- all currently implemented features still operate against the existing
  backend;
- every unsupported interaction is labeled `Preview` and isolated from real
  APIs;
- no dead controls, fake production success, hardcoded clocks, inaccessible
  icon actions, or private metadata leaks remain;
- activation and restart/resume browser proofs pass;
- frontend checks, type checks, tests, production build, and required browser
  tests pass;
- all related backend checks pass if backend code changed;
- preview interfaces can accept future backend adapters without redesigning
  their presentation components.

## 16. Non-Goals

- Replacing Next.js with TanStack Start.
- Deploying or maintaining two frontends.
- Changing France-first launch scope.
- Claiming unsupported plans or providers are purchasable.
- Enabling customer realtime before its backend identity contract is repaired.
- Rewriting safe backend state machines solely to match a mock interaction.
- Copying every unused template primitive or dependency.
