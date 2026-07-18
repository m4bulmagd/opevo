import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StageRefresh } from "@/app/(activation)/activate/_components/stage-refresh";
import ActivationError from "@/app/(activation)/activate/error";
import ActivationLoading from "@/app/(activation)/activate/loading";
import Page from "@/app/(activation)/activate/page";
import type { ActivationSnapshot } from "@/lib/types/activation";

const clerkConfigState = vi.hoisted(() => ({
  authMode: "local" as "local" | "clerk",
  shouldWrapClerk: false,
}));
const { getActivationSnapshotMock, getDevelopmentCapabilitiesMock, redirectMock, refreshMock } = vi.hoisted(() => ({
  getActivationSnapshotMock: vi.fn(),
  getDevelopmentCapabilitiesMock: vi.fn(),
  redirectMock: vi.fn(() => {
    throw new Error("NEXT_REDIRECT");
  }),
  refreshMock: vi.fn(),
}));

vi.mock("@/lib/auth/clerk-config", () => ({
  get authMode() {
    return clerkConfigState.authMode;
  },
  get shouldWrapClerk() {
    return clerkConfigState.shouldWrapClerk;
  },
}));

vi.mock("@clerk/nextjs", () => ({
  SignOutButton: ({ children }: { children: React.ReactNode }) => <div data-testid="sign-out-control">{children}</div>,
}));

vi.mock("next/navigation", () => ({
  redirect: redirectMock,
  useRouter: () => ({ refresh: refreshMock }),
}));

vi.mock("@/lib/api/activation", () => ({
  getActivationSnapshot: getActivationSnapshotMock,
}));

vi.mock("@/app/(activation)/activate/actions", () => ({
  confirmProfileAction: vi.fn(),
  lookupCarrierAction: vi.fn(),
  saveBusinessProfileAction: vi.fn(),
}));

vi.mock("@/lib/development/capabilities", () => ({
  getDevelopmentCapabilities: getDevelopmentCapabilitiesMock,
}));

function buildSnapshot(overrides: Partial<ActivationSnapshot> = {}): ActivationSnapshot {
  return {
    workflow_version: 1,
    stage: "profile_required",
    completed_milestones: [],
    next_action: "complete_profile",
    blockers: ["profile_not_confirmed"],
    warnings: [],
    profile: {
      owner_name: null,
      business_name: null,
      business_type: null,
      public_description: null,
      timezone: null,
      business_hours: null,
      existing_phone_e164: null,
      confirmed_carrier: null,
      receptionist_name: null,
      faqs: [],
      special_instructions: null,
      escalation_notes: null,
      detected_carrier: null,
      detected_number_type: null,
      carrier_lookup_status: null,
      carrier_looked_up_at: null,
      content_revision: 0,
      routing_revision: 0,
    },
    profile_constraints: {
      name_max_length: 100,
      business_type_max_length: 100,
      public_description_max_length: 1_000,
      faq_max_items: 20,
      faq_question_max_length: 200,
      faq_answer_max_length: 1_000,
      special_instructions_max_length: 2_000,
      escalation_notes_max_length: 2_000,
      max_intervals_per_day: 2,
      phone_country: "FR",
    },
    activation: {
      profile_confirmed_at: null,
      provisioning_consented_at: null,
      verification_window_started_at: null,
      verification_window_expires_at: null,
      verification_status: "not_started",
      forwarding_verified_at: null,
      go_live_approved_at: null,
      activated_at: null,
      last_failure_code: null,
    },
    billing: {
      eligible: false,
      plan_tier: null,
      subscription_status: null,
      allocated_minutes: 0,
      minutes_remaining: 0,
      current_period_start: null,
      current_period_end: null,
    },
    number: {
      assigned_e164: null,
      country_code: null,
      provider_ready: false,
      provisioning_status: null,
      can_retry: false,
    },
    forwarding: null,
    runtime_readiness: {
      stage: "subscription_required",
      can_provision_number: false,
      can_activate: false,
      should_enable_phone: false,
      can_route: false,
      blockers: ["profile_not_confirmed"],
      warnings: [],
      policy_version: "runtime-v2",
    },
    evaluated_at: "2026-07-17T10:00:00Z",
    ...overrides,
  };
}

describe("activation page", () => {
  beforeEach(() => {
    getActivationSnapshotMock.mockReset();
    getDevelopmentCapabilitiesMock.mockReset().mockReturnValue({
      localBilling: false,
      localVerification: false,
    });
    redirectMock.mockClear();
    refreshMock.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the canonical selected milestone after awaiting search parameters", async () => {
    getActivationSnapshotMock.mockResolvedValue(buildSnapshot({ stage: "payment_required" }));

    render(await Page({ searchParams: Promise.resolve({ milestone: "launch" }) }));

    expect(getActivationSnapshotMock).toHaveBeenCalledTimes(1);
    expect(getDevelopmentCapabilitiesMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: /Choose your Presvo number/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start starter plan/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Prepare to go live/i })).not.toBeInTheDocument();
  });

  it("integrates the shared profile form only for the business and receptionist branches", async () => {
    getActivationSnapshotMock.mockResolvedValueOnce(buildSnapshot());
    const businessView = render(await Page({ searchParams: Promise.resolve({ milestone: "business" }) }));

    expect(screen.getByLabelText(/Owner name/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Receptionist name/i)).not.toBeInTheDocument();
    businessView.unmount();

    const base = buildSnapshot();
    getActivationSnapshotMock.mockResolvedValueOnce(
      buildSnapshot({
        profile: {
          ...base.profile,
          owner_name: "Maya",
          business_name: "Atelier Maya",
          business_type: "Florist",
          timezone: "Europe/Paris",
          business_hours: {
            monday: { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
            tuesday: { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
            wednesday: { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
            thursday: { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
            friday: { closed: false, intervals: [{ start: "09:00", end: "17:00" }] },
            saturday: { closed: true, intervals: [] },
            sunday: { closed: true, intervals: [] },
          },
          existing_phone_e164: "+33612345678",
          confirmed_carrier: "orange",
        },
      }),
    );
    render(await Page({ searchParams: Promise.resolve({ milestone: "receptionist" }) }));

    expect(screen.getByLabelText(/Receptionist name/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Owner name/i)).not.toBeInTheDocument();
  });

  it("redirects an active customer to the dashboard", async () => {
    getActivationSnapshotMock.mockResolvedValue(buildSnapshot({ stage: "active" }));

    await expect(Page({ searchParams: Promise.resolve({}) })).rejects.toThrow("NEXT_REDIRECT");
    expect(redirectMock).toHaveBeenCalledWith("/dashboard");
  });

  it("redirects a previously activated runtime-paused customer to the dashboard", async () => {
    getActivationSnapshotMock.mockResolvedValue(
      buildSnapshot({
        stage: "runtime_paused",
        activation: {
          ...buildSnapshot().activation,
          go_live_approved_at: "2026-07-17T11:00:00Z",
          activated_at: "2026-07-17T11:01:00Z",
        },
      }),
    );

    await expect(Page({ searchParams: Promise.resolve({}) })).rejects.toThrow("NEXT_REDIRECT");
    expect(redirectMock).toHaveBeenCalledWith("/dashboard");
  });

  it("keeps an unproven runtime-paused state in the launch milestone", async () => {
    getActivationSnapshotMock.mockResolvedValue(buildSnapshot({ stage: "runtime_paused" }));

    render(await Page({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("heading", { name: /Prepare to go live/i })).toBeInTheDocument();
    expect(redirectMock).not.toHaveBeenCalled();
  });
});

describe("authoritative stage refresh", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    refreshMock.mockClear();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it.each(["provisioning", "activating"] as const)("refreshes while %s is visible", (stage) => {
    render(<StageRefresh stage={stage} />);

    vi.advanceTimersByTime(3_000);

    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it("does not schedule refreshes for a stable stage", () => {
    render(<StageRefresh stage="forwarding_required" />);

    vi.advanceTimersByTime(9_000);

    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("pauses while hidden, resumes on visibility, and cleans up", () => {
    const view = render(<StageRefresh stage="provisioning" />);

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    fireEvent(document, new Event("visibilitychange"));
    vi.advanceTimersByTime(6_000);
    expect(refreshMock).not.toHaveBeenCalled();

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    fireEvent(document, new Event("visibilitychange"));
    vi.advanceTimersByTime(3_000);
    expect(refreshMock).toHaveBeenCalledTimes(1);

    view.unmount();
    vi.advanceTimersByTime(6_000);
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });
});

describe("activation route feedback", () => {
  it("keeps account destinations and local context in the focused route layout", async () => {
    clerkConfigState.authMode = "local";
    clerkConfigState.shouldWrapClerk = false;
    const { default: ActivationLayout } = await import("@/app/(activation)/activate/layout");

    render(await ActivationLayout({ children: <div id="activation-content">Activation form</div> }));

    expect(screen.getByRole("link", { name: /^Presvo$/i })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /Account/i })).toHaveAttribute("href", "/dashboard");
    expect(screen.getByRole("link", { name: /Billing/i })).toHaveAttribute("href", "/dashboard/billing");
    expect(screen.getByRole("link", { name: /Calls/i })).toHaveAttribute("href", "/dashboard/calls");
    expect(screen.getByText(/Local development/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Skip to activation/i })).toHaveAttribute("href", "#activation-content");
  });

  it("renders a visible sign-out action when Clerk supplies it", async () => {
    clerkConfigState.authMode = "clerk";
    clerkConfigState.shouldWrapClerk = true;
    const { default: ActivationLayout } = await import("@/app/(activation)/activate/layout");

    render(await ActivationLayout({ children: <div id="activation-content">Activation form</div> }));

    expect(screen.getByRole("button", { name: /Sign out/i })).toBeInTheDocument();
    expect(screen.getByTestId("sign-out-control")).toBeInTheDocument();
  });

  it("renders an accessible loading status", () => {
    render(<ActivationLoading />);

    expect(screen.getByRole("status", { name: /Loading activation/i })).toBeInTheDocument();
  });

  it("offers an accessible retry when the route fails", () => {
    const reset = vi.fn();
    render(<ActivationError error={new Error("private provider message")} reset={reset} />);

    expect(screen.queryByText(/private provider message/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Try again/i }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
