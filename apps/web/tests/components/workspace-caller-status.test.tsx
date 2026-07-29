import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WorkspaceCallerStatus } from "@/components/workspace/workspace-caller-status";

describe("WorkspaceCallerStatus", () => {
  it("renders a stable ready state when no call is active", () => {
    render(<WorkspaceCallerStatus agentName="Ava" caller={null} />);

    expect(screen.getByText("No active call")).toBeVisible();
    expect(screen.getByText("Ava is ready")).toBeVisible();
    expect(screen.getByTestId("caller-status-icon")).toBeInTheDocument();
  });

  it("prefers a normalized matched contact name and derives initials", () => {
    render(
      <WorkspaceCallerStatus
        agentName="Ava"
        caller={{ contactName: "  Sophie Bernard  ", phoneNumber: "+33612345678" }}
      />,
    );

    expect(screen.getByText("Sophie Bernard")).toBeVisible();
    expect(screen.getByText("SB")).toBeVisible();
    expect(screen.getByText("Ava is answering this call")).toBeVisible();
  });

  it("falls back to the caller number without inventing initials", () => {
    render(<WorkspaceCallerStatus agentName="Ava" caller={{ contactName: null, phoneNumber: "+33612345678" }} />);

    expect(screen.getByText("+33612345678")).toBeVisible();
    expect(screen.getByTestId("caller-status-icon")).toBeInTheDocument();
  });

  it("uses Unknown caller when an active call has no usable identity", () => {
    render(<WorkspaceCallerStatus agentName="Ava" caller={{ contactName: "  ", phoneNumber: null }} />);

    expect(screen.getByText("Unknown caller")).toBeVisible();
    expect(screen.getByText("Ava is answering this call")).toBeVisible();
  });
});
