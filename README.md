# Fieldwork

**Automated, adaptive voice user-interviews for founders.**

A founder defines a research goal and a handful of seed questions. Fieldwork then runs
real, *adaptive* voice interviews with their users — it asks follow-ups instead of reading
a fixed script — and **synthesizes themes across all interviews** into a dashboard. The
output isn't 50 transcripts; it's *"4 ranked themes, with the exact quotes that support
each one."*

Built for the **AssemblyAI Voice Agent Hackathon** (Sep 2026). Solo project, learning-first:
the goal is to learn AWS and distributed-systems design by building each piece by hand
rather than reaching for an all-in-one API.

> **Current status: Phase 0 (in progress).** A local browser → WebSocket → Python loop
> that streams raw microphone audio to the backend. STT/LLM/TTS are not wired in yet.
> See [Roadmap](#roadmap) and [What actually runs today](#what-actually-runs-today).

---

## The core idea: three planes

The system is deliberately split into three planes with very different constraints. Keeping
them separate is the central design decision.

| Plane | Purpose | Constraint | Compute style |
|---|---|---|---|
| **Control** | Founder sets up studies, reads results | CRUD, low traffic | REST + serverless / DB |
| **Real-time** | The live interview | Latency-critical, long-lived, stateful | Long-running container |
| **Async** | Post-call extraction + synthesis | Throughput, bursty, fan-out | Serverless workers |

A voice call is a long-lived, stateful connection — a poor fit for Lambda's 15-minute cap
and cold starts, which is exactly *why* containers exist. The post-call jobs are short,
stateless, and bursty — Lambda's sweet spot. Building both is the point.

## Architecture (target)

```
CONTROL PLANE
  Founder ─▶ Next.js dashboard (CloudFront + S3)
              └▶ API Gateway (REST) ─▶ Cognito (auth)
                    └▶ DynamoDB (studies, questions, sessions, computed themes)

REAL-TIME PLANE  (latency-critical)
  Respondent browser (WebAudio mic capture)
      │ audio frames over WebSocket
      ▼
  API Gateway (WebSocket)
      ▼
  Session Orchestrator  (ECS Fargate, long-lived)   ◀── we own the orchestration:
      ├─ audio  ─▶ AssemblyAI Realtime STT ─▶ partial/final transcripts + endpointing
      ├─ turn text ─▶ LLM (Featherless) ─▶ next adaptive follow-up
      ├─ reply text ─▶ TTS (SpeechSynthesis → Polly → ElevenLabs) ─▶ audio back to browser
      ├─ turn-taking / endpointing · barge-in (cancel in-flight LLM+TTS)
      └─ live session state in Redis (ElastiCache)
      │ on CALL END: transcript → S3, emit "InterviewCompleted"
      ▼
  S3 (raw audio + transcript)  +  EventBridge event

ASYNC PLANE  (throughput, fan-out)
  EventBridge / SQS (+ retries, DLQ)
      ├─ Lambda: Extract   (per-question answers, sentiment, quotes → DynamoDB)
      ├─ Lambda: Embed     (vectors → OpenSearch / pgvector)
      └─ Lambda: Synthesize (cluster a study's answers → ranked themes → DynamoDB)
              └▶ dashboard reads themes (back to Control Plane)

Cross-cutting: CloudWatch + OpenTelemetry · Secrets Manager · Terraform · GitHub Actions
```

### The two hard problems (with deliberate fallbacks)

Scope is guarded by building the "dumb but working" version first and upgrading only if
time allows:

- **Adaptive interviewing** — if follow-ups are dumb, it's a survey with extra steps.
  *Fallback:* seed questions + one LLM-generated follow-up each. *Upgrade:* multi-turn
  probing driven by the running transcript.
- **Cross-interview synthesis** — theme clustering across N interviews can eat a week.
  *Fallback:* a single LLM pass that summarizes all transcripts for a study into themes.
  *Upgrade:* embeddings + vector clustering, then incremental re-clustering as each
  interview completes.

## Tech stack

- **Frontend:** Next.js (founder dashboard + respondent interview page)
- **Real-time compute:** ECS Fargate — a session orchestrator that holds the WebSocket and
  owns turn-taking, the LLM call, TTS streaming, and barge-in
- **STT:** AssemblyAI Realtime Speech-to-Text (WebSocket)
- **LLM:** Featherless (OpenAI-compatible, swappable), small fast model
- **TTS:** pluggable — browser `SpeechSynthesis` (Phase 0) → AWS Polly (dev) → ElevenLabs
  Flash v2.5 (final demo)
- **Async:** EventBridge / SQS + Lambda workers (extract, embed, synthesize)
- **Data:** DynamoDB (structured), S3 (audio/transcripts), OpenSearch or pgvector
  (embeddings), ElastiCache/Redis (live session state)
- **Platform:** Cognito, Secrets Manager, CloudWatch + OpenTelemetry, GitHub Actions
- **Language:** Python (orchestrator + Lambdas); TypeScript (Next.js). **IaC:** Terraform (HCL)

## Repository layout

```
.
├── server/               # Phase 0 local backend
│   ├── main.py           #   FastAPI app: serves the mic page, WebSocket at /ws
│   └── static/
│       └── processor.js  #   AudioWorklet: captures mic, emits 16 kHz Int16 PCM chunks
└── infra/
    └── hello/            # First Terraform config (learning): creates one S3 bucket
        ├── main.tf
        └── .terraform.lock.hcl
```

> Terraform state (`*.tfstate`) and the downloaded provider binaries (`.terraform/`) are
> gitignored — state can contain secrets and the binaries are large and platform-specific.
> Run `terraform init` to fetch providers locally.

## What actually runs today

**`server/`** — the Phase 0 real-time loop, local only, no AWS:

1. The browser page opens a WebSocket to `ws://localhost:8000/ws`.
2. An `AudioWorklet` (`static/processor.js`) captures the microphone and streams raw
   **16 kHz mono Int16 PCM** chunks over the socket.
3. The FastAPI backend receives the byte frames and, for now, echoes back the length of
   each chunk — proving the end-to-end audio path before STT/LLM/TTS are added.

**`infra/hello/`** — a first Terraform config that provisions a single S3 bucket
(`fieldwork-tf-hello-<account-id>`). It exists to learn the Terraform init/plan/apply loop,
not because the app needs it yet.

### Run the Phase 0 loop

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install fastapi "uvicorn[standard]"
uvicorn main:app --reload --port 8000
```

Open <http://localhost:8000>, click **Start Recording**, allow mic access, and watch the
`Received bytes of len: …` messages stream in.

### Try the Terraform hello (optional)

Requires AWS credentials (`aws configure`). **This creates a real S3 bucket** — run
`terraform destroy` when done.

```bash
cd infra/hello
terraform init
terraform plan
terraform apply
```

## Roadmap

Each phase leaves a working, demoable system — a time slip just means submitting an
earlier phase.

- **Phase 0 — local voice loop** *(in progress).* Browser mic → local backend →
  AssemblyAI STT → LLM → TTS → audio back. Prove the real-time loop, including turn-taking
  and interruption. *(Today: browser → WS → backend echo is working; STT/LLM/TTS next.)*
- **Phase 1 — on AWS.** Containerize the orchestrator → ECS Fargate, front with API
  Gateway (WebSocket). IaC from day one.
- **Phase 2 — memory + control plane.** DynamoDB (studies/sessions), S3 (transcripts),
  founder dashboard, respondent study links.
- **Phase 3 — async pipeline.** On call-end emit an event → SQS/EventBridge → Lambda
  extract (per-question answers, sentiment, quotes). Retries + DLQ.
- **Phase 4 — synthesis.** Embeddings + vector store, cross-interview theme clustering
  (fallback: single LLM pass; upgrade: incremental clustering).
- **Phase 5 — scale + polish.** Autoscaling for many concurrent calls, distributed
  tracing, then the demo video and submission write-up.

## Conventions

- **IaC for everything** — no click-ops for anything that should be reproducible.
- **Secrets** live in Secrets Manager / env — never in code or committed files.
- Keep the three planes in separate modules/services; don't let control-plane CRUD leak
  into the latency-critical real-time path.
