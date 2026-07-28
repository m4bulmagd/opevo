import { redirect } from "next/navigation";

import { AuthEntryShell, PRESVO_CLERK_APPEARANCE } from "@/components/auth/auth-entry-shell";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { authMode, isAppAuthConfigured } from "@/lib/auth/clerk-config";

export default async function SignUpPage() {
  if (authMode === "local") {
    redirect("/activate");
  }

  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Sign-up is unavailable"
        description="Configure Clerk in your local environment before using the hosted sign-up flow."
      />
    );
  }

  const { SignUp } = await import("@clerk/nextjs");

  return (
    <AuthEntryShell
      description="Create your account, then configure your France-first missed-call receptionist."
      title="Create your Presvo account"
    >
      <SignUp appearance={PRESVO_CLERK_APPEARANCE} forceRedirectUrl="/dashboard" signInUrl="/sign-in" />
    </AuthEntryShell>
  );
}
