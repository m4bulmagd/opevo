import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StageRefresh } from "@/app/(activation)/activate/_components/stage-refresh";
import ActivationError from "@/app/(activation)/activate/error";
import ActivationLoading from "@/app/(activation)/activate/loading";
import Page from "@/app/(activation)/activate/page";
import type { ActivationSnapshot } from "@/lib/types/activation";

import { forwardingGuide } from "./activation-snapshot-fixture";

const clerkConfigState = vi.hoisted(() => ({
  authMode: "local" as "local" | "clerk",
  shouldWrapClerk: false,
}));
const clerkBoundaryState = vi.hoisted(() => ({
  loadedDuringServerLayout: false,
  serverLayoutExecuting: false,
}));
const { getAccountMock, getActivationSnapshotMock, getDevelopmentCapabilitiesMock, redirectMock, refreshMock } =
  vi.hoisted(() => ({
    getAccountMock: vi.fn(),
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

vi.mock("@clerk/nextjs", async () => {
  if (clerkBoundaryState.serverLayoutExecuting) {
    clerkBoundaryState.loadedDuringServerLayout = true;
  }

  const { Children, cloneElement } = await import("react");

  return {
    SignOutButton: ({ children }: { children: React.ReactNode }) => {
      return cloneElement(Children.only(children) as React.ReactElement);
    },
  };
});

vi.mock("next/navigation", () => ({
  redirect: redirectMock,
  useRouter: () => ({ refresh: refreshMock }),
}));

vi.mock("@/lib/api/activation", () => ({
  getActivationSnapshot: getActivationSnapshotMock,
}));

vi.mock("@/lib/api/account", () => ({
  getAccount: getAccountMock,
}));

vi.mock("@/app/(activation)/activate/actions", () => ({
  confirmProfileAction: vi.fn(),
  goLiveAction: vi.fn(),
  lookupCarrierAction: vi.fn(),
  openVerificationWindowAction: vi.fn(),
  saveBusinessProfileAction: vi.fn(),
  simulateDevelopmentForwardedCallAction: vi.fn(),
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

function activationStepCard() {
  const card = document.querySelector('[data-slot="activation-step-card"]');

  expect(card).toHaveClass("rounded-2xl", "border", "border-border", "bg-card", "shadow-card");
  expect(within(card as HTMLElement).getByRole("heading", { level: 1 })).toBeVisible();
  expect(document.querySelectorAll("h1")).toHaveLength(1);

  return within(card as HTMLElement);
}

describe("activation page", () => {
  beforeEach(() => {
    getAccountMock.mockReset().mockResolvedValue({
      status: "active",
      serving: false,
      deactivation: null,
      reactivation_allowed: false,
      blocker: "customer_not_ready",
    });
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
    expect(screen.getByRole("main")).toHaveClass("max-w-3xl", "px-4", "sm:px-6");
    expect(screen.getByText("Step 3 of 5")).toHaveClass("text-label");
    expect(document.querySelectorAll('[data-slot="activation-progress-segment"]')).toHaveLength(5);
    expect(activationStepCard().getByRole("button", { name: /Start starter plan/i })).toBeVisible();
  });

  it("uses a minimal Presvo header before activation is complete", async () => {
    const { default: ActivationLayout } = await import("@/app/(activation)/activate/layout");

    render(await ActivationLayout({ children: <div>Activation content</div> }));

    expect(screen.getByRole("banner")).toHaveClass("bg-background/90", "backdrop-blur");
    expect(screen.getByRole("link", { name: "Presvo home" })).toHaveAttribute("href", "/");
    expect(screen.getByText("Local development")).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "Account navigation" })).not.toBeInTheDocument();
  });

  it("integrates the shared profile form only for the business and receptionist branches", async () => {
    getActivationSnapshotMock.mockResolvedValueOnce(buildSnapshot());
    const businessView = render(await Page({ searchParams: Promise.resolve({ milestone: "business" }) }));

    expect(screen.getByLabelText(/Owner name/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Receptionist name/i)).not.toBeInTheDocument();
    expect(activationStepCard().getByRole("button", { name: "Continue" })).toBeVisible();
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

  it.each([
    "deactivating",
    "inactive",
  ] as const)("redirects a %s owner to Account before rendering mutable activation milestones", async (status) => {
    getAccountMock.mockResolvedValueOnce({
      status,
      serving: false,
      deactivation: status === "deactivating" ? { state: "finalizing", requested_at: "2026-07-24T10:00:00Z" } : null,
      reactivation_allowed: status === "inactive",
      blocker: status === "deactivating" ? "account_deactivating" : "account_inactive",
    });
    getActivationSnapshotMock.mockResolvedValue(buildSnapshot());

    await expect(Page({ searchParams: Promise.resolve({}) })).rejects.toThrow("NEXT_REDIRECT");

    expect(redirectMock).toHaveBeenCalledWith("/dashboard/account");
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

  it("renders forwarding for a completed legacy number without historical consent", async () => {
    getActivationSnapshotMock.mockResolvedValue(
      buildSnapshot({
        stage: "forwarding_required",
        completed_milestones: ["profile_confirmed", "number_provisioned"],
        forwarding: forwardingGuide(),
        number: {
          assigned_e164: "+33187654321",
          country_code: "FR",
          provider_ready: true,
          provisioning_status: "succeeded",
          can_retry: false,
        },
      }),
    );

    render(await Page({ searchParams: Promise.resolve({ milestone: "forwarding" }) }));

    expect(screen.getByRole("heading", { name: /Forward missed calls to Presvo/i })).toBeInTheDocument();
    expect(screen.getByText("+33 1 87 65 43 21")).toBeInTheDocument();
    expect(activationStepCard().getByRole("button", { name: /Start 10-minute test/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Choose your Presvo number/i })).not.toBeInTheDocument();
  });

  it("renders the server-owned verification window and guarded local simulator in launch", async () => {
    getDevelopmentCapabilitiesMock.mockReturnValue({ localBilling: false, localVerification: true });
    const base = buildSnapshot();
    getActivationSnapshotMock.mockResolvedValue(
      buildSnapshot({
        stage: "verification_window_open",
        completed_milestones: ["profile_confirmed", "number_provisioned"],
        activation: {
          ...base.activation,
          verification_window_started_at: "2026-07-17T10:00:00Z",
          verification_window_expires_at: "2026-07-17T10:10:00Z",
          verification_status: "open",
        },
      }),
    );

    render(await Page({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("heading", { name: /Prepare to go live/i })).toBeInTheDocument();
    expect(screen.getByRole("timer")).toHaveTextContent("10:00");
    expect(activationStepCard().getByRole("button", { name: /Simulate forwarded call/i })).toBeInTheDocument();
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
  it("keeps local context and skip navigation in the focused route layout", async () => {
    clerkConfigState.authMode = "local";
    clerkConfigState.shouldWrapClerk = false;
    const { default: ActivationLayout } = await import("@/app/(activation)/activate/layout");

    render(await ActivationLayout({ children: <div id="activation-content">Activation form</div> }));

    expect(screen.getByRole("link", { name: "Presvo home" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("navigation", { name: "Account navigation" })).not.toBeInTheDocument();
    expect(screen.getByText(/Local development/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Skip to activation/i })).toHaveAttribute("href", "#activation-content");
  });

  it("renders Clerk sign-out through a client-owned single-child boundary", async () => {
    clerkConfigState.authMode = "clerk";
    clerkConfigState.shouldWrapClerk = true;
    const { default: ActivationLayout } = await import("@/app/(activation)/activate/layout");

    clerkBoundaryState.loadedDuringServerLayout = false;
    clerkBoundaryState.serverLayoutExecuting = true;
    let layout: React.ReactNode;
    try {
      layout = await ActivationLayout({ children: <div id="activation-content">Activation form</div> });
    } finally {
      clerkBoundaryState.serverLayoutExecuting = false;
    }
    expect(clerkBoundaryState.loadedDuringServerLayout).toBe(false);
    render(layout);

    expect(screen.getByRole("button", { name: /Sign out/i })).toBeInTheDocument();
  });

  it("renders an accessible loading status", () => {
    render(<ActivationLoading />);

    expect(screen.getByRole("status", { name: /Loading activation/i })).toHaveClass("max-w-3xl", "px-4");
    expect(document.querySelector('[data-slot="activation-loading-card"]')).toHaveClass(
      "rounded-2xl",
      "border",
      "bg-card",
      "shadow-card",
    );
  });

  it("offers an accessible retry when the route fails", () => {
    const reset = vi.fn();
    render(<ActivationError error={new Error("private provider message")} reset={reset} />);

    expect(screen.getByRole("main")).toHaveClass("max-w-3xl", "px-4");
    expect(screen.queryByText(/private provider message/i)).not.toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /Try again/i });
    expect(retry).toHaveClass("min-h-11");
    fireEvent.click(retry);
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
