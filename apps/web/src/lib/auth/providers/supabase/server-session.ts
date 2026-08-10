import "server-only";

import { createSupabaseServerClient } from "@/lib/auth/providers/supabase/server-client";
import type { ServerSessionState } from "@/lib/auth/session-contract";

export async function getSupabaseServerSession(): Promise<ServerSessionState> {
  const supabase = await createSupabaseServerClient();
  const claimsResult = await supabase.auth.getClaims();
  const authenticated = Boolean(claimsResult.data?.claims?.sub) && !claimsResult.error;

  return {
    isAuthenticated: authenticated,
    getToken: async () => {
      if (!authenticated) {
        return null;
      }
      const { data, error } = await supabase.auth.getSession();
      return error ? null : (data.session?.access_token ?? null);
    },
  };
}
