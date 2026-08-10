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

export function SupabasePasswordRecoveryForm() {
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);
    const email = String(new FormData(event.currentTarget).get("email") ?? "");
    const { error: resetError } = await createSupabaseBrowserClient().auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/auth/callback?next=/update-password`,
    });
    setPending(false);
    if (resetError) {
      setError("Unable to send a reset link right now.");
      return;
    }
    setSent(true);
  }

  return (
    <form className="flex flex-col gap-5" onSubmit={submit}>
      <FieldGroup>
        <Field data-invalid={Boolean(error)}>
          <FieldLabel htmlFor="recovery-email">Email</FieldLabel>
          <Input
            aria-invalid={Boolean(error)}
            autoComplete="email"
            disabled={pending || sent}
            id="recovery-email"
            name="email"
            required
            type="email"
          />
        </Field>
      </FieldGroup>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {sent ? (
        <Alert>
          <AlertDescription>Check your email for a secure password reset link.</AlertDescription>
        </Alert>
      ) : null}
      <Button disabled={pending || sent} type="submit">
        {pending ? <Spinner data-icon="inline-start" /> : null}
        Send reset link
      </Button>
      <Button asChild variant="link">
        <Link href="/sign-in">Back to sign in</Link>
      </Button>
    </form>
  );
}

export function SupabaseUpdatePasswordForm() {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const router = useRouter();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    const confirmation = String(form.get("passwordConfirmation") ?? "");
    if (password !== confirmation) {
      setError("Passwords do not match.");
      setPending(false);
      return;
    }
    const { error: updateError } = await createSupabaseBrowserClient().auth.updateUser({ password });
    if (updateError) {
      setError("Unable to update your password right now.");
      setPending(false);
      return;
    }
    router.replace("/dashboard/account");
    router.refresh();
  }

  return (
    <form className="flex flex-col gap-5" onSubmit={submit}>
      <FieldGroup>
        <Field data-invalid={Boolean(error)}>
          <FieldLabel htmlFor="new-password">New password</FieldLabel>
          <Input
            aria-invalid={Boolean(error)}
            autoComplete="new-password"
            disabled={pending}
            id="new-password"
            minLength={8}
            name="password"
            required
            type="password"
          />
        </Field>
        <Field data-invalid={Boolean(error)}>
          <FieldLabel htmlFor="password-confirmation">Confirm password</FieldLabel>
          <Input
            aria-invalid={Boolean(error)}
            autoComplete="new-password"
            disabled={pending}
            id="password-confirmation"
            minLength={8}
            name="passwordConfirmation"
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
      <Button disabled={pending} type="submit">
        {pending ? <Spinner data-icon="inline-start" /> : null}
        Save new password
      </Button>
    </form>
  );
}
