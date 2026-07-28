import { describe, expect, it } from "vitest";

import * as dashboardNavigation from "@/navigation/dashboard-items";

type DashboardGroupFactory = (agentName: string) => Array<{
  id: string;
  label: string;
  items: Array<{ href: string; status: string; title: string }>;
}>;

describe("dashboard navigation model", () => {
  it("groups the complete production route set with explicit capability status", () => {
    const dashboardGroups = Reflect.get(dashboardNavigation, "dashboardGroups") as DashboardGroupFactory | undefined;
    const groups = dashboardGroups?.("Léa");

    expect(groups?.map((group) => ({ id: group.id, label: group.label }))).toEqual([
      { id: "main", label: "Main" },
      { id: "account", label: "Account" },
    ]);
    expect(
      groups?.flatMap((group) =>
        group.items.map((item) => ({
          href: item.href,
          status: item.status,
          title: item.title,
        })),
      ),
    ).toEqual([
      { href: "/dashboard", status: "live", title: "Overview" },
      { href: "/dashboard/live-call", status: "preview", title: "Live call" },
      { href: "/dashboard/calls", status: "live", title: "Calls" },
      { href: "/dashboard/agent", status: "live", title: "Léa" },
      { href: "/dashboard/billing", status: "live", title: "Usage & Billing" },
      { href: "/dashboard/account", status: "live", title: "Account" },
    ]);
  });

  it("marks only exact overview and nested destination routes active", () => {
    expect(dashboardNavigation.isDashboardItemActive("/dashboard", "/dashboard")).toBe(true);
    expect(dashboardNavigation.isDashboardItemActive("/dashboard/calls", "/dashboard")).toBe(false);
    expect(dashboardNavigation.isDashboardItemActive("/dashboard/calls/call-1", "/dashboard/calls")).toBe(true);
    expect(dashboardNavigation.isDashboardItemActive("/dashboard/live-call", "/dashboard/calls")).toBe(false);
  });
});
