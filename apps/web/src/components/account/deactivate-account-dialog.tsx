"use client";

import { type MouseEvent, useState, useTransition } from "react";

import { TriangleAlert } from "lucide-react";

import { deactivateAccount } from "@/app/(app)/dashboard/account/actions";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";

const CONSEQUENCES = [
  "New calls stop immediately.",
  "Your subscription is canceled immediately with no automatic prorated refund.",
  "An active call may finish before cleanup completes.",
  "Your current Presvo number is permanently released.",
  "Your calls, recordings, billing history, and saved configuration are retained.",
  "Reactivation requires a new subscription and a newly provisioned number.",
] as const;

export function DeactivateAccountDialog() {
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const isConfirmed = confirmation === "DEACTIVATE";

  const handleOpenChange = (open: boolean) => {
    if (!open && !isPending) {
      setConfirmation("");
      setError(null);
    }
  };

  const handleDeactivate = (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    if (!isConfirmed || isPending) {
      return;
    }

    setError(null);
    startTransition(async () => {
      const result = await deactivateAccount(confirmation);
      if (result?.status === "error") {
        setError(result.message);
      }
    });
  };

  return (
    <AlertDialog onOpenChange={handleOpenChange}>
      <AlertDialogTrigger asChild>
        <Button variant="destructive">
          <TriangleAlert data-icon="inline-start" />
          Deactivate Presvo
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Deactivate Presvo?</AlertDialogTitle>
          <AlertDialogDescription>
            Review each consequence. Deactivation preserves your data but ends the current service cycle.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <ul className="grid gap-2 text-sm" aria-label="Account deactivation consequences">
          {CONSEQUENCES.map((consequence) => (
            <li key={consequence} className="flex gap-2">
              <span className="mt-2 size-1.5 shrink-0 rounded-full bg-destructive" aria-hidden="true" />
              <span>{consequence}</span>
            </li>
          ))}
        </ul>

        <div className="grid gap-2">
          <Label htmlFor="deactivation-confirmation">Type DEACTIVATE to confirm</Label>
          <Input
            id="deactivation-confirmation"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            disabled={isPending}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "deactivation-error" : "deactivation-help"}
          />
          <p id="deactivation-help" className="text-muted-foreground text-xs">
            Confirmation is case-sensitive.
          </p>
          {error ? (
            <p id="deactivation-error" className="text-destructive text-sm" role="alert" aria-live="polite">
              {error}
            </p>
          ) : null}
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Keep Presvo active</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={!isConfirmed || isPending} onClick={handleDeactivate}>
            {isPending ? <Spinner data-icon="inline-start" /> : <TriangleAlert data-icon="inline-start" />}
            Deactivate account
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
