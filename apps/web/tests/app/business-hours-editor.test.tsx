import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  BusinessHoursEditor,
  createDefaultBusinessHours,
} from "@/app/(activation)/activate/_components/profile/business-hours-editor";
import type { BusinessHours } from "@/lib/types/activation";

function hoursWithOneMondayInterval(): BusinessHours {
  return {
    monday: { closed: false, intervals: [{ start: "09:00", end: "12:00" }] },
    tuesday: { closed: true, intervals: [] },
    wednesday: { closed: true, intervals: [] },
    thursday: { closed: true, intervals: [] },
    friday: { closed: true, intervals: [] },
    saturday: { closed: true, intervals: [] },
    sunday: { closed: true, intervals: [] },
  };
}

describe("business hours editor", () => {
  it("names its controls for predictable browser and assistive-technology behavior", () => {
    render(<BusinessHoursEditor maxIntervalsPerDay={2} onChange={vi.fn()} value={hoursWithOneMondayInterval()} />);

    expect(screen.getByRole("checkbox", { name: /Monday closed/i })).toHaveAttribute("name", "monday_closed");
    expect(screen.getByLabelText(/Monday start 1/i)).toHaveAttribute("name", "monday_interval_0_start");
    expect(screen.getByLabelText(/Monday end 1/i)).toHaveAttribute("name", "monday_interval_0_end");
  });

  it("supports two non-overlapping intervals and no third interval", () => {
    render(<BusinessHoursEditor maxIntervalsPerDay={2} onChange={vi.fn()} value={hoursWithOneMondayInterval()} />);

    fireEvent.click(screen.getByRole("button", { name: /Add interval for Monday/i }));

    expect(screen.getAllByLabelText(/Monday start/i)).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Add interval for Monday/i })).not.toBeInTheDocument();
  });

  it("adds a blank second interval instead of overlapping the default full-day interval", () => {
    render(<BusinessHoursEditor maxIntervalsPerDay={2} onChange={vi.fn()} value={createDefaultBusinessHours()} />);

    fireEvent.click(screen.getByRole("button", { name: /Add interval for Monday/i }));

    expect(screen.getAllByLabelText(/Monday start/i)[1]).toHaveValue("");
    expect(screen.getAllByLabelText(/Monday end/i)[1]).toHaveValue("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("provides an accessible action to remove an added interval", () => {
    const onChange = vi.fn();
    render(<BusinessHoursEditor maxIntervalsPerDay={2} onChange={onChange} value={hoursWithOneMondayInterval()} />);
    fireEvent.click(screen.getByRole("button", { name: /Add interval for Monday/i }));

    fireEvent.click(screen.getByRole("button", { name: /Remove Monday interval 2/i }));

    expect(screen.getAllByLabelText(/Monday start/i)).toHaveLength(1);
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ monday: { closed: false, intervals: [{ start: "09:00", end: "12:00" }] } }),
    );
  });

  it("keeps a closed day free of intervals", () => {
    const onChange = vi.fn();
    render(<BusinessHoursEditor maxIntervalsPerDay={2} onChange={onChange} value={hoursWithOneMondayInterval()} />);

    fireEvent.click(screen.getByRole("checkbox", { name: /Monday closed/i }));

    expect(screen.queryByLabelText(/Monday start/i)).not.toBeInTheDocument();
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ monday: { closed: true, intervals: [] } }));
  });

  it("reports overlapping intervals and focuses the conflicting start", () => {
    render(<BusinessHoursEditor maxIntervalsPerDay={2} onChange={vi.fn()} value={hoursWithOneMondayInterval()} />);
    fireEvent.click(screen.getByRole("button", { name: /Add interval for Monday/i }));

    const starts = screen.getAllByLabelText(/Monday start/i);
    const ends = screen.getAllByLabelText(/Monday end/i);
    fireEvent.change(starts[1], { target: { value: "11:00" } });
    fireEvent.change(ends[1], { target: { value: "13:00" } });
    fireEvent.blur(ends[1]);

    expect(screen.getByRole("alert")).toHaveTextContent(/overlap/i);
    expect(starts[1]).toHaveFocus();
  });

  it("rejects an interval that does not end after it starts", () => {
    render(<BusinessHoursEditor maxIntervalsPerDay={2} onChange={vi.fn()} value={hoursWithOneMondayInterval()} />);
    const start = screen.getByLabelText(/Monday start 1/i);
    const end = screen.getByLabelText(/Monday end 1/i);

    fireEvent.change(start, { target: { value: "12:00" } });
    fireEvent.change(end, { target: { value: "11:00" } });
    fireEvent.blur(end);

    expect(screen.getByRole("alert")).toHaveTextContent(/end after they start/i);
    expect(start).toHaveFocus();
  });

  it("does not steal focus while the user corrects an externally invalid interval", () => {
    const hours = hoursWithOneMondayInterval();
    hours.monday.intervals.push({ start: "11:00", end: "13:00" });
    render(<BusinessHoursEditor invalid maxIntervalsPerDay={2} onChange={vi.fn()} value={hours} />);

    expect(screen.getByLabelText(/Monday start 2/i)).toHaveFocus();
    const conflictingEnd = screen.getByLabelText(/Monday end 2/i);
    act(() => conflictingEnd.focus());
    fireEvent.change(conflictingEnd, { target: { value: "14:00" } });

    expect(conflictingEnd).toHaveFocus();
  });
});
