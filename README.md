# Fieldwork

**Voice user interviews that synthesize themselves.**

<p>
  <img alt="hackathon" src="https://img.shields.io/badge/AssemblyAI-Voice%20Agent%20Hackathon-6a5acd">
  <img alt="stt" src="https://img.shields.io/badge/STT-Universal--3%20Pro%20Streaming-2b6cb0">
  <img alt="infra" src="https://img.shields.io/badge/AWS-Fargate%20%C2%B7%20Lambda%20%C2%B7%20SQS%20%C2%B7%20DynamoDB-ff9900">
  <img alt="iac" src="https://img.shields.io/badge/IaC-Terraform-7b42bc">
</p>

A founder writes down what they want to learn and a few seed questions, then shares one link.
Every user who opens it has a real spoken conversation with an AI interviewer. It asks
follow-ups instead of reading a script, and you can interrupt it mid-sentence. When the
interviews are in, Fieldwork ranks the themes that came up across all of them. Every theme
is backed by the participants' exact words, and each quote links to the line of the
transcript it came from.

The output isn't fifty transcripts. It's *"4 ranked themes, and here's who said what."*

**Demo video:** _add link_ · **Live app:** _add link_ · Built solo for the AssemblyAI Voice Agent Hackathon (September 2026).

---

## What it does

**For the founder (dashboard)**
1. Create a study with a research goal and seed questions, then publish it to get an invite link.
2. Watch interviews arrive. Each one is analysed a few seconds after the call ends: key
   takeaways, sentiment and verbatim quotes.
3. Click **Synthesize** to get up to 5 themes, ranked by how many interviews raised them.
   Click any quote to jump to the highlighted line in its transcript.

**For the participant (interview page)**
- No account or install. Open the link, allow the mic and talk.
- The interviewer greets you, asks one question at a time, digs into specifics ("walk me
  through the last time that happened"), and wraps up by itself once the goal is covered.
- Talk over it and it stops and listens (barge-in). Live captions show what it heard.

## How AssemblyAI is used

The real-time plane uses **Universal-3 Pro Streaming** over WebSocket
(`speech_model=universal-3-6-pro`, `mode=balanced`). We run our own orchestration on top:

| Streaming feature | What Fieldwork does with it |
|---|---|
| `end_of_turn` on `Turn` messages | The turn-taking signal. The LLM runs the moment a participant finishes. |
| `min_turn_silence` = 560 ms, `max_turn_silence` = 2400 ms | Tuned for interviews: people pause to think mid-answer, so we wait longer than a support bot would before taking the turn. |
| Partial `Turn` transcripts | **Barge-in.** A partial of two or more words while the interviewer is thinking or speaking cancels the reply and flushes the browser's audio queue. |
| Formatted final transcripts | Stored as the transcript of record. The quotes shown on the dashboard are checked against this text. |

## Architecture

Three planes with very different constraints, kept deliberately separate:

```
CONTROL PLANE (CRUD, low traffic)
  Founder ─▶ Next.js on Vercel ─(server-side only)─▶ API Gateway (HTTP API, throttled)
                                                      └▶ Lambda router ─▶ DynamoDB + S3

REAL-TIME PLANE (latency-critical, long-lived, stateful)
  Participant browser ── wss ──▶ CloudFront ─▶ ALB (CloudFront-only) ─▶ ECS Fargate orchestrator
     mic: AudioWorklet → 16 kHz PCM                                        │
     speaker: gap-free PCM player, flushed on barge-in                     ├─▶ AssemblyAI Universal-3 Pro Streaming
                                                                           ├─▶ LLM (Featherless, streamed, 4 s watchdog → fallback model)
                                                                           └─▶ Amazon Polly generative voice (streamed PCM)
                                          on hang-up: transcript → S3, session → DynamoDB, {studyId, sessionId} → SQS

ASYNC PLANE (bursty, throughput, fan-out)
  SQS extraction ─▶ Lambda extractor ─▶ takeaways, sentiment, verbatim quotes (idempotent write)   + DLQ
  Founder clicks Synthesize ─▶ API ─▶ SQS synthesis ─▶ Lambda synthesizer ─▶ ranked themes on the study row + DLQ
```

A voice call is a long-lived stateful socket, a poor fit for Lambda, so the orchestrator
is a container. Post-call work is short, stateless and bursty, which is exactly what
Lambda is for. The queue between them means a slow LLM never holds up a live call, and a
failed extraction retries by itself and ends up in a dead-letter queue instead of vanishing.

### The real-time loop

```
participant stops talking
  → AssemblyAI end_of_turn
  → LLM streams the reply; each finished sentence goes to Polly immediately
  → Polly streams 16 kHz PCM → WebSocket → browser schedules it gap-free
```

Measured locally with a scripted participant: **about 1.0–1.2 s** from AssemblyAI's
end-of-turn to the first audio byte (LLM first token ~0.7 s, Polly first audio ~0.25 s).

What keeps it feeling like a conversation:
- **Barge-in:** partial transcripts cancel the in-flight LLM and TTS task, and an `interrupt`
  message makes the browser drop every queued audio buffer. What the interviewer actually
  said before being cut off is recorded, flagged `interrupted`.
- **Echo guard:** the browser runs echo cancellation, and the server also ignores
  "speech" that is mostly the interviewer's own words (with a short grace window after
  playback), so it works on laptop speakers without headphones.
- **One question per turn:** the reply stops after its first question even if the model
  stacks several.
- **Provider hiccups:** if the model hasn't produced a token in 4 s, the request is
  abandoned and retried on a fallback model. If Polly fails, that sentence falls back to
  the browser's voice.
- **Natural ending, driven by the research goal:** after every answer a note-taker model
  (running alongside the reply, so it adds no latency) marks each seed question as
  `done` / `partial` / `no`. Each reply gets a private note steering the interviewer toward
  what's still uncovered. Once everything is `done`, the orchestrator (not the
  interview model) turns the next reply into a goodbye and hangs up. "I have to run" is caught
  instantly by a phrase check, so the goodbye doesn't wait for the note-taker. The participant sees
  "2 of 4 topics covered", and the dashboard shows each interview's coverage and why it ended.
  Turn and time caps keep a rambling call bounded.

### Evidence you can trust

The whole pitch is "themes backed by exact quotes", so quotes are verified in code, not
trusted to the LLM:
- The extractor only keeps quotes the participant said verbatim.
- The synthesizer keeps a quote under a theme only if **(a)** it matches a stored quote
  from that interview and **(b)** the model also cited that interview for that theme.
  Each quote is kept under one theme only (the strongest), `sessionCount` is computed
  from citations, and unknown session ids are dropped.
- Every quote on the dashboard deep-links to its transcript line and is highlighted there.

### Reliability details

- **Idempotent consumers:** the extractor writes with `attribute_not_exists(processedAt)`,
  so SQS's at-least-once redelivery can't double-write.
- **Synthesis state machine** on the study row (`PENDING → RUNNING → DONE | FAILED`), with a
  conditional write against double-clicks, stale-run takeover after 480 s, and old themes
  kept when a run fails. The dashboard polls the row instead of holding a request open,
  so API Gateway's 30 s limit never matters.
- **Secrets** live in Secrets Manager: injected into the Fargate task by ECS, and fetched at
  runtime by the Lambdas, so they never enter Terraform state or the repo.
- **Least privilege:** the ALB accepts only CloudFront's origin-facing IP ranges; each
  Lambda and the task role get only the tables, queues and actions they use.

## Tech stack

| Area | Choice |
|---|---|
| Speech-to-text | AssemblyAI Universal-3 Pro Streaming (WebSocket) |
| Interview LLM | Featherless (OpenAI-compatible): Qwen2.5-7B-Instruct, falling back to DeepSeek-V4.1-Flash |
| Analysis LLM | DeepSeek-V4.1-Flash (thinking off) for extraction and synthesis |
| Voice | Amazon Polly generative engine, streamed PCM |
| Real-time compute | FastAPI on ECS Fargate (ARM64) behind ALB + CloudFront |
| Async | SQS (+ DLQs) → Lambda (Python 3.13) |
| Data | DynamoDB (studies, sessions), S3 (transcripts) |
| Frontend | Next.js 15 (App Router, server actions) on Vercel |
| Platform | Terraform, Secrets Manager, CloudWatch Logs |

## Repository layout

```
server/          Real-time plane: session orchestrator (FastAPI, WebSocket /ws)
control-plane/   Control plane: API Gateway → Lambda router (studies, invites, sessions, synthesis)
extract/         Async plane: per-interview extractor Lambda (SQS-triggered)
synthesize/      Async plane: cross-interview synthesizer Lambda (SQS-triggered)
frontend/        Next.js dashboard + participant interview page
infra/app/       Terraform for everything on AWS (single state)
infra/up.sh      One-shot bring-up: ECR + secret → image → everything else
```

## Running it

**Prerequisites:** AWS CLI (logged in), Terraform ≥ 1.6, Docker, Node 20+, Python 3.13+,
and keys for AssemblyAI and Featherless in a root `.env`:

```
ASSEMBLY_API=...
FEATHERLESS_API=...
```

**Deploy the backend (≈10 min, most of it CloudFront):**

```bash
./infra/up.sh     # prints CONTROL_PLANE_API_URL and NEXT_PUBLIC_ORCHESTRATOR_WS_URL
```

**Frontend:** set those two variables plus `NEXT_PUBLIC_APP_URL` on Vercel (root directory
`frontend/`), or put them in `frontend/.env.local` and run `npm run dev`.
Optionally pass `-var allowed_origins=https://your-app.vercel.app` to Terraform so only your
frontend can open interview sockets.

**Run the orchestrator locally** against the deployed tables: add `STUDIES_TABLE`,
`SESSIONS_TABLE`, `TRANSCRIPTS_BUCKET` and `EXTRACTION_QUEUE_URL` to `.env`, then
`uvicorn main:app --port 8000` from `server/`.

**Tear down:** `terraform -chdir=infra/app destroy`.

## What's next

- Real sign-in with Cognito. Founder identity is a single demo account today; the
  `byFounder` index already supports it.
- Automatic re-synthesis as interviews arrive (EventBridge Scheduler sweep), instead of a button.
- Embedding-based clustering for studies with hundreds of interviews. Today it's a single
  LLM pass, which is fine at tens.
- Phone-call interviews over SIP for users who won't click a link.
