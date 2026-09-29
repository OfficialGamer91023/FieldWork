# Fieldwork frontend

Next.js (App Router) app with two surfaces:

- **Founder dashboard**
  - `/` lists studies; `/studies/new` creates one.
  - `/studies/[id]` publishes a study (draft → live), shows the invite link, lists every
    interview with how many topics it covered, and runs **Synthesize** to show ranked themes.
  - `/studies/[id]/sessions/[sessionId]` is the transcript viewer. Theme quotes deep-link
    here and the quoted line is highlighted.
- **Respondent interview page** (`/interview/[token]`) resolves the invite, then runs the
  mic → WebSocket voice loop against the orchestrator, with live captions and a
  topics-covered indicator.

## Architecture notes

- **Control-plane calls run server-side** (Server Components + Server Actions in
  `app/actions.ts`, through `apiFetch` in `app/lib/api.ts`). The browser never calls API
  Gateway directly, so there is **no CORS** to configure on the Lambda, and
  `CONTROL_PLANE_API_URL` stays off the client.
- **Synthesis is polled, not awaited.** `SynthesisPanel` starts the run via a server action
  (the API answers `202`), then calls `router.refresh()` every 2.5 s until the study's
  `synthesisStatus` is `DONE` or `FAILED`.
- The **respondent WebSocket is client-side** (browser → orchestrator), using
  `NEXT_PUBLIC_ORCHESTRATOR_WS_URL`. It appends `?token=<inviteToken>`; the orchestrator
  re-resolves the token server-side and gates on `status == "live"`.
- `public/processor.js` is the AudioWorklet that downsamples mic audio to 16 kHz Int16 PCM.
  Agent audio comes back as 16 kHz PCM and is scheduled gap-free; an `interrupt` message
  flushes the queue (barge-in).

## Setup

```bash
cd frontend
cp .env.local.example .env.local   # then edit values
npm install
npm run dev                        # http://localhost:3000
```

`.env.local`:

| var | scope | value |
| --- | --- | --- |
| `CONTROL_PLANE_API_URL` | server | `control_plane_api_url` Terraform output (printed by `infra/up.sh`) |
| `NEXT_PUBLIC_ORCHESTRATOR_WS_URL` | browser | `ws://localhost:8000` (local orchestrator) or the `orchestrator_ws_url` output (`wss://…cloudfront.net`) |
| `NEXT_PUBLIC_APP_URL` | browser | `http://localhost:3000`, or the deployed URL (used to render invite links) |

`NEXT_PUBLIC_*` values are baked into the browser bundle at build time, so redeploy after
changing them. A page served over `https` can only open a `wss://` socket, which is why the
deployed orchestrator sits behind CloudFront.

## Local end-to-end

1. Bring up the backend with `./infra/up.sh` from the repo root.
2. Either use the deployed orchestrator URL, or run it locally (`uvicorn main:app --port 8000`
   in `server/`, with the table/bucket/queue names in the root `.env`).
3. `npm run dev` here.
4. Create a study → **Publish** → open the interview link → **Start interview**.
5. After a few interviews, open the study and click **Synthesize**.

## Deploy (Vercel)

Root directory `frontend/`, and set the three variables above in Project → Settings →
Environment Variables. To lock interview sockets to this site, re-apply Terraform with
`-var allowed_origins=https://your-app.vercel.app`.
