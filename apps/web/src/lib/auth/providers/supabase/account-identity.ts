import "server-only";

import { createSupabaseServerClient } from "@/lib/auth/providers/supabase/server-client";
import type { AccountIdentity } from "@/lib/types/account-settings";

export async function resolveSupabaseAccountIdentity(): Promise<AccountIdentity> {
  try {
    const supabase = await createSupabaseServerClient();
    const { data, error } = await supabase.auth.getClaims();
    const email = data?.claims?.email;
    return {
      email: !error && typeof email === "string" ? email : null,
      securityMode: "managed",
    };
  } catch {
    return { email: null, securityMode: "managed" };
  }
}
