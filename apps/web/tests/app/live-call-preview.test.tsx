import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("live call preview", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps the complete interactive experience visibly local-only", async () => {
    const { default: Page, metadata } = await import("@/app/(app)/dashboard/live-call/page");

    expect(metadata.title).toBe("Live call preview — Opevo");
    render(<Page />);

    expect(screen.getByRole("heading", { level: 1, name: "Live call" })).toBeInTheDocument();
    expect(screen.getAllByText("Preview").length).toBeGreaterThan(0);
    expect(screen.getByRole("note")).toHaveTextContent(/Nothing here places, answers, or ends a real call/i);

    const overview = screen.getByRole("region", { name: "Preview call overview" });
    expect(within(overview).getByText("Sophie Bernard")).toBeInTheDocument();
    expect(within(overview).getByText("+33 6 12 34 56 78")).toBeInTheDocument();
    expect(within(overview).getByText("Active")).toBeInTheDocument();
    expect(within(overview).getByText("01:42")).toHaveAttribute("aria-live", "polite");

    act(() => vi.advanceTimersByTime(1_000));
    expect(within(overview).getByText("01:43")).toBeInTheDocument();

    const transcript = screen.getByRole("region", { name: "Live transcript" });
    expect(within(transcript).getByText(/Atelier Marceau/i)).toBeInTheDocument();
    expect(within(transcript).getAllByRole("listitem").length).toBeGreaterThanOrEqual(2);

    const stateControls = within(transcript).getByRole("group", { name: "Preview state controls" });
    fireEvent.click(within(stateControls).getByRole("button", { name: "Connecting" }));
    expect(within(overview).getByText("Connecting")).toBeInTheDocument();
    expect(within(transcript).getByText(/Connecting to \+33 6 12 34 56 78/i)).toBeInTheDocument();
    fireEvent.click(within(stateControls).getByRole("button", { name: "Active" }));

    const callerDetails = screen.getByRole("region", { name: "Caller information" });
    expect(within(callerDetails).getByText("Request a showroom appointment")).toBeInTheDocument();
    expect(within(callerDetails).getByText("Camille")).toBeInTheDocument();

    const notes = screen.getByRole("textbox", { name: "Preview call notes" });
    expect(notes).toHaveAttribute("name", "preview-call-notes");
    expect(notes).toHaveAttribute("autocomplete", "off");
    const save = screen.getByRole("button", { name: "Save preview note" });
    expect(save).toBeDisabled();
    fireEvent.change(notes, { target: { value: "Confirm the Thursday afternoon appointment." } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(screen.getByRole("status", { name: "Preview note status" })).toHaveTextContent("Saved in this preview only");

    const endPreview = screen.getByRole("button", { name: "End preview" });
    fireEvent.click(endPreview);
    expect(endPreview).toBeDisabled();
    expect(within(overview).getByText("Completed")).toBeInTheDocument();
    expect(within(transcript).getByText("Preview completed")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Restart preview" }));
    expect(within(overview).getByText("Active")).toBeInTheDocument();
    expect(within(overview).getByText("01:42")).toBeInTheDocument();
    expect(notes).toHaveValue("");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
