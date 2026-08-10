"use client";

import { SignOutButton } from "@clerk/nextjs";

import { SignOutButtonContent, type SignOutVariant } from "@/components/auth/sign-out-button-content";

export function ClerkSignOut({ variant }: Readonly<{ variant: SignOutVariant }>) {
  return (
    <SignOutButton redirectUrl="/">
      <SignOutButtonContent variant={variant} />
    </SignOutButton>
  );
}
