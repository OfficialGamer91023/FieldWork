import Link from "next/link";
import { notFound } from "next/navigation";
import { apiFetch, formatWhen, SessionDetail, shortId, TranscriptLine } from "../../../../lib/api";

export const dynamic = "force-dynamic";

// Loose match so a quote still finds its line despite punctuation/casing differences.
function normalize(text: string) {
  return text
    .toLowerCase()
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[^a-z0-9' ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function findQuoteLine(transcript: TranscriptLine[], quote: string) {
  const q = normalize(quote);
  if (!q) return -1;
  return transcript.findIndex((l) => l.role === "user" && normalize(l.content).includes(q));
}

// Wrap the quoted words inside the line in <mark>, if they appear verbatim.
function Highlighted({ text, quote }: { text: string; quote: string }) {
  const at = text.toLowerCase().indexOf(quote.toLowerCase().trim());
  if (!quote || at < 0) return <>{text}</>;
  const end = at + quote.trim().length;
  return (
    <>
      {text.slice(0, at)}
      <mark>{text.slice(at, end)}</mark>
      {text.slice(end)}
    </>
  );
}

export default async function SessionPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string; sessionId: string }>;
  searchParams: Promise<{ q?: string }>;
}) {
  const { id, sessionId } = await params;
  const { q = "" } = await searchParams;

  let res: Response;
  try {
    res = await apiFetch(`/studies/${id}/sessions/${sessionId}`);
  } catch {
    return <div className="error-box">Couldn&apos;t reach the API. Try again shortly.</div>;
  }
  if (res.status === 404) notFound();
  if (!res.ok) {
    return <div className="error-box">Failed to load interview (API returned {res.status}).</div>;
  }
  const session: SessionDetail = await res.json();
  const quoteLine = q ? findQuoteLine(session.transcript, q) : -1;

  return (
    <>
      <p>
        <Link href={`/studies/${id}`}>← Back to study</Link>
      </p>
      <div className="row-between">
        <h1>Interview {shortId(session.sessionId)}</h1>
        {session.sentiment && session.sentiment !== "n/a" && (
          <span className={`badge sentiment ${session.sentiment}`}>{session.sentiment}</span>
        )}
      </div>
      <p className="hint">
        {formatWhen(session.endedAt)} · {session.turnCount ?? 0} answers
        {session.status === "aborted" ? " · participant dropped" : ""}
      </p>

      {q && quoteLine < 0 && (
        <div className="error-box mt-4">
          Couldn&apos;t find the quote &ldquo;{q}&rdquo; word-for-word in this transcript.
        </div>
      )}

      {session.answers && session.answers.length > 0 && (
        <>
          <h2>Key takeaways</h2>
          <ul className="field-list">
            {session.answers.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </>
      )}

      <h2>Transcript</h2>
      {session.transcript.length === 0 ? (
        <p className="hint">The transcript for this interview isn&apos;t available.</p>
      ) : (
        <div className="transcript">
          {session.transcript.map((line, i) => (
            <div
              key={i}
              id={i === quoteLine ? "quote" : undefined}
              className={`turn ${line.role === "assistant" ? "agent" : "user"}${
                i === quoteLine ? " highlight" : ""
              }`}
            >
              <div className="who">
                {line.role === "assistant" ? "Interviewer" : "Participant"}
                {line.interrupted && <span className="hint"> · cut off</span>}
              </div>
              <div>{i === quoteLine ? <Highlighted text={line.content} quote={q} /> : line.content}</div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
