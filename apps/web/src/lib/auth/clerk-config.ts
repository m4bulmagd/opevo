export const CLERK_REQUIRED_ENV_VARS = ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "CLERK_SECRET_KEY"] as const;

const BACKEND_REQUIRED_ENV_VAR = "API_BASE_URL or NEXT_PUBLIC_API_BASE_URL";

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
  if (config.nodeEnv !== "production") {
    return;
  }

  const missing = [
    isAbsent(config.publishableKey) ? CLERK_REQUIRED_ENV_VARS[0] : undefined,
    isAbsent(config.secretKey) ? CLERK_REQUIRED_ENV_VARS[1] : undefined,
    isAbsent(config.backendBaseUrl) ? BACKEND_REQUIRED_ENV_VAR : undefined,
  ].filter((name): name is string => Boolean(name));

  if (missing.length > 0) {
    throw new Error(`Missing required production settings: ${missing.join(", ")}`);
  }
}

export function shouldUseClerkMiddleware(config: ClerkMiddlewareConfig): boolean {
  return config.nodeEnv === "production" || config.clerkConfigured;
}

requireProductionClerkConfig({
  nodeEnv: process.env.NODE_ENV,
  publishableKey: process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  secretKey: process.env.CLERK_SECRET_KEY,
  backendBaseUrl: selectFirstNonblank(process.env.API_BASE_URL, process.env.NEXT_PUBLIC_API_BASE_URL),
});

export const isClerkConfigured = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY);
