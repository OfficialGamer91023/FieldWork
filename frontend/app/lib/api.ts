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
