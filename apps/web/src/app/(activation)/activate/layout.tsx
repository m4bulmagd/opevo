import type { ReactNode } from "react";

import Link from "next/link";

import { CreditCard, LogOut, Phone, PhoneCall, UserRound } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { authMode, shouldWrapClerk } from "@/lib/auth/clerk-config";

async function AccountControl() {
  if (authMode === "local") {
    return (
      <Badge variant="secondary">
        <span className="sm:hidden">Local</span>
        <span className="hidden sm:inline">Local development</span>
      </Badge>
    );
  }

  if (!shouldWrapClerk) {
    return null;
  }

  const { SignOutButton } = await import("@clerk/nextjs");

  return (
    <SignOutButton redirectUrl="/">
      <Button variant="ghost" size="sm" aria-label="Sign out">
        <LogOut data-icon="inline-start" />
        <span className="hidden sm:inline">Sign out</span>
      </Button>
    </SignOutButton>
  );
}

export default async function ActivationLayout({ children }: Readonly<{ children: ReactNode }>) {
  const accountControl = await AccountControl();

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <a
        href="#activation-content"
        className="sr-only rounded-md bg-background px-3 py-2 focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:ring-3 focus:ring-ring/50"
      >
        Skip to activation
      </a>
      <header className="border-b bg-card">
        <div className="mx-auto flex min-h-16 w-full max-w-6xl items-center justify-between gap-3 px-4 sm:px-8">
          <Link
            href="/"
            className="inline-flex items-center gap-2 rounded-md font-semibold tracking-tight outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <span className="inline-flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground">
              <PhoneCall aria-hidden="true" />
            </span>
            Presvo
          </Link>

          <div className="flex items-center gap-1 sm:gap-2">
            <nav aria-label="Account navigation" className="flex items-center">
              <Button asChild variant="ghost" size="sm">
                <Link href="/dashboard" aria-label="Account">
                  <UserRound data-icon="inline-start" />
                  <span className="hidden md:inline">Account</span>
                </Link>
              </Button>
              <Button asChild variant="ghost" size="sm">
                <Link href="/dashboard/billing" aria-label="Billing">
                  <CreditCard data-icon="inline-start" />
                  <span className="hidden md:inline">Billing</span>
                </Link>
              </Button>
              <Button asChild variant="ghost" size="sm">
                <Link href="/dashboard/calls" aria-label="Calls">
                  <Phone data-icon="inline-start" />
                  <span className="hidden md:inline">Calls</span>
                </Link>
              </Button>
            </nav>
            <Separator orientation="vertical" className="mx-1 hidden h-5 sm:block" />
            {accountControl}
          </div>
        </div>
      </header>
      {children}
    </div>
  );
}
