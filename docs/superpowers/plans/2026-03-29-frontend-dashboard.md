# Frontend Dashboard Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first customer-facing frontend as a Next.js dashboard app that helps users get their AI phone agent live and then operate it through calls, agent settings, and billing screens.

**Architecture:** Add a new isolated `apps/web` Next.js App Router app rather than introducing a JS workspace at the repo root. Use Clerk-protected app routes, server-first reads against the existing backend REST APIs, and server actions for frontend-originated mutations such as agent config updates, call archive, and hosted billing redirects. Apply `@frontend-design`, `@next-best-practices`, `@vercel-react-best-practices`, `@adapt`, and `@harden` during implementation so the app stays distinctive, server-first, responsive, and resilient.

**Tech Stack:** Next.js App Router, TypeScript, Tailwind CSS, Clerk, Vitest, React Testing Library, Docker Compose

---

## File Map

- Create: `apps/web/package.json`
  - Define Next.js, Clerk, Tailwind, Vitest, and lint/test/build scripts.
- Create: `apps/web/package-lock.json`
  - Lock frontend dependencies after install.
- Create: `apps/web/tsconfig.json`
  - Configure TypeScript with `@/*` imports.
- Create: `apps/web/next.config.ts`
  - Enable standalone output and any required image/runtime settings.
- Create: `apps/web/postcss.config.mjs`
  - Configure Tailwind pipeline.
- Create: `apps/web/eslint.config.mjs`
  - Configure linting for Next.js and TypeScript.
- Create: `apps/web/.gitignore`
  - Ignore `.next`, `node_modules`, and local env files.
- Create: `apps/web/.env.example`
  - Document Clerk and backend API variables.
- Create: `apps/web/Dockerfile`
  - Build the standalone Next.js app for Compose.
- Create: `apps/web/proxy.ts`
  - Protect `/app` routes with Clerk in the Next.js 16+ file convention.
- Create: `apps/web/app/layout.tsx`
  - Root layout, fonts, Clerk provider, and metadata shell.
- Create: `apps/web/app/page.tsx`
  - Redirect signed-in users to `/app` and signed-out users to sign-in.
- Create: `apps/web/app/globals.css`
  - Define tokens, typography, and global styles for the calm/premium visual direction.
- Create: `apps/web/app/global-error.tsx`
  - Global crash boundary.
- Create: `apps/web/app/not-found.tsx`
  - Shared not-found screen.
- Create: `apps/web/app/unauthorized.tsx`
  - Unauthorized auth-state UI.
- Create: `apps/web/app/forbidden.tsx`
  - Forbidden access-state UI.
- Create: `apps/web/app/(auth)/sign-in/[[...sign-in]]/page.tsx`
  - Clerk sign-in entry.
- Create: `apps/web/app/(auth)/sign-up/[[...sign-up]]/page.tsx`
  - Clerk sign-up entry.
- Create: `apps/web/app/(app)/app/layout.tsx`
  - Protected app shell with primary navigation.
- Create: `apps/web/app/(app)/app/loading.tsx`
  - Route-level loading UI for the app shell.
- Create: `apps/web/app/(app)/app/error.tsx`
  - Route-level error UI for app routes.
- Create: `apps/web/app/(app)/app/page.tsx`
  - Adaptive home route for first-run and active users.
- Create: `apps/web/app/(app)/app/agent/page.tsx`
  - Agent configuration screen.
- Create: `apps/web/app/(app)/app/agent/actions.ts`
  - Server actions for agent config mutation.
- Create: `apps/web/app/(app)/app/calls/page.tsx`
  - Calls index screen.
- Create: `apps/web/app/(app)/app/calls/[callId]/page.tsx`
  - Call detail screen.
- Create: `apps/web/app/(app)/app/calls/[callId]/loading.tsx`
  - Loading state for call detail.
- Create: `apps/web/app/(app)/app/calls/actions.ts`
  - Server action for call archive.
- Create: `apps/web/app/(app)/app/billing/page.tsx`
  - Billing and usage screen.
- Create: `apps/web/app/(app)/app/billing/actions.ts`
  - Server actions for checkout and billing portal redirects.
- Create: `apps/web/components/app-shell.tsx`
  - Shared authenticated app frame.
- Create: `apps/web/components/nav/app-nav.tsx`
  - Desktop/mobile navigation.
- Create: `apps/web/components/home/status-hero.tsx`
  - Home hero with readiness and live-state messaging.
- Create: `apps/web/components/home/setup-checklist.tsx`
  - First-run activation sequence.
- Create: `apps/web/components/home/recent-calls-panel.tsx`
  - Home recent-calls panel for active users.
- Create: `apps/web/components/home/usage-summary-card.tsx`
  - Compact usage/subscription card.
- Create: `apps/web/components/agent/agent-config-form.tsx`
  - Editable agent config form with guarded enable toggle.
- Create: `apps/web/components/calls/calls-list.tsx`
  - Calls list component.
- Create: `apps/web/components/calls/call-detail-view.tsx`
  - Transcript and recording presentation.
- Create: `apps/web/components/billing/billing-summary.tsx`
  - Subscription and usage summary UI.
- Create: `apps/web/components/billing/usage-ledger-list.tsx`
  - Billing ledger UI.
- Create: `apps/web/components/ui/button.tsx`
  - Shared button primitive.
- Create: `apps/web/components/ui/empty-state.tsx`
  - Shared empty-state primitive.
- Create: `apps/web/components/ui/status-pill.tsx`
  - Shared live-state/status primitive.
- Create: `apps/web/lib/auth/server-session.ts`
  - Clerk server-session helpers and backend bearer token bridge.
- Create: `apps/web/lib/api/backend-client.ts`
  - Shared backend fetch wrapper with error normalization.
- Create: `apps/web/lib/api/agent.ts`
  - Typed agent config reads and writes.
- Create: `apps/web/lib/api/calls.ts`
  - Typed call list, call detail, and archive helpers.
- Create: `apps/web/lib/api/billing.ts`
  - Typed billing reads and hosted-session helpers.
- Create: `apps/web/lib/types/agent.ts`
  - Agent config and mutation result types.
- Create: `apps/web/lib/types/calls.ts`
  - Calls list/detail types.
- Create: `apps/web/lib/types/billing.ts`
  - Billing and usage types.
- Create: `apps/web/lib/formatters.ts`
  - Shared date, duration, and number formatters.
- Create: `apps/web/vitest.config.ts`
  - Vitest config for app and lib tests.
- Create: `apps/web/vitest.setup.ts`
  - Testing Library and DOM setup.
- Create: `apps/web/tests/lib/backend-client.test.ts`
  - Backend client and auth bridge tests.
- Create: `apps/web/tests/app/root-page.test.tsx`
  - Root redirect/auth shell tests.
- Create: `apps/web/tests/app/home-page.test.tsx`
  - Adaptive home-state tests.
- Create: `apps/web/tests/app/agent-page.test.tsx`
  - Agent form and enable-toggle tests.
- Create: `apps/web/tests/app/calls-page.test.tsx`
  - Calls list/detail and archive tests.
- Create: `apps/web/tests/app/billing-page.test.tsx`
  - Billing screen and hosted-session action tests.
- Modify: `compose.yaml`
  - Add a `web` service to the app profile.
- Modify: `compose.dev.yaml`
  - Add bind-mounted web dev service.
- Modify: `README.md`
  - Document frontend app commands and local dev flow.

## Chunk 1: Frontend Workspace Foundation

### Task 1: Create the isolated `apps/web` Next.js workspace

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/next.config.ts`
- Create: `apps/web/postcss.config.mjs`
- Create: `apps/web/eslint.config.mjs`
- Create: `apps/web/.gitignore`
- Create: `apps/web/vitest.config.ts`
- Create: `apps/web/vitest.setup.ts`
- Test: `apps/web/tests/app/root-page.test.tsx`

- [ ] **Step 1: Write the failing root-page smoke test**

```tsx
import { describe, expect, it } from "vitest";

describe("root page", () => {
  it("redirects unauthenticated users to sign-in", async () => {
    expect(true).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npm run test -- tests/app/root-page.test.tsx --run`
Expected: FAIL because the Next.js workspace and test harness do not exist yet.

- [ ] **Step 3: Create the minimal workspace and test harness**

Implement:
- `package.json` with `dev`, `build`, `start`, `lint`, and `test` scripts
- Next.js App Router dependency set
- Vitest + Testing Library config
- `next.config.ts` with `output: "standalone"`
- Tailwind and ESLint base config

- [ ] **Step 4: Add the minimal root app files needed for the smoke test**

Implement:
- `app/layout.tsx`
- `app/page.tsx`
- `app/globals.css`

Keep `page.tsx` minimal for now so the test harness can exercise the root route contract.

- [ ] **Step 5: Run test to verify the workspace boots**

Run: `cd apps/web && npm run test -- tests/app/root-page.test.tsx --run`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat: scaffold frontend workspace"
```

### Task 2: Add auth gating, error boundaries, and the authenticated shell

**Files:**
- Create: `apps/web/proxy.ts`
- Create: `apps/web/app/global-error.tsx`
- Create: `apps/web/app/not-found.tsx`
- Create: `apps/web/app/unauthorized.tsx`
- Create: `apps/web/app/forbidden.tsx`
- Create: `apps/web/app/(auth)/sign-in/[[...sign-in]]/page.tsx`
- Create: `apps/web/app/(auth)/sign-up/[[...sign-up]]/page.tsx`
- Create: `apps/web/app/(app)/app/layout.tsx`
- Create: `apps/web/components/app-shell.tsx`
- Create: `apps/web/components/nav/app-nav.tsx`
- Test: `apps/web/tests/app/root-page.test.tsx`

- [ ] **Step 1: Expand the failing auth-shell tests**

```tsx
it("protects /app through proxy routing", async () => {
  expect(proxy).toBeDefined();
});

it("renders app navigation links for authenticated users", async () => {
  expect(screen.getByRole("link", { name: "Calls" })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npm run test -- tests/app/root-page.test.tsx --run`
Expected: FAIL because `proxy.ts`, auth pages, and the app shell do not exist.

- [ ] **Step 3: Implement the minimal auth shell**

Implement:
- `proxy.ts` using the Next.js 16+ convention for `/app/:path*`
- root error and not-found boundaries
- Clerk sign-in/sign-up routes
- authenticated `/app` layout with stable nav for `Home`, `Calls`, `Agent`, `Billing`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npm run test -- tests/app/root-page.test.tsx --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/proxy.ts apps/web/app apps/web/components/app-shell.tsx apps/web/components/nav/app-nav.tsx apps/web/tests/app/root-page.test.tsx
git commit -m "feat: add frontend auth shell"
```

## Chunk 2: Backend Client And Shared Domain Layer

### Task 3: Build Clerk-backed backend client helpers

**Files:**
- Create: `apps/web/lib/auth/server-session.ts`
- Create: `apps/web/lib/api/backend-client.ts`
- Create: `apps/web/lib/formatters.ts`
- Test: `apps/web/tests/lib/backend-client.test.ts`

- [ ] **Step 1: Write the failing backend client tests**

```tsx
import { describe, expect, it } from "vitest";

describe("backend client", () => {
  it("adds the Clerk bearer token to authenticated requests", async () => {
    expect(true).toBe(false);
  });

  it("normalizes backend errors into UI-safe messages", async () => {
    expect(true).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npm run test -- tests/lib/backend-client.test.ts --run`
Expected: FAIL because the server-session helper and backend client do not exist.

- [ ] **Step 3: Implement the minimal auth bridge and backend fetch wrapper**

Implement:
- server helper that reads the authenticated Clerk session
- backend fetch wrapper that adds `Authorization: Bearer <token>`
- normalized error object for `401`, `404`, `409`, `422`, and `502`
- shared formatting helpers for dates, durations, and minute balances

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npm run test -- tests/lib/backend-client.test.ts --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/lib/auth/server-session.ts apps/web/lib/api/backend-client.ts apps/web/lib/formatters.ts apps/web/tests/lib/backend-client.test.ts
git commit -m "feat: add frontend backend client"
```

### Task 4: Add typed API modules for agent, calls, and billing

**Files:**
- Create: `apps/web/lib/api/agent.ts`
- Create: `apps/web/lib/api/calls.ts`
- Create: `apps/web/lib/api/billing.ts`
- Create: `apps/web/lib/types/agent.ts`
- Create: `apps/web/lib/types/calls.ts`
- Create: `apps/web/lib/types/billing.ts`
- Test: `apps/web/tests/lib/backend-client.test.ts`

- [ ] **Step 1: Expand the failing API-module tests**

```tsx
it("maps agent config responses into typed frontend models", async () => {
  expect(true).toBe(false);
});

it("maps call detail responses including null recording_url", async () => {
  expect(true).toBe(false);
});

it("maps zeroed billing usage snapshots for unsubscribed users", async () => {
  expect(true).toBe(false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npm run test -- tests/lib/backend-client.test.ts --run`
Expected: FAIL because the typed API wrappers and domain models are incomplete.

- [ ] **Step 3: Implement the typed read/write modules**

Implement:
- `agent.ts` for `GET /api/agent/config` and `PATCH /api/agent/config`
- `calls.ts` for `GET /api/calls`, `GET /api/calls/{callId}`, and `DELETE /api/calls/{callId}`
- `billing.ts` for subscription, usage, ledger, checkout, and portal actions
- route-friendly types that preserve backend behavior such as nullable recording URLs and zero-state billing

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npm run test -- tests/lib/backend-client.test.ts --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/lib/api apps/web/lib/types apps/web/tests/lib/backend-client.test.ts
git commit -m "feat: add typed frontend api modules"
```

## Chunk 3: Adaptive Home And Agent Configuration

### Task 5: Implement the adaptive home route

**Files:**
- Create: `apps/web/app/(app)/app/page.tsx`
- Create: `apps/web/app/(app)/app/loading.tsx`
- Create: `apps/web/components/home/status-hero.tsx`
- Create: `apps/web/components/home/setup-checklist.tsx`
- Create: `apps/web/components/home/recent-calls-panel.tsx`
- Create: `apps/web/components/home/usage-summary-card.tsx`
- Create: `apps/web/components/ui/status-pill.tsx`
- Create: `apps/web/components/ui/empty-state.tsx`
- Test: `apps/web/tests/app/home-page.test.tsx`

- [ ] **Step 1: Write the failing adaptive-home tests**

```tsx
it("shows the setup checklist when the agent is not yet live", async () => {
  expect(screen.getByText("Enable the agent")).toBeInTheDocument();
});

it("shows recent calls and usage summary for active users", async () => {
  expect(screen.getByText("Recent calls")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npm run test -- tests/app/home-page.test.tsx --run`
Expected: FAIL because the home route and home modules do not exist.

- [ ] **Step 3: Implement the minimal adaptive home**

Implement:
- server-first reads for agent config, recent calls, and usage snapshot in parallel
- setup-dominant home state when `is_enabled` is false or config is incomplete
- operations-dominant home state when the agent is active
- calm, premium layout tokens in `globals.css` that support both desktop and mobile widths

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npm run test -- tests/app/home-page.test.tsx --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/app/(app)/app/page.tsx apps/web/app/(app)/app/loading.tsx apps/web/components/home apps/web/components/ui/status-pill.tsx apps/web/components/ui/empty-state.tsx apps/web/tests/app/home-page.test.tsx apps/web/app/globals.css
git commit -m "feat: add adaptive frontend home"
```

### Task 6: Implement the agent configuration screen and guarded enable flow

**Files:**
- Create: `apps/web/app/(app)/app/agent/page.tsx`
- Create: `apps/web/app/(app)/app/agent/actions.ts`
- Create: `apps/web/components/agent/agent-config-form.tsx`
- Create: `apps/web/components/ui/button.tsx`
- Test: `apps/web/tests/app/agent-page.test.tsx`

- [ ] **Step 1: Write the failing agent-page tests**

```tsx
it("hydrates the current agent config values", async () => {
  expect(screen.getByDisplayValue("Ava")).toBeInTheDocument();
});

it("surfaces a precise message when enabling fails with 409", async () => {
  expect(screen.getByText("Phone number not found")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npm run test -- tests/app/agent-page.test.tsx --run`
Expected: FAIL because the page, server action, and form do not exist.

- [ ] **Step 3: Implement the minimal agent settings flow**

Implement:
- server-read of current config
- explicit save for prompt-heavy fields
- guarded pending state for `is_enabled`
- user-visible handling for `409`, `422`, and `502`
- revalidation of `/app` and `/app/agent` after successful mutation

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npm run test -- tests/app/agent-page.test.tsx --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/app/(app)/app/agent apps/web/components/agent/agent-config-form.tsx apps/web/components/ui/button.tsx apps/web/tests/app/agent-page.test.tsx
git commit -m "feat: add agent configuration screen"
```

## Chunk 4: Calls And Billing Surfaces

### Task 7: Implement call history, call detail, and archive flow

**Files:**
- Create: `apps/web/app/(app)/app/calls/page.tsx`
- Create: `apps/web/app/(app)/app/calls/[callId]/page.tsx`
- Create: `apps/web/app/(app)/app/calls/[callId]/loading.tsx`
- Create: `apps/web/app/(app)/app/calls/actions.ts`
- Create: `apps/web/components/calls/calls-list.tsx`
- Create: `apps/web/components/calls/call-detail-view.tsx`
- Test: `apps/web/tests/app/calls-page.test.tsx`

- [ ] **Step 1: Write the failing calls-page tests**

```tsx
it("renders the call list newest first with summary text", async () => {
  expect(screen.getByText("Caller asked about opening hours.")).toBeInTheDocument();
});

it("renders an expired-recording state when recording_url is null", async () => {
  expect(screen.getByText("Recording no longer available")).toBeInTheDocument();
});

it("archives a call and removes it from the list on refresh", async () => {
  expect(true).toBe(false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npm run test -- tests/app/calls-page.test.tsx --run`
Expected: FAIL because the calls routes, archive action, and UI components do not exist.

- [ ] **Step 3: Implement the minimal calls experience**

Implement:
- calls index using server-first reads
- call detail route with transcript and recording state
- archive server action wired to `DELETE /api/calls/{callId}`
- empty state teaching users what will appear after calls arrive

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npm run test -- tests/app/calls-page.test.tsx --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/app/(app)/app/calls apps/web/components/calls apps/web/tests/app/calls-page.test.tsx
git commit -m "feat: add call history screens"
```

### Task 8: Implement billing usage and hosted Stripe actions

**Files:**
- Create: `apps/web/app/(app)/app/billing/page.tsx`
- Create: `apps/web/app/(app)/app/billing/actions.ts`
- Create: `apps/web/components/billing/billing-summary.tsx`
- Create: `apps/web/components/billing/usage-ledger-list.tsx`
- Test: `apps/web/tests/app/billing-page.test.tsx`

- [ ] **Step 1: Write the failing billing-page tests**

```tsx
it("renders a valid zero-state billing snapshot for new users", async () => {
  expect(screen.getByText("0 minutes remaining")).toBeInTheDocument();
});

it("redirects to hosted checkout when the user starts a plan", async () => {
  expect(true).toBe(false);
});

it("redirects to hosted billing portal for subscribed users", async () => {
  expect(true).toBe(false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npm run test -- tests/app/billing-page.test.tsx --run`
Expected: FAIL because the billing route, server actions, and billing components do not exist.

- [ ] **Step 3: Implement the minimal billing surface**

Implement:
- subscription and usage snapshot read
- recent usage ledger list
- server actions that call checkout and portal APIs then redirect to the returned hosted URL
- secondary placement in the UI so billing supports the product without overpowering setup and operations

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npm run test -- tests/app/billing-page.test.tsx --run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/app/(app)/app/billing apps/web/components/billing apps/web/tests/app/billing-page.test.tsx
git commit -m "feat: add billing dashboard screen"
```

## Chunk 5: Runtime Wiring, Docs, And Verification

### Task 9: Add environment, Docker, and Compose wiring

**Files:**
- Create: `apps/web/.env.example`
- Create: `apps/web/Dockerfile`
- Modify: `compose.yaml`
- Modify: `compose.dev.yaml`
- Modify: `README.md`

- [ ] **Step 1: Write the failing build/dev assumptions down in docs comments or TODO assertions**

```md
- frontend container must reach the API service by service name in Compose
- frontend local dev must expose port 3000
- Clerk and backend base URL variables must be documented
```

- [ ] **Step 2: Run a frontend build to confirm the runtime wiring is still missing**

Run: `cd apps/web && npm run build`
Expected: FAIL or remain blocked until env docs, Dockerfile, and Compose wiring are added.

- [ ] **Step 3: Implement minimal runtime wiring**

Implement:
- `.env.example` for Clerk and backend URL config
- `Dockerfile` for standalone Next.js build
- `compose.yaml` `web` service on port `3000`
- `compose.dev.yaml` bind-mounted frontend dev command
- `README.md` frontend setup and run commands

- [ ] **Step 4: Run build to verify it passes**

Run: `cd apps/web && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/.env.example apps/web/Dockerfile compose.yaml compose.dev.yaml README.md
git commit -m "feat: wire frontend runtime"
```

### Task 10: Final verification and readiness pass

**Files:**
- Modify: `apps/web/app/globals.css`
- Modify: `apps/web/components/**/*`
- Modify: `apps/web/tests/**/*`

- [ ] **Step 1: Run the full frontend verification suite**

Run: `cd apps/web && npm run lint && npm run test -- --run && npm run build`
Expected: PASS

- [ ] **Step 2: Manually verify responsive behavior in the local dev app**

Run: `docker compose -f compose.yaml -f compose.dev.yaml --profile app up web api worker agent`
Expected: the frontend loads on `http://localhost:3000`, app routes authenticate correctly, and the primary screens remain usable on mobile-width browser emulation.

- [ ] **Step 3: Apply final polish and hardening fixes only if verification reveals real issues**

Focus:
- responsive overflow and touch targets
- empty states and error copy
- loading-state polish
- any hydration or server/client boundary issues

- [ ] **Step 4: Re-run the full suite after fixes**

Run: `cd apps/web && npm run lint && npm run test -- --run && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web
git commit -m "feat: finish frontend dashboard"
```

## Execution Notes

- Keep reads server-first per `@next-best-practices`; do not build an internal proxy API for simple page reads.
- Use parallel data loading with `Promise.all` where the home route needs agent, calls, and billing data together.
- Keep route-level error files in place from the start; do not push all failure handling into inline toasts.
- Treat `is_enabled` as a consequential remote state change, not a cosmetic toggle.
- Do not hide important product functionality on mobile; adapt the layout instead.
- Keep the visual system opinionated and cohesive rather than falling back to default SaaS card grids.
