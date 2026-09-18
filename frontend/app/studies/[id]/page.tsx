import Link from "next/link";
import { notFound } from "next/navigation";
import { apiFetch, Study } from "../../lib/api";
import { publishStudy } from "../../actions";
import InviteLink from "./InviteLink";

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
  const isLive = study.status === "live";
  const inviteUrl = study.inviteToken ? `${APP_URL}/interview/${study.inviteToken}` : null;

  return (
    <>
      <p>
        <Link href="/">← All studies</Link>
      </p>

      <div className="row-between">
        <h1>{study.title}</h1>
        <span className={`badge ${isLive ? "live" : "draft"}`}>{study.status}</span>
      </div>

      {study.goal && (
        <>
          <h2>Research goal</h2>
          <p>{study.goal}</p>
        </>
      )}

      {study.seedQuestions && study.seedQuestions.length > 0 && (
        <>
          <h2>Seed questions</h2>
          <ul className="field-list">
            {study.seedQuestions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </>
      )}

      <h2>Interview link</h2>
      {isLive ? (
        inviteUrl ? (
          <>
            <p className="hint mt-2">Share this link with participants:</p>
            <InviteLink url={inviteUrl} />
            <p className="mt-4">
              <a href={inviteUrl} target="_blank" rel="noreferrer" className="btn secondary">
                Open interview page
              </a>
            </p>
          </>
        ) : (
          <p className="hint">This study has no invite token.</p>
        )
      ) : (
        <>
          <p className="hint mt-2">
            This study is a <strong>draft</strong>. Publish it to activate the interview link and
            let participants start interviews.
          </p>
          <form action={publishStudy} className="mt-4">
            <input type="hidden" name="id" value={study.studyId} />
            <button type="submit" className="btn big">
              Publish study
            </button>
          </form>
        </>
      )}
    </>
  );
}
