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

  it.each([
    {
      securityMode: "unavailable" as const,
      expected: "Email unavailable in local development",
      unexpected: "Email is temporarily unavailable.",
    },
    {
      securityMode: "clerk" as const,
      expected: "Email is temporarily unavailable.",
      unexpected: "Email unavailable in local development",
    },
  ])("renders truthful missing-email copy for $securityMode identity mode", async ({
    securityMode,
    expected,
    unexpected,
  }) => {
    resolveAccountIdentityMock.mockResolvedValueOnce({ email: null, securityMode });

    await renderAccountPage();

    expect(screen.getByText(expected)).toBeVisible();
    expect(screen.queryByText(unexpected)).not.toBeInTheDocument();
  });

  it("keeps unsupported notification, privacy, and MFA preferences visibly local-only and resets without fetching", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<AccountSettingsPreview securityMode="unavailable" />);

    const notifications = screen.getByRole("region", { name: "Notifications Preview" });
    const privacy = screen.getByRole("region", { name: "Privacy & recordings Preview" });
    const security = screen.getByRole("region", { name: "Security" });
    for (const region of [notifications, privacy]) {
      expect(region.querySelector('[data-capability-status="preview"]')).toBeVisible();
      expect(region).toHaveTextContent(/reset on reload/i);
    }
    expect(security.querySelector('[data-capability-status="preview"]')).toBeVisible();
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

  it("limits Preview copy to MFA while Clerk password management remains live", () => {
    render(<AccountSettingsPreview securityMode="clerk" />);

    const security = screen.getByRole("region", { name: "Security" });
    expect(security).toHaveAccessibleDescription("Manage password and sign-in through Clerk.");
    expect(within(security).getByRole("button", { name: "Manage password and sign-in" })).toBeVisible();

    const mfaRow = within(security).getByText("Two-factor authentication").closest(".grid");
    expect(mfaRow).toHaveTextContent("Preview");
    expect(mfaRow).toHaveTextContent("Preview only. This preference stays local and resets on reload.");
  });

  it.each([
    {
      caseName: "serving active account",
      account: activeAccount,
      label: "Active",
      title: "Presvo is active",
      action: null,
    },
    {
      caseName: "non-serving active account",
      account: { ...activeAccount, serving: false, blocker: "customer_not_ready" as const },
      label: "Action needed",
      title: "Presvo needs account attention",
      action: "Review Overview",
    },
  ])("renders the compact lifecycle summary for a $caseName", ({ account, label, title, action }) => {
    render(<CompactAccountStatusCard account={account} />);

    const status = screen.getByRole("region", { name: "Account status" });
    expect(within(status).getByRole("heading", { level: 2, name: "Account status" })).toBeVisible();
    expect(within(status).getByText(label)).toBeVisible();
    expect(within(status).getByText(title)).toBeVisible();
    if (action) {
      expect(within(status).getByRole("link", { name: action })).toHaveAttribute("href", "/dashboard");
    } else {
      expect(within(status).queryByRole("link")).not.toBeInTheDocument();
      expect(within(status).queryByRole("button")).not.toBeInTheDocument();
    }
    expect(status).not.toHaveTextContent("customer_not_ready");
  });

  it.each([
    ["requested", "Request accepted"],
    ["disabling_routing", "Stopping new calls"],
    ["canceling_subscription", "Canceling subscription"],
    ["draining_call", "Waiting for an active call to finish"],
    ["releasing_number", "Releasing your Presvo number"],
    ["finalizing", "Finalizing your account"],
  ] as const)("maps compact %s deactivation progress without exposing internal state", (state, progressCopy) => {
    render(
      <CompactAccountStatusCard
        account={{
          status: "deactivating",
          serving: false,
          deactivation: { state, requested_at: "2026-07-24T10:00:00Z" },
          reactivation_allowed: false,
          blocker: "account_deactivating",
        }}
      />,
    );

    const status = screen.getByRole("region", { name: "Account status" });
    expect(within(status).getByText("Deactivating")).toBeVisible();
    expect(within(status).getByText("Finishing account deactivation")).toBeVisible();
    expect(within(status).getByText(progressCopy)).toBeVisible();
    expect(status).not.toHaveTextContent(state);
    expect(status).not.toHaveTextContent("account_deactivating");
    expect(within(status).queryByRole("link")).not.toBeInTheDocument();
    expect(within(status).queryByRole("button")).not.toBeInTheDocument();
  });

  it.each([
    ["attention_required", "account_deactivating"],
    ["draining_call", "deactivation_attention_required"],
  ] as const)("maps compact %s / %s cleanup to customer-safe attention copy", (state, blocker) => {
    render(
      <CompactAccountStatusCard
        account={{
          status: "deactivating",
          serving: false,
          deactivation: { state, requested_at: "2026-07-24T10:00:00Z" },
          reactivation_allowed: false,
          blocker,
        }}
      />,
    );

    const status = screen.getByRole("region", { name: "Account status" });
    expect(within(status).getByText("Attention required")).toBeVisible();
    expect(within(status).getByText("Account cleanup needs attention")).toBeVisible();
    expect(status).toHaveTextContent("contact Presvo support");
    expect(status).not.toHaveTextContent(/attention_required|deactivation_attention_required|account_deactivating/);
  });

  it.each([
    ["account_inactive", true],
    ["reactivation_not_ready", false],
  ] as const)("keeps compact inactive reactivation truthful for %s", (blocker, reactivationAllowed) => {
    render(
      <CompactAccountStatusCard
        account={{
          ...inactiveAccount,
          blocker,
          reactivation_allowed: reactivationAllowed,
        }}
      />,
    );

    const status = screen.getByRole("region", { name: "Account status" });
    expect(within(status).getByText("Inactive")).toBeVisible();
    expect(within(status).getByText("Presvo is inactive")).toBeVisible();
    const reactivate = within(status).getByRole("button", { name: "Reactivate Presvo" });
    expect(reactivate).toHaveClass("min-h-11");
    if (reactivationAllowed) {
      expect(reactivate).toBeEnabled();
    } else {
      expect(reactivate).toBeDisabled();
      expect(status).toHaveTextContent("Reactivation will become available after cleanup finishes.");
    }
    expect(status).not.toHaveTextContent(blocker);
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
