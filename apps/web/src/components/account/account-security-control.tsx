import { authProvider } from "@/lib/auth/auth-config";
import { ClerkSecurityButton } from "@/lib/auth/providers/clerk/security-button";
import { SupabaseSecurityButton } from "@/lib/auth/providers/supabase/security-button";

export function AccountSecurityControl() {
  if (authProvider === "clerk") {
    return <ClerkSecurityButton />;
  }
  if (authProvider === "supabase") {
    return <SupabaseSecurityButton />;
  }
  return null;
}
