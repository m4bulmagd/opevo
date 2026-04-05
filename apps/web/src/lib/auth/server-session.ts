import { isClerkConfigured } from "@/lib/auth/clerk-config";

export async function getServerSessionState() {
  if (!isClerkConfigured) {
    return {
      isAuthenticated: false,
      userId: null,
      getToken: async () => null,
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
