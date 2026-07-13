"use client";

import { useTransition } from "react";

import { toast } from "sonner";

import { createCheckoutSessionAction, createPortalSessionAction } from "@/app/(app)/dashboard/billing/actions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import type { Subscription } from "@/lib/types/billing";

export function BillingActionsCard({ subscription }: { subscription: Subscription | null }) {
  const [isPending, startTransition] = useTransition();
  const canStartCheckout = subscription === null || subscription.can_start_checkout;

  const handleCheckout = () => {
    startTransition(async () => {
      const result = await createCheckoutSessionAction("starter");

      if (result.status === "success" && result.url) {
        toast.success("Opening Stripe Checkout");
        window.location.assign(result.url);
        return;
      }

      toast.error(result.message);
    });
  };

  const handlePortal = () => {
    startTransition(async () => {
      const result = await createPortalSessionAction();

      if (result.status === "success" && result.url) {
        toast.success("Opening billing portal");
        window.location.assign(result.url);
        return;
      }

      toast.error(result.message);
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Billing actions</CardTitle>
        <CardDescription>
          France self-serve launch uses a single starter plan with hosted Stripe checkout.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {canStartCheckout ? (
          <Button onClick={handleCheckout} disabled={isPending}>
            {isPending ? <Spinner data-icon="inline-start" /> : null}
            Start starter plan
          </Button>
        ) : (
          <Button onClick={handlePortal} disabled={isPending}>
            {isPending ? <Spinner data-icon="inline-start" /> : null}
            Manage billing
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
