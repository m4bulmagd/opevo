"use server";

import { revalidatePath } from "next/cache";

import { retryProvisioning } from "@/lib/api/activation";
import { BackendApiError } from "@/lib/api/backend-client";

export type RetryProvisioningActionResult = {
  status: "success" | "error";
  message: string;
};

export async function retryProvisioningAction(): Promise<RetryProvisioningActionResult> {
  try {
    await retryProvisioning();
    revalidatePath("/dashboard");

    return {
      status: "success",
      message: "Provisioning retry queued. Check back shortly.",
    };
  } catch (error) {
    if (error instanceof BackendApiError) {
      if (error.status === 409) {
        return {
          status: "error",
          message: "Provisioning retry is not available right now.",
        };
      }

      return {
        status: "error",
        message: error.message,
      };
    }

    return {
      status: "error",
      message: "Unexpected error while retrying provisioning.",
    };
  }
}
