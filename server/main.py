"""Fieldwork session orchestrator (real-time plane).

One WebSocket per interview. The browser streams 16 kHz PCM mic audio up; we relay it to
AssemblyAI Realtime STT, run the LLM on each end-of-turn, and stream the reply back as
Polly PCM audio. Barge-in: if the participant starts talking while the interviewer is
thinking or speaking, we cancel the reply and tell the browser to drop queued audio.

Browser <-> orchestrator protocol
  up   binary : Int16 PCM, 16 kHz mono (mic)
  up   text   : {"type": "playback_done"}          browser finished playing the agent's audio
  down binary : Int16 PCM, 16 kHz mono (agent voice)
  down text   : {"type": "state", "value": "listening" | "thinking" | "speaking"}
                {"type": "user", "text": str, "final": bool}   live caption
                {"type": "agent", "text": str, "fallback": bool} one sentence of the reply
                {"type": "agent_done"}                          no more audio for this reply
                {"type": "interrupt"}                           drop all queued agent audio
                {"type": "progress", "covered": int, "total": int} seed topics answered
                {"type": "end"}                                 interviewer wrapped up

Natural ending: after every answer a note-taker model (off the latency path) marks each
seed question as done / partial / no. Each reply gets a hidden note steering toward what's
left, and once everything is done (or the participant wants out, or a turn cap is hit) the
orchestrator, not the interview model, turns the next reply into a goodbye and hangs up.
"""

import asyncio
import contextlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

import boto3
import websockets
from boto3.dynamodb.conditions import Key
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from openai import AsyncOpenAI

load_dotenv()
app = FastAPI()

ASSEMBLY_API_KEY = os.environ["ASSEMBLY_API"]
FEATHERLESS_API_KEY = os.environ["FEATHERLESS_API"]
INTERVIEW_MODEL = os.environ.get("INTERVIEW_MODEL", "Qwen/Qwen2.5-7B-Instruct")
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash")
FIRST_TOKEN_TIMEOUT = float(os.environ.get("FIRST_TOKEN_TIMEOUT", "4"))
COVERAGE_MODEL = os.environ.get("COVERAGE_MODEL", FALLBACK_MODEL)
MAX_USER_TURNS = int(os.environ.get("MAX_USER_TURNS", "14"))
POLLY_VOICE = os.environ.get("POLLY_VOICE", "Matthew")
MAX_SESSION_SEC = int(os.environ.get("MAX_SESSION_SEC", "1200"))
# Comma-separated list of browser origins allowed to open an interview socket. Empty = any.
ALLOWED_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()}

dynamodb = boto3.resource("dynamodb")
studies_table = dynamodb.Table(os.environ["STUDIES_TABLE"])
sessions_table = dynamodb.Table(os.environ["SESSIONS_TABLE"])
s3 = boto3.client("s3")
BUCKET = os.environ["TRANSCRIPTS_BUCKET"]
sqs = boto3.client("sqs")
EXTRACTION_QUEUE_URL = os.environ.get("EXTRACTION_QUEUE_URL")
polly = boto3.client("polly")

CLIENT = AsyncOpenAI(base_url="https://api.featherless.ai/v1", api_key=FEATHERLESS_API_KEY)

END_MARKER = "[END]"
MIN_TURNS_BEFORE_END = 3
AAI_MIN_CHUNK = 1600  # 50 ms of 16 kHz Int16 audio
KICKOFF = "[The participant has joined the call. Greet them and ask your first question.]"
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
COVERAGE_RANK = {"no": 0, "partial": 1, "done": 2}
# "I want to leave" can't wait a turn for the note-taker, so catch the obvious phrasings
# instantly. Narrow on purpose: "I have to go to the store" must not end the call.
# ...only when leaving ends the clause: "I have to go now." yes, "I need to go shopping" no.
_CLAUSE_END = r"(?:\s+(?:now|soon|right now|in a (?:sec|second|minute)))?(?=\s*(?:[.,!;]|$|\s(?:so|sorry|but|because)\b))"
WANTS_TO_STOP = re.compile(
    r"\b(?:i|we)(?:'ve| have| need| got)? ?(?:got|have|need|gotta) to (?:go|run|leave|head out|hop off|jump off)" + _CLAUSE_END +
    r"|\bgotta (?:go|run|leave)" + _CLAUSE_END +
    r"|\b(?:can|could) we (?:stop|end|wrap up|finish)\b"
    r"|\blet'?s (?:stop|end|wrap up|finish)\b"
    r"|\b(?:that'?s all|i'?m done|i'?m finished)\W*$"
    r"|\b(?:good)?bye\W*$",
    re.IGNORECASE,
)
GOODBYE = "That's everything I wanted to ask. Thank you so much for your time, this was really helpful. Goodbye!"
NOTE = "[Note to the interviewer, not said by the participant: {}]"
WRAP_NOTES = {
    "goal_covered": "you now have everything you need. Reply to what they just said in one short "
    "sentence, thank them for their time, and say goodbye. Do not ask any more questions.",
    "participant_stopped": "the participant wants to finish. Thank them for their time and say "
    "goodbye in one or two short sentences. Do not ask any more questions.",
    "turn_cap": "time is up. Thank them for their time and say goodbye in one or two short "
    "sentences. Do not ask any more questions.",
}
COVERAGE_PROMPT = """You are the note-taker for a live user interview. Research goal: {goal}

Topics the interviewer must cover:
{topics}

Transcript so far:
{transcript}

For each topic, in order, judge whether the participant has answered it well enough for the research goal:
- "done": they gave a real answer with at least one concrete detail, example or reason
- "partial": they touched on it, but the answer is thin or vague
- "no": not discussed yet
Also decide whether the participant has asked to stop or said they need to leave.

Reply with JSON only, no other text: {{"topics": ["done" | "partial" | "no", ...], "wants_to_stop": true | false}}"""
WORD = re.compile(r"[a-z0-9']+")


class EndInterview(Exception):
    """Raised inside the task group to finish the call on the interviewer's side."""


def connect_to_assemblyai():
    params = {
        "sample_rate": 16000,
        "speech_model": "universal-3-6-pro",
        "mode": "balanced",
        # Interview answers have thinking pauses; don't end the turn on the first comma-length gap.
        "min_turn_silence": os.environ.get("MIN_TURN_SILENCE_MS", "560"),
        "max_turn_silence": os.environ.get("MAX_TURN_SILENCE_MS", "2400"),
    }
    url = f"wss://streaming.assemblyai.com/v3/ws?{urlencode(params)}"
    return websockets.connect(url, additional_headers={"Authorization": ASSEMBLY_API_KEY})


def build_system_prompt(study: dict) -> str:
    goal = study.get("goal", "")
    seed_questions = "\n".join(f"- {q}" for q in study.get("seedQuestions", []))
    return f"""You are an expert user researcher conducting a live voice interview.

Your research goal: {goal}

Seed questions to cover:
{seed_questions}

Rules:
- Ask exactly ONE open-ended question per turn. Never stack multiple questions.
- Dig into specifics and emotions. When they mention a frustration, ask them to walk you through the last time it happened, or why it mattered.
- Keep it short and conversational. You are speaking out loud, so use 1-2 short sentences, no lists, no markdown, no emoji.
- Do not give advice, opinions, or answer factual/trivia questions. If they go off-topic, gently steer back to the research goal.
- If you were cut off mid-sentence, don't repeat yourself; respond to what they just said.
- Start by warmly introducing yourself in one sentence and asking your first question.
- You may receive bracketed notes to the interviewer. They are private guidance, never read them out or mention them.
- Don't end the interview on your own; a note will tell you when to wrap up. Only if the participant clearly asks to stop, thank them in one sentence and end your message with {END_MARKER}."""


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Session:
    def __init__(self, websocket: WebSocket, study: dict):
        self.ws = websocket
        self.study_id = study["studyId"]
        self.session_id = uuid.uuid4().hex
        self.system_prompt = build_system_prompt(study)
        self.goal = study.get("goal", "")
        self.topics = [q for q in study.get("seedQuestions", []) if q.strip()] or [self.goal or "the research goal"]
        self.coverage = ["no"] * len(self.topics)  # per seed question: no / partial / done
        self.wants_to_stop = False
        self.coverage_task: asyncio.Task | None = None
        self.coverage_stale = False  # answers arrived that no check has seen yet
        self.end_reason: str | None = None
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": KICKOFF},
        ]
        # What actually happened in the call, persisted for extraction + the transcript viewer.
        self.transcript: list[dict] = []
        self.state = "listening"
        self.reply_task: asyncio.Task | None = None
        self.agent_text = ""  # everything the agent said this reply, for echo detection
        self.wrapping_up = False
        self.playback_ended_at = 0.0  # loop time when the browser last finished playing us
        self.send_lock = asyncio.Lock()

    # ---- sending ---------------------------------------------------------------------------
    async def send_json(self, payload: dict):
        async with self.send_lock:
            await self.ws.send_text(json.dumps(payload))

    async def send_bytes(self, data: bytes):
        async with self.send_lock:
            await self.ws.send_bytes(data)

    async def set_state(self, value: str):
        if self.state != value:
            self.state = value
            await self.send_json({"type": "state", "value": value})

    # ---- agent turn ------------------------------------------------------------------------
    def start_reply(self):
        self.agent_text = ""
        self.reply_task = asyncio.create_task(self.respond())

    async def respond(self):
        await self.set_state("thinking")
        spoken: list[str] = []
        interrupted = False
        reason = self.wrap_reason()
        self.wrapping_up = reason is not None
        self.end_reason = reason
        if reason:
            print(f"[{self.session_id}] wrapping up: {reason}")
        try:
            buf = ""
            asked = False
            replies = self.llm_text(self.steered_messages(reason))
            try:
                async for text in replies:
                    buf += text
                    # Speak each sentence as soon as it's complete; Polly works on sentence 1
                    # while the LLM is still writing sentence 2.
                    parts = SENTENCE_END.split(buf)
                    for sentence in parts[:-1]:
                        if self.wrapping_up and sentence.rstrip().endswith("?"):
                            continue  # a goodbye must not ask something we'll never hear answered
                        await self.speak(sentence, spoken)
                        if sentence.rstrip().endswith("?"):
                            asked = True  # one question per turn, even if the model stacks more
                            break
                    buf = parts[-1]
                    if asked:
                        break
            finally:
                await replies.aclose()
            if not asked and not (self.wrapping_up and buf.rstrip().endswith("?")):
                await self.speak(buf, spoken)
            if self.wrapping_up and not spoken:
                await self.speak(GOODBYE, spoken)
            await self.send_json({"type": "agent_done"})
        except asyncio.CancelledError:
            interrupted = True
            raise
        except Exception as e:
            print(f"[{self.session_id}] reply failed: {e!r}")
            if not spoken:
                await self.speak("Sorry, I lost my train of thought for a second. Could you say that again?", spoken)
            await self.send_json({"type": "agent_done"})
        finally:
            text = " ".join(spoken)
            if text:
                self.messages.append({"role": "assistant", "content": text})
                self.transcript.append(
                    {"role": "assistant", "content": text, "at": now_iso(), "interrupted": interrupted}
                )
            if not spoken and self.state == "thinking":
                # Nothing was said, so there is no playback_done coming.
                with contextlib.suppress(Exception):
                    await self.set_state("listening")

    async def llm_text(self, messages: list[dict]):
        """Stream the reply's text. If the first words don't arrive within FIRST_TOKEN_TIMEOUT,
        give up on that model and retry once on the fallback, so a provider hiccup costs a
        few seconds instead of a dead-air interview."""
        loop = asyncio.get_running_loop()
        for attempt, model in enumerate((INTERVIEW_MODEL, FALLBACK_MODEL)):
            last = attempt == 1
            started = loop.time()
            stream = None
            try:
                async with asyncio.timeout(None if last else FIRST_TOKEN_TIMEOUT):
                    stream = await CLIENT.chat.completions.create(
                        model=model,
                        messages=messages,
                        stream=True,
                        max_tokens=200,
                        temperature=0.6,
                        # DeepSeek is a reasoning model; thinking would eat the latency budget.
                        extra_body={"chat_template_kwargs": {"thinking": False}} if "DeepSeek" in model else None,
                    )
                    chunks = aiter(stream)
                    first = ""
                    while not first:
                        chunk = await anext(chunks)
                        first = chunk.choices[0].delta.content if chunk.choices else ""
            except TimeoutError:
                print(f"[{self.session_id}] {model} slow (> {FIRST_TOKEN_TIMEOUT}s to first token), retrying")
                if stream is not None:
                    await stream.close()
                continue
            except StopAsyncIteration:
                return
            print(f"[{self.session_id}] llm first token {loop.time() - started:.2f}s ({model})")
            try:
                yield first
                async for chunk in chunks:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
            finally:
                await stream.close()
            return

    async def speak(self, sentence: str, spoken: list[str]):
        if END_MARKER in sentence:
            # Small models sometimes tack the marker onto the greeting; only trust it once
            # the interview has actually happened. The participant can always hang up.
            if self.user_turns() >= MIN_TURNS_BEFORE_END and not self.wrapping_up:
                self.wrapping_up = True
                self.end_reason = "participant_stopped"
            sentence = sentence.replace(END_MARKER, "")
        sentence = sentence.strip()
        if not sentence:
            return
        spoken.append(sentence)
        self.agent_text += " " + sentence
        started = asyncio.get_running_loop().time()
        try:
            resp = await asyncio.to_thread(
                polly.synthesize_speech,
                Engine="generative",
                VoiceId=POLLY_VOICE,
                OutputFormat="pcm",
                SampleRate="16000",
                Text=sentence,
            )
        except Exception as e:
            # Keep the interview going on the browser's built-in voice.
            print(f"[{self.session_id}] polly failed, falling back to browser TTS: {e!r}")
            await self.set_state("speaking")
            await self.send_json({"type": "agent", "text": sentence, "fallback": True})
            return

        if len(spoken) == 1:
            print(f"[{self.session_id}] polly first sentence {asyncio.get_running_loop().time() - started:.2f}s")
        await self.set_state("speaking")
        await self.send_json({"type": "agent", "text": sentence, "fallback": False})
        audio = resp["AudioStream"]
        carry = b""
        try:
            while True:
                chunk = await asyncio.to_thread(audio.read, 8192)
                if not chunk:
                    break
                chunk = carry + chunk
                cut = len(chunk) - (len(chunk) % 2)  # Int16 samples must not straddle frames
                carry = chunk[cut:]
                if cut:
                    await self.send_bytes(chunk[:cut])
        finally:
            audio.close()

    # ---- natural ending ---------------------------------------------------------------------
    def wrap_reason(self) -> str | None:
        """Whether this reply should be the goodbye. Decided here, not by the interview model."""
        turns = self.user_turns()
        if not turns:
            return None  # the greeting
        if self.wants_to_stop:
            return "participant_stopped"
        if turns >= MIN_TURNS_BEFORE_END and all(c == "done" for c in self.coverage):
            return "goal_covered"
        if turns >= MAX_USER_TURNS:
            return "turn_cap"
        return None

    def steered_messages(self, reason: str | None) -> list[dict]:
        """The conversation plus a private note on the participant's latest message."""
        last = self.messages[-1]
        if last["role"] != "user" or last["content"] == KICKOFF:
            return self.messages
        if reason:
            note = WRAP_NOTES[reason]
        else:
            left = [q for q, c in zip(self.topics, self.coverage) if c != "done"]
            if not left:
                return self.messages
            note = (
                f"topics still to cover: {' | '.join(left)}. If their last answer has something "
                "worth digging into, follow up on that first; otherwise move on to one of these."
            )
        return self.messages[:-1] + [{"role": "user", "content": f"{last['content']}\n\n{NOTE.format(note)}"}]

    def start_coverage_check(self):
        # One check at a time; if answers pile up while the model is slow, the running check
        # finishes (its verdict still counts) and one more runs over the latest transcript.
        self.coverage_stale = True
        if not self.coverage_task or self.coverage_task.done():
            self.coverage_task = asyncio.create_task(self.coverage_loop())

    async def coverage_loop(self):
        while self.coverage_stale:
            self.coverage_stale = False
            await self.check_coverage()

    async def check_coverage(self):
        """Ask the note-taker model which seed questions have been answered. Runs alongside the
        reply, so its verdict steers the next turn rather than delaying this one."""
        transcript = "\n".join(
            f"{'Interviewer' if t['role'] == 'assistant' else 'Participant'}: {t['content']}" for t in self.transcript
        )
        prompt = COVERAGE_PROMPT.format(
            goal=self.goal,
            topics="\n".join(f"{i + 1}. {q}" for i, q in enumerate(self.topics)),
            transcript=transcript,
        )
        try:
            async with asyncio.timeout(20):
                resp = await CLIENT.chat.completions.create(
                    model=COVERAGE_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=150,
                    temperature=0,
                    extra_body={"chat_template_kwargs": {"thinking": False}} if "DeepSeek" in COVERAGE_MODEL else None,
                )
            raw = resp.choices[0].message.content or ""
            data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            verdicts = [v if v in COVERAGE_RANK else "no" for v in data.get("topics", [])]
            if len(verdicts) != len(self.topics):
                raise ValueError(f"expected {len(self.topics)} verdicts, got {raw!r}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[{self.session_id}] coverage check failed: {e!r}")
            return
        # Never un-cover a topic: a later off-topic answer doesn't erase an earlier good one.
        self.coverage = [max(old, new, key=COVERAGE_RANK.get) for old, new in zip(self.coverage, verdicts)]
        self.wants_to_stop = self.wants_to_stop or data.get("wants_to_stop") is True
        print(f"[{self.session_id}] coverage {self.coverage} wants_to_stop={self.wants_to_stop}")
        with contextlib.suppress(Exception):
            await self.send_json({"type": "progress", "covered": self.covered(), "total": len(self.topics)})

    def covered(self) -> int:
        return sum(1 for c in self.coverage if c == "done")

    async def interrupt(self):
        task = self.reply_task
        self.reply_task = None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.wrapping_up = False
        await self.send_json({"type": "interrupt"})
        await self.set_state("listening")

    def user_turns(self) -> int:
        return sum(1 for t in self.transcript if t["role"] == "user")

    def is_echo(self, text: str) -> bool:
        """True if what STT heard is most likely our own voice leaking from the speakers."""
        heard = words(text)
        if not heard or not self.agent_text:
            return False
        said = set(words(self.agent_text))
        overlap = sum(1 for w in heard if w in said) / len(heard)
        if self.agent_busy():
            return overlap >= 0.7
        # STT lags a little, so an echo of our last words can land just after playback ends.
        recent = asyncio.get_running_loop().time() - self.playback_ended_at < 3
        return recent and len(heard) >= 3 and overlap >= 0.8

    def agent_busy(self) -> bool:
        return self.state in ("thinking", "speaking")

    # ---- STT events ------------------------------------------------------------------------
    async def on_partial(self, text: str):
        if not text.strip():
            return
        if self.agent_busy():
            if self.is_echo(text) or len(words(text)) < 2:
                return
            await self.interrupt()
        await self.send_json({"type": "user", "text": text, "final": False})

    async def on_end_of_turn(self, text: str):
        text = text.strip()
        if not text or self.is_echo(text):
            return
        if self.agent_busy():
            await self.interrupt()
        await self.send_json({"type": "user", "text": text, "final": True})
        self.transcript.append({"role": "user", "content": text, "at": now_iso()})
        # If they kept talking after an interruption, fold it into the same user turn.
        if self.messages[-1]["role"] == "user" and self.messages[-1]["content"] != KICKOFF:
            self.messages[-1]["content"] += " " + text
        else:
            self.messages.append({"role": "user", "content": text})
        if WANTS_TO_STOP.search(text):
            self.wants_to_stop = True
        self.start_coverage_check()
        self.start_reply()

    async def on_playback_done(self):
        if self.state == "speaking" and (self.reply_task is None or self.reply_task.done()):
            if self.wrapping_up:
                await self.send_json({"type": "end"})
                raise EndInterview()
            self.playback_ended_at = asyncio.get_running_loop().time()
            await self.set_state("listening")

    # ---- persistence ------------------------------------------------------------------------
    async def persist(self, status: str):
        user_turns = self.user_turns()
        if not user_turns:
            print(f"[{self.session_id}] no answers recorded, not saving the session")
            return
        key = f"transcripts/{self.study_id}/{self.session_id}.json"
        body = [{"role": "system", "content": self.system_prompt}] + self.transcript
        await asyncio.to_thread(
            s3.put_object, Bucket=BUCKET, Key=key, Body=json.dumps(body).encode(), ContentType="application/json"
        )
        await asyncio.to_thread(
            sessions_table.put_item,
            Item={
                "studyId": self.study_id,
                "sessionId": self.session_id,
                "endedAt": now_iso(),
                "turnCount": user_turns,
                "transcriptKey": key,
                "status": status,
                "topicsCovered": self.covered(),
                "topicsTotal": len(self.topics),
                "endReason": self.end_reason or "unknown",
            },
        )
        if EXTRACTION_QUEUE_URL:
            await asyncio.to_thread(
                sqs.send_message,
                QueueUrl=EXTRACTION_QUEUE_URL,
                MessageBody=json.dumps({"studyId": self.study_id, "sessionId": self.session_id}),
            )


@app.on_event("startup")
async def warm_up():
    # The first Polly call on a fresh process pays for credentials + TLS setup (seconds).
    # Pay it here instead of in the first interview's greeting.
    with contextlib.suppress(Exception):
        await asyncio.to_thread(
            polly.synthesize_speech, Engine="generative", VoiceId=POLLY_VOICE,
            OutputFormat="pcm", SampleRate="16000", Text="Hi.",
        )


@app.get("/")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    origin = (websocket.headers.get("origin") or "").rstrip("/")
    if ALLOWED_ORIGINS and origin not in ALLOWED_ORIGINS:
        await websocket.close(code=1008)
        return

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return

    resp = await asyncio.to_thread(
        studies_table.query,
        IndexName="byInviteToken",
        KeyConditionExpression=Key("inviteToken").eq(token),
    )
    items = resp.get("Items", [])
    if not items or items[0].get("status") != "live":
        await websocket.close(code=1008)
        return

    await websocket.accept()
    session = Session(websocket, items[0])
    print(f"[{session.session_id}] interview started for study {session.study_id}")
    status = "aborted"

    try:
        async with connect_to_assemblyai() as aai_ws:

            async def browser_to_aai():
                pending = b""
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        raise WebSocketDisconnect(msg.get("code", 1000))
                    if msg.get("bytes"):
                        # AssemblyAI rejects chunks shorter than 50 ms, so re-frame whatever
                        # the browser sends into 50-100 ms pieces.
                        pending += msg["bytes"]
                        while len(pending) >= AAI_MIN_CHUNK:
                            take = min(len(pending) - len(pending) % 2, AAI_MIN_CHUNK * 2)
                            await aai_ws.send(pending[:take])
                            pending = pending[take:]
                    elif msg.get("text"):
                        with contextlib.suppress(json.JSONDecodeError):
                            if json.loads(msg["text"]).get("type") == "playback_done":
                                await session.on_playback_done()

            async def aai_to_browser():
                async for message in aai_ws:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    kind = data.get("type")
                    if kind == "Turn":
                        if data.get("end_of_turn"):
                            await session.on_end_of_turn(data.get("transcript", ""))
                        else:
                            await session.on_partial(data.get("transcript", ""))
                    elif kind == "Error" or "error" in data:
                        print(f"[{session.session_id}] STT error: {data}")
                    elif kind == "Termination":
                        print(f"[{session.session_id}] STT terminated: {data}")
                        break
                raise EndInterview()  # STT side closed, nothing more we can hear

            session.start_reply()  # the interviewer speaks first
            try:
                async with asyncio.timeout(MAX_SESSION_SEC):
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(browser_to_aai())
                        tg.create_task(aai_to_browser())
            except* WebSocketDisconnect:
                status = "completed"
                session.end_reason = session.end_reason or "participant_hung_up"
                print(f"[{session.session_id}] participant hung up")
            except* EndInterview:
                status = "completed"
                print(f"[{session.session_id}] interviewer wrapped up")
            except* TimeoutError:
                status = "completed"
                session.end_reason = "time_cap"
                print(f"[{session.session_id}] hit the {MAX_SESSION_SEC}s session cap")
    except* Exception as eg:
        print(f"[{session.session_id}] session error: {eg.exceptions!r}")
    finally:
        if session.coverage_task and not session.coverage_task.done():
            session.coverage_task.cancel()
        if session.reply_task and not session.reply_task.done():
            session.reply_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await session.reply_task
        try:
            await session.persist(status)
        except Exception as e:
            print(f"[{session.session_id}] error persisting session: {e!r}")
        with contextlib.suppress(Exception):
            await websocket.close()
