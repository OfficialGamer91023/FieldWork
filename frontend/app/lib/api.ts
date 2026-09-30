// Server-side helper for talking to the control-plane API (API Gateway + Lambda).
// This runs only on the Next.js server, so the API URL never reaches the browser
// and there is no CORS to configure on the Lambda side.

const BASE = (process.env.CONTROL_PLANE_API_URL ?? "").replace(/\/+$/, "");

export function apiConfigured(): boolean {
  return BASE.length > 0;
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  if (!BASE) {
    throw new Error(
      "CONTROL_PLANE_API_URL is not set. Copy .env.local.example to .env.local."
    );
  }
  return fetch(`${BASE}${path}`, { cache: "no-store", ...init });
}

export type StudySummary = {
  studyId: string;
  title: string;
  status: string;
  createdAt?: string;
};

export type SynthesisStatus = "PENDING" | "RUNNING" | "DONE" | "FAILED";

export type ThemeQuote = { text: string; sessionId: string };

export type Theme = {
  title: string;
  summary: string;
  sessionCount: number;
  sessionCitations: string[];
  quotes: ThemeQuote[];
};

export type Study = StudySummary & {
  goal?: string;
  seedQuestions?: string[];
  inviteToken?: string;
  founderId?: string;
  // Synthesis state lives in its own attributes — `status` is draft/live.
  synthesisStatus?: SynthesisStatus;
  synthesisStartedAt?: string;
  synthesizedAt?: string;
  synthesisError?: string;
  themes?: Theme[];
};

export type Sentiment = "positive" | "negative" | "neutral" | "mixed" | "n/a";

// One interview as the dashboard lists it (the transcript stays in S3 until opened).
export type SessionSummary = {
  sessionId: string;
  endedAt?: string;
  turnCount?: number;
  status?: "completed" | "aborted";
  // How many seed questions the live note-taker judged answered, and why the call ended.
  topicsCovered?: number;
  topicsTotal?: number;
  endReason?: "goal_covered" | "participant_stopped" | "participant_hung_up" | "turn_cap" | "time_cap" | "participant_idle" | "unknown";
  // Filled in by the async extractor a few seconds after the call ends.
  processedAt?: string;
  sentiment?: Sentiment;
  answers?: string[];
  quotes?: string[];
};

export type TranscriptLine = {
  role: "user" | "assistant";
  content: string;
  at?: string;
  interrupted?: boolean;
};

export type SessionDetail = SessionSummary & { transcript: TranscriptLine[] };

export function shortId(id: string) {
  return id.slice(0, 6);
}

export function formatWhen(iso?: string) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  });
}
