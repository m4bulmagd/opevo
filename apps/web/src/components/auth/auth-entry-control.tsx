import { redirect } from "next/navigation";

import { authProvider } from "@/lib/auth/auth-config";
import { ClerkSignInEntry, ClerkSignUpEntry } from "@/lib/auth/providers/clerk/auth-entry";
import { SupabaseAuthForm } from "@/lib/auth/providers/supabase/auth-form";

export function SignInControl() {
  return authProvider === "supabase" ? <SupabaseAuthForm mode="sign-in" /> : <ClerkSignInEntry />;
}

export function SignUpControl() {
  return authProvider === "supabase" ? <SupabaseAuthForm mode="sign-up" /> : <ClerkSignUpEntry />;
}

export function requireHostedAuthEntry() {
  if (authProvider === "local") {
    redirect("/activate");
  }
}
