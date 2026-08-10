import "server-only";

import { authProvider } from "@/lib/auth/auth-config";
import { exchangeSupabaseAuthCode } from "@/lib/auth/providers/supabase/callback";

export type AuthCallbackOutcome = "accepted" | "not-applicable" | "rejected";

export async function completeAuthCallback(code: string | null): Promise<AuthCallbackOutcome> {
  if (authProvider !== "supabase") {
    return "not-applicable";
  }
  if (!code) {
    return "rejected";
  }
  try {
    return (await exchangeSupabaseAuthCode(code)) ? "accepted" : "rejected";
  } catch {
    return "rejected";
  }
}
