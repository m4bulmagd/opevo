import { requireWebAuthConfiguration } from "@/lib/auth/auth-mode";

function isAbsent(value: string | undefined): boolean {
  return !value?.trim();
}

export function selectFirstNonblank(...candidates: Array<string | undefined>): string | undefined {
  return candidates.find((candidate) => !isAbsent(candidate));
}

const backendBaseUrl = selectFirstNonblank(process.env.API_BASE_URL, process.env.NEXT_PUBLIC_API_BASE_URL);

export const authMode = requireWebAuthConfiguration({
  nodeEnv: process.env.NODE_ENV,
  authMode: process.env.AUTH_MODE,
  publishableKey: process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: process.env.CLERK_SECRET_KEY,
  backendBaseUrl,
});
export const shouldWrapClerk = authMode === "clerk";
