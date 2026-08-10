import type { SignOutVariant } from "@/components/auth/sign-out-button-content";
import { authProvider } from "@/lib/auth/auth-config";
import { ClerkSignOut } from "@/lib/auth/providers/clerk/sign-out";
import { SupabaseSignOut } from "@/lib/auth/providers/supabase/sign-out";

export function SignOutControl({ variant }: Readonly<{ variant: SignOutVariant }>) {
  if (authProvider === "clerk") {
    return <ClerkSignOut variant={variant} />;
  }
  if (authProvider === "supabase") {
    return <SupabaseSignOut variant={variant} />;
  }
  return null;
}
