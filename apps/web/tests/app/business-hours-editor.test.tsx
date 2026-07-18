import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BusinessHoursEditor } from "@/app/(activation)/activate/_components/profile/business-hours-editor";
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
  it("supports two non-overlapping intervals and no third interval", () => {
    render(<BusinessHoursEditor maxIntervalsPerDay={2} onChange={vi.fn()} value={hoursWithOneMondayInterval()} />);

    fireEvent.click(screen.getByRole("button", { name: /Add afternoon hours for Monday/i }));

    expect(screen.getAllByLabelText(/Monday start/i)).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Add afternoon hours for Monday/i })).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: /Add afternoon hours for Monday/i }));

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
});
