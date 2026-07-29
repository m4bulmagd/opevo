# Presvo UI Migration Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `apps/web` the production-ready Presvo frontend, visually matching `Presvo_frontend` while preserving France-first product truth, existing backend behavior, and explicit Preview labeling for local-only features.

**Architecture:** Migrate in place through five independently releasable phases. Keep the Next.js App Router, Clerk/local authentication, server actions, typed FastAPI clients, and existing domain state machines. Treat `Presvo_frontend` as a read-only visual reference and adapt its presentation patterns onto existing `apps/web` primitives and route contracts.

**Tech Stack:** Next.js 16, React 19, TypeScript 5.9, Tailwind CSS 4, shadcn/Base UI primitives, Vitest, Testing Library, Playwright, FastAPI, pytest.

## Global Constraints

- The approved design is `docs/superpowers/specs/2026-07-29-presvo-ui-migration-design.md`.
- `apps/web` is the only production frontend; do not add TanStack Router or a second deployment.
- Preserve the exact template token values, system font stack, 256px sidebar, 16px desktop shell gap, compact header, card borders, radii, and shadows.
- Preserve French phone-number behavior, five-milestone activation, current plan truth, readiness gating, Clerk, Stripe, Telnyx, LiveKit, and existing FastAPI contracts.
- Use existing shadcn primitives before adding components.
- Every unsupported interaction has `status: "preview"` and a visible `Preview` label; preview state is local and never calls a production mutation.
- Additive backend changes are allowed only when a migrated live screen cannot express an already-approved product behavior through the current contract.
- Follow red-green-refactor for every behavior change.
- Do not edit `Presvo_frontend`; it remains a reference.
- Do not update visual snapshots until semantic assertions pass and the rendered result has been inspected at both approved viewports.
- Commit after every green roadmap task with the commit message specified by the phase plan.

---

## Shared Contracts

### Capability status

All navigation and preview surfaces use one shared type:

```ts
export type CapabilityStatus = "live" | "preview" | "unavailable";
```

`Preview` is persistent visible text, not tooltip-only, color-only, or accessible-name-only.

### Presentation boundary

Route data follows:

```text
FastAPI response
  -> typed apps/web API client
  -> domain/view-model mapper
  -> server component or server action
  -> presentational component
```

Presentational components accept typed values and callbacks. They do not import authenticated API clients or FastAPI wire types.

### URL-owned state

The following state is shareable and belongs in the URL:

- call search: `/dashboard/calls?q=<term>`;
- call filters and pagination;
- supported page tabs;
- selected call identifiers represented by routes.

Transient drawers, preview simulations, unread notification state, and unsaved form edits remain local.

### Viewport and theme matrix

Visual coverage uses:

| Viewport | Size | Themes |
| --- | --- | --- |
| Desktop | 1440 × 1100 | Light and dark |
| Mobile | 390 × 844 | Light and dark |

Landing and activation add page-specific snapshots in their phase. The authenticated shell baseline is established in Phase 1.

### Required verification at every phase boundary

Run from `apps/web`:

```bash
npm run check
npm run typecheck
npm run test:ci
npm run build
```

Run the phase-specific Playwright project or spec after starting the documented local test stack. If a phase changes `apps/api`, also run that package's documented lint, type, and pytest commands before the phase commit.

---

## Phase 1: Visual Foundation and Responsive Shell

Detailed plan:
`docs/superpowers/plans/2026-07-29-presvo-ui-foundation-shell.md`

- [ ] Install exact light/dark visual tokens and system typography.
- [ ] Align shared controls, cards, surfaces, headings, and capability badges.
- [ ] Introduce grouped route metadata with capability status.
- [ ] replace the dark desktop command rail with the template sidebar.
- [ ] Replace mobile bottom navigation with a focus-managed left sheet.
- [ ] Add functional shell search and local Preview notifications.
- [ ] Establish shell unit, accessibility, overflow, reduced-motion, and visual regression coverage.

Exit criteria:

- All authenticated routes render inside the new shell without changing their data behavior.
- Desktop navigation is 256px at `lg` and absent below `lg`.
- Mobile navigation opens from the header, closes after route selection, restores trigger focus on Escape, and does not clip at 390px.
- Search submits to `/dashboard/calls?q=<term>`.
- Notifications visibly say `Preview` and perform no network mutation.

---

## Phase 2: Public Entry and Activation

Plan filename:
`docs/superpowers/plans/2026-07-29-presvo-entry-activation.md`

- [ ] Inventory landing, Clerk handoff, activation route, actions, and current tests after Phase 1.
- [ ] Write the phase plan against the Phase 1 token and primitive interfaces.
- [ ] Restyle `/`, sign-in, and sign-up while keeping truthful France-first copy.
- [ ] Recompose the existing five activation milestones in the template's centered onboarding hierarchy.
- [ ] Preserve autosave, carrier confirmation, payment/provisioning consent, forwarding verification, go-live, restart, and resume.
- [ ] Add responsive loading, error, retry, and unsupported-provider states.
- [ ] Add landing and activation snapshots at the approved viewports.

Exit criteria:

- Returning users resume the server-authoritative milestone.
- Provisioning, billing, and go-live copy never report success before backend confirmation.
- The full activation browser proof passes on desktop and mobile.

---

## Phase 3: Dashboard, Calls, and Live-Call Preview

Plan filename:
`docs/superpowers/plans/2026-07-29-presvo-dashboard-calls.md`

- [ ] Inventory current dashboard/call APIs, view models, actions, and test fixtures after Phase 2.
- [ ] Write the phase plan against the established shell and surface interfaces.
- [ ] Port overview metrics, answering state, usage, activity, and setup checklist.
- [ ] Port call history filters, pagination, desktop table, and mobile cards.
- [ ] Connect shell search and route-owned filters to the existing search contract.
- [ ] Port call detail summary, transcript, recording presentation, and confirmed removal flow.
- [ ] Add `/dashboard/live-call` as an isolated local-state Preview.
- [ ] Add dashboard, call history, call detail, and live-call visual/interaction coverage.

Exit criteria:

- Recharts output is non-empty for non-empty dashboard data and has an accessible text summary.
- Call URL state survives reload and back/forward navigation.
- Call removal remains backend-confirmed and tenant-safe.
- Live-call controls are visibly Preview and emit no telephony or billing requests.

---

## Phase 4: Assistant, Billing, Account, and Preview Extensions

Plan filename:
`docs/superpowers/plans/2026-07-29-presvo-configuration-billing-account.md`

- [x] Inventory the live agent, billing, account, and lifecycle contracts after Phase 3.
- [x] Write the phase plan against the established presentation/view-model boundaries.
- [x] Port live assistant configuration without weakening server validation.
- [x] Add isolated Preview-only advanced assistant controls and voice preview.
- [x] Port live plan, usage, invoices, and Stripe-hosted billing actions.
- [x] Add a non-purchasable Preview plan comparison.
- [x] Port account identity, theme, and lifecycle controls.
- [x] Add Preview settings extensions and finalize shell notification presentation.
- [x] Add unsaved-change warnings and truthful save baselines.

Exit criteria:

- Live settings persist only after confirmed server actions.
- Preview controls reset locally and cannot invoke provider, billing, or lifecycle APIs.
- Billing and account destructive flows preserve confirmation and backend truth.
- Assistant, billing, and account snapshots pass at approved viewports.

---

## Phase 5: Production Hardening and Release Gate

Plan filename:
`docs/superpowers/plans/2026-07-29-presvo-production-hardening.md`

- [ ] Write the hardening plan from the accumulated route and component inventory.
- [ ] Audit keyboard order, focus visibility/restoration, landmarks, labels, live regions, contrast, and screen-reader names.
- [ ] Verify reduced motion and remove unbounded or decorative motion that obscures state.
- [ ] Remove dead controls, stale mock dates, duplicate components, accidental metadata exposure, and unreachable branches.
- [ ] Confirm route bundles exclude future-use components until imported.
- [ ] Audit production dependencies and resolve high-severity actionable findings.
- [ ] Run all frontend and changed-backend checks.
- [ ] Run the complete Playwright flow and visual matrix.
- [ ] Document environment variables, local integration, preview limitations, and backend replacement seams.

Exit criteria:

- Every completion criterion in the approved design is evidenced by a command, test, screenshot inspection, or explicit code audit.
- No `TODO`, `FIXME`, dead link, unlabeled icon action, fake success, or hardcoded current date remains in production routes.
- `apps/web` builds in production mode and is ready for backend/environment integration without a second frontend.

---

## Roadmap Completion Checklist

- [ ] All five phase plans have been executed and their checkboxes updated.
- [ ] The route/capability map in the approved design matches the shipped UI.
- [ ] Live features remain backend-backed and Preview features remain local-only.
- [ ] Exact design tokens and representative layouts are regression-protected.
- [ ] Frontend checks, type checks, tests, build, Playwright flows, and relevant backend checks pass.
- [ ] Final verification evidence is recorded in the Phase 5 handoff.
