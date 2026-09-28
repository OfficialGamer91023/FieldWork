import Link from "next/link";
import { shortId, type Theme } from "../../lib/api";

// Server component: pure rendering of what's on the study row. It updates
// during a run because SynthesisPanel's router.refresh() re-renders the page.
export default function ThemesList({ studyId, themes }: { studyId: string; themes: Theme[] }) {
  if (themes.length === 0) {
    return <p className="hint mt-4">No completed interviews to synthesize yet.</p>;
  }

  // Themes arrive sorted by sessionCount; scale the bars against the top one.
  const maxCount = Math.max(...themes.map((t) => t.sessionCount), 1);

  return (
    <ol className="themes">
      {themes.map((theme, i) => (
        <li key={`${i}-${theme.title}`} className="card theme">
          <div className="row-between">
            <h3>
              <span className="theme-rank">{i + 1}</span>
              {theme.title}
            </h3>
            <span className="badge">
              {theme.sessionCount} {theme.sessionCount === 1 ? "interview" : "interviews"}
            </span>
          </div>
          <div className="theme-bar" aria-hidden="true">
            <div style={{ width: `${(theme.sessionCount / maxCount) * 100}%` }} />
          </div>
          <p>{theme.summary}</p>
          {theme.quotes.length > 0 && (
            <ul className="theme-quotes">
              {theme.quotes.map((q, j) => (
                <li key={j}>
                  <blockquote>“{q.text}”</blockquote>
                  <Link
                    className="hint quote-source"
                    href={`/studies/${studyId}/sessions/${q.sessionId}?q=${encodeURIComponent(q.text)}#quote`}
                  >
                    Interview {shortId(q.sessionId)} · see in transcript →
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ol>
  );
}
