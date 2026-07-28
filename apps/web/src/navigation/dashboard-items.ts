import type { LucideIcon } from "lucide-react";
import { Bot, CreditCard, House, Phone, Radio, UserRound } from "lucide-react";

import type { CapabilityStatus } from "@/lib/types/capability";

export type NavItem = {
  title: string;
  href: string;
  icon: LucideIcon;
  status: CapabilityStatus;
};

export type NavGroup = {
  id: "account" | "main";
  label: "Account" | "Main";
  items: NavItem[];
};

export function normalizeAgentName(agentName: string): string {
  return agentName.trim() || "Receptionist";
}

export function dashboardGroups(agentName: string): NavGroup[] {
  return [
    {
      id: "main",
      label: "Main",
      items: [
        { title: "Overview", href: "/dashboard", icon: House, status: "live" },
        { title: "Live call", href: "/dashboard/live-call", icon: Radio, status: "preview" },
        { title: "Calls", href: "/dashboard/calls", icon: Phone, status: "live" },
        { title: normalizeAgentName(agentName), href: "/dashboard/agent", icon: Bot, status: "live" },
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

/** Legacy flat list retained for the mobile command bar until Phase 1 Task 5. */
export function dashboardItems(agentName: string): NavItem[] {
  return [
    { title: "Overview", href: "/dashboard", icon: House, status: "live" },
    { title: "Calls", href: "/dashboard/calls", icon: Phone, status: "live" },
    { title: normalizeAgentName(agentName), href: "/dashboard/agent", icon: Bot, status: "live" },
    { title: "Billing", href: "/dashboard/billing", icon: CreditCard, status: "live" },
    { title: "Account", href: "/dashboard/account", icon: UserRound, status: "live" },
  ];
}

export function isDashboardItemActive(pathname: string, href: string): boolean {
  return href === "/dashboard" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
}
