import { afterEach, describe, expect, it, vi } from "vitest";

const clerkAuthMock = vi.fn();

vi.mock("@clerk/nextjs/server", () => ({
  auth: clerkAuthMock,
}));

async function importServerSession() {
  vi.resetModules();
  return import("@/lib/auth/server-session");
}

describe("server session selection", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    clerkAuthMock.mockReset();
    vi.resetModules();
  });

  it("returns the fixed local session only on the server", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    vi.stubEnv("LOCAL_AUTH_TOKEN", "presvo-local-development-token");

    const { getServerSessionState } = await importServerSession();
    const session = await getServerSessionState();

    expect(session.isAuthenticated).toBe(true);
    expect(session.userId).toBe("local_presvo_user");
    expect(await session.getToken()).toBe("presvo-local-development-token");
    expect(clerkAuthMock).not.toHaveBeenCalled();
  });

  it("fails closed when local mode has no server token", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    vi.stubEnv("LOCAL_AUTH_TOKEN", "  ");

    const { getServerSessionState } = await importServerSession();

    await expect(getServerSessionState()).rejects.toThrow("LOCAL_AUTH_TOKEN");
  });

  it("fails closed when the local server token is padded", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    vi.stubEnv("LOCAL_AUTH_TOKEN", " padded-local-token ");

    const { getServerSessionState } = await importServerSession();

    await expect(getServerSessionState()).rejects.toThrow("LOCAL_AUTH_TOKEN");
  });

  it("cannot construct a server session module with incomplete Clerk configuration", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "clerk");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("API_BASE_URL", "http://api:8000");

    await expect(importServerSession()).rejects.toThrow("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY");
  });

  it("uses Clerk when Clerk mode is configured", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "clerk");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_session");
    vi.stubEnv("CLERK_SECRET_KEY", "sk_test_session");
    const getToken = vi.fn().mockResolvedValue("clerk-session-token");
    clerkAuthMock.mockResolvedValue({ userId: "user_123", getToken });

    const { getServerSessionState } = await importServerSession();
    const session = await getServerSessionState();

    expect(session.isAuthenticated).toBe(true);
    expect(session.userId).toBe("user_123");
    expect(await session.getToken()).toBe("clerk-session-token");
  });

  it("requires both an authenticated identity and token for server requests", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "clerk");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_session");
    vi.stubEnv("CLERK_SECRET_KEY", "sk_test_session");
    clerkAuthMock.mockResolvedValue({ userId: "user_123", getToken: vi.fn().mockResolvedValue(null) });

    const { requireServerSession, ServerSessionRequiredError } = await importServerSession();

    await expect(requireServerSession()).rejects.toBeInstanceOf(ServerSessionRequiredError);
  });
});
