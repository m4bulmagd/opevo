import type { ReactNode } from "react";

import Link from "next/link";

import { PhoneCall } from "lucide-react";

import { ClerkSignOut } from "@/components/auth/clerk-sign-out";
import { Badge } from "@/components/ui/badge";
import { authMode, shouldWrapClerk } from "@/lib/auth/clerk-config";

function AccountControl() {
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

  return <ClerkSignOut variant="activation" />;
}

export default function ActivationLayout({ children }: Readonly<{ children: ReactNode }>) {
  const accountControl = AccountControl();

  return (
    <div className="flex min-h-svh flex-col bg-background text-foreground">
      <a
        className="sr-only rounded-md bg-background px-3 py-2 focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:ring-3 focus:ring-ring/50"
        href="#activation-content"
      >
        Skip to activation
      </a>
      <header className="border-border border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex min-h-16 w-full max-w-5xl items-center justify-between gap-3 px-4 sm:px-6">
          <Link
            aria-label="Presvo home"
            className="inline-flex items-center gap-2 rounded-md font-semibold tracking-tight outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            href="/"
          >
            <span className="inline-flex size-9 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <PhoneCall aria-hidden="true" className="size-4" />
            </span>
            Presvo
          </Link>
          <div className="flex items-center gap-2">{accountControl}</div>
        </div>
      </header>
      {children}
    </div>
  );
}
