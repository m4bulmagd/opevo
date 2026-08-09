"use client";

import { createBrowserClient } from "@supabase/ssr";

import { getSupabasePublicConfiguration } from "@/lib/auth/providers/supabase/config";

export function createSupabaseBrowserClient() {
  const { url, publishableKey } = getSupabasePublicConfiguration();
  return createBrowserClient(url, publishableKey);
}
