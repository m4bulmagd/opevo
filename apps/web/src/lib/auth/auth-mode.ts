export type WebAuthMode = "clerk" | "local";

type WebAuthModeInput = {
  nodeEnv?: string;
  authMode?: string;
};

export type WebAuthConfigurationInput = WebAuthModeInput & {
  publishableKey?: string;
  secretKey?: string;
  backendBaseUrl?: string;
};

const CLERK_PUBLISHABLE_KEY = "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY";
const CLERK_SECRET_KEY = "CLERK_SECRET_KEY";
const BACKEND_BASE_URL = "API_BASE_URL or NEXT_PUBLIC_API_BASE_URL";

function isBlank(value: string | undefined): boolean {
  return !value?.trim();
}

export function resolveWebAuthMode(input: WebAuthModeInput): WebAuthMode {
  const mode = input.authMode?.trim() || "clerk";

  if (mode !== "clerk" && mode !== "local") {
    throw new Error("Unsupported AUTH_MODE; expected 'clerk' or 'local'");
  }

  if (mode === "local" && input.nodeEnv !== "development") {
    throw new Error("AUTH_MODE=local is development-only");
  }

  return mode;
}

export function requireWebAuthConfiguration(input: WebAuthConfigurationInput): WebAuthMode {
  const mode = resolveWebAuthMode(input);
  const missing = [
    mode === "clerk" && isBlank(input.publishableKey) ? CLERK_PUBLISHABLE_KEY : undefined,
    mode === "clerk" && isBlank(input.secretKey) ? CLERK_SECRET_KEY : undefined,
    input.nodeEnv === "production" && isBlank(input.backendBaseUrl) ? BACKEND_BASE_URL : undefined,
  ].filter((setting): setting is string => Boolean(setting));

  if (missing.length > 0) {
    throw new Error(`Missing required authentication settings: ${missing.join(", ")}`);
  }

  return mode;
}
