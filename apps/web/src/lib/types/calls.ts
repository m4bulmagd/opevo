export type CallHistoryListItem = {
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
};

export type CallTranscriptLine = {
  speaker: string;
  text: string;
  sequence_number: number;
  created_at: string;
};

export type CallDetail = {
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
