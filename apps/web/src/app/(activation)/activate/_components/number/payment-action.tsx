"use client";

import { useRef, useState } from "react";

import { useRouter } from "next/navigation";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

import { activateDevelopmentStarterAction, createActivationCheckoutAction } from "../../actions";

type PaymentActionProps = {
  localBilling: boolean;
  navigate?: (url: string) => void;
};

const defaultNavigate = (url: string) => window.location.assign(url);

export function PaymentAction({ localBilling, navigate = defaultNavigate }: PaymentActionProps) {
  const router = useRouter();
  const pendingRef = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startPlan = async () => {
    if (pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setError(null);

    if (localBilling) {
      try {
        const result = await activateDevelopmentStarterAction({});
        if (result.status === "error") {
          setError(result.message);
          return;
        }
        router.refresh();
      } catch {
        setError("We couldn't activate the local starter plan. Refresh and try again.");
      } finally {
        pendingRef.current = false;
        setPending(false);
      }
      return;
    }

    let checkoutUrl: string | null = null;
    try {
      const result = await createActivationCheckoutAction({});
      if (result.status === "error") {
        setError(result.message);
        return;
      }
      checkoutUrl = result.data.url;
    } catch {
      setError("We couldn't open checkout. Refresh and try again.");
      return;
    } finally {
      pendingRef.current = false;
      setPending(false);
    }

    if (checkoutUrl) navigate(checkoutUrl);
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <p className="font-semibold text-lg">Starter plan</p>
        <p className="max-w-2xl text-muted-foreground text-sm leading-6">
          Payment activates your Presvo plan. It does not order a phone number; you review that separately next.
        </p>
        {!localBilling ? (
          <p className="text-muted-foreground text-sm">
            Cancel checkout before completing payment and Presvo will not charge you.
          </p>
        ) : (
          <p className="text-muted-foreground text-sm">
            Local development uses the deterministic fake billing provider.
          </p>
        )}
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>We couldn't activate the plan</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <Button className="min-h-11 self-start" size="lg" disabled={pending} onClick={() => void startPlan()}>
        {pending ? <Spinner /> : null}
        {localBilling ? "Activate local starter plan" : "Start starter plan"}
      </Button>
    </div>
  );
}
