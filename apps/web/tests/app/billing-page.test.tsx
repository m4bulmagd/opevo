import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const getSubscriptionMock = vi.fn();
const getUsageSnapshotMock = vi.fn();
const getUsageLedgerMock = vi.fn();
const createCheckoutSessionMock = vi.fn();
const createPortalSessionMock = vi.fn();
const revalidatePathMock = vi.fn();

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

describe("billing page", () => {
  it("renders usage state and checkout action for unsubscribed users", async () => {
    getSubscriptionMock.mockResolvedValueOnce(null);
    getUsageSnapshotMock.mockResolvedValueOnce({
      minutes_remaining: 0,
      allocated_minutes: 0,
      plan_tier: null,
      subscription_status: null,
      current_period_start: null,
      current_period_end: null,
    });
    getUsageLedgerMock.mockResolvedValueOnce({ entries: [] });

    const { default: Page } = await import("@/app/(app)/dashboard/billing/page");
    render(await Page());

    expect(screen.getByText(/No active subscription/i)).toBeInTheDocument();
    expect(screen.getByText(/No billing activity yet/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start starter plan/i })).toBeInTheDocument();
    expect(screen.queryByText(/standard/i)).not.toBeInTheDocument();
  });

  it("renders portal action for active subscriptions", async () => {
    getSubscriptionMock.mockResolvedValueOnce({
      plan_tier: "starter",
      status: "active",
      allocated_minutes: 200,
      current_period_start: "2026-03-01T00:00:00Z",
      current_period_end: "2026-03-31T23:59:59Z",
      stripe_customer_id: "cus_123",
      stripe_subscription_id: "sub_123",
      can_start_checkout: false,
    });
    getUsageSnapshotMock.mockResolvedValueOnce({
      minutes_remaining: 183,
      allocated_minutes: 200,
      plan_tier: "starter",
      subscription_status: "active",
      current_period_start: "2026-03-01T00:00:00Z",
      current_period_end: "2026-03-31T23:59:59Z",
    });
    getUsageLedgerMock.mockResolvedValueOnce({
      entries: [
        {
          id: "entry-1",
          event_type: "call_charge",
          minutes_delta: -1,
          balance_after: 183,
          call_id: "call-1",
          created_at: "2026-03-28T10:01:00Z",
        },
      ],
    });

    const { default: Page } = await import("@/app/(app)/dashboard/billing/page");
    render(await Page());

    expect(screen.getAllByText(/Starter/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Call Charge/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Manage billing/i })).toBeInTheDocument();
  });

  it.each([
    "trialing",
    "past_due",
    "unpaid",
    "incomplete",
    "paused",
  ])("renders portal action instead of checkout for %s subscriptions", async (status) => {
    getSubscriptionMock.mockResolvedValueOnce({
      plan_tier: "starter",
      status,
      allocated_minutes: 60,
      current_period_start: "2026-03-01T00:00:00Z",
      current_period_end: "2026-03-31T23:59:59Z",
      stripe_customer_id: "cus_123",
      stripe_subscription_id: "sub_123",
      can_start_checkout: false,
    });
    getUsageSnapshotMock.mockResolvedValueOnce({
      minutes_remaining: 60,
      allocated_minutes: 60,
      plan_tier: "starter",
      subscription_status: status,
      current_period_start: "2026-03-01T00:00:00Z",
      current_period_end: "2026-03-31T23:59:59Z",
    });
    getUsageLedgerMock.mockResolvedValueOnce({ entries: [] });

    const { default: Page } = await import("@/app/(app)/dashboard/billing/page");
    render(await Page());

    expect(screen.getByRole("button", { name: /Manage billing/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start starter plan/i })).not.toBeInTheDocument();
  });

  it.each([
    "canceled",
    "incomplete_expired",
  ])("renders checkout action for terminal %s subscriptions", async (status) => {
    getSubscriptionMock.mockResolvedValueOnce({
      plan_tier: "starter",
      status,
      allocated_minutes: 60,
      current_period_start: "2026-03-01T00:00:00Z",
      current_period_end: "2026-03-31T23:59:59Z",
      stripe_customer_id: "cus_123",
      stripe_subscription_id: "sub_123",
      can_start_checkout: true,
    });
    getUsageSnapshotMock.mockResolvedValueOnce({
      minutes_remaining: 60,
      allocated_minutes: 60,
      plan_tier: "starter",
      subscription_status: status,
      current_period_start: "2026-03-01T00:00:00Z",
      current_period_end: "2026-03-31T23:59:59Z",
    });
    getUsageLedgerMock.mockResolvedValueOnce({ entries: [] });

    const { default: Page } = await import("@/app/(app)/dashboard/billing/page");
    render(await Page());

    expect(screen.getByRole("button", { name: /Start starter plan/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Manage billing/i })).not.toBeInTheDocument();
  });

  it("uses backend checkout eligibility instead of duplicating status policy", async () => {
    getSubscriptionMock.mockResolvedValueOnce({
      plan_tier: "starter",
      status: "canceled",
      allocated_minutes: 60,
      current_period_start: null,
      current_period_end: null,
      stripe_customer_id: "cus_policy",
      stripe_subscription_id: "sub_policy",
      can_start_checkout: false,
    });
    getUsageSnapshotMock.mockResolvedValueOnce({
      minutes_remaining: 0,
      allocated_minutes: 60,
      plan_tier: "starter",
      subscription_status: "canceled",
      current_period_start: null,
      current_period_end: null,
    });
    getUsageLedgerMock.mockResolvedValueOnce({ entries: [] });

    const { default: Page } = await import("@/app/(app)/dashboard/billing/page");
    render(await Page());

    expect(screen.getByRole("button", { name: /Manage billing/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start starter plan/i })).not.toBeInTheDocument();
  });

  it("creates hosted billing sessions through the server actions", async () => {
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
