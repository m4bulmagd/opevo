import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listCallsMock = vi.fn();
const getCallDetailMock = vi.fn();
const getAccountMock = vi.fn();
const deleteCallMock = vi.fn();
const revalidatePathMock = vi.fn();
const notFoundMock = vi.fn();
const redirectMock = vi.fn(() => {
  throw new Error("NEXT_REDIRECT");
});

vi.mock("next/cache", () => ({
  revalidatePath: revalidatePathMock,
}));

vi.mock("next/navigation", () => ({
  notFound: notFoundMock,
  redirect: redirectMock,
}));

vi.mock("@/lib/api/calls", () => ({
  listCalls: listCallsMock,
  getCallDetail: getCallDetailMock,
  deleteCall: deleteCallMock,
}));

vi.mock("@/lib/api/account", () => ({
  getAccount: getAccountMock,
}));

describe("calls pages", () => {
  beforeEach(() => {
    getAccountMock.mockReset().mockResolvedValue({
      status: "active",
      serving: true,
      deactivation: null,
      reactivation_allowed: false,
      blocker: null,
    });
  });

  it("renders empty and populated call states", async () => {
    listCallsMock.mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 20,
      offset: 0,
      has_more: false,
    });

    const { default: Page } = await import("@/app/(app)/dashboard/calls/page");
    render(await Page());

    expect(screen.getByText(/No calls yet/i)).toBeInTheDocument();

    listCallsMock.mockResolvedValueOnce({
      calls: [
        {
          id: "call-1",
          status: "completed",
          caller_number: "+33123456789",
          started_at: "2026-03-28T10:00:00Z",
          ended_at: "2026-03-28T10:01:00Z",
          duration_seconds: 60,
          minutes_charged: 1,
          summary_text: "Caller asked about opening hours.",
          summary_status: "ready",
          caller_intent: "Check opening hours",
          action_items: ["Send weekday hours"],
          sentiment: "neutral",
          follow_up_required: true,
          has_recording: true,
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
      has_more: false,
    });

    const { default: HydratedPage } = await import("@/app/(app)/dashboard/calls/page");
    render(await HydratedPage());

    expect(screen.getByText(/Caller asked about opening hours/i)).toBeInTheDocument();
    expect(screen.getByText(/Check opening hours/i)).toBeInTheDocument();
    expect(screen.getByText(/Follow-up needed/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open call/i })).toHaveAttribute("href", "/dashboard/calls/call-1");
  });

  it("renders call detail transcript and recording fallback", async () => {
    getCallDetailMock.mockResolvedValueOnce({
      id: "call-1",
      status: "completed",
      caller_number: "+33123456789",
      started_at: "2026-03-28T10:00:00Z",
      ended_at: "2026-03-28T10:01:00Z",
      duration_seconds: 60,
      minutes_charged: 1,
      summary_text: "Caller asked about opening hours.",
      summary_status: "ready",
      caller_intent: "Check opening hours",
      action_items: ["Send weekday hours"],
      sentiment: "neutral",
      follow_up_required: true,
      recording_url: null,
      transcript: [
        {
          speaker: "CALLER",
          text: "What are your opening hours?",
          sequence_number: 1,
          created_at: "2026-03-28T10:00:10Z",
        },
      ],
    });

    const { default: DetailPage } = await import("@/app/(app)/dashboard/calls/[callId]/page");
    render(await DetailPage({ params: Promise.resolve({ callId: "call-1" }) }));

    expect(screen.getByText(/What are your opening hours\?/i)).toBeInTheDocument();
    expect(screen.getByText(/Check opening hours/i)).toBeInTheDocument();
    expect(screen.getByText(/Send weekday hours/i)).toBeInTheDocument();
    expect(screen.getByText(/Recording unavailable/i)).toBeInTheDocument();
  });

  it("defers call removal until an active call completes", async () => {
    getCallDetailMock.mockResolvedValueOnce({
      id: "call-active",
      status: "connected",
      caller_number: "+33123456789",
      started_at: "2026-03-28T10:00:00Z",
      ended_at: null,
      duration_seconds: null,
      minutes_charged: null,
      summary_text: null,
      summary_status: "processing",
      caller_intent: null,
      action_items: null,
      sentiment: null,
      follow_up_required: null,
      recording_url: null,
      transcript: [],
    });

    const { default: DetailPage } = await import("@/app/(app)/dashboard/calls/[callId]/page");
    render(await DetailPage({ params: Promise.resolve({ callId: "call-active" }) }));

    const availability = screen.getByText(/available after the call completes/i);
    expect(availability).toBeInTheDocument();
    expect(availability.closest('[role="alert"]')).not.toBeNull();
    expect(screen.queryByRole("button", { name: /Remove call/i })).not.toBeInTheDocument();
  });

  it.each([
    "deactivating",
    "inactive",
  ] as const)("keeps %s call history read-only by hiding removal", async (status) => {
    getAccountMock.mockResolvedValueOnce({
      status,
      serving: false,
      deactivation: null,
      reactivation_allowed: status === "inactive",
      blocker: status === "inactive" ? null : "account_deactivating",
    });
    getCallDetailMock.mockResolvedValueOnce({
      id: "call-retained",
      status: "completed",
      caller_number: "+33123456789",
      started_at: "2026-03-28T10:00:00Z",
      ended_at: "2026-03-28T10:01:00Z",
      duration_seconds: 60,
      minutes_charged: 1,
      summary_text: "Retained history.",
      summary_status: "ready",
      caller_intent: null,
      action_items: null,
      sentiment: null,
      follow_up_required: null,
      recording_url: null,
      transcript: [],
    });

    const { default: DetailPage } = await import("@/app/(app)/dashboard/calls/[callId]/page");
    render(
      await DetailPage({
        params: Promise.resolve({ callId: "call-retained" }),
      }),
    );

    expect(screen.getByText("Retained history.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove call/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Call history is read-only while your account/i)).toBeInTheDocument();
  });

  it("deletes a call, revalidates call routes, and redirects after success", async () => {
    deleteCallMock.mockResolvedValueOnce(undefined);

    const { deleteCallAction } = await import("@/app/(app)/dashboard/calls/actions");
    await expect(deleteCallAction("call-1")).rejects.toThrow("NEXT_REDIRECT");

    expect(deleteCallMock).toHaveBeenCalledWith("call-1");
    expect(revalidatePathMock).toHaveBeenCalledWith("/dashboard/calls");
    expect(revalidatePathMock).toHaveBeenCalledWith("/dashboard");
    expect(redirectMock).toHaveBeenCalledWith("/dashboard/calls");
  });
});
