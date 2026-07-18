import type { AnchorHTMLAttributes } from "react";

import type { LinkProps } from "next/link";

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const listCallsMock = vi.fn();
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

vi.mock("@/lib/api/calls", () => ({
  listCalls: listCallsMock,
}));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  listCallsMock.mockReset();
  redirectMock.mockClear();
});

describe("app shell", () => {
  it("renders Dashboard, Calls, Agent, and Billing in the sidebar", async () => {
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
  });

  it("renders protected dashboard content in guarded local mode", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    listCallsMock.mockResolvedValueOnce([]);

    const { default: CallsPage } = await import("@/app/(app)/dashboard/calls/page");
    render(await CallsPage());

    expect(screen.getByText(/No calls yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/Call history is unavailable/i)).not.toBeInTheDocument();
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
