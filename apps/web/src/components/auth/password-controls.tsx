import { redirect } from "next/navigation";

import { authProvider } from "@/lib/auth/auth-config";
import { SupabasePasswordRecoveryForm, SupabaseUpdatePasswordForm } from "@/lib/auth/providers/supabase/password-forms";

export function PasswordRecoveryControl() {
  if (authProvider !== "supabase") {
    redirect("/sign-in");
  }
  return <SupabasePasswordRecoveryForm />;
}

export function requirePasswordUpdateProvider() {
  if (authProvider !== "supabase") {
    redirect("/dashboard/account");
  }
}

export function PasswordUpdateControl() {
  requirePasswordUpdateProvider();
  return <SupabaseUpdatePasswordForm />;
}
