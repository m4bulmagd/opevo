"use client";

import { type FormEvent, useState } from "react";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { createSupabaseBrowserClient } from "@/lib/auth/providers/supabase/browser-client";

type SupabaseAuthFormProps = Readonly<{ mode: "sign-in" | "sign-up" }>;

export function SupabaseAuthForm({ mode }: SupabaseAuthFormProps) {
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const router = useRouter();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    setPending(true);
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "");
    const password = String(form.get("password") ?? "");
    const supabase = createSupabaseBrowserClient();
    const result =
      mode === "sign-in"
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({
            email,
            password,
            options: { emailRedirectTo: `${window.location.origin}/auth/callback?next=/dashboard` },
          });

    if (result.error) {
      setError(isSignIn ? "Unable to sign in with those credentials." : "Unable to create your account right now.");
      setPending(false);
      return;
    }
    if (mode === "sign-up" && !result.data.session) {
      setMessage("Check your email to confirm your account, then return here to sign in.");
      setPending(false);
      return;
    }
    router.replace("/dashboard");
    router.refresh();
  }

  const isSignIn = mode === "sign-in";

  return (
    <form className="flex flex-col gap-5" onSubmit={submit}>
      <FieldGroup>
        <Field data-invalid={Boolean(error)}>
          <FieldLabel htmlFor={`${mode}-email`}>Email</FieldLabel>
          <Input
            aria-invalid={Boolean(error)}
            autoComplete="email"
            disabled={pending}
            id={`${mode}-email`}
            name="email"
            required
            type="email"
          />
        </Field>
        <Field data-invalid={Boolean(error)}>
          <FieldLabel htmlFor={`${mode}-password`}>Password</FieldLabel>
          <Input
            aria-invalid={Boolean(error)}
            autoComplete={isSignIn ? "current-password" : "new-password"}
            disabled={pending}
            id={`${mode}-password`}
            minLength={8}
            name="password"
            required
            type="password"
          />
        </Field>
      </FieldGroup>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {message ? (
        <Alert>
          <AlertDescription>{message}</AlertDescription>
        </Alert>
      ) : null}
      <Button disabled={pending} type="submit">
        {pending ? <Spinner data-icon="inline-start" /> : null}
        {isSignIn ? "Sign in" : "Create account"}
      </Button>
      <div className="flex items-center justify-between gap-3 text-sm">
        <Button asChild variant="link">
          <Link href={isSignIn ? "/sign-up" : "/sign-in"}>{isSignIn ? "Create an account" : "Sign in instead"}</Link>
        </Button>
        {isSignIn ? (
          <Button asChild variant="link">
            <Link href="/forgot-password">Forgot password?</Link>
          </Button>
        ) : null}
      </div>
    </form>
  );
}
