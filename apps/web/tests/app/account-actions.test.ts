import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BackendApiError } from "@/lib/api/backend-client";

import { activationSnapshot } from "./activation-snapshot-fixture";

const {
  requireServerSessionMock,
  deactivateAccountApiMock,
  activateDevelopmentStarterMock,
  createCheckoutSessionMock,
  getActivationSnapshotMock,
  getDevelopmentCapabilitiesMock,
  revalidatePathMock,
  redirectMock,
  saveBusinessProfileMock,
} = vi.hoisted(() => ({
  requireServerSessionMock: vi.fn(),
  deactivateAccountApiMock: vi.fn(),
  activateDevelopmentStarterMock: vi.fn(),
  createCheckoutSessionMock: vi.fn(),
  getActivationSnapshotMock: vi.fn(),
  getDevelopmentCapabilitiesMock: vi.fn(),
  revalidatePathMock: vi.fn(),
  redirectMock: vi.fn(),
  saveBusinessProfileMock: vi.fn(),
}));

vi.mock("@/lib/auth/server-session", () => ({
  requireServerSession: requireServerSessionMock,
  ServerSessionRequiredError: class ServerSessionRequiredError extends Error {},
}));
vi.mock("@/lib/api/account", () => ({
  deactivateAccount: deactivateAccountApiMock,
}));
vi.mock("@/lib/api/activation", () => ({
  activateDevelopmentStarter: activateDevelopmentStarterMock,
  getActivationSnapshot: getActivationSnapshotMock,
  saveBusinessProfile: saveBusinessProfileMock,
}));
vi.mock("@/lib/api/billing", () => ({
  createCheckoutSession: createCheckoutSessionMock,
}));
vi.mock("@/lib/development/capabilities", () => ({
  getDevelopmentCapabilities: getDevelopmentCapabilitiesMock,
}));
vi.mock("next/cache", () => ({
  revalidatePath: revalidatePathMock,
}));
vi.mock("next/navigation", () => ({
  redirect: redirectMock,
}));

import { deactivateAccount, reactivateAccount, saveAccountProfileAction } from "@/app/(app)/dashboard/account/actions";

const validAccountProfileInput = {
  owner_name: "Maya Martin",
  business_name: "Atelier Martin",
  existing_phone_e164: "06 12 34 56 78",
  timezone: "Europe/Paris",
};

describe("account server actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    requireServerSessionMock.mockResolvedValue({ userId: "owner-1", token: "session-token" });
    getDevelopmentCapabilitiesMock.mockReturnValue({ localBilling: false, localVerification: false });
    deactivateAccountApiMock.mockResolvedValue({
      status: "deactivating",
      serving: false,
      deactivation: { state: "requested", requested_at: "2026-07-24T10:00:00Z" },
      reactivation_allowed: false,
      blocker: "account_deactivating",
    });
    getActivationSnapshotMock.mockResolvedValue(
      activationSnapshot({
        profile: {
          ...activationSnapshot().profile,
          business_type: "Florist",
          public_description: "A neighbourhood florist.",
          business_hours: {
            monday: { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
            tuesday: { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
            wednesday: { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
            thursday: { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
            friday: { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
            saturday: { closed: true, intervals: [] },
            sunday: { closed: true, intervals: [] },
          },
          receptionist_name: "Lea",
          faqs: [{ question: "Parking?", answer: "Street parking is available." }],
          special_instructions: "Keep replies concise.",
          escalation_notes: "Escalate urgent requests.",
        },
      }),
    );
    saveBusinessProfileMock.mockResolvedValue({});
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("rejects anything except the exact DEACTIVATE confirmation before mutation", async () => {
    const result = await deactivateAccount("deactivate");

    expect(result).toEqual({
      status: "error",
      code: "invalid_confirmation",
      message: "Type DEACTIVATE exactly to confirm.",
    });
    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(deactivateAccountApiMock).not.toHaveBeenCalled();
  });

  it("authenticates, delegates deactivation to the API, revalidates lifecycle surfaces, and redirects", async () => {
    await deactivateAccount("DEACTIVATE");

    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(deactivateAccountApiMock).toHaveBeenCalledWith("DEACTIVATE");
    expect(revalidatePathMock.mock.calls).toEqual([
      ["/dashboard"],
      ["/dashboard/account"],
      ["/dashboard/agent"],
      ["/dashboard/billing"],
      ["/activate"],
    ]);
    expect(redirectMock).toHaveBeenCalledWith("/dashboard/account");
  });

  it("maps API failures to bounded customer-safe errors", async () => {
    deactivateAccountApiMock.mockRejectedValueOnce(
      new BackendApiError(
        {
          code: "account_inactive",
          provider_number_id: "pn_must_not_leak",
        },
        409,
      ),
    );

    const result = await deactivateAccount("DEACTIVATE");

    expect(result).toMatchObject({
      status: "error",
      code: "account_inactive",
      message: "This account is already inactive.",
    });
    expect(JSON.stringify(result)).not.toContain("pn_must_not_leak");
    expect(revalidatePathMock).not.toHaveBeenCalled();
    expect(redirectMock).not.toHaveBeenCalled();
  });

  it("reactivates through the guarded local fake billing boundary and returns to activation", async () => {
    getDevelopmentCapabilitiesMock.mockReturnValue({ localBilling: true, localVerification: false });
    activateDevelopmentStarterMock.mockResolvedValue({ stage: "payment_required" });

    const result = await reactivateAccount();

    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(activateDevelopmentStarterMock).toHaveBeenCalledOnce();
    expect(createCheckoutSessionMock).not.toHaveBeenCalled();
    expect(result).toEqual({
      status: "success",
      message: "Your starter plan is ready. Continue activation.",
      url: "/activate",
    });
  });

  it("reactivates through hosted checkout outside local fake billing", async () => {
    createCheckoutSessionMock.mockResolvedValue({ url: "https://checkout.stripe.test/reactivate" });

    const result = await reactivateAccount();

    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(createCheckoutSessionMock).toHaveBeenCalledWith("starter");
    expect(activateDevelopmentStarterMock).not.toHaveBeenCalled();
    expect(result).toEqual({
      status: "success",
      message: "Checkout is ready.",
      url: "https://checkout.stripe.test/reactivate",
    });
  });

  it("limits the Stripe test fixture to the test environment", async () => {
    vi.stubEnv("NODE_ENV", "production");
    createCheckoutSessionMock.mockResolvedValue({ url: "https://checkout.stripe.test/reactivate" });

    const result = await reactivateAccount();

    expect(result).toEqual({
      status: "error",
      code: "request_failed",
      message: "We couldn't open checkout. Refresh and try again.",
    });
  });

  it("accepts Stripe Checkout as the hosted reactivation boundary", async () => {
    createCheckoutSessionMock.mockResolvedValue({
      url: "https://checkout.stripe.com/c/pay/cs_live_123",
    });

    const result = await reactivateAccount();

    expect(result).toEqual({
      status: "success",
      message: "Checkout is ready.",
      url: "https://checkout.stripe.com/c/pay/cs_live_123",
    });
  });

  it.each([
    "http://checkout.stripe.com/c/pay/cs_secret",
    "https://provider.internal/session-secret",
    "https://checkout.stripe.com.evil.example/session-secret",
    "https://checkout.stripe.com:444/session-secret",
    "https://user:password@checkout.stripe.com/session-secret",
  ])("does not expose an untrusted hosted redirect: %s", async (unsafeUrl) => {
    createCheckoutSessionMock.mockResolvedValueOnce({ url: unsafeUrl });
    const result = await reactivateAccount();

    expect(result).toEqual({
      status: "error",
      code: "request_failed",
      message: "We couldn't open checkout. Refresh and try again.",
    });
    expect(JSON.stringify(result)).not.toContain("session-secret");
  });

  it("does not expose backend provider details", async () => {
    createCheckoutSessionMock.mockRejectedValueOnce(
      new BackendApiError({ code: "reactivation_not_ready", subscription_id: "sub_secret" }, 409),
    );
    const conflictResult = await reactivateAccount();

    expect(conflictResult).toMatchObject({
      status: "error",
      code: "reactivation_not_ready",
      message: "Reactivation is not ready yet. Refresh and try again.",
    });
    expect(JSON.stringify(conflictResult)).not.toContain("sub_secret");
  });

  it("saves exactly the editable profile values while preserving the latest complete draft", async () => {
    const result = await saveAccountProfileAction({
      owner_name: "  Maya Martin  ",
      business_name: "Atelier Martin",
      existing_phone_e164: "06 12 34 56 78",
      timezone: "Europe/Paris",
    });

    expect(saveBusinessProfileMock).toHaveBeenCalledWith({
      owner_name: "Maya Martin",
      business_name: "Atelier Martin",
      existing_phone_e164: "+33612345678",
      timezone: "Europe/Paris",
      business_type: "Florist",
      public_description: "A neighbourhood florist.",
      business_hours: expect.any(Object),
      confirmed_carrier: "orange",
      receptionist_name: "Lea",
      faqs: [{ question: "Parking?", answer: "Street parking is available." }],
      special_instructions: "Keep replies concise.",
      escalation_notes: "Escalate urgent requests.",
    });
    expect(result).toMatchObject({
      status: "success",
      message: "Profile saved.",
      profile: {
        owner_name: "Maya Martin",
        business_name: "Atelier Martin",
        existing_phone_e164: "+33612345678",
        timezone: "Europe/Paris",
      },
    });
    expect(revalidatePathMock.mock.calls).toEqual([
      ["/dashboard"],
      ["/dashboard/account"],
      ["/dashboard/agent"],
      ["/dashboard/billing"],
      ["/activate"],
    ]);
  });

  it.each([
    {},
    {
      owner_name: "Maya",
      business_name: "Atelier Maya",
      existing_phone_e164: "not a phone number",
      timezone: "Europe/Paris",
    },
  ])("rejects malformed account profile input before reading or saving a profile", async (input) => {
    const result = await saveAccountProfileAction(input);

    expect(result).toMatchObject({ status: "error", code: "invalid_input" });
    expect(getActivationSnapshotMock).not.toHaveBeenCalled();
    expect(saveBusinessProfileMock).not.toHaveBeenCalled();
  });

  it.each([
    { ...validAccountProfileInput, unexpected_field: "must be rejected" },
    { ...validAccountProfileInput, owner_name: "   " },
    { ...validAccountProfileInput, business_name: "\t" },
    { ...validAccountProfileInput, existing_phone_e164: "  " },
    { ...validAccountProfileInput, timezone: "\n" },
  ])("rejects extra or whitespace-only profile fields before reading or saving a profile", async (input) => {
    const result = await saveAccountProfileAction(input);

    expect(result).toMatchObject({ status: "error", code: "invalid_input" });
    expect(getActivationSnapshotMock).not.toHaveBeenCalled();
    expect(saveBusinessProfileMock).not.toHaveBeenCalled();
  });

  it("rejects owner and business names beyond the current server constraint", async () => {
    getActivationSnapshotMock.mockResolvedValueOnce(
      activationSnapshot({
        profile_constraints: { ...activationSnapshot().profile_constraints, name_max_length: 4 },
      }),
    );

    const result = await saveAccountProfileAction({
      owner_name: "Maya Martin",
      business_name: "Atelier Martin",
      existing_phone_e164: "06 12 34 56 78",
      timezone: "Europe/Paris",
    });

    expect(result).toMatchObject({ status: "error", code: "invalid_input" });
    expect(saveBusinessProfileMock).not.toHaveBeenCalled();
  });

  it("returns a bounded error when the latest profile snapshot is unavailable", async () => {
    getActivationSnapshotMock.mockRejectedValueOnce(new Error("upstream snapshot details must not escape"));

    const result = await saveAccountProfileAction({
      owner_name: "Maya Martin",
      business_name: "Atelier Martin",
      existing_phone_e164: "06 12 34 56 78",
      timezone: "Europe/Paris",
    });

    expect(result).toMatchObject({
      status: "error",
      code: expect.stringMatching(/profile_unavailable|request_failed/),
    });
    expect(JSON.stringify(result)).not.toContain("upstream snapshot details must not escape");
    expect(saveBusinessProfileMock).not.toHaveBeenCalled();
  });

  it("does not expose provider details when a profile save fails", async () => {
    saveBusinessProfileMock.mockRejectedValueOnce(
      new BackendApiError({ provider_profile_id: "profile_secret", message: "provider details" }, 503),
    );

    const result = await saveAccountProfileAction({
      owner_name: "Maya Martin",
      business_name: "Atelier Martin",
      existing_phone_e164: "06 12 34 56 78",
      timezone: "Europe/Paris",
    });

    expect(result).toMatchObject({ status: "error", code: "request_failed" });
    expect(JSON.stringify(result)).not.toContain("profile_secret");
    expect(JSON.stringify(result)).not.toContain("provider details");
  });
});
