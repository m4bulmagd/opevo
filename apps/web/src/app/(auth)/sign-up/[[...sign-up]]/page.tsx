import { requireHostedAuthEntry, SignUpControl } from "@/components/auth/auth-entry-control";
import { AuthEntryShell } from "@/components/auth/auth-entry-shell";

export default async function SignUpPage() {
  requireHostedAuthEntry();
  return (
    <AuthEntryShell
      description="Create your account, then configure your France-first missed-call receptionist."
      title="Create your Opevo account"
    >
      <SignUpControl />
    </AuthEntryShell>
  );
}
