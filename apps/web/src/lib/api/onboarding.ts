import { backendFetch } from "@/lib/api/backend-client";
import type { OnboardingStatus } from "@/lib/types/onboarding";

export async function getOnboardingStatus() {
  return backendFetch<OnboardingStatus>("/api/onboarding");
}
