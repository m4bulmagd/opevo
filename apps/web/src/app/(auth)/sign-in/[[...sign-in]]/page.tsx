import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { isClerkConfigured } from "@/lib/auth/clerk-config";

export default async function SignInPage() {
  if (!isClerkConfigured) {
    return (
      <ClerkSetupNotice
        title="Sign-in is unavailable"
        description="Configure Clerk in your local environment before using the hosted sign-in flow."
      />
    );
  }

  const { SignIn } = await import("@clerk/nextjs");

  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-10">
      <SignIn forceRedirectUrl="/dashboard" signUpUrl="/sign-up" />
    </main>
  );
}
