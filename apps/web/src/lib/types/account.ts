export type AccountStatus = {
  status: "active" | "deactivating" | "inactive";
  serving: boolean;
  deactivation: {
    state:
      | "requested"
      | "disabling_routing"
      | "canceling_subscription"
      | "draining_call"
      | "releasing_number"
      | "finalizing"
      | "attention_required";
    requested_at: string;
  } | null;
  reactivation_allowed: boolean;
  blocker:
    | "account_deactivating"
    | "account_inactive"
    | "deactivation_attention_required"
    | "reactivation_not_ready"
    | "customer_not_ready"
    | null;
};
