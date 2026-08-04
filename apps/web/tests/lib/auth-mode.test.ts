import { describe, expect, it } from "vitest";

import { requireWebAuthConfiguration, resolveWebAuthMode } from "@/lib/auth/auth-mode";

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const developmentClerkConfig = {
  nodeEnv: "development",
  authMode: "clerk",
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

describe("resolveWebAuthMode", () => {
  it("defaults blank auth modes to Clerk", () => {
    expect(resolveWebAuthMode({ nodeEnv: "development" })).toBe("clerk");
    expect(resolveWebAuthMode({ nodeEnv: "development", authMode: "  " })).toBe("clerk");
  });

  it("rejects unknown auth modes", () => {
    expect(() => resolveWebAuthMode({ nodeEnv: "development", authMode: "mock" })).toThrow("Unsupported AUTH_MODE");
  });

  it("accepts local auth only in development", () => {
    expect(resolveWebAuthMode({ nodeEnv: "development", authMode: "local" })).toBe("local");
    expect(() => resolveWebAuthMode({ nodeEnv: "production", authMode: "local" })).toThrow(/AUTH_MODE=local/);
    expect(() => resolveWebAuthMode({ nodeEnv: "test", authMode: "local" })).toThrow(/development-only/);
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
        authMode: "local",
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
      requireWebAuthConfiguration({ ...developmentClerkConfig, nodeEnv: "production", authMode: "local" }),
    ).toThrow(/AUTH_MODE=local/);
  });

  it("does not expose the local server credential as a public environment variable", () => {
    const source = readSourceTree(join(process.cwd(), "src"));

    expect(source).not.toContain("NEXT_PUBLIC_LOCAL_AUTH_TOKEN");
  });
});
