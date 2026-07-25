"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { z } from "zod";

import { deactivateAccount as requestAccountDeactivation } from "@/lib/api/account";
import { activateDevelopmentStarter } from "@/lib/api/activation";
import { BackendApiError } from "@/lib/api/backend-client";
import { createCheckoutSession } from "@/lib/api/billing";
import { requireServerSession, ServerSessionRequiredError } from "@/lib/auth/server-session";
import { getDevelopmentCapabilities } from "@/lib/development/capabilities";

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

const confirmationSchema = z.literal("DEACTIVATE");

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

function isSafeHostedUrl(value: string): boolean {
  try {
    return new URL(value).protocol === "https:";
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
    if (!isSafeHostedUrl(session.url)) {
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
