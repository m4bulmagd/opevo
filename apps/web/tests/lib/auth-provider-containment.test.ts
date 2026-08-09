import { describe, expect, it } from "vitest";

import { readdirSync, readFileSync } from "node:fs";
import { relative, resolve } from "node:path";

const sourceRoot = resolve(process.cwd(), "src");

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory() ? sourceFiles(path) : [path];
  });
}

describe("auth provider containment", () => {
  it.each([
    ["@clerk/", "lib/auth/providers/clerk/"],
    ["@supabase/", "lib/auth/providers/supabase/"],
  ])("keeps %s SDK imports inside %s", (sdkImport, providerDirectory) => {
    const offenders = sourceFiles(sourceRoot)
      .filter((path) => readFileSync(path, "utf8").includes(sdkImport))
      .map((path) => relative(sourceRoot, path))
      .filter((path) => !path.startsWith(providerDirectory));

    expect(offenders).toEqual([]);
  });

  it("keeps the legacy Clerk identity name out of shared source", () => {
    const offenders = sourceFiles(sourceRoot)
      .filter((path) => /clerk_user_id|clerkUserId/.test(readFileSync(path, "utf8")))
      .map((path) => relative(sourceRoot, path));

    expect(offenders).toEqual([]);
  });

  it("keeps provider leaf imports inside provider modules and explicit composition seams", () => {
    const compositionModules = new Set([
      "components/account/account-security-control.tsx",
      "components/auth/auth-entry-control.tsx",
      "components/auth/auth-provider-root.tsx",
      "components/auth/password-controls.tsx",
      "components/auth/sign-out-control.tsx",
      "lib/auth/account-identity.ts",
      "lib/auth/auth-callback.ts",
      "lib/auth/route-protection.ts",
      "lib/auth/server-session.ts",
    ]);
    const offenders = sourceFiles(sourceRoot)
      .filter((path) => readFileSync(path, "utf8").includes('from "@/lib/auth/providers/'))
      .map((path) => relative(sourceRoot, path))
      .filter((path) => !path.startsWith("lib/auth/providers/") && !compositionModules.has(path));

    expect(offenders).toEqual([]);
  });
});
