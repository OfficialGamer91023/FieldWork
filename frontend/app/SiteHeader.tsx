"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Participants arrive on /interview/...; they shouldn't see the founder's controls.
export default function SiteHeader() {
  const participant = usePathname().startsWith("/interview/");
  return (
    <header className="site-header">
      <div className="container">
        {participant ? (
          <span className="brand">Fieldwork</span>
        ) : (
          <>
            <Link href="/" className="brand">
              Fieldwork
            </Link>
            <Link href="/studies/new" className="btn">
              New study
            </Link>
          </>
        )}
      </div>
    </header>
  );
}
