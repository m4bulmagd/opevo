import { notFound } from "next/navigation";

import { ClerkSetupNotice } from "@/components/auth/clerk-setup-notice";
import { CallDetailCard } from "@/components/calls/call-detail-card";
import { DeleteCallDialog } from "@/components/calls/delete-call-dialog";
import { RecordingPanel } from "@/components/calls/recording-panel";
import { TranscriptPanel } from "@/components/calls/transcript-panel";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { BackendApiError } from "@/lib/api/backend-client";
import { getCallDetail } from "@/lib/api/calls";
import { isAppAuthConfigured } from "@/lib/auth/clerk-config";

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
    const call = await getCallDetail(callId);

    return (
      <div className="@container/main grid gap-4 md:gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(320px,1fr)]">
        <div className="flex flex-col gap-4 md:gap-6">
          <CallDetailCard call={call} />
          <TranscriptPanel transcript={call.transcript} />
        </div>
        <aside className="flex flex-col gap-4 md:gap-6">
          <RecordingPanel recordingUrl={call.recording_url} />
          {call.status === "completed" || call.status === "failed" ? (
            <DeleteCallDialog callId={call.id} />
          ) : (
            <Alert>
              <AlertDescription>Remove call will be available after the call completes.</AlertDescription>
            </Alert>
          )}
        </aside>
      </div>
    );
  } catch (error) {
    if (error instanceof BackendApiError && error.status === 404) {
      notFound();
    }

    throw error;
  }
}
