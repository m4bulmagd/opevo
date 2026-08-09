import "server-only";

import { auth } from "@clerk/nextjs/server";

import type { ServerSessionState } from "@/lib/auth/session-contract";

export async function getClerkServerSession(): Promise<ServerSessionState> {
  const session = await auth();

  return {
    isAuthenticated: Boolean(session.userId),
    getToken: session.getToken,
  };
}
