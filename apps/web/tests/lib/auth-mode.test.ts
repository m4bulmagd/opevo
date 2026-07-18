import { describe, expect, it } from "vitest";

import { requireProductionWebAuth, resolveWebAuthMode } from "@/lib/auth/auth-mode";

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const productionConfig = {
  nodeEnv: "production",
  authMode: "clerk",
  publishableKey: "pk_live_test",
  secretKey: "sk_live_test",
  backendBaseUrl: "https://api.example.com",
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

describe("requireProductionWebAuth", () => {
  it("requires Clerk keys and a backend URL in production", () => {
    expect(() =>
      requireProductionWebAuth({
        nodeEnv: "production",
        authMode: "clerk",
        publishableKey: " ",
        secretKey: "",
        backendBaseUrl: undefined,
      }),
    ).toThrow("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY, CLERK_SECRET_KEY, API_BASE_URL or NEXT_PUBLIC_API_BASE_URL");
  });

  it("accepts complete production Clerk configuration", () => {
    expect(() => requireProductionWebAuth(productionConfig)).not.toThrow();
  });

  it("rejects local auth in production even when Clerk settings exist", () => {
    expect(() => requireProductionWebAuth({ ...productionConfig, authMode: "local" })).toThrow(/AUTH_MODE=local/);
  });

  it("does not expose the local server credential as a public environment variable", () => {
    const source = readSourceTree(join(process.cwd(), "src"));

    expect(source).not.toContain("NEXT_PUBLIC_LOCAL_AUTH_TOKEN");
  });
});
