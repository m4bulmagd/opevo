# Presvo UI Foundation and Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install the approved Presvo visual contract in `apps/web` and replace the authenticated navigation shell with the template-matched desktop sidebar, compact header, mobile sheet, functional search, and explicit Preview notifications.

**Architecture:** Keep the existing authenticated dashboard layout and server data loading. Restyle shared CSS/primitives, centralize capability-aware route metadata, and compose server-owned account/auth controls with small client islands for pathname state, the mobile sheet, and local notification state. Existing route children remain unchanged in this phase.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, Tailwind CSS 4, Base UI/shadcn, Vitest, Testing Library, Playwright.

## Global Constraints

- Read `docs/superpowers/specs/2026-07-29-presvo-ui-migration-design.md` before implementation.
- Use `Presvo_frontend/src/styles.css` and `Presvo_frontend/src/components/layout/*` only as visual references.
- Do not modify `Presvo_frontend`.
- Do not alter dashboard API calls, authentication gates, or account lifecycle logic.
- Maintain a visible skip link and 44px minimum touch targets for primary mobile actions.
- Do not use `transition-all`.
- Keep the existing reduced-motion override.
- Follow each red-green-refactor loop and commit only after its listed checks pass.

---

## File Map

### Modify

- `apps/web/src/app/globals.css`
- `apps/web/src/app/layout.tsx`
- `apps/web/src/components/ui/button.tsx`
- `apps/web/src/components/ui/input.tsx`
- `apps/web/src/components/ui/card.tsx`
- `apps/web/src/components/ui/badge.tsx`
- `apps/web/src/components/product/product-surface.tsx`
- `apps/web/src/components/product/page-intro.tsx`
- `apps/web/src/navigation/dashboard-items.ts`
- `apps/web/src/components/workspace/command-rail.tsx`
- `apps/web/src/components/workspace/workspace-navigation.tsx`
- `apps/web/src/components/workspace/workspace-header.tsx`
- `apps/web/src/components/workspace/workspace-shell.tsx`
- `apps/web/tests/styles/theme-tokens.test.ts`
- `apps/web/tests/app/app-shell.test.tsx`
- `apps/web/tests/e2e/dashboard-visual.spec.ts`

### Create

- `apps/web/src/lib/types/capability.ts`
- `apps/web/src/components/product/capability-badge.tsx`
- `apps/web/src/components/workspace/mobile-workspace-navigation.tsx`
- `apps/web/src/components/workspace/workspace-notifications-preview.tsx`
- `apps/web/tests/components/design-system-primitives.test.tsx`
- `apps/web/tests/navigation/dashboard-items.test.ts`

### Delete after import verification

- `apps/web/src/lib/fonts/registry.ts`
- `apps/web/src/components/workspace/mobile-command-bar.tsx`
- `apps/web/src/components/workspace/mobile-more-sheet.tsx`

---

## Task 1: Lock the Exact Visual Token and Typography Contract

**Files:**

- Modify: `apps/web/tests/styles/theme-tokens.test.ts`
- Modify: `apps/web/src/app/globals.css`
- Modify: `apps/web/src/app/layout.tsx`
- Modify: `apps/web/src/components/workspace/workspace-shell.tsx`
- Modify: `apps/web/tests/app/app-shell.test.tsx`
- Delete: `apps/web/src/lib/fonts/registry.ts`

- [x] **Step 1: Add failing exact-value token tests**

Add a declaration collector to `theme-tokens.test.ts`:

```ts
function declarationsForSelector(root: postcss.Root, selector: string) {
  const values = new Map<string, string>();
  root.walkRules(selector, (rule) => {
    rule.walkDecls((declaration) => values.set(declaration.prop, declaration.value));
  });
  return values;
}
```

Assert the approved light values:

```ts
expect(light.get("--radius")).toBe("0.875rem");
expect(light.get("--background")).toBe("oklch(0.976 0.004 120)");
expect(light.get("--foreground")).toBe("oklch(0.245 0.012 150)");
expect(light.get("--card")).toBe("oklch(1 0 0)");
expect(light.get("--primary")).toBe("oklch(0.42 0.045 152)");
expect(light.get("--border")).toBe("oklch(0.923 0.006 135)");
expect(light.get("--shadow-card")).toBe(
  "0 1px 2px oklch(0.245 0.012 150 / 0.04), 0 8px 24px oklch(0.245 0.012 150 / 0.04)",
);
expect(light.get("--shadow-raised")).toBe(
  "0 2px 4px oklch(0.245 0.012 150 / 0.05), 0 16px 40px oklch(0.245 0.012 150 / 0.07)",
);
```

Assert the template dark values for background, foreground, card, primary, border, shadow-card, and shadow-raised. Assert that `--font-sans` is the literal system stack and that reduced-motion durations remain `["0s", "0s"]`.

- [x] **Step 2: Run the token test and confirm it fails**

```bash
cd apps/web
npm run test:ci -- tests/styles/theme-tokens.test.ts
```

Expected: failures show the current warm-canvas/cobalt values and font variables.

- [x] **Step 3: Port the template tokens without dropping production aliases**

Replace the light and dark color, radius, and shadow values in `globals.css` with the exact values in `Presvo_frontend/src/styles.css`.

Keep these production compatibility aliases:

```css
--surface: var(--card);
--surface-elevated: var(--popover);
--surface-subtle: var(--muted);
--text-primary: var(--foreground);
--text-secondary: var(--muted-foreground);
--text-tertiary: var(--muted-foreground);
--destructive-subtle: color-mix(in oklch, var(--destructive) 12%, transparent);
--success-subtle: color-mix(in oklch, var(--success) 12%, transparent);
--warning-subtle: color-mix(in oklch, var(--warning) 14%, transparent);
```

Register exact typography and utilities:

```css
@theme inline {
  --font-sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  --shadow-card: var(--shadow-card);
  --shadow-raised: var(--shadow-raised);
}

@utility text-label {
  font-size: 0.6875rem;
  line-height: 1rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-muted-foreground);
}

@utility surface-card {
  background-color: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-card);
}
```

Retain the existing semantic motion durations and the complete reduced-motion media query.

- [x] **Step 4: Remove active Google-font dependencies from layout code**

In `app/layout.tsx`, remove `publicFontVariables` and render:

```tsx
<body className="min-h-screen font-sans antialiased">{bodyContent}</body>
```

In `workspace-shell.tsx`, remove `authenticatedFontVariable` and its Figtree class. Delete `lib/fonts/registry.ts` only after:

```bash
rg -n "fonts/registry|publicFontVariables|authenticatedFontVariable" apps/web/src apps/web/tests
```

returns no imports.

Remove the `@/lib/fonts/registry` mock from `app-shell.test.tsx` and replace the
old Figtree assertion with:

```ts
const workspaceShell = document.querySelector('[data-slot="workspace-shell"]');
expect(workspaceShell).toHaveClass("font-sans");
expect(workspaceShell).not.toHaveClass(
  "font-[family-name:var(--font-figtree)]",
);
```

- [x] **Step 5: Run targeted verification**

```bash
cd apps/web
npm run test:ci -- tests/styles/theme-tokens.test.ts tests/app/app-shell.test.tsx
npm run typecheck
npm run check
```

Expected: all pass.

- [x] **Step 6: Commit**

```bash
git add apps/web/src/app/globals.css apps/web/src/app/layout.tsx apps/web/src/components/workspace/workspace-shell.tsx apps/web/src/lib/fonts/registry.ts apps/web/tests/styles/theme-tokens.test.ts apps/web/tests/app/app-shell.test.tsx
git commit -m "feat(web): install Presvo visual tokens"
```

---

## Task 2: Align Shared Primitives and Add Capability Status

**Files:**

- Create: `apps/web/tests/components/design-system-primitives.test.tsx`
- Create: `apps/web/src/lib/types/capability.ts`
- Create: `apps/web/src/components/product/capability-badge.tsx`
- Modify: `apps/web/src/components/ui/button.tsx`
- Modify: `apps/web/src/components/ui/input.tsx`
- Modify: `apps/web/src/components/ui/card.tsx`
- Modify: `apps/web/src/components/ui/badge.tsx`
- Modify: `apps/web/src/components/product/product-surface.tsx`
- Modify: `apps/web/src/components/product/page-intro.tsx`

- [x] **Step 1: Add failing semantic and class-contract tests**

Cover:

```tsx
render(
  <>
    <Button>Save</Button>
    <Input aria-label="Company" />
    <Card aria-label="Card" />
    <CapabilityBadge status="preview" />
  </>,
);

expect(screen.getByRole("button", { name: "Save" })).not.toHaveClass("transition-all");
expect(screen.getByLabelText("Card")).toHaveClass("border", "border-border", "shadow-card");
expect(screen.getByText("Preview")).toBeVisible();
```

Also assert:

- Preview renders literal `Preview` and `data-capability-status="preview"`;
- Live renders `Live`;
- unavailable renders `Unavailable`;
- `PageIntro` uses `text-xl sm:text-2xl`;
- `ProductSurface` default tone uses `rounded-xl border border-border bg-card shadow-card`.

- [x] **Step 2: Run the primitive test and confirm it fails**

```bash
cd apps/web
npm run test:ci -- tests/components/design-system-primitives.test.tsx
```

- [x] **Step 3: Add the capability interface and badge**

`lib/types/capability.ts`:

```ts
export type CapabilityStatus = "live" | "preview" | "unavailable";
```

`components/product/capability-badge.tsx`:

```tsx
import { Badge } from "@/components/ui/badge";
import type { CapabilityStatus } from "@/lib/types/capability";
import { cn } from "@/lib/utils";

const LABEL: Record<CapabilityStatus, string> = {
  live: "Live",
  preview: "Preview",
  unavailable: "Unavailable",
};

export function CapabilityBadge({
  className,
  status,
}: {
  className?: string;
  status: CapabilityStatus;
}) {
  return (
    <Badge
      className={cn(
        status === "preview" && "border-primary/20 bg-primary-soft text-accent-foreground",
        status === "live" && "border-success/20 bg-success/10 text-success",
        status === "unavailable" && "border-border bg-muted text-muted-foreground",
        className,
      )}
      data-capability-status={status}
      variant="outline"
    >
      {LABEL[status]}
    </Badge>
  );
}
```

- [x] **Step 4: Match the template's shared geometry**

Use scoped transitions on buttons and badges:

```ts
"transition-[color,background-color,border-color,box-shadow,transform]"
```

Update:

- buttons and inputs to template-mapped `rounded-md`, compact 36px default height, and existing focus ring;
- cards to `rounded-xl border border-border bg-card shadow-card`, removing the simulated `ring-foreground/10`;
- badges to `rounded-full`;
- default `ProductSurface` to `rounded-xl border-border bg-card shadow-card`;
- `PageIntro` to a 20px mobile / 24px desktop title and tighter template header spacing.

Do not change public component props.

- [x] **Step 5: Run targeted verification**

```bash
cd apps/web
npm run test:ci -- tests/components/design-system-primitives.test.tsx tests/components/product-components.test.tsx
npm run typecheck
npm run check
```

- [x] **Step 6: Commit**

```bash
git add apps/web/src/components/ui apps/web/src/components/product apps/web/src/lib/types/capability.ts apps/web/tests/components/design-system-primitives.test.tsx apps/web/tests/components/product-components.test.tsx
git commit -m "feat(web): align Presvo design primitives"
```

---

## Task 3: Make Dashboard Navigation Capability-Aware and Grouped

**Files:**

- Create: `apps/web/tests/navigation/dashboard-items.test.ts`
- Modify: `apps/web/src/navigation/dashboard-items.ts`

- [x] **Step 1: Add failing navigation-model tests**

Assert this exact production model:

```ts
expect(dashboardGroups("Léa").map((group) => group.label)).toEqual(["Main", "Account"]);
expect(dashboardGroups("Léa").flatMap((group) => group.items.map((item) => ({
  href: item.href,
  status: item.status,
  title: item.title,
})))).toEqual([
  { href: "/dashboard", status: "live", title: "Overview" },
  { href: "/dashboard/live-call", status: "preview", title: "Live call" },
  { href: "/dashboard/calls", status: "live", title: "Calls" },
  { href: "/dashboard/agent", status: "live", title: "Léa" },
  { href: "/dashboard/billing", status: "live", title: "Usage & Billing" },
  { href: "/dashboard/account", status: "live", title: "Account" },
]);
```

Keep active-route assertions for exact `/dashboard`, nested call detail, and live-call isolation.

- [x] **Step 2: Run the model test and confirm it fails**

```bash
cd apps/web
npm run test:ci -- tests/navigation/dashboard-items.test.ts
```

- [x] **Step 3: Implement the typed grouped model**

Use:

```ts
export type NavGroup = {
  id: "account" | "main";
  label: "Account" | "Main";
  items: NavItem[];
};

export type NavItem = {
  title: string;
  href: string;
  icon: LucideIcon;
  status: CapabilityStatus;
};
```

Export `dashboardGroups(agentName)`. Keep `dashboardItems(agentName)` temporarily
returning the current five-item legacy set, including the existing `Billing`
label, so the old mobile bar remains green until Task 5. Mark it with a removal
comment that names Task 5; do not use it in new code.

- [x] **Step 4: Run targeted verification**

```bash
cd apps/web
npm run test:ci -- tests/navigation/dashboard-items.test.ts
npm run typecheck
```

Expected: both commands pass and the unchanged shell remains compatible with
`dashboardItems`.

- [x] **Step 5: Commit**

```bash
git add apps/web/src/navigation/dashboard-items.ts apps/web/tests/navigation/dashboard-items.test.ts
git commit -m "feat(web): model dashboard capability navigation"
```

---

## Task 4: Port the Desktop Sidebar

**Files:**

- Modify: `apps/web/tests/app/app-shell.test.tsx`
- Modify: `apps/web/src/components/workspace/command-rail.tsx`
- Modify: `apps/web/src/components/workspace/workspace-navigation.tsx`

- [x] **Step 1: Replace rail-specific tests with template sidebar expectations**

The desktop test must assert:

```tsx
const sidebar = screen.getByRole("complementary", { name: "Workspace sidebar" });
expect(sidebar).toHaveClass("w-64", "rounded-2xl", "border", "shadow-card");

const navigation = within(sidebar).getByRole("navigation", { name: "Workspace navigation" });
expect(within(navigation).getByText("Main")).toBeVisible();
expect(within(navigation).getByText("Account")).toBeVisible();
expect(within(navigation).getByRole("link", { name: /Live call/ })).toHaveTextContent("Preview");
expect(within(navigation).getByRole("link", { name: "Overview" })).toHaveAttribute(
  "aria-current",
  "page",
);
```

Retain tests for normalized agent names, nested active routes, account lifecycle banner, local auth badge, and sign-out rendering.

- [x] **Step 2: Run the shell test and confirm it fails**

```bash
cd apps/web
npm run test:ci -- tests/app/app-shell.test.tsx
```

- [x] **Step 3: Rebuild `CommandRail` as the template sidebar**

Keep the exported component name during this phase to minimize unrelated churn. Render:

```tsx
<aside aria-label="Workspace sidebar" className="hidden w-64 shrink-0 lg:block">
  <div className="sticky top-4 flex h-[calc(100svh-2rem)] flex-col overflow-hidden rounded-2xl border border-sidebar-border bg-sidebar shadow-card">
    {/* Presvo brand */}
    {/* grouped WorkspaceNavigation */}
    {/* agent runtime/account card */}
  </div>
</aside>
```

Brand geometry:

- outer content `p-4`;
- logo mark `size-9 rounded-xl bg-primary`;
- `Presvo` at 14px semibold;
- `AI Call Assistant` at 12px muted.

Runtime card geometry:

- `rounded-xl border border-sidebar-border bg-card px-3 py-2.5`;
- agent name and runtime state remain sourced from the existing server-owned props;
- no fabricated email or customer identity is added.

- [x] **Step 4: Port the desktop branch of `WorkspaceNavigation`**

Use `dashboardGroups` for the desktop branch and remove its rail tooltip and
layout marker. Keep the existing mobile branch and its `dashboardItems` input
unchanged until Task 5 so this commit remains green. Use `usePathname` for
`aria-current` and active classes.

Each group renders its 11px `text-label` heading. Each link uses:

```ts
"flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm outline-none transition-colors hover:bg-sidebar-accent focus-visible:ring-3 focus-visible:ring-sidebar-ring/50"
```

Active links use `bg-sidebar-accent text-sidebar-accent-foreground`. Preview items render `CapabilityBadge` after the title.

- [x] **Step 5: Run targeted verification**

```bash
cd apps/web
npm run test:ci -- tests/app/app-shell.test.tsx tests/navigation/dashboard-items.test.ts
npm run typecheck
npm run check
```

Expected: both new desktop assertions and the unchanged legacy mobile assertions pass.

- [x] **Step 6: Commit**

```bash
git add apps/web/src/components/workspace/command-rail.tsx apps/web/src/components/workspace/workspace-navigation.tsx apps/web/tests/app/app-shell.test.tsx
git commit -m "feat(web): port Presvo desktop sidebar"
```

---

## Task 5: Replace Mobile Bottom Navigation with a Focus-Managed Sheet

**Files:**

- Create: `apps/web/src/components/workspace/mobile-workspace-navigation.tsx`
- Modify: `apps/web/src/components/workspace/workspace-header.tsx`
- Modify: `apps/web/src/components/workspace/workspace-shell.tsx`
- Modify: `apps/web/tests/app/app-shell.test.tsx`
- Delete: `apps/web/src/components/workspace/mobile-command-bar.tsx`
- Delete: `apps/web/src/components/workspace/mobile-more-sheet.tsx`

- [x] **Step 1: Add failing mobile navigation tests**

Use `userEvent` and assert:

```tsx
const trigger = screen.getByRole("button", { name: "Open navigation" });
await user.click(trigger);

const dialog = screen.getByRole("dialog", { name: "Workspace navigation" });
expect(within(dialog).getByRole("link", { name: /Live call/ })).toHaveTextContent("Preview");
expect(within(dialog).getByRole("link", { name: "Overview" })).toBeFocused();

await user.keyboard("{Escape}");
expect(dialog).not.toBeInTheDocument();
expect(trigger).toHaveFocus();
```

Mock route navigation and verify selecting a link calls the sheet close handler.

- [x] **Step 2: Run the mobile shell tests and confirm they fail**

```bash
cd apps/web
npm run test:ci -- tests/app/app-shell.test.tsx
```

- [x] **Step 3: Implement the mobile navigation island**

`mobile-workspace-navigation.tsx` is a client component with controlled `open` state. Compose the existing `Sheet`, a 44px menu button, `SheetTitle`, and the same grouped route data used by the desktop sidebar.

Required behavior:

- side is `left`;
- content is `w-72 p-0`;
- first destination receives focus on open;
- selecting any destination sets `open` to false;
- Escape closes and restores trigger focus through the primitive;
- preview status remains visible;
- bottom padding uses `max(1rem, env(safe-area-inset-bottom))`.

- [x] **Step 4: Compose it into the compact header and remove the bottom bar**

Add the mobile trigger to `WorkspaceHeader`. Remove `MobileCommandBar` from `WorkspaceShell` and remove bottom-navigation padding:

```tsx
<div className="min-h-svh lg:flex lg:gap-4 lg:p-4" data-slot="workspace-content">
  <CommandRail ... />
  <div className="flex min-w-0 flex-1 flex-col gap-4">
    <WorkspaceHeader ... />
    <main className="min-w-0 flex-1 px-4 pb-10 lg:px-0" id="workspace-main">
      ...
    </main>
  </div>
</div>
```

Delete the two old mobile navigation files only after:

```bash
rg -n "MobileCommandBar|MobileMoreSheet|variant=\"mobile\"" apps/web/src apps/web/tests
```

returns no imports or obsolete assertions.

Now remove the legacy mobile branch, Motion imports, tooltip imports, variant
prop, and temporary `dashboardItems` compatibility helper from
`WorkspaceNavigation`/`dashboard-items.ts`. The remaining
`WorkspaceNavigation` accepts `agentName`, optional `onNavigate`, and an
optional first-destination ref so desktop and sheet navigation render the same
groups and status badges.

- [x] **Step 5: Run targeted verification**

```bash
cd apps/web
npm run test:ci -- tests/app/app-shell.test.tsx
npm run typecheck
npm run check
```

- [x] **Step 6: Commit**

```bash
git add apps/web/src/components/workspace apps/web/tests/app/app-shell.test.tsx
git commit -m "feat(web): add responsive Presvo navigation"
```

---

## Task 6: Add Functional Shell Search and Preview Notifications

**Files:**

- Create: `apps/web/src/components/workspace/workspace-notifications-preview.tsx`
- Modify: `apps/web/src/components/workspace/workspace-header.tsx`
- Modify: `apps/web/tests/app/app-shell.test.tsx`

- [ ] **Step 1: Add failing header interaction tests**

Assert the search contract:

```tsx
const search = screen.getByRole("searchbox", { name: "Search calls" });
await user.type(search, "Dupont mobile");
await user.click(screen.getByRole("button", { name: "Search" }));
expect(search.closest("form")).toHaveAttribute("action", "/dashboard/calls");
expect(search).toHaveAttribute("name", "q");
```

Assert notifications:

```tsx
await user.click(screen.getByRole("button", { name: /Notifications/ }));
const panel = screen.getByRole("dialog", { name: "Notifications Preview" });
expect(within(panel).getByText("Preview")).toBeVisible();
expect(within(panel).getByText(/reset on reload/i)).toBeVisible();
await user.click(within(panel).getByRole("button", { name: "Mark all read" }));
expect(screen.getByRole("button", { name: "Notifications (0 unread)" })).toBeVisible();
```

Mock `global.fetch` and assert no request occurs during notification interactions.

- [ ] **Step 2: Run the shell test and confirm it fails**

```bash
cd apps/web
npm run test:ci -- tests/app/app-shell.test.tsx
```

- [ ] **Step 3: Implement server-compatible search**

In `WorkspaceHeader`, render a GET form:

```tsx
<form action="/dashboard/calls" className="hidden min-w-0 justify-center md:flex" role="search">
  <div className="relative w-full max-w-sm">
    <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
    <Input aria-label="Search calls" className="pl-9" name="q" placeholder="Search calls, callers or notes" type="search" />
  </div>
  <button className="sr-only" type="submit">Search</button>
</form>
```

This phase establishes navigation only. Phase 3 binds the route query to backend filtering.

- [ ] **Step 4: Implement local Preview notifications**

Create a client component using the existing Popover. Seed three stable France-first operational examples in the component module. Keep `useState` inside the component; marking one/all read changes only local state.

The popover heading must be `Notifications Preview`, render `CapabilityBadge status="preview"`, and state:

`Preview interactions are local and reset on reload.`

Do not import an API client, server action, or billing/telephony module.

- [ ] **Step 5: Complete the template header composition**

Header contract:

- sticky at top with `z-20`;
- three-column layout;
- `border-b bg-background/90 px-4 py-3 backdrop-blur` on mobile;
- `lg:rounded-2xl lg:border lg:bg-card lg:shadow-card`;
- mobile menu left;
- centered search at `md` and above;
- notification Preview, call-history outline action, and live-call primary action on the right;
- icon-only mobile actions have explicit accessible labels.

- [ ] **Step 6: Run targeted verification**

```bash
cd apps/web
npm run test:ci -- tests/app/app-shell.test.tsx
npm run typecheck
npm run check
```

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/workspace/workspace-header.tsx apps/web/src/components/workspace/workspace-notifications-preview.tsx apps/web/tests/app/app-shell.test.tsx
git commit -m "feat(web): add shell search and notification preview"
```

---

## Task 7: Lock the Responsive Shell with Browser and Visual Tests

**Files:**

- Modify: `apps/web/tests/e2e/dashboard-visual.spec.ts`
- Update after visual inspection: `apps/web/tests/e2e/dashboard-visual.spec.ts-snapshots/*`

- [ ] **Step 1: Change the browser assertions before snapshots**

Replace mobile bottom-bar helpers with:

```ts
async function openAndVerifyMobileNavigation(page: Page) {
  const trigger = page.getByRole("button", { name: "Open navigation" });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Workspace navigation" });
  await expect(dialog.getByRole("link", { name: "Overview" })).toBeFocused();
  await expect(dialog.getByRole("link", { name: /Live call/ })).toContainText("Preview");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
}
```

For desktop, assert `Workspace sidebar` is visible and exactly 256 CSS pixels wide. For mobile, assert it is hidden and no bottom navigation exists.

Keep:

- 1440×1100 and 390×844 viewports;
- light/dark theme resolution;
- reduced-motion audit;
- horizontal overflow audit;
- existing live dashboard content assertions.

- [ ] **Step 2: Run semantic Playwright assertions and confirm old assumptions fail**

Use the repository's documented local E2E server command, then:

```bash
cd apps/web
npx playwright test tests/e2e/dashboard-visual.spec.ts --grep-invert "matches"
```

Expected before implementation is complete: old bottom-navigation or rail assertions fail. After Tasks 1–6: semantic tests pass.

- [ ] **Step 3: Capture candidate snapshots**

```bash
cd apps/web
npx playwright test tests/e2e/dashboard-visual.spec.ts --update-snapshots
```

- [ ] **Step 4: Inspect all four images**

Verify each rendered screenshot:

- exact sage/off-white palette;
- system typography;
- 256px rounded desktop sidebar and 16px gap;
- compact rounded header;
- card border/shadow consistency;
- mobile header and content have no clipping;
- no stale dark command rail or bottom navigation;
- dark theme remains legible and geometrically identical.

Reject and fix code if any discrepancy is visible; do not normalize it by masking stable UI.

- [ ] **Step 5: Run the full Phase 1 verification**

```bash
cd apps/web
npm run check
npm run typecheck
npm run test:ci
npm run build
npx playwright test tests/e2e/dashboard-visual.spec.ts
```

Expected: all commands exit 0.

- [ ] **Step 6: Scan for obsolete shell code and placeholders**

```bash
rg -n "MobileCommandBar|MobileMoreSheet|variant=\"mobile\"|font-figtree|font-inter|transition-all|TODO|FIXME" apps/web/src apps/web/tests
```

Expected: no obsolete shell/font imports, no `transition-all` in the changed primitive files, and no new TODO/FIXME markers.

- [ ] **Step 7: Commit**

```bash
git add apps/web/tests/e2e/dashboard-visual.spec.ts apps/web/tests/e2e/dashboard-visual.spec.ts-snapshots
git commit -m "test(web): lock Presvo shell visuals"
```

---

## Phase 1 Completion Checklist

- [ ] Exact light and dark template tokens are covered by tests.
- [ ] The UI uses the approved system font stack.
- [ ] Shared primitives use scoped transitions and exact card geometry.
- [ ] Capability status is typed and Preview is visibly labeled.
- [ ] Desktop sidebar, compact header, and mobile sheet match the template hierarchy.
- [ ] Search routes to `/dashboard/calls?q=<term>`.
- [ ] Notifications are local-only and visibly Preview.
- [ ] Existing authentication and account lifecycle tests remain green.
- [ ] Unit, type, lint/check, build, browser, overflow, reduced-motion, and visual tests pass.
