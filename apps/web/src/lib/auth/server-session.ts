import "server-only";

import { authMode } from "@/lib/auth/clerk-config";

const LOCAL_USER_ID = "local_presvo_user";

export class ServerSessionRequiredError extends Error {
  constructor() {
    super("An authenticated server session is required");
    this.name = "ServerSessionRequiredError";
  }
}

export async function getServerSessionState() {
  if (authMode === "local") {
    const token = process.env.LOCAL_AUTH_TOKEN;

    if (!token || token !== token.trim()) {
      throw new Error("LOCAL_AUTH_TOKEN is required when AUTH_MODE=local");
    }

    return {
      isAuthenticated: true,
      userId: LOCAL_USER_ID,
      getToken: async () => token,
    };
  }

  const { auth } = await import("@clerk/nextjs/server");
  const session = await auth();

  return {
    isAuthenticated: Boolean(session.userId),
    userId: session.userId,
    getToken: session.getToken,
  };
}

export async function requireServerSession() {
  const session = await getServerSessionState();

  if (!session.isAuthenticated || !session.userId) {
    throw new ServerSessionRequiredError();
  }

  const token = await session.getToken();

  if (!token) {
    throw new ServerSessionRequiredError();
  }

  return { userId: session.userId, token };
}
