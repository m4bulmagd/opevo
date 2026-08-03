import type { AnchorHTMLAttributes } from "react";

import type { LinkProps } from "next/link";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountStatus } from "@/lib/types/account";
import type { AgentConfig } from "@/lib/types/agent";

const testState = vi.hoisted(() => ({
  clerkSignOutVariants: [] as Array<"activation" | "mobile" | "workspace">,
  pathname: "/dashboard",
  reducedMotion: false,
  listCallsMock: vi.fn(),
  getAccountMock: vi.fn(),
  getAgentConfigForRequestMock: vi.fn(),
  routerPushMock: vi.fn(),
  redirectMock: vi.fn(() => {
    throw new Error("NEXT_REDIRECT");
  }),
}));

vi.mock("@/components/auth/clerk-sign-out", () => {
  return {
    ClerkSignOut: ({ variant }: { variant: "activation" | "mobile" | "workspace" }) => {
      testState.clerkSignOutVariants.push(variant);
      return <button aria-label={`${variant} Clerk sign-out sentinel`} type="button" />;
    },
  };
});

type MockLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
  prefetch?: LinkProps["prefetch"];
};

vi.mock("next/link", () => ({
  default: ({ children, href, onClick, prefetch: _prefetch, ...props }: MockLinkProps) => (
    <a
      href={href}
      onClick={(event) => {
        event.preventDefault();
        onClick?.(event);
      }}
      {...props}
    >
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => testState.pathname,
  useRouter: () => ({ push: testState.routerPushMock }),
  redirect: testState.redirectMock,
}));

vi.mock("@/lib/api/calls", () => ({
  listCalls: testState.listCallsMock,
}));

vi.mock("@/lib/api/account", () => ({
  getAccount: testState.getAccountMock,
}));

vi.mock("@/lib/api/request-data", () => ({
  getAgentConfigForRequest: testState.getAgentConfigForRequestMock,
}));

vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("motion/react")>();

  return {
    ...actual,
    useReducedMotion: () => testState.reducedMotion,
  };
});

const activeAccount: AccountStatus = {
  status: "active",
  serving: true,
  deactivation: null,
  reactivation_allowed: false,
  blocker: null,
};

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

function agentConfig(agentName: string, isEnabled = true): AgentConfig {
  return {
    agent_name: agentName,
    owner_context: "Reception for North Clinic",
    system_prompt: "Be helpful.",
    knowledge_base: "Open weekdays",
    pipeline_mode: "stt_llm_tts",
    is_enabled: isEnabled,
  };
}

async function renderDashboardLayout({
  account = activeAccount,
  agentEnabled = true,
  agentName = "Ava",
  authMode = "local",
}: {
  account?: AccountStatus;
  agentEnabled?: boolean;
  agentName?: string;
  authMode?: "clerk" | "local";
} = {}) {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("AUTH_MODE", authMode);
  vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", authMode === "clerk" ? "pk_test_configured" : "");
  vi.stubEnv("CLERK_SECRET_KEY", authMode === "clerk" ? "sk_test_configured" : "");
  testState.getAccountMock.mockResolvedValue(account);
  testState.getAgentConfigForRequestMock.mockResolvedValue(agentConfig(agentName, agentEnabled));

  const { default: DashboardLayout } = await import("@/app/(app)/dashboard/layout");
  const { TooltipProvider } = await import("@/components/ui/tooltip");
  const { PreferencesStoreProvider } = await import("@/stores/preferences/preferences-provider");

  return render(
    <TooltipProvider>
      <PreferencesStoreProvider themeMode="light">
        {
          await DashboardLayout({
            children: <div>Dashboard content</div>,
          })
        }
      </PreferencesStoreProvider>
    </TooltipProvider>,
  );
}

function desktopNavigation() {
  const sidebar = screen.getByRole("complementary", { name: "Workspace sidebar" });
  return within(sidebar).getByRole("navigation", { name: "Workspace navigation" });
}

async function openMobileNavigation() {
  const trigger = screen.getByRole("button", { name: "Open navigation" });
  fireEvent.click(trigger);

  return {
    dialog: await screen.findByRole("dialog", { name: "Workspace navigation" }),
    trigger,
  };
}

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {
        return undefined;
      }
      unobserve() {
        return undefined;
      }
      disconnect() {
        return undefined;
      }
    },
  );
});

beforeEach(() => {
  testState.clerkSignOutVariants.length = 0;
  testState.pathname = "/dashboard";
  testState.reducedMotion = false;
  testState.listCallsMock.mockReset();
  testState.listCallsMock.mockResolvedValue({
    calls: [],
    total: 0,
    limit: 1,
    offset: 0,
    has_more: false,
  });
  testState.getAccountMock.mockReset();
  testState.getAgentConfigForRequestMock.mockReset();
  testState.routerPushMock.mockReset();
  testState.redirectMock.mockClear();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

afterAll(() => {
  vi.unstubAllGlobals();
});

describe("app shell", () => {
  it("server-renders grouped desktop navigation with visible preview status", async () => {
    const { WorkspaceNavigation } = await import("@/components/workspace/workspace-navigation");
    const { TooltipProvider } = await import("@/components/ui/tooltip");

    const markup = renderToString(
      <TooltipProvider>
        <WorkspaceNavigation />
      </TooltipProvider>,
    );

    expect(markup).toContain("Main");
    expect(markup).toContain("Account");
    expect(markup).toContain("Preview");
    expect(markup).not.toContain("data-motion");
  });

  it("uses the fixed Agent label in the complete desktop destination set", async () => {
    await renderDashboardLayout({ agentName: "Ava" });

    const navigation = desktopNavigation();
    const destinations = within(navigation).getAllByRole("link");

    expect(destinations).toHaveLength(6);
    expect(destinations.map((link) => link.getAttribute("href"))).toEqual([
      "/dashboard",
      "/dashboard/live-call",
      "/dashboard/calls",
      "/dashboard/agent",
      "/dashboard/billing",
      "/dashboard/account",
    ]);
    expect(destinations.map((link) => link.getAttribute("aria-label"))).toEqual([
      "Overview",
      "Live call",
      "Calls",
      "Agent",
      "Usage & Billing",
      "Account",
    ]);
    expect(within(within(navigation).getByRole("link", { name: "Live call" })).getByText("Preview")).toBeVisible();
  });

  it.each([
    ["Enabled", true],
    ["Paused", false],
  ] as const)("shows the configured agent and honest %s runtime state in the labelled sidebar", async (state, agentEnabled) => {
    const longAgentName = "Ava, North Clinic Evening Receptionist";
    await renderDashboardLayout({ agentEnabled, agentName: longAgentName });

    const sidebar = screen.getByRole("complementary", { name: "Workspace sidebar" });
    const runtime = within(sidebar).getByRole("group", {
      name: `Agent runtime: ${longAgentName}, ${state}`,
    });
    const visibleAgentName = within(runtime).getByText(longAgentName);

    expect(runtime).toHaveClass("rounded-xl", "border", "bg-card");
    expect(visibleAgentName).toHaveClass("truncate");
    expect(visibleAgentName).toHaveAttribute("title", longAgentName);
    expect(within(runtime).getByText(state)).toBeInTheDocument();
  });

  it.each([
    [
      "a non-serving active account",
      {
        ...activeAccount,
        serving: false,
        blocker: "customer_not_ready",
      },
      "Paused",
    ],
    [
      "a deactivating account",
      {
        status: "deactivating",
        serving: false,
        deactivation: { state: "draining_call", requested_at: "2026-07-24T10:00:00Z" },
        reactivation_allowed: false,
        blocker: "account_deactivating",
      },
      "Deactivating",
    ],
    [
      "a deactivation needing attention with a stale general blocker",
      {
        status: "deactivating",
        serving: false,
        deactivation: { state: "attention_required", requested_at: "2026-07-24T10:00:00Z" },
        reactivation_allowed: false,
        blocker: "account_deactivating",
      },
      "Attention required",
    ],
    [
      "an inactive account",
      {
        status: "inactive",
        serving: false,
        deactivation: null,
        reactivation_allowed: true,
        blocker: "account_inactive",
      },
      "Inactive",
    ],
  ] satisfies ReadonlyArray<
    readonly [string, AccountStatus, "Attention required" | "Deactivating" | "Inactive" | "Paused"]
  >)("uses account state instead of saved routing alone for %s", async (_label, account, expectedState) => {
    await renderDashboardLayout({ account, agentEnabled: true });

    const sidebar = screen.getByRole("complementary", { name: "Workspace sidebar" });
    expect(
      within(sidebar).getByRole("group", {
        name: `Agent runtime: Ava, ${expectedState}`,
      }),
    ).toBeInTheDocument();
  });

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
    expect(within(dialog).getByRole("link", { name: "Agent" })).toHaveAttribute("href", "/dashboard/agent");
  });

  it("exposes labelled desktop and mobile shell compositions", async () => {
    const view = await renderDashboardLayout();

    const sidebar = screen.getByRole("complementary", { name: "Workspace sidebar" });
    const sidebarPanel = sidebar.querySelector('[data-slot="workspace-sidebar-panel"]');
    expect(sidebar).toHaveClass("hidden", "w-64", "shrink-0", "lg:block");
    expect(sidebar).not.toHaveClass("md:flex", "md:w-18");
    expect(sidebarPanel).toHaveClass("rounded-2xl", "border", "shadow-card");

    for (const link of within(desktopNavigation()).getAllByRole("link")) {
      expect(link).toHaveClass("min-h-11");
      expect(within(link).getByText(link.getAttribute("aria-label") ?? "")).toHaveClass("truncate");
    }
    expect(within(desktopNavigation()).getByText("Main")).toHaveClass("text-label");
    expect(within(desktopNavigation()).getByText("Account", { selector: "p" })).toHaveClass("text-label");

    const workspaceHeader = screen.getByRole("banner");
    expect(workspaceHeader).not.toHaveClass("hidden", "md:hidden");
    expect(within(workspaceHeader).getByText("Local development")).toBeInTheDocument();
    expect(within(workspaceHeader).getByRole("button", { name: /Current theme:/i })).toHaveClass("size-11");
    expect(within(workspaceHeader).getByRole("button", { name: "Open navigation" })).toHaveClass(
      "min-h-11",
      "min-w-11",
      "xl:hidden",
    );
    expect(screen.queryByRole("navigation", { name: "Mobile workspace navigation" })).not.toBeInTheDocument();

    const workspaceShell = view.container.querySelector('[data-slot="workspace-shell"]');
    const workspaceContent = view.container.querySelector('[data-slot="workspace-content"]');
    expect(workspaceShell).toHaveClass("font-sans");
    expect(workspaceShell).not.toHaveClass("font-[family-name:var(--font-figtree)]");
    expect(document.body).not.toHaveClass("font-figtree");
    expect(workspaceContent).toHaveClass("lg:flex", "lg:gap-4", "lg:p-4");
    expect(workspaceContent).not.toHaveClass("md:pl-18", "lg:pl-64");
    expect(workspaceContent).not.toHaveClass("pb-[calc(4rem+env(safe-area-inset-bottom))]");

    const workspaceMain = view.container.querySelector("#workspace-main");
    expect(workspaceMain).toHaveClass("flex", "w-full", "px-4", "sm:px-6", "md:px-8", "lg:px-0");
    expect(workspaceMain).not.toHaveClass("mx-auto", "max-w-7xl", "lg:px-10");

    const activeMarkers = view.container.querySelectorAll('[data-slot="active-navigation-marker"]');
    expect(activeMarkers).toHaveLength(0);
  });

  it("announces and traps mobile navigation, closes it with Escape, and restores focus", async () => {
    await renderDashboardLayout();

    const backgroundControl = within(screen.getByRole("banner")).getByRole("button", {
      name: /Current theme:/i,
    });
    const { dialog, trigger } = await openMobileNavigation();
    const overviewLink = within(dialog).getByRole("link", { name: "Overview" });
    const liveCallLink = within(dialog).getByRole("link", { name: "Live call" });
    const billingLink = within(dialog).getByRole("link", { name: "Usage & Billing" });
    const accountLink = within(dialog).getByRole("link", { name: "Account" });

    await waitFor(() => expect(overviewLink).toHaveFocus());
    expect(within(liveCallLink).getByText("Preview")).toBeVisible();
    expect(billingLink).toHaveAttribute("href", "/dashboard/billing");
    expect(accountLink).toHaveAttribute("href", "/dashboard/account");
    expect(dialog).toContainElement(document.activeElement as HTMLElement);

    backgroundControl.focus();
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Workspace navigation" })).toBeNull());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("closes mobile navigation after selecting a destination", async () => {
    await renderDashboardLayout();

    const { dialog } = await openMobileNavigation();
    fireEvent.click(within(dialog).getByRole("link", { name: "Calls" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Workspace navigation" })).toBeNull());
  });

  it("delegates desktop and mobile account controls to explicit shared-leaf variants", async () => {
    await renderDashboardLayout({ authMode: "clerk" });

    const header = screen.getByRole("banner");
    const trigger = within(header).getByRole("button", { name: "Open navigation" });
    expect(within(header).getByRole("button", { name: "workspace Clerk sign-out sentinel" })).toBeInTheDocument();
    expect(new Set(testState.clerkSignOutVariants)).toEqual(new Set(["workspace"]));

    const { dialog } = await openMobileNavigation();
    expect(within(dialog).getByRole("button", { name: "mobile Clerk sign-out sentinel" })).toBeInTheDocument();
    expect(new Set(testState.clerkSignOutVariants)).toEqual(new Set(["workspace", "mobile"]));
    expect(trigger).toHaveClass("xl:hidden");
    expect(trigger).not.toHaveClass("lg:hidden");
  });

  it("keeps local development visible in the workspace drawer without invoking Clerk", async () => {
    await renderDashboardLayout();

    const { dialog } = await openMobileNavigation();
    expect(within(dialog).getByText("Local development")).toBeVisible();
    expect(testState.clerkSignOutVariants).toEqual([]);
  });

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

  it("routes shell search and header destinations to their production URLs", async () => {
    await renderDashboardLayout();

    const header = screen.getByRole("banner");
    const search = within(header).getByRole("searchbox", { name: "Search calls" });
    const form = search.closest("form");

    expect(header).toHaveClass("flex", "bg-background/90", "lg:rounded-2xl", "lg:shadow-card");
    expect(
      Array.from(header.querySelectorAll("[data-header-item]")).map((item) => item.getAttribute("data-header-item")),
    ).toEqual([
      "search",
      "caller-status",
      "live-call",
      "notifications",
      "call-history",
      "account-control",
      "theme-control",
    ]);
    expect(search).toHaveAttribute("name", "q");
    expect(search).toHaveAttribute("placeholder", "Search calls, callers or notes");
    expect(form).toHaveAttribute("action", "/dashboard/calls");
    expect(form).toHaveAttribute("method", "get");
    expect(within(header).getByRole("link", { name: "Call history" })).toHaveAttribute("href", "/dashboard/calls");
    expect(within(header).getByRole("link", { name: "Live call" })).toHaveAttribute("href", "/dashboard/live-call");
    expect(within(within(header).getByRole("link", { name: "Live call" })).getByText("Preview")).toBeVisible();
  });

  it("keeps notification Preview interactions local and resettable", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await renderDashboardLayout();

    const trigger = screen.getByRole("button", { name: "Notifications (3 unread)" });
    fireEvent.click(trigger);

    const panel = await screen.findByRole("dialog", { name: "Notifications Preview" });
    expect(within(panel).getByText("Preview")).toBeVisible();
    expect(within(panel).getByText(/interactions are local and reset on reload/i)).toBeVisible();
    expect(within(panel).getAllByRole("listitem")).toHaveLength(3);

    fireEvent.click(within(panel).getByRole("button", { name: "Mark all read" }));
    expect(screen.getByRole("button", { name: "Notifications (0 unread)" })).toBeVisible();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("marks only the matching nested destination as the current page", async () => {
    testState.pathname = "/dashboard/calls/call-123";
    await renderDashboardLayout();

    const navigation = desktopNavigation();
    expect(within(navigation).getByRole("link", { name: "Calls" })).toHaveAttribute("aria-current", "page");
    expect(within(navigation).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("renders protected dashboard content in guarded local mode", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    testState.listCallsMock.mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 20,
      offset: 0,
      has_more: false,
    });

    const { default: CallsPage } = await import("@/app/(app)/dashboard/calls/page");
    render(
      await CallsPage({
        searchParams: Promise.resolve({}),
      }),
    );

    expect(screen.getByText(/No calls yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/Call history is unavailable/i)).not.toBeInTheDocument();
  });

  it.each([
    [
      "deactivating",
      {
        status: "deactivating",
        serving: false,
        deactivation: { state: "requested", requested_at: "2026-07-24T10:00:00Z" },
        reactivation_allowed: false,
        blocker: "account_deactivating",
      },
      "Presvo is no longer accepting new calls",
    ],
    [
      "inactive",
      {
        status: "inactive",
        serving: false,
        deactivation: null,
        reactivation_allowed: true,
        blocker: "account_inactive",
      },
      "Presvo is inactive",
    ],
  ] as const)("shows the global lifecycle banner and every retained navigation destination while %s", async (_status, account, title) => {
    await renderDashboardLayout({ account });

    expect(screen.getByText(title)).toBeInTheDocument();
    expect(
      within(desktopNavigation())
        .getAllByRole("link")
        .map((link) => link.getAttribute("href")),
    ).toEqual([
      "/dashboard",
      "/dashboard/live-call",
      "/dashboard/calls",
      "/dashboard/agent",
      "/dashboard/billing",
      "/dashboard/account",
    ]);

    const { dialog } = await openMobileNavigation();
    expect(within(dialog).getByRole("link", { name: "Usage & Billing" })).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Account" })).toBeInTheDocument();
  });

  it("omits the global lifecycle banner while active and retains navigation", async () => {
    await renderDashboardLayout();

    expect(screen.queryByText("Presvo is no longer accepting new calls")).not.toBeInTheDocument();
    expect(screen.queryByText("Presvo is inactive")).not.toBeInTheDocument();
    expect(within(desktopNavigation()).getByRole("link", { name: "Account" })).toHaveAttribute(
      "href",
      "/dashboard/account",
    );
    expect(within(desktopNavigation()).getByRole("link", { name: "Calls" })).toHaveAttribute(
      "href",
      "/dashboard/calls",
    );
    expect(within(desktopNavigation()).getByRole("link", { name: "Usage & Billing" })).toHaveAttribute(
      "href",
      "/dashboard/billing",
    );
  });

  it.each([
    ["sign-in", "@/app/(auth)/sign-in/[[...sign-in]]/page"],
    ["sign-up", "@/app/(auth)/sign-up/[[...sign-up]]/page"],
  ])("redirects local %s routes to activation", async (_route, modulePath) => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");

    const { default: AuthPage } = await import(/* @vite-ignore */ modulePath);

    await expect(AuthPage()).rejects.toThrow("NEXT_REDIRECT");
    expect(testState.redirectMock).toHaveBeenCalledWith("/activate");
  });
});
