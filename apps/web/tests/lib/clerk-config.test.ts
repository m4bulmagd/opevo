import { afterEach, describe, expect, it, vi } from "vitest";

async function importClerkConfig() {
  vi.resetModules();
  return import("@/lib/auth/clerk-config");
}

function stubConfiguredDevelopmentClerk() {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("AUTH_MODE", "clerk");
  vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_configured");
  vi.stubEnv("CLERK_SECRET_KEY", "sk_test_configured");
  vi.stubEnv("API_BASE_URL", "http://api:8000");
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://127.0.0.1:8000");
}

describe("clerk configuration module", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("fails module initialization for incomplete development Clerk mode", async () => {
    stubConfiguredDevelopmentClerk();
    vi.stubEnv("CLERK_SECRET_KEY", " ");

    await expect(importClerkConfig()).rejects.toThrow("CLERK_SECRET_KEY");
  });

  it("derives Clerk wrappers from a valid selected mode", async () => {
    stubConfiguredDevelopmentClerk();

    const config = await importClerkConfig();

    expect(config.authMode).toBe("clerk");
    expect(config.shouldWrapClerk).toBe(true);
  });

  it("permits wrapper pass-through only in explicit local development", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("API_BASE_URL", "http://api:8000");

    const config = await importClerkConfig();

    expect(config.authMode).toBe("local");
    expect(config.shouldWrapClerk).toBe(false);
  });

  it("uses the public backend URL when the server-only candidate is blank", async () => {
    const publicFallback = "https://public-api.example.com";
    stubConfiguredDevelopmentClerk();
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("API_BASE_URL", " ");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", publicFallback);

    const config = await importClerkConfig();

    expect(config.selectFirstNonblank(" ", publicFallback)).toBe(publicFallback);
  });
});
