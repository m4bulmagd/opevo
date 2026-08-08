# Task 9 Report: Account, read-only, and scheduled-cancellation web UX

## Status and commit

- Status: complete
- Starting HEAD: `2e36a18c23a4f17a294ba55166d208de8783b98d`
- Commit: included in `feat: add account deactivation experience` (final SHA is
  returned in the parent handoff)
- Scope: Task 9 web UX only. No Task 10 E2E fixture or provider work was added.

## Files

- Added the typed account contract/client, authenticated account actions,
  Account route, lifecycle banner, state card, destructive confirmation
  dialog, and the small inactive-only reactivation client boundary.
- Integrated account state into dashboard layout, sidebar navigation,
  activation routing, agent read-only controls, agent action error mapping,
  billing scheduled-cancellation presentation, and billing types.
- Added focused Account action/page tests and extended the app-shell, agent,
  activation, and billing tests.
- The existing `activateDevelopmentStarter`, `createCheckoutSession`,
  `getSubscription`, and sidebar-item composition boundaries already provided
  the required low-level calls, so they were reused rather than duplicated.

## RED evidence

The exact six-file command from the brief ran first under project Node
22.23.1:

```bash
npm run test:ci -- \
  tests/app/account-actions.test.ts \
  tests/app/account-page.test.tsx \
  tests/app/app-shell.test.tsx \
  tests/app/agent-page.test.tsx \
  tests/app/activation-page.test.tsx \
  tests/app/billing-page.test.tsx
```

Result: exit `1`; **6 failed files, 7 failed tests, 31 passed tests**. The new
Account suites could not load the missing action/page/types, while the
existing suites proved Account navigation, lifecycle activation redirects,
agent read-only controls, and scheduled-cancellation copy were absent.

A self-review progress-state RED then asserted the safe requested and
attention labels. Result: **2 failed, 4 passed** because no progress label was
rendered. The smallest follow-up implementation added bounded customer copy
for every API progress state.

## GREEN and verification evidence

Final exact focused command: **6 files passed, 50 tests passed**.

Final complete web suite:

```text
Test Files  30 passed (30)
Tests       246 passed (246)
```

Static gates under Node 22.23.1:

- `npm run check`: `Checked 155 files`; no fixes or errors.
- `npm run typecheck`: exit `0`.
- `git diff --check`: exit `0`.

The default sandboxed Turbopack process stalled at compilation because of the
restricted process environment. An authorized normal-environment run compiled
and typechecked, then correctly required production configuration during page
collection. The final build used the repository-documented CI-only Clerk/API
placeholders:

```bash
AUTH_MODE=clerk \
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_YWEuYWEk \
CLERK_SECRET_KEY=ci-build-only-secret \
API_BASE_URL=http://127.0.0.1:8000 \
npm run build
```

Result: exit `0`; compiled in 8.4s, TypeScript completed in 8.1s, and **10/10
static pages** generated. `/dashboard/account` is present as a dynamic route.
No provider was contacted.

## State UX matrix

| Account state | Global/page presentation | Mutation state |
|---|---|---|
| active and serving | active card; danger zone available | agent controls editable; exact-confirmation deactivation available |
| active but not serving | active card says setup is still required | account remains active; lifecycle copy does not call it inactive |
| deactivating | global “Opevo is no longer accepting new calls”; page “Finishing account deactivation”; safe current-progress label | no danger zone; all agent fields, routing switch, and save disabled |
| attention required | same truthful non-serving and finishing copy; “Cleanup needs additional time” | still read-only; no provider/error internals |
| inactive, cleanup ready | retained-data explanation and `Reactivate Opevo` | history/navigation remain; reactivation enabled |
| inactive, cleanup unresolved | retained-data explanation and disabled reactivation with visible reason | no checkout/local activation call can be started from the UI |

Inactive Account UI has no assigned-number field and never receives a phone
number in its account status contract. Activation redirects inactive and
deactivating owners to `/dashboard/account` before mutable milestones render.
Calls and Billing remain in sidebar navigation for every state.

## Action, authentication, and safe-error matrix

| Action/path | Authentication and validation | Mutation boundary | Customer-safe result |
|---|---|---|---|
| `getAccount` | authenticated `backendFetch` | read-only `GET /api/account` | typed bounded projection; React request cache deduplicates layout/page reads |
| `deactivateAccount` | explicit server-session check plus strict Zod literal `DEACTIVATE` | `POST /api/account/deactivate`; web never changes account state | safe validation/rate/conflict/service codes only; lifecycle paths revalidated; redirect occurs outside catches |
| local `reactivateAccount` | explicit server-session check; server-owned capability selection | existing `/api/development/activate-starter` client boundary | returns controlled `/activate` destination |
| hosted `reactivateAccount` | explicit server-session check; fixed `starter` plan | existing checkout API boundary | accepts only an HTTPS hosted URL; provider details and unsafe URLs are discarded |
| agent save | authenticated backend client and authoritative API enforcement | existing agent PATCH API | account lifecycle blockers receive bounded read-only/reactivation messages; backend details are not echoed |

The deactivation success invalidates `/dashboard`, `/dashboard/account`,
`/dashboard/agent`, `/dashboard/billing`, and `/activate`, then redirects to
Account. Neither account action writes local account, subscription, or phone
state.

## RSC, data-fetch, and performance notes

- Dashboard cookies, account status, and independent layout preferences start
  together with `Promise.all`.
- Agent configuration/account state and activation query/account/capability
  reads are parallel.
- All pages and status/banner components remain Server Components. Only the
  destructive dialog, existing agent form, and inactive reactivation button
  are client boundaries.
- The reactivation client receives one boolean instead of the full account
  projection. Server-to-client props are plain JSON values; there are no async
  client components, `Date`, class, function, `Map`, or `Set` props.
- `getAccount` uses React request caching to deduplicate the layout and nested
  page read without cross-request persistence.

## Accessibility, responsive, and read-only proof

- The destructive flow uses the existing accessible AlertDialog primitive,
  retains Escape/Cancel behavior, has an explicit input label, case-sensitive
  help, error live region, loading state, and disabled confirmation until the
  exact value is present.
- Reactivation has disabled/pending states, an explanatory
  `aria-describedby`, and a live safe error.
- Agent values remain visible while every input, textarea, routing switch, and
  submit button is disabled; the action remains API-enforced if invoked
  directly.
- Lifecycle alerts use `role=alert`/polite live updates. Headings preserve page
  hierarchy. Status-card title/badge layout stacks on narrow screens and
  returns to a row at `sm`; the dialog and page retain existing Opevo
  responsive primitives.

## Exact copy proof

The dialog renders these six complete sentences and no shortened variants:

1. `New calls stop immediately.`
2. `Your subscription is canceled immediately with no automatic prorated refund.`
3. `An active call may finish before cleanup completes.`
4. `Your current Opevo number is permanently released.`
5. `Your calls, recordings, billing history, and saved configuration are retained.`
6. `Reactivation requires a new subscription and a newly provisioned number.`

The button is enabled only for exact case-sensitive `DEACTIVATE`; lowercase
and trailing-space inputs remain disabled and the server independently
rejects them.

Billing renders `Cancels at the end of your paid period` plus the localized
UTC effective date while retaining the actual active subscription badge. It
does not describe a scheduled-cancellation account as inactive.

## Concerns

- The production build requires the documented production auth/backend
  settings; absent or invalid values intentionally fail closed.
- The sandbox-only Turbopack stall was environmental; the authorized normal
  build completed successfully.
- No unresolved Task 9 code, design, accessibility, performance, or privacy
  concern remains.

## Fix round 1/5

Review base: `611dd60d622c3ceeb868bbcdbf1a726c34e6b6fa`.

### Changes

- Bounded the destructive AlertDialog to
  `max-h-[calc(100dvh-2rem)]`, contained outer overscroll, and split it into a
  `minmax(0,1fr)` scroll body plus a non-scrolling footer. All six
  consequences and the labelled confirmation input remain vertically
  reachable, while Cancel and Deactivate remain visible in the existing
  stacked mobile footer. The Radix content/root and focus trap are unchanged.
- Added the meaningful `deactivation-confirmation` name to the confirmation
  input.
- Replaced the generic HTTPS redirect check with an exact Stripe Checkout
  boundary. Production accepts only `https://checkout.stripe.com` with no
  credentials or custom port. The established `https://checkout.stripe.test`
  fixture is accepted only when `NODE_ENV=test`; a dedicated assertion proves
  it is rejected in production. Non-HTTPS, unrelated, lookalike, credentialed,
  and custom-port URLs return the existing bounded error without leaking the
  rejected URL.
- Added direct dashboard layout coverage proving deactivating and inactive
  accounts render the global lifecycle banner, active accounts omit it, and
  Account, Calls, and Billing navigation remains available in every state.
  The layout behavior was already present; this review item closed the missing
  regression coverage.

### RED and GREEN evidence

- The review RED pass produced the expected failures for the missing input
  name and viewport/scroll contract, and four arbitrary-HTTPS redirect cases
  were accepted by the previous validator. The existing HTTP rejection
  remained green. Direct layout tests initially exposed test-harness-only font
  and Tooltip provider dependencies; after isolating those unrelated controls,
  the new state assertions exercised the existing layout behavior.
- Final focused verification:
  **3 test files passed, 27 tests passed**.
- Final full web verification:
  **30 test files passed, 257 tests passed**.
- `biome check`: **155 files checked**, no fixes or errors.
- `tsc --noEmit`: exit `0`.
- `git diff --check`: exit `0`.
- The sandboxed Turbopack build again stalled during compilation. The
  authorized normal-environment build with the documented CI-only Clerk/API
  placeholders compiled in 8.1s, completed TypeScript in 9.0s, generated
  **10/10 static pages**, and exited `0`. No provider was contacted.

No Task 10 fixture, E2E, or provider work was added.
