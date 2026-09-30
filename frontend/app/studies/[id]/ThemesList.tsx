import Link from "next/link";
import { shortId, type Theme, type ThemeQuote } from "../../lib/api";
import ScrollBox from "../../ScrollBox";

// Past this many interviews a row of dots gets too long; show a bar instead.
const MAX_DOTS = 20;
// Quotes shown per theme before the rest fold away behind "N more quotes".
const SHOWN_QUOTES = 2;

function QuoteList({ studyId, quotes }: { studyId: string; quotes: ThemeQuote[] }) {
  return (
    <ul className="theme-quotes">
      {quotes.map((q, j) => (
        <li key={j}>
          <blockquote>“{q.text}”</blockquote>
          <Link
            className="quote-source"
            href={`/studies/${studyId}/sessions/${q.sessionId}?q=${encodeURIComponent(q.text)}#quote`}
          >
            Interview {shortId(q.sessionId)} · see in transcript →
          </Link>
        </li>
      ))}
    </ul>
  );
}

// Server component: pure rendering of what's on the study row. It updates
// during a run because SynthesisPanel's router.refresh() re-renders the page.
export default function ThemesList({
  studyId,
  themes,
  interviewCount,
}: {
  studyId: string;
  themes: Theme[];
  interviewCount?: number;
}) {
  if (themes.length === 0) {
    return <p className="hint mt-4">No completed interviews to synthesize yet.</p>;
  }

  // Themes arrive sorted by sessionCount. The total can lag behind (interviews that
  // finished after the last run), so never let a theme exceed it.
  const maxCount = Math.max(...themes.map((t) => t.sessionCount), 1);
  const total = Math.max(interviewCount ?? 0, maxCount);

  return (
    <ScrollBox size="tall" label="Themes">
      <ol className="themes">
        {themes.map((theme, i) => (
          <li key={`${i}-${theme.title}`} className="theme">
            <span className="theme-num" aria-hidden="true">
              {String(i + 1).padStart(2, "0")}
            </span>
            <div className="theme-body">
              <h3>{theme.title}</h3>
              <div className="theme-reach">
                {total <= MAX_DOTS ? (
                  <span className="dot-meter" aria-hidden="true">
                    {Array.from({ length: total }, (_, j) => (
                      <span key={j} className={j < theme.sessionCount ? "on" : undefined} />
                    ))}
                  </span>
                ) : (
                  <span className="theme-bar" aria-hidden="true">
                    <div style={{ width: `${(theme.sessionCount / total) * 100}%` }} />
                  </span>
                )}
                <span>
                  Raised in {theme.sessionCount} of {total} {total === 1 ? "interview" : "interviews"}
                </span>
              </div>
              <p>{theme.summary}</p>
            </div>
            {theme.quotes.length > 0 && (
              <div className="theme-evidence">
                <QuoteList studyId={studyId} quotes={theme.quotes.slice(0, SHOWN_QUOTES)} />
                {theme.quotes.length > SHOWN_QUOTES && (
                  <details>
                    <summary>
                      {theme.quotes.length - SHOWN_QUOTES} more{" "}
                      {theme.quotes.length - SHOWN_QUOTES === 1 ? "quote" : "quotes"}
                    </summary>
                    <QuoteList studyId={studyId} quotes={theme.quotes.slice(SHOWN_QUOTES)} />
                  </details>
                )}
              </div>
            )}
          </li>
        ))}
      </ol>
    </ScrollBox>
  );
}
