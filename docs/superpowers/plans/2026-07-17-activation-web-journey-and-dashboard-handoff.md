# Activation Web Journey and Dashboard Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Presvo's complete five-milestone self-service activation journey in the Next.js app, enable the deterministic local journey, and hand active customers into a dashboard with obvious answering state, structured call outcomes, playable original audio, and dependable deletion.

**Architecture:** The FastAPI activation snapshot remains the only workflow authority. Next.js Server Components read it, authenticated Server Actions issue explicit commands, and small Client Components handle autosave, pending transitions, clipboard behavior, countdowns, and server refreshes. The dashboard consumes a bounded structured-summary projection; call deletion removes active storage and customer content before hiding the row.

**Tech Stack:** Next.js 16.2, React 19.2, TypeScript 5.9, React Hook Form 7, Zod 4, Tailwind CSS 4, shadcn/ui Radix Vega, Vitest 3, Testing Library 16, Playwright, FastAPI, Pydantic 2, SQLAlchemy 2, MinIO/S3

## Global Constraints

- Complete Plans 1-3 before executing this plan; their API contracts and state transitions are prerequisites.
- Product market is France. Product copy and receptionist behavior remain English. No launch UI may describe an Irish number or use `+353` examples.
- The web app never derives activation state from local form state, query parameters, Stripe redirects, or elapsed client time. It renders the latest canonical snapshot.
- Reads happen in Server Components. Mutations happen through Server Actions that authenticate on every invocation and call the authenticated backend client.
- Client Components receive only the serializable fields they use. All timestamps cross the RSC boundary as ISO strings.
- Local identity uses a server-only fixed token. It is never exposed through a `NEXT_PUBLIC_*` variable, browser bundle, HTML, action result, log, or activation event.
- Production startup rejects `AUTH_MODE=local`; production always uses Clerk, Stripe, Telnyx, and the real routing path.
- The activation route has one visually dominant action per state, a compact milestone navigator, persistent save feedback, keyboard support, visible focus, screen-reader labels, reduced-motion support, mobile layout, and room for 20 percent copy expansion.
- Use the existing shadcn source components before adding anything. Forms use `FieldGroup`/`Field`; callouts use `Alert`; destructive confirmation uses `AlertDialog`; loading buttons compose `Spinner`; semantic tokens replace raw status colors.
- Do not import a full template. Use [shadcn Blocks](https://ui.shadcn.com/blocks) and [Tailwind Plus Application UI](https://tailwindcss.com/plus/ui-blocks/application-ui) only as layout references. Tailwind Plus is optional/paid and must not become a dependency.
- The visual direction is calm, refined, dependable, and quietly warm. Avoid provider jargon, playful AI imagery, nested card grids, and dashboard-like density inside activation.
- Normal call audio remains available until deletion. User-triggered deletion must remove the recording object and customer-content projection before the call disappears; the UI must not promise erasure from backups.
- Remove the existing local MinIO 30-day expiration rule. Automatic retention remains deferred and must not silently delete audio in this slice.
- `ACTIVATION_FLOW_ENABLED` is enabled in local development only after the complete journey and browser test pass. No deployment or cloud resource change is part of this plan.
- Follow TDD: write a focused failing test, observe the expected failure, add the minimum implementation, rerun the focused test, then commit.

---

### Task 1: Add guarded web auth modes and an authenticated backend boundary

**Files:**
- Create: `apps/web/src/lib/auth/auth-mode.ts`
- Create: `apps/web/tests/lib/auth-mode.test.ts`
- Create: `apps/web/tests/lib/server-session.test.ts`
- Modify: `apps/web/src/lib/auth/clerk-config.ts`
- Modify: `apps/web/src/lib/auth/server-session.ts`
- Modify: `apps/web/src/lib/api/backend-client.ts`
- Modify: `apps/web/src/app/layout.tsx`
- Modify: `apps/web/src/app/(auth)/sign-in/[[...sign-in]]/page.tsx`
- Modify: `apps/web/src/app/(auth)/sign-up/[[...sign-up]]/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/layout.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/agent/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/billing/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/calls/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/calls/[callId]/page.tsx`
- Modify: `apps/web/src/proxy.ts`
- Modify: `apps/web/tests/lib/backend-client.test.ts`
- Modify: `apps/web/tests/app/app-shell.test.tsx`

**Interfaces:**
- Produces: `WebAuthMode`, `resolveWebAuthMode`, `requireProductionWebAuth`, `isAppAuthConfigured`, and `shouldWrapClerk`.
- Updates: `getServerSessionState()` to support guarded Clerk and local server identities.
- Updates: `BackendApiError` to preserve safe structured backend error details.

- [ ] **Step 1: Write failing auth-mode and local-session tests**

```ts
it("accepts local auth only in development", () => {
  expect(resolveWebAuthMode({ nodeEnv: "development", authMode: "local" })).toBe("local")
  expect(() => requireProductionWebAuth({ nodeEnv: "production", authMode: "local" }))
    .toThrow(/AUTH_MODE=local/)
})

it("returns the fixed local session only on the server", async () => {
  vi.stubEnv("NODE_ENV", "development")
  vi.stubEnv("AUTH_MODE", "local")
  vi.stubEnv("LOCAL_AUTH_TOKEN", "presvo-local-development-token")
  const session = await getServerSessionState()
  expect(session.isAuthenticated).toBe(true)
  expect(session.userId).toBe("local_presvo_user")
  expect(await session.getToken()).toBe("presvo-local-development-token")
})
```

Add cases for blank/unknown mode, local mode without a token, Clerk mode without
keys, production Clerk requirements, and a source scan proving
`LOCAL_AUTH_TOKEN` is never prefixed with `NEXT_PUBLIC_`. Add page tests proving
local mode renders protected dashboard content and redirects `/sign-in` and
`/sign-up` to `/activate` instead of showing Clerk setup notices.

- [ ] **Step 2: Run focused tests and observe the absent auth-mode module**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/lib/auth-mode.test.ts tests/lib/server-session.test.ts
```

Expected: FAIL because `auth-mode.ts` and local session selection do not exist.

- [ ] **Step 3: Implement fail-closed server-selected auth mode**

```ts
export type WebAuthMode = "clerk" | "local"

export function resolveWebAuthMode(input: { nodeEnv?: string; authMode?: string }): WebAuthMode {
  const mode = input.authMode?.trim() || "clerk"
  if (mode !== "clerk" && mode !== "local") throw new Error("Unsupported AUTH_MODE")
  if (mode === "local" && input.nodeEnv !== "development") {
    throw new Error("AUTH_MODE=local is development-only")
  }
  return mode
}
```

`requireProductionWebAuth` must require Clerk keys and the backend URL in
production and reject local mode unconditionally. Export `authMode`,
`isAppAuthConfigured`, and `shouldWrapClerk`; keep the local token out of this
module so `proxy.ts` cannot accidentally serialize it.

- [ ] **Step 4: Select the local session only inside the server session module**

In local development, require a nonblank `LOCAL_AUTH_TOKEN` and return the fixed
external identity `local_presvo_user`. In Clerk mode, keep the dynamic Clerk
import and current behavior. Add `requireServerSession()` for Server Actions:

```ts
export async function requireServerSession() {
  const session = await getServerSessionState()
  const token = session.isAuthenticated ? await session.getToken() : null
  if (!token) throw new ServerSessionRequiredError()
  return { userId: session.userId, token }
}
```

Define `ServerSessionRequiredError` in `server-session.ts`; do not import an
error class from `backend-client.ts`, because that client already imports the
session helper and the reverse import would create a cycle.

`backendFetch` must call this helper for every request. Change
`BackendApiError.detail` from a string-only assumption to a safe
`string | { code?: string; [key: string]: unknown }`, while its message uses
the code or HTTP status and never stringifies arbitrary provider payloads.

- [ ] **Step 5: Protect `/activate` and `/dashboard` in Clerk mode**

Extend the route matcher to `createRouteMatcher(["/activate(.*)",
"/dashboard(.*)"])`. Clerk mode uses `auth.protect()`. Local development lets
requests reach Server Components, where every data read and action still uses
the fixed authenticated backend credential. Root layout wraps Clerk only when
`shouldWrapClerk` is true.

Replace Clerk-key presence gates in every dashboard page/layout with
`isAppAuthConfigured` so local mode can render the real product. In local mode,
the public sign-in and sign-up routes redirect to `/activate`; they must not try
to render Clerk components. Clerk mode preserves current auth pages.

- [ ] **Step 6: Run auth, backend-client, and shell regressions**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/lib/auth-mode.test.ts tests/lib/server-session.test.ts \
  tests/lib/backend-client.test.ts tests/app/app-shell.test.tsx
```

Expected: PASS; local mode works only in development and production remains
Clerk-only.

- [ ] **Step 7: Commit the web auth boundary**

```bash
git add apps/web/src/lib/auth apps/web/src/lib/api/backend-client.ts \
  apps/web/src/app apps/web/src/proxy.ts apps/web/tests
git commit -m "feat: add guarded local web identity"
```

---

### Task 2: Define activation web contracts and authenticated Server Actions

**Files:**
- Create: `apps/web/src/lib/types/activation.ts`
- Create: `apps/web/src/lib/api/activation.ts`
- Create: `apps/web/src/lib/development/capabilities.ts`
- Create: `apps/web/src/app/(activation)/activate/actions.ts`
- Create: `apps/web/tests/lib/activation-api.test.ts`
- Create: `apps/web/tests/app/activation-actions.test.ts`

**Interfaces:**
- Produces: `ActivationSnapshot`, `BusinessProfileDraft`, `ActivationStage`, `ForwardingGuide`, and `ActivationActionResult`.
- Produces: typed query/command functions for every approved activation endpoint.
- Produces: authenticated Server Actions for autosave, lookup, confirmation, billing, provisioning, verification, simulation, and go-live.

- [ ] **Step 1: Write failing API-client and action-security tests**

```ts
it("sends explicit provisioning consent to its dedicated command", async () => {
  await confirmProvisioning()
  expect(fetchMock).toHaveBeenCalledWith(
    "http://localhost:8000/api/activation/confirm-provisioning",
    expect.objectContaining({ method: "POST" }),
  )
})

it("authenticates and rejects malformed profile action input before mutation", async () => {
  const result = await saveBusinessProfileAction({ owner_name: [] })
  expect(result.status).toBe("error")
  expect(fetchMock).not.toHaveBeenCalled()
})
```

Add tests for every endpoint, structured `409`/`422`/`503` mapping, local
development actions being unavailable in production, and exact path
revalidation only after successful commands.

- [ ] **Step 2: Run the focused tests and observe missing contracts/actions**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/lib/activation-api.test.ts tests/app/activation-actions.test.ts
```

Expected: FAIL with missing activation modules.

- [ ] **Step 3: Mirror the bounded canonical snapshot as serializable types**

Define the exact stage union:

```ts
export type ActivationStage =
  | "profile_required"
  | "payment_required"
  | "provisioning_consent_required"
  | "provisioning"
  | "provisioning_failed"
  | "forwarding_required"
  | "verification_window_open"
  | "ready_to_activate"
  | "activating"
  | "runtime_paused"
  | "active"
```

`ActivationSnapshot` must mirror `workflow_version`, `stage`,
`completed_milestones`, `next_action`, stable blockers/warnings, profile,
`profile_constraints`, billing, number, forwarding, verification,
`runtime_readiness`, activation timestamps, and `evaluated_at`. Do not expose
provider error strings or invent browser-only workflow fields.

- [ ] **Step 4: Add one API function per explicit command**

```ts
export const getActivationSnapshot = () =>
  backendFetch<ActivationSnapshot>("/api/activation")

export const saveBusinessProfile = (draft: BusinessProfileDraft) =>
  backendFetch<BusinessProfile>("/api/business-profile", {
    method: "PUT",
    body: JSON.stringify(draft),
  })

export const openVerificationWindow = () =>
  backendFetch<ActivationSnapshot>("/api/activation/open-verification-window", { method: "POST" })
```

Add lookup, confirm-profile, confirm-provisioning, retry-provisioning, go-live,
development starter activation, and development forwarded-call simulation.

- [ ] **Step 5: Implement authenticated, validated Server Actions**

Every action must parse an allowed-key Zod transport schema, call
`requireServerSession()` inside the action, then call the typed API function.
The API remains the authority for product bounds; the browser receives the
authoritative `profile_constraints` for counters and `maxLength` attributes.

```ts
export type ActivationActionResult<T = ActivationSnapshot> =
  | { status: "success"; data: T; message: string }
  | { status: "error"; code: string; message: string; fields?: string[] }
```

Map stable backend codes to plain customer copy in one function. Revalidate
`/activate` and `/dashboard` only after successful mutations. Do not put
`redirect()` inside a caught block.

- [ ] **Step 6: Expose boolean local capabilities without exposing secrets**

`getDevelopmentCapabilities()` is server-only and returns:

```ts
type DevelopmentCapabilities = {
  localBilling: boolean
  localVerification: boolean
}
```

Both values require `NODE_ENV=development`, local auth, and the matching fake
provider mode. Never return mode credentials or token material.

- [ ] **Step 7: Run focused tests and commit the command boundary**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/lib/activation-api.test.ts tests/app/activation-actions.test.ts
```

Expected: PASS.

```bash
git add apps/web/src/lib/types/activation.ts apps/web/src/lib/api/activation.ts \
  apps/web/src/lib/development/capabilities.ts \
  'apps/web/src/app/(activation)/activate/actions.ts' apps/web/tests
git commit -m "feat: add activation web command boundary"
```

---

### Task 3: Build the dedicated activation shell and canonical navigation

**Files:**
- Create: `apps/web/src/app/(activation)/activate/layout.tsx`
- Create: `apps/web/src/app/(activation)/activate/page.tsx`
- Create: `apps/web/src/app/(activation)/activate/loading.tsx`
- Create: `apps/web/src/app/(activation)/activate/error.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/activation-shell.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/milestone-nav.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/stage-refresh.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/stage-router.ts`
- Create: `apps/web/tests/app/activation-page.test.tsx`
- Create: `apps/web/tests/app/milestone-nav.test.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/page.tsx`
- Modify: `apps/web/tests/app/dashboard-onboarding.test.tsx`

**Interfaces:**
- Produces: the protected `/activate` route and five-milestone focused shell.
- Produces: pure `selectMilestone(snapshot, requestedMilestone)` routing.
- Updates: dashboard entry to redirect never-activated users to `/activate`.

- [ ] **Step 1: Write failing navigation and redirect tests**

```tsx
it.each([
  ["profile_required", "business"],
  ["payment_required", "number"],
  ["forwarding_required", "forwarding"],
  ["verification_window_open", "launch"],
])("maps %s to %s", (stage, expected) => {
  expect(selectMilestone(buildSnapshot({ stage }), null)).toBe(expected)
})

it("redirects an active activation route to the dashboard", async () => {
  getActivationSnapshotMock.mockResolvedValue(buildSnapshot({ stage: "active" }))
  await Page({ searchParams: Promise.resolve({}) })
  expect(redirectMock).toHaveBeenCalledWith("/dashboard")
})
```

Add cases for incomplete business versus receptionist fields, a requested
completed milestone, a locked future milestone, `runtime_paused`, loading, and
the route error boundary.

- [ ] **Step 2: Run focused route tests and observe missing route files**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/app/activation-page.test.tsx tests/app/milestone-nav.test.tsx
```

Expected: FAIL because `/activate` does not exist.

- [ ] **Step 3: Implement a server-led route with narrow client islands**

The page awaits Next.js 16 `searchParams`, then loads the snapshot and safe
development capabilities in parallel. Redirect `active` and previously
activated `runtime_paused` customers to `/dashboard`. Pass only the selected
milestone's data to its Client Component.

```tsx
const [snapshot, capabilities] = await Promise.all([
  getActivationSnapshot(),
  getDevelopmentCapabilities(),
])
```

`error.tsx` is a Client Component with an accessible retry button. `loading.tsx`
uses existing `Skeleton`. The layout includes the Presvo wordmark, milestone
progress, account/billing/calls links, and sign-out where Clerk supplies it;
local mode shows a non-interactive `Local development` badge.

- [ ] **Step 4: Derive the five milestones from canonical facts**

Use stable IDs `business`, `receptionist`, `number`, `forwarding`, `launch`.
Profile-required defaults to business until its required subset is populated,
then receptionist. Later stages map exactly as in the test table. Query
parameters may reopen a completed milestone but cannot unlock a future one.

Render a compact ordered navigator rather than five generic cards. Use
`aria-current="step"`, text plus icon status, and links only for canonical
completed/current steps.

- [ ] **Step 5: Add refresh only for authoritative pending states**

`StageRefresh` calls `router.refresh()` every three seconds only for
`provisioning` and `activating`, pauses while the document is hidden, and
cleans up its timer. It never changes the stage locally.

- [ ] **Step 6: Redirect dashboard entry without wasting protected reads**

At the top of `DashboardPage`, load activation first. If the user has never
activated, call `redirect("/activate")` before fetching calls, billing, or agent
data. Allow `active` and previously activated `runtime_paused`; the latter will
receive a dashboard warning in Task 7.

- [ ] **Step 7: Run route tests and commit the shell**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/app/activation-page.test.tsx \
  tests/app/milestone-nav.test.tsx tests/app/dashboard-onboarding.test.tsx
```

Expected: PASS.

```bash
git add 'apps/web/src/app/(activation)' 'apps/web/src/app/(app)/dashboard/page.tsx' \
  apps/web/tests/app
git commit -m "feat: add canonical activation shell"
```

---

### Task 4: Implement autosaving business and receptionist milestones

**Files:**
- Create: `apps/web/src/app/(activation)/activate/_components/profile/profile-form.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/profile/business-fields.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/profile/business-hours-editor.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/profile/carrier-confirmation.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/profile/receptionist-fields.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/profile/receptionist-preview.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/profile/use-profile-autosave.ts`
- Create: `apps/web/tests/app/profile-form.test.tsx`
- Create: `apps/web/tests/app/business-hours-editor.test.tsx`
- Create: `apps/web/tests/app/carrier-confirmation.test.tsx`
- Modify: `apps/web/src/app/(activation)/activate/page.tsx`

**Interfaces:**
- Produces: one full-draft form shared by the business and receptionist milestones.
- Produces: debounced autosave with explicit saved/error state and a flush-before-continue path.
- Produces: automatic carrier lookup plus explicit customer confirmation/manual fallback.

- [ ] **Step 1: Inspect the current shadcn project and exact component docs**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npx shadcn@latest info --json
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npx shadcn@latest docs field input textarea select checkbox button alert badge progress
```

Expected: project reports Radix Vega, RSC enabled, Tailwind v4, Lucide icons,
and the components already exist. Do not run `add` unless `info` proves a
required component is absent; preview any addition with `--dry-run`/`--diff`.

Use the official shadcn and Tailwind Plus block URLs in Global Constraints as
visual references, then implement with the existing Presvo tokens and source
components.

- [ ] **Step 2: Write failing form, hours, carrier, and autosave tests**

```tsx
it("shows saved only after the debounced server action succeeds", async () => {
  vi.useFakeTimers()
  render(<ProfileForm snapshot={profileSnapshot()} milestone="business" />)
  await user.type(screen.getByLabelText(/Owner name/i), "Maya")
  await vi.advanceTimersByTimeAsync(700)
  expect(saveProfileMock).toHaveBeenCalledTimes(1)
  expect(await screen.findByText(/Saved/i)).toBeInTheDocument()
})

it("supports two non-overlapping intervals and no third interval", async () => {
  render(<BusinessHoursEditor value={hoursWithOneMondayInterval()} />)
  await user.click(screen.getByRole("button", { name: /Add afternoon hours/i }))
  expect(screen.getAllByLabelText(/Monday start/i)).toHaveLength(2)
  expect(screen.queryByRole("button", { name: /Add interval/i })).not.toBeInTheDocument()
})
```

Add tests for closed days, overlap error focus, default `Europe/Paris`, French
number formatting, lookup success without implicit confirmation, lookup
failure with immediate manual choices, all five carriers, FAQ limit, bounds
from `profile_constraints`, retry, refresh/resume, and continue flushing the
latest unsaved change before profile confirmation.

- [ ] **Step 3: Run focused tests and observe missing components**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/app/profile-form.test.tsx \
  tests/app/business-hours-editor.test.tsx tests/app/carrier-confirmation.test.tsx
```

Expected: FAIL with missing profile components.

- [ ] **Step 4: Build the full-draft React Hook Form boundary**

Initialize from `snapshot.profile`. Both milestone views edit the same complete
draft object so the API's PUT never clears fields from the other screen. Use
`FieldGroup`, `Field`, `FieldLabel`, `FieldDescription`, `Input`, `Textarea`,
`SelectGroup`, and semantic validation attributes. Use limits from
`snapshot.profile_constraints` for `maxLength` and counters.

The business screen contains owner name, business name/type, timezone,
structured hours, existing French number, and carrier confirmation. The
receptionist screen contains receptionist name, public description, FAQs,
special instructions, escalation notes, and a plain-language knowledge preview.

- [ ] **Step 5: Implement deterministic autosave and continue behavior**

`useProfileAutosave` waits 700 ms after a valid dirty change, runs the Server
Action in `startTransition`, and renders `Unsaved`, `Saving…`, `Saved`, or
`Couldn't save`. It cancels stale timers but never cancels an already-issued
server mutation. Maintain a monotonically increasing request sequence so an
older response cannot overwrite newer feedback.

The primary Continue submit cancels the timer, saves the exact latest draft,
waits for success, then confirms the profile only from the receptionist
milestone. Business Continue navigates to `?milestone=receptionist` after a
successful flush. Draft save never advances silently.

- [ ] **Step 6: Implement carrier discovery and confirmation**

After a valid French number is saved, offer `Check carrier`; automatically run
it once on first valid blur. A detected carrier appears as a suggestion with
`Confirm carrier`. Never assign it to `confirmed_carrier` without the explicit
selection. Lookup failure renders an `Alert` and the manual Select choices
Orange, SFR, Bouygues Telecom, Free, and Other immediately.

- [ ] **Step 7: Run focused form tests, accessibility assertions, and commit**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/app/profile-form.test.tsx \
  tests/app/business-hours-editor.test.tsx tests/app/carrier-confirmation.test.tsx
```

Expected: PASS; every control has an accessible name and invalid submissions
focus the first invalid field.

```bash
git add 'apps/web/src/app/(activation)/activate' apps/web/tests/app
git commit -m "feat: add guided activation profile"
```

---

### Task 5: Implement payment, explicit number consent, and provisioning recovery

**Files:**
- Create: `apps/web/src/app/(activation)/activate/_components/number/number-milestone.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/number/payment-action.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/number/provisioning-consent.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/number/provisioning-status.tsx`
- Create: `apps/web/tests/app/number-milestone.test.tsx`
- Modify: `apps/web/src/app/(activation)/activate/page.tsx`
- Modify: `apps/web/src/app/(activation)/activate/actions.ts`

**Interfaces:**
- Produces: one number milestone for real Stripe checkout or deterministic local billing.
- Produces: explicit `Confirm and provision my number` consent with double-submit protection.
- Produces: pending, succeeded, retryable failure, and terminal correction states.

- [ ] **Step 1: Write failing payment and provisioning behavior tests**

```tsx
it("does not provision after payment alone", async () => {
  render(<NumberMilestone snapshot={paidSnapshotWithoutConsent()} />)
  expect(screen.getByRole("button", { name: /Confirm and provision my number/i })).toBeEnabled()
  expect(confirmProvisioningMock).not.toHaveBeenCalled()
})

it("queues exactly one consent while the action is pending", async () => {
  render(<ProvisioningConsent snapshot={paidSnapshotWithoutConsent()} />)
  await user.dblClick(screen.getByRole("button", { name: /Confirm and provision my number/i }))
  expect(confirmProvisioningMock).toHaveBeenCalledTimes(1)
})
```

Add real checkout URL, fake billing, cancellation/no-charge copy, assigned
French number, retryable failure, terminal correction, refresh while pending,
and no provider-error leakage.

- [ ] **Step 2: Run the focused test and observe missing number components**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/app/number-milestone.test.tsx
```

Expected: FAIL with missing number milestone.

- [ ] **Step 3: Render payment as eligibility, never purchase consent**

When unpaid, show the starter plan and one payment action. In local mode the
action calls `/api/development/activate-starter`; in Stripe mode it obtains the
existing checkout URL and performs browser navigation outside caught action
logic. Copy states explicitly: payment activates the plan but does not order a
number.

- [ ] **Step 4: Require a destructive-style review before number ordering**

Use `AlertDialog` titled `Provision your French Presvo number`. Show country,
one-number limit, the fact that forwarding is configured next, and the exact
button `Confirm and provision my number`. Compose `Spinner` and disable the
trigger while pending. The server's idempotency remains the final double-click
backstop.

- [ ] **Step 5: Add self-service provisioning states**

Pending shows an inline progress row and relies on `StageRefresh`. Success
prominently displays the assigned `+33` number and advances canonically.
Retryable failure offers `Retry provisioning` and says no second number will be
ordered. Terminal failure links back to the profile correction milestone and
shows only the safe reference code.

- [ ] **Step 6: Run focused tests and commit the number milestone**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/app/number-milestone.test.tsx tests/app/activation-actions.test.ts
```

Expected: PASS.

```bash
git add 'apps/web/src/app/(activation)/activate' apps/web/tests/app
git commit -m "feat: add consent-first number milestone"
```

---

### Task 6: Implement forwarding guidance, verification, and explicit launch

**Files:**
- Create: `apps/web/src/app/(activation)/activate/_components/forwarding/forwarding-milestone.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/forwarding/forwarding-step-list.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/forwarding/copy-dial-code.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/launch/launch-milestone.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/launch/verification-countdown.tsx`
- Create: `apps/web/src/app/(activation)/activate/_components/launch/readiness-review.tsx`
- Create: `apps/web/tests/app/forwarding-milestone.test.tsx`
- Create: `apps/web/tests/app/launch-milestone.test.tsx`
- Create: `apps/web/tests/app/verification-countdown.test.tsx`
- Modify: `apps/web/src/app/(activation)/activate/page.tsx`

**Interfaces:**
- Produces: versioned carrier-aware conditional-forwarding instructions.
- Produces: server-synchronized ten-minute verification countdown and local simulator control.
- Produces: explicit readiness-reviewed `Go live` action.

- [ ] **Step 1: Write failing forwarding, countdown, and go-live tests**

```tsx
it("renders only conditional forwarding conditions", () => {
  render(<ForwardingMilestone snapshot={forwardingSnapshot()} />)
  expect(screen.getByText(/When unanswered/i)).toBeInTheDocument()
  expect(screen.getByText(/When busy/i)).toBeInTheDocument()
  expect(screen.getByText(/When unreachable/i)).toBeInTheDocument()
  expect(screen.queryByText(/unconditional/i)).not.toBeInTheDocument()
})

it("uses server time for the verification deadline", () => {
  render(<VerificationCountdown evaluatedAt="2026-07-17T10:00:00Z" expiresAt="2026-07-17T10:10:00Z" />)
  expect(screen.getByRole("timer")).toHaveTextContent("10:00")
})
```

Add copy success/failure, no guessed code state, Other carrier guidance, window
opening, local simulation visibility, expiry refresh once, successful
verification, each readiness blocker link, double-click go-live protection,
activating, runtime failure, and active redirect.

- [ ] **Step 2: Run focused tests and observe missing components**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/app/forwarding-milestone.test.tsx \
  tests/app/launch-milestone.test.tsx tests/app/verification-countdown.test.tsx
```

Expected: FAIL with missing forwarding/launch components.

- [ ] **Step 3: Render carrier guidance as one readable instruction surface**

Show the assigned Presvo number once, then the three ordered conditions from
the API. Use `Accordion` only on small screens and a simple description list on
larger screens; do not wrap each line in a card. Copy buttons exist only when
`dial_code` is non-null and use `navigator.clipboard` with an accessible live
result. Link the official provider source in a secondary disclosure only when
the API supplies one; the Other path must not invent a source.

- [ ] **Step 4: Open and display the verification window**

The primary action says `Start 10-minute test`. Explain that the owner must call
the existing business number from another phone and let it forward. The
countdown computes a fixed server offset from `evaluated_at`, then renders:

```ts
const serverOffsetMs = Date.parse(evaluatedAt) - Date.now()
const remainingMs = Date.parse(expiresAt) - (Date.now() + serverOffsetMs)
```

Use a one-second interval for display only. At zero, call `router.refresh()`
once; never mark expiry locally. In local development only, show `Simulate
forwarded call` with a clear development badge. It calls the same backend
verification service through the development action.

- [ ] **Step 5: Separate successful verification from launch**

After verification, show the fixed success outcome and an explicit readiness
review. Map each stable blocker to the precise milestone link. The only primary
action is `Go live`. During `activating`, disable further commands and let
`StageRefresh` wait for provider projection. Do not report active until the
snapshot says `active`.

- [ ] **Step 6: Run focused tests and commit forwarding/launch**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/app/forwarding-milestone.test.tsx \
  tests/app/launch-milestone.test.tsx tests/app/verification-countdown.test.tsx
```

Expected: PASS.

```bash
git add 'apps/web/src/app/(activation)/activate' apps/web/tests/app
git commit -m "feat: add verified explicit launch experience"
```

---

### Task 7: Make the active dashboard and call handoff obvious and privacy-safe

**Files:**
- Create: `apps/api/app/schemas/call_summary_projection.py`
- Create: `apps/api/tests/calls/test_call_summary_projection.py`
- Modify: `apps/api/app/schemas/calls.py`
- Modify: `apps/api/app/services/call_history_service.py`
- Modify: `apps/api/app/services/recording_service.py`
- Modify: `apps/api/app/providers/storage/base.py`
- Modify: `apps/api/app/providers/storage/s3.py`
- Modify: `apps/api/app/repositories/message_repository.py`
- Modify: `apps/api/app/repositories/call_repository.py`
- Modify: `apps/api/app/routers/calls.py`
- Modify: `apps/api/tests/calls/test_call_history_api.py`
- Modify: `apps/api/tests/providers/test_s3_lifecycle.py`
- Modify: `compose.dev.yaml`
- Delete: `infra/minio/recording-lifecycle.json`
- Create: `apps/web/src/components/dashboard/answering-status-banner.tsx`
- Create: `apps/web/src/components/calls/call-outcome.tsx`
- Create: `apps/web/src/components/calls/delete-call-dialog.tsx`
- Create: `apps/web/tests/app/call-handoff.test.tsx`
- Modify: `apps/web/src/lib/types/calls.ts`
- Modify: `apps/web/src/lib/api/calls.ts`
- Modify: `apps/web/src/components/dashboard/recent-calls-list.tsx`
- Modify: `apps/web/src/components/dashboard/onboarding-status-card.tsx`
- Modify: `apps/web/src/components/dashboard/setup-checklist.tsx`
- Modify: `apps/web/src/components/calls/calls-table.tsx`
- Modify: `apps/web/src/components/calls/call-detail-card.tsx`
- Modify: `apps/web/src/components/calls/recording-panel.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/calls/[callId]/page.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/calls/actions.ts`
- Modify: `apps/web/tests/app/calls-page.test.tsx`
- Modify: `apps/web/tests/app/dashboard-onboarding.test.tsx`
- Modify: `apps/web/tests/app/dashboard-actions.test.ts`

**Interfaces:**
- Extends call list/detail with `summary_status`, `caller_intent`, `action_items`, `sentiment`, and `follow_up_required`.
- Produces: active/runtime-paused dashboard banner and concise outcome/follow-up surfaces.
- Updates: call deletion to remove recording storage, transcript, summary, and caller content before soft deletion.

- [ ] **Step 1: Write failing structured-summary and deletion-purge API tests**

```python
def test_summary_projection_bounds_customer_facing_fields() -> None:
    projection = CallSummaryProjection.from_stored(valid_summary_data())
    assert projection.caller_intent == "Book a consultation"
    assert projection.action_items == ["Return the call"]
    assert projection.follow_up_required is True


@pytest.mark.anyio
async def test_delete_call_removes_recording_and_customer_content(db_session, seeded_call) -> None:
    await service.delete_call(seeded_call.user_id, seeded_call.id)
    recording_provider.delete_object.assert_awaited_once_with(object_key=seeded_call.recording_object_key)
    assert await message_repository.list_by_call_id(seeded_call.id) == []
    assert seeded_call.summary_text is None
    assert seeded_call.summary_data is None
    assert seeded_call.caller_number is None
    assert seeded_call.deleted_at is not None
```

Add malformed/oversized legacy summary, processing/unavailable status, missing
recording idempotency, storage retryable failure leaving database content
visible for retry, cross-tenant delete, duplicate delete, and proof that the
local stack no longer installs automatic expiration.

- [ ] **Step 2: Run focused API tests and observe missing projection/purge behavior**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/calls/test_call_summary_projection.py tests/calls/test_call_history_api.py \
  tests/providers/test_s3_lifecycle.py -q
```

Expected: FAIL because structured fields and storage deletion do not exist.

- [ ] **Step 3: Add a bounded customer-facing summary projection**

```python
class CallSummaryProjection(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    caller_intent: str = Field(min_length=1, max_length=200)
    action_items: list[Annotated[str, StringConstraints(min_length=1, max_length=300)]] = Field(max_length=10)
    sentiment: str = Field(min_length=1, max_length=32)
    follow_up_required: bool
```

Parse `Call.summary_data` defensively. Invalid legacy data returns null
structured fields, never a 500. `summary_status` is `processing` for active
finalization states, `ready` when the summary projection or text exists, and
`unavailable` after completion/failure without a usable summary. Extend both
list and detail response models.

- [ ] **Step 4: Make deletion remove active customer content**

Add `StorageProvider.delete_object`, an instrumented/idempotent S3
implementation, and `RecordingService.delete_recording`. `CallHistoryService`
deletes the object first; retryable storage failure returns safe `503
{"code":"call_delete_retryable"}` without changing the database. Missing
objects count as success. Then, in one database transaction:

- delete all `CallMessage` rows for the call;
- clear caller number, summary text/data, summary sequence, recording object
  key/URL/egress metadata;
- set `deleted_at`.

Load by `(call_id, user_id)` including already-deleted rows. A repeat delete by
the owning user returns 204 without another storage call; an unknown or
cross-tenant ID remains 404. Set `has_recording` from
`recording_object_key`, not a legacy stored URL.

Do not claim backup erasure. Add a rate limit to DELETE matching other call
history operations.

Remove `mc ilm rule import` and its mounted lifecycle file from
`compose.dev.yaml`, then delete `infra/minio/recording-lifecycle.json`. Keep the
bucket private and keep signed access. Replace the old lifecycle assertion with
a regression test/source check proving Presvo's local stack does not configure
automatic expiration. This deliberately implements the approved "delete now,
retention later" decision.

- [ ] **Step 5: Write failing web handoff tests**

```tsx
it("shows the call outcome and an obvious follow-up", () => {
  render(<RecentCallsList calls={[structuredCall({ follow_up_required: true })]} />)
  expect(screen.getByText("Book a consultation")).toBeInTheDocument()
  expect(screen.getByText(/Follow-up needed/i)).toBeInTheDocument()
  expect(screen.getByText("Return the call")).toBeInTheDocument()
})

it("renders original audio with native controls", () => {
  render(<RecordingPanel recordingUrl="https://recording.test/call.ogg" />)
  expect(screen.getByLabelText(/Original call recording/i)).toHaveAttribute("controls")
})
```

Add summary processing/unavailable, no-action state, delete confirmation,
delete retry error, active banner, runtime-pause reason, and masked caller
fallback.

- [ ] **Step 6: Implement the concise dashboard handoff**

Lead the dashboard with `Presvo is answering` or `Presvo is paused`, not setup
cards. For each call, show one-sentence summary, caller intent as the outcome,
follow-up badge, up to three action items plus a count, time/duration, and
recording availability. The detail page uses native `<audio controls
preload="metadata">` for the signed original recording URL and an
`AlertDialog` deletion control.

Rename the misleading web `archiveCall`/`archiveCallAction` functions to
`deleteCall`/`deleteCallAction`. The deletion Server Action authenticates,
calls the backend, revalidates call routes, and redirects only after success
outside its catch block. Customer copy
says `Remove call` and explains removal from the active Presvo account without
making a backup-erasure promise.

Replace the legacy onboarding/setup copy with links to `/activate` where still
relevant and correct all Irish-number references.

- [ ] **Step 7: Run API and web handoff tests, scan country copy, and commit**

```bash
cd apps/api
env DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest \
  tests/calls/test_call_summary_projection.py tests/calls/test_call_history_api.py \
  tests/providers/test_s3_lifecycle.py -q
cd ../web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci -- tests/app/call-handoff.test.tsx tests/app/calls-page.test.tsx \
  tests/app/dashboard-onboarding.test.tsx tests/app/dashboard-actions.test.ts
rg -n -i "irish|ireland|\\+353" src tests
```

Expected: tests PASS and `rg` exits 1 with no launch-UI matches.

```bash
git add apps/api/app apps/api/tests apps/web/src apps/web/tests compose.dev.yaml infra/minio
git commit -m "feat: clarify active calls and deletion"
```

---

### Task 8: Prove the provider-free browser journey and document local operation

**Files:**
- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/tests/e2e/activation.spec.ts`
- Create: `scripts/run-local-e2e.sh`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Modify: `compose.dev.yaml`
- Modify: `compose.yaml`
- Modify: `apps/api/.env.example`
- Modify: `apps/web/.env.example`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/PROJECT_STATUS.md`
- Create: `docs/architecture/local-self-service-activation.md`

**Interfaces:**
- Produces: `npm run test:e2e` and a disposable Docker-backed local E2E runner.
- Enables: complete local activation with local auth and fake billing/carrier/telephony/verification.
- Documents: implemented local capability, real-provider opt-in boundaries, and deferred production gates.

- [ ] **Step 1: Add Playwright as a locked direct development dependency**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm install --save-dev @playwright/test
```

Add scripts:

```json
{
  "test:e2e": "playwright test",
  "test:e2e:headed": "playwright test --headed"
}
```

Configure Chromium, `baseURL` from `E2E_BASE_URL` defaulting to
`http://127.0.0.1:3300`, screenshots/traces only on failure, and one worker for
the shared deterministic account.

- [ ] **Step 2: Write the failing full browser journey**

```ts
test("local owner activates Presvo without external providers", async ({ page }) => {
  await page.goto("/activate")
  await completeBusinessMilestone(page)
  await page.reload()
  await completeReceptionistMilestone(page)
  await page.reload()
  await page.getByRole("button", { name: "Activate local starter plan" }).click()
  await page.getByRole("button", { name: "Confirm and provision my number" }).click()
  await expect(page.getByText(/^\+339/)).toBeVisible()
  await page.reload()
  await applyForwardingGuidance(page)
  await page.getByRole("button", { name: "Start 10-minute test" }).click()
  await page.reload()
  await page.getByRole("button", { name: "Simulate forwarded call" }).click()
  await page.getByRole("button", { name: "Go live" }).click()
  await expect(page).toHaveURL(/\/dashboard/)
  await expect(page.getByText("Presvo is answering")).toBeVisible()
})
```

Keep this as one serial journey because local identity intentionally exposes one
fixed account. The reloads prove canonical resume without a dangerous database
reset endpoint. Component/API suites cover controlled failure variants.

- [ ] **Step 3: Configure deterministic local modes in Compose**

Parameterize host ports in `compose.dev.yaml` with defaults so E2E can use
`WEB_PORT=3300`, `API_PORT=5800`, `POSTGRES_PORT=55432`, `REDIS_PORT=56379`,
and separate MinIO ports. Set these explicit development service values:

```yaml
AUTH_MODE: local
LOCAL_AUTH_TOKEN: presvo-local-development-token
BILLING_MODE: fake
CARRIER_LOOKUP_MODE: fake
TELEPHONY_MODE: fake
ACTIVATION_FLOW_ENABLED: "true"
```

Pass `AUTH_MODE` and `LOCAL_AUTH_TOKEN` only to API and web server
environments, never to the worker or as public build args. The worker receives
only `TELEPHONY_MODE=fake` plus `ACTIVATION_FLOW_ENABLED=true` for the readiness
rules used by durable jobs. The web server also receives
`BILLING_MODE=fake` and `TELEPHONY_MODE=fake` so it can compute the two boolean
development capabilities; it does not receive carrier credentials or provider
secrets. Keep real provider calls opt-in.
Production Compose explicitly selects Clerk/Stripe/Telnyx modes and requires
`ACTIVATION_FLOW_ENABLED`; it must contain no local token.

- [ ] **Step 4: Add a disposable runner with cleanup**

`scripts/run-local-e2e.sh` uses `set -eu`, project name `presvo-e2e`, the
alternate ports, and a trap that runs `docker compose ... down --volumes`.
It builds/starts Postgres, Redis, MinIO, migrate, API, worker, and web; waits for
health; runs Playwright from `apps/web`; and always cleans up. It does not start
the LiveKit agent because the deterministic simulator exercises the same
verification application service.

- [ ] **Step 5: Run the browser test and make it pass**

```bash
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm exec --prefix apps/web -- playwright install chromium
bash scripts/run-local-e2e.sh
```

Expected: the browser journey PASSes with no Clerk, Stripe, Telnyx, LiveKit, or
cloud credentials and no manual database edits.

- [ ] **Step 6: Add browser coverage to CI without deploying anything**

Add an `e2e` job after API/agent/web unit jobs. It installs Node 22, runs
`npm ci`, installs Chromium with system dependencies, then invokes the same
disposable local runner. Pin any new GitHub Actions by full commit SHA. This is
test orchestration only; it does not publish images or mutate cloud resources.

- [ ] **Step 7: Update operator and open-source documentation**

Document:

- the complete local five-milestone journey and exact startup/test commands;
- why payment and provisioning consent are separate;
- France/English launch scope and conditional forwarding;
- local versus real provider modes and production fail-closed rules;
- that cloud deployment, real-provider certification, French localization,
  legal approval, appointment booking, conversation flows, and automatic
  30-day retention remain planned;
- that user deletion removes active call content/storage but makes no backup
  erasure claim.

Update old automatic-provisioning and Irish-number statements rather than
leaving contradictory historical product status in README/PROJECT_STATUS.

- [ ] **Step 8: Commit E2E and local documentation**

```bash
git add apps/web/package.json apps/web/package-lock.json apps/web/playwright.config.ts \
  apps/web/tests/e2e scripts/run-local-e2e.sh compose.dev.yaml compose.yaml \
  apps/api/.env.example apps/web/.env.example .github/workflows/ci.yml \
  README.md docs/PROJECT_STATUS.md docs/architecture/local-self-service-activation.md
git commit -m "test: prove local self-service activation"
```

---

### Task 9: Verify the complete four-plan product slice

**Files:**
- Modify only files already owned by Plans 1-4 if verification reveals a defect.

- [ ] **Step 1: Run API quality and full tests**

```bash
cd apps/api
env ACTIVATION_FLOW_ENABLED=true DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync ruff check app tests
env ACTIVATION_FLOW_ENABLED=true DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync mypy app
env ACTIVATION_FLOW_ENABLED=true DATABASE_URL=sqlite+aiosqlite:// REDIS_URL=redis://localhost:6379/0 UV_CACHE_DIR=/tmp/uv-cache \
  uv run --frozen --no-sync python -m pytest -q
```

Expected: all commands exit 0.

- [ ] **Step 2: Run agent quality and full tests**

```bash
cd apps/agent
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync ruff check agent tests
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync mypy agent
env UV_CACHE_DIR=/tmp/uv-cache uv run --frozen --no-sync python -m pytest -q
```

Expected: all commands exit 0, including fixed-message verification isolation.

- [ ] **Step 3: Run web quality, unit tests, and production build**

```bash
cd apps/web
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run check
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run typecheck
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  npm run test:ci
env PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  AUTH_MODE=clerk NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_Y2xlcmsuZXhhbXBsZS5jb20k \
  CLERK_SECRET_KEY=ci-build-only-secret API_BASE_URL=http://127.0.0.1:8000 \
  NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 NEXT_PUBLIC_APP_URL=http://127.0.0.1:3000 \
  npm run build
```

Expected: formatting/lint, TypeScript, tests, and build exit 0.

- [ ] **Step 4: Run the disposable provider-free browser suite**

```bash
bash scripts/run-local-e2e.sh
```

Expected: the activation journey, including each refresh/resume checkpoint,
PASSes.

- [ ] **Step 5: Run safety and scope scans**

```bash
rg -n -i "irish number|automatic number provisioning|\\+353" \
  apps/web/src apps/web/tests README.md docs/PROJECT_STATUS.md
rg -n "NEXT_PUBLIC_.*LOCAL_AUTH" apps/web/src apps/web/next.config.*
rg -n "LOCAL_AUTH_TOKEN" compose.yaml
rg -n 'TO''DO|T''BD|FIX''ME|implement[[:space:]]+later' \
  docs/superpowers/plans/2026-07-17-activation-*.md \
  docs/superpowers/plans/2026-07-17-consent-provisioning-and-local-providers.md \
  docs/superpowers/plans/2026-07-17-forwarding-verification-and-go-live.md
git status --short
```

Expected: no stale launch copy, no public local credential, no unfinished-plan
markers, and only intentional implementation changes before the final commit.

- [ ] **Step 6: Review the acceptance criteria against evidence**

Check every acceptance criterion in
`docs/superpowers/specs/2026-07-17-local-self-service-activation-design.md` and
record its proving test or UI/API path in the implementation PR description.
Do not claim cloud deployment, qualified legal approval, automatic retention,
or credentialed provider certification.

- [ ] **Step 7: Commit verification-only corrections if needed**

```bash
git add apps/api apps/agent apps/web compose.dev.yaml compose.yaml README.md docs .github
git commit -m "fix: close activation verification gaps"
```

Skip this commit when verification required no correction.
