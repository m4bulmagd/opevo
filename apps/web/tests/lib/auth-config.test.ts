import { afterEach, describe, expect, it, vi } from "vitest";

async function importAuthConfig() {
  vi.resetModules();
  return import("@/lib/auth/auth-config");
}

function stubConfiguredDevelopmentClerk() {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("AUTH_PROVIDER", "clerk");
  vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_configured");
  vi.stubEnv("CLERK_SECRET_KEY", "clerk-test-fixture");
  vi.stubEnv("API_BASE_URL", "http://api:8000");
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://127.0.0.1:8000");
}

describe("authentication configuration module", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("fails module initialization for incomplete development Clerk mode", async () => {
    stubConfiguredDevelopmentClerk();
    vi.stubEnv("CLERK_SECRET_KEY", " ");

    await expect(importAuthConfig()).rejects.toThrow("CLERK_SECRET_KEY");
  });

  it("derives Clerk wrappers from a valid selected mode", async () => {
    stubConfiguredDevelopmentClerk();

    const config = await importAuthConfig();

    expect(config.authProvider).toBe("clerk");
  });

  it("permits wrapper pass-through only in explicit local development", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_PROVIDER", "local");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("API_BASE_URL", "http://api:8000");

    const config = await importAuthConfig();

    expect(config.authProvider).toBe("local");
  });

  it("uses the public backend URL when the server-only candidate is blank", async () => {
    const publicFallback = "https://public-api.example.com";
    stubConfiguredDevelopmentClerk();
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("API_BASE_URL", " ");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", publicFallback);

    const config = await importAuthConfig();

    expect(config.selectFirstNonblank(" ", publicFallback)).toBe(publicFallback);
  });

  it("selects Supabase without requiring Clerk credentials", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("AUTH_PROVIDER", "supabase");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("API_BASE_URL", "https://api.example.com");

    const config = await importAuthConfig();

    expect(config.authProvider).toBe("supabase");
  });
});
