import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getServerSessionStateMock = vi.fn();

vi.mock("@/lib/auth/server-session", () => ({
  getServerSessionState: getServerSessionStateMock,
}));

describe("backendFetch base URL selection", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.resetModules();
    getServerSessionStateMock.mockReset();
    getServerSessionStateMock.mockResolvedValue({
      isAuthenticated: true,
      getToken: vi.fn().mockResolvedValue("session-token"),
    });
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
});
