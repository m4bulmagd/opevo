import { SignIn, SignUp } from "@clerk/nextjs";

import { OPEVO_CLERK_APPEARANCE } from "@/lib/auth/providers/clerk/appearance";

export function ClerkSignInEntry() {
  return <SignIn appearance={OPEVO_CLERK_APPEARANCE} forceRedirectUrl="/dashboard" signUpUrl="/sign-up" />;
}

export function ClerkSignUpEntry() {
  return <SignUp appearance={OPEVO_CLERK_APPEARANCE} forceRedirectUrl="/dashboard" signInUrl="/sign-in" />;
}
