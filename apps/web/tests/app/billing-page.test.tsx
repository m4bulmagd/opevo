import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BillingActionsCard } from "@/components/billing/billing-actions-card";
import { BackendApiError } from "@/lib/api/backend-client";
import type { Subscription, UsageLedgerEntry, UsageSnapshot } from "@/lib/types/billing";

const {
  createCheckoutSessionMock,
  createPortalSessionMock,
  getSubscriptionMock,
  getUsageLedgerMock,
  getUsageSnapshotMock,
  revalidatePathMock,
} = vi.hoisted(() => ({
  createCheckoutSessionMock: vi.fn(),
  createPortalSessionMock: vi.fn(),
  getSubscriptionMock: vi.fn(),
  getUsageLedgerMock: vi.fn(),
  getUsageSnapshotMock: vi.fn(),
  revalidatePathMock: vi.fn(),
}));

vi.mock("next/cache", () => ({
  revalidatePath: revalidatePathMock,
}));

vi.mock("@/lib/api/billing", () => ({
  getSubscription: getSubscriptionMock,
  getUsageSnapshot: getUsageSnapshotMock,
  getUsageLedger: getUsageLedgerMock,
  createCheckoutSession: createCheckoutSessionMock,
  createPortalSession: createPortalSessionMock,
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const activeSubscription: Subscription = {
  plan_tier: "starter",
  status: "active",
  allocated_minutes: 200,
  current_period_start: "2026-03-01T00:00:00Z",
  current_period_end: "2026-04-01T00:00:00Z",
  stripe_customer_id: "cus_123",
  stripe_subscription_id: "sub_123",
  can_start_checkout: false,
  cancel_at_period_end: false,
  cancellation_effective_at: null,
};

const activeUsage: UsageSnapshot = {
  minutes_remaining: 183,
  allocated_minutes: 200,
  plan_tier: "starter",
  subscription_status: "active",
  current_period_start: "2026-03-01T00:00:00Z",
  current_period_end: "2026-04-01T00:00:00Z",
};

const ledgerEntry: UsageLedgerEntry = {
  id: "entry-1",
  event_type: "call_charge",
  minutes_delta: -1,
  balance_after: 183,
  call_id: "call-1",
  created_at: "2026-03-28T10:01:00Z",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });

  return { promise, reject, resolve };
}

async function renderBillingPage({
  entries = [ledgerEntry],
  subscription = activeSubscription,
  usageSnapshot = activeUsage,
}: {
  entries?: UsageLedgerEntry[];
  subscription?: Subscription | null;
  usageSnapshot?: UsageSnapshot;
} = {}) {
  getSubscriptionMock.mockResolvedValueOnce(subscription);
  getUsageSnapshotMock.mockResolvedValueOnce(usageSnapshot);
  getUsageLedgerMock.mockResolvedValueOnce({ entries });

  const { default: Page } = await import("@/app/(app)/dashboard/billing/page");
  return render(await Page());
}

function expectBefore(first: Element, later: Element) {
  expect(first.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
}

describe("billing page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses PageIntro for the only h1 and leads with subscription status before every billing surface", async () => {
    const { container } = await renderBillingPage();

    const intro = container.querySelector('[data-slot="page-intro"]');
    const subscriptionStatus = screen.getByRole("region", { name: "Active" });
    const metrics = screen.getByRole("region", { name: "Billing metrics" });
    const usage = screen.getByRole("region", { name: "Current period usage" });
    const actions = screen.getByRole("region", { name: "Billing actions" });
    const history = screen.getByRole("region", { name: "Usage history" });

    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Billing and usage" })).toBeInTheDocument();
    expect(intro).toBeInTheDocument();
    expectBefore(intro as Element, subscriptionStatus);
    expectBefore(subscriptionStatus, metrics);
    expectBefore(subscriptionStatus, usage);
    expectBefore(subscriptionStatus, actions);
    expectBefore(subscriptionStatus, history);
  });

  it("keeps the no-subscription checkout path and empty-ledger guidance", async () => {
    await renderBillingPage({
      entries: [],
      subscription: null,
      usageSnapshot: {
        minutes_remaining: 0,
        allocated_minutes: 0,
        plan_tier: null,
        subscription_status: null,
        current_period_start: null,
        current_period_end: null,
      },
    });

    expect(screen.getByText("No active subscription")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start starter plan" })).toBeInTheDocument();
    expect(screen.getByText("No billing activity yet")).toBeInTheDocument();
    expect(
      screen.getByText("Usage ledger events will appear here after the first plan or call event."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/standard/i)).not.toBeInTheDocument();
  });

  it("presents remaining, derived used minutes, and plan in one three-item metric band", async () => {
    await renderBillingPage();

    const metrics = screen.getByRole("region", { name: "Billing metrics" });
    const metricItems = metrics.querySelectorAll('[data-slot="metric-item"]');

    expect(metricItems).toHaveLength(3);
    expect(within(metrics).getByText("Minutes remaining").closest('[data-slot="metric-item"]')).toHaveTextContent(
      "183 min",
    );
    expect(within(metrics).getByText("Minutes used").closest('[data-slot="metric-item"]')).toHaveTextContent("17 min");
    expect(within(metrics).getByText("Plan").closest('[data-slot="metric-item"]')).toHaveTextContent("Starter");
  });

  it("clamps presentation-only used minutes at zero when remaining exceeds allocated", async () => {
    await renderBillingPage({
      usageSnapshot: {
        ...activeUsage,
        allocated_minutes: 60,
        minutes_remaining: 65,
      },
    });

    const metrics = screen.getByRole("region", { name: "Billing metrics" });
    expect(within(metrics).getByText("Minutes used").closest('[data-slot="metric-item"]')).toHaveTextContent("0 min");
  });

  it("keeps scheduled cancellation active through the paid-period end and renders its valid UTC date", async () => {
    const { container } = await renderBillingPage({
      subscription: {
        ...activeSubscription,
        cancel_at_period_end: true,
        cancellation_effective_at: "2026-04-01T00:00:00Z",
      },
    });

    const status = screen.getByRole("region", { name: "Active" });
    expect(status).toHaveTextContent("Cancels at the end of your paid period");
    expect(status).toHaveTextContent("Effective April 1, 2026 · UTC");
    expect(status).toHaveTextContent("Active");
    expect(container).not.toHaveTextContent(/account is inactive|account deactivation|deactivate account/i);
  });

  it("does not describe a non-active cancellation record as active", async () => {
    await renderBillingPage({
      subscription: {
        ...activeSubscription,
        status: "canceled",
        can_start_checkout: false,
        cancel_at_period_end: true,
        cancellation_effective_at: "2026-04-01T00:00:00Z",
      },
      usageSnapshot: {
        ...activeUsage,
        subscription_status: "canceled",
      },
    });

    const status = screen.getByRole("region", { name: "Canceled" });
    expect(status).toHaveTextContent("Cancels at the end of your paid period");
    expect(status).not.toHaveTextContent("remains active");
  });

  it.each([
    "active",
    "trialing",
    "past_due",
    "unpaid",
    "incomplete",
    "paused",
  ])("shows Manage billing for backend-ineligible %s subscriptions", async (status) => {
    await renderBillingPage({
      subscription: {
        ...activeSubscription,
        status,
        can_start_checkout: false,
      },
      usageSnapshot: {
        ...activeUsage,
        subscription_status: status,
      },
    });

    expect(screen.getByRole("button", { name: "Manage billing" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start starter plan" })).not.toBeInTheDocument();
  });

  it.each([
    "canceled",
    "incomplete_expired",
  ])("shows Start starter plan for backend-eligible terminal %s subscriptions", async (status) => {
    await renderBillingPage({
      subscription: {
        ...activeSubscription,
        status,
        can_start_checkout: true,
      },
      usageSnapshot: {
        ...activeUsage,
        subscription_status: status,
      },
    });

    expect(screen.getByRole("button", { name: "Start starter plan" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage billing" })).not.toBeInTheDocument();
  });

  it("keeps portal access for terminal history when the backend disallows a new checkout", async () => {
    await renderBillingPage({
      subscription: {
        ...activeSubscription,
        status: "canceled",
        can_start_checkout: false,
      },
      usageSnapshot: {
        ...activeUsage,
        subscription_status: "canceled",
      },
    });

    expect(screen.getByRole("button", { name: "Manage billing" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Usage ledger" })).toBeInTheDocument();
    expect(screen.getByText("Call Charge")).toBeInTheDocument();
  });

  it.each([
    {
      idle: "Start starter plan",
      pending: "Opening checkout",
      successToast: "Opening Stripe Checkout",
      subscription: null,
      url: "https://checkout.test",
    },
    {
      idle: "Manage billing",
      pending: "Opening billing portal",
      successToast: "Opening billing portal",
      subscription: activeSubscription,
      url: "https://portal.test",
    },
  ])("presents idle and pending phases before redirecting from $idle", async (testCase) => {
    const session = deferred<{ url: string }>();
    const navigate = vi.fn();
    if (testCase.subscription === null) {
      createCheckoutSessionMock.mockReturnValueOnce(session.promise);
    } else {
      createPortalSessionMock.mockReturnValueOnce(session.promise);
    }
    render(<BillingActionsCard navigate={navigate} subscription={testCase.subscription} />);

    const idleButton = screen.getByRole("button", { name: testCase.idle });
    expect(within(idleButton).getByText(testCase.idle).closest("[data-phase]")).toHaveAttribute("data-phase", "idle");

    fireEvent.click(idleButton);

    const pendingPhase = await waitFor(() => {
      const phase = document.querySelector('[data-phase="pending"]');
      expect(phase).toBeInTheDocument();
      return phase as HTMLElement;
    });
    expect(pendingPhase.closest("button")).toBeDisabled();
    expect(within(pendingPhase).getByText(testCase.pending)).toBeInTheDocument();

    await act(async () => {
      session.resolve({ url: testCase.url });
    });

    await waitFor(() => expect(navigate).toHaveBeenCalledWith(testCase.url));
    expect(toast.success).toHaveBeenCalledWith(testCase.successToast);
    if (testCase.subscription === null) {
      expect(createCheckoutSessionMock).toHaveBeenCalledWith("starter");
    } else {
      expect(createPortalSessionMock).toHaveBeenCalled();
    }
  });

  it("shows an observable error phase and clears stale feedback throughout a retry", async () => {
    const retry = deferred<{ url: string }>();
    const navigate = vi.fn();
    createCheckoutSessionMock
      .mockRejectedValueOnce(new BackendApiError("Checkout is unavailable", 502))
      .mockReturnValueOnce(retry.promise);
    render(<BillingActionsCard navigate={navigate} subscription={null} />);

    fireEvent.click(screen.getByRole("button", { name: "Start starter plan" }));

    const errorPhase = await waitFor(() => {
      const phase = document.querySelector('[data-phase="error"]');
      expect(phase).toBeInTheDocument();
      return phase as HTMLElement;
    });
    const retryButton = errorPhase.closest("button") as HTMLButtonElement;
    expect(retryButton).toBeEnabled();
    expect(within(errorPhase).getByText("Try checkout again")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Billing action feedback" })).toHaveTextContent(
      "Checkout is unavailable",
    );
    expect(toast.error).toHaveBeenCalledWith("Checkout is unavailable");

    fireEvent.click(retryButton);

    const pendingPhase = await waitFor(() => {
      const phase = document.querySelector('[data-phase="pending"]');
      expect(phase).toBeInTheDocument();
      return phase as HTMLElement;
    });
    expect(pendingPhase.closest("button")).toBeDisabled();
    expect(screen.getByRole("status", { name: "Billing action feedback" })).toBeEmptyDOMElement();
    expect(screen.queryByText("Checkout is unavailable")).not.toBeInTheDocument();

    await act(async () => {
      retry.resolve({ url: "https://checkout.retry.test" });
    });

    await waitFor(() => expect(navigate).toHaveBeenCalledWith("https://checkout.retry.test"));
  });

  it("renders usage history through labelled DataLedger cells without changing stored formatting", async () => {
    await renderBillingPage();

    const ledger = screen.getByRole("list", { name: "Usage ledger" });
    const row = within(ledger).getByRole("listitem");

    for (const label of ["Event", "Date", "Change", "Balance"]) {
      expect(
        within(row).getByText(label, {
          selector: '[data-slot="data-ledger-mobile-label"]',
        }),
      ).toBeInTheDocument();
    }
    expect(row).toHaveTextContent("Call Charge");
    expect(row.querySelector("time")).toHaveAttribute("dateTime", "2026-03-28T10:01:00Z");
    expect(row.querySelector("time")).toHaveTextContent(/^Mar 28, \d{2}:\d{2}$/);
    expect(row).toHaveTextContent("-1");
    expect(row).toHaveTextContent("183");
  });

  it("keeps usage detail, billing action, and ledger history in distinct product surfaces", async () => {
    await renderBillingPage();

    const usage = screen.getByRole("region", { name: "Current period usage" });
    const actions = screen.getByRole("region", { name: "Billing actions" });
    const history = screen.getByRole("region", { name: "Usage history" });

    expect(usage).toHaveAttribute("data-slot", "product-surface");
    expect(actions).toHaveAttribute("data-slot", "product-surface");
    expect(history).toHaveAttribute("data-slot", "product-surface");
    expect(usage).not.toBe(actions);
    expect(actions).not.toBe(history);
    expect(actions).not.toHaveTextContent(/deactivate|release number|inactive account/i);
  });

  it("creates hosted billing sessions through the unchanged server actions", async () => {
    createCheckoutSessionMock.mockResolvedValueOnce({ url: "https://checkout.test" });
    createPortalSessionMock.mockResolvedValueOnce({ url: "https://portal.test" });

    const { createCheckoutSessionAction, createPortalSessionAction } = await import(
      "@/app/(app)/dashboard/billing/actions"
    );

    const checkoutResult = await createCheckoutSessionAction("starter");
    const portalResult = await createPortalSessionAction();

    expect(checkoutResult.url).toBe("https://checkout.test");
    expect(createCheckoutSessionMock).toHaveBeenCalledWith("starter");
    expect(portalResult.url).toBe("https://portal.test");
  });
});
