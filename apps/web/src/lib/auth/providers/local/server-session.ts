import "server-only";

import type { ServerSessionState } from "@/lib/auth/session-contract";

export async function getLocalServerSession(): Promise<ServerSessionState> {
  const token = process.env.LOCAL_AUTH_TOKEN;

  if (!token || token !== token.trim()) {
    throw new Error("LOCAL_AUTH_TOKEN is required when AUTH_PROVIDER=local");
  }

  return {
    isAuthenticated: true,
    getToken: async () => token,
  };
}
