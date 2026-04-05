import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { CallsTable } from "@/components/calls/calls-table";
import { listCalls } from "@/lib/api/calls";
import { isClerkConfigured } from "@/lib/auth/clerk-config";

export default async function CallsPage() {
  if (!isClerkConfigured) {
    return (
      <ClerkSetupNotice
        title="Call history is unavailable"
        description="Configure Clerk in your local environment before loading protected call records."
      />
    );
  }

  const calls = await listCalls(20);

  return <CallsTable calls={calls} />;
}
