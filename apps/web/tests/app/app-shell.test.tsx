import type { AnchorHTMLAttributes } from "react";

import type { LinkProps } from "next/link";

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const listCallsMock = vi.fn();
const getAccountMock = vi.fn();
const cookiesMock = vi.fn();
const redirectMock = vi.fn(() => {
  throw new Error("NEXT_REDIRECT");
});

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
  usePathname: () => "/dashboard",
  redirect: redirectMock,
}));

vi.mock("next/headers", () => ({
  cookies: cookiesMock,
}));

vi.mock("@/lib/api/calls", () => ({
  listCalls: listCallsMock,
}));

vi.mock("@/lib/api/account", () => ({
  getAccount: getAccountMock,
}));

vi.mock("@/app/(app)/dashboard/_components/sidebar/layout-controls", () => ({
  LayoutControls: () => <div data-testid="layout-controls" />,
}));

vi.mock("@/app/(app)/dashboard/_components/sidebar/theme-switcher", () => ({
  ThemeSwitcher: () => <div data-testid="theme-switcher" />,
}));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  listCallsMock.mockReset();
  getAccountMock.mockReset();
  cookiesMock.mockReset();
  redirectMock.mockClear();
});

describe("app shell", () => {
  it("renders Dashboard, Calls, Agent, Billing, and Account in the sidebar", async () => {
    const { AppSidebar } = await import("@/app/(app)/dashboard/_components/sidebar/app-sidebar");
    const { SidebarProvider } = await import("@/components/ui/sidebar");
    const { TooltipProvider } = await import("@/components/ui/tooltip");

    render(
      <TooltipProvider>
        <SidebarProvider defaultOpen>
          <AppSidebar />
        </SidebarProvider>
      </TooltipProvider>,
    );

    expect(screen.getByRole("link", { name: /Dashboard/i })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("link", { name: /Calls/i })).toHaveAttribute("href", "/dashboard/calls");
    expect(screen.getByRole("link", { name: /Agent/i })).toHaveAttribute("href", "/dashboard/agent");
    expect(screen.getByRole("link", { name: /Billing/i })).toHaveAttribute("href", "/dashboard/billing");
    expect(screen.getByRole("link", { name: /Account/i })).toHaveAttribute("href", "/dashboard/account");
  });

  it("renders protected dashboard content in guarded local mode", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    listCallsMock.mockResolvedValueOnce({
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
  ] as const)("shows the global lifecycle banner and retained navigation while %s", async (_status, account, title) => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    cookiesMock.mockResolvedValue({ get: vi.fn().mockReturnValue(undefined) });
    getAccountMock.mockResolvedValue(account);

    const { default: DashboardLayout } = await import("@/app/(app)/dashboard/layout");
    const { TooltipProvider } = await import("@/components/ui/tooltip");
    render(<TooltipProvider>{await DashboardLayout({ children: <div>Dashboard content</div> })}</TooltipProvider>);

    expect(screen.getByText(title)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Account$/i })).toHaveAttribute("href", "/dashboard/account");
    expect(screen.getByRole("link", { name: /^Calls$/i })).toHaveAttribute("href", "/dashboard/calls");
    expect(screen.getByRole("link", { name: /^Billing$/i })).toHaveAttribute("href", "/dashboard/billing");
  });

  it("omits the global lifecycle banner while active and retains navigation", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    cookiesMock.mockResolvedValue({ get: vi.fn().mockReturnValue(undefined) });
    getAccountMock.mockResolvedValue({
      status: "active",
      serving: true,
      deactivation: null,
      reactivation_allowed: false,
      blocker: null,
    });

    const { default: DashboardLayout } = await import("@/app/(app)/dashboard/layout");
    const { TooltipProvider } = await import("@/components/ui/tooltip");
    render(<TooltipProvider>{await DashboardLayout({ children: <div>Dashboard content</div> })}</TooltipProvider>);

    expect(screen.queryByText("Presvo is no longer accepting new calls")).not.toBeInTheDocument();
    expect(screen.queryByText("Presvo is inactive")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Account$/i })).toHaveAttribute("href", "/dashboard/account");
    expect(screen.getByRole("link", { name: /^Calls$/i })).toHaveAttribute("href", "/dashboard/calls");
    expect(screen.getByRole("link", { name: /^Billing$/i })).toHaveAttribute("href", "/dashboard/billing");
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
    expect(redirectMock).toHaveBeenCalledWith("/activate");
  });
});
