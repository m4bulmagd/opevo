"use client";

import { useId } from "react";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { motion, useReducedMotion } from "motion/react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { MobileMoreSheet } from "@/components/workspace/mobile-more-sheet";
import { SPRING_LAYOUT } from "@/lib/motion/tokens";
import { dashboardItems, isDashboardItemActive, type NavItem } from "@/navigation/dashboard-items";

type NavigationVariant = "rail" | "mobile";

type WorkspaceNavigationProps = {
  agentName: string;
  variant: NavigationVariant;
};

type NavigationLinkProps = {
  active: boolean;
  item: NavItem;
  layoutId: string;
  variant: NavigationVariant;
};

// Adapted from BeUI's shared-layout-bg:
// https://beui.dev/components/motion/shared-layout-bg
// The shared marker represents authoritative route state, never hover.
function ActiveNavigationMarker({ layoutId }: { layoutId: string }) {
  const shouldReduceMotion = useReducedMotion();
  const className = "absolute inset-1 rounded-md bg-sidebar-active";

  if (shouldReduceMotion) {
    return <span aria-hidden="true" className={className} data-motion="static" data-slot="active-navigation-marker" />;
  }

  return (
    <motion.span
      aria-hidden="true"
      className={className}
      data-layout-id={layoutId}
      data-motion="layout"
      data-slot="active-navigation-marker"
      layoutId={layoutId}
      transition={SPRING_LAYOUT}
    />
  );
}

function NavigationLink({ active, item, layoutId, variant }: NavigationLinkProps) {
  const link = (
    <Link
      aria-current={active ? "page" : undefined}
      aria-label={item.title}
      className={
        variant === "rail"
          ? "relative flex min-h-11 min-w-11 items-center gap-3 overflow-hidden rounded-md px-3 text-sidebar-foreground text-sm outline-none transition-colors hover:bg-sidebar-accent focus-visible:ring-3 focus-visible:ring-sidebar-ring/60 lg:px-4"
          : "relative flex min-h-11 min-w-11 flex-col items-center justify-center gap-1 overflow-hidden rounded-md px-1 text-sidebar-foreground outline-none transition-colors hover:bg-sidebar-accent focus-visible:ring-3 focus-visible:ring-sidebar-ring/60"
      }
      href={item.href}
      prefetch={false}
    >
      {active ? <ActiveNavigationMarker layoutId={layoutId} /> : null}
      <item.icon aria-hidden="true" className="relative z-10 size-5 shrink-0" />
      <span
        className={
          variant === "rail"
            ? "relative z-10 hidden min-w-0 truncate lg:block"
            : "relative z-10 max-w-full truncate text-xs"
        }
      >
        {item.title}
      </span>
    </Link>
  );

  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side={variant === "mobile" ? "top" : "right"} sideOffset={8}>
        {item.title}
      </TooltipContent>
    </Tooltip>
  );
}

export function WorkspaceNavigation({ agentName, variant }: WorkspaceNavigationProps) {
  const pathname = usePathname();
  const navigationId = useId();
  const layoutId = `workspace-active-${navigationId}`;
  const items = dashboardItems(agentName);

  if (variant === "mobile") {
    const primaryItems = items.slice(0, 3);
    const moreItems = items.slice(3);

    return (
      <nav
        aria-label="Mobile workspace navigation"
        className="fixed inset-x-0 bottom-0 z-40 grid min-h-16 grid-cols-4 border-sidebar-border border-t bg-sidebar px-2 pt-1 pb-[env(safe-area-inset-bottom)] shadow-raised md:hidden"
      >
        {primaryItems.map((item) => (
          <NavigationLink
            active={isDashboardItemActive(pathname, item.href)}
            item={item}
            key={item.href}
            layoutId={layoutId}
            variant="mobile"
          />
        ))}
        <MobileMoreSheet items={moreItems} pathname={pathname} />
      </nav>
    );
  }

  return (
    <nav aria-label="Workspace navigation" className="flex min-w-0 flex-1 flex-col gap-1 px-2 lg:px-3">
      {items.map((item) => (
        <NavigationLink
          active={isDashboardItemActive(pathname, item.href)}
          item={item}
          key={item.href}
          layoutId={layoutId}
          variant="rail"
        />
      ))}
    </nav>
  );
}
