import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DeleteCallDialog } from "@/components/calls/delete-call-dialog";
import { RecordingPanel } from "@/components/calls/recording-panel";
import { AnsweringStatusBanner } from "@/components/dashboard/answering-status-banner";
import { RecentCallsList } from "@/components/dashboard/recent-calls-list";
import type { CallHistoryListItem } from "@/lib/types/calls";
import type { OnboardingStatus } from "@/lib/types/onboarding";

function structuredCall(overrides: Record<string, unknown> = {}): CallHistoryListItem {
  return {
    id: "call-1",
    status: "completed",
    caller_number: "+33123456789",
    started_at: "2026-07-18T10:00:00Z",
    ended_at: "2026-07-18T10:01:00Z",
    duration_seconds: 60,
    minutes_charged: 1,
    summary_text: "The caller would like to arrange a consultation.",
    summary_status: "ready",
    caller_intent: "Book a consultation",
    action_items: ["Return the call"],
    sentiment: "positive",
    follow_up_required: true,
    has_recording: true,
    ...overrides,
  } as CallHistoryListItem;
}

function onboardingStatus(overrides: Partial<OnboardingStatus> = {}): OnboardingStatus {
  return {
    subscription_status: "active",
    plan_tier: "starter",
    minutes_remaining: 183,
    phone_number: "+33123456789",
    phone_number_status: "ready",
    agent_setup_complete: true,
    can_retry_provisioning: false,
    stage: "live",
    can_activate: true,
    can_route: true,
    blockers: [],
    warnings: [],
    evaluated_at: "2026-07-18T10:00:00Z",
    policy_version: "runtime-v1",
    ...overrides,
  };
}

describe("call handoff", () => {
  it("shows the call outcome and an obvious follow-up", () => {
    render(<RecentCallsList calls={[structuredCall()]} />);

    expect(screen.getByText("Book a consultation")).toBeInTheDocument();
    expect(screen.getByText(/Follow-up needed/i)).toBeInTheDocument();
    expect(screen.getByText("Return the call")).toBeInTheDocument();
  });

  it("shows bounded action items and the remaining count", () => {
    render(
      <RecentCallsList
        calls={[
          structuredCall({
            action_items: ["First action", "Second action", "Third action", "Fourth action"],
          }),
        ]}
      />,
    );

    expect(screen.getByText("First action")).toBeInTheDocument();
    expect(screen.getByText("Third action")).toBeInTheDocument();
    expect(screen.queryByText("Fourth action")).not.toBeInTheDocument();
    expect(screen.getByText("+1 more")).toBeInTheDocument();
  });

  it("renders duplicate action labels without duplicate React keys", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    try {
      render(
        <RecentCallsList
          calls={[
            structuredCall({
              action_items: ["Return the call", "Return the call"],
            }),
          ]}
        />,
      );

      expect(screen.getAllByText("Return the call")).toHaveLength(2);
      expect(consoleError.mock.calls.flat().join(" ")).not.toMatch(/same key/i);
    } finally {
      consoleError.mockRestore();
    }
  });

  it("shows processing, unavailable, and no-action states", () => {
    const { rerender } = render(
      <RecentCallsList
        calls={[
          structuredCall({
            summary_text: null,
            summary_status: "processing",
            caller_intent: null,
            action_items: null,
            sentiment: null,
            follow_up_required: null,
          }),
        ]}
      />,
    );
    expect(screen.getByText(/Summary is still processing/i)).toBeInTheDocument();

    rerender(
      <RecentCallsList
        calls={[
          structuredCall({
            summary_text: null,
            summary_status: "unavailable",
            caller_intent: null,
            action_items: null,
            sentiment: null,
            follow_up_required: null,
          }),
        ]}
      />,
    );
    expect(screen.getByText(/Summary unavailable/i)).toBeInTheDocument();

    rerender(
      <RecentCallsList
        calls={[
          structuredCall({
            action_items: [],
            follow_up_required: false,
          }),
        ]}
      />,
    );
    expect(screen.getByText(/No follow-up needed/i)).toBeInTheDocument();
    expect(screen.getByText(/No action items suggested/i)).toBeInTheDocument();
  });

  it("uses a privacy-safe label when caller ID is withheld", () => {
    render(<RecentCallsList calls={[structuredCall({ caller_number: null })]} />);

    expect(screen.getByText("Private caller")).toBeInTheDocument();
  });

  it("renders original audio with native controls", () => {
    render(<RecordingPanel recordingUrl="https://recording.test/call.ogg" />);

    expect(screen.getByLabelText(/Original call recording/i)).toHaveAttribute("controls");
    expect(screen.getByLabelText(/Original call recording/i)).toHaveAttribute("preload", "metadata");
  });

  it("makes active call answering the lead dashboard status", () => {
    render(<AnsweringStatusBanner onboardingStatus={onboardingStatus()} />);

    expect(screen.getByText("Presvo is answering")).toBeInTheDocument();
  });

  it("explains why runtime answering is paused", () => {
    render(
      <AnsweringStatusBanner
        onboardingStatus={onboardingStatus({
          minutes_remaining: 0,
          stage: "suspended",
          can_route: false,
          blockers: ["minutes_exhausted"],
        })}
      />,
    );

    expect(screen.getByText("Presvo is paused")).toBeInTheDocument();
    expect(screen.getByText(/No minutes remain/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Review activation/i })).toHaveAttribute("href", "/activate");
  });

  it("confirms active-account call removal without a backup-erasure promise", () => {
    render(<DeleteCallDialog callId="call-1" deleteHandler={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove call" }));

    expect(screen.getByRole("heading", { name: /Remove this call/i })).toBeInTheDocument();
    expect(screen.getByText(/active Presvo account/i)).toBeInTheDocument();
    expect(screen.queryByText(/backup/i)).not.toBeInTheDocument();
  });

  it("keeps the dialog actionable when deletion must be retried", async () => {
    const deleteHandler = vi.fn().mockResolvedValue({
      status: "error",
      message: "Presvo could not remove this call right now. Try again.",
    });
    render(<DeleteCallDialog callId="call-1" deleteHandler={deleteHandler} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove call" }));
    const removeButtons = screen.getAllByRole("button", { name: "Remove call" });
    const confirmationButton = removeButtons.at(-1);
    if (!confirmationButton) {
      throw new Error("Remove call confirmation is missing");
    }
    fireEvent.click(confirmationButton);

    await waitFor(() => {
      expect(screen.getByText(/could not remove this call right now/i)).toBeInTheDocument();
    });
    expect(deleteHandler).toHaveBeenCalledWith("call-1");
  });
});
