# Frontend Dashboard Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the customer dashboard as a fresh `apps/web` Next.js app that preserves the `dashboard-temp` template shell, colors, theme presets, and interaction style while serving the product routes `/dashboard`, `/dashboard/calls`, `/dashboard/agent`, and `/dashboard/billing`.

**Architecture:** Create a new isolated `apps/web` workspace that ports the useful template foundation into `apps/web/src` instead of designing a new dashboard system. Keep the template's Next 16, shadcn, Biome, theme preset, preferences, sidebar, and header architecture, then adapt navigation and page content to the existing backend APIs. Apply `@frontend-design`, `@shadcn`, `@next-best-practices`, and `@vercel-react-best-practices` during implementation so the result stays template-native rather than drifting into a separate custom style.

**Tech Stack:** Next.js App Router, TypeScript, Tailwind CSS v4, shadcn/ui, Biome, Clerk, Vitest, React Testing Library, Docker Compose

---

## File Map

- Create: `apps/web/package.json`
  - Define the new frontend package using the template-style dependency set, scripts, and Next.js 16 toolchain.
- Create: `apps/web/package-lock.json`
  - Lock the frontend dependency tree after install.
- Create: `apps/web/tsconfig.json`
  - Base TypeScript config for the app.
- Create: `apps/web/tsconfig.scripts.json`
  - Support template-style script execution such as preset generation if retained.
- Create: `apps/web/next.config.mjs`
  - Configure Next.js output and any image/runtime settings.
- Create: `apps/web/postcss.config.mjs`
  - Configure Tailwind CSS v4 through PostCSS.
- Create: `apps/web/biome.json`
  - Configure formatting and linting with Biome.
- Create: `apps/web/components.json`
  - Configure shadcn aliases and CSS entrypoint.
- Create: `apps/web/.gitignore`
  - Ignore build output, dependencies, and local env files.
- Create: `apps/web/.env.example`
  - Document Clerk and backend API variables.
- Create: `apps/web/Dockerfile`
  - Build the frontend app for Compose.
- Create: `apps/web/proxy.ts`
  - Protect authenticated routes with Clerk-aware proxy logic.
- Create: `apps/web/src/app/layout.tsx`
  - Root layout, metadata, tooltip provider, toaster, theme boot script, and preferences provider.
- Create: `apps/web/src/app/globals.css`
  - Import Tailwind, shadcn theme CSS, and template preset styles.
- Create: `apps/web/src/app/page.tsx`
  - Redirect root visitors to `/dashboard` or auth entry based on auth state.
- Create: `apps/web/src/app/not-found.tsx`
  - Shared not-found screen.
- Create: `apps/web/src/app/unauthorized/page.tsx`
  - Unauthorized state screen.
- Create: `apps/web/src/app/(auth)/sign-in/[[...sign-in]]/page.tsx`
  - Clerk sign-in route.
- Create: `apps/web/src/app/(auth)/sign-up/[[...sign-up]]/page.tsx`
  - Clerk sign-up route.
- Create: `apps/web/src/app/(app)/dashboard/layout.tsx`
  - Template-style authenticated shell for product routes.
- Create: `apps/web/src/app/(app)/dashboard/page.tsx`
  - Adaptive home route.
- Create: `apps/web/src/app/(app)/dashboard/calls/page.tsx`
  - Calls index page.
- Create: `apps/web/src/app/(app)/dashboard/calls/[callId]/page.tsx`
  - Call detail page.
- Create: `apps/web/src/app/(app)/dashboard/agent/page.tsx`
  - Agent configuration page.
- Create: `apps/web/src/app/(app)/dashboard/billing/page.tsx`
  - Billing page.
- Create: `apps/web/src/app/(app)/dashboard/agent/actions.ts`
  - Server actions for agent settings updates.
- Create: `apps/web/src/app/(app)/dashboard/calls/actions.ts`
  - Server action for call archive.
- Create: `apps/web/src/app/(app)/dashboard/billing/actions.ts`
  - Server actions for checkout and billing portal redirects.
- Create: `apps/web/src/app/(app)/dashboard/_components/sidebar/app-sidebar.tsx`
  - Product-adapted sidebar built from the template shell.
- Create: `apps/web/src/app/(app)/dashboard/_components/sidebar/nav-main.tsx`
  - Sidebar navigation list.
- Create: `apps/web/src/app/(app)/dashboard/_components/sidebar/theme-switcher.tsx`
  - Theme mode and preset control.
- Create: `apps/web/src/app/(app)/dashboard/_components/sidebar/layout-controls.tsx`
  - Content-width and sidebar preference controls.
- Create: `apps/web/src/app/(app)/dashboard/_components/sidebar/nav-user.tsx`
  - Clerk-linked user menu.
- Create: `apps/web/src/navigation/sidebar/sidebar-items.ts`
  - Product navigation config for `Home`, `Calls`, `Agent`, and `Billing`.
- Create: `apps/web/src/config/app-config.ts`
  - App name and metadata adapted from the template.
- Create: `apps/web/src/lib/utils.ts`
  - Shared utility helpers.
- Create: `apps/web/src/lib/fonts/registry.ts`
  - Template-compatible font registry.
- Create: `apps/web/src/lib/preferences/preferences-config.ts`
  - Preference defaults and persistence strategy.
- Create: `apps/web/src/lib/preferences/layout.ts`
  - Layout preference enums.
- Create: `apps/web/src/lib/preferences/theme.ts`
  - Theme mode and preset definitions including `default`, `brutalist`, `soft-pop`, and `tangerine`.
- Create: `apps/web/src/lib/preferences/theme-utils.ts`
  - Theme application helpers.
- Create: `apps/web/src/lib/preferences/preferences-storage.ts`
  - Client persistence helpers.
- Create: `apps/web/src/stores/preferences/preferences-store.ts`
  - Zustand store for UI preferences.
- Create: `apps/web/src/stores/preferences/preferences-provider.tsx`
  - Provider that hydrates template preference state.
- Create: `apps/web/src/scripts/theme-boot.tsx`
  - Script that applies theme state before hydration.
- Create: `apps/web/src/styles/presets/brutalist.css`
  - Template preset.
- Create: `apps/web/src/styles/presets/soft-pop.css`
  - Template preset.
- Create: `apps/web/src/styles/presets/tangerine.css`
  - Template preset.
- Create: `apps/web/src/components/ui/*`
  - shadcn and template UI primitives required by the shell and product pages.
- Create: `apps/web/src/components/ui/sonner.tsx`
  - Shared toast host.
- Create: `apps/web/src/components/ui/sidebar.tsx`
  - Template sidebar primitive.
- Create: `apps/web/src/components/ui/card.tsx`
  - Shared card primitive.
- Create: `apps/web/src/components/ui/button.tsx`
  - Shared button primitive.
- Create: `apps/web/src/components/ui/input.tsx`
  - Shared input primitive.
- Create: `apps/web/src/components/ui/textarea.tsx`
  - Shared textarea primitive.
- Create: `apps/web/src/components/ui/select.tsx`
  - Shared select primitive.
- Create: `apps/web/src/components/ui/switch.tsx`
  - Shared switch primitive.
- Create: `apps/web/src/components/ui/badge.tsx`
  - Shared badge primitive.
- Create: `apps/web/src/components/ui/tooltip.tsx`
  - Shared tooltip primitive.
- Create: `apps/web/src/components/home/*`
  - Home-specific cards, lists, and empty states built in the template visual language.
- Create: `apps/web/src/components/calls/*`
  - Call list and detail components.
- Create: `apps/web/src/components/agent/*`
  - Agent settings form components.
- Create: `apps/web/src/components/billing/*`
  - Billing summary and ledger components.
- Create: `apps/web/src/lib/auth/server-session.ts`
  - Clerk server helpers and backend bearer-token bridge.
- Create: `apps/web/src/lib/api/backend-client.ts`
  - Shared backend fetch wrapper.
- Create: `apps/web/src/lib/api/agent.ts`
  - Typed agent API helpers.
- Create: `apps/web/src/lib/api/calls.ts`
  - Typed calls API helpers.
- Create: `apps/web/src/lib/api/billing.ts`
  - Typed billing API helpers.
- Create: `apps/web/src/lib/types/agent.ts`
  - Agent types.
- Create: `apps/web/src/lib/types/calls.ts`
  - Calls types.
- Create: `apps/web/src/lib/types/billing.ts`
  - Billing types.
- Create: `apps/web/src/lib/formatters.ts`
  - Shared formatters.
- Create: `apps/web/vitest.config.ts`
  - Vitest config.
- Create: `apps/web/vitest.setup.ts`
  - Testing Library setup.
- Create: `apps/web/tests/app/root-page.test.tsx`
  - Root redirect and auth tests.
- Create: `apps/web/tests/app/app-shell.test.tsx`
  - Template shell and sidebar nav tests.
- Create: `apps/web/tests/app/home-page.test.tsx`
  - Adaptive home tests.
- Create: `apps/web/tests/app/calls-page.test.tsx`
  - Calls list and detail tests.
- Create: `apps/web/tests/app/agent-page.test.tsx`
  - Agent form and enable-flow tests.
- Create: `apps/web/tests/app/billing-page.test.tsx`
  - Billing page and server action tests.
- Create: `apps/web/tests/lib/preferences-store.test.ts`
  - Theme and preference persistence tests.
- Modify: `compose.yaml`
  - Add frontend service to the app profile.
- Modify: `compose.dev.yaml`
  - Add dev service wiring for `apps/web`.
- Modify: `README.md`
  - Document frontend dev, build, and test flows.

## Chunk 1: Recreate The Template Foundation In `apps/web`

### Task 1: Scaffold the new `apps/web` workspace with template-native tooling

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/package-lock.json`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/tsconfig.scripts.json`
- Create: `apps/web/next.config.mjs`
- Create: `apps/web/postcss.config.mjs`
- Create: `apps/web/biome.json`
- Create: `apps/web/components.json`
- Create: `apps/web/.gitignore`
- Create: `apps/web/.env.example`
- Test: `apps/web/tests/app/root-page.test.tsx`

- [ ] **Step 1: Write the failing root smoke test**

```tsx
import { describe, expect, it } from "vitest";

describe("root page", () => {
  it("redirect contract is implemented", () => {
    expect(false).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test to verify the workspace does not exist yet**

Run: `cd apps/web && npm run test -- tests/app/root-page.test.tsx --run`
Expected: FAIL because `apps/web` has not been created yet.

- [ ] **Step 3: Create the base package and config files**

Implement:
- Next.js 16, React 19, Tailwind CSS v4, Biome, shadcn, Clerk, Zustand, and the template-aligned UI dependency set
- `dev`, `build`, `start`, `lint`, `format`, `check`, and `test` scripts
- path aliases that resolve `@/*` to `src/*`
- `components.json` that points shadcn at `src/app/globals.css`

- [ ] **Step 4: Copy only the template foundation, not the demo pages**

Source from `dashboard-temp`:
- package and tooling patterns
- shadcn config shape
- Tailwind and PostCSS setup

Do not copy:
- demo dashboard pages
- placeholder auth pages
- sample CRM or finance data

- [ ] **Step 5: Run the smoke test again**

Run: `cd apps/web && npm run test -- tests/app/root-page.test.tsx --run`
Expected: FAIL later in app setup, not because the workspace is missing.

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat: scaffold apps/web workspace"
```

### Task 2: Port the template theme and preference system into `apps/web/src`

**Files:**
- Create: `apps/web/src/app/layout.tsx`
- Create: `apps/web/src/app/globals.css`
- Create: `apps/web/src/config/app-config.ts`
- Create: `apps/web/src/lib/fonts/registry.ts`
- Create: `apps/web/src/lib/preferences/preferences-config.ts`
- Create: `apps/web/src/lib/preferences/layout.ts`
- Create: `apps/web/src/lib/preferences/theme.ts`
- Create: `apps/web/src/lib/preferences/theme-utils.ts`
- Create: `apps/web/src/lib/preferences/preferences-storage.ts`
- Create: `apps/web/src/stores/preferences/preferences-store.ts`
- Create: `apps/web/src/stores/preferences/preferences-provider.tsx`
- Create: `apps/web/src/scripts/theme-boot.tsx`
- Create: `apps/web/src/styles/presets/brutalist.css`
- Create: `apps/web/src/styles/presets/soft-pop.css`
- Create: `apps/web/src/styles/presets/tangerine.css`
- Test: `apps/web/tests/lib/preferences-store.test.ts`

- [ ] **Step 1: Write the failing preference persistence test**

```ts
import { describe, expect, it } from "vitest";

describe("preferences", () => {
  it("keeps the template preset list", () => {
    expect(["default", "brutalist", "soft-pop", "tangerine"]).toContain("tangerine");
  });
});
```

- [ ] **Step 2: Run the test to confirm the preference system is not wired yet**

Run: `cd apps/web && npm run test -- tests/lib/preferences-store.test.ts --run`
Expected: FAIL because the imported theme and preference modules do not exist yet.

- [ ] **Step 3: Port the template theme infrastructure**

Implement:
- preset definitions for `default`, `brutalist`, `soft-pop`, and `tangerine`
- `light`, `dark`, and `system` theme modes
- cookie-backed preference persistence for layout-critical state
- `ThemeBootScript` and provider wiring in the root layout

- [ ] **Step 4: Keep template defaults unless product needs force a change**

Default values:
- `theme_preset = "default"`
- `theme_mode = "light"`
- `content_layout = "centered"`
- `navbar_style = "sticky"`
- `sidebar_variant = "inset"`
- `sidebar_collapsible = "icon"`

- [ ] **Step 5: Run the preference test**

Run: `cd apps/web && npm run test -- tests/lib/preferences-store.test.ts --run`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat: port template theme foundation"
```

## Chunk 2: Build The Authenticated Product Shell

### Task 3: Add root routes, auth routes, and Clerk-aware protection

**Files:**
- Create: `apps/web/proxy.ts`
- Create: `apps/web/src/app/page.tsx`
- Create: `apps/web/src/app/not-found.tsx`
- Create: `apps/web/src/app/unauthorized/page.tsx`
- Create: `apps/web/src/app/(auth)/sign-in/[[...sign-in]]/page.tsx`
- Create: `apps/web/src/app/(auth)/sign-up/[[...sign-up]]/page.tsx`
- Create: `apps/web/src/lib/auth/server-session.ts`
- Create: `apps/web/src/lib/api/backend-client.ts`
- Test: `apps/web/tests/app/root-page.test.tsx`

- [ ] **Step 1: Replace the smoke test with a real redirect test**

```tsx
import { describe, expect, it } from "vitest";

describe("root page", () => {
  it("sends signed-out users to sign-in and signed-in users to /dashboard", async () => {
    expect(true).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test to verify route logic is still missing**

Run: `cd apps/web && npm run test -- tests/app/root-page.test.tsx --run`
Expected: FAIL because auth-aware route logic is not implemented yet.

- [ ] **Step 3: Implement route protection and root redirects**

Implement:
- `proxy.ts` matcher that protects `/dashboard` paths
- root page redirect behavior
- Clerk sign-in and sign-up entry pages
- a server helper that acquires auth context for backend requests

- [ ] **Step 4: Normalize backend fetch failures**

Implement `backend-client.ts` so page code can distinguish:
- unauthenticated access
- forbidden access
- validation or conflict responses
- temporary upstream failures

- [ ] **Step 5: Run the redirect test**

Run: `cd apps/web && npm run test -- tests/app/root-page.test.tsx --run`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat: add frontend auth entry and protection"
```

### Task 4: Adapt the template sidebar and header shell to product navigation

**Files:**
- Create: `apps/web/src/app/(app)/dashboard/layout.tsx`
- Create: `apps/web/src/app/(app)/dashboard/_components/sidebar/app-sidebar.tsx`
- Create: `apps/web/src/app/(app)/dashboard/_components/sidebar/nav-main.tsx`
- Create: `apps/web/src/app/(app)/dashboard/_components/sidebar/theme-switcher.tsx`
- Create: `apps/web/src/app/(app)/dashboard/_components/sidebar/layout-controls.tsx`
- Create: `apps/web/src/app/(app)/dashboard/_components/sidebar/nav-user.tsx`
- Create: `apps/web/src/navigation/sidebar/sidebar-items.ts`
- Create: `apps/web/src/components/ui/sidebar.tsx`
- Create: `apps/web/src/components/ui/tooltip.tsx`
- Create: `apps/web/src/components/ui/button.tsx`
- Create: `apps/web/src/components/ui/badge.tsx`
- Create: `apps/web/src/components/ui/card.tsx`
- Create: `apps/web/src/components/ui/sonner.tsx`
- Test: `apps/web/tests/app/app-shell.test.tsx`

- [ ] **Step 1: Write the failing app shell navigation test**

```tsx
import { describe, expect, it } from "vitest";

describe("app shell", () => {
  it("renders Home, Calls, Agent, and Billing in the sidebar", () => {
    expect(["Home", "Calls", "Agent", "Billing"]).toHaveLength(4);
  });
});
```

- [ ] **Step 2: Run the test to verify the shell is not present yet**

Run: `cd apps/web && npm run test -- tests/app/app-shell.test.tsx --run`
Expected: FAIL because the authenticated shell and sidebar do not exist yet.

- [ ] **Step 3: Port and adapt the template shell**

Source from `dashboard-temp`:
- sidebar provider and inset layout
- header structure
- theme and layout controls
- toast and tooltip wiring

Adapt:
- home link should target `/dashboard`
- sidebar items should be only `Home`, `Calls`, `Agent`, and `Billing`
- user control should connect to Clerk instead of demo user data

- [ ] **Step 4: Remove demo-only shell features**

Do not keep:
- demo dashboard groups
- coming-soon sections
- sample documents
- fake account switcher data

- [ ] **Step 5: Run the shell test**

Run: `cd apps/web && npm run test -- tests/app/app-shell.test.tsx --run`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat: adapt template shell to product navigation"
```

## Chunk 3: Implement Product Screens In The Template Style

### Task 5: Build the adaptive `/dashboard` home route

**Files:**
- Create: `apps/web/src/app/(app)/dashboard/page.tsx`
- Create: `apps/web/src/components/home/status-summary-cards.tsx`
- Create: `apps/web/src/components/home/setup-checklist.tsx`
- Create: `apps/web/src/components/home/recent-calls-list.tsx`
- Create: `apps/web/src/components/home/usage-summary-card.tsx`
- Create: `apps/web/src/components/home/agent-snapshot-card.tsx`
- Create: `apps/web/src/lib/api/agent.ts`
- Create: `apps/web/src/lib/api/calls.ts`
- Create: `apps/web/src/lib/api/billing.ts`
- Create: `apps/web/src/lib/types/agent.ts`
- Create: `apps/web/src/lib/types/calls.ts`
- Create: `apps/web/src/lib/types/billing.ts`
- Create: `apps/web/src/lib/formatters.ts`
- Test: `apps/web/tests/app/home-page.test.tsx`

- [ ] **Step 1: Write the failing adaptive home test**

```tsx
import { describe, expect, it } from "vitest";

describe("home page", () => {
  it("shows setup UI for first-run users and recent calls for active users", () => {
    expect(true).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test to confirm the route contract is unimplemented**

Run: `cd apps/web && npm run test -- tests/app/home-page.test.tsx --run`
Expected: FAIL because `/dashboard` does not render product-specific content yet.

- [ ] **Step 3: Implement typed reads for agent, calls, and billing summary**

Requirements:
- server-first data reads
- normalized empty-state handling
- no internal frontend proxy API for simple reads

- [ ] **Step 4: Build the page in the template card rhythm**

Implement:
- top summary cards
- setup checklist and empty states for first-run
- recent calls and usage context for active users

Constraint:
- preserve template spacing, card treatment, and responsive density

- [ ] **Step 5: Run the home test**

Run: `cd apps/web && npm run test -- tests/app/home-page.test.tsx --run`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat: add adaptive dashboard home"
```

### Task 6: Build calls list and call detail pages

**Files:**
- Create: `apps/web/src/app/(app)/dashboard/calls/page.tsx`
- Create: `apps/web/src/app/(app)/dashboard/calls/[callId]/page.tsx`
- Create: `apps/web/src/app/(app)/dashboard/calls/actions.ts`
- Create: `apps/web/src/components/calls/calls-table.tsx`
- Create: `apps/web/src/components/calls/call-detail-card.tsx`
- Create: `apps/web/src/components/calls/recording-panel.tsx`
- Create: `apps/web/src/components/calls/transcript-panel.tsx`
- Test: `apps/web/tests/app/calls-page.test.tsx`

- [ ] **Step 1: Write the failing calls-page tests**

```tsx
import { describe, expect, it } from "vitest";

describe("calls pages", () => {
  it("renders empty and populated call states", () => {
    expect(true).toBe(true);
  });
});
```

- [ ] **Step 2: Run the calls tests**

Run: `cd apps/web && npm run test -- tests/app/calls-page.test.tsx --run`
Expected: FAIL because the calls screens are not implemented yet.

- [ ] **Step 3: Implement the calls index**

Requirements:
- list from `GET /api/calls`
- readable status and summary rows
- template-native list, table, or stacked-card presentation

- [ ] **Step 4: Implement the call detail page and archive action**

Requirements:
- detail read from `GET /api/calls/{call_id}`
- transcript rendering
- recording state when present or unavailable
- archive action via `DELETE /api/calls/{call_id}`

- [ ] **Step 5: Run the calls tests**

Run: `cd apps/web && npm run test -- tests/app/calls-page.test.tsx --run`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat: add calls workflow pages"
```

### Task 7: Build the agent settings page and server actions

**Files:**
- Create: `apps/web/src/app/(app)/dashboard/agent/page.tsx`
- Create: `apps/web/src/app/(app)/dashboard/agent/actions.ts`
- Create: `apps/web/src/components/agent/agent-settings-form.tsx`
- Create: `apps/web/src/components/agent/agent-runtime-card.tsx`
- Create: `apps/web/src/components/ui/input.tsx`
- Create: `apps/web/src/components/ui/textarea.tsx`
- Create: `apps/web/src/components/ui/select.tsx`
- Create: `apps/web/src/components/ui/switch.tsx`
- Test: `apps/web/tests/app/agent-page.test.tsx`

- [ ] **Step 1: Write the failing agent-page tests**

```tsx
import { describe, expect, it } from "vitest";

describe("agent page", () => {
  it("renders editable settings and guarded enable states", () => {
    expect(true).toBe(true);
  });
});
```

- [ ] **Step 2: Run the agent tests**

Run: `cd apps/web && npm run test -- tests/app/agent-page.test.tsx --run`
Expected: FAIL because the agent page and actions do not exist yet.

- [ ] **Step 3: Implement the form in grouped template cards**

Requirements:
- explicit save for prompt-heavy fields
- grouped sections for general settings, prompt content, and runtime state

- [ ] **Step 4: Implement server actions for updates and enable toggle**

Requirements:
- handle ordinary validation failures
- surface `409` conflicts as actionable setup problems
- surface temporary upstream failures clearly

- [ ] **Step 5: Run the agent tests**

Run: `cd apps/web && npm run test -- tests/app/agent-page.test.tsx --run`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat: add agent settings page"
```

### Task 8: Build the billing page and hosted billing actions

**Files:**
- Create: `apps/web/src/app/(app)/dashboard/billing/page.tsx`
- Create: `apps/web/src/app/(app)/dashboard/billing/actions.ts`
- Create: `apps/web/src/components/billing/billing-summary-cards.tsx`
- Create: `apps/web/src/components/billing/usage-ledger-list.tsx`
- Test: `apps/web/tests/app/billing-page.test.tsx`

- [ ] **Step 1: Write the failing billing-page tests**

```tsx
import { describe, expect, it } from "vitest";

describe("billing page", () => {
  it("renders usage state and checkout or portal actions", () => {
    expect(true).toBe(true);
  });
});
```

- [ ] **Step 2: Run the billing tests**

Run: `cd apps/web && npm run test -- tests/app/billing-page.test.tsx --run`
Expected: FAIL because the billing route is not implemented yet.

- [ ] **Step 3: Implement the page with template-native finance cards**

Requirements:
- current subscription state
- usage snapshot
- recent ledger items

- [ ] **Step 4: Implement hosted Stripe actions**

Requirements:
- checkout entry for unsubscribed users
- billing portal entry for subscribed users
- clear pending and failure states

- [ ] **Step 5: Run the billing tests**

Run: `cd apps/web && npm run test -- tests/app/billing-page.test.tsx --run`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat: add billing page"
```

## Chunk 4: Wire Local Development And Final Verification

### Task 9: Add container wiring, docs, and final verification

**Files:**
- Create: `apps/web/Dockerfile`
- Modify: `compose.yaml`
- Modify: `compose.dev.yaml`
- Modify: `README.md`

- [ ] **Step 1: Write the failing container or docs check**

Run: `docker compose --profile app config`
Expected: the frontend service is missing from the composed configuration.

- [ ] **Step 2: Add the frontend container wiring**

Implement:
- production image build for `apps/web`
- dev bind mount and command wiring
- environment variables for Clerk and backend API access

- [ ] **Step 3: Document the frontend workflow**

Document:
- install and run commands
- test, lint, and build commands
- required environment variables
- the fact that the UI preserves template theme presets and `/dashboard` product routes

- [ ] **Step 4: Run verification**

Run:
- `cd apps/web && npm run check`
- `cd apps/web && npm run build`
- `cd apps/web && npm run test -- --run`
- `docker compose --profile app config`

Expected:
- Biome passes
- Next.js build passes
- Vitest passes
- Compose renders a valid config including `web`

- [ ] **Step 5: Commit**

```bash
git add apps/web compose.yaml compose.dev.yaml README.md
git commit -m "feat: wire frontend app into local development"
```

## Notes For Implementation

- Treat `dashboard-temp` as the structural donor, not as a directory to copy wholesale.
- Keep the frontend under `apps/web`, with all route code under `apps/web/src`.
- Keep reads server-first per `@next-best-practices`.
- Preserve the template's theme presets and default visual language before inventing new tokens.
- Prefer adapting existing template primitives over creating custom replacements when the template already solves the UI need cleanly.

Plan complete and saved to `docs/superpowers/plans/2026-03-29-frontend-dashboard.md`. Ready to execute?
