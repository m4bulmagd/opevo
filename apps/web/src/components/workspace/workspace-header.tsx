import type { ReactNode } from "react";

import Link from "next/link";

import { LogOut, PhoneCall } from "lucide-react";

import { ThemeSwitcher } from "@/app/(app)/dashboard/_components/sidebar/theme-switcher";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

export function WorkspaceHeader({ accountControl }: { accountControl: ReactNode }) {
  return (
    <header className="sticky top-0 z-20 flex min-h-16 items-center justify-between gap-3 border-b bg-surface px-4 md:px-8 lg:px-10">
      <Link
        aria-label="Presvo overview"
        className="inline-flex min-h-11 items-center gap-2 rounded-md font-semibold tracking-tight outline-none focus-visible:ring-3 focus-visible:ring-ring/50 md:hidden"
        href="/dashboard"
        prefetch={false}
      >
        <span className="inline-flex size-9 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <PhoneCall aria-hidden="true" />
        </span>
        Presvo
      </Link>
      <p className="hidden font-medium text-muted-foreground text-sm md:block">Customer workspace</p>
      <div className="flex items-center gap-2">
        {accountControl}
        <ThemeSwitcher />
      </div>
    </header>
  );
}
