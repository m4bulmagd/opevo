import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NumberMilestone } from "@/app/(activation)/activate/_components/number/number-milestone";
import { PaymentAction } from "@/app/(activation)/activate/_components/number/payment-action";
import { ProvisioningConsent } from "@/app/(activation)/activate/_components/number/provisioning-consent";
import type { ActivationSnapshot } from "@/lib/types/activation";

const { activateStarterMock, checkoutMock, confirmProvisioningMock, retryProvisioningMock, refreshMock } = vi.hoisted(
  () => ({
    activateStarterMock: vi.fn(),
    checkoutMock: vi.fn(),
    confirmProvisioningMock: vi.fn(),
    retryProvisioningMock: vi.fn(),
    refreshMock: vi.fn(),
  }),
);

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: refreshMock }),
}));

vi.mock("@/app/(activation)/activate/actions", () => ({
  activateDevelopmentStarterAction: activateStarterMock,
  createActivationCheckoutAction: checkoutMock,
  confirmProvisioningAction: confirmProvisioningMock,
  retryProvisioningAction: retryProvisioningMock,
}));

function snapshot(overrides: Partial<ActivationSnapshot> = {}): ActivationSnapshot {
  return {
    workflow_version: 1,
    stage: "provisioning_consent_required",
    completed_milestones: ["business", "receptionist"],
    next_action: "confirm_provisioning",
    blockers: [],
    warnings: [],
    profile: {
      owner_name: "Maya",
      business_name: "Atelier Maya",
      business_type: "Florist",
      public_description: "A neighbourhood flower studio.",
      timezone: "Europe/Paris",
      business_hours: null,
      existing_phone_e164: "+33612345678",
      confirmed_carrier: "orange",
      receptionist_name: "Camille",
      faqs: [],
      special_instructions: null,
      escalation_notes: null,
      detected_carrier: "orange",
      detected_number_type: "mobile",
      carrier_lookup_status: "succeeded",
      carrier_looked_up_at: "2026-07-17T10:00:00Z",
      content_revision: 1,
      routing_revision: 1,
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
      profile_confirmed_at: "2026-07-17T09:00:00Z",
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
      eligible: true,
      plan_tier: "starter",
      subscription_status: "active",
      allocated_minutes: 60,
      minutes_remaining: 60,
      current_period_start: "2026-07-17T09:00:00Z",
      current_period_end: "2026-08-16T09:00:00Z",
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
      stage: "number_provisioning",
      can_provision_number: true,
      can_activate: false,
      should_enable_phone: false,
      can_route: false,
      blockers: [],
      warnings: [],
      policy_version: "runtime-v2",
    },
    evaluated_at: "2026-07-17T10:00:00Z",
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

describe("number milestone", () => {
  beforeEach(() => {
    activateStarterMock.mockReset().mockResolvedValue({
      status: "success",
      data: snapshot(),
      message: "Local starter plan activated.",
    });
    checkoutMock.mockReset().mockResolvedValue({
      status: "success",
      data: { url: "https://checkout.stripe.test/session" },
      message: "Checkout session created.",
    });
    confirmProvisioningMock.mockReset().mockResolvedValue({
      status: "success",
      data: snapshot({ stage: "provisioning" }),
      message: "Number provisioning started.",
    });
    retryProvisioningMock.mockReset().mockResolvedValue({
      status: "success",
      data: snapshot({ stage: "provisioning" }),
      message: "Number provisioning retry started.",
    });
    refreshMock.mockReset();
  });

  it("treats local payment as eligibility and never provisions after payment alone", async () => {
    render(
      <NumberMilestone
        localBilling
        snapshot={snapshot({
          stage: "payment_required",
          billing: { ...snapshot().billing, eligible: false, plan_tier: null, subscription_status: null },
        })}
      />,
    );

    expect(screen.getByText(/does not order a phone number/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Activate local starter plan/i }));

    await waitFor(() => expect(activateStarterMock).toHaveBeenCalledTimes(1));
    expect(confirmProvisioningMock).not.toHaveBeenCalled();
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it("opens the real checkout URL on the client and explains cancellation has no charge", async () => {
    const navigate = vi.fn();
    render(<PaymentAction localBilling={false} navigate={navigate} />);

    expect(screen.getByText(/Cancel checkout before completing payment/i)).toHaveTextContent(/Presvo will not charge/i);
    fireEvent.click(screen.getByRole("button", { name: /Start starter plan/i }));

    await waitFor(() => expect(checkoutMock).toHaveBeenCalledTimes(1));
    expect(navigate).toHaveBeenCalledWith("https://checkout.stripe.test/session");
    expect(confirmProvisioningMock).not.toHaveBeenCalled();
  });

  it("does not provision after payment alone and requires explicit review", () => {
    render(<NumberMilestone localBilling={false} snapshot={snapshot()} />);

    expect(screen.getByRole("button", { name: /Review number provisioning/i })).toBeEnabled();
    expect(screen.getByText(/Payment activates your plan/i)).toBeInTheDocument();
    expect(confirmProvisioningMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Review number provisioning/i }));
    expect(screen.getByRole("heading", { name: /Provision your French Presvo number/i })).toBeInTheDocument();
    expect(screen.getByText(/France \(\+33\)/i)).toBeInTheDocument();
    expect(screen.getByText(/One Presvo number/i)).toBeInTheDocument();
    expect(screen.getByText(/Configure conditional forwarding/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Confirm and provision my number/i })).toBeEnabled();
  });

  it("honors canonical payment_required even when an assigned number already exists", () => {
    render(
      <NumberMilestone
        localBilling={false}
        snapshot={snapshot({
          stage: "payment_required",
          billing: { ...snapshot().billing, eligible: false, subscription_status: "past_due" },
          number: {
            assigned_e164: "+33187654321",
            country_code: "FR",
            provider_ready: true,
            provisioning_status: "succeeded",
            can_retry: false,
          },
        })}
      />,
    );

    expect(screen.getByRole("button", { name: /Start starter plan/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Continue to forwarding/i })).not.toBeInTheDocument();
  });

  it("queues exactly one provisioning consent while the action is pending", async () => {
    const pending = deferred<{
      status: "success";
      data: ActivationSnapshot;
      message: string;
    }>();
    confirmProvisioningMock.mockReturnValue(pending.promise);
    render(<ProvisioningConsent />);

    fireEvent.click(screen.getByRole("button", { name: /Review number provisioning/i }));
    const confirm = screen.getByRole("button", { name: /Confirm and provision my number/i });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    expect(confirmProvisioningMock).toHaveBeenCalledTimes(1);
    expect(confirm).toBeDisabled();
    expect(screen.getByRole("status", { name: /Loading/i })).toBeInTheDocument();

    await act(async () =>
      pending.resolve({
        status: "success",
        data: snapshot({ stage: "provisioning" }),
        message: "Number provisioning started.",
      }),
    );
  });

  it("resumes pending provisioning without offering another consent", () => {
    render(
      <NumberMilestone
        localBilling={false}
        snapshot={snapshot({
          stage: "provisioning",
          number: { ...snapshot().number, provisioning_status: "running", can_retry: false },
        })}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(/Provisioning your French number/i);
    expect(screen.getByText(/You can safely leave and return/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Confirm and provision my number/i })).not.toBeInTheDocument();
  });

  it("keeps a safe consent failure inside the open review dialog", async () => {
    confirmProvisioningMock.mockResolvedValueOnce({
      status: "error",
      code: "request_failed",
      message: "We couldn't complete this step. Refresh and try again.",
    });
    render(<ProvisioningConsent />);

    fireEvent.click(screen.getByRole("button", { name: /Review number provisioning/i }));
    fireEvent.click(screen.getByRole("button", { name: /Confirm and provision my number/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't complete this step/i);
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Confirm and provision my number/i })).toBeEnabled();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("prominently displays the assigned French number", () => {
    render(
      <NumberMilestone
        localBilling={false}
        snapshot={snapshot({
          stage: "forwarding_required",
          number: {
            assigned_e164: "+33187654321",
            country_code: "FR",
            provider_ready: true,
            provisioning_status: "succeeded",
            can_retry: false,
          },
        })}
      />,
    );

    expect(screen.getByText("+33 1 87 65 43 21")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Continue to forwarding/i })).toHaveAttribute(
      "href",
      "/activate?milestone=forwarding",
    );
  });

  it("does not present an assigned number as ready without provider readiness", () => {
    render(
      <NumberMilestone
        localBilling={false}
        snapshot={snapshot({
          stage: "provisioning",
          number: {
            assigned_e164: "+33187654321",
            country_code: "FR",
            provider_ready: false,
            provisioning_status: "running",
            can_retry: false,
          },
        })}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(/Provisioning your French number/i);
    expect(screen.queryByText("Number ready")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Continue to forwarding/i })).not.toBeInTheDocument();
  });

  it("offers a safe retry without ordering a second number", async () => {
    render(
      <NumberMilestone
        localBilling={false}
        snapshot={snapshot({
          stage: "provisioning_failed",
          warnings: ["telnyx secret upstream failure"],
          activation: { ...snapshot().activation, last_failure_code: "number_order_temporarily_unavailable" },
          number: { ...snapshot().number, provisioning_status: "failed", can_retry: true },
        })}
      />,
    );

    expect(screen.getByText(/No second number will be ordered/i)).toBeInTheDocument();
    expect(screen.queryByText(/telnyx secret/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Retry provisioning/i }));

    await waitFor(() => expect(retryProvisioningMock).toHaveBeenCalledTimes(1));
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it("announces an asynchronous retry failure without leaking provider details", async () => {
    retryProvisioningMock.mockResolvedValueOnce({
      status: "error",
      code: "request_failed",
      message: "We couldn't complete this step. Refresh and try again.",
    });
    render(
      <NumberMilestone
        localBilling={false}
        snapshot={snapshot({
          stage: "provisioning_failed",
          number: { ...snapshot().number, provisioning_status: "failed", can_retry: true },
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Retry provisioning/i }));

    const error = await screen.findByText(/couldn't complete this step/i);
    expect(error.closest('[role="alert"]')).not.toBeNull();
    expect(screen.queryByText(/provider/i)).not.toBeInTheDocument();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("explains a terminal assignment inconsistency without retry or profile correction", () => {
    render(
      <NumberMilestone
        localBilling={false}
        snapshot={snapshot({
          stage: "provisioning_failed",
          blockers: ["number_assignment_inconsistent", "private-provider-detail"],
          number: {
            ...snapshot().number,
            assigned_e164: "+33187654321",
            provider_ready: false,
            provisioning_status: "succeeded",
            can_retry: false,
          },
        })}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/couldn't verify your assigned number/i);
    expect(screen.getByText(/Reference: number_assignment_inconsistent/i)).toBeInTheDocument();
    expect(screen.queryByText("+33187654321")).not.toBeInTheDocument();
    expect(screen.queryByText("+33 1 87 65 43 21")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry provisioning/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Correct business profile/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/private-provider-detail/i)).not.toBeInTheDocument();
  });

  it("sends terminal failures to profile correction with only a safe reference", () => {
    render(
      <NumberMilestone
        localBilling={false}
        snapshot={snapshot({
          stage: "provisioning_failed",
          blockers: ["raw-provider-validation-secret"],
          activation: { ...snapshot().activation, last_failure_code: "profile_correction_required" },
          number: { ...snapshot().number, provisioning_status: "failed", can_retry: false },
        })}
      />,
    );

    expect(screen.getByText(/Reference: number_provisioning_failed/i)).toBeInTheDocument();
    expect(screen.queryByText(/raw-provider-validation-secret/i)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Correct business profile/i })).toHaveAttribute(
      "href",
      "/activate?milestone=business",
    );
  });
});
