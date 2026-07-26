import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { BillingActionsCard } from "@/components/billing/billing-actions-card";
import { BillingSummaryCards } from "@/components/billing/billing-summary-cards";
import { UsageLedgerList } from "@/components/billing/usage-ledger-list";
import { PageIntro } from "@/components/product/page-intro";
import { getSubscription, getUsageLedger, getUsageSnapshot } from "@/lib/api/billing";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";

export default async function BillingPage() {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Billing is unavailable"
        description="Configure Clerk in your local environment before loading subscription and usage data."
      />
    );
  }

  const [subscription, usageSnapshot, ledger] = await Promise.all([
    getSubscription(),
    getUsageSnapshot(),
    getUsageLedger(10),
  ]);

  return (
    <div className="@container/main flex flex-col gap-4 md:gap-6">
      <PageIntro
        description="Review your subscription, current-period minutes, and recent billing history."
        title="Billing and usage"
      />
      <BillingSummaryCards subscription={subscription} usageSnapshot={usageSnapshot} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(320px,1fr)] lg:gap-6">
        <UsageLedgerList entries={ledger.entries} />
        <aside className="flex flex-col gap-4 md:gap-6">
          <BillingActionsCard subscription={subscription} />
        </aside>
      </div>
    </div>
  );
}
