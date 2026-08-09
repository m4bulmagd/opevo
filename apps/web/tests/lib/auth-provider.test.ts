import { describe, expect, it } from "vitest";

import { requireWebAuthConfiguration, resolveWebAuthProvider } from "@/lib/auth/auth-provider";

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const developmentClerkConfig = {
  nodeEnv: "development",
  authProvider: "clerk",
  publishableKey: "pk_test_configured",
  secretKey: "clerk-test-fixture",
  backendBaseUrl: "http://api:8000",
};

function readSourceTree(directory: string): string {
  return readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) => {
      const path = join(directory, entry.name);
      return entry.isDirectory() ? readSourceTree(path) : readFileSync(path, "utf8");
    })
    .join("\n");
}

describe("resolveWebAuthProvider", () => {
  it("defaults a blank provider to Clerk", () => {
    expect(resolveWebAuthProvider({ nodeEnv: "development" })).toBe("clerk");
    expect(resolveWebAuthProvider({ nodeEnv: "development", authProvider: "  " })).toBe("clerk");
  });

  it("rejects unknown auth modes", () => {
    expect(() => resolveWebAuthProvider({ nodeEnv: "development", authProvider: "mock" })).toThrow(
      "Unsupported AUTH_PROVIDER",
    );
  });

  it("accepts local auth only in development", () => {
    expect(resolveWebAuthProvider({ nodeEnv: "development", authProvider: "local" })).toBe("local");
    expect(() => resolveWebAuthProvider({ nodeEnv: "production", authProvider: "local" })).toThrow(
      /AUTH_PROVIDER=local/,
    );
    expect(() => resolveWebAuthProvider({ nodeEnv: "test", authProvider: "local" })).toThrow(/development-only/);
  });

  it("accepts Supabase as a hosted provider", () => {
    expect(resolveWebAuthProvider({ nodeEnv: "production", authProvider: "supabase" })).toBe("supabase");
  });
});

describe("requireWebAuthConfiguration", () => {
  it.each([
    ["publishable key", { publishableKey: " " }, "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"],
    ["secret key", { secretKey: "" }, "CLERK_SECRET_KEY"],
  ])("rejects a missing Clerk %s in development", (_label, override, missingName) => {
    expect(() => requireWebAuthConfiguration({ ...developmentClerkConfig, ...override })).toThrow(missingName);
  });

  it("accepts explicit local development without Clerk keys", () => {
    expect(
      requireWebAuthConfiguration({
        nodeEnv: "development",
        authProvider: "local",
        publishableKey: "",
        secretKey: "",
        backendBaseUrl: "http://api:8000",
      }),
    ).toBe("local");
  });

  it("requires the backend URL only in production", () => {
    expect(() =>
      requireWebAuthConfiguration({
        ...developmentClerkConfig,
        nodeEnv: "production",
        backendBaseUrl: "",
      }),
    ).toThrow("API_BASE_URL or NEXT_PUBLIC_API_BASE_URL");
  });

  it("accepts complete production Clerk configuration", () => {
    expect(() => requireWebAuthConfiguration({ ...developmentClerkConfig, nodeEnv: "production" })).not.toThrow();
  });

  it("rejects local auth in production even when Clerk settings exist", () => {
    expect(() =>
      requireWebAuthConfiguration({ ...developmentClerkConfig, nodeEnv: "production", authProvider: "local" }),
    ).toThrow(/AUTH_PROVIDER=local/);
  });

  it.each([
    ["URL", { supabaseUrl: " " }, "NEXT_PUBLIC_SUPABASE_URL"],
    ["publishable key", { supabasePublishableKey: "" }, "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"],
  ])("rejects a missing Supabase %s", (_label, override, missingName) => {
    expect(() =>
      requireWebAuthConfiguration({
        nodeEnv: "production",
        authProvider: "supabase",
        supabaseUrl: "https://project.supabase.co",
        supabasePublishableKey: "sb_publishable_test",
        backendBaseUrl: "https://api.example.com",
        ...override,
      }),
    ).toThrow(missingName);
  });

  it("does not expose the local server credential as a public environment variable", () => {
    const source = readSourceTree(join(process.cwd(), "src"));

    expect(source).not.toContain("NEXT_PUBLIC_LOCAL_AUTH_TOKEN");
  });
});
