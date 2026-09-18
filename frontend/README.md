# Fieldwork frontend

Next.js (App Router) app with two surfaces:

- **Founder dashboard** (`/`, `/studies/new`, `/studies/[id]`) — create studies, publish
  them (draft → live), and copy the participant interview link.
- **Respondent interview page** (`/interview/[token]`) — resolves the invite, then runs the
  mic → WebSocket voice loop against the orchestrator.

## Architecture notes

- **Control-plane calls run server-side** (Server Components + Server Actions in
  `app/actions.ts`). The browser never calls API Gateway directly, so there is **no CORS**
  to configure on the Lambda, and `CONTROL_PLANE_API_URL` stays off the client.
- The **respondent WebSocket is client-side** (browser → orchestrator), using
  `NEXT_PUBLIC_ORCHESTRATOR_WS_URL`. It appends `?token=<inviteToken>`; the orchestrator
  re-resolves the token server-side and gates on `status == "live"`.
- `public/processor.js` is the AudioWorklet that downsamples mic audio to 16 kHz Int16 PCM
  (copied from the orchestrator's `server/static/processor.js`).

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
| `CONTROL_PLANE_API_URL` | server | `control_plane_api_url` Terraform output |
| `NEXT_PUBLIC_ORCHESTRATOR_WS_URL` | browser | `ws://localhost:8000` (local) or `ws://<alb_dns_name>` |
| `NEXT_PUBLIC_APP_URL` | browser | `http://localhost:3000` (used to render invite links) |

## Local end-to-end

1. `terraform apply` in `infra/app/` (adds the `PATCH /studies/{id}` publish route).
2. Run the orchestrator locally (`uvicorn main:app` in `server/`).
3. `npm run dev` here.
4. Create a study → **Publish** → open the interview link → **Start interview**.
