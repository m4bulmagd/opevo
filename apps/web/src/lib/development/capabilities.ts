import "server-only";

import { authProvider } from "@/lib/auth/auth-config";

export type DevelopmentCapabilities = {
  localBilling: boolean;
  localVerification: boolean;
};

export function getDevelopmentCapabilities(): DevelopmentCapabilities {
  const localDevelopment = process.env.NODE_ENV === "development" && authProvider === "local";

  return {
    localBilling: localDevelopment && process.env.BILLING_MODE === "fake",
    localVerification: localDevelopment && process.env.TELEPHONY_MODE === "fake",
  };
}
