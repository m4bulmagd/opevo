import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { CallsTable } from "@/components/calls/calls-table";
import { listCalls } from "@/lib/api/calls";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import { CALLS_PAGE_SIZE } from "@/lib/calls/call-history-navigation";

export default async function CallsPage() {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Call history is unavailable"
        description="Configure Clerk in your local environment before loading protected call records."
      />
    );
  }

  const page = await listCalls({ limit: CALLS_PAGE_SIZE });

  return <CallsTable calls={page.calls} />;
}
