import "server-only";

import { requireWebAuthConfiguration } from "@/lib/auth/auth-provider";

function isAbsent(value: string | undefined): boolean {
  return !value?.trim();
}

export function selectFirstNonblank(...candidates: Array<string | undefined>): string | undefined {
  return candidates.find((candidate) => !isAbsent(candidate));
}

const backendBaseUrl = selectFirstNonblank(process.env.API_BASE_URL, process.env.NEXT_PUBLIC_API_BASE_URL);

export const authProvider = requireWebAuthConfiguration({
  nodeEnv: process.env.NODE_ENV,
  authProvider: process.env.AUTH_PROVIDER,
  publishableKey: process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: process.env.CLERK_SECRET_KEY,
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL,
  supabasePublishableKey: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
  backendBaseUrl,
});
