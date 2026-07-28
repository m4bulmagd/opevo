import { redirect } from "next/navigation";

import { AuthEntryShell, PRESVO_CLERK_APPEARANCE } from "@/components/auth/auth-entry-shell";
import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { authMode, isAppAuthConfigured } from "@/lib/auth/clerk-config";

export default async function SignInPage() {
  if (authMode === "local") {
    redirect("/activate");
  }

  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Sign-in is unavailable"
        description="Configure Clerk in your local environment before using the hosted sign-in flow."
      />
    );
  }

  const { SignIn } = await import("@clerk/nextjs");

  return (
    <AuthEntryShell description="Continue to your calls, receptionist settings, and account." title="Welcome back">
      <SignIn appearance={PRESVO_CLERK_APPEARANCE} forceRedirectUrl="/dashboard" signUpUrl="/sign-up" />
    </AuthEntryShell>
  );
}
