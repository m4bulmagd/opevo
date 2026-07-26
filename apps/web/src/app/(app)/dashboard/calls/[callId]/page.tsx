import { notFound } from "next/navigation";

import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { CallDetailCard } from "@/components/calls/call-detail-card";
import { DeleteCallDialog } from "@/components/calls/delete-call-dialog";
import { RecordingPanel } from "@/components/calls/recording-panel";
import { TranscriptPanel } from "@/components/calls/transcript-panel";
import { PageIntro } from "@/components/product/page-intro";
import { ProductSurface } from "@/components/product/product-surface";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { getAccount } from "@/lib/api/account";
import { BackendApiError } from "@/lib/api/backend-client";
import { getCallDetail } from "@/lib/api/calls";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";
import { formatCallTime, formatPhoneNumber, toTitleCase } from "@/lib/formatters";

export default async function CallDetailPage({ params }: { params: Promise<{ callId: string }> }) {
  if (!isAppAuthConfigured) {
    return (
      <ClerkSetupNotice
        title="Call details are unavailable"
        description="Configure Clerk in your local environment before loading protected call detail routes."
      />
    );
  }

  const { callId } = await params;

  try {
    const [call, account] = await Promise.all([getCallDetail(callId), getAccount()]);

    return (
      <div className="@container/main flex flex-col gap-6 md:gap-8">
        <PageIntro
          description={
            <>
              <time dateTime={call.started_at ?? undefined}>{formatCallTime(call.started_at)}</time>
              {" · "}
              Stored status: {toTitleCase(call.status)}
            </>
          }
          eyebrow="Call details"
          title={formatPhoneNumber(call.caller_number)}
        />
        <CallDetailCard call={call} />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,0.65fr)]">
          <ProductSurface description="Speaker-labelled conversation in its stored order." title="Transcript">
            <TranscriptPanel transcript={call.transcript} />
          </ProductSurface>
          <aside className="flex flex-col gap-6">
            <ProductSurface description="Original call audio from a fresh private recording link." title="Recording">
              <RecordingPanel recordingUrl={call.recording_url} />
            </ProductSurface>
            {account.status !== "active" ? (
              <Alert>
                <AlertDescription>Call history is read-only while your account is {account.status}.</AlertDescription>
              </Alert>
            ) : call.status === "completed" || call.status === "failed" ? (
              <DeleteCallDialog callId={call.id} />
            ) : (
              <Alert>
                <AlertDescription>Remove call will be available after the call completes.</AlertDescription>
              </Alert>
            )}
          </aside>
        </div>
      </div>
    );
  } catch (error) {
    if (error instanceof BackendApiError && error.status === 404) {
      notFound();
    }

    throw error;
  }
}
