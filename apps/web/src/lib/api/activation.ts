import { backendFetch } from "@/lib/api/backend-client";
import type {
  ActivationSnapshot,
  BusinessProfile,
  BusinessProfileDraft,
  CarrierLookupResponse,
} from "@/lib/types/activation";

const postActivationCommand = (path: string) =>
  backendFetch<ActivationSnapshot>(path, {
    method: "POST",
  });

export const getActivationSnapshot = () => backendFetch<ActivationSnapshot>("/api/activation");

export const saveBusinessProfile = (draft: BusinessProfileDraft) =>
  backendFetch<BusinessProfile>("/api/business-profile", {
    method: "PUT",
    body: JSON.stringify(draft),
  });

export const lookupCarrier = () =>
  backendFetch<CarrierLookupResponse>("/api/activation/lookup-carrier", {
    method: "POST",
  });

export const confirmProfile = () => postActivationCommand("/api/activation/confirm-profile");
export const confirmProvisioning = () => postActivationCommand("/api/activation/confirm-provisioning");
export const retryProvisioning = () => postActivationCommand("/api/activation/retry-provisioning");
export const openVerificationWindow = () => postActivationCommand("/api/activation/open-verification-window");
export const goLive = () => postActivationCommand("/api/activation/go-live");
export const activateDevelopmentStarter = () => postActivationCommand("/api/development/activate-starter");
export const simulateDevelopmentForwardedCall = () => postActivationCommand("/api/development/simulate-forwarded-call");
