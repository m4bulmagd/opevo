import "server-only";

import { cookies } from "next/headers";

import { createServerClient } from "@supabase/ssr";

import { getSupabasePublicConfiguration } from "@/lib/auth/providers/supabase/config";

export async function createSupabaseServerClient() {
  const { url, publishableKey } = getSupabasePublicConfiguration();
  const cookieStore = await cookies();

  return createServerClient(url, publishableKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Server Components cannot write cookies. The proxy refreshes them.
        }
      },
    },
  });
}
