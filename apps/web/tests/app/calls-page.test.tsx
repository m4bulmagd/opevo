import { fireEvent, render, screen, within } from "@testing-library/react";
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
  const callItem = {
    id: "call-1",
    status: "completed",
    caller_number: "+33123456789",
    started_at: "2026-03-28T10:00:00Z",
    ended_at: "2026-03-28T10:01:00Z",
    duration_seconds: 60,
    minutes_charged: 1,
    summary_text: "Caller asked about opening hours.",
    summary_status: "ready" as const,
    caller_intent: "Check opening hours",
    action_items: ["Send weekday hours"],
    sentiment: "neutral",
    follow_up_required: true,
    has_recording: true,
  };

  const secondPageItems = Array.from({ length: 20 }, (_, index) =>
    index === 0
      ? callItem
      : {
          ...callItem,
          id: `call-${index + 1}`,
          summary_text: `Additional call ${index + 1}`,
          caller_intent: null,
          action_items: null,
          follow_up_required: false,
        },
  );

  beforeEach(() => {
    listCallsMock.mockReset();
    getCallDetailMock.mockReset();
    deleteCallMock.mockReset();
    revalidatePathMock.mockClear();
    notFoundMock.mockClear();
    redirectMock.mockClear();
    getAccountMock.mockReset().mockResolvedValue({
      status: "active",
      serving: true,
      deactivation: null,
      reactivation_allowed: false,
      blocker: null,
    });
  });

  it("reads async URL state and renders range-preserving navigation", async () => {
    listCallsMock.mockResolvedValueOnce({
      calls: secondPageItems,
      total: 47,
      limit: 20,
      offset: 20,
      has_more: true,
    });
    const { default: Page } = await import("@/app/(app)/dashboard/calls/page");

    render(
      await Page({
        searchParams: Promise.resolve({
          q: " opening ",
          status: "in_progress",
          range: "7d",
          page: "2",
        }),
      }),
    );

    expect(listCallsMock).toHaveBeenCalledWith({
      limit: 20,
      offset: 20,
      query: "opening",
      status: "in_progress",
      range: "7d",
    });
    expect(screen.getByLabelText("Search calls")).toHaveValue("opening");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Calls" })).toBeInTheDocument();
    expect(document.querySelector('[data-slot="page-intro"]')).not.toBeNull();
    const search = screen.getByRole("search");
    expect(search).toHaveAttribute("method", "get");
    expect(search).toHaveAttribute("action", "/dashboard/calls");
    const searchInput = within(search).getByRole("searchbox", { name: "Search calls" });
    expect(searchInput).toHaveAttribute("name", "q");
    expect(searchInput).toHaveValue("opening");
    expect(within(search).getByRole("combobox", { name: "Filter by status" })).toHaveValue("in_progress");
    expect(within(search).getByRole("combobox", { name: "Filter by date" })).toHaveValue("7d");
    expect(within(search).getByRole("button", { name: "Apply filters" })).toHaveClass("min-h-11");
    expect(screen.getByText("47 matching calls")).toHaveAttribute("role", "status");
    expect(screen.getByText("Showing 21–40 of 47 calls")).toBeInTheDocument();
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Previous" })).toHaveAttribute(
      "href",
      "/dashboard/calls?q=opening&status=in_progress&range=7d",
    );
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "/dashboard/calls?q=opening&status=in_progress&range=7d&page=3",
    );
    expect(screen.getByRole("link", { name: "Clear filters" })).toHaveAttribute("href", "/dashboard/calls");
    expect(document.querySelector('form input[name="page"]')).not.toBeInTheDocument();
    expect(screen.getByText(/Caller asked about opening hours/i)).toBeInTheDocument();
    expect(screen.getByText(/Check opening hours/i)).toBeInTheDocument();
    expect(screen.getByText("Follow-up needed", { exact: true })).toBeInTheDocument();
    const callLedger = screen.getByRole("table", { name: "Call history" });
    const firstRow = callLedger.querySelector<HTMLElement>('[data-slot="call-history-row"]');
    expect(firstRow).not.toBeNull();
    if (!firstRow) {
      throw new Error("The first call ledger row is missing");
    }
    for (const label of ["Caller", "Intent", "Follow-up", "Status", "Duration", "Started", "Recording"]) {
      expect(within(firstRow).getByText(label)).toBeInTheDocument();
    }
    expect(firstRow).toHaveClass("rounded-xl", "md:table-row");
    expect(
      within(firstRow).getByRole("link", {
        name: /Open call from \+33123456789, status Completed, intent Check opening hours, Follow-up needed, duration 1m, started Mar 28, 11:00/i,
      }),
    ).toHaveAttribute("href", "/dashboard/calls/call-1");
    expect(firstRow.querySelectorAll("a")).toHaveLength(1);
    expect(firstRow.querySelector("a")).toHaveClass("min-h-11");
    expect(screen.queryByRole("button", { name: /tag|note|export|refresh/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /tag|note|export|refresh/i })).not.toBeInTheDocument();
  });

  it("distinguishes no history from no search matches", async () => {
    listCallsMock
      .mockResolvedValueOnce({
        calls: [],
        total: 0,
        limit: 20,
        offset: 0,
        has_more: false,
      })
      .mockResolvedValueOnce({
        calls: [],
        total: 0,
        limit: 20,
        offset: 0,
        has_more: false,
      });
    const { default: Page } = await import("@/app/(app)/dashboard/calls/page");

    const historyView = render(await Page({ searchParams: Promise.resolve({}) }));
    expect(screen.getByText("No calls yet")).toBeInTheDocument();
    historyView.unmount();

    render(
      await Page({
        searchParams: Promise.resolve({ status: "failed", range: "30d", page: "1" }),
      }),
    );
    expect(screen.getByText("No calls match your filters")).toBeInTheDocument();
    expect(screen.getByText(/Try a different search term, status, or date range/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Search calls")).toHaveValue("");
    expect(screen.getByRole("link", { name: "Clear filters" })).toHaveAttribute("href", "/dashboard/calls");
  });

  it("keeps privacy-safe caller, intent, and follow-up states explicit in ledger rows", async () => {
    listCallsMock.mockResolvedValueOnce({
      calls: [
        {
          ...callItem,
          id: "call-private",
          caller_number: null,
          summary_status: "processing",
          summary_text: null,
          caller_intent: null,
          follow_up_required: null,
        },
        {
          ...callItem,
          id: "call-unavailable",
          summary_status: "unavailable",
          summary_text: null,
          caller_intent: null,
          follow_up_required: false,
        },
      ],
      total: 2,
      limit: 20,
      offset: 0,
      has_more: false,
    });
    const { default: Page } = await import("@/app/(app)/dashboard/calls/page");

    render(await Page({ searchParams: Promise.resolve({}) }));

    expect(screen.getByText("Private caller")).toBeInTheDocument();
    expect(screen.getByText("Summary processing")).toBeInTheDocument();
    expect(screen.getByText("Intent unavailable")).toBeInTheDocument();
    expect(screen.getByText("Follow-up unknown")).toBeInTheDocument();
    expect(screen.getByText("No follow-up needed")).toBeInTheDocument();
  });

  it("shows disabled first and final page controls", async () => {
    listCallsMock
      .mockResolvedValueOnce({
        calls: [callItem],
        total: 21,
        limit: 20,
        offset: 0,
        has_more: true,
      })
      .mockResolvedValueOnce({
        calls: [callItem],
        total: 21,
        limit: 20,
        offset: 20,
        has_more: false,
      });
    const { default: Page } = await import("@/app/(app)/dashboard/calls/page");

    const first = render(await Page({ searchParams: Promise.resolve({ page: "1" }) }));
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Next" })).toBeInTheDocument();
    first.unmount();

    render(await Page({ searchParams: Promise.resolve({ page: "2" }) }));
    expect(screen.getByRole("link", { name: "Previous" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("redirects an out-of-range page to the final matching page", async () => {
    listCallsMock.mockResolvedValueOnce({
      calls: [],
      total: 21,
      limit: 20,
      offset: 80,
      has_more: false,
    });
    const { default: Page } = await import("@/app/(app)/dashboard/calls/page");

    await expect(
      Page({
        searchParams: Promise.resolve({
          q: "opening",
          status: "completed",
          range: "30d",
          page: "5",
        }),
      }),
    ).rejects.toThrow("NEXT_REDIRECT");
    expect(redirectMock).toHaveBeenCalledWith("/dashboard/calls?q=opening&status=completed&range=30d&page=2");
  });

  it("redirects a zero-result later page to filtered page one", async () => {
    listCallsMock.mockResolvedValueOnce({
      calls: [],
      total: 0,
      limit: 20,
      offset: 20,
      has_more: false,
    });
    const { default: Page } = await import("@/app/(app)/dashboard/calls/page");

    await expect(
      Page({
        searchParams: Promise.resolve({ q: "opening", page: "2" }),
      }),
    ).rejects.toThrow("NEXT_REDIRECT");
    expect(redirectMock).toHaveBeenCalledWith("/dashboard/calls?q=opening");
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
        {
          speaker: "ASSISTANT",
          text: "We are open on weekdays from nine.",
          sequence_number: 2,
          created_at: "2026-03-28T10:00:20Z",
        },
      ],
    });

    const { default: DetailPage } = await import("@/app/(app)/dashboard/calls/[callId]/page");
    render(await DetailPage({ params: Promise.resolve({ callId: "call-1" }) }));

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "+33123456789" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to calls" })).toHaveAttribute("href", "/dashboard/calls");
    expect(document.querySelector('[data-slot="page-intro"]')).not.toBeNull();
    const callStatus = screen.getByRole("region", { name: "Call status: Completed" });
    expect(within(callStatus).getByText("Completed")).toBeInTheDocument();
    expect(callStatus.querySelector("svg")).not.toBeNull();
    const summary = screen.getByRole("region", { name: "Generated summary" });
    const recording = screen.getByRole("region", { name: "Recording" });
    const transcript = screen.getByRole("region", { name: "Full transcript" });
    const metadata = screen.getByRole("region", { name: "Metadata" });
    for (const section of [summary, recording, transcript, metadata]) {
      expect(section).toHaveAttribute("data-slot", "product-surface");
    }
    expect(screen.getByText(/What are your opening hours\?/i)).toBeInTheDocument();
    const transcriptLines = within(transcript).getAllByRole("listitem");
    expect(transcriptLines).toHaveLength(2);
    expect(transcriptLines[0]).toHaveTextContent("CALLER");
    expect(transcriptLines[0]).toHaveTextContent("What are your opening hours?");
    expect(transcriptLines[1]).toHaveTextContent("ASSISTANT");
    expect(transcriptLines[1]).toHaveTextContent("We are open on weekdays from nine.");
    const transcriptSearch = within(transcript).getByRole("searchbox", { name: "Search transcript" });
    expect(transcriptSearch).toHaveClass("min-h-11");
    fireEvent.change(transcriptSearch, { target: { value: "weekdays" } });
    expect(within(transcript).getAllByRole("listitem")).toHaveLength(1);
    expect(within(transcript).queryByText("What are your opening hours?")).not.toBeInTheDocument();
    expect(within(transcript).getByText("weekdays")).toBeInTheDocument();
    fireEvent.change(transcriptSearch, { target: { value: "unmatched phrase" } });
    expect(within(transcript).getByText(/No transcript lines match/i)).toBeInTheDocument();
    expect(screen.getByText(/Check opening hours/i)).toBeInTheDocument();
    expect(screen.getByText(/Send weekday hours/i)).toBeInTheDocument();
    expect(screen.getByText(/Recording unavailable/i)).toBeInTheDocument();
    expect(within(metadata).getByText("Mar 28, 11:00")).toBeInTheDocument();
    expect(within(metadata).getByText("Mar 28, 11:01")).toBeInTheDocument();
    expect(within(metadata).getByText("1m")).toBeInTheDocument();
    expect(within(metadata).getByText("1 min")).toBeInTheDocument();
    expect(within(metadata).getByText("call-1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove call" })).toBeInTheDocument();
  });

  it.each([
    {
      summary_status: "processing",
      expected: "Summary is still processing.",
    },
    {
      summary_status: "unavailable",
      expected: "Summary unavailable.",
    },
    {
      summary_status: "ready",
      expected: "No summary was provided.",
    },
  ] as const)("renders the stored $summary_status summary state without inventing content", async ({
    summary_status,
    expected,
  }) => {
    getCallDetailMock.mockResolvedValueOnce({
      id: `call-${summary_status}`,
      status: "connected",
      caller_number: null,
      started_at: "2026-03-28T10:00:00Z",
      ended_at: null,
      duration_seconds: null,
      minutes_charged: null,
      summary_text: null,
      summary_status,
      caller_intent: null,
      action_items: null,
      sentiment: null,
      follow_up_required: null,
      recording_url: null,
      transcript: [],
    });

    const { default: DetailPage } = await import("@/app/(app)/dashboard/calls/[callId]/page");
    render(await DetailPage({ params: Promise.resolve({ callId: `call-${summary_status}` }) }));

    expect(screen.getByRole("heading", { level: 1, name: "Private caller" })).toBeInTheDocument();
    expect(screen.getByText(expected)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Call status: Connected" })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Metadata" })).getByText("Not available")).toBeInTheDocument();
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

  it("maps only backend 404 call detail failures to the not-found boundary", async () => {
    const { BackendApiError } = await import("@/lib/api/backend-client");
    getCallDetailMock.mockRejectedValueOnce(new BackendApiError("missing", 404));

    const { default: DetailPage } = await import("@/app/(app)/dashboard/calls/[callId]/page");
    await expect(DetailPage({ params: Promise.resolve({ callId: "missing" }) })).rejects.toThrow("missing");

    expect(notFoundMock).toHaveBeenCalledTimes(1);
  });

  it("propagates non-404 call detail failures", async () => {
    const { BackendApiError } = await import("@/lib/api/backend-client");
    getCallDetailMock.mockRejectedValueOnce(new BackendApiError("upstream unavailable", 503));

    const { default: DetailPage } = await import("@/app/(app)/dashboard/calls/[callId]/page");
    await expect(DetailPage({ params: Promise.resolve({ callId: "call-1" }) })).rejects.toMatchObject({
      message: "upstream unavailable",
      status: 503,
    });

    expect(notFoundMock).not.toHaveBeenCalled();
  });
});
