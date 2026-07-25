import type { LucideIcon } from "lucide-react";
import { Bot, CreditCard, House, Phone, UserRound } from "lucide-react";

export type NavItem = {
  title: string;
  href: string;
  icon: LucideIcon;
};

export function normalizeAgentName(agentName: string): string {
  return agentName.trim() || "Receptionist";
}

export function dashboardItems(agentName: string): NavItem[] {
  return [
    { title: "Overview", href: "/dashboard", icon: House },
    { title: "Calls", href: "/dashboard/calls", icon: Phone },
    { title: normalizeAgentName(agentName), href: "/dashboard/agent", icon: Bot },
    { title: "Billing", href: "/dashboard/billing", icon: CreditCard },
    { title: "Account", href: "/dashboard/account", icon: UserRound },
  ];
}

export function isDashboardItemActive(pathname: string, href: string): boolean {
  return href === "/dashboard" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
}
