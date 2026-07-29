# Workspace Header Caller Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorder the authenticated desktop header, show a server-rendered active-caller or ready-state summary, and rename the Agent configuration destination consistently.

**Architecture:** Keep call lookup in the server dashboard layout, pass a small nullable caller-identity value through `WorkspaceShell`, and isolate identity fallback rendering in a focused `WorkspaceCallerStatus` component. Keep the navigation destination model static while retaining the normalized configured agent name for runtime copy and page context.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, Tailwind CSS 4, Vitest, Testing Library

## Global Constraints

- Wide desktop order is `Search → Caller status → Live call → Notifications → Call history → Account/environment → Theme`.
- Active caller fallback order is matched contact name, formatted phone number, then `Unknown caller`.
- Idle copy is `No active call` with `{agent_name} is ready`.
- Lookup failure degrades to the ready state without suppressing authenticated content.
- `/dashboard/agent` is labelled `Agent` in desktop and mobile navigation.
- Keep the configured agent name in header status, sidebar runtime status, Agent page context, and other runtime descriptions.
- Do not add contacts storage, upload, matching, polling, WebSockets, or realtime claims.
- Preserve existing routes, Preview labels, account controls, keyboard behavior, accessible names, and minimum touch targets.
- Do not modify `Presvo_frontend`.

---

## File Map

- Create `apps/web/src/components/workspace/workspace-caller-status.tsx`: own caller-name, number, unknown, and idle presentation.
- Create `apps/web/tests/components/workspace-caller-status.test.tsx`: lock caller fallback copy and avatar behavior.
- Modify `apps/web/src/navigation/dashboard-items.ts`: make the Agent route title static.
- Modify `apps/web/src/components/workspace/workspace-navigation.tsx`: remove the obsolete configured-name dependency.
- Modify `apps/web/src/components/workspace/mobile-workspace-navigation.tsx`: use the static workspace navigation model.
- Modify `apps/web/src/components/workspace/command-rail.tsx`: retain configured name only for runtime status.
- Modify `apps/web/src/components/workspace/workspace-header.tsx`: compose the requested header order and caller status.
- Modify `apps/web/src/components/workspace/workspace-shell.tsx`: pass caller identity into the header.
- Modify `apps/web/src/app/(app)/dashboard/layout.tsx`: load the newest in-progress call and map it into header identity.
- Modify `apps/web/tests/navigation/dashboard-items.test.ts`: lock the fixed Agent destination.
- Modify `apps/web/tests/app/app-shell.test.tsx`: lock integrated lookup, fallback, header order, and navigation behavior.

### Task 1: Make the Agent Navigation Label Static

**Files:**

- Modify: `apps/web/tests/navigation/dashboard-items.test.ts`
- Modify: `apps/web/tests/app/app-shell.test.tsx`
- Modify: `apps/web/src/navigation/dashboard-items.ts`
- Modify: `apps/web/src/components/workspace/workspace-navigation.tsx`
- Modify: `apps/web/src/components/workspace/mobile-workspace-navigation.tsx`
- Modify: `apps/web/src/components/workspace/command-rail.tsx`
- Modify: `apps/web/src/components/workspace/workspace-header.tsx`

**Interfaces:**

- Consumes: the fixed `/dashboard/agent` route.
- Produces: `dashboardGroups(): NavGroup[]` and `WorkspaceNavigation` with no `agentName` prop.

- [ ] **Step 1: Write the failing static-label tests**

In `apps/web/tests/navigation/dashboard-items.test.ts`, change the reflected
factory signature and invocation to:

```ts
type DashboardGroupFactory = () => Array<{
  id: string;
  label: string;
  items: Array<{ href: string; status: string; title: string }>;
}>;

const groups = dashboardGroups?.();
```

Change the Agent route expectation to:

```ts
{ href: "/dashboard/agent", status: "live", title: "Agent" },
```

In `apps/web/tests/app/app-shell.test.tsx`:

- render `<WorkspaceNavigation />` without `agentName`;
- change the complete destination-label expectation from `"Ava"` to
  `"Agent"`;
- replace the blank-name navigation test with:

```ts
it("keeps the fixed Agent destination separate from the normalized runtime name", async () => {
  await renderDashboardLayout({ agentName: " \n\t " });

  expect(within(desktopNavigation()).getByRole("link", { name: "Agent" })).toHaveAttribute(
    "href",
    "/dashboard/agent",
  );
  expect(
    screen.getByRole("group", {
      name: "Agent runtime: Receptionist, Enabled",
    }),
  ).toBeInTheDocument();

  const { dialog } = await openMobileNavigation();
  expect(within(dialog).getByRole("link", { name: "Agent" })).toHaveAttribute(
    "href",
    "/dashboard/agent",
  );
});
```

Remove the two tests that require the configured name to be the desktop or
mobile navigation label. The existing runtime-card test continues to protect
long configured-name visibility and truncation.

- [ ] **Step 2: Run the focused tests and verify that they fail**

Run:

```bash
cd apps/web
npm run test:ci -- tests/navigation/dashboard-items.test.ts tests/app/app-shell.test.tsx
```

Expected: navigation expectations fail because the Agent destination still
uses the configured name.

- [ ] **Step 3: Implement the static navigation model**

In `apps/web/src/navigation/dashboard-items.ts`, keep
`normalizeAgentName(agentName)` unchanged for runtime consumers. Change the
group factory and Agent item to:

```ts
export function dashboardGroups(): NavGroup[] {
  return [
    {
      id: "main",
      label: "Main",
      items: [
        { title: "Overview", href: "/dashboard", icon: House, status: "live" },
        { title: "Live call", href: "/dashboard/live-call", icon: Radio, status: "preview" },
        { title: "Calls", href: "/dashboard/calls", icon: Phone, status: "live" },
        { title: "Agent", href: "/dashboard/agent", icon: Bot, status: "live" },
      ],
    },
    {
      id: "account",
      label: "Account",
      items: [
        { title: "Usage & Billing", href: "/dashboard/billing", icon: CreditCard, status: "live" },
        { title: "Account", href: "/dashboard/account", icon: UserRound, status: "live" },
      ],
    },
  ];
}
```

In `workspace-navigation.tsx`, remove `agentName` from
`WorkspaceNavigationProps`, the function parameters, and the factory call:

```tsx
{dashboardGroups().map((group, groupIndex) => (
```

In `mobile-workspace-navigation.tsx`, change the export and child call to:

```tsx
export function MobileWorkspaceNavigation() {
```

```tsx
<WorkspaceNavigation
  ariaLabel="Mobile workspace destinations"
  firstDestinationRef={firstDestinationRef}
  onNavigate={() => setOpen(false)}
/>
```

In `command-rail.tsx`, retain `agentName` for the runtime fieldset and render:

```tsx
<WorkspaceNavigation />
```

In `workspace-header.tsx`, retain `agentName` for later caller-status copy and
render:

```tsx
<MobileWorkspaceNavigation />
```

- [ ] **Step 4: Run focused verification**

Run:

```bash
cd apps/web
npm run test:ci -- tests/navigation/dashboard-items.test.ts tests/app/app-shell.test.tsx
npm run typecheck
```

Expected: both test files and TypeScript pass.

- [ ] **Step 5: Commit the navigation change**

```bash
git add apps/web/src/navigation/dashboard-items.ts apps/web/src/components/workspace/workspace-navigation.tsx apps/web/src/components/workspace/mobile-workspace-navigation.tsx apps/web/src/components/workspace/command-rail.tsx apps/web/src/components/workspace/workspace-header.tsx apps/web/tests/navigation/dashboard-items.test.ts apps/web/tests/app/app-shell.test.tsx
git commit -m "fix(web): use a stable Agent navigation label"
```

### Task 2: Add the Caller Status Presentation Boundary

**Files:**

- Create: `apps/web/src/components/workspace/workspace-caller-status.tsx`
- Create: `apps/web/tests/components/workspace-caller-status.test.tsx`

**Interfaces:**

- Produces:

```ts
export type WorkspaceCallerIdentity = Readonly<{
  contactName: string | null;
  phoneNumber: string | null;
}>;

export function WorkspaceCallerStatus(props: {
  agentName: string;
  caller: WorkspaceCallerIdentity | null;
}): React.JSX.Element;
```

- [ ] **Step 1: Write the failing caller fallback tests**

Create `apps/web/tests/components/workspace-caller-status.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WorkspaceCallerStatus } from "@/components/workspace/workspace-caller-status";

describe("WorkspaceCallerStatus", () => {
  it("renders a stable ready state when no call is active", () => {
    render(<WorkspaceCallerStatus agentName="Ava" caller={null} />);

    expect(screen.getByText("No active call")).toBeVisible();
    expect(screen.getByText("Ava is ready")).toBeVisible();
    expect(screen.getByTestId("caller-status-icon")).toBeInTheDocument();
  });

  it("prefers a normalized matched contact name and derives initials", () => {
    render(
      <WorkspaceCallerStatus
        agentName="Ava"
        caller={{ contactName: "  Sophie Bernard  ", phoneNumber: "+33612345678" }}
      />,
    );

    expect(screen.getByText("Sophie Bernard")).toBeVisible();
    expect(screen.getByText("SB")).toBeVisible();
    expect(screen.getByText("Ava is answering this call")).toBeVisible();
  });

  it("falls back to the caller number without inventing initials", () => {
    render(
      <WorkspaceCallerStatus
        agentName="Ava"
        caller={{ contactName: null, phoneNumber: "+33612345678" }}
      />,
    );

    expect(screen.getByText("+33612345678")).toBeVisible();
    expect(screen.getByTestId("caller-status-icon")).toBeInTheDocument();
  });

  it("uses Unknown caller when an active call has no usable identity", () => {
    render(
      <WorkspaceCallerStatus
        agentName="Ava"
        caller={{ contactName: "  ", phoneNumber: null }}
      />,
    );

    expect(screen.getByText("Unknown caller")).toBeVisible();
    expect(screen.getByText("Ava is answering this call")).toBeVisible();
  });
});
```

- [ ] **Step 2: Run the component test and verify that it fails**

Run:

```bash
cd apps/web
npm run test:ci -- tests/components/workspace-caller-status.test.tsx
```

Expected: FAIL because `workspace-caller-status.tsx` does not exist.

- [ ] **Step 3: Implement caller identity rendering**

Create `apps/web/src/components/workspace/workspace-caller-status.tsx`:

```tsx
import { PhoneCall } from "lucide-react";

import { formatPhoneNumber } from "@/lib/formatters";

export type WorkspaceCallerIdentity = Readonly<{
  contactName: string | null;
  phoneNumber: string | null;
}>;

type WorkspaceCallerStatusProps = {
  agentName: string;
  caller: WorkspaceCallerIdentity | null;
};

function initialsFor(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export function WorkspaceCallerStatus({
  agentName,
  caller,
}: WorkspaceCallerStatusProps) {
  const contactName = caller?.contactName?.trim() || null;
  const phoneNumber = caller?.phoneNumber?.trim() || null;
  const primary = caller
    ? contactName ?? (phoneNumber ? formatPhoneNumber(phoneNumber) : "Unknown caller")
    : "No active call";
  const secondary = caller
    ? `${agentName} is answering this call`
    : `${agentName} is ready`;

  return (
    <div
      className="hidden min-w-0 shrink-0 items-center gap-3 xl:flex"
      data-header-item="caller-status"
      data-slot="workspace-caller-status"
    >
      <span
        aria-hidden
        className="grid size-10 shrink-0 place-items-center rounded-full bg-primary-soft font-semibold text-accent-foreground text-xs"
      >
        {contactName ? (
          initialsFor(contactName)
        ) : (
          <PhoneCall className="size-4" data-testid="caller-status-icon" />
        )}
      </span>
      <span className="min-w-0 max-w-52">
        <span className="block truncate font-semibold text-sm" title={primary}>
          {primary}
        </span>
        <span className="block truncate text-muted-foreground text-xs" title={secondary}>
          {secondary}
        </span>
      </span>
    </div>
  );
}
```

- [ ] **Step 4: Run component verification**

Run:

```bash
cd apps/web
npm run test:ci -- tests/components/workspace-caller-status.test.tsx
npm run typecheck
```

Expected: all four component tests and TypeScript pass.

- [ ] **Step 5: Commit the presentation boundary**

```bash
git add apps/web/src/components/workspace/workspace-caller-status.tsx apps/web/tests/components/workspace-caller-status.test.tsx
git commit -m "feat(web): add workspace caller status"
```

### Task 3: Load the Active Caller and Recompose the Header

**Files:**

- Modify: `apps/web/tests/app/app-shell.test.tsx`
- Modify: `apps/web/src/app/(app)/dashboard/layout.tsx`
- Modify: `apps/web/src/components/workspace/workspace-shell.tsx`
- Modify: `apps/web/src/components/workspace/workspace-header.tsx`

**Interfaces:**

- Consumes:

```ts
listCalls({ limit: 1, status: "in_progress" })
WorkspaceCallerIdentity
```

- Produces:

```ts
WorkspaceShellProps["activeCaller"]: WorkspaceCallerIdentity | null
WorkspaceHeaderProps["activeCaller"]: WorkspaceCallerIdentity | null
```

- [ ] **Step 1: Add the default call snapshot fixture**

In `apps/web/tests/app/app-shell.test.tsx`, add this default after resetting
`listCallsMock` in `beforeEach`:

```ts
testState.listCallsMock.mockResolvedValue({
  calls: [],
  total: 0,
  limit: 1,
  offset: 0,
  has_more: false,
});
```

Add a complete active-call fixture near `activeAccount`:

```ts
const activeCall = {
  id: "call-active",
  status: "in_progress",
  caller_number: "+33612345678",
  started_at: "2026-07-29T10:00:00Z",
  ended_at: null,
  duration_seconds: null,
  minutes_charged: null,
  summary_status: "unavailable" as const,
  summary_text: null,
  caller_intent: null,
  action_items: null,
  sentiment: null,
  follow_up_required: null,
  has_recording: false,
};
```

- [ ] **Step 2: Write the failing layout and fallback tests**

Add these tests to the app-shell suite:

```tsx
it("loads the newest in-progress call and shows its number in the header", async () => {
  testState.listCallsMock.mockResolvedValueOnce({
    calls: [activeCall],
    total: 1,
    limit: 1,
    offset: 0,
    has_more: false,
  });

  await renderDashboardLayout({ agentName: "Ava" });

  expect(testState.listCallsMock).toHaveBeenCalledWith({
    limit: 1,
    status: "in_progress",
  });
  expect(screen.getByText("+33612345678")).toBeVisible();
  expect(screen.getByText("Ava is answering this call")).toBeVisible();
});

it("shows the ready state when no call is active", async () => {
  await renderDashboardLayout({ agentName: "Ava" });

  expect(screen.getByText("No active call")).toBeVisible();
  expect(screen.getByText("Ava is ready")).toBeVisible();
});

it("degrades an active-call lookup failure to the ready state", async () => {
  testState.listCallsMock.mockRejectedValueOnce(new Error("call lookup unavailable"));

  await renderDashboardLayout({ agentName: "Ava" });

  expect(screen.getByText("No active call")).toBeVisible();
  expect(screen.getByText("Ava is ready")).toBeVisible();
  expect(screen.getByText("Dashboard content")).toBeVisible();
});
```

In the existing header routing test, replace the old grid-class assertion and
add exact item-order coverage:

```tsx
expect(header).toHaveClass(
  "flex",
  "bg-background/90",
  "lg:rounded-2xl",
  "lg:shadow-card",
);

expect(
  Array.from(header.querySelectorAll("[data-header-item]")).map((item) =>
    item.getAttribute("data-header-item"),
  ),
).toEqual([
  "search",
  "caller-status",
  "live-call",
  "notifications",
  "call-history",
  "account-control",
  "theme-control",
]);
```

- [ ] **Step 3: Run the shell test and verify that it fails**

Run:

```bash
cd apps/web
npm run test:ci -- tests/app/app-shell.test.tsx
```

Expected: the new caller-status text and header-order assertions fail because
the layout does not load a call and the header still uses the old grid order.

- [ ] **Step 4: Load a failure-safe active caller in the server layout**

In `apps/web/src/app/(app)/dashboard/layout.tsx`, import:

```ts
import type { WorkspaceCallerIdentity } from "@/components/workspace/workspace-caller-status";
import { listCalls } from "@/lib/api/calls";
```

Add:

```ts
async function resolveActiveCaller(): Promise<WorkspaceCallerIdentity | null> {
  try {
    const result = await listCalls({ limit: 1, status: "in_progress" });
    const call = result.calls[0];
    return call
      ? {
          contactName: null,
          phoneNumber: call.caller_number,
        }
      : null;
  } catch {
    return null;
  }
}
```

Include `resolveActiveCaller()` in the existing `Promise.all`, bind it as
`activeCaller`, and pass it to the shell:

```tsx
<WorkspaceShell
  account={account}
  accountControl={accountControl}
  activeCaller={activeCaller}
  agentEnabled={agentConfig.is_enabled}
  agentName={agentName}
>
```

- [ ] **Step 5: Thread caller identity through the shell**

In `workspace-shell.tsx`, import the caller type and add it to the props:

```ts
import type { WorkspaceCallerIdentity } from "@/components/workspace/workspace-caller-status";

type WorkspaceShellProps = {
  account: AccountStatus;
  accountControl: ReactNode;
  activeCaller: WorkspaceCallerIdentity | null;
  agentEnabled: boolean;
  agentName: string;
  children: ReactNode;
};
```

Destructure `activeCaller` and render:

```tsx
<WorkspaceHeader
  accountControl={accountControl}
  activeCaller={activeCaller}
  agentName={agentName}
/>
```

- [ ] **Step 6: Recompose the header in the approved order**

In `workspace-header.tsx`, import `WorkspaceCallerStatus` and its type. Define:

```ts
type WorkspaceHeaderProps = {
  accountControl: ReactNode;
  activeCaller: WorkspaceCallerIdentity | null;
  agentName: string;
};
```

Change the header to a flex container:

```tsx
<header className="sticky top-0 z-20 flex items-center gap-2 border-border border-b bg-background/90 px-4 py-3 backdrop-blur lg:rounded-2xl lg:border lg:bg-card lg:shadow-card">
```

Keep the existing mobile navigation and Presvo identity first without a
`data-header-item` attribute. Then render the wide-header items in this exact
source order:

```tsx
<form
  action="/dashboard/calls"
  aria-label="Call search"
  className="hidden min-w-48 flex-1 md:flex"
  data-header-item="search"
  method="get"
>
  <InputGroup className="h-11 w-full max-w-sm bg-background">
    <InputGroupInput
      aria-label="Search calls"
      autoComplete="off"
      name="q"
      placeholder="Search calls, callers or notes"
      type="search"
    />
    <InputGroupAddon>
      <Search aria-hidden="true" />
    </InputGroupAddon>
  </InputGroup>
  <button className="sr-only" type="submit">
    Search
  </button>
</form>

<WorkspaceCallerStatus agentName={agentName} caller={activeCaller} />

<Button asChild className="min-h-11 px-2.5">
  <Link
    aria-label="Live call"
    data-header-item="live-call"
    href="/dashboard/live-call"
    prefetch={false}
  >
    <PhoneCall aria-hidden="true" data-icon="inline-start" />
    <span className="hidden xl:inline">Live call</span>
    <CapabilityBadge
      className="border-primary-foreground/25 bg-primary-foreground/15 text-primary-foreground"
      status="preview"
    />
  </Link>
</Button>

<div data-header-item="notifications">
  <WorkspaceNotificationsPreview />
</div>

<Button asChild className="hidden min-h-11 md:inline-flex" variant="outline">
  <Link data-header-item="call-history" href="/dashboard/calls" prefetch={false}>
    <History aria-hidden="true" data-icon="inline-start" />
    Call history
  </Link>
</Button>

<div className="hidden xl:block" data-header-item="account-control">
  {accountControl}
</div>

<div data-header-item="theme-control">
  <ThemeSwitcher />
</div>
```

- [ ] **Step 7: Run focused and static verification**

Run:

```bash
cd apps/web
npm run test:ci -- tests/components/workspace-caller-status.test.tsx tests/navigation/dashboard-items.test.ts tests/app/app-shell.test.tsx
npm run typecheck
npm run check
```

Expected: all focused tests, TypeScript, and Biome pass.

- [ ] **Step 8: Run the full web suite**

Run:

```bash
cd apps/web
npm run test:ci
```

Expected: all web test files pass with zero failures.

- [ ] **Step 9: Review the final diff**

Run:

```bash
git diff --check
git status --short
git diff -- apps/web/src apps/web/tests
```

Expected: no whitespace errors, no `Presvo_frontend` changes, and only the
approved header, caller-status, navigation, and test files differ.

- [ ] **Step 10: Commit the integrated header change**

```bash
git add 'apps/web/src/app/(app)/dashboard/layout.tsx' apps/web/src/components/workspace/workspace-shell.tsx apps/web/src/components/workspace/workspace-header.tsx apps/web/tests/app/app-shell.test.tsx
git commit -m "feat(web): surface active caller in workspace header"
```
