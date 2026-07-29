import type { ReactNode } from "react";

import Link from "next/link";

import { History, LogOut, PhoneCall, Search } from "lucide-react";

import { ThemeSwitcher } from "@/app/(app)/dashboard/_components/sidebar/theme-switcher";
import { CapabilityBadge } from "@/components/product/capability-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { MobileWorkspaceNavigation } from "@/components/workspace/mobile-workspace-navigation";
import { type WorkspaceCallerIdentity, WorkspaceCallerStatus } from "@/components/workspace/workspace-caller-status";
import { WorkspaceNotificationsPreview } from "@/components/workspace/workspace-notifications-preview";
import { authMode, shouldWrapClerk } from "@/lib/auth/clerk-config";

export async function resolveWorkspaceAccountControl(): Promise<ReactNode> {
  if (authMode === "local") {
    return <Badge variant="secondary">Local development</Badge>;
  }

  if (!shouldWrapClerk) {
    return null;
  }

  const { SignOutButton } = await import("@clerk/nextjs");

  return (
    <SignOutButton redirectUrl="/">
      <Button aria-label="Sign out" className="size-11" size="icon" variant="ghost">
        <LogOut aria-hidden="true" />
      </Button>
    </SignOutButton>
  );
}

type WorkspaceHeaderProps = {
  accountControl: ReactNode;
  activeCaller: WorkspaceCallerIdentity | null;
  agentName: string;
};

export function WorkspaceHeader({ accountControl, activeCaller, agentName }: WorkspaceHeaderProps) {
  return (
    <header className="sticky top-0 z-20 flex items-center gap-2 border-border border-b bg-background/90 px-4 py-3 backdrop-blur lg:rounded-2xl lg:border lg:bg-card lg:shadow-card">
      <div className="flex min-w-0 items-center gap-2">
        <MobileWorkspaceNavigation />
        <Link
          aria-label="Presvo overview"
          className="hidden min-h-11 min-w-0 items-center gap-2 rounded-md font-semibold tracking-tight outline-none focus-visible:ring-3 focus-visible:ring-ring/50 sm:inline-flex lg:hidden"
          href="/dashboard"
          prefetch={false}
        >
          <span className="inline-flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <PhoneCall aria-hidden="true" />
          </span>
          <span className="truncate">Presvo</span>
        </Link>
      </div>
      <form
        action="/dashboard/calls"
        aria-label="Call search"
        className="hidden min-w-48 flex-1 md:flex"
        data-header-item="search"
        method="get"
      >
        <InputGroup className="h-11 w-full max-w-sm bg-background">
          <InputGroupInput
            aria-label="Search calls"
            autoComplete="off"
            name="q"
            placeholder="Search calls, callers or notes"
            type="search"
          />
          <InputGroupAddon>
            <Search aria-hidden="true" />
          </InputGroupAddon>
        </InputGroup>
        <button className="sr-only" type="submit">
          Search
        </button>
      </form>

      <WorkspaceCallerStatus agentName={agentName} caller={activeCaller} />

      <Button asChild className="min-h-11 px-2.5">
        <Link aria-label="Live call" data-header-item="live-call" href="/dashboard/live-call" prefetch={false}>
          <PhoneCall aria-hidden="true" data-icon="inline-start" />
          <span className="hidden xl:inline">Live call</span>
          <CapabilityBadge
            className="border-primary-foreground/25 bg-primary-foreground/15 text-primary-foreground"
            status="preview"
          />
        </Link>
      </Button>

      <div data-header-item="notifications">
        <WorkspaceNotificationsPreview />
      </div>

      <Button asChild className="hidden min-h-11 md:inline-flex" variant="outline">
        <Link data-header-item="call-history" href="/dashboard/calls" prefetch={false}>
          <History aria-hidden="true" data-icon="inline-start" />
          Call history
        </Link>
      </Button>

      <div className="hidden xl:block" data-header-item="account-control">
        {accountControl}
      </div>

      <div data-header-item="theme-control">
        <ThemeSwitcher />
      </div>
    </header>
  );
}
