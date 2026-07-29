import Link from "next/link";
import { notFound } from "next/navigation";

import { ArrowLeft } from "lucide-react";

import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { CallMetadataCard, CallStatusSurface, CallSummaryCard } from "@/components/calls/call-detail-card";
import { DeleteCallDialog } from "@/components/calls/delete-call-dialog";
import { RecordingPanel } from "@/components/calls/recording-panel";
import { TranscriptPanel } from "@/components/calls/transcript-panel";
import { PageIntro } from "@/components/product/page-intro";
import { ProductSurface } from "@/components/product/product-surface";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
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
      <div className="@container/main space-y-5">
        <Button asChild className="-ml-2 min-h-11" size="sm" variant="ghost">
          <Link href="/dashboard/calls">
            <ArrowLeft aria-hidden data-icon="inline-start" />
            Back to calls
          </Link>
        </Button>
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
        <CallStatusSurface call={call} />
        <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]">
          <div className="space-y-5">
            <CallSummaryCard call={call} />
            <ProductSurface description="Original call audio from a fresh private recording link." title="Recording">
              <RecordingPanel recordingUrl={call.recording_url} />
            </ProductSurface>
            <ProductSurface description="Speaker-labelled conversation in its stored order." title="Full transcript">
              <TranscriptPanel transcript={call.transcript} />
            </ProductSurface>
          </div>
          <aside className="space-y-5">
            <CallMetadataCard call={call} />
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
