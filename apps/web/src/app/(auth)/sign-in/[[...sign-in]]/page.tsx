import { requireHostedAuthEntry, SignInControl } from "@/components/auth/auth-entry-control";
import { AuthEntryShell } from "@/components/auth/auth-entry-shell";

export default async function SignInPage() {
  requireHostedAuthEntry();
  return (
    <AuthEntryShell description="Continue to your calls, receptionist settings, and account." title="Welcome back">
      <SignInControl />
    </AuthEntryShell>
  );
}
