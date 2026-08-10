import "server-only";

import { authProvider } from "@/lib/auth/auth-config";
import { resolveClerkAccountIdentity } from "@/lib/auth/providers/clerk/account-identity";
import { resolveSupabaseAccountIdentity } from "@/lib/auth/providers/supabase/account-identity";
import type { AccountIdentity } from "@/lib/types/account-settings";

export async function resolveAccountIdentity(): Promise<AccountIdentity> {
  if (authProvider === "local") {
    return { email: null, securityMode: "unavailable" };
  }
  return authProvider === "supabase" ? resolveSupabaseAccountIdentity() : resolveClerkAccountIdentity();
}
