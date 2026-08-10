import "server-only";

import { createSupabaseServerClient } from "@/lib/auth/providers/supabase/server-client";

export async function exchangeSupabaseAuthCode(code: string): Promise<boolean> {
  const supabase = await createSupabaseServerClient();
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  return !error;
}
