import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AssignedNumberCard } from "@/components/account/assigned-number-card";

import { forwardingGuide } from "../app/activation-snapshot-fixture";

const { writeTextMock } = vi.hoisted(() => ({
  writeTextMock: vi.fn(),
}));

describe("assigned number card", () => {
  beforeEach(() => {
    writeTextMock.mockReset().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: writeTextMock },
    });
  });

  it("formats a real number and links to the forwarding milestone", () => {
    render(<AssignedNumberCard forwarding={forwardingGuide()} number="+33612345678" />);

    expect(screen.getByRole("heading", { level: 2, name: "Assigned number" })).toBeVisible();
    expect(screen.getByText("06 12 34 56 78")).toBeVisible();
    expect(screen.getByRole("button", { name: "Copy assigned number" })).toHaveClass("min-h-11", "min-w-11");
    const forwardingLink = screen.getByRole("link", { name: "Review forwarding setup" });
    expect(forwardingLink).toHaveClass("min-h-11");
    expect(forwardingLink).toHaveAttribute("href", "/activate?milestone=forwarding");
  });

  it("announces when the assigned number is copied", async () => {
    render(<AssignedNumberCard forwarding={forwardingGuide()} number="+33612345678" />);

    fireEvent.click(screen.getByRole("button", { name: "Copy assigned number" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Assigned number copied.");
    expect(writeTextMock).toHaveBeenCalledWith("+33612345678");
  });

  it("announces when copying the assigned number fails", async () => {
    writeTextMock.mockRejectedValueOnce(new Error("clipboard blocked"));
    render(<AssignedNumberCard forwarding={forwardingGuide()} number="+33612345678" />);

    fireEvent.click(screen.getByRole("button", { name: "Copy assigned number" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Copy failed. Select the number and copy it manually.");
  });

  it("keeps an unassigned number truthful and links to number setup", () => {
    render(<AssignedNumberCard forwarding={null} number={null} />);

    expect(screen.getByText("No Opevo number is assigned yet.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Copy assigned number" })).toBeNull();
    const setupLink = screen.getByRole("link", { name: "Review number setup" });
    expect(setupLink).toHaveClass("min-h-11");
    expect(setupLink).toHaveAttribute("href", "/activate?milestone=number");
  });
});
