import { apiFetch } from "../../lib/api";
import InterviewClient from "./InterviewClient";

export const dynamic = "force-dynamic";

const WS_BASE = (process.env.NEXT_PUBLIC_ORCHESTRATOR_WS_URL ?? "ws://localhost:8000").replace(
  /\/+$/,
  ""
);

export default async function InterviewPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;

  let res: Response;
  try {
    res = await apiFetch(`/invite/${token}`);
  } catch {
    return (
      <div className="error-box">Couldn&apos;t reach the interview service. Try again shortly.</div>
    );
  }

  if (res.status === 403) {
    return (
      <div className="empty">
        <h1>This interview isn&apos;t active</h1>
        <p className="hint">The study may not be published yet, or it has been closed.</p>
      </div>
    );
  }
  if (res.status === 404 || !res.ok) {
    return (
      <div className="empty">
        <h1>Invalid link</h1>
        <p className="hint">This interview link doesn&apos;t match an active study.</p>
      </div>
    );
  }

  const study: { studyId: string; title: string; goal?: string } = await res.json();

  return <InterviewClient token={token} wsBase={WS_BASE} title={study.title} />;
}
