import type { AnchorHTMLAttributes } from "react";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    prefetch: _prefetch,
    ...props
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
}));

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
});
