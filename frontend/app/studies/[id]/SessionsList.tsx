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
      <p className="hint mt-2">
        No interviews yet. Share the interview link above; each finished conversation shows up here
        within a few seconds.
      </p>
    );
  }

  return (
    <ScrollBox label="Interviews">
      <ul className="sessions">
        {sessions.map((s) => (
          <li key={s.sessionId}>
            <Link href={`/studies/${studyId}/sessions/${s.sessionId}`} className="card session-row">
              <div className="row-between">
                <strong>Interview {shortId(s.sessionId)}</strong>
                <span className="hint">{formatWhen(s.endedAt)}</span>
              </div>
              <div className="session-meta">
                <span>
                  {s.turnCount ?? 0} {s.turnCount === 1 ? "answer" : "answers"}
                </span>
                {s.topicsTotal ? (
                  <span>
                    {s.topicsCovered ?? 0}/{s.topicsTotal} topics
                  </span>
                ) : null}
                {s.endReason === "goal_covered" && <span className="badge live">goal covered</span>}
                {s.status === "aborted" && <span className="badge draft">dropped</span>}
                {s.processedAt ? (
                  s.sentiment && s.sentiment !== "n/a" && (
                    <span className={`badge sentiment ${s.sentiment}`}>{s.sentiment}</span>
                  )
                ) : (
                  <span className="hint">analyzing…</span>
                )}
              </div>
              {s.answers && s.answers.length > 0 && <p className="session-gist">{s.answers[0]}</p>}
            </Link>
          </li>
        ))}
      </ul>
    </ScrollBox>
  );
}
