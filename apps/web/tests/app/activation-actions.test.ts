import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BackendApiError } from "@/lib/api/backend-client";

const {
  requireServerSessionMock,
  revalidatePathMock,
  getDevelopmentCapabilitiesMock,
  saveBusinessProfileMock,
  lookupCarrierMock,
  confirmProfileMock,
  confirmProvisioningMock,
  retryProvisioningMock,
  openVerificationWindowMock,
  goLiveMock,
  activateDevelopmentStarterMock,
  simulateDevelopmentForwardedCallMock,
} = vi.hoisted(() => ({
  requireServerSessionMock: vi.fn(),
  revalidatePathMock: vi.fn(),
  getDevelopmentCapabilitiesMock: vi.fn(),
  saveBusinessProfileMock: vi.fn(),
  lookupCarrierMock: vi.fn(),
  confirmProfileMock: vi.fn(),
  confirmProvisioningMock: vi.fn(),
  retryProvisioningMock: vi.fn(),
  openVerificationWindowMock: vi.fn(),
  goLiveMock: vi.fn(),
  activateDevelopmentStarterMock: vi.fn(),
  simulateDevelopmentForwardedCallMock: vi.fn(),
}));

vi.mock("next/cache", () => ({ revalidatePath: revalidatePathMock }));
vi.mock("@/lib/auth/server-session", () => ({
  requireServerSession: requireServerSessionMock,
  ServerSessionRequiredError: class ServerSessionRequiredError extends Error {},
}));
vi.mock("@/lib/development/capabilities", () => ({
  getDevelopmentCapabilities: getDevelopmentCapabilitiesMock,
}));
vi.mock("@/lib/api/activation", () => ({
  saveBusinessProfile: saveBusinessProfileMock,
  lookupCarrier: lookupCarrierMock,
  confirmProfile: confirmProfileMock,
  confirmProvisioning: confirmProvisioningMock,
  retryProvisioning: retryProvisioningMock,
  openVerificationWindow: openVerificationWindowMock,
  goLive: goLiveMock,
  activateDevelopmentStarter: activateDevelopmentStarterMock,
  simulateDevelopmentForwardedCall: simulateDevelopmentForwardedCallMock,
}));

import {
  activateDevelopmentStarterAction,
  confirmProfileAction,
  confirmProvisioningAction,
  goLiveAction,
  lookupCarrierAction,
  openVerificationWindowAction,
  retryProvisioningAction,
  saveBusinessProfileAction,
  simulateDevelopmentForwardedCallAction,
} from "@/app/(activation)/activate/actions";

const snapshot = { stage: "profile_required" };

describe("activation Server Actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    requireServerSessionMock.mockResolvedValue({ userId: "user_123", token: "session-token" });
    getDevelopmentCapabilitiesMock.mockReturnValue({ localBilling: true, localVerification: true });
    for (const mock of [
      saveBusinessProfileMock,
      lookupCarrierMock,
      confirmProfileMock,
      confirmProvisioningMock,
      retryProvisioningMock,
      openVerificationWindowMock,
      goLiveMock,
      activateDevelopmentStarterMock,
      simulateDevelopmentForwardedCallMock,
    ]) {
      mock.mockResolvedValue(snapshot);
    }
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("authenticates, validates, and saves only allowed profile fields", async () => {
    const draft = { owner_name: "Maya", confirmed_carrier: "orange" };

    const result = await saveBusinessProfileAction(draft);

    expect(result.status).toBe("success");
    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(saveBusinessProfileMock).toHaveBeenCalledWith(draft);
    expect(revalidatePathMock.mock.calls).toEqual([["/activate"], ["/dashboard"]]);
  });

  it("rejects malformed and extra profile input before mutation", async () => {
    const result = await saveBusinessProfileAction({
      owner_name: [],
      provider_operation_key: "must-not-cross-boundary",
    });

    expect(result).toMatchObject({ status: "error", code: "invalid_input", fields: ["owner_name"] });
    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(saveBusinessProfileMock).not.toHaveBeenCalled();
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it("rejects keys on no-input commands", async () => {
    const result = await confirmProvisioningAction({ provider_number_id: "unsafe" });

    expect(result).toMatchObject({ status: "error", code: "invalid_input" });
    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(confirmProvisioningMock).not.toHaveBeenCalled();
  });

  it.each([
    ["lookup", () => lookupCarrierAction(), lookupCarrierMock],
    ["confirm profile", () => confirmProfileAction(), confirmProfileMock],
    ["confirm provisioning", () => confirmProvisioningAction(), confirmProvisioningMock],
    ["retry provisioning", () => retryProvisioningAction(), retryProvisioningMock],
    ["open verification", () => openVerificationWindowAction(), openVerificationWindowMock],
    ["go live", () => goLiveAction(), goLiveMock],
    ["activate local starter", () => activateDevelopmentStarterAction(), activateDevelopmentStarterMock],
    ["simulate local forwarding", () => simulateDevelopmentForwardedCallAction(), simulateDevelopmentForwardedCallMock],
  ])("authenticates and revalidates exact paths after successful %s", async (_label, action, apiMock) => {
    const result = await action();

    expect(result.status).toBe("success");
    expect(requireServerSessionMock).toHaveBeenCalledOnce();
    expect(apiMock).toHaveBeenCalledOnce();
    expect(revalidatePathMock.mock.calls).toEqual([["/activate"], ["/dashboard"]]);
  });

  it.each([
    [
      "profile fields",
      confirmProfileMock,
      () => confirmProfileAction(),
      new BackendApiError({ code: "profile_incomplete", fields: ["business_name"], provider_detail: "hidden" }, 422),
      { code: "profile_incomplete", fields: ["business_name"] },
    ],
    [
      "carrier outage",
      lookupCarrierMock,
      () => lookupCarrierAction(),
      new BackendApiError({ code: "carrier_lookup_unavailable", provider_detail: "hidden" }, 503),
      { code: "carrier_lookup_unavailable" },
    ],
    [
      "go-live conflict",
      goLiveMock,
      () => goLiveAction(),
      new BackendApiError({ code: "go_live_blocked", blockers: ["forwarding_not_verified"] }, 409),
      { code: "go_live_blocked" },
    ],
  ])("maps safe structured %s errors without revalidation", async (_label, apiMock, action, error, expected) => {
    apiMock.mockRejectedValueOnce(error);

    const result = await action();

    expect(result).toMatchObject({ status: "error", ...expected });
    expect(result.message).not.toContain("hidden");
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it.each([
    "verification_window_already_claimed",
    "verification_session_not_found",
    "verification_session_not_claimed",
    "verification_completion_expired",
    "verification_routing_stale",
  ])("maps the customer-reachable simulator code %s", async (code) => {
    simulateDevelopmentForwardedCallMock.mockRejectedValueOnce(new BackendApiError({ code }, 409));

    const result = await simulateDevelopmentForwardedCallAction();

    expect(result).toMatchObject({ status: "error", code });
    expect(revalidatePathMock).not.toHaveBeenCalled();
  });

  it.each([
    ["billing", () => activateDevelopmentStarterAction(), activateDevelopmentStarterMock],
    ["verification", () => simulateDevelopmentForwardedCallAction(), simulateDevelopmentForwardedCallMock],
  ])("fails closed when local %s capability is unavailable", async (capability, action, apiMock) => {
    getDevelopmentCapabilitiesMock.mockReturnValue({ localBilling: false, localVerification: false });

    const result = await action();

    expect(result).toMatchObject({ status: "error", code: "development_unavailable" });
    expect(apiMock).not.toHaveBeenCalled();
    expect(revalidatePathMock).not.toHaveBeenCalled();
    expect(capability).toBeTruthy();
  });
});
