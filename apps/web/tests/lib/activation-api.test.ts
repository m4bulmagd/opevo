import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { requireServerSessionMock } = vi.hoisted(() => ({
  requireServerSessionMock: vi.fn(),
}));

vi.mock("@/lib/auth/server-session", () => ({
  requireServerSession: requireServerSessionMock,
}));

const importActivationApi = () => import("@/lib/api/activation");

describe("activation API commands", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.resetModules();
    requireServerSessionMock.mockReset().mockResolvedValue({ userId: "user_123", token: "session-token" });
    fetchMock.mockReset().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ stage: "profile_required" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("API_BASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("reads the canonical activation snapshot", async () => {
    const { getActivationSnapshot } = await importActivationApi();

    await getActivationSnapshot();

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/activation",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("saves the bounded business profile draft", async () => {
    const { saveBusinessProfile } = await importActivationApi();
    const draft = { owner_name: "Maya", confirmed_carrier: "orange" as const };

    await saveBusinessProfile(draft);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/business-profile",
      expect.objectContaining({ method: "PUT", body: JSON.stringify(draft) }),
    );
  });

  it.each([
    ["carrier lookup", "lookupCarrier", "/api/activation/lookup-carrier"],
    ["profile confirmation", "confirmProfile", "/api/activation/confirm-profile"],
    ["explicit provisioning consent", "confirmProvisioning", "/api/activation/confirm-provisioning"],
    ["provisioning retry", "retryProvisioning", "/api/activation/retry-provisioning"],
    ["verification window", "openVerificationWindow", "/api/activation/open-verification-window"],
    ["go-live", "goLive", "/api/activation/go-live"],
    ["development starter", "activateDevelopmentStarter", "/api/development/activate-starter"],
    [
      "development forwarded-call simulation",
      "simulateDevelopmentForwardedCall",
      "/api/development/simulate-forwarded-call",
    ],
  ] as const)("sends %s to its dedicated command", async (_label, commandName, path) => {
    const activationApi = await importActivationApi();

    await activationApi[commandName]();

    expect(fetchMock).toHaveBeenCalledWith(`http://localhost:8000${path}`, expect.objectContaining({ method: "POST" }));
  });
});

describe("development capabilities", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("exposes only booleans for guarded local fake providers", async () => {
    vi.resetModules();
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_MODE", "local");
    vi.stubEnv("BILLING_MODE", "fake");
    vi.stubEnv("TELEPHONY_MODE", "fake");
    vi.stubEnv("LOCAL_AUTH_TOKEN", "must-not-be-returned");

    const { getDevelopmentCapabilities } = await import("@/lib/development/capabilities");
    const capabilities = getDevelopmentCapabilities();

    expect(capabilities).toEqual({ localBilling: true, localVerification: true });
    expect(JSON.stringify(capabilities)).not.toContain("must-not-be-returned");
  });

  it.each([
    ["production", "clerk", "fake", "fake"],
    ["development", "clerk", "fake", "fake"],
    ["development", "local", "stripe", "telnyx"],
  ])("fails closed for node=%s auth=%s billing=%s telephony=%s", async (nodeEnv, auth, billing, telephony) => {
    vi.resetModules();
    vi.stubEnv("NODE_ENV", nodeEnv);
    vi.stubEnv("AUTH_MODE", auth);
    vi.stubEnv("BILLING_MODE", billing);
    vi.stubEnv("TELEPHONY_MODE", telephony);

    if (nodeEnv === "production") {
      vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_live_test");
      vi.stubEnv("CLERK_SECRET_KEY", "sk_live_test");
      vi.stubEnv("API_BASE_URL", "https://api.presvo.test");
    }

    const { getDevelopmentCapabilities } = await import("@/lib/development/capabilities");

    expect(getDevelopmentCapabilities()).toEqual({ localBilling: false, localVerification: false });
  });
});
