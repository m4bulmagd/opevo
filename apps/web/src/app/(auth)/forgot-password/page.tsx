import { AuthEntryShell } from "@/components/auth/auth-entry-shell";
import { PasswordRecoveryControl } from "@/components/auth/password-controls";

export default function ForgotPasswordPage() {
  return (
    <AuthEntryShell description="We'll email you a secure link to choose a new password." title="Reset your password">
      <PasswordRecoveryControl />
    </AuthEntryShell>
  );
}
