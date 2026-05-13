import { backendFetch } from "@/lib/api/backend-client";
import type { OnboardingStatus, RetryProvisioningResponse } from "@/lib/types/onboarding";

export async function getOnboardingStatus() {
  return backendFetch<OnboardingStatus>("/api/onboarding");
}

export async function retryProvisioning() {
  return backendFetch<RetryProvisioningResponse>("/api/onboarding/retry-provisioning", {
    method: "POST",
  });
}
