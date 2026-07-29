"use client";

import { useClerk } from "@clerk/nextjs";
import { KeyRound } from "lucide-react";

import { Button } from "@/components/ui/button";

export function ClerkSecurityButton() {
  const { openUserProfile } = useClerk();

  return (
    <Button className="min-h-11" onClick={() => openUserProfile()} type="button" variant="outline">
      <KeyRound aria-hidden data-icon="inline-start" />
      Manage password and sign-in
    </Button>
  );
}
