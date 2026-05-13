export type OnboardingPhoneNumberStatus = "missing" | "provisioning" | "ready" | "failed";

export type OnboardingOverallStatus =
  | "not_subscribed"
  | "subscription_active"
  | "provisioning_number"
  | "setup_required"
  | "ready_to_enable"
  | "live"
  | "provisioning_failed";

export type OnboardingStatus = {
  subscription_status: string | null;
  plan_tier: string | null;
  minutes_remaining: number;
  phone_number: string | null;
  phone_number_status: OnboardingPhoneNumberStatus;
  routing_enabled: boolean;
  agent_setup_complete: boolean;
  overall_status: OnboardingOverallStatus;
  can_retry_provisioning: boolean;
};

export type RetryProvisioningResponse = {
  status: "accepted";
  queued: boolean;
};
