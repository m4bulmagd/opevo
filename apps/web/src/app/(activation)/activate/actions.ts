"use server";

import { revalidatePath } from "next/cache";

import { z } from "zod";

import {
  activateDevelopmentStarter,
  confirmProfile,
  confirmProvisioning,
  goLive,
  lookupCarrier,
  openVerificationWindow,
  retryProvisioning,
  saveBusinessProfile,
  simulateDevelopmentForwardedCall,
} from "@/lib/api/activation";
import { BackendApiError } from "@/lib/api/backend-client";
import { createCheckoutSession } from "@/lib/api/billing";
import { requireServerSession, ServerSessionRequiredError } from "@/lib/auth/server-session";
import { type DevelopmentCapabilities, getDevelopmentCapabilities } from "@/lib/development/capabilities";
import type { ActivationActionResult, BusinessProfile, CarrierLookupResponse } from "@/lib/types/activation";

const carrierSchema = z.enum(["orange", "sfr", "bouygues", "free", "other"]);
const faqSchema = z.object({ question: z.string(), answer: z.string() }).strict();
const openingIntervalSchema = z.object({ start: z.string(), end: z.string() }).strict();
const dayHoursSchema = z
  .object({
    closed: z.boolean(),
    intervals: z.array(openingIntervalSchema),
  })
  .strict();
const businessHoursSchema = z
  .object({
    monday: dayHoursSchema,
    tuesday: dayHoursSchema,
    wednesday: dayHoursSchema,
    thursday: dayHoursSchema,
    friday: dayHoursSchema,
    saturday: dayHoursSchema,
    sunday: dayHoursSchema,
  })
  .strict();
const nullableString = z.string().nullable().optional();
const businessProfileSchema = z
  .object({
    owner_name: nullableString,
    business_name: nullableString,
    business_type: nullableString,
    public_description: nullableString,
    timezone: nullableString,
    business_hours: businessHoursSchema.nullable().optional(),
    existing_phone_e164: nullableString,
    confirmed_carrier: carrierSchema.nullable().optional(),
    receptionist_name: nullableString,
    faqs: z.array(faqSchema).optional(),
    special_instructions: nullableString,
    escalation_notes: nullableString,
  })
  .strict();
const emptyCommandSchema = z.object({}).strict();

const SAFE_BACKEND_MESSAGES = {
  profile_unavailable: "Your profile is not ready yet. Refresh and try again.",
  profile_incomplete: "Complete the highlighted profile fields before continuing.",
  profile_projection_too_large: "Your receptionist details are too long. Shorten them and try again.",
  carrier_lookup_unavailable: "We couldn't check your carrier. Choose it manually to continue.",
  user_inactive: "This account is not active.",
  unsupported_country: "Number provisioning is currently available only in France.",
  phone_already_assigned: "A Presvo number is already assigned to this account.",
  provisioning_state_conflict: "Number setup changed. Refresh before trying again.",
  provisioning_retry_not_allowed: "Number provisioning cannot be retried right now.",
  profile_not_confirmed: "Confirm your profile before provisioning a number.",
  profile_confirmation_stale: "Your profile changed. Review and confirm it again.",
  subscription_missing: "Activate the starter plan before provisioning a number.",
  plan_unsupported: "The selected plan cannot be used for activation.",
  subscription_status_ineligible: "Your subscription is not eligible for activation.",
  subscription_period_missing: "Your billing period is not ready yet.",
  subscription_period_inactive: "Your billing period is not currently active.",
  minutes_exhausted: "Add available minutes before continuing.",
  phone_not_ready: "Your Presvo number is not ready yet.",
  provisioning_not_succeeded: "Number provisioning must finish before verification.",
  verification_window_already_open: "A verification window is already open.",
  verification_already_succeeded: "Call forwarding is already verified.",
  verification_window_not_found: "No verification window was found.",
  verification_window_not_open: "Start a verification window before simulating a forwarded call.",
  verification_window_already_claimed: "This verification call is already being processed.",
  verification_window_expired: "The verification window expired. Start a new test.",
  verification_session_not_found: "The verification call could not be found. Start a new test.",
  verification_session_not_claimed: "The verification call is not ready to complete.",
  verification_completion_expired: "The verification call finished too late. Start a new test.",
  verification_routing_stale: "Forwarding details changed. Start a new verification test.",
  go_live_blocked: "Presvo is not ready to go live. Review the remaining activation steps.",
  local_billing_disabled: "Local billing simulation is unavailable.",
  local_telephony_disabled: "Local forwarding simulation is unavailable.",
  user_unavailable: "The local account is unavailable.",
  local_subscription_unavailable: "The local starter plan could not be activated.",
  real_subscription_present: "Local billing cannot replace an existing subscription.",
} as const;

type SafeBackendCode = keyof typeof SAFE_BACKEND_MESSAGES;

function isSafeBackendCode(value: unknown): value is SafeBackendCode {
  return typeof value === "string" && value in SAFE_BACKEND_MESSAGES;
}

function invalidInputResult(error: z.ZodError): ActivationActionResult<never> {
  const fields = Array.from(
    new Set(error.issues.map((issue) => issue.path[0]).filter((field): field is string => typeof field === "string")),
  );

  return {
    status: "error",
    code: "invalid_input",
    message: "Check the highlighted fields and try again.",
    ...(fields.length > 0 ? { fields } : {}),
  };
}

function mapActionError(error: unknown): ActivationActionResult<never> {
  if (error instanceof ServerSessionRequiredError) {
    return {
      status: "error",
      code: "authentication_required",
      message: "Sign in before continuing.",
    };
  }

  if (error instanceof BackendApiError) {
    const detail = typeof error.detail === "object" ? error.detail : null;
    const code = detail?.code;

    if (isSafeBackendCode(code)) {
      const rawFields = detail?.fields;
      const fields = Array.isArray(rawFields)
        ? rawFields.filter((field): field is string => typeof field === "string")
        : [];

      return {
        status: "error",
        code,
        message: SAFE_BACKEND_MESSAGES[code],
        ...(code === "profile_incomplete" && fields.length > 0 ? { fields } : {}),
      };
    }

    const message =
      error.status === 503
        ? "This service is temporarily unavailable. Try again shortly."
        : "We couldn't complete this step. Refresh and try again.";
    return { status: "error", code: "request_failed", message };
  }

  return {
    status: "error",
    code: "unexpected_error",
    message: "Something went wrong. Refresh and try again.",
  };
}

function revalidateActivationPaths(): void {
  revalidatePath("/activate");
  revalidatePath("/dashboard");
}

type ExecuteActionOptions<TSchema extends z.ZodType, TResult> = {
  input: unknown;
  schema: TSchema;
  command: (input: z.infer<TSchema>) => Promise<TResult>;
  successMessage: string;
  capability?: keyof DevelopmentCapabilities;
};

async function executeActivationAction<TSchema extends z.ZodType, TResult>({
  input,
  schema,
  command,
  successMessage,
  capability,
}: ExecuteActionOptions<TSchema, TResult>): Promise<ActivationActionResult<TResult>> {
  try {
    await requireServerSession();
  } catch (error) {
    return mapActionError(error);
  }

  const parsed = schema.safeParse(input);
  if (!parsed.success) {
    return invalidInputResult(parsed.error);
  }

  try {
    if (capability && !getDevelopmentCapabilities()[capability]) {
      return {
        status: "error",
        code: "development_unavailable",
        message: "This local development action is unavailable.",
      };
    }

    const data = await command(parsed.data);
    revalidateActivationPaths();
    return { status: "success", data, message: successMessage };
  } catch (error) {
    return mapActionError(error);
  }
}

export async function saveBusinessProfileAction(input: unknown): Promise<ActivationActionResult<BusinessProfile>> {
  return executeActivationAction({
    input,
    schema: businessProfileSchema,
    command: saveBusinessProfile,
    successMessage: "Profile saved.",
  });
}

export async function lookupCarrierAction(input: unknown = {}): Promise<ActivationActionResult<CarrierLookupResponse>> {
  return executeActivationAction({
    input,
    schema: emptyCommandSchema,
    command: lookupCarrier,
    successMessage: "Carrier check complete.",
  });
}

export async function confirmProfileAction(input: unknown = {}): Promise<ActivationActionResult> {
  return executeActivationAction({
    input,
    schema: emptyCommandSchema,
    command: confirmProfile,
    successMessage: "Profile confirmed.",
  });
}

export async function confirmProvisioningAction(input: unknown = {}): Promise<ActivationActionResult> {
  return executeActivationAction({
    input,
    schema: emptyCommandSchema,
    command: confirmProvisioning,
    successMessage: "Number provisioning started.",
  });
}

export async function retryProvisioningAction(input: unknown = {}): Promise<ActivationActionResult> {
  return executeActivationAction({
    input,
    schema: emptyCommandSchema,
    command: retryProvisioning,
    successMessage: "Number provisioning retry started.",
  });
}

export async function openVerificationWindowAction(input: unknown = {}): Promise<ActivationActionResult> {
  return executeActivationAction({
    input,
    schema: emptyCommandSchema,
    command: openVerificationWindow,
    successMessage: "Forwarding verification started.",
  });
}

export async function goLiveAction(input: unknown = {}): Promise<ActivationActionResult> {
  return executeActivationAction({
    input,
    schema: emptyCommandSchema,
    command: goLive,
    successMessage: "Go-live started.",
  });
}

export async function activateDevelopmentStarterAction(input: unknown = {}): Promise<ActivationActionResult> {
  return executeActivationAction({
    input,
    schema: emptyCommandSchema,
    command: activateDevelopmentStarter,
    successMessage: "Local starter plan activated.",
    capability: "localBilling",
  });
}

export async function createActivationCheckoutAction(
  input: unknown = {},
): Promise<ActivationActionResult<{ url: string }>> {
  try {
    await requireServerSession();
  } catch (error) {
    return mapActionError(error);
  }

  const parsed = emptyCommandSchema.safeParse(input);
  if (!parsed.success) return invalidInputResult(parsed.error);

  try {
    const session = await createCheckoutSession("starter");
    const checkoutUrl = new URL(session.url);
    if (checkoutUrl.protocol !== "https:") throw new Error("Checkout URL must use HTTPS.");
    return {
      status: "success",
      data: { url: checkoutUrl.toString() },
      message: "Checkout session created.",
    };
  } catch (error) {
    return mapActionError(error);
  }
}

export async function simulateDevelopmentForwardedCallAction(input: unknown = {}): Promise<ActivationActionResult> {
  return executeActivationAction({
    input,
    schema: emptyCommandSchema,
    command: simulateDevelopmentForwardedCall,
    successMessage: "Local forwarded call verified.",
    capability: "localVerification",
  });
}
