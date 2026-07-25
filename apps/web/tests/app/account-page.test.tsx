import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountStatus } from "@/lib/types/account";

const { getAccountMock, deactivateAccountMock, reactivateAccountMock } = vi.hoisted(() => ({
  getAccountMock: vi.fn(),
  deactivateAccountMock: vi.fn(),
  reactivateAccountMock: vi.fn(),
}));

vi.mock("@/lib/api/account", () => ({
  getAccount: getAccountMock,
}));
vi.mock("@/app/(app)/dashboard/account/actions", () => ({
  deactivateAccount: deactivateAccountMock,
  reactivateAccount: reactivateAccountMock,
}));
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const activeAccount: AccountStatus = {
  status: "active",
  serving: true,
  deactivation: null,
  reactivation_allowed: false,
  blocker: null,
};

function deactivatingAccount(state: NonNullable<AccountStatus["deactivation"]>["state"]): AccountStatus {
  return {
    status: "deactivating",
    serving: false,
    deactivation: {
      state,
      requested_at: "2026-07-24T10:00:00Z",
    },
    reactivation_allowed: false,
    blocker: state === "attention_required" ? "deactivation_attention_required" : "account_deactivating",
  };
}

const inactiveAccount: AccountStatus = {
  status: "inactive",
  serving: false,
  deactivation: null,
  reactivation_allowed: true,
  blocker: "account_inactive",
};

describe("account page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    deactivateAccountMock.mockResolvedValue(undefined);
    reactivateAccountMock.mockResolvedValue({
      status: "success",
      message: "Checkout is ready.",
      url: "https://checkout.stripe.test/reactivate",
    });
  });

  it("shows the danger zone only for active accounts", async () => {
    getAccountMock.mockResolvedValue(activeAccount);

    const { default: Page } = await import("@/app/(app)/dashboard/account/page");
    render(await Page());

    expect(screen.getByRole("heading", { name: "Account" })).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Danger zone" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deactivate Presvo" })).toBeInTheDocument();
  });

  it("requires exact case-sensitive confirmation and presents every consequence", async () => {
    getAccountMock.mockResolvedValue(activeAccount);

    const { default: Page } = await import("@/app/(app)/dashboard/account/page");
    render(await Page());
    fireEvent.click(screen.getByRole("button", { name: "Deactivate Presvo" }));

    for (const sentence of [
      "New calls stop immediately.",
      "Your subscription is canceled immediately with no automatic prorated refund.",
      "An active call may finish before cleanup completes.",
      "Your current Presvo number is permanently released.",
      "Your calls, recordings, billing history, and saved configuration are retained.",
      "Reactivation requires a new subscription and a newly provisioned number.",
    ]) {
      expect(screen.getByText(sentence)).toBeInTheDocument();
    }

    const input = screen.getByLabelText("Type DEACTIVATE to confirm");
    const confirmation = screen.getByRole("button", { name: "Deactivate account" });
    expect(confirmation).toBeDisabled();

    fireEvent.change(input, { target: { value: "deactivate" } });
    expect(confirmation).toBeDisabled();

    fireEvent.change(input, { target: { value: "DEACTIVATE " } });
    expect(confirmation).toBeDisabled();

    fireEvent.change(input, { target: { value: "DEACTIVATE" } });
    expect(confirmation).toBeEnabled();
    fireEvent.click(confirmation);

    await waitFor(() => expect(deactivateAccountMock).toHaveBeenCalledWith("DEACTIVATE"));
  });

  it.each([
    ["requested", "Request accepted"],
    ["attention_required", "Cleanup needs additional time"],
  ] as const)("keeps non-serving copy truthful while deactivation is %s", async (state, progressCopy) => {
    getAccountMock.mockResolvedValue(deactivatingAccount(state));

    const { default: Page } = await import("@/app/(app)/dashboard/account/page");
    const view = render(await Page());

    expect(screen.getByText("Presvo is no longer accepting new calls")).toBeInTheDocument();
    expect(screen.getByText("Finishing account deactivation")).toBeInTheDocument();
    expect(screen.getByText(progressCopy)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Danger zone" })).not.toBeInTheDocument();
    expect(view.container.textContent).not.toMatch(/provider|stripe|telnyx|pn_|sub_/i);
  });

  it("explains retained inactive data, offers reactivation, and never presents an old number as assigned", async () => {
    getAccountMock.mockResolvedValue(inactiveAccount);

    const { default: Page } = await import("@/app/(app)/dashboard/account/page");
    render(await Page());

    expect(screen.getByText("Presvo is inactive")).toBeInTheDocument();
    expect(
      screen.getByText(/Your calls, recordings, billing history, and saved configuration remain available/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reactivate Presvo" })).toBeInTheDocument();
    expect(screen.queryByText(/assigned number/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Danger zone" })).not.toBeInTheDocument();
  });

  it("disables reactivation when cleanup is not yet eligible", async () => {
    getAccountMock.mockResolvedValue({
      ...inactiveAccount,
      reactivation_allowed: false,
      blocker: "reactivation_not_ready",
    });

    const { default: Page } = await import("@/app/(app)/dashboard/account/page");
    render(await Page());

    expect(screen.getByRole("button", { name: "Reactivate Presvo" })).toBeDisabled();
    expect(screen.getByText(/Reactivation will become available after cleanup finishes/i)).toBeInTheDocument();
  });
});
