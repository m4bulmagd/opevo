import { afterEach, describe, expect, it, vi } from "vitest";

async function resolveForMode({
  shouldWrapClerk,
  currentUser,
}: {
  shouldWrapClerk: boolean;
  currentUser?: () => Promise<unknown>;
}) {
  vi.resetModules();
  vi.doMock("@/lib/auth/clerk-config", () => ({ shouldWrapClerk }));
  if (currentUser) {
    vi.doMock("@clerk/nextjs/server", () => ({ currentUser }));
  }

  const { resolveAccountIdentity } = await import("@/lib/auth/account-identity");
  return resolveAccountIdentity();
}

describe("resolveAccountIdentity", () => {
  afterEach(() => {
    vi.doUnmock("@/lib/auth/clerk-config");
    vi.doUnmock("@clerk/nextjs/server");
    vi.resetModules();
  });

  it("reports an unavailable identity in local mode", async () => {
    await expect(resolveForMode({ shouldWrapClerk: false })).resolves.toEqual({
      email: null,
      securityMode: "unavailable",
    });
  });

  it("returns the primary Clerk email when Clerk is active", async () => {
    await expect(
      resolveForMode({
        shouldWrapClerk: true,
        currentUser: async () => ({
          primaryEmailAddress: { emailAddress: "owner@presvo.test" },
          emailAddresses: [],
        }),
      }),
    ).resolves.toEqual({
      email: "owner@presvo.test",
      securityMode: "clerk",
    });
  });

  it("keeps Clerk mode and hides a failed Clerk lookup", async () => {
    await expect(
      resolveForMode({
        shouldWrapClerk: true,
        currentUser: async () => Promise.reject(new Error("provider lookup details must not escape")),
      }),
    ).resolves.toEqual({
      email: null,
      securityMode: "clerk",
    });
  });
});
