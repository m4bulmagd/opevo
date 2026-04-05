"use server";

import { BackendApiError } from "@/lib/api/backend-client";
import { createCheckoutSession, createPortalSession } from "@/lib/api/billing";

type HostedActionResult = {
  status: "success" | "error";
  message: string;
  url?: string;
};

export async function createCheckoutSessionAction(planTier: "starter" | "standard"): Promise<HostedActionResult> {
  try {
    const session = await createCheckoutSession(planTier);
    return {
      status: "success",
      message: "Checkout session created.",
      url: session.url,
    };
  } catch (error) {
    if (error instanceof BackendApiError) {
      return {
        status: "error",
        message: error.message,
      };
    }

    return {
      status: "error",
      message: "Unexpected error while creating checkout session.",
    };
  }
}

export async function createPortalSessionAction(): Promise<HostedActionResult> {
  const appUrl = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";

  try {
    const session = await createPortalSession(`${appUrl}/dashboard/billing`);
    return {
      status: "success",
      message: "Billing portal session created.",
      url: session.url,
    };
  } catch (error) {
    if (error instanceof BackendApiError) {
      return {
        status: "error",
        message: error.message,
      };
    }

    return {
      status: "error",
      message: "Unexpected error while creating billing portal session.",
    };
  }
}
