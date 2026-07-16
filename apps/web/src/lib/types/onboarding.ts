export type OnboardingPhoneNumberStatus = "missing" | "provisioning" | "ready" | "failed";

export type CustomerReadinessStage =
  | "subscription_required"
  | "number_provisioning"
  | "number_provisioning_failed"
  | "receptionist_setup_required"
  | "ready"
  | "routing_pending"
  | "live"
  | "suspended";

export type ReadinessBlocker =
  | "user_inactive"
  | "subscription_missing"
  | "plan_unsupported"
  | "subscription_status_ineligible"
  | "subscription_period_missing"
  | "subscription_period_inactive"
  | "minutes_exhausted"
  | "phone_missing"
  | "phone_provider_id_missing"
  | "agent_config_missing"
  | "agent_setup_incomplete"
  | "agent_content_invalid"
  | "agent_disabled"
  | "phone_inactive"
  | "phone_projection_inactive";

export type OnboardingStatus = {
  subscription_status: string | null;
  plan_tier: string | null;
  minutes_remaining: number;
  phone_number: string | null;
  phone_number_status: OnboardingPhoneNumberStatus;
  agent_setup_complete: boolean;
  can_retry_provisioning: boolean;
  stage: CustomerReadinessStage;
  can_activate: boolean;
  can_route: boolean;
  blockers: ReadinessBlocker[];
  warnings: string[];
  evaluated_at: string;
  policy_version: string;
};

export type RetryProvisioningResponse = {
  status: "accepted";
  queued: boolean;
};
