import { backendFetch } from "@/lib/api/backend-client";
import type { Subscription, UsageLedgerListResponse, UsageSnapshot } from "@/lib/types/billing";

export async function getUsageSnapshot() {
  return backendFetch<UsageSnapshot>("/api/billing/usage");
}

export async function getSubscription() {
  return backendFetch<Subscription | null>("/api/billing/subscription");
}

export async function getUsageLedger(limit = 20) {
  const params = new URLSearchParams({
    limit: String(limit),
  });
  return backendFetch<UsageLedgerListResponse>(`/api/billing/usage-ledger?${params.toString()}`);
}

export async function createCheckoutSession(planTier: "starter" | "standard") {
  return backendFetch<{ url: string }>("/api/billing/checkout-session", {
    method: "POST",
    body: JSON.stringify({ plan_tier: planTier }),
  });
}

export async function createPortalSession(returnUrl: string) {
  return backendFetch<{ url: string }>("/api/billing/portal-session", {
    method: "POST",
    body: JSON.stringify({ return_url: returnUrl }),
  });
}
