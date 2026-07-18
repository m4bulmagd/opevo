import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfileForm } from "@/app/(activation)/activate/_components/profile/profile-form";
import type { ActivationSnapshot, BusinessProfile } from "@/lib/types/activation";

const { confirmProfileMock, pushMock, refreshMock, saveProfileMock } = vi.hoisted(() => ({
  confirmProfileMock: vi.fn(),
  pushMock: vi.fn(),
  refreshMock: vi.fn(),
  saveProfileMock: vi.fn(),
}));

vi.mock("@/app/(activation)/activate/actions", () => ({
  confirmProfileAction: confirmProfileMock,
  lookupCarrierAction: vi.fn(),
  saveBusinessProfileAction: saveProfileMock,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, refresh: refreshMock }),
}));

function profile(overrides: Partial<BusinessProfile> = {}): BusinessProfile {
  return {
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
    ...overrides,
  };
}

function profileSnapshot(profileOverrides: Partial<BusinessProfile> = {}): ActivationSnapshot {
  return {
    workflow_version: 1,
    stage: "profile_required",
    completed_milestones: [],
    next_action: "complete_profile",
    blockers: ["profile_not_confirmed"],
    warnings: [],
    profile: profile(profileOverrides),
    profile_constraints: {
      name_max_length: 12,
      business_type_max_length: 16,
      public_description_max_length: 40,
      faq_max_items: 2,
      faq_question_max_length: 20,
      faq_answer_max_length: 30,
      special_instructions_max_length: 50,
      escalation_notes_max_length: 60,
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
  };
}

function savedProfile(input: unknown): { status: "success"; data: BusinessProfile; message: string } {
  return { status: "success", data: profile(input as Partial<BusinessProfile>), message: "Profile saved." };
}

function completeBusinessProfile(overrides: Partial<BusinessProfile> = {}): Partial<BusinessProfile> {
  return {
    owner_name: "Maya",
    business_name: "Atelier",
    business_type: "Florist",
    timezone: "Europe/Paris",
    existing_phone_e164: "+33612345678",
    confirmed_carrier: "orange",
    ...overrides,
  };
}

describe("profile form", () => {
  beforeEach(() => {
    saveProfileMock.mockReset().mockImplementation(async (input) => savedProfile(input));
    confirmProfileMock.mockReset().mockResolvedValue({
      status: "success",
      data: profileSnapshot(),
      message: "Profile confirmed.",
    });
    pushMock.mockReset();
    refreshMock.mockReset();
  });

  afterEach(() => vi.useRealTimers());

  it("shows saved only after the deterministic 700ms autosave succeeds", async () => {
    vi.useFakeTimers();
    render(<ProfileForm milestone="business" snapshot={profileSnapshot()} />);

    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "Maya" } });
    expect(screen.getByText(/Unsaved/i)).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(699));
    expect(saveProfileMock).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(1));

    expect(saveProfileMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/^Saved$/i)).toBeInTheDocument();
    expect(saveProfileMock).toHaveBeenCalledWith(
      expect.objectContaining({
        owner_name: "Maya",
        business_name: null,
        public_description: null,
        timezone: "Europe/Paris",
        business_hours: expect.any(Object),
        faqs: [],
      }),
    );
  });

  it("ignores an older save response when a newer save finishes first", async () => {
    vi.useFakeTimers();
    let resolveFirst: ((value: ReturnType<typeof savedProfile>) => void) | undefined;
    saveProfileMock
      .mockImplementationOnce(
        (input) =>
          new Promise((resolve) => {
            resolveFirst = resolve;
            void input;
          }),
      )
      .mockImplementationOnce(async (input) => savedProfile(input));
    render(<ProfileForm milestone="business" snapshot={profileSnapshot()} />);

    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "Maya" } });
    await act(() => vi.advanceTimersByTimeAsync(700));
    fireEvent.change(screen.getByLabelText(/Business name/i), { target: { value: "Presvo" } });
    await act(() => vi.advanceTimersByTimeAsync(700));
    expect(screen.getByText(/^Saved$/i)).toBeInTheDocument();
    await act(async () =>
      resolveFirst?.({ status: "error", code: "request_failed", message: "late failure" } as never),
    );

    expect(screen.getByText(/^Saved$/i)).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't save/i)).not.toBeInTheDocument();
  });

  it("defaults to Europe/Paris and resumes the full server draft after refresh", () => {
    render(
      <ProfileForm
        milestone="receptionist"
        snapshot={profileSnapshot({
          owner_name: "Maya",
          business_name: "Atelier Maya",
          timezone: "Europe/Paris",
          receptionist_name: "Camille",
          public_description: "A neighbourhood flower studio.",
          faqs: [{ question: "Sunday?", answer: "Closed." }],
        })}
      />,
    );

    expect(screen.getByLabelText(/Receptionist name/i)).toHaveValue("Camille");
    expect(screen.getByLabelText(/Public description/i)).toHaveValue("A neighbourhood flower studio.");
    expect(screen.getByDisplayValue("Sunday?")).toBeInTheDocument();
    expect(screen.getByText(/Atelier Maya/i)).toBeInTheDocument();
    expect(screen.getByText(/neighbourhood flower studio/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Knowledge preview/i })).toBeInTheDocument();
  });

  it("defaults a null business timezone to Europe/Paris", () => {
    render(<ProfileForm milestone="business" snapshot={profileSnapshot({ timezone: null })} />);

    expect(screen.getByLabelText(/Timezone/i)).toHaveValue("Europe/Paris");
  });

  it("uses server constraints for lengths, counters, and the FAQ limit", () => {
    render(<ProfileForm milestone="receptionist" snapshot={profileSnapshot()} />);

    expect(screen.getByLabelText(/Receptionist name/i)).toHaveAttribute("maxlength", "12");
    expect(screen.getByLabelText(/Public description/i)).toHaveAttribute("maxlength", "40");
    expect(screen.getByText("0 / 40")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Add FAQ/i }));
    fireEvent.click(screen.getByRole("button", { name: /Add FAQ/i }));
    expect(screen.queryByRole("button", { name: /Add FAQ/i })).not.toBeInTheDocument();
    expect(screen.getAllByLabelText(/FAQ question/i)).toHaveLength(2);
    expect(screen.getAllByLabelText(/FAQ answer/i)).toHaveLength(2);
    expect(screen.getAllByLabelText(/FAQ question/i)[0]).toHaveAttribute("maxlength", "20");
    expect(screen.getAllByLabelText(/FAQ answer/i)[0]).toHaveAttribute("maxlength", "30");
  });

  it("flushes the exact latest business draft before navigating", async () => {
    vi.useFakeTimers();
    render(<ProfileForm milestone="business" snapshot={profileSnapshot(completeBusinessProfile())} />);
    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "Maya" } });
    fireEvent.change(screen.getByLabelText(/Business name/i), { target: { value: "Atelier 2" } });
    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));

    await act(() => vi.runAllTimersAsync());
    expect(saveProfileMock).toHaveBeenCalledTimes(1);
    expect(saveProfileMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ owner_name: "Maya", business_name: "Atelier 2" }),
    );
    expect(confirmProfileMock).not.toHaveBeenCalled();
    expect(pushMock).toHaveBeenCalledWith("/activate?milestone=receptionist");
  });

  it("confirms the profile only after the receptionist's latest draft saves", async () => {
    const order: string[] = [];
    saveProfileMock.mockImplementation(async (input) => {
      order.push("save");
      return savedProfile(input);
    });
    confirmProfileMock.mockImplementation(async () => {
      order.push("confirm");
      return { status: "success", data: profileSnapshot(), message: "Profile confirmed." };
    });
    render(
      <ProfileForm milestone="receptionist" snapshot={profileSnapshot({ public_description: "A trusted florist." })} />,
    );
    fireEvent.change(screen.getByLabelText(/Receptionist name/i), { target: { value: "Camille" } });
    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));

    await waitFor(() => expect(confirmProfileMock).toHaveBeenCalledTimes(1));
    expect(order).toEqual(["save", "confirm"]);
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });

  it("does not navigate or confirm when the flush save fails", async () => {
    saveProfileMock.mockResolvedValue({ status: "error", code: "request_failed", message: "Try again." });
    render(<ProfileForm milestone="business" snapshot={profileSnapshot(completeBusinessProfile())} />);
    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "Maya" } });
    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));

    expect(await screen.findByText(/Couldn't save/i)).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
    expect(confirmProfileMock).not.toHaveBeenCalled();
  });

  it("focuses the first invalid control and does not save on invalid submit", async () => {
    render(<ProfileForm milestone="business" snapshot={profileSnapshot()} />);

    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));

    await waitFor(() => expect(screen.getByLabelText(/Owner name/i)).toHaveFocus());
    expect(saveProfileMock).not.toHaveBeenCalled();
  });
});
