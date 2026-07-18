import { redirect } from "next/navigation";

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
    <main className="flex min-h-screen items-center justify-center px-6 py-10">
      <SignUp forceRedirectUrl="/dashboard" signInUrl="/sign-in" />
    </main>
  );
}
