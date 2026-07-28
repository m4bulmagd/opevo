import { redirect } from "next/navigation";

import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { CallHistoryPagination, CallHistorySearch } from "@/components/calls/call-history-controls";
import { CallsTable } from "@/components/calls/calls-table";
import { PageIntro } from "@/components/product/page-intro";
import { listCalls } from "@/lib/api/calls";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import {
  buildCallHistoryHref,
  type CallHistorySearchParams,
  callHistoryPageCount,
  parseCallHistoryNavigation,
} from "@/lib/calls/call-history-navigation";

type CallsPageProps = {
  searchParams: Promise<CallHistorySearchParams>;
};

export default async function CallsPage({ searchParams }: CallsPageProps) {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Call history is unavailable"
        description="Configure Clerk in your local environment before loading protected call records."
      />
    );
  }

  const navigation = parseCallHistoryNavigation(await searchParams);
  const result = await listCalls({
    limit: navigation.limit,
    offset: navigation.offset,
    query: navigation.query,
    status: navigation.status,
    range: navigation.range,
  });
  const lastPage = callHistoryPageCount(result.total, navigation.limit);

  if (navigation.page > lastPage) {
    redirect(
      buildCallHistoryHref({
        query: navigation.query,
        status: navigation.status,
        range: navigation.range,
        page: lastPage,
      }),
    );
  }

  return (
    <div className="flex flex-col gap-6 md:gap-8">
      <PageIntro
        description="Search stored conversations and review the context your receptionist captured."
        eyebrow="Call workspace"
        title="Calls"
      />
      <CallHistorySearch query={navigation.query} status={navigation.status} range={navigation.range} />
      <CallsTable calls={result.calls} query={navigation.query} />
      <CallHistoryPagination
        query={navigation.query}
        status={navigation.status}
        range={navigation.range}
        page={navigation.page}
        pageSize={navigation.limit}
        total={result.total}
        returnedCount={result.calls.length}
      />
    </div>
  );
}
