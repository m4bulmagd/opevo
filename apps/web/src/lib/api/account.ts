import { cache } from "react";

import { backendFetch } from "@/lib/api/backend-client";
import type { AccountStatus } from "@/lib/types/account";

export const getAccount = cache(() => backendFetch<AccountStatus>("/api/account"));

export function deactivateAccount(confirmation: string): Promise<AccountStatus> {
  return backendFetch<AccountStatus>("/api/account/deactivate", {
    method: "POST",
    body: JSON.stringify({ confirmation }),
  });
}
