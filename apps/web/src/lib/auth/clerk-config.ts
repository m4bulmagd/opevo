import { requireProductionWebAuth, resolveWebAuthMode } from "@/lib/auth/auth-mode";

export const CLERK_REQUIRED_ENV_VARS = ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "CLERK_SECRET_KEY"] as const;

type ProductionClerkConfig = {
  nodeEnv?: string;
  publishableKey?: string;
  secretKey?: string;
  backendBaseUrl?: string;
};

type ClerkMiddlewareConfig = {
  nodeEnv?: string;
  clerkConfigured: boolean;
};

function isAbsent(value: string | undefined): boolean {
  return !value?.trim();
}

export function selectFirstNonblank(...candidates: Array<string | undefined>): string | undefined {
  return candidates.find((candidate) => !isAbsent(candidate));
}

export function requireProductionClerkConfig(config: ProductionClerkConfig): void {
  requireProductionWebAuth({ ...config, authMode: "clerk" });
}

export function shouldUseClerkMiddleware(config: ClerkMiddlewareConfig): boolean {
  return config.nodeEnv === "production" || config.clerkConfigured;
}

const publishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const secretKey = process.env.CLERK_SECRET_KEY;
const backendBaseUrl = selectFirstNonblank(process.env.API_BASE_URL, process.env.NEXT_PUBLIC_API_BASE_URL);

requireProductionWebAuth({
  nodeEnv: process.env.NODE_ENV,
  authMode: process.env.AUTH_MODE,
  publishableKey,
  secretKey,
  backendBaseUrl,
});

export const authMode = resolveWebAuthMode({ nodeEnv: process.env.NODE_ENV, authMode: process.env.AUTH_MODE });
export const isClerkConfigured = !isAbsent(publishableKey) && !isAbsent(secretKey);
export const isAppAuthConfigured = authMode === "local" || isClerkConfigured;
export const shouldWrapClerk = authMode === "clerk" && isClerkConfigured;
