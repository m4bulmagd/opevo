import type { ActivationSnapshot } from "@/lib/types/activation";

import { PaymentAction } from "./payment-action";
import { ProvisioningConsent } from "./provisioning-consent";
import { ProvisioningStatus } from "./provisioning-status";

type NumberMilestoneProps = {
  snapshot: ActivationSnapshot;
  localBilling: boolean;
};

export function NumberMilestone({ snapshot, localBilling }: NumberMilestoneProps) {
  if (snapshot.number.assigned_e164 || snapshot.stage === "provisioning" || snapshot.stage === "provisioning_failed") {
    return <ProvisioningStatus snapshot={snapshot} />;
  }

  if (!snapshot.billing.eligible) {
    return <PaymentAction localBilling={localBilling} />;
  }

  return <ProvisioningConsent />;
}
