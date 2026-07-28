# Presvo Public Entry and Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the public entry, hosted-auth handoff, and five authoritative activation milestones into the approved Presvo visual system without changing France-first product truth or backend behavior.

**Architecture:** Keep the existing Next.js routes, session resolution, Clerk/local-mode gates, activation API clients, server actions, and stage router. Move the public landing markup into a focused presentation component, add a shared auth presentation shell, and recompose activation around a template-matched progress header and surface card. Existing milestone forms and commands remain the only owners of mutations.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, Tailwind CSS 4, shadcn/Radix primitives, Clerk, Motion, Vitest, Testing Library, Playwright.

## Global Constraints

- `apps/web` remains the only deployed frontend; `/home/mo/code/ai/bmad-opevo/Presvo_frontend` is read-only visual reference material.
- Preserve the exact Phase 1 Presvo tokens, system typography, spacing, borders, shadows, card styles, and visual hierarchy.
- Keep `/`, `/sign-in/[[...sign-in]]`, `/sign-up/[[...sign-up]]`, and `/activate` as the production routes.
- Preserve the existing five milestones: Business, Receptionist, Number, Forwarding, Launch.
- Preserve autosave, first-invalid-field focus, carrier confirmation, starter-plan checkout, explicit number-provisioning consent, conditional-forwarding verification, go-live confirmation, restart, and resume.
- France remains the only phone country in activation copy and behavior.
- Billing, provisioning, verification, and go-live success may appear only after backend acknowledgement.
- Do not add or modify an API endpoint in this phase.
- Do not add `transition-all`; keep reduced-motion behavior and 44px mobile targets.
- Keep one `main` landmark, a visible-on-focus skip link, hierarchical headings, safe-area spacing, and no horizontal overflow at 390px.
- Follow red-green-refactor for each task and commit only after its listed checks pass.

---

## File Map

### Create

- `apps/web/src/components/landing/presvo-landing-page.tsx`
- `apps/web/src/components/auth/auth-entry-shell.tsx`
- `apps/web/tests/app/auth-entry.test.tsx`
- `apps/web/tests/e2e/entry-activation-visual.spec.ts`
- `apps/web/tests/e2e/entry-activation-visual.spec.ts-snapshots/*`

### Modify

- `apps/web/src/app/page.tsx`
- `apps/web/src/app/(auth)/sign-in/[[...sign-in]]/page.tsx`
- `apps/web/src/app/(auth)/sign-up/[[...sign-up]]/page.tsx`
- `apps/web/src/app/(activation)/activate/layout.tsx`
- `apps/web/src/app/(activation)/activate/page.tsx`
- `apps/web/src/app/(activation)/activate/loading.tsx`
- `apps/web/src/app/(activation)/activate/error.tsx`
- `apps/web/src/app/(activation)/activate/_components/activation-shell.tsx`
- `apps/web/src/app/(activation)/activate/_components/milestone-nav.tsx`
- `apps/web/src/app/(activation)/activate/_components/profile/profile-form.tsx`
- `apps/web/src/app/(activation)/activate/_components/number/payment-action.tsx`
- `apps/web/src/app/(activation)/activate/_components/number/provisioning-consent.tsx`
- `apps/web/src/app/(activation)/activate/_components/number/provisioning-status.tsx`
- `apps/web/src/app/(activation)/activate/_components/forwarding/forwarding-milestone.tsx`
- `apps/web/src/app/(activation)/activate/_components/launch/launch-milestone.tsx`
- `apps/web/tests/app/root-page.test.tsx`
- `apps/web/tests/app/activation-page.test.tsx`
- `apps/web/tests/app/milestone-nav.test.tsx`
- `apps/web/tests/e2e/activation.spec.ts`
- `scripts/run-local-e2e.sh`

### Preserve Without Behavioral Changes

- `apps/web/src/app/(activation)/activate/actions.ts`
- `apps/web/src/app/(activation)/activate/_components/stage-router.ts`
- `apps/web/src/app/(activation)/activate/_components/profile/use-profile-autosave.ts`
- `apps/web/src/lib/api/activation.ts`
- `apps/web/src/lib/types/activation.ts`

---

## Task 1: Replace the Blue Landing Page with the Presvo Public Entry

**Files:**

- Create: `apps/web/src/components/landing/presvo-landing-page.tsx`
- Modify: `apps/web/src/app/page.tsx`
- Modify: `apps/web/tests/app/root-page.test.tsx`

**Interfaces:**

- Consumes: `isAuthenticated: boolean`, Phase 1 `Button`, `Badge`, `Card`, `CapabilityBadge`, and landing motion primitives.
- Produces: `PresvoLandingPage({ isAuthenticated }: { isAuthenticated: boolean })`.

- [x] **Step 1: Add failing semantic and visual-contract tests**

Extend `root-page.test.tsx`:

```tsx
expect(screen.getByRole("main")).toHaveClass("bg-background", "text-foreground");
expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/missed call/i);
expect(screen.getByText(/Built for French businesses/i)).toBeVisible();
expect(screen.getByRole("region", { name: "Product overview" })).toHaveClass(
  "rounded-2xl",
  "border",
  "bg-card",
  "shadow-raised",
);
expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute(
  "href",
  "#landing-content",
);
expect(container.innerHTML).not.toContain("transition-all");
expect(container.innerHTML).not.toContain("slate-");
expect(container.innerHTML).not.toContain("SonicWaveformCanvas");
```

Keep the signed-out `Log in`/`Sign up` and signed-in `Dashboard` assertions.

- [x] **Step 2: Run the root-page tests and confirm failure**

```bash
cd apps/web
npm run test:ci -- tests/app/root-page.test.tsx
```

Expected: the old blue/slate landing and missing Presvo surface contract fail.

- [x] **Step 3: Extract the presentation component**

Reduce `app/page.tsx` to session ownership:

```tsx
import { PresvoLandingPage } from "@/components/landing/presvo-landing-page";
import { getServerSessionState } from "@/lib/auth/server-session";

export default async function Page() {
  const session = await getServerSessionState();
  return <PresvoLandingPage isAuthenticated={session.isAuthenticated} />;
}
```

Create `PresvoLandingPage` with these exact public sections and anchors:

```ts
const PUBLIC_NAVIGATION = [
  { href: "#product", label: "Product" },
  { href: "#how-it-works", label: "How it works" },
  { href: "#questions", label: "Questions" },
] as const;

const ENTRY_STEPS = [
  {
    title: "Share your business context",
    description: "Add your hours, services, existing French number, and the details callers need.",
  },
  {
    title: "Provision your Presvo line",
    description: "Activate the starter plan, then explicitly approve one French number for conditional forwarding.",
  },
  {
    title: "Verify before going live",
    description: "Test missed-call forwarding and choose when Presvo may begin answering.",
  },
] as const;
```

The page structure is:

```tsx
<main className="min-h-screen bg-background text-foreground">
  <a href="#landing-content" className="sr-only ... focus:not-sr-only">
    Skip to main content
  </a>
  <header className="border-b border-border bg-background/90 backdrop-blur">
    {/* Presvo mark, PUBLIC_NAVIGATION, auth-aware actions */}
  </header>
  <div id="landing-content">
    <section className="mx-auto grid w-full max-w-6xl gap-10 px-5 py-16 sm:px-8 lg:grid-cols-[1fr_0.88fr] lg:py-24">
      {/* "Built for French businesses", missed-call h1, truthful copy, CTA */}
      <section aria-label="Product overview" className="rounded-2xl border border-border bg-card p-5 shadow-raised sm:p-6">
        {/* live status, France (+33), 60-minute starter allowance, recent-call empty preview */}
      </section>
    </section>
    {/* #product feature cards, #how-it-works numbered steps, #questions FAQ, CTA, compact footer */}
  </div>
</main>
```

Use only semantic Phase 1 colors (`background`, `card`, `muted`, `primary`, `border`, `success`) and `shadow-card`/`shadow-raised`. Preserve `LandingMotionGroup`, `LandingMotionItem`, and `LandingMotionFade`; do not use hover motion on informational cards.

- [x] **Step 4: Run focused verification**

```bash
cd apps/web
npm run test:ci -- tests/app/root-page.test.tsx tests/components/motion-primitives.test.tsx
npm run typecheck
npm run check
```

- [x] **Step 5: Commit**

```bash
git add apps/web/src/app/page.tsx apps/web/src/components/landing/presvo-landing-page.tsx apps/web/tests/app/root-page.test.tsx
git commit -m "feat(web): migrate Presvo public entry"
```

---

## Task 2: Add the Presvo Hosted-Auth Handoff

**Files:**

- Create: `apps/web/src/components/auth/auth-entry-shell.tsx`
- Create: `apps/web/tests/app/auth-entry.test.tsx`
- Modify: `apps/web/src/app/(auth)/sign-in/[[...sign-in]]/page.tsx`
- Modify: `apps/web/src/app/(auth)/sign-up/[[...sign-up]]/page.tsx`

**Interfaces:**

- Consumes: a `title`, `description`, and Clerk child node.
- Produces:

```ts
type AuthEntryShellProps = {
  children: ReactNode;
  description: string;
  title: string;
};
```

- [x] **Step 1: Write failing auth-shell tests**

Mock Clerk and the auth configuration for configured Clerk mode. Assert:

```tsx
expect(screen.getByRole("main")).toHaveClass("bg-background");
expect(screen.getByRole("link", { name: "Presvo home" })).toHaveAttribute("href", "/");
expect(screen.getByRole("heading", { name: "Welcome back" })).toBeVisible();
expect(screen.getByTestId("clerk-sign-in")).toBeVisible();
expect(signInMock).toHaveBeenCalledWith(
  expect.objectContaining({
    forceRedirectUrl: "/dashboard",
    signUpUrl: "/sign-up",
    appearance: expect.any(Object),
  }),
);
```

Repeat for sign-up with title `Create your Presvo account`, `signInUrl="/sign-in"`, and the existing dashboard redirect. Keep local mode redirect assertions.

- [x] **Step 2: Run the auth test and confirm failure**

```bash
cd apps/web
npm run test:ci -- tests/app/auth-entry.test.tsx
```

- [x] **Step 3: Implement `AuthEntryShell`**

Render:

```tsx
<main className="relative grid min-h-screen place-items-center overflow-hidden bg-background px-4 py-10 sm:px-6">
  <div aria-hidden="true" className="absolute inset-x-0 top-0 h-72 bg-[radial-gradient(circle_at_top,var(--color-primary-soft),transparent_68%)] opacity-70" />
  <div className="relative flex w-full max-w-md flex-col gap-6">
    <Link aria-label="Presvo home" href="/" className="mx-auto inline-flex min-h-11 items-center gap-3 ...">
      {/* Phase 1 Presvo phone mark */}
    </Link>
    <section aria-labelledby="auth-entry-title" className="rounded-2xl border border-border bg-card p-5 shadow-raised sm:p-7">
      <div className="mb-5 text-center">
        <h1 id="auth-entry-title" className="font-semibold text-2xl tracking-tight">{title}</h1>
        <p className="mt-2 text-muted-foreground text-sm leading-6">{description}</p>
      </div>
      {children}
    </section>
    <p className="text-center text-muted-foreground text-xs">France-first call handling · Your activation progress resumes automatically.</p>
  </div>
</main>
```

- [x] **Step 4: Pass a shared Clerk appearance contract**

Both routes pass:

```ts
const PRESVO_CLERK_APPEARANCE = {
  variables: {
    colorPrimary: "var(--primary)",
    colorBackground: "var(--card)",
    colorText: "var(--foreground)",
    colorTextSecondary: "var(--muted-foreground)",
    colorInputBackground: "var(--background)",
    colorInputText: "var(--foreground)",
    borderRadius: "0.875rem",
    fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
  elements: {
    rootBox: "w-full",
    cardBox: "w-full shadow-none",
    card: "w-full bg-transparent shadow-none p-0",
    footer: "bg-transparent",
  },
} as const;
```

Do not change `authMode`, `isAppAuthConfigured`, redirect URLs, or dynamic imports.

- [x] **Step 5: Run verification and commit**

```bash
cd apps/web
npm run test:ci -- tests/app/auth-entry.test.tsx tests/app/app-shell.test.tsx
npm run typecheck
npm run check
git add src/components/auth/auth-entry-shell.tsx src/app/'(auth)' tests/app/auth-entry.test.tsx
git commit -m "feat(web): style hosted auth entry"
```

---

## Task 3: Port the Template Onboarding Hierarchy to the Authoritative Stage Router

**Files:**

- Modify: `apps/web/src/app/(activation)/activate/layout.tsx`
- Modify: `apps/web/src/app/(activation)/activate/_components/activation-shell.tsx`
- Modify: `apps/web/src/app/(activation)/activate/_components/milestone-nav.tsx`
- Modify: `apps/web/tests/app/activation-page.test.tsx`
- Modify: `apps/web/tests/app/milestone-nav.test.tsx`

**Interfaces:**

- Consumes: `ActivationSnapshot`, `ActivationMilestoneId`, `getMilestoneState`.
- Produces: the same `ActivationShell` and `MilestoneNav` props; no state-router changes.

- [x] **Step 1: Add failing shell and progress assertions**

Assert:

```tsx
const main = screen.getByRole("main");
expect(main).toHaveClass("max-w-3xl", "px-4", "sm:px-6");
expect(screen.getByText("Step 4 of 5")).toHaveClass("text-label");

const progress = screen.getByRole("navigation", { name: "Activation progress" });
expect(progress).toHaveAttribute("aria-label", "Activation progress");
expect(progress.querySelectorAll('[data-slot="activation-progress-segment"]')).toHaveLength(5);
expect(screen.getByText("Forwarding").closest("[aria-current='step']")).not.toBeNull();
```

For a locked milestone, assert it is not a link. For completed milestones, retain their canonical query URLs and accessible `Complete` suffixes.

- [x] **Step 2: Run the activation shell tests and confirm failure**

```bash
cd apps/web
npm run test:ci -- tests/app/activation-page.test.tsx tests/app/milestone-nav.test.tsx
```

- [x] **Step 3: Simplify the activation layout**

Keep the skip link and account control, but replace the pre-activation dashboard links with a minimal header:

```tsx
<div className="flex min-h-svh flex-col bg-background text-foreground">
  <a href="#activation-content" className="sr-only ... focus:not-sr-only">Skip to activation</a>
  <header className="border-border border-b bg-background/90 backdrop-blur">
    <div className="mx-auto flex min-h-16 w-full max-w-5xl items-center justify-between gap-3 px-4 sm:px-6">
      {/* Presvo home mark */}
      <div className="flex items-center gap-2">{accountControl}</div>
    </div>
  </header>
  {children}
</div>
```

Local mode stays visibly `Local development`; Clerk mode retains sign out.

- [x] **Step 4: Recompose `ActivationShell`**

Use:

```tsx
<main
  id="activation-content"
  className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 py-8 sm:px-6 sm:py-12"
>
  <StageRefresh stage={snapshot.stage} />
  <div className="flex flex-col gap-6">
    <div className="flex flex-col gap-3">
      <p className="text-label">Step {stepNumber} of 5</p>
      <MilestoneNav snapshot={snapshot} selectedMilestone={selectedMilestone} />
    </div>
    <section aria-labelledby={`${selectedMilestone}-title`}>{children}</section>
  </div>
</main>
```

- [x] **Step 5: Replace scrolling chips with template progress segments**

Each list item contains a `h-1.5 rounded-full` segment plus a compact label beneath it. The selected/completed segment uses `bg-primary`; future segments use `bg-muted`. Completed/current labels remain links when canonical access is allowed. Use `data-slot="activation-progress-segment"` and `aria-hidden="true"` on the visual bar while keeping state text available to assistive technology.

- [x] **Step 6: Run verification and commit**

```bash
cd apps/web
npm run test:ci -- tests/app/activation-page.test.tsx tests/app/milestone-nav.test.tsx tests/app/app-shell.test.tsx
npm run typecheck
npm run check
git add src/app/'(activation)'/activate/layout.tsx src/app/'(activation)'/activate/_components/activation-shell.tsx src/app/'(activation)'/activate/_components/milestone-nav.tsx tests/app/activation-page.test.tsx tests/app/milestone-nav.test.tsx
git commit -m "feat(web): port activation progress hierarchy"
```

---

## Task 4: Align Every Activation Milestone Surface Without Changing Commands

**Files:**

- Modify: `apps/web/src/app/(activation)/activate/page.tsx`
- Modify: `apps/web/src/app/(activation)/activate/_components/profile/profile-form.tsx`
- Modify: `apps/web/src/app/(activation)/activate/_components/number/payment-action.tsx`
- Modify: `apps/web/src/app/(activation)/activate/_components/number/provisioning-consent.tsx`
- Modify: `apps/web/src/app/(activation)/activate/_components/number/provisioning-status.tsx`
- Modify: `apps/web/src/app/(activation)/activate/_components/forwarding/forwarding-milestone.tsx`
- Modify: `apps/web/src/app/(activation)/activate/_components/launch/launch-milestone.tsx`
- Modify: `apps/web/tests/app/activation-page.test.tsx`

**Interfaces:**

- Consumes: existing milestone components and all existing action functions.
- Produces: one `data-slot="activation-step-card"` presentation boundary around the selected milestone.

- [x] **Step 1: Add failing card and hierarchy tests**

For business, number, forwarding, and launch snapshots, assert:

```tsx
const card = document.querySelector('[data-slot="activation-step-card"]');
expect(card).toHaveClass("rounded-2xl", "border", "border-border", "bg-card", "shadow-card");
expect(within(card as HTMLElement).getByRole("heading", { level: 1 })).toBeVisible();
expect(document.querySelectorAll("h1")).toHaveLength(1);
```

Assert the business autosave status and `Continue`, number consent, forwarding `Start 10-minute test`, and launch `Go live` remain inside this surface. Existing action tests continue to prove backend-confirmed state.

- [x] **Step 2: Run the activation tests and confirm failure**

```bash
cd apps/web
npm run test:ci -- tests/app/activation-page.test.tsx tests/app/profile-form.test.tsx tests/app/number-milestone.test.tsx tests/app/forwarding-milestone.test.tsx tests/app/launch-milestone.test.tsx
```

- [x] **Step 3: Add the shared step card in `page.tsx`**

Render:

```tsx
<div
  className="rounded-2xl border border-border bg-card p-5 shadow-card sm:p-7"
  data-slot="activation-step-card"
>
  <div className="flex flex-col gap-2 border-border border-b pb-5">
    <div className="flex flex-wrap items-center gap-2">
      <p className="text-label">{copy.eyebrow}</p>
      {localJourney ? <Badge variant="secondary">Local development</Badge> : null}
    </div>
    <h1 id={`${selectedMilestone}-title`} className="font-semibold text-2xl tracking-tight">{copy.title}</h1>
    <p className="max-w-2xl text-muted-foreground text-sm leading-6">{copy.description}</p>
  </div>
  <div className="pt-5">{/* selected milestone */}</div>
</div>
```

- [x] **Step 4: Normalize milestone roots**

Replace root `border-y py-6` wrappers inside profile, number, forwarding, and launch components with spacing-only classes such as `flex flex-col gap-5`. Keep intentional internal separators (`border-t`, list dividers, alert borders) and all semantic/status attributes.

Use `rounded-xl border border-border bg-muted/30 p-4 sm:p-5` for bounded review/verification groups, `tabular-nums` for phone numbers and timers, and `min-h-11` for primary actions.

Do not change:

- any server action import or call;
- pending refs or duplicate-submit guards;
- success/error copy that encodes backend truth;
- route pushes/refreshes;
- field names, validation, or autosave baselines.

- [x] **Step 5: Run all milestone behavior tests**

```bash
cd apps/web
npm run test:ci -- \
  tests/app/activation-page.test.tsx \
  tests/app/profile-form.test.tsx \
  tests/app/business-hours-editor.test.tsx \
  tests/app/carrier-confirmation.test.tsx \
  tests/app/number-milestone.test.tsx \
  tests/app/forwarding-milestone.test.tsx \
  tests/app/launch-milestone.test.tsx
npm run typecheck
npm run check
```

- [x] **Step 6: Commit**

```bash
git add apps/web/src/app/'(activation)' apps/web/tests/app
git commit -m "feat(web): align activation milestone surfaces"
```

---

## Task 5: Match Activation Loading, Error, and Narrow-Screen States

**Files:**

- Modify: `apps/web/src/app/(activation)/activate/loading.tsx`
- Modify: `apps/web/src/app/(activation)/activate/error.tsx`
- Modify: `apps/web/tests/app/activation-page.test.tsx`

**Interfaces:**

- Consumes: Phase 1 `Skeleton`, `Alert`, and `Button`.
- Produces: the same route-level loading/error exports.

- [ ] **Step 1: Add failing state-contract tests**

Assert:

```tsx
render(<ActivationLoading />);
expect(screen.getByRole("status", { name: "Loading activation" })).toHaveClass("max-w-3xl", "px-4");
expect(document.querySelector('[data-slot="activation-loading-card"]')).toHaveClass(
  "rounded-2xl",
  "border",
  "bg-card",
  "shadow-card",
);

render(<ActivationError error={new Error("offline")} reset={reset} />);
expect(screen.getByRole("main")).toHaveClass("max-w-3xl", "px-4");
expect(screen.getByRole("button", { name: "Try again" })).toHaveClass("min-h-11");
```

- [ ] **Step 2: Run the state tests and confirm failure**

```bash
cd apps/web
npm run test:ci -- tests/app/activation-page.test.tsx
```

- [ ] **Step 3: Implement geometry-matched loading and error surfaces**

Loading uses the same five progress segments and a `data-slot="activation-loading-card"` card with header/content skeletons. Error uses a centered `rounded-2xl border bg-card p-5 shadow-card sm:p-7` surface, keeps the truthful stored-progress message, and provides the existing `reset` action.

Both use `min-h-[calc(100svh-4rem)]`, `max-w-3xl`, `px-4 sm:px-6`, and safe vertical padding.

- [ ] **Step 4: Verify and commit**

```bash
cd apps/web
npm run test:ci -- tests/app/activation-page.test.tsx
npm run typecheck
npm run check
git add src/app/'(activation)'/activate/loading.tsx src/app/'(activation)'/activate/error.tsx tests/app/activation-page.test.tsx
git commit -m "feat(web): polish activation route states"
```

---

## Task 6: Add Public Entry and Activation Browser/Visual Coverage

**Files:**

- Create: `apps/web/tests/e2e/entry-activation-visual.spec.ts`
- Create: `apps/web/tests/e2e/entry-activation-visual.spec.ts-snapshots/*`
- Modify: `apps/web/tests/e2e/activation.spec.ts`
- Modify: `scripts/run-local-e2e.sh`

**Interfaces:**

- Consumes: the disposable local E2E stack and serial activation state.
- Produces: landing snapshots at 1440×1100 and 390×844, activation snapshots before mutation at both viewports, and overflow/semantic audits.

- [ ] **Step 1: Add browser assertions before snapshots**

Create a serial visual spec that runs before `activation.spec.ts`:

```ts
const CASES = [
  { name: "landing-desktop-light.png", viewport: { width: 1440, height: 1100 } },
  { name: "landing-mobile-light.png", viewport: { width: 390, height: 844 } },
  { name: "activation-desktop-light.png", viewport: { width: 1440, height: 1100 } },
  { name: "activation-mobile-light.png", viewport: { width: 390, height: 844 } },
] as const;
```

For landing, assert the missed-call h1, France-first badge, product overview region, and auth-aware `Dashboard` link in local mode. For activation, assert `Tell us about your business`, five progress segments, `Step 1 of 5`, `Local development`, owner/business/French-number controls, and no horizontal overflow.

Capture viewport screenshots with animations disabled and caret hidden.

- [ ] **Step 2: Prove responsive semantics**

Add:

```ts
expect(await page.evaluate(() => ({
  body: document.body.scrollWidth,
  root: document.documentElement.scrollWidth,
}))).toEqual({ body: viewport.width, root: viewport.width });
```

At 390px assert every progress label remains present, the form controls fit, and the primary action has a bounding height of at least 44px.

- [ ] **Step 3: Update the disposable runner order**

Run:

```text
entry-activation-visual.spec.ts
→ activation.spec.ts
→ dashboard-visual.spec.ts
→ deactivation-start.spec.ts
→ restart-resume.spec.ts
```

When `E2E_UPDATE_SNAPSHOTS=1`, pass `--update-snapshots` to both visual specs. Keep the current isolated Compose project, ports, state file, cleanup trap, and lifecycle ordering.

- [ ] **Step 4: Run with snapshot update**

```bash
PATH=/home/mo/.nvm/versions/node/v22.23.1/bin:$PATH \
E2E_UPDATE_SNAPSHOTS=1 \
bash scripts/run-local-e2e.sh
```

- [ ] **Step 5: Inspect all four new images**

Confirm:

- exact Phase 1 sage/off-white palette and system typography;
- compact public header and truthful France-first hero;
- no blue/slate legacy landing treatment;
- centered 768px activation column;
- five template-style progress segments;
- card border/radius/shadow consistency;
- no clipped mobile fields, labels, dialogs, or actions.

- [ ] **Step 6: Run the visual specs without snapshot updates**

```bash
bash scripts/run-local-e2e.sh
```

Expected: the complete lifecycle and both visual suites pass against committed images.

- [ ] **Step 7: Commit**

```bash
git add apps/web/tests/e2e/entry-activation-visual.spec.ts apps/web/tests/e2e/entry-activation-visual.spec.ts-snapshots apps/web/tests/e2e/activation.spec.ts scripts/run-local-e2e.sh
git commit -m "test(web): lock Presvo entry and activation visuals"
```

---

## Task 7: Run the Phase 2 Production Gate

**Files:**

- Modify: `docs/superpowers/plans/2026-07-29-presvo-entry-activation.md`

- [ ] **Step 1: Scan for migration regressions**

```bash
rg -n 'SonicWaveformCanvas|slate-|transition-all|TODO|FIXME|hardcoded.*date' \
  apps/web/src/app/page.tsx \
  apps/web/src/components/landing \
  apps/web/src/app/'(auth)' \
  apps/web/src/app/'(activation)'
```

Expected: no blue/slate legacy entry styles, no `transition-all`, and no new TODO/FIXME markers in the changed surfaces.

- [ ] **Step 2: Run the full web verification**

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

- [ ] **Step 3: Verify the phase invariants**

Confirm from tests and browser output:

- returning owners resume the server-authoritative milestone;
- locked milestones cannot be opened from URL or progress navigation;
- checkout is not provisioning consent;
- provisioning is not reported ready before `provider_ready`;
- forwarding uses only unanswered, busy, and unreachable conditions;
- verification windows use server time;
- go-live redirects only after backend acknowledgement;
- restart/resume still preserves durable state.

- [ ] **Step 4: Mark this plan complete and commit**

Mark every Phase 2 checkbox complete, then:

```bash
git add docs/superpowers/plans/2026-07-29-presvo-entry-activation.md
git commit -m "docs: complete Presvo entry and activation phase"
```

---

## Phase 2 Completion Checklist

- [ ] Public entry uses the exact Presvo design system and France-first truth.
- [ ] Signed-out and signed-in calls to action keep their correct production routes.
- [ ] Hosted auth is visually contained without changing Clerk/local-mode gates.
- [ ] Activation uses the centered template hierarchy and five visible milestones.
- [ ] All milestones preserve server-authoritative state and existing mutation boundaries.
- [ ] Loading, error, retry, provisioning, verification, and go-live states are responsive and truthful.
- [ ] Landing and activation pass desktop/mobile visual and overflow checks.
- [ ] The full activation, deactivation, and restart/resume browser proof passes.
- [ ] Check, typecheck, all unit tests, production build, and Playwright pass.
