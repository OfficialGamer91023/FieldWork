import Link from "next/link";
import { apiFetch, apiConfigured, StudySummary } from "./lib/api";

export const dynamic = "force-dynamic";

function StatusBadge({ status }: { status: string }) {
  const cls = status === "live" ? "badge live" : status === "draft" ? "badge draft" : "badge";
  return <span className={cls}>{status}</span>;
}

export default async function DashboardPage() {
  if (!apiConfigured()) {
    return (
      <>
        <h1>Studies</h1>
        <div className="error-box mt-4">
          <strong>CONTROL_PLANE_API_URL is not set.</strong> Copy{" "}
          <code>.env.local.example</code> to <code>.env.local</code> and set it to your{" "}
          <code>control_plane_api_url</code> Terraform output, then restart the dev server.
        </div>
      </>
    );
  }

  let studies: StudySummary[] = [];
  let error: string | null = null;

  try {
    const res = await apiFetch("/studies");
    if (!res.ok) {
      error = `API returned ${res.status}`;
    } else {
      const data = await res.json();
      studies = data.studies ?? [];
    }
  } catch (e) {
    error = e instanceof Error ? e.message : "Could not reach the control-plane API.";
  }

  // Newest first (createdAt is an ISO string).
  studies.sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));

  return (
    <>
      <div className="row-between">
        <h1>Studies</h1>
      </div>
      <p className="subtitle">Define a research goal, publish it, and share the interview link.</p>

      {error && <div className="error-box">Couldn&apos;t load studies: {error}</div>}

      {!error && studies.length === 0 && (
        <div className="empty">
          No studies yet.
          <div className="mt-4">
            <Link href="/studies/new" className="btn">
              Create your first study
            </Link>
          </div>
        </div>
      )}

      {studies.map((s) => (
        <Link key={s.studyId} href={`/studies/${s.studyId}`} className="card study-link">
          <div className="row-between">
            <strong>{s.title}</strong>
            <StatusBadge status={s.status} />
          </div>
        </Link>
      ))}
    </>
  );
}
