"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { z } from "zod";

import { isAccountTimezoneAllowed } from "@/lib/account-timezone";
import { deactivateAccount as requestAccountDeactivation } from "@/lib/api/account";
import { activateDevelopmentStarter, getActivationSnapshot, saveBusinessProfile } from "@/lib/api/activation";
import { BackendApiError } from "@/lib/api/backend-client";
import { createCheckoutSession } from "@/lib/api/billing";
import { requireServerSession, ServerSessionRequiredError } from "@/lib/auth/server-session";
import { getDevelopmentCapabilities } from "@/lib/development/capabilities";
import { normalizeFrenchNumber } from "@/lib/phone-numbers";
import type { AccountProfileValues } from "@/lib/types/account-settings";
import type { ActivationSnapshot, BusinessProfileDraft } from "@/lib/types/activation";

export type ActionResult =
  | {
      status: "success";
      message: string;
    }
  | {
      status: "error";
      code: string;
      message: string;
    };

export type HostedActionResult =
  | {
      status: "success";
      message: string;
      url: string;
    }
  | {
      status: "error";
      code: string;
      message: string;
    };

export type AccountProfileActionResult =
  | {
      status: "success";
      message: string;
      profile: AccountProfileValues;
    }
  | {
      status: "error";
      code: string;
      message: string;
      fields?: Array<keyof AccountProfileValues>;
    };

const confirmationSchema = z.literal("DEACTIVATE");
const accountProfileSchema = z
  .object({
    owner_name: z.string().trim().min(1),
    business_name: z.string().trim().min(1),
    existing_phone_e164: z.string().trim().min(1),
    timezone: z.string().trim().min(1),
  })
  .strict();
const STRIPE_CHECKOUT_ORIGIN = "https://checkout.stripe.com";
const TEST_STRIPE_CHECKOUT_ORIGIN = "https://checkout.stripe.test";

const SAFE_ACCOUNT_MESSAGES = {
  account_deactivating: "Account deactivation is already in progress.",
  account_inactive: "This account is already inactive.",
  deactivation_attention_required: "Account deactivation still needs time to finish.",
  reactivation_not_ready: "Reactivation is not ready yet. Refresh and try again.",
  customer_not_ready: "This account is not ready for that action.",
} as const;

type SafeAccountCode = keyof typeof SAFE_ACCOUNT_MESSAGES;

function safeBackendCode(error: BackendApiError): SafeAccountCode | null {
  if (typeof error.detail !== "object") {
    return null;
  }

  const code = error.detail.code;
  return typeof code === "string" && code in SAFE_ACCOUNT_MESSAGES ? (code as SafeAccountCode) : null;
}

function mapAccountActionError(error: unknown): Extract<ActionResult, { status: "error" }> {
  if (error instanceof ServerSessionRequiredError) {
    return {
      status: "error",
      code: "authentication_required",
      message: "Sign in before continuing.",
    };
  }

  if (error instanceof BackendApiError) {
    const code = safeBackendCode(error);
    if (code) {
      return {
        status: "error",
        code,
        message: SAFE_ACCOUNT_MESSAGES[code],
      };
    }

    if (error.status === 422) {
      return {
        status: "error",
        code: "invalid_confirmation",
        message: "Type DEACTIVATE exactly to confirm.",
      };
    }

    if (error.status === 429) {
      return {
        status: "error",
        code: "rate_limited",
        message: "Too many attempts. Wait a moment and try again.",
      };
    }

    if (error.status === 409) {
      return {
        status: "error",
        code: "account_state_conflict",
        message: "The account state changed. Refresh and try again.",
      };
    }

    if (error.status === 503) {
      return {
        status: "error",
        code: "service_unavailable",
        message: "Account service is temporarily unavailable. Try again shortly.",
      };
    }

    return {
      status: "error",
      code: "request_failed",
      message: "We couldn't complete this account request. Refresh and try again.",
    };
  }

  return {
    status: "error",
    code: "unexpected_error",
    message: "Something went wrong. Refresh and try again.",
  };
}

function revalidateAccountPaths(): void {
  revalidatePath("/dashboard");
  revalidatePath("/dashboard/account");
  revalidatePath("/dashboard/agent");
  revalidatePath("/dashboard/billing");
  revalidatePath("/activate");
}

function invalidProfileInput(fields?: Array<keyof AccountProfileValues>): AccountProfileActionResult {
  return {
    status: "error",
    code: "invalid_input",
    message: "Review your profile details and try again.",
    ...(fields ? { fields } : {}),
  };
}

export async function saveAccountProfileAction(input: unknown): Promise<AccountProfileActionResult> {
  try {
    await requireServerSession();
  } catch (error) {
    return mapAccountActionError(error);
  }

  const parsed = accountProfileSchema.safeParse(input);
  if (!parsed.success) {
    return invalidProfileInput();
  }

  const normalizedPhone = normalizeFrenchNumber(parsed.data.existing_phone_e164);
  if (!normalizedPhone) {
    return invalidProfileInput(["existing_phone_e164"]);
  }

  let snapshot: ActivationSnapshot;
  try {
    snapshot = await getActivationSnapshot();
  } catch {
    return {
      status: "error",
      code: "profile_unavailable",
      message: "Your profile is temporarily unavailable. Try saving again.",
    };
  }

  if (!isAccountTimezoneAllowed(parsed.data.timezone, snapshot.profile.timezone)) {
    return invalidProfileInput(["timezone"]);
  }

  const fields = (["owner_name", "business_name"] as const).filter(
    (field) => parsed.data[field].length > snapshot.profile_constraints.name_max_length,
  );
  if (fields.length > 0) {
    return invalidProfileInput(fields);
  }

  const completeDraft: BusinessProfileDraft = {
    owner_name: parsed.data.owner_name,
    business_name: parsed.data.business_name,
    business_type: snapshot.profile.business_type,
    public_description: snapshot.profile.public_description,
    timezone: parsed.data.timezone,
    business_hours: snapshot.profile.business_hours,
    existing_phone_e164: normalizedPhone,
    confirmed_carrier: snapshot.profile.confirmed_carrier,
    receptionist_name: snapshot.profile.receptionist_name,
    faqs: snapshot.profile.faqs.map((faq) => ({ ...faq })),
    special_instructions: snapshot.profile.special_instructions,
    escalation_notes: snapshot.profile.escalation_notes,
  };

  try {
    await saveBusinessProfile(completeDraft);
  } catch {
    return {
      status: "error",
      code: "request_failed",
      message: "We couldn't save your profile. Try saving again.",
    };
  }

  revalidateAccountPaths();
  return {
    status: "success",
    message: "Profile saved.",
    profile: {
      owner_name: parsed.data.owner_name,
      business_name: parsed.data.business_name,
      existing_phone_e164: normalizedPhone,
      timezone: parsed.data.timezone,
    },
  };
}

function isTrustedStripeCheckoutUrl(value: string): boolean {
  try {
    const checkoutUrl = new URL(value);
    if (checkoutUrl.username || checkoutUrl.password || checkoutUrl.port) {
      return false;
    }

    if (checkoutUrl.origin === STRIPE_CHECKOUT_ORIGIN) {
      return true;
    }

    return process.env.NODE_ENV === "test" && checkoutUrl.origin === TEST_STRIPE_CHECKOUT_ORIGIN;
  } catch {
    return false;
  }
}

export async function deactivateAccount(confirmation: string): Promise<ActionResult> {
  try {
    await requireServerSession();
  } catch (error) {
    return mapAccountActionError(error);
  }

  const parsed = confirmationSchema.safeParse(confirmation);
  if (!parsed.success) {
    return {
      status: "error",
      code: "invalid_confirmation",
      message: "Type DEACTIVATE exactly to confirm.",
    };
  }

  try {
    await requestAccountDeactivation(parsed.data);
  } catch (error) {
    return mapAccountActionError(error);
  }

  revalidateAccountPaths();
  redirect("/dashboard/account");
}

export async function reactivateAccount(): Promise<HostedActionResult> {
  try {
    await requireServerSession();
  } catch (error) {
    return mapAccountActionError(error);
  }

  try {
    if (getDevelopmentCapabilities().localBilling) {
      await activateDevelopmentStarter();
      revalidateAccountPaths();
      return {
        status: "success",
        message: "Your starter plan is ready. Continue activation.",
        url: "/activate",
      };
    }

    const session = await createCheckoutSession("starter");
    if (!isTrustedStripeCheckoutUrl(session.url)) {
      return {
        status: "error",
        code: "request_failed",
        message: "We couldn't open checkout. Refresh and try again.",
      };
    }

    return {
      status: "success",
      message: "Checkout is ready.",
      url: session.url,
    };
  } catch (error) {
    return mapAccountActionError(error);
  }
}
