import { afterEach, describe, expect, it, vi } from "vitest";

async function resolveForProvider({
  authProvider,
  currentUser,
  getClaims,
}: {
  authProvider: "clerk" | "local" | "supabase";
  currentUser?: () => Promise<unknown>;
  getClaims?: () => Promise<unknown>;
}) {
  vi.resetModules();
  vi.doMock("@/lib/auth/auth-config", () => ({ authProvider }));
  if (currentUser) {
    vi.doMock("@clerk/nextjs/server", () => ({ currentUser }));
  }
  if (getClaims) {
    vi.doMock("@/lib/auth/providers/supabase/server-client", () => ({
      createSupabaseServerClient: async () => ({ auth: { getClaims } }),
    }));
  }

  const { resolveAccountIdentity } = await import("@/lib/auth/account-identity");
  return resolveAccountIdentity();
}

describe("resolveAccountIdentity", () => {
  afterEach(() => {
    vi.doUnmock("@/lib/auth/auth-config");
    vi.doUnmock("@clerk/nextjs/server");
    vi.doUnmock("@/lib/auth/providers/supabase/server-client");
    vi.resetModules();
  });

  it("reports an unavailable identity in local mode", async () => {
    await expect(resolveForProvider({ authProvider: "local" })).resolves.toEqual({
      email: null,
      securityMode: "unavailable",
    });
  });

  it("returns the primary Clerk email when Clerk is active", async () => {
    await expect(
      resolveForProvider({
        authProvider: "clerk",
        currentUser: async () => ({
          primaryEmailAddress: { emailAddress: "owner@opevo.test" },
          emailAddresses: [],
        }),
      }),
    ).resolves.toEqual({
      email: "owner@opevo.test",
      securityMode: "managed",
    });
  });

  it("keeps Clerk mode and hides a failed Clerk lookup", async () => {
    await expect(
      resolveForProvider({
        authProvider: "clerk",
        currentUser: async () => Promise.reject(new Error("provider lookup details must not escape")),
      }),
    ).resolves.toEqual({
      email: null,
      securityMode: "managed",
    });
  });

  it("returns the verified Supabase email through the same shape", async () => {
    await expect(
      resolveForProvider({
        authProvider: "supabase",
        getClaims: async () => ({ data: { claims: { email: "supabase@opevo.test" } }, error: null }),
      }),
    ).resolves.toEqual({
      email: "supabase@opevo.test",
      securityMode: "managed",
    });
  });
});
