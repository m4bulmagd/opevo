import { afterEach, describe, expect, it, vi } from "vitest";

const clerkAuthMock = vi.fn();
const supabaseGetClaimsMock = vi.fn();
const supabaseGetSessionMock = vi.fn();

vi.mock("@clerk/nextjs/server", () => ({
  auth: clerkAuthMock,
}));

vi.mock("@/lib/auth/providers/supabase/server-client", () => ({
  createSupabaseServerClient: vi.fn(async () => ({
    auth: {
      getClaims: supabaseGetClaimsMock,
      getSession: supabaseGetSessionMock,
    },
  })),
}));

async function importServerSession() {
  vi.resetModules();
  return import("@/lib/auth/server-session");
}

describe("server session selection", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    clerkAuthMock.mockReset();
    supabaseGetClaimsMock.mockReset();
    supabaseGetSessionMock.mockReset();
    vi.resetModules();
  });

  it("returns the fixed local session only on the server", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_PROVIDER", "local");
    vi.stubEnv("LOCAL_AUTH_TOKEN", "opevo-local-development-token");

    const { getServerSessionState } = await importServerSession();
    const session = await getServerSessionState();

    expect(session.isAuthenticated).toBe(true);
    expect(await session.getToken()).toBe("opevo-local-development-token");
    expect(clerkAuthMock).not.toHaveBeenCalled();
  });

  it("fails closed when local mode has no server token", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_PROVIDER", "local");
    vi.stubEnv("LOCAL_AUTH_TOKEN", "  ");

    const { getServerSessionState } = await importServerSession();

    await expect(getServerSessionState()).rejects.toThrow("LOCAL_AUTH_TOKEN");
  });

  it("fails closed when the local server token is padded", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_PROVIDER", "local");
    vi.stubEnv("LOCAL_AUTH_TOKEN", " padded-local-token ");

    const { getServerSessionState } = await importServerSession();

    await expect(getServerSessionState()).rejects.toThrow("LOCAL_AUTH_TOKEN");
  });

  it("cannot construct a server session module with incomplete Clerk configuration", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_PROVIDER", "clerk");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("API_BASE_URL", "http://api:8000");

    await expect(importServerSession()).rejects.toThrow("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY");
  });

  it("uses Clerk when Clerk mode is configured", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_PROVIDER", "clerk");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_session");
    vi.stubEnv("CLERK_SECRET_KEY", "sk_test_session");
    const getToken = vi.fn().mockResolvedValue("clerk-session-token");
    clerkAuthMock.mockResolvedValue({ userId: "user_123", getToken });

    const { getServerSessionState } = await importServerSession();
    const session = await getServerSessionState();

    expect(session.isAuthenticated).toBe(true);
    expect(await session.getToken()).toBe("clerk-session-token");
  });

  it("uses a verified Supabase cookie session and returns only its access token", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_PROVIDER", "supabase");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test");
    supabaseGetClaimsMock.mockResolvedValue({ data: { claims: { sub: "supabase-user" } }, error: null });
    supabaseGetSessionMock.mockResolvedValue({
      data: { session: { access_token: "supabase-session-token" } },
      error: null,
    });

    const { getServerSessionState } = await importServerSession();
    const session = await getServerSessionState();

    expect(session.isAuthenticated).toBe(true);
    expect(await session.getToken()).toBe("supabase-session-token");
    expect(session).not.toHaveProperty("userId");
    expect(clerkAuthMock).not.toHaveBeenCalled();
  });

  it("requires both an authenticated identity and token for server requests", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_PROVIDER", "clerk");
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_session");
    vi.stubEnv("CLERK_SECRET_KEY", "sk_test_session");
    clerkAuthMock.mockResolvedValue({ userId: "user_123", getToken: vi.fn().mockResolvedValue(null) });

    const { requireServerSession, ServerSessionRequiredError } = await importServerSession();

    await expect(requireServerSession()).rejects.toBeInstanceOf(ServerSessionRequiredError);
  });
});
