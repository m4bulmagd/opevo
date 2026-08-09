import "server-only";

import { authProvider } from "@/lib/auth/auth-config";
import { getClerkServerSession } from "@/lib/auth/providers/clerk/server-session";
import { getLocalServerSession } from "@/lib/auth/providers/local/server-session";
import { getSupabaseServerSession } from "@/lib/auth/providers/supabase/server-session";

export class ServerSessionRequiredError extends Error {
  constructor() {
    super("An authenticated server session is required");
    this.name = "ServerSessionRequiredError";
  }
}

export async function getServerSessionState() {
  if (authProvider === "local") {
    return getLocalServerSession();
  }
  if (authProvider === "supabase") {
    return getSupabaseServerSession();
  }
  return getClerkServerSession();
}

export async function requireServerSession() {
  const session = await getServerSessionState();

  if (!session.isAuthenticated) {
    throw new ServerSessionRequiredError();
  }

  const token = await session.getToken();

  if (!token) {
    throw new ServerSessionRequiredError();
  }

  return { token };
}
