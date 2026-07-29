import "server-only";

import { shouldWrapClerk } from "@/lib/auth/clerk-config";
import type { AccountIdentity } from "@/lib/types/account-settings";

export async function resolveAccountIdentity(): Promise<AccountIdentity> {
  if (!shouldWrapClerk) {
    return { email: null, securityMode: "unavailable" };
  }

  try {
    const { currentUser } = await import("@clerk/nextjs/server");
    const user = await currentUser();
    const email = user?.primaryEmailAddress?.emailAddress ?? user?.emailAddresses[0]?.emailAddress ?? null;
    return { email, securityMode: "clerk" };
  } catch {
    return { email: null, securityMode: "clerk" };
  }
}
