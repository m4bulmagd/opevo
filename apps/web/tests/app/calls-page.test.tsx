import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const listCallsMock = vi.fn();
const getCallDetailMock = vi.fn();
const archiveCallMock = vi.fn();
const revalidatePathMock = vi.fn();
const notFoundMock = vi.fn();

vi.mock("next/cache", () => ({
  revalidatePath: revalidatePathMock,
}));

vi.mock("next/navigation", () => ({
  notFound: notFoundMock,
}));

vi.mock("@/lib/api/calls", () => ({
  listCalls: listCallsMock,
  getCallDetail: getCallDetailMock,
  archiveCall: archiveCallMock,
}));

describe("calls pages", () => {
  it("renders empty and populated call states", async () => {
    listCallsMock.mockResolvedValueOnce([]);

    const { default: Page } = await import("@/app/(app)/dashboard/calls/page");
    render(await Page());

    expect(screen.getByText(/No calls yet/i)).toBeInTheDocument();

    listCallsMock.mockResolvedValueOnce([
      {
        id: "call-1",
        status: "completed",
        caller_number: "+33123456789",
        started_at: "2026-03-28T10:00:00Z",
        ended_at: "2026-03-28T10:01:00Z",
        duration_seconds: 60,
        minutes_charged: 1,
        summary_text: "Caller asked about opening hours.",
        has_recording: true,
      },
    ]);

    const { default: HydratedPage } = await import("@/app/(app)/dashboard/calls/page");
    render(await HydratedPage());

    expect(screen.getByText(/Caller asked about opening hours/i)).toBeInTheDocument();
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
    expect(screen.getByText(/Recording unavailable/i)).toBeInTheDocument();
  });

  it("archives a call and revalidates the calls routes", async () => {
    archiveCallMock.mockResolvedValueOnce(undefined);

    const { archiveCallAction } = await import("@/app/(app)/dashboard/calls/actions");
    await archiveCallAction("call-1");

    expect(archiveCallMock).toHaveBeenCalledWith("call-1");
    expect(revalidatePathMock).toHaveBeenCalledWith("/dashboard/calls");
    expect(revalidatePathMock).toHaveBeenCalledWith("/dashboard");
  });
});
