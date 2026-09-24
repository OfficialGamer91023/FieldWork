"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { startSynthesis } from "../../actions";
import type { SynthesisStatus } from "../../lib/api";

const POLL_MS = 2500;
// Mirrors local.synthesis_stale_after_sec in infra/app/main.tf: past this, an
// in-flight run is dead and the API will let a new one take over.
const STALE_AFTER_MS = 480_000;

type Props = {
  studyId: string;
  status?: SynthesisStatus;
  startedAt?: string;
  synthesizedAt?: string;
  error?: string;
};

export default function SynthesisPanel({ studyId, status, startedAt, synthesizedAt, error }: Props) {
  const router = useRouter();
  const [isStarting, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);

  const inFlight = status === "PENDING" || status === "RUNNING";
  const stale =
    inFlight && !!startedAt && Date.now() - new Date(startedAt).getTime() > STALE_AFTER_MS;
  const busy = isStarting || (inFlight && !stale);

  // The server component owns the data; while a run is in flight we just ask it
  // to re-render. Polling stops on its own once status leaves PENDING/RUNNING.
  useEffect(() => {
    if (!inFlight || stale) return;
    const timer = setInterval(() => router.refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [inFlight, stale, router]);

  function onClick() {
    setMessage(null);
    startTransition(async () => {
      const result = await startSynthesis(studyId);
      if (!result.ok) setMessage(result.message ?? "Something went wrong.");
      router.refresh();
    });
  }

  return (
    <div className="card synthesis-panel">
      <div className="row-between">
        <div>
          {busy ? (
            <p className="synthesis-status">
              <span className="spinner" aria-hidden="true" />
              {status === "RUNNING" ? "Finding themes across interviews…" : "Starting synthesis…"}
            </p>
          ) : stale ? (
            <p className="hint">The last run stopped responding. You can start a new one.</p>
          ) : status === "FAILED" ? (
            <p className="error-text">
              Last run failed{error ? `: ${error}` : "."} Previous themes are kept below.
            </p>
          ) : synthesizedAt ? (
            <p className="hint">
              Last synthesized{" "}
              {/* Server and browser can format this in different timezones. */}
              <time dateTime={synthesizedAt} suppressHydrationWarning>
                {new Date(synthesizedAt).toLocaleString()}
              </time>
              .
            </p>
          ) : (
            <p className="hint">Group every completed interview into ranked themes with quotes.</p>
          )}
          {message && <p className="error-text">{message}</p>}
        </div>
        <button type="button" className="btn" onClick={onClick} disabled={busy}>
          {busy ? "Synthesizing…" : synthesizedAt ? "Re-synthesize" : "Synthesize"}
        </button>
      </div>
    </div>
  );
}
