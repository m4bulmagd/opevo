import { Bot, CreditCard, House, Phone } from "lucide-react";

export type NavItem = {
  title: string;
  url: string;
  icon: typeof House;
};

export const sidebarItems: NavItem[] = [
  {
    title: "Dashboard",
    url: "/dashboard",
    icon: House,
  },
  {
    title: "Calls",
    url: "/dashboard/calls",
    icon: Phone,
  },
  {
    title: "Agent",
    url: "/dashboard/agent",
    icon: Bot,
  },
  {
    title: "Billing",
    url: "/dashboard/billing",
    icon: CreditCard,
  },
];
