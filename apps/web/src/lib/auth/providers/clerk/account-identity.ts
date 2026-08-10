import "server-only";

import { currentUser } from "@clerk/nextjs/server";

import type { AccountIdentity } from "@/lib/types/account-settings";

export async function resolveClerkAccountIdentity(): Promise<AccountIdentity> {
  try {
    const user = await currentUser();
    const email = user?.primaryEmailAddress?.emailAddress ?? user?.emailAddresses[0]?.emailAddress ?? null;
    return { email, securityMode: "managed" };
  } catch {
    return { email: null, securityMode: "managed" };
  }
}
