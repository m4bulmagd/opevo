import { redirect } from "next/navigation";

import { AuthEntryShell, PRESVO_CLERK_APPEARANCE } from "@/components/auth/auth-entry-shell";
import { authMode } from "@/lib/auth/clerk-config";

export default async function SignInPage() {
  if (authMode === "local") {
    redirect("/activate");
  }

  const { SignIn } = await import("@clerk/nextjs");

  return (
    <AuthEntryShell description="Continue to your calls, receptionist settings, and account." title="Welcome back">
      <SignIn appearance={PRESVO_CLERK_APPEARANCE} forceRedirectUrl="/dashboard" signUpUrl="/sign-up" />
    </AuthEntryShell>
  );
}
