export type ActivationStage =
  | "profile_required"
  | "payment_required"
  | "provisioning_consent_required"
  | "provisioning"
  | "provisioning_failed"
  | "forwarding_required"
  | "verification_window_open"
  | "ready_to_activate"
  | "activating"
  | "runtime_paused"
  | "active";

export type CarrierCode = "orange" | "sfr" | "bouygues" | "free" | "other";
export type Weekday = "monday" | "tuesday" | "wednesday" | "thursday" | "friday" | "saturday" | "sunday";

export type OpeningInterval = {
  start: string;
  end: string;
};

export type DayHours = {
  closed: boolean;
  intervals: OpeningInterval[];
};

export type BusinessHours = Record<Weekday, DayHours>;

export type FaqItem = {
  question: string;
  answer: string;
};

export type BusinessProfileDraft = {
  owner_name?: string | null;
  business_name?: string | null;
  business_type?: string | null;
  public_description?: string | null;
  timezone?: string | null;
  business_hours?: BusinessHours | null;
  existing_phone_e164?: string | null;
  confirmed_carrier?: CarrierCode | null;
  receptionist_name?: string | null;
  faqs?: FaqItem[];
  special_instructions?: string | null;
  escalation_notes?: string | null;
};

export type BusinessProfile = {
  owner_name: string | null;
  business_name: string | null;
  business_type: string | null;
  public_description: string | null;
  timezone: string | null;
  business_hours: BusinessHours | null;
  existing_phone_e164: string | null;
  confirmed_carrier: CarrierCode | null;
  receptionist_name: string | null;
  faqs: FaqItem[];
  special_instructions: string | null;
  escalation_notes: string | null;
  detected_carrier: string | null;
  detected_number_type: string | null;
  carrier_lookup_status: string | null;
  carrier_looked_up_at: string | null;
  content_revision: number;
  routing_revision: number;
};

export type BusinessProfileConstraints = {
  name_max_length: number;
  business_type_max_length: number;
  public_description_max_length: number;
  faq_max_items: number;
  faq_question_max_length: number;
  faq_answer_max_length: number;
  special_instructions_max_length: number;
  escalation_notes_max_length: number;
  max_intervals_per_day: number;
  phone_country: "FR";
};

export type CarrierLookupResponse = {
  normalized_number: string;
  country_code: string;
  carrier_name: string | null;
  normalized_carrier: CarrierCode;
  number_type: string | null;
  looked_up_at: string;
};

export type ForwardingCondition = "unanswered" | "busy" | "unreachable";

export type ForwardingStep = {
  condition: ForwardingCondition;
  title: string;
  instructions: string[];
  dial_code: string | null;
  disable_code: string | null;
  source_url: string | null;
};

export type ForwardingGuide = {
  version: string;
  carrier: CarrierCode;
  number_type: string | null;
  opevo_number: string;
  warning: string;
  steps: ForwardingStep[];
};

export type ActivationProgress = {
  profile_confirmed_at: string | null;
  provisioning_consented_at: string | null;
  verification_window_started_at: string | null;
  verification_window_expires_at: string | null;
  verification_status: "not_started" | "open" | "claimed" | "succeeded" | "failed" | "expired" | "invalidated";
  forwarding_verified_at: string | null;
  go_live_approved_at: string | null;
  activated_at: string | null;
  last_failure_code: string | null;
};

export type ActivationBilling = {
  eligible: boolean;
  plan_tier: string | null;
  subscription_status: string | null;
  allocated_minutes: number;
  minutes_remaining: number;
  current_period_start: string | null;
  current_period_end: string | null;
};

export type ActivationNumber = {
  assigned_e164: string | null;
  country_code: string | null;
  provider_ready: boolean;
  provisioning_status: string | null;
  can_retry: boolean;
};

export type CustomerReadinessStage =
  | "subscription_required"
  | "number_provisioning"
  | "number_provisioning_failed"
  | "receptionist_setup_required"
  | "ready"
  | "routing_pending"
  | "live"
  | "suspended";

export type RuntimeReadiness = {
  stage: CustomerReadinessStage;
  can_provision_number: boolean;
  can_activate: boolean;
  should_enable_phone: boolean;
  can_route: boolean;
  blockers: string[];
  warnings: string[];
  policy_version: string;
};

export type ActivationSnapshot = {
  workflow_version: number;
  stage: ActivationStage;
  completed_milestones: string[];
  next_action: string | null;
  blockers: string[];
  warnings: string[];
  profile: BusinessProfile;
  profile_constraints: BusinessProfileConstraints;
  activation: ActivationProgress;
  billing: ActivationBilling;
  number: ActivationNumber;
  forwarding: ForwardingGuide | null;
  runtime_readiness: RuntimeReadiness;
  evaluated_at: string;
};

export type ActivationActionResult<T = ActivationSnapshot> =
  | { status: "success"; data: T; message: string }
  | { status: "error"; code: string; message: string; fields?: string[] };
