import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VerificationCountdown } from "@/app/(activation)/activate/_components/launch/verification-countdown";

const { refreshMock } = vi.hoisted(() => ({ refreshMock: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: refreshMock }) }));

describe("verification countdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-17T09:55:00Z"));
    refreshMock.mockReset();
  });

  afterEach(() => vi.useRealTimers());

  it("uses a fixed server offset for the verification deadline", () => {
    render(<VerificationCountdown evaluatedAt="2026-07-17T10:00:00Z" expiresAt="2026-07-17T10:10:00Z" />);

    expect(screen.getByRole("timer")).toHaveTextContent("10:00");
    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByRole("timer")).toHaveTextContent("09:59");
  });

  it("refreshes once at zero and never marks expiry locally", () => {
    render(<VerificationCountdown evaluatedAt="2026-07-17T10:00:00Z" expiresAt="2026-07-17T10:00:02Z" />);

    act(() => vi.advanceTimersByTime(5_000));
    expect(screen.getByRole("timer")).toHaveTextContent("00:00");
    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/expired/i)).not.toBeInTheDocument();
  });
});
