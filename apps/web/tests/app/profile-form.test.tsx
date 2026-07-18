import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfileForm } from "@/app/(activation)/activate/_components/profile/profile-form";
import type { ActivationSnapshot, BusinessHours, BusinessProfile } from "@/lib/types/activation";

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
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

function businessHoursWithMondayOverlap(): BusinessHours {
  return {
    monday: {
      closed: false,
      intervals: [
        { start: "09:00", end: "17:00" },
        { start: "13:00", end: "18:00" },
      ],
    },
    tuesday: { closed: true, intervals: [] },
    wednesday: { closed: true, intervals: [] },
    thursday: { closed: true, intervals: [] },
    friday: { closed: true, intervals: [] },
    saturday: { closed: true, intervals: [] },
    sunday: { closed: true, intervals: [] },
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

  it("never overlaps saves and coalesces queued edits to the latest complete draft", async () => {
    vi.useFakeTimers();
    const first = deferred<ReturnType<typeof savedProfile>>();
    const second = deferred<ReturnType<typeof savedProfile>>();
    saveProfileMock.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
    render(<ProfileForm milestone="business" snapshot={profileSnapshot()} />);

    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "Maya" } });
    await act(() => vi.advanceTimersByTimeAsync(700));
    fireEvent.change(screen.getByLabelText(/Business name/i), { target: { value: "Presvo" } });
    await act(() => vi.advanceTimersByTimeAsync(700));
    fireEvent.change(screen.getByLabelText(/Business name/i), { target: { value: "Newest" } });
    await act(() => vi.advanceTimersByTimeAsync(700));

    expect(saveProfileMock).toHaveBeenCalledTimes(1);
    await act(async () => first.resolve(savedProfile(saveProfileMock.mock.calls[0]?.[0])));

    expect(saveProfileMock).toHaveBeenCalledTimes(2);
    expect(saveProfileMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ owner_name: "Maya", business_name: "Newest" }),
    );
    await act(async () => second.resolve(savedProfile(saveProfileMock.mock.calls[1]?.[0])));
  });

  it("shows Saved only after the exact latest queued draft is durable", async () => {
    vi.useFakeTimers();
    const first = deferred<ReturnType<typeof savedProfile>>();
    const second = deferred<ReturnType<typeof savedProfile>>();
    saveProfileMock.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
    render(<ProfileForm milestone="business" snapshot={profileSnapshot()} />);

    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "Maya" } });
    await act(() => vi.advanceTimersByTimeAsync(700));
    fireEvent.change(screen.getByLabelText(/Business name/i), { target: { value: "Newest" } });
    await act(() => vi.advanceTimersByTimeAsync(700));

    await act(async () => first.resolve(savedProfile(saveProfileMock.mock.calls[0]?.[0])));

    expect(screen.queryByText(/^Saved$/i)).not.toBeInTheDocument();
    expect(screen.getByText(/^Saving…$/i)).toBeInTheDocument();
    await act(async () => second.resolve(savedProfile(saveProfileMock.mock.calls[1]?.[0])));

    expect(screen.getByText(/^Saved$/i)).toBeInTheDocument();
  });

  it("queues a revert to the last-saved draft behind a conflicting in-flight save", async () => {
    vi.useFakeTimers();
    const first = deferred<ReturnType<typeof savedProfile>>();
    const reverted = deferred<ReturnType<typeof savedProfile>>();
    saveProfileMock.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => reverted.promise);
    render(<ProfileForm milestone="business" snapshot={profileSnapshot()} />);

    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "Maya" } });
    await act(() => vi.advanceTimersByTimeAsync(700));
    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "" } });
    await act(() => vi.advanceTimersByTimeAsync(700));

    expect(saveProfileMock).toHaveBeenCalledTimes(1);
    await act(async () => first.resolve(savedProfile(saveProfileMock.mock.calls[0]?.[0])));

    expect(saveProfileMock).toHaveBeenCalledTimes(2);
    expect(saveProfileMock).toHaveBeenLastCalledWith(expect.objectContaining({ owner_name: null }));
    await act(async () => reverted.resolve(savedProfile(saveProfileMock.mock.calls[1]?.[0])));
    expect(screen.getByText(/^Saved$/i)).toBeInTheDocument();
  });

  it("restores Saved when a local edit reverts without a conflicting save", async () => {
    vi.useFakeTimers();
    render(<ProfileForm milestone="business" snapshot={profileSnapshot()} />);

    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "Maya" } });
    expect(screen.getByText(/^Unsaved$/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "" } });

    expect(screen.getByText(/^Saved$/i)).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(700));
    expect(saveProfileMock).not.toHaveBeenCalled();
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

  it("resumes a persisted Other carrier as an explicit suggestion after refresh", () => {
    render(
      <ProfileForm
        milestone="business"
        snapshot={profileSnapshot({
          ...completeBusinessProfile({ confirmed_carrier: null }),
          detected_carrier: "other",
        })}
      />,
    );

    expect(screen.getByText("Suggested carrier")).toBeInTheDocument();
    expect(screen.getByText(/^Other$/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Confirm carrier/i })).toBeInTheDocument();
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

  it("flush waits through the in-flight save and latest queued draft before navigating", async () => {
    vi.useFakeTimers();
    const first = deferred<ReturnType<typeof savedProfile>>();
    const latest = deferred<ReturnType<typeof savedProfile>>();
    saveProfileMock.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => latest.promise);
    render(<ProfileForm milestone="business" snapshot={profileSnapshot(completeBusinessProfile())} />);

    fireEvent.change(screen.getByLabelText(/Owner name/i), { target: { value: "Maya 2" } });
    await act(() => vi.advanceTimersByTimeAsync(700));
    fireEvent.change(screen.getByLabelText(/Business name/i), { target: { value: "Atelier 2" } });
    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));

    expect(saveProfileMock).toHaveBeenCalledTimes(1);
    expect(pushMock).not.toHaveBeenCalled();
    await act(async () => first.resolve(savedProfile(saveProfileMock.mock.calls[0]?.[0])));

    expect(saveProfileMock).toHaveBeenCalledTimes(2);
    expect(saveProfileMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ owner_name: "Maya 2", business_name: "Atelier 2" }),
    );
    expect(pushMock).not.toHaveBeenCalled();
    await act(async () => latest.resolve(savedProfile(saveProfileMock.mock.calls[1]?.[0])));

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
    expect(pushMock).toHaveBeenCalledWith("/activate?milestone=number");
    expect(refreshMock).not.toHaveBeenCalled();
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

  it("settles a rejected save transport as an error and does not navigate", async () => {
    saveProfileMock.mockRejectedValueOnce(new Error("transport unavailable"));
    render(<ProfileForm milestone="business" snapshot={profileSnapshot(completeBusinessProfile())} />);
    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));

    await waitFor(() => expect(document.querySelector('[data-status="error"]')).toHaveTextContent("Couldn't save"));
    expect(await screen.findAllByText(/couldn't save your profile/i)).not.toHaveLength(0);
    expect(pushMock).not.toHaveBeenCalled();
    expect(confirmProfileMock).not.toHaveBeenCalled();
  });

  it("focuses the first invalid control and does not save on invalid submit", async () => {
    render(<ProfileForm milestone="business" snapshot={profileSnapshot()} />);

    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));

    await waitFor(() => expect(screen.getByLabelText(/Owner name/i)).toHaveFocus());
    expect(saveProfileMock).not.toHaveBeenCalled();
  });

  it("highlights and focuses the first invalid business interval on submit without a prior blur", async () => {
    render(
      <ProfileForm
        milestone="business"
        snapshot={profileSnapshot(completeBusinessProfile({ business_hours: businessHoursWithMondayOverlap() }))}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));

    expect(await screen.findByText(/Monday intervals cannot overlap/i)).toBeInTheDocument();
    const invalidStart = screen.getByLabelText(/Monday start 2/i);
    expect(invalidStart).toHaveFocus();
    expect(invalidStart).toHaveAttribute("aria-invalid", "true");
    expect(saveProfileMock).not.toHaveBeenCalled();
  });

  it("associates a missing carrier error with the focused phone and clears it after confirmation", async () => {
    render(
      <ProfileForm
        milestone="business"
        snapshot={profileSnapshot({
          ...completeBusinessProfile({ confirmed_carrier: null }),
          detected_carrier: "Orange France",
        })}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Continue/i }));

    const phone = screen.getByLabelText(/Existing French number/i);
    await waitFor(() => expect(phone).toHaveFocus());
    expect(phone).toHaveAttribute("aria-invalid", "true");
    expect(phone).toHaveAccessibleDescription(/Confirm or choose the carrier/i);

    fireEvent.click(screen.getByRole("button", { name: /Confirm carrier/i }));

    await waitFor(() => expect(phone).toHaveAttribute("aria-invalid", "false"));
    expect(phone).not.toHaveAccessibleDescription(/Confirm or choose the carrier/i);
    expect(screen.queryByText(/Confirm or choose the carrier/i)).not.toBeInTheDocument();
  });
});
