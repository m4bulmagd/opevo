import { redirect } from "next/navigation";

import { getServerSessionState } from "@/lib/auth/server-session";

export default async function Page() {
  const session = await getServerSessionState();

  redirect(session.isAuthenticated ? "/dashboard" : "/sign-in");
}
