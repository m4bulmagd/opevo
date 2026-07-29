"use client";

import { useState, useTransition } from "react";

import { TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { createCheckoutSessionAction, createPortalSessionAction } from "@/app/(app)/dashboard/billing/actions";
import { type ActionPhase, ActionState } from "@/components/motion/action-state";
import { PresvoMotionProvider } from "@/components/motion/presvo-motion-provider";
import { ProductSurface } from "@/components/product/product-surface";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { Subscription } from "@/lib/types/billing";

type BillingActionsCardProps = {
  subscription: Subscription | null;
  navigate?: (url: string) => void;
};

const defaultNavigate = (url: string) => window.location.assign(url);

export function BillingActionsCard({ subscription, navigate = defaultNavigate }: BillingActionsCardProps) {
  const [feedback, setFeedback] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const canStartCheckout = subscription === null || subscription.can_start_checkout;
  const actionPhase: ActionPhase = isPending ? "pending" : feedback ? "error" : "idle";
  const actionCopy = canStartCheckout
    ? {
        error: "Try checkout again",
        idle: "Start starter plan",
        pending: "Opening checkout",
      }
    : {
        error: "Try portal again",
        idle: "Manage billing",
        pending: "Opening billing portal",
      };

  const handleAction = () => {
    setFeedback(null);
    startTransition(async () => {
      const result = canStartCheckout
        ? await createCheckoutSessionAction("starter")
        : await createPortalSessionAction();

      if (result.status === "success" && result.url) {
        toast.success(canStartCheckout ? "Opening Stripe Checkout" : "Opening billing portal");
        navigate(result.url);
        return;
      }

      setFeedback(result.message);
      toast.error(result.message);
    });
  };

  return (
    <ProductSurface
      description={
        canStartCheckout
          ? "France self-serve launch uses a single Starter plan with hosted Stripe Checkout."
          : "Open the secure Stripe Portal for invoices, receipts, and payment methods."
      }
      title="Invoices and payment"
    >
      <div className="flex flex-col items-start gap-4">
        <Button className="min-h-11 px-4" disabled={isPending} onClick={handleAction}>
          <PresvoMotionProvider>
            <ActionState
              error={
                <>
                  <TriangleAlert aria-hidden data-icon="inline-start" />
                  {actionCopy.error}
                </>
              }
              idle={actionCopy.idle}
              pending={
                <>
                  <Spinner data-icon="inline-start" />
                  {actionCopy.pending}
                </>
              }
              phase={actionPhase}
              success={actionCopy.idle}
            />
          </PresvoMotionProvider>
        </Button>
        <p
          aria-atomic="true"
          aria-label="Billing action feedback"
          className="min-h-5 text-sm text-text-secondary"
          role="status"
        >
          {feedback}
        </p>
      </div>
    </ProductSurface>
  );
}
