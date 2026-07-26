import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountLifecycleBanner } from "@/components/account/account-lifecycle-banner";
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

function expectNoInternalLifecycleDetails(container: HTMLElement) {
  expect(container).not.toHaveTextContent(
    /account_deactivating|account_inactive|deactivation_attention_required|reactivation_not_ready|customer_not_ready|attention_required|disabling_routing|canceling_subscription|draining_call|releasing_number|provider|stripe|telnyx|pn_|sub_/i,
  );
}

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

  it("uses PageIntro for the only h1 and leads with account status before settings and danger", async () => {
    const { container } = await renderAccountPage();

    const intro = container.querySelector('[data-slot="page-intro"]');
    const status = screen.getByRole("region", { name: "Active" });
    const settings = screen.getByRole("region", { name: "Account settings" });
    const danger = screen.getByRole("region", { name: "Danger zone" });

    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Account" })).toBeInTheDocument();
    expect(intro).toBeInTheDocument();
    expectBefore(intro as Element, status);
    expectBefore(status, settings);
    expectBefore(settings, danger);
  });

  it.each([
    {
      account: activeAccount,
      label: "Active",
      title: "Presvo is active",
      description: "Presvo can accept new calls.",
    },
    {
      account: { ...activeAccount, serving: false, blocker: "customer_not_ready" } satisfies AccountStatus,
      label: "Action needed",
      title: "Presvo needs account attention",
      description: "Presvo is not accepting new calls yet.",
    },
  ])("maps an active account to the bounded $label status", async ({ account, description, label, title }) => {
    const { container } = await renderAccountPage(account);

    const status = screen.getByRole("region", { name: label });
    expect(status).toHaveAttribute("data-slot", "status-surface");
    expect(status).not.toContainElement(status.querySelector('[data-slot="card"]'));
    expect(within(status).getByRole("heading", { level: 2, name: title })).toBeInTheDocument();
    expect(within(status).getByText(description)).toBeInTheDocument();
    if (!account.serving) {
      expect(within(status).getByRole("link", { name: "Review Overview" })).toHaveAttribute("href", "/dashboard");
    }
    expectNoInternalLifecycleDetails(container);
  });

  it.each([
    ["requested", "Request accepted"],
    ["disabling_routing", "Stopping new calls"],
    ["canceling_subscription", "Canceling subscription"],
    ["draining_call", "Waiting for an active call to finish"],
    ["releasing_number", "Releasing your Presvo number"],
    ["finalizing", "Finalizing your account"],
  ] as const)("maps ordinary %s deactivation progress without exposing its internal state", async (state, progressCopy) => {
    const { container } = await renderAccountPage(deactivatingAccount(state));

    const status = screen.getByRole("region", { name: "Deactivating" });
    expect(status).toHaveAttribute("data-slot", "status-surface");
    expect(within(status).getByRole("heading", { name: "Finishing account deactivation" })).toBeInTheDocument();
    expect(within(status).getByText("Presvo is no longer accepting new calls")).toBeInTheDocument();
    expect(within(status).getByText(progressCopy)).toBeInTheDocument();
    expectNoInternalLifecycleDetails(container);
  });

  it.each([
    {
      deactivation: { state: "attention_required", requested_at: "2026-07-24T10:00:00Z" },
      blocker: "account_deactivating",
    },
    {
      deactivation: { state: "requested", requested_at: "2026-07-24T10:00:00Z" },
      blocker: "deactivation_attention_required",
    },
  ] satisfies Array<
    Pick<AccountStatus, "blocker" | "deactivation">
  >)("turns either attention signal into customer-safe status and next-step guidance", async ({
    blocker,
    deactivation,
  }) => {
    const { container } = await renderAccountPage({
      status: "deactivating",
      serving: false,
      deactivation,
      reactivation_allowed: false,
      blocker,
    });

    const status = screen.getByRole("region", { name: "Attention required" });
    expect(within(status).getByRole("heading", { name: "Account cleanup needs attention" })).toBeInTheDocument();
    expect(within(status).getByText("Presvo is no longer accepting new calls")).toBeInTheDocument();
    expect(within(status).getByText(/Your retained data remains available/i)).toBeInTheDocument();
    expect(within(status).getByText(/refresh this page.*contact Presvo support/i)).toBeInTheDocument();
    expectNoInternalLifecycleDetails(container);
  });

  it("groups real account destinations and header-based session guidance in calm settings rows", async () => {
    await renderAccountPage();

    const settings = screen.getByRole("region", { name: "Account settings" });
    expect(settings).toHaveAttribute("data-slot", "product-surface");
    expect(settings).toHaveAttribute("data-tone", "subtle");

    const sections = settings.querySelectorAll('[data-slot="settings-section"]');
    expect(sections).toHaveLength(4);
    expect(Array.from(sections, (section) => within(section as HTMLElement).getByRole("heading").textContent)).toEqual([
      "Receptionist profile",
      "Billing and subscription",
      "Session and security",
      "Account state",
    ]);
    expect(within(settings).getByRole("link", { name: "Manage receptionist" })).toHaveAttribute(
      "href",
      "/dashboard/agent",
    );
    expect(within(settings).getByRole("link", { name: "View billing" })).toHaveAttribute("href", "/dashboard/billing");
    expect(
      within(settings).getByText(
        "Authentication and session controls follow the active sign-in mode. For hosted accounts, use the workspace header account control to sign out.",
      ),
    ).toBeInTheDocument();
    expect(within(settings).queryByRole("link", { name: /profile|security/i })).not.toBeInTheDocument();
  });

  it("keeps the danger zone separate, destructive, and limited to active accounts", async () => {
    await renderAccountPage();

    const settings = screen.getByRole("region", { name: "Account settings" });
    const danger = screen.getByRole("region", { name: "Danger zone" });
    expect(danger).toHaveAttribute("data-slot", "product-surface");
    expect(danger).toHaveAttribute("data-tone", "danger");
    expect(settings).not.toContainElement(danger);
    expect(danger.querySelector('[data-slot="settings-section"]')).not.toBeInTheDocument();
    expect(within(danger).getByRole("button", { name: "Deactivate Presvo" })).toHaveClass("min-h-11");
  });

  it("requires exact case-sensitive confirmation and presents every consequence", async () => {
    await renderAccountPage();
    fireEvent.click(screen.getByRole("button", { name: "Deactivate Presvo" }));

    const consequenceList = screen.getByRole("list", { name: "Account deactivation consequences" });
    expect(Array.from(consequenceList.children, (item) => item.textContent)).toEqual(consequences);
    for (const sentence of consequences) {
      expect(screen.getByText(sentence)).toBeInTheDocument();
    }

    const input = screen.getByLabelText("Type DEACTIVATE to confirm");
    const confirmation = screen.getByRole("button", { name: "Deactivate account" });
    expect(input).toHaveAttribute("name", "deactivation-confirmation");
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

  it("contains the confirmation workflow in a viewport-bounded, internally scrollable dialog", async () => {
    await renderAccountPage();
    fireEvent.click(screen.getByRole("button", { name: "Deactivate Presvo" }));

    const dialog = screen.getByRole("alertdialog");
    const scrollRegion = dialog.querySelector('[data-slot="deactivation-dialog-scroll-region"]');

    expect(dialog).toHaveClass("max-h-[calc(100dvh-2rem)]", "overflow-hidden", "overscroll-contain");
    expect(scrollRegion).toHaveClass("min-h-0", "overflow-y-auto", "overscroll-contain");
    expect(screen.getByRole("list", { name: "Account deactivation consequences" }).children).toHaveLength(6);
    expect(screen.getByLabelText("Type DEACTIVATE to confirm")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep Presvo active" })).toHaveClass("min-h-11");
    expect(screen.getByRole("button", { name: "Deactivate account" })).toHaveClass("min-h-11");
  });

  it("traps dialog focus and restores it after Escape and the keep-active action", async () => {
    await renderAccountPage();
    const trigger = screen.getByRole("button", { name: "Deactivate Presvo" });
    const backgroundLink = screen.getByRole("link", { name: "Manage receptionist" });

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

  it("retains bounded cleanup progress and new-cycle rules while inactive", async () => {
    const { container } = await renderAccountPage({
      ...inactiveAccount,
      deactivation: { state: "finalizing", requested_at: "2026-07-24T10:00:00Z" },
      reactivation_allowed: false,
      blocker: "reactivation_not_ready",
    });

    const status = screen.getByRole("region", { name: "Inactive" });
    expect(within(status).getByText("Presvo is inactive")).toBeInTheDocument();
    expect(
      within(status).getByText(/Your calls, recordings, billing history, and saved configuration remain available/i),
    ).toBeInTheDocument();
    expect(within(status).getByText("Finalizing your account")).toBeInTheDocument();
    expect(within(status).getByText(/new subscription.*newly provisioned number/i)).toBeInTheDocument();
    expect(status.querySelector('[data-slot="reactivation-action"]')).toHaveClass("flex-col", "max-w-sm");
    expect(within(status).getByRole("button", { name: "Reactivate Presvo" })).toHaveClass("min-h-11");
    expect(within(status).getByRole("button", { name: "Reactivate Presvo" })).toBeDisabled();
    expect(container).not.toHaveTextContent(/(?:old|released|current).{0,24}(?:assigned|reserved)/i);
    expectNoInternalLifecycleDetails(container);
    expect(screen.queryByRole("heading", { name: "Danger zone" })).not.toBeInTheDocument();
  });

  it("enables reactivation only when the inactive account is eligible", async () => {
    await renderAccountPage(inactiveAccount);

    expect(screen.getByRole("button", { name: "Reactivate Presvo" })).toBeEnabled();
    expect(screen.getByText(/new subscription.*newly provisioned number/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Danger zone" })).not.toBeInTheDocument();
  });

  it.each([
    {
      deactivation: { state: "attention_required", requested_at: "2026-07-24T10:00:00Z" },
      blocker: "account_deactivating",
    },
    {
      deactivation: { state: "requested", requested_at: "2026-07-24T10:00:00Z" },
      blocker: "deactivation_attention_required",
    },
  ] satisfies Array<
    Pick<AccountStatus, "blocker" | "deactivation">
  >)("gives either cleanup attention signal a concise customer-safe global banner", ({ blocker, deactivation }) => {
    const { container } = render(
      <AccountLifecycleBanner
        account={{
          status: "deactivating",
          serving: false,
          deactivation,
          reactivation_allowed: false,
          blocker,
        }}
      />,
    );

    expect(screen.getByText("Account cleanup needs attention")).toBeInTheDocument();
    expect(screen.getByText(/retained data remains available/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View account" })).toHaveClass("min-h-11");
    expect(screen.getByRole("link", { name: "View account" })).toHaveAttribute("href", "/dashboard/account");
    expectNoInternalLifecycleDetails(container);
  });
});
