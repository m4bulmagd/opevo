import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountSettingsPreview } from "@/components/account/account-settings-preview";
import { CompactAccountStatusCard } from "@/components/account/account-status-card";
import type { AccountStatus } from "@/lib/types/account";

import { activationSnapshot } from "./activation-snapshot-fixture";

const {
  getAccountMock,
  getActivationSnapshotMock,
  resolveAccountIdentityMock,
  deactivateAccountMock,
  reactivateAccountMock,
} = vi.hoisted(() => ({
  getAccountMock: vi.fn(),
  getActivationSnapshotMock: vi.fn(),
  resolveAccountIdentityMock: vi.fn(),
  deactivateAccountMock: vi.fn(),
  reactivateAccountMock: vi.fn(),
}));

vi.mock("@/lib/api/account", () => ({ getAccount: getAccountMock }));
vi.mock("@/lib/api/activation", () => ({ getActivationSnapshot: getActivationSnapshotMock }));
vi.mock("@/lib/auth/account-identity", () => ({ resolveAccountIdentity: resolveAccountIdentityMock }));
vi.mock("@/app/(app)/dashboard/account/actions", () => ({
  deactivateAccount: deactivateAccountMock,
  reactivateAccount: reactivateAccountMock,
}));
vi.mock("@clerk/nextjs", () => ({
  useClerk: () => ({ openUserProfile: vi.fn() }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const activeAccount: AccountStatus = {
  status: "active",
  serving: true,
  deactivation: null,
  reactivation_allowed: false,
  blocker: null,
};

const inactiveAccount: AccountStatus = {
  status: "inactive",
  serving: false,
  deactivation: null,
  reactivation_allowed: true,
  blocker: "account_inactive",
};

const consequences = [
  "New calls stop immediately.",
  "Your subscription is canceled immediately with no automatic prorated refund.",
  "An active call may finish before cleanup completes.",
  "Your current Presvo number is permanently released.",
  "Your calls, recordings, billing history, and saved configuration are retained.",
  "Reactivation requires a new subscription and a newly provisioned number.",
] as const;

async function renderAccountPage(account: AccountStatus = activeAccount) {
  getAccountMock.mockResolvedValueOnce(account);
  const { default: Page } = await import("@/app/(app)/dashboard/account/page");
  return render(await Page());
}

function expectBefore(first: Element, later: Element) {
  expect(first.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
}

describe("account page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getActivationSnapshotMock.mockResolvedValue(activationSnapshot());
    resolveAccountIdentityMock.mockResolvedValue({ email: "maya@presvo.test", securityMode: "clerk" });
    deactivateAccountMock.mockResolvedValue(undefined);
    reactivateAccountMock.mockResolvedValue({
      status: "success",
      message: "Checkout is ready.",
      url: "https://checkout.stripe.test/reactivate",
    });
  });

  it("leads with profile and service context, then user preferences, security, and the danger zone", async () => {
    const { container } = await renderAccountPage();

    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
    const profile = screen.getByRole("region", { name: "Profile" });
    const number = screen.getByRole("region", { name: "Assigned number" });
    const accountStatus = screen.getByRole("region", { name: "Account status" });
    const notifications = screen.getByRole("region", { name: "Notifications Preview" });
    const privacy = screen.getByRole("region", { name: "Privacy & recordings Preview" });
    const security = screen.getByRole("region", { name: "Security" });
    const danger = screen.getByRole("region", { name: "Danger zone" });

    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expectBefore(profile, number);
    expectBefore(number, accountStatus);
    expectBefore(accountStatus, notifications);
    expectBefore(notifications, privacy);
    expectBefore(privacy, security);
    expectBefore(security, danger);

    for (const name of [
      "Receptionist profile",
      "Billing and subscription",
      "Theme and session",
      "Manage receptionist",
      "View billing",
    ]) {
      expect(screen.queryByRole("heading", { name })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name })).not.toBeInTheDocument();
    }
  });

  it("keeps account controls available and describes profile data honestly when activation lookup fails", async () => {
    getActivationSnapshotMock.mockRejectedValueOnce(new Error("activation unavailable"));
    await renderAccountPage();

    const unavailableProfile = screen.getByRole("region", { name: "Profile unavailable" });
    const retry = within(unavailableProfile).getByRole("link", { name: "Retry profile" });
    expect(retry).toHaveAttribute("href", "/dashboard/account");
    expect(retry).not.toHaveAttribute("data-prefetch", "true");
    expect(screen.getByRole("region", { name: "Assigned number" })).toHaveTextContent(/unavailable/i);
    expect(screen.getByRole("region", { name: "Account status" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Security" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Danger zone" })).toBeVisible();
  });

  it("keeps each unsupported preference surface visibly local-only and resets without fetching", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<AccountSettingsPreview securityMode="unavailable" />);

    const notifications = screen.getByRole("region", { name: "Notifications Preview" });
    const privacy = screen.getByRole("region", { name: "Privacy & recordings Preview" });
    const security = screen.getByRole("region", { name: "Security" });
    for (const region of [notifications, privacy, security]) {
      expect(region.querySelector('[data-capability-status="preview"]')).toBeVisible();
      expect(region).toHaveTextContent(/reset on reload/i);
    }
    expect(
      within(security).getByText("Password and sign-in methods are managed through Clerk in hosted accounts."),
    ).toBeVisible();
    expect(within(security).queryByRole("button", { name: "Manage password and sign-in" })).not.toBeInTheDocument();
    expect(security).not.toHaveTextContent(/saved|enabled|updated successfully/i);

    const summaries = within(notifications).getByRole("switch", { name: "Call summaries" });
    const recording = within(privacy).getByRole("switch", { name: "Record calls" });
    const mfa = within(security).getByRole("switch", { name: "Two-factor authentication" });
    fireEvent.click(summaries);
    fireEvent.change(within(privacy).getByRole("combobox", { name: "Preview recording retention" }), {
      target: { value: "365" },
    });
    fireEvent.click(mfa);
    fireEvent.click(within(notifications).getByRole("button", { name: "Reset settings Preview" }));

    expect(summaries).toBeChecked();
    expect(recording).toBeChecked();
    expect(mfa).not.toBeChecked();
    expect(within(privacy).getByRole("combobox", { name: "Preview recording retention" })).toHaveValue("30");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("renders compact lifecycle context in the service column", () => {
    render(<CompactAccountStatusCard account={activeAccount} />);

    const status = screen.getByRole("region", { name: "Account status" });
    expect(within(status).getByRole("heading", { level: 2, name: "Account status" })).toBeVisible();
    expect(within(status).getByText("Active")).toBeVisible();
  });

  it("keeps the danger zone separate, destructive, and limited to active accounts", async () => {
    const { unmount } = await renderAccountPage();
    const danger = screen.getByRole("region", { name: "Danger zone" });
    expect(danger).toHaveAttribute("data-slot", "product-surface");
    expect(danger).toHaveAttribute("data-tone", "danger");
    expect(within(danger).getByRole("button", { name: "Deactivate Presvo" })).toHaveClass("min-h-11");

    unmount();
    await renderAccountPage(inactiveAccount);
    expect(screen.queryByRole("region", { name: "Danger zone" })).not.toBeInTheDocument();
  });

  it("preserves the exact case-sensitive deactivation confirmation and consequences", async () => {
    await renderAccountPage();
    fireEvent.click(screen.getByRole("button", { name: "Deactivate Presvo" }));

    const consequenceList = screen.getByRole("list", { name: "Account deactivation consequences" });
    expect(Array.from(consequenceList.children, (item) => item.textContent)).toEqual(consequences);
    const input = screen.getByLabelText("Type DEACTIVATE to confirm");
    const confirmation = screen.getByRole("button", { name: "Deactivate account" });
    fireEvent.change(input, { target: { value: "deactivate" } });
    expect(confirmation).toBeDisabled();
    fireEvent.change(input, { target: { value: "DEACTIVATE" } });
    expect(confirmation).toBeEnabled();
    fireEvent.click(confirmation);
    await waitFor(() => expect(deactivateAccountMock).toHaveBeenCalledWith("DEACTIVATE"));
  });

  it("keeps the confirmation workflow viewport-bounded and internally scrollable", async () => {
    await renderAccountPage();
    fireEvent.click(screen.getByRole("button", { name: "Deactivate Presvo" }));

    const dialog = screen.getByRole("alertdialog");
    const scrollRegion = dialog.querySelector('[data-slot="deactivation-dialog-scroll-region"]');
    expect(dialog).toHaveClass("max-h-[calc(100dvh-2rem)]", "overflow-hidden", "overscroll-contain");
    expect(scrollRegion).toHaveClass("min-h-0", "overflow-y-auto", "overscroll-contain");
    expect(screen.getByRole("list", { name: "Account deactivation consequences" }).children).toHaveLength(6);
  });

  it("traps dialog focus and restores it after escape and keep-active actions", async () => {
    await renderAccountPage();
    const trigger = screen.getByRole("button", { name: "Deactivate Presvo" });
    const backgroundLink = screen.getByRole("link", { name: "Review forwarding setup" });

    fireEvent.click(trigger);
    const firstDialog = screen.getByRole("alertdialog");
    await waitFor(() => expect(firstDialog).toContainElement(document.activeElement as HTMLElement));
    backgroundLink.focus();
    await waitFor(() => expect(firstDialog).toContainElement(document.activeElement as HTMLElement));

    fireEvent.keyDown(firstDialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();

    fireEvent.click(trigger);
    const secondDialog = screen.getByRole("alertdialog");
    await waitFor(() => expect(secondDialog).toContainElement(document.activeElement as HTMLElement));
    fireEvent.click(within(secondDialog).getByRole("button", { name: "Keep Presvo active" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
});
