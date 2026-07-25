export type CallSummaryStatus = "processing" | "ready" | "unavailable";

export type CallSummaryFields = {
  summary_status: CallSummaryStatus;
  caller_intent: string | null;
  action_items: string[] | null;
  sentiment: string | null;
  follow_up_required: boolean | null;
};

export type CallHistoryListItem = CallSummaryFields & {
  id: string;
  status: string;
  caller_number: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  minutes_charged: number | null;
  summary_text: string | null;
  has_recording: boolean;
};

export type CallHistoryListResponse = {
  calls: CallHistoryListItem[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

export type CallTranscriptLine = {
  speaker: string;
  text: string;
  sequence_number: number;
  created_at: string;
};

export type CallDetail = CallSummaryFields & {
  id: string;
  status: string;
  caller_number: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  minutes_charged: number | null;
  summary_text: string | null;
  recording_url: string | null;
  transcript: CallTranscriptLine[];
};
