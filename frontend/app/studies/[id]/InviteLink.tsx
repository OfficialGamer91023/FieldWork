"use client";

import { useState } from "react";

export default function InviteLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be blocked (insecure context); ignore silently.
    }
  }

  return (
    <div className="invite-box">
      <span style={{ flex: 1 }}>{url}</span>
      <button type="button" className="btn secondary" onClick={copy}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
