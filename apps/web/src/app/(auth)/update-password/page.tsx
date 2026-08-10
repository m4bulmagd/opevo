import { redirect } from "next/navigation";

import { AuthEntryShell } from "@/components/auth/auth-entry-shell";
import { PasswordUpdateControl, requirePasswordUpdateProvider } from "@/components/auth/password-controls";
import { getServerSessionState } from "@/lib/auth/server-session";

export default async function UpdatePasswordPage() {
  requirePasswordUpdateProvider();
  const session = await getServerSessionState();
  if (!session.isAuthenticated) {
    redirect("/sign-in");
  }
  return (
    <AuthEntryShell description="Choose a strong password for your Opevo account." title="Choose a new password">
      <PasswordUpdateControl />
    </AuthEntryShell>
  );
}
