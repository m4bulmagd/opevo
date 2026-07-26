import type { AnchorHTMLAttributes } from "react";

import type { LinkProps } from "next/link";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountStatus } from "@/lib/types/account";
import type { AgentConfig } from "@/lib/types/agent";

const testState = vi.hoisted(() => ({
  pathname: "/dashboard",
  reducedMotion: false,
  listCallsMock: vi.fn(),
  getAccountMock: vi.fn(),
  getAgentConfigForRequestMock: vi.fn(),
  redirectMock: vi.fn(() => {
    throw new Error("NEXT_REDIRECT");
  }),
}));

type MockLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
  prefetch?: LinkProps["prefetch"];
};

vi.mock("next/link", () => ({
  default: ({ children, href, prefetch: _prefetch, ...props }: MockLinkProps) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => testState.pathname,
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

vi.mock("@/lib/fonts/registry", () => ({
  authenticatedFontVariable: "font-figtree",
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
}: {
  account?: AccountStatus;
  agentEnabled?: boolean;
  agentName?: string;
} = {}) {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("AUTH_MODE", "local");
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
  const rail = screen.getByRole("complementary", { name: "Workspace command rail" });
  return within(rail).getByRole("navigation", { name: "Workspace navigation" });
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
  testState.pathname = "/dashboard";
  testState.reducedMotion = false;
  testState.listCallsMock.mockReset();
  testState.getAccountMock.mockReset();
  testState.getAgentConfigForRequestMock.mockReset();
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
  it("server-renders a static active marker before the client motion preference hydrates", async () => {
    testState.reducedMotion = false;
    const { WorkspaceNavigation } = await import("@/components/workspace/workspace-navigation");
    const { TooltipProvider } = await import("@/components/ui/tooltip");

    const markup = renderToString(
      <TooltipProvider>
        <WorkspaceNavigation agentName="Ava" variant="rail" />
      </TooltipProvider>,
    );

    expect(markup).toContain('data-motion="static"');
    expect(markup).not.toContain('data-motion="layout"');
    expect(markup).not.toContain("data-layout-id");
  });

  it("uses the configured agent name in the complete desktop destination set", async () => {
    await renderDashboardLayout({ agentName: "Ava" });

    const navigation = desktopNavigation();
    const destinations = within(navigation).getAllByRole("link");

    expect(destinations).toHaveLength(5);
    expect(destinations.map((link) => link.getAttribute("href"))).toEqual([
      "/dashboard",
      "/dashboard/calls",
      "/dashboard/agent",
      "/dashboard/billing",
      "/dashboard/account",
    ]);
    expect(destinations.map((link) => link.getAttribute("aria-label"))).toEqual([
      "Overview",
      "Calls",
      "Ava",
      "Billing",
      "Account",
    ]);
  });

  it.each([
    ["Enabled", true],
    ["Paused", false],
  ] as const)("shows the configured agent and honest %s runtime state in the labelled rail", async (state, agentEnabled) => {
    const longAgentName = "Ava, North Clinic Evening Receptionist";
    await renderDashboardLayout({ agentEnabled, agentName: longAgentName });

    const rail = screen.getByRole("complementary", { name: "Workspace command rail" });
    const runtime = within(rail).getByRole("group", {
      name: `Agent runtime: ${longAgentName}, ${state}`,
    });
    const visibleAgentName = within(runtime).getByText(longAgentName);

    expect(runtime).toHaveClass("hidden", "lg:block");
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

    const rail = screen.getByRole("complementary", { name: "Workspace command rail" });
    expect(
      within(rail).getByRole("group", {
        name: `Agent runtime: Ava, ${expectedState}`,
      }),
    ).toBeInTheDocument();
  });

  it("normalizes a blank configured agent name to Receptionist in desktop and mobile navigation", async () => {
    await renderDashboardLayout({ agentName: " \n\t " });

    expect(within(desktopNavigation()).getByRole("link", { name: "Receptionist" })).toHaveAttribute(
      "href",
      "/dashboard/agent",
    );
    expect(
      within(screen.getByRole("navigation", { name: "Mobile workspace navigation" })).getByRole("link", {
        name: "Receptionist",
      }),
    ).toHaveAttribute("href", "/dashboard/agent");
  });

  it("truncates only the visible long agent label while preserving its accessible name and tooltip", async () => {
    const longAgentName = "Ava, North Clinic Evening Receptionist";
    await renderDashboardLayout({ agentName: `  ${longAgentName}  ` });

    const agentLink = within(desktopNavigation()).getByRole("link", { name: longAgentName });
    const visibleLabel = within(agentLink).getByText(longAgentName);

    expect(visibleLabel).toHaveClass("truncate");
    expect(agentLink).toHaveAccessibleName(longAgentName);

    fireEvent.focus(agentLink);
    expect(await screen.findByRole("tooltip", { name: longAgentName })).toBeInTheDocument();
  });

  it("reveals the complete long agent name from the mobile command tooltip", async () => {
    const longAgentName = "Ava, North Clinic Evening Receptionist";
    await renderDashboardLayout({ agentName: longAgentName });

    const mobileNavigation = screen.getByRole("navigation", { name: "Mobile workspace navigation" });
    const agentLink = within(mobileNavigation).getByRole("link", { name: longAgentName });

    expect(agentLink).toHaveClass("min-h-11", "min-w-11");
    expect(agentLink).toHaveAccessibleName(longAgentName);

    fireEvent.focus(agentLink);
    expect(await screen.findByRole("tooltip", { name: longAgentName })).toBeInTheDocument();
  });

  it("exposes labelled desktop, compact tablet, and mobile shell compositions", async () => {
    const view = await renderDashboardLayout();

    const rail = screen.getByRole("complementary", { name: "Workspace command rail" });
    expect(rail).toHaveClass("hidden", "md:flex", "md:w-18", "lg:w-64");

    for (const link of within(desktopNavigation()).getAllByRole("link")) {
      expect(link).toHaveClass("min-h-11", "min-w-11");
      expect(within(link).getByText(link.getAttribute("aria-label") ?? "")).toHaveClass(
        "hidden",
        "truncate",
        "lg:block",
      );
    }

    const mobileNavigation = screen.getByRole("navigation", { name: "Mobile workspace navigation" });
    expect(mobileNavigation).toHaveClass("md:hidden");
    expect(within(mobileNavigation).getAllByRole("link")).toHaveLength(3);
    for (const link of within(mobileNavigation).getAllByRole("link")) {
      expect(link).toHaveClass("min-h-11", "min-w-11");
    }
    expect(
      within(mobileNavigation)
        .getAllByRole("link")
        .map((link) => link.getAttribute("aria-label")),
    ).toEqual(["Overview", "Calls", "Ava"]);
    expect(within(mobileNavigation).getByRole("button", { name: "More" })).toHaveClass("min-h-11", "min-w-11");
    expect(within(mobileNavigation).queryByRole("link", { name: "Billing" })).not.toBeInTheDocument();
    expect(within(mobileNavigation).queryByRole("link", { name: "Account" })).not.toBeInTheDocument();

    const workspaceHeader = screen.getByRole("banner");
    expect(workspaceHeader).not.toHaveClass("hidden", "md:hidden");
    expect(within(workspaceHeader).getByText("Local development")).toBeInTheDocument();
    expect(within(workspaceHeader).getByRole("button", { name: /Current theme:/i })).toHaveClass("size-11");

    const workspaceShell = view.container.querySelector('[data-slot="workspace-shell"]');
    const workspaceContent = view.container.querySelector('[data-slot="workspace-content"]');
    expect(workspaceShell).toHaveClass("font-figtree", "font-[family-name:var(--font-figtree)]");
    expect(document.body).not.toHaveClass("font-figtree");
    expect(workspaceContent).toHaveClass(
      "pb-[calc(4rem+env(safe-area-inset-bottom))]",
      "md:pb-0",
      "md:pl-18",
      "lg:pl-64",
    );

    const activeMarkers = view.container.querySelectorAll('[data-slot="active-navigation-marker"]');
    expect(activeMarkers).toHaveLength(2);
    expect(new Set(Array.from(activeMarkers, (marker) => marker.getAttribute("data-layout-id"))).size).toBe(2);
    for (const marker of activeMarkers) {
      expect(marker).toHaveAttribute("data-motion", "layout");
      expect(marker.getAttribute("data-layout-id")).toMatch(/^workspace-active-/);
    }
  });

  it("uses static active markers when the user prefers reduced motion", async () => {
    testState.reducedMotion = true;
    const view = await renderDashboardLayout();

    const activeMarkers = view.container.querySelectorAll('[data-slot="active-navigation-marker"]');
    expect(activeMarkers).toHaveLength(2);
    for (const marker of activeMarkers) {
      expect(marker).toHaveAttribute("data-motion", "static");
      expect(marker).not.toHaveAttribute("data-layout-id");
    }
  });

  it("announces and traps the More sheet, closes it with Escape, and restores focus", async () => {
    await renderDashboardLayout();

    const mobileNavigation = screen.getByRole("navigation", { name: "Mobile workspace navigation" });
    const moreTrigger = within(mobileNavigation).getByRole("button", { name: "More" });
    const backgroundControl = within(screen.getByRole("banner")).getByRole("button", {
      name: /Current theme:/i,
    });
    fireEvent.click(moreTrigger);

    const sheet = await screen.findByRole("dialog", { name: "More workspace destinations" });
    const billingLink = within(sheet).getByRole("link", { name: "Billing" });
    const accountLink = within(sheet).getByRole("link", { name: "Account" });

    expect(billingLink).toHaveAttribute("href", "/dashboard/billing");
    expect(accountLink).toHaveAttribute("href", "/dashboard/account");
    await waitFor(() => expect(sheet).toContainElement(document.activeElement as HTMLElement));

    backgroundControl.focus();
    await waitFor(() => expect(sheet).toContainElement(document.activeElement as HTMLElement));

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "More workspace destinations" })).toBeNull());
    await waitFor(() => expect(moreTrigger).toHaveFocus());
  });

  it("leaves More sheet presentation to one reduced-motion opacity entrance", async () => {
    testState.reducedMotion = true;
    await renderDashboardLayout();

    const mobileNavigation = screen.getByRole("navigation", { name: "Mobile workspace navigation" });
    fireEvent.click(within(mobileNavigation).getByRole("button", { name: "More" }));

    const sheet = await screen.findByRole("dialog", { name: "More workspace destinations" });
    expect(sheet).toHaveClass("!animate-none", "!transform-none", "!transition-none");

    const bottomSheet = sheet.querySelector('[data-slot="bottom-sheet-motion"]');
    expect(bottomSheet).toHaveAttribute("data-motion", "opacity-only");
    expect((bottomSheet as HTMLElement).style.transform).toBe("");
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
    ).toEqual(["/dashboard", "/dashboard/calls", "/dashboard/agent", "/dashboard/billing", "/dashboard/account"]);

    const mobileNavigation = screen.getByRole("navigation", { name: "Mobile workspace navigation" });
    fireEvent.click(within(mobileNavigation).getByRole("button", { name: "More" }));
    const sheet = await screen.findByRole("dialog", { name: "More workspace destinations" });
    expect(within(sheet).getByRole("link", { name: "Billing" })).toBeInTheDocument();
    expect(within(sheet).getByRole("link", { name: "Account" })).toBeInTheDocument();
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
    expect(within(desktopNavigation()).getByRole("link", { name: "Billing" })).toHaveAttribute(
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
