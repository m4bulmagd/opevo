import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const requireServerSessionMock = vi.fn();

vi.mock("@/lib/auth/server-session", () => ({
  requireServerSession: requireServerSessionMock,
}));

describe("backendFetch base URL selection", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.resetModules();
    requireServerSessionMock.mockReset();
    requireServerSessionMock.mockResolvedValue({ userId: "user_123", token: "session-token" });
    fetchMock.mockReset();
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ ok: true }),
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("uses the public backend URL when the production server-only value is blank", async () => {
    const publicBaseUrl = "https://public-api.example.com";
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("API_BASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", publicBaseUrl);

    const { backendFetch } = await import("@/lib/api/backend-client");
    await backendFetch("/api/example");

    expect(fetchMock).toHaveBeenCalledWith(
      `${publicBaseUrl}/api/example`,
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("uses localhost when both backend URLs are absent in development", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("API_BASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");

    const { backendFetch } = await import("@/lib/api/backend-client");
    await backendFetch("/api/example");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/example",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("authenticates every backend request at the server boundary", async () => {
    vi.stubEnv("NODE_ENV", "development");

    const { backendFetch } = await import("@/lib/api/backend-client");
    await backendFetch("/api/first");
    await backendFetch("/api/second");

    expect(requireServerSessionMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8000/api/first",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    const requestHeaders = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(requestHeaders.get("Authorization")).toBe("Bearer session-token");
  });

  it("preserves safe structured backend details without exposing arbitrary payloads in the message", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: "Conflict",
      json: vi.fn().mockResolvedValue({
        detail: { code: "activation_not_ready", blockers: ["forwarding_unverified"], provider_secret: "hidden" },
      }),
    });

    const { backendFetch, BackendApiError } = await import("@/lib/api/backend-client");

    try {
      await backendFetch("/api/activate", { method: "POST" });
      expect.fail("Expected backendFetch to reject");
    } catch (error) {
      expect(error).toBeInstanceOf(BackendApiError);
      expect(error).toMatchObject({
        status: 409,
        detail: {
          code: "activation_not_ready",
          blockers: ["forwarding_unverified"],
          provider_secret: "hidden",
        },
      });
      expect((error as Error).message).toBe("activation_not_ready");
      expect((error as Error).message).not.toContain("hidden");
    }
  });

  it("uses the HTTP status when a structured detail has no safe code", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 503,
      statusText: "Service Unavailable",
      json: vi.fn().mockResolvedValue({ detail: { provider_secret: "must-not-leak" } }),
    });

    const { backendFetch } = await import("@/lib/api/backend-client");

    await expect(backendFetch("/api/example")).rejects.toMatchObject({
      message: "Backend request failed (503)",
      detail: { provider_secret: "must-not-leak" },
    });
  });
});
