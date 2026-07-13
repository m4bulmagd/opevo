export type UsageSnapshot = {
  minutes_remaining: number;
  allocated_minutes: number;
  plan_tier: "starter" | null;
  subscription_status: string | null;
  current_period_start: string | null;
  current_period_end: string | null;
};

export type Subscription = {
  plan_tier: "starter";
  status: string;
  allocated_minutes: number;
  current_period_start: string | null;
  current_period_end: string | null;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  can_start_checkout: boolean;
};

export type UsageLedgerEntry = {
  id: string;
  event_type: string;
  minutes_delta: number;
  balance_after: number | null;
  call_id: string | null;
  created_at: string;
};

export type UsageLedgerListResponse = {
  entries: UsageLedgerEntry[];
};
