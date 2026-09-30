import Link from "next/link";
import { formatWhen, SessionSummary, shortId } from "../../lib/api";
import ScrollBox from "../../ScrollBox";

// Every interview for the study, newest first, each linking to its transcript.
export default function SessionsList({
  studyId,
  sessions,
}: {
  studyId: string;
  sessions: SessionSummary[];
}) {
  if (sessions.length === 0) {
    return (
      <p className="hint mt-4">
        No interviews yet. Share the interview link above; each finished conversation shows up here
        within a few seconds.
      </p>
    );
  }

  return (
    <div className="interview-table-wrap mt-4">
      <ScrollBox label="Interviews">
        <div className="interview-table">
          <div className="it-head" aria-hidden="true">
            <span>Interview</span>
            <span>Ended</span>
            <span>Answers</span>
            <span>Topics</span>
            <span>Sentiment</span>
            <span>Key takeaway</span>
          </div>
          {sessions.map((s) => (
            <Link key={s.sessionId} href={`/studies/${studyId}/sessions/${s.sessionId}`} className="it-row">
              <span className="it-id">{shortId(s.sessionId)}</span>
              <span className="it-when">{formatWhen(s.endedAt)}</span>
              <span className="it-answers">
                {s.turnCount ?? 0}
                <span className="it-label"> {s.turnCount === 1 ? "answer" : "answers"}</span>
              </span>
              <span className="it-topics">
                {s.topicsTotal ? `${s.topicsCovered ?? 0}/${s.topicsTotal}` : "–"}
                <span className="it-label"> topics</span>
              </span>
              <span>
                {s.status === "aborted" ? (
                  <span className="pill dropped">dropped</span>
                ) : !s.processedAt ? (
                  <span className="hint">analyzing…</span>
                ) : s.sentiment && s.sentiment !== "n/a" ? (
                  <span className={`pill ${s.sentiment}`}>{s.sentiment}</span>
                ) : (
                  <span className="hint">–</span>
                )}
              </span>
              <span className="it-gist">{s.answers?.[0] ?? ""}</span>
            </Link>
          ))}
        </div>
      </ScrollBox>
    </div>
  );
}
