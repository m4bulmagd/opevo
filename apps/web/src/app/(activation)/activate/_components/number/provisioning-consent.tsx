"use client";

import { useRef, useState } from "react";

import { useRouter } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
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

import { confirmProvisioningAction } from "../../actions";

export function ProvisioningConsent() {
  const router = useRouter();
  const pendingRef = useRef(false);
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await confirmProvisioningAction({});
      if (result.status === "error") {
        setError(result.message);
        return;
      }
      setOpen(false);
      router.refresh();
    } catch {
      setError("We couldn't start number provisioning. Refresh and try again.");
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <p className="font-semibold text-lg">Your plan is ready</p>
        <p className="max-w-2xl text-muted-foreground text-sm leading-6">
          Payment activates your plan, but it is not consent to order a number. Review the provisioning details first.
        </p>
      </div>

      <AlertDialog open={open} onOpenChange={(nextOpen) => (!pending ? setOpen(nextOpen) : undefined)}>
        <AlertDialogTrigger asChild>
          <Button className="min-h-11 self-start" size="lg" disabled={pending}>
            Review number provisioning
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Provision your French Presvo number</AlertDialogTitle>
            <AlertDialogDescription>
              Presvo will order one French number for this account. Your existing business number stays with your
              carrier, and conditional forwarding is configured in the next step.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="rounded-xl border border-border bg-muted/30 p-4 text-sm leading-6">
            <dl className="grid gap-2">
              <div className="flex justify-between gap-4">
                <dt className="text-muted-foreground">Country</dt>
                <dd className="font-medium">France (+33)</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted-foreground">Account limit</dt>
                <dd className="font-medium">One Presvo number</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-muted-foreground">Next</dt>
                <dd className="text-right font-medium">Configure conditional forwarding</dd>
              </div>
            </dl>
          </div>
          {error ? (
            <Alert variant="destructive">
              <AlertTitle>Provisioning did not start</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
            <Button disabled={pending} onClick={() => void confirm()}>
              {pending ? <Spinner /> : null}
              Confirm and provision my number
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
