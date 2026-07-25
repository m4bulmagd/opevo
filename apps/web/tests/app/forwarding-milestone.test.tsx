import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ForwardingMilestone } from "@/app/(activation)/activate/_components/forwarding/forwarding-milestone";

import { activationSnapshot, forwardingGuide } from "./activation-snapshot-fixture";

const { openWindowMock, refreshMock, writeTextMock } = vi.hoisted(() => ({
  openWindowMock: vi.fn(),
  refreshMock: vi.fn(),
  writeTextMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: refreshMock }) }));
vi.mock("@/app/(activation)/activate/actions", () => ({ openVerificationWindowAction: openWindowMock }));

describe("forwarding milestone", () => {
  beforeEach(() => {
    openWindowMock.mockReset().mockResolvedValue({
      status: "success",
      data: activationSnapshot({ stage: "verification_window_open" }),
      message: "Forwarding verification started.",
    });
    refreshMock.mockReset();
    writeTextMock.mockReset().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: writeTextMock } });
    Object.defineProperty(window, "matchMedia", { configurable: true, value: undefined });
  });

  it("renders one assigned number and only the three conditional forwarding conditions", () => {
    render(<ForwardingMilestone snapshot={activationSnapshot()} />);

    expect(screen.getAllByText("+33 1 87 65 43 21")).toHaveLength(1);
    expect(screen.getAllByText(/When unanswered/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/When busy/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/When unreachable/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/unconditional/i)).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Copy dial code/i })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Copy disable code/i })).not.toBeInTheDocument();
  });

  it("uses accordion controls only on small screens", () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    });

    render(<ForwardingMilestone snapshot={activationSnapshot()} />);

    expect(screen.getByRole("button", { name: "When unanswered" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "When busy" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "When unreachable" })).toHaveAttribute("aria-expanded", "false");
  });

  it("copies only supplied dial codes and announces success or failure", async () => {
    render(<ForwardingMilestone snapshot={activationSnapshot()} />);

    fireEvent.click(screen.getAllByRole("button", { name: /Copy dial code/i })[0]);
    expect(await screen.findByRole("status")).toHaveTextContent(/Copied/i);
    expect(writeTextMock).toHaveBeenCalledWith("*61*0187654321#");

    writeTextMock.mockRejectedValueOnce(new Error("clipboard blocked"));
    fireEvent.click(screen.getAllByRole("button", { name: /Copy dial code/i })[1]);
    expect(await screen.findByRole("status")).toHaveTextContent(/Copy failed/i);
  });

  it("never guesses codes or source links for Other carrier guidance", () => {
    const guide = forwardingGuide();
    guide.carrier = "other";
    guide.warning = "Contact your carrier and ask for conditional forwarding.";
    guide.steps = guide.steps.map((step) => ({ ...step, dial_code: null, disable_code: null, source_url: null }));
    render(<ForwardingMilestone snapshot={activationSnapshot({ forwarding: guide })} />);

    expect(screen.getByText(/Contact your carrier/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Copy dial code/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Official carrier source/i })).not.toBeInTheDocument();
  });

  it("links the official source only when supplied and starts one ten-minute test", async () => {
    render(<ForwardingMilestone snapshot={activationSnapshot()} />);

    expect(screen.getAllByRole("link", { name: /Official carrier source/i }).length).toBeGreaterThan(0);
    expect(screen.getByText(/call your existing business number from another phone/i)).toBeInTheDocument();
    const start = screen.getByRole("button", { name: /Start 10-minute test/i });
    fireEvent.click(start);
    fireEvent.click(start);

    await waitFor(() => expect(openWindowMock).toHaveBeenCalledTimes(1));
    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(start).toBeDisabled();
    fireEvent.click(start);
    expect(openWindowMock).toHaveBeenCalledTimes(1);
  });

  it("keeps a safe window-opening failure visible", async () => {
    openWindowMock.mockResolvedValueOnce({
      status: "error",
      code: "request_failed",
      message: "We couldn't complete this step. Refresh and try again.",
    });
    render(<ForwardingMilestone snapshot={activationSnapshot()} />);

    fireEvent.click(screen.getByRole("button", { name: /Start 10-minute test/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't complete this step/i);
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("keeps a completed forwarding milestone read-only on revisit", () => {
    const base = activationSnapshot();
    render(
      <ForwardingMilestone
        snapshot={activationSnapshot({
          stage: "ready_to_activate",
          completed_milestones: [...base.completed_milestones, "forwarding_verified"],
          activation: {
            ...base.activation,
            verification_status: "succeeded",
            forwarding_verified_at: "2026-07-17T10:02:00Z",
          },
        })}
      />,
    );

    expect(screen.getByText(/Forwarding already verified/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start 10-minute test/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Continue to final checks/i })).toHaveAttribute(
      "href",
      "/activate?milestone=launch",
    );
  });

  it("requires a new test when historical success is no longer canonically complete", () => {
    const base = activationSnapshot();
    render(
      <ForwardingMilestone
        snapshot={activationSnapshot({
          stage: "forwarding_required",
          activation: {
            ...base.activation,
            verification_status: "succeeded",
            forwarding_verified_at: "2026-07-17T10:02:00Z",
          },
        })}
      />,
    );

    expect(screen.queryByText(/Forwarding already verified/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start 10-minute test/i })).toBeEnabled();
  });
});
