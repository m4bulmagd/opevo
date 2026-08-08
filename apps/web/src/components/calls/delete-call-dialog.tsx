"use client";

import { type MouseEvent, useState, useTransition } from "react";

import { Trash2 } from "lucide-react";

import { type DeleteCallActionResult, deleteCallAction } from "@/app/(app)/dashboard/calls/actions";
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
import { Spinner } from "@/components/ui/spinner";

type DeleteHandler = (callId: string) => Promise<DeleteCallActionResult | undefined>;

export function DeleteCallDialog({
  callId,
  deleteHandler = deleteCallAction,
}: {
  callId: string;
  deleteHandler?: DeleteHandler;
}) {
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const removeCall = (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    setError(null);
    startTransition(async () => {
      const result = await deleteHandler(callId);
      if (result?.status === "error") {
        setError(result.message);
      }
    });
  };

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button className="min-h-11" variant="destructive">
          <Trash2 data-icon="inline-start" />
          Remove call
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove this call?</AlertDialogTitle>
          <AlertDialogDescription>
            This removes the recording, transcript, summary, and caller details from your active Opevo account.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error ? (
          <p aria-live="polite" className="text-destructive text-sm" role="alert">
            {error}
          </p>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction disabled={isPending} onClick={removeCall} variant="destructive">
            {isPending ? <Spinner data-icon="inline-start" /> : <Trash2 data-icon="inline-start" />}
            Remove call
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
