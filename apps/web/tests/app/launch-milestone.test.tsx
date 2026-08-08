import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LaunchMilestone } from "@/app/(activation)/activate/_components/launch/launch-milestone";

import { activationSnapshot } from "./activation-snapshot-fixture";

const { simulateMock, goLiveMock, pushMock, refreshMock } = vi.hoisted(() => ({
  simulateMock: vi.fn(),
  goLiveMock: vi.fn(),
  pushMock: vi.fn(),
  refreshMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock, refresh: refreshMock }) }));
vi.mock("@/app/(activation)/activate/actions", () => ({
  simulateDevelopmentForwardedCallAction: simulateMock,
  goLiveAction: goLiveMock,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

describe("launch milestone", () => {
  beforeEach(() => {
    simulateMock.mockReset().mockResolvedValue({
      status: "success",
      data: activationSnapshot({ stage: "ready_to_activate" }),
      message: "Local forwarded call verified.",
    });
    goLiveMock.mockReset().mockResolvedValue({
      status: "success",
      data: activationSnapshot({ stage: "activating" }),
      message: "Go-live started.",
    });
    pushMock.mockReset();
    refreshMock.mockReset();
  });

  it("shows the server-timed call test and local simulator only when enabled", () => {
    const windowSnapshot = activationSnapshot({
      stage: "verification_window_open",
      activation: {
        ...activationSnapshot().activation,
        verification_window_started_at: "2026-07-17T10:00:00Z",
        verification_window_expires_at: "2026-07-17T10:10:00Z",
        verification_status: "open",
      },
    });
    const view = render(<LaunchMilestone localVerification={false} snapshot={windowSnapshot} />);

    expect(screen.getByText(/call your existing business number from another phone/i)).toBeInTheDocument();
    expect(screen.getByRole("timer")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Simulate forwarded call/i })).not.toBeInTheDocument();
    view.rerender(<LaunchMilestone localVerification snapshot={windowSnapshot} />);
    expect(screen.getByText(/Local development/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Simulate forwarded call/i })).toBeInTheDocument();
  });

  it("queues one local simulation and keeps a safe failure visible", async () => {
    simulateMock.mockResolvedValueOnce({
      status: "error",
      code: "request_failed",
      message: "We couldn't complete this step. Refresh and try again.",
    });
    const windowSnapshot = activationSnapshot({
      stage: "verification_window_open",
      activation: {
        ...activationSnapshot().activation,
        verification_window_expires_at: "2026-07-17T10:10:00Z",
        verification_status: "open",
      },
    });
    render(<LaunchMilestone localVerification snapshot={windowSnapshot} />);
    const simulate = screen.getByRole("button", { name: /Simulate forwarded call/i });
    fireEvent.click(simulate);
    fireEvent.click(simulate);

    await waitFor(() => expect(simulateMock).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't complete this step/i);
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("returns a successful local simulation to the canonical launch milestone", async () => {
    const windowSnapshot = activationSnapshot({
      stage: "verification_window_open",
      activation: {
        ...activationSnapshot().activation,
        verification_window_expires_at: "2026-07-17T10:10:00Z",
        verification_status: "open",
      },
    });
    render(<LaunchMilestone localVerification snapshot={windowSnapshot} />);

    const simulate = screen.getByRole("button", { name: /Simulate forwarded call/i });
    fireEvent.click(simulate);

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/activate?milestone=launch"));
    expect(simulate).toBeEnabled();
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it("separates verified success from launch and ignores expected projection blockers", () => {
    render(
      <LaunchMilestone
        localVerification={false}
        snapshot={activationSnapshot({
          stage: "ready_to_activate",
          completed_milestones: [...activationSnapshot().completed_milestones, "forwarding_verified"],
          activation: {
            ...activationSnapshot().activation,
            verification_status: "succeeded",
            forwarding_verified_at: "2026-07-17T10:02:00Z",
          },
          runtime_readiness: {
            ...activationSnapshot().runtime_readiness,
            blockers: ["agent_disabled", "phone_inactive", "phone_projection_inactive", "go_live_not_approved"],
          },
        })}
      />,
    );

    expect(screen.getByText(/Forwarding verified/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Go live$/i })).toBeEnabled();
  });

  it.each([
    ["subscription_missing", "/activate?milestone=number"],
    ["plan_unsupported", "/dashboard/billing"],
    ["subscription_status_ineligible", "/dashboard/billing"],
    ["subscription_period_missing", "/dashboard/billing"],
    ["subscription_period_inactive", "/dashboard/billing"],
    ["minutes_exhausted", "/dashboard/billing"],
    ["phone_missing", "/activate?milestone=number"],
    ["phone_provider_id_missing", "/activate?milestone=number"],
    ["business_profile_incomplete", "/activate?milestone=business"],
    ["agent_config_missing", "/activate?milestone=receptionist"],
    ["agent_setup_incomplete", "/activate?milestone=receptionist"],
    ["agent_content_invalid", "/activate?milestone=receptionist"],
    ["profile_projection_stale", "/activate?milestone=receptionist"],
    ["forwarding_not_verified", "/activate?milestone=forwarding"],
  ])("maps readiness blocker %s to its corrective destination", (blocker, href) => {
    render(
      <LaunchMilestone
        localVerification={false}
        snapshot={activationSnapshot({
          stage: "ready_to_activate",
          completed_milestones: [...activationSnapshot().completed_milestones, "forwarding_verified"],
          activation: {
            ...activationSnapshot().activation,
            verification_status: "succeeded",
            forwarding_verified_at: "2026-07-17T10:02:00Z",
          },
          runtime_readiness: { ...activationSnapshot().runtime_readiness, blockers: [blocker] },
        })}
      />,
    );

    expect(screen.getByRole("link", { name: new RegExp(blocker.replaceAll("_", " "), "i") })).toHaveAttribute(
      "href",
      href,
    );
    expect(screen.queryByRole("button", { name: /^Go live$/i })).not.toBeInTheDocument();
  });

  it.each([
    "user_inactive",
    "unexpected_runtime_blocker",
  ])("renders non-correctable blocker %s as explicit recovery guidance, not a self-loop link", (blocker) => {
    render(
      <LaunchMilestone
        localVerification={false}
        snapshot={activationSnapshot({
          stage: "ready_to_activate",
          completed_milestones: [...activationSnapshot().completed_milestones, "forwarding_verified"],
          runtime_readiness: { ...activationSnapshot().runtime_readiness, blockers: [blocker] },
        })}
      />,
    );

    expect(screen.getByText(new RegExp(blocker.replaceAll("_", " "), "i"))).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: new RegExp(blocker.replaceAll("_", " "), "i") })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Go live$/i })).not.toBeInTheDocument();
  });

  it("queues one explicit go-live command while pending", async () => {
    const pending = deferred<{ status: "success"; data: ReturnType<typeof activationSnapshot>; message: string }>();
    goLiveMock.mockReturnValue(pending.promise);
    render(
      <LaunchMilestone
        localVerification={false}
        snapshot={activationSnapshot({
          stage: "ready_to_activate",
          completed_milestones: [...activationSnapshot().completed_milestones, "forwarding_verified"],
          activation: {
            ...activationSnapshot().activation,
            verification_status: "succeeded",
            forwarding_verified_at: "2026-07-17T10:02:00Z",
          },
          runtime_readiness: { ...activationSnapshot().runtime_readiness, blockers: ["go_live_not_approved"] },
        })}
      />,
    );
    const button = screen.getByRole("button", { name: /^Go live$/i });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(goLiveMock).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    await act(async () =>
      pending.resolve({
        status: "success",
        data: activationSnapshot({ stage: "activating" }),
        message: "Go-live started.",
      }),
    );
    expect(refreshMock).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
  });

  it("renders activating and safe runtime failure states without claiming active", () => {
    const view = render(
      <LaunchMilestone localVerification={false} snapshot={activationSnapshot({ stage: "activating" })} />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(/Bringing Opevo live/i);
    expect(screen.queryByText(/^Active$/i)).not.toBeInTheDocument();
    view.rerender(
      <LaunchMilestone
        localVerification={false}
        snapshot={activationSnapshot({
          stage: "ready_to_activate",
          completed_milestones: [...activationSnapshot().completed_milestones, "forwarding_verified"],
          activation: {
            ...activationSnapshot().activation,
            verification_status: "succeeded",
            forwarding_verified_at: "2026-07-17T10:02:00Z",
            last_failure_code: "routing_provider_terminal",
          },
        })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/couldn't bring Opevo live/i);
    expect(screen.getByText(/Reference: routing_provider_terminal/i)).toBeInTheDocument();
  });

  it("fails closed for an unproven runtime-paused snapshot", () => {
    render(
      <LaunchMilestone
        localVerification={false}
        snapshot={activationSnapshot({
          stage: "runtime_paused",
          runtime_readiness: {
            ...activationSnapshot().runtime_readiness,
            blockers: ["agent_disabled", "phone_inactive", "go_live_not_activated"],
          },
        })}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/Opevo is not active/i);
    expect(screen.queryByRole("button", { name: /^Go live$/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/^Active$/i)).not.toBeInTheDocument();
  });

  it("does not trust historical success fields without the canonical forwarding milestone", () => {
    render(
      <LaunchMilestone
        localVerification={false}
        snapshot={activationSnapshot({
          stage: "ready_to_activate",
          activation: {
            ...activationSnapshot().activation,
            verification_status: "succeeded",
            forwarding_verified_at: "2026-07-17T10:02:00Z",
          },
          runtime_readiness: {
            ...activationSnapshot().runtime_readiness,
            blockers: ["agent_disabled", "phone_inactive", "go_live_not_approved"],
          },
        })}
      />,
    );

    expect(screen.queryByText(/Forwarding verified/i)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /forwarding not verified/i })).toHaveAttribute(
      "href",
      "/activate?milestone=forwarding",
    );
    expect(screen.queryByRole("button", { name: /^Go live$/i })).not.toBeInTheDocument();
  });
});
