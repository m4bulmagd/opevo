"use client";

import type { ReactElement } from "react";

import { SignOutButton } from "@clerk/nextjs";
import { LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";

type ClerkSignOutProps = {
  variant: "activation" | "mobile" | "workspace";
};

function signOutControl(variant: ClerkSignOutProps["variant"]): ReactElement {
  if (variant === "activation") {
    return (
      <Button variant="ghost" size="sm" aria-label="Sign out">
        <LogOut data-icon="inline-start" />
        <span className="hidden sm:inline">Sign out</span>
      </Button>
    );
  }

  if (variant === "mobile") {
    return (
      <Button aria-label="Sign out" className="min-h-11 w-full justify-start px-3" variant="ghost">
        <LogOut aria-hidden="true" data-icon="inline-start" />
        Sign out
      </Button>
    );
  }

  return (
    <Button aria-label="Sign out" className="size-11" size="icon" variant="ghost">
      <LogOut aria-hidden="true" />
    </Button>
  );
}

export function ClerkSignOut({ variant }: ClerkSignOutProps) {
  const control = signOutControl(variant);

  return <SignOutButton redirectUrl="/">{control}</SignOutButton>;
}
