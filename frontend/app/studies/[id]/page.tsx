import Link from "next/link";
import { notFound } from "next/navigation";
import { apiFetch, SessionSummary, Study } from "../../lib/api";
import { publishStudy } from "../../actions";
import InviteLink from "./InviteLink";
import SynthesisPanel from "./SynthesisPanel";
import SessionsList from "./SessionsList";
import ThemesList from "./ThemesList";

export const dynamic = "force-dynamic";

const APP_URL = (process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000").replace(/\/+$/, "");

export default async function StudyDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  const res = await apiFetch(`/studies/${id}`);
  if (res.status === 404) notFound();
  if (!res.ok) {
    return <div className="error-box">Failed to load study (API returned {res.status}).</div>;
  }

  const study: Study = await res.json();
  // The interview list is secondary; if it fails, still render the study.
  let sessions: SessionSummary[] | null = null;
  try {
    const sres = await apiFetch(`/studies/${id}/sessions`);
    if (sres.ok) sessions = ((await sres.json()) as { sessions: SessionSummary[] }).sessions;
  } catch {}
  const isLive = study.status === "live";
  const inviteUrl = study.inviteToken ? `${APP_URL}/interview/${study.inviteToken}` : null;

  // Dropped interviews aren't synthesized, so they don't count toward a theme's reach.
  const completed = sessions?.filter((s) => s.status !== "aborted").length;

  return (
    <>
      <p className="back">
        <Link href="/">← All studies</Link>
      </p>

      <header className="study-head">
        <p className="kicker">
          Study · <span className={isLive ? "live" : "draft"}>{isLive ? "Live" : "Draft"}</span>
          {sessions ? ` · ${sessions.length} ${sessions.length === 1 ? "interview" : "interviews"}` : ""}
        </p>
        <h1>{study.title}</h1>
        {study.goal && <p className="study-lede">{study.goal}</p>}

        {isLive ? (
          inviteUrl ? (
            <div className="study-bar">
              <span className="bar-label">Invite link</span>
              <InviteLink url={inviteUrl} />
              <a href={inviteUrl} target="_blank" rel="noreferrer" className="btn secondary">
                Open interview page
              </a>
            </div>
          ) : (
            <p className="hint mt-4">This study has no invite token.</p>
          )
        ) : (
          <div className="study-bar">
            <span className="bar-label">
              This study is a draft. Publish it to activate the interview link.
            </span>
            <form action={publishStudy}>
              <input type="hidden" name="id" value={study.studyId} />
              <button type="submit" className="btn">
                Publish study
              </button>
            </form>
          </div>
        )}
      </header>

      <section aria-labelledby="themes-heading">
        <div className="section-head">
          <h2 id="themes-heading">What we heard</h2>
          {study.themes && study.themes.length > 0 && (
            <span className="hint">
              {study.themes.length} {study.themes.length === 1 ? "theme" : "themes"}, most common first
            </span>
          )}
        </div>
        <SynthesisPanel
          studyId={study.studyId}
          status={study.synthesisStatus}
          startedAt={study.synthesisStartedAt}
          synthesizedAt={study.synthesizedAt}
          error={study.synthesisError}
        />
        {/* Absent until the first run finishes; a FAILED run keeps the old themes. */}
        {study.themes && (
          <ThemesList studyId={study.studyId} themes={study.themes} interviewCount={completed} />
        )}
      </section>

      <section aria-labelledby="interviews-heading">
        <div className="section-head">
          <h2 id="interviews-heading">Interviews</h2>
          {sessions && sessions.length > 0 && <span className="hint">Newest first</span>}
        </div>
        {sessions ? (
          <SessionsList studyId={study.studyId} sessions={sessions} />
        ) : (
          <p className="hint mt-4">Couldn&apos;t load the interview list.</p>
        )}
      </section>

      {study.seedQuestions && study.seedQuestions.length > 0 && (
        <section aria-labelledby="seed-heading">
          <div className="section-head">
            <h2 id="seed-heading">Seed questions</h2>
            <span className="hint">What the interviewer sets out to cover</span>
          </div>
          <ol className="seed-list">
            {study.seedQuestions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}
