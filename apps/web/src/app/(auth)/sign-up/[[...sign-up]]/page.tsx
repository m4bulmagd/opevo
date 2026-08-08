import { redirect } from "next/navigation";

import { AuthEntryShell, OPEVO_CLERK_APPEARANCE } from "@/components/auth/auth-entry-shell";
import { authMode } from "@/lib/auth/clerk-config";

export default async function SignUpPage() {
  if (authMode === "local") {
    redirect("/activate");
  }

  const { SignUp } = await import("@clerk/nextjs");

  return (
    <AuthEntryShell
      description="Create your account, then configure your France-first missed-call receptionist."
      title="Create your Opevo account"
    >
      <SignUp appearance={OPEVO_CLERK_APPEARANCE} forceRedirectUrl="/dashboard" signInUrl="/sign-in" />
    </AuthEntryShell>
  );
}
