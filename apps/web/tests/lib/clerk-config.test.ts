import { afterEach, describe, expect, it, vi } from "vitest";

import { requireProductionClerkConfig, selectFirstNonblank, shouldUseClerkMiddleware } from "@/lib/auth/clerk-config";

const productionConfig = {
  nodeEnv: "production",
  publishableKey: "pk_live_test",
  secretKey: "sk_live_test",
  backendBaseUrl: "https://api.example.com",
};

describe("requireProductionClerkConfig", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("throws when production Clerk publishable configuration is absent", () => {
    expect(() =>
      requireProductionClerkConfig({
        ...productionConfig,
        publishableKey: "",
      }),
    ).toThrow("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY");
  });

  it("throws when production Clerk secret configuration is absent", () => {
    expect(() =>
      requireProductionClerkConfig({
        ...productionConfig,
        secretKey: "",
      }),
    ).toThrow("CLERK_SECRET_KEY");
  });

  it("throws when production backend configuration is absent", () => {
    expect(() =>
      requireProductionClerkConfig({
        ...productionConfig,
        backendBaseUrl: "",
      }),
    ).toThrow("API_BASE_URL or NEXT_PUBLIC_API_BASE_URL");
  });

  it("reports every absent production variable", () => {
    expect(() =>
      requireProductionClerkConfig({
        nodeEnv: "production",
        publishableKey: " ",
        secretKey: "",
        backendBaseUrl: undefined,
      }),
    ).toThrow("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY, CLERK_SECRET_KEY, API_BASE_URL or NEXT_PUBLIC_API_BASE_URL");
  });

  it("accepts complete production configuration", () => {
    expect(() => requireProductionClerkConfig(productionConfig)).not.toThrow();
  });

  it("keeps local setup usable without production configuration", () => {
    expect(() =>
      requireProductionClerkConfig({
        nodeEnv: "development",
        publishableKey: "",
        secretKey: "",
        backendBaseUrl: "",
      }),
    ).not.toThrow();
  });

  it("uses the public backend URL when the server-only candidate is blank", () => {
    const publicFallback = "https://public-api.example.com";
    const backendBaseUrl = selectFirstNonblank(" ", publicFallback);

    expect(backendBaseUrl).toBe(publicFallback);
    expect(() =>
      requireProductionClerkConfig({
        ...productionConfig,
        backendBaseUrl,
      }),
    ).not.toThrow();
  });

  it("initializes production with a public backend URL when the server-only value is blank", async () => {
    vi.resetModules();
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_live_test");
    vi.stubEnv("CLERK_SECRET_KEY", "sk_live_test");
    vi.stubEnv("API_BASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://public-api.example.com");

    await expect(import("@/lib/auth/clerk-config")).resolves.toBeDefined();
  });

  it("fails during server initialization when production configuration is absent", async () => {
    vi.resetModules();
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("API_BASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");

    await expect(import("@/lib/auth/clerk-config")).rejects.toThrow("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY");
  });

  it("does not produce a proxy default export for invalid production configuration", async () => {
    vi.resetModules();
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("API_BASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");

    await expect(import("@/proxy")).rejects.toThrow("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY");
  });

  it("uses Clerk middleware unconditionally in production", () => {
    expect(
      shouldUseClerkMiddleware({
        nodeEnv: "production",
        clerkConfigured: false,
      }),
    ).toBe(true);
  });

  it("allows pass-through only when local Clerk configuration is absent", () => {
    expect(
      shouldUseClerkMiddleware({
        nodeEnv: "development",
        clerkConfigured: false,
      }),
    ).toBe(false);
    expect(
      shouldUseClerkMiddleware({
        nodeEnv: "development",
        clerkConfigured: true,
      }),
    ).toBe(true);
  });
});
