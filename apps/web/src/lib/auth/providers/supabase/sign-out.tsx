"use client";

import { useState } from "react";

import { useRouter } from "next/navigation";

import { toast } from "sonner";

import { SignOutButtonContent, type SignOutVariant } from "@/components/auth/sign-out-button-content";
import { createSupabaseBrowserClient } from "@/lib/auth/providers/supabase/browser-client";

export function SupabaseSignOut({ variant }: Readonly<{ variant: SignOutVariant }>) {
  const [pending, setPending] = useState(false);
  const router = useRouter();

  return (
    <SignOutButtonContent
      disabled={pending}
      onClick={async () => {
        if (pending) {
          return;
        }
        setPending(true);
        try {
          const { error } = await createSupabaseBrowserClient().auth.signOut();
          if (error) {
            toast.error("Unable to sign out right now. Please try again.");
            setPending(false);
            return;
          }
        } catch {
          toast.error("Unable to sign out right now. Please try again.");
          setPending(false);
          return;
        }
        router.replace("/");
        router.refresh();
      }}
      variant={variant}
    />
  );
}
