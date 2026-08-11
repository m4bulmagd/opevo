export type WebAuthProvider = "clerk" | "local" | "supabase";

type WebAuthProviderInput = {
  nodeEnv?: string;
  authProvider?: string;
};

export type WebAuthConfigurationInput = WebAuthProviderInput & {
  publishableKey?: string;
  secretKey?: string;
  supabaseUrl?: string;
  supabasePublishableKey?: string;
  backendBaseUrl?: string;
};

const CLERK_PUBLISHABLE_KEY = "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY";
const CLERK_SECRET_KEY = "CLERK_SECRET_KEY";
const SUPABASE_URL = "NEXT_PUBLIC_SUPABASE_URL";
const SUPABASE_PUBLISHABLE_KEY = "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY";
const BACKEND_BASE_URL = "API_BASE_URL or NEXT_PUBLIC_API_BASE_URL";

function isBlank(value: string | undefined): boolean {
  return !value?.trim();
}

export function resolveWebAuthProvider(input: WebAuthProviderInput): WebAuthProvider {
  const provider = input.authProvider?.trim() || "supabase";

  if (provider !== "clerk" && provider !== "local" && provider !== "supabase") {
    throw new Error("Unsupported AUTH_PROVIDER; expected 'clerk', 'supabase', or 'local'");
  }

  if (provider === "local" && input.nodeEnv !== "development") {
    throw new Error("AUTH_PROVIDER=local is development-only");
  }

  return provider;
}

export function requireWebAuthConfiguration(input: WebAuthConfigurationInput): WebAuthProvider {
  const provider = resolveWebAuthProvider(input);
  const missing = [
    provider === "clerk" && isBlank(input.publishableKey) ? CLERK_PUBLISHABLE_KEY : undefined,
    provider === "clerk" && isBlank(input.secretKey) ? CLERK_SECRET_KEY : undefined,
    provider === "supabase" && isBlank(input.supabaseUrl) ? SUPABASE_URL : undefined,
    provider === "supabase" && isBlank(input.supabasePublishableKey) ? SUPABASE_PUBLISHABLE_KEY : undefined,
    input.nodeEnv === "production" && isBlank(input.backendBaseUrl) ? BACKEND_BASE_URL : undefined,
  ].filter((setting): setting is string => Boolean(setting));

  if (missing.length > 0) {
    throw new Error(`Missing required authentication settings: ${missing.join(", ")}`);
  }

  return provider;
}
