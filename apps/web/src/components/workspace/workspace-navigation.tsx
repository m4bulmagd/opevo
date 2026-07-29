"use client";

import type { Ref } from "react";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { CapabilityBadge } from "@/components/product/capability-badge";
import { cn } from "@/lib/utils";
import { dashboardGroups, isDashboardItemActive } from "@/navigation/dashboard-items";

type WorkspaceNavigationProps = {
  ariaLabel?: string;
  firstDestinationRef?: Ref<HTMLAnchorElement>;
  onNavigate?: () => void;
};

export function WorkspaceNavigation({
  ariaLabel = "Workspace navigation",
  firstDestinationRef,
  onNavigate,
}: WorkspaceNavigationProps) {
  const pathname = usePathname();

  return (
    <nav aria-label={ariaLabel} className="flex min-w-0 flex-1 flex-col gap-6 overflow-y-auto">
      {dashboardGroups().map((group, groupIndex) => (
        <div className="flex flex-col gap-1" key={group.id}>
          <p className="px-3 pb-1 text-label">{group.label}</p>
          {group.items.map((item, itemIndex) => {
            const active = isDashboardItemActive(pathname, item.href);

            return (
              <Link
                aria-current={active ? "page" : undefined}
                aria-label={item.title}
                className={cn(
                  "flex min-h-11 items-center gap-3 rounded-lg px-3 text-sidebar-foreground text-sm outline-none transition-colors hover:bg-sidebar-accent focus-visible:ring-3 focus-visible:ring-sidebar-ring/50",
                  active && "bg-sidebar-accent text-sidebar-accent-foreground",
                )}
                href={item.href}
                key={item.href}
                onClick={onNavigate}
                prefetch={false}
                ref={groupIndex === 0 && itemIndex === 0 ? firstDestinationRef : undefined}
              >
                <item.icon aria-hidden="true" className="size-5 shrink-0" />
                <span className="min-w-0 flex-1 truncate" title={item.title}>
                  {item.title}
                </span>
                {item.status === "live" ? null : <CapabilityBadge status={item.status} />}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
