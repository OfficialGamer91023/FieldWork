"use client";

import { useEffect, useRef, useState } from "react";

type Phase = "intro" | "connecting" | "live" | "ended";
type AgentState = "listening" | "thinking" | "speaking";
type Turn = { role: "agent" | "user"; text: string };

type ServerMessage =
  | { type: "state"; value: AgentState }
  | { type: "user"; text: string; final: boolean }
  | { type: "agent"; text: string; fallback: boolean }
  | { type: "agent_done" }
  | { type: "interrupt" }
  | { type: "progress"; covered: number; total: number }
  | { type: "end" };

const SAMPLE_RATE = 16000;

// Plays the interviewer's streamed PCM gap-free, and can be cut off instantly on barge-in.
class Player {
  ctx: AudioContext;
  analyser: AnalyserNode;
  private next = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private speaking = 0; // browser-TTS fallback utterances in flight
  onIdle: () => void = () => {};

  constructor() {
    this.ctx = new AudioContext();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.connect(this.ctx.destination);
  }

  enqueue(data: ArrayBuffer) {
    const pcm = new Int16Array(data);
    if (!pcm.length) return;
    const buffer = this.ctx.createBuffer(1, pcm.length, SAMPLE_RATE);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;

    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.analyser);
    const at = Math.max(this.next, this.ctx.currentTime + 0.03);
    src.start(at);
    this.next = at + buffer.duration;
    this.sources.add(src);
    src.onended = () => {
      this.sources.delete(src);
      if (this.idle) this.onIdle();
    };
  }

  speakFallback(text: string) {
    try {
      const u = new SpeechSynthesisUtterance(text);
      this.speaking++;
      u.onend = u.onerror = () => {
        this.speaking = Math.max(0, this.speaking - 1);
        if (this.idle) this.onIdle();
      };
      window.speechSynthesis.speak(u);
    } catch {}
  }

  get idle() {
    return this.sources.size === 0 && this.speaking === 0;
  }

  stop() {
    for (const src of this.sources) {
      src.onended = null;
      try {
        src.stop();
      } catch {}
    }
    this.sources.clear();
    this.next = 0;
    this.speaking = 0;
    try {
      window.speechSynthesis.cancel();
    } catch {}
  }

  close() {
    this.stop();
    this.ctx.close().catch(() => {});
  }
}

function level(analyser: AnalyserNode | null, buf: Uint8Array<ArrayBuffer>) {
  if (!analyser) return 0;
  analyser.getByteTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = (buf[i] - 128) / 128;
    sum += v * v;
  }
  return Math.min(1, Math.sqrt(sum / buf.length) * 4);
}

export default function InterviewClient({
  token,
  wsBase,
  title,
}: {
  token: string;
  wsBase: string;
  title: string;
}) {
  const [phase, setPhase] = useState<Phase>("intro");
  const [agentState, setAgentState] = useState<AgentState>("thinking");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [partial, setPartial] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [finishedByAgent, setFinishedByAgent] = useState(false);
  const [progress, setProgress] = useState<{ covered: number; total: number } | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const micCtxRef = useRef<AudioContext | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);
  const playerRef = useRef<Player | null>(null);
  const agentDoneRef = useRef(false);
  const replyOpenRef = useRef(false); // an agent bubble is still receiving sentences
  const agentStateRef = useRef<AgentState>("thinking");
  const orbRef = useRef<HTMLDivElement | null>(null);
  const convoRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true); // box is scrolled to the newest line
  const [showJump, setShowJump] = useState(false);

  function teardown() {
    try {
      micRef.current?.getTracks().forEach((t) => t.stop());
    } catch {}
    micRef.current = null;
    micCtxRef.current?.close().catch(() => {});
    micCtxRef.current = null;
    micAnalyserRef.current = null;
    playerRef.current?.close();
    playerRef.current = null;
    const ws = wsRef.current;
    wsRef.current = null;
    try {
      ws?.close();
    } catch {}
  }

  function hangUp() {
    teardown();
    setPartial("");
    setPhase("ended");
  }

  // Release the mic, speakers and socket if the participant navigates away.
  useEffect(() => () => teardown(), []);

  // Keep the conversation box on the newest line, unless the participant scrolled up
  // to reread something; then offer a jump button instead of yanking them back down.
  // Scrolls only the box (scrollIntoView would scroll the whole page too).
  useEffect(() => {
    const box = convoRef.current;
    if (!box) return;
    if (pinnedRef.current) box.scrollTop = box.scrollHeight;
    else setShowJump(true);
  }, [turns, partial]);

  function onConvoScroll() {
    const box = convoRef.current;
    if (!box) return;
    pinnedRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
    if (pinnedRef.current) setShowJump(false);
  }

  function jumpToLatest() {
    const box = convoRef.current;
    if (!box) return;
    pinnedRef.current = true;
    setShowJump(false);
    box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
  }

  // Pulse the orb with whoever is talking.
  useEffect(() => {
    if (phase !== "live") return;
    const buf = new Uint8Array(256);
    let raf = 0;
    const tick = () => {
      const speaking = agentStateRef.current === "speaking";
      const v = level(speaking ? playerRef.current?.analyser ?? null : micAnalyserRef.current, buf);
      orbRef.current?.style.setProperty("--level", v.toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [phase]);

  function maybeReportPlaybackDone() {
    const player = playerRef.current;
    const ws = wsRef.current;
    if (agentDoneRef.current && player?.idle && ws?.readyState === WebSocket.OPEN) {
      agentDoneRef.current = false;
      ws.send(JSON.stringify({ type: "playback_done" }));
    }
  }

  async function startMic(ws: WebSocket) {
    const mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    micRef.current = mic;

    // Ask for a 16 kHz context so the browser resamples for us; Firefox refuses to mix rates,
    // so fall back to the native rate and let the worklet downsample.
    let ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
    let source: MediaStreamAudioSourceNode;
    try {
      source = ctx.createMediaStreamSource(mic);
    } catch {
      await ctx.close();
      ctx = new AudioContext();
      source = ctx.createMediaStreamSource(mic);
    }
    micCtxRef.current = ctx;

    await ctx.audioWorklet.addModule("/processor.js");
    const node = new AudioWorkletNode(ctx, "pcm-processor");
    node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(event.data);
    };
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    micAnalyserRef.current = analyser;
    source.connect(analyser);
    source.connect(node);
    // Keep the worklet pulled by the graph without sending mic audio to the speakers.
    const mute = ctx.createGain();
    mute.gain.value = 0;
    node.connect(mute).connect(ctx.destination);
  }

  function onMessage(msg: ServerMessage) {
    const player = playerRef.current;
    switch (msg.type) {
      case "state":
        agentStateRef.current = msg.value;
        setAgentState(msg.value);
        break;
      case "user":
        if (msg.final) {
          setPartial("");
          setTurns((t) => [...t, { role: "user", text: msg.text }]);
        } else {
          setPartial(msg.text);
        }
        break;
      case "agent": {
        agentDoneRef.current = false;
        // Sentences of one reply arrive separately; show them as one bubble.
        const append = replyOpenRef.current;
        replyOpenRef.current = true;
        setTurns((t) => {
          const last = t[t.length - 1];
          if (append && last?.role === "agent") {
            return [...t.slice(0, -1), { role: "agent", text: `${last.text} ${msg.text}` }];
          }
          return [...t, { role: "agent", text: msg.text }];
        });
        if (msg.fallback) player?.speakFallback(msg.text);
        break;
      }
      case "agent_done":
        replyOpenRef.current = false;
        agentDoneRef.current = true;
        maybeReportPlaybackDone();
        break;
      case "interrupt":
        replyOpenRef.current = false;
        agentDoneRef.current = false;
        player?.stop();
        break;
      case "progress":
        setProgress({ covered: msg.covered, total: msg.total });
        break;
      case "end":
        setFinishedByAgent(true);
        break;
    }
  }

  function start() {
    setError(null);
    setTurns([]);
    setPartial("");
    setFinishedByAgent(false);
    setProgress(null);
    setAgentState("thinking");
    agentStateRef.current = "thinking";
    agentDoneRef.current = false;
    replyOpenRef.current = false;
    setPhase("connecting");

    // Create the speaker context inside the click so mobile browsers allow audio.
    const player = new Player();
    player.onIdle = maybeReportPlaybackDone;
    playerRef.current = player;

    const ws = new WebSocket(`${wsBase}/ws?token=${encodeURIComponent(token)}`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    let opened = false;

    ws.onopen = async () => {
      opened = true;
      try {
        await startMic(ws);
        setPhase("live");
      } catch (e) {
        console.error(e);
        setError(
          "We couldn't use your microphone. Allow microphone access in your browser, then start again."
        );
        hangUp();
      }
    };

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        playerRef.current?.enqueue(event.data);
        return;
      }
      try {
        onMessage(JSON.parse(event.data) as ServerMessage);
      } catch {}
    };

    ws.onclose = (event) => {
      if (wsRef.current !== ws) return; // we hung up ourselves
      if (event.code === 1008) {
        setError("This interview isn't available right now. The link may have been closed.");
      } else if (!opened) {
        setError("We couldn't connect to the interviewer. Check your connection and try again.");
      }
      // Let the goodbye finish playing before we release the speakers.
      const player = playerRef.current;
      if (player && !player.idle) {
        player.onIdle = hangUp;
      } else {
        hangUp();
      }
    };
  }

  if (phase === "intro") {
    return (
      <div className="interview-stage">
        <p className="eyebrow">Research interview</p>
        <h1>{title}</h1>
        <p className="lede">
          You&apos;ll have a short voice conversation with an AI interviewer about your experience.
          There are no right answers. Just talk the way you normally would.
        </p>
        <ul className="interview-facts">
          <li>About 10 minutes</li>
          <li>Needs your microphone</li>
          <li>You can interrupt it any time</li>
        </ul>
        {error && <div className="error-box">{error}</div>}
        <button className="btn big" onClick={start}>
          Start interview
        </button>
        <p className="hint mt-6">
          The conversation is transcribed so the research team can learn from it. Your answers are
          only shared with the team that sent you this link.
        </p>
      </div>
    );
  }

  if (phase === "ended") {
    const answered = turns.some((t) => t.role === "user");
    return (
      <div className="interview-stage">
        {error ? (
          <>
            <h1>Something went wrong</h1>
            <div className="error-box">{error}</div>
          </>
        ) : answered ? (
          <>
            <h1>Thank you</h1>
            <p className="lede">
              {finishedByAgent
                ? "That's everything. Your answers have been saved and will help the team build something better."
                : "Your answers so far have been saved. Thanks for taking the time."}
            </p>
          </>
        ) : (
          <>
            <h1>Interview ended</h1>
            <p className="lede">Nothing was recorded. You can start again whenever you&apos;re ready.</p>
          </>
        )}
        {(!answered || error) && (
          <button className="btn big" onClick={start}>
            Start again
          </button>
        )}
        {answered && <p className="hint mt-6">You can close this tab.</p>}
      </div>
    );
  }

  const label =
    phase === "connecting"
      ? "Connecting…"
      : agentState === "speaking"
      ? "Interviewer is speaking. Jump in any time."
      : agentState === "thinking"
      ? "Thinking…"
      : "Listening. Take your time.";

  return (
    <div className="interview-stage live">
      <h1>{title}</h1>
      <div
        ref={orbRef}
        className={`orb ${phase === "connecting" ? "connecting" : agentState}`}
        aria-hidden="true"
      />
      <p className="mic-status" aria-live="polite">
        {label}
      </p>
      {progress && progress.total > 0 && (
        <div className="topic-progress" aria-label={`${progress.covered} of ${progress.total} topics covered`}>
          <div className="topic-bar">
            <span style={{ width: `${(100 * progress.covered) / progress.total}%` }} />
          </div>
          <span>
            {progress.covered === progress.total
              ? "All topics covered, wrapping up"
              : `${progress.covered} of ${progress.total} topics covered`}
          </span>
        </div>
      )}

      {error && <div className="error-box">{error}</div>}

      <button className="btn big secondary" onClick={hangUp}>
        End interview
      </button>

      <section className="convo" aria-label="Conversation">
        <div className="convo-head">
          <span>Conversation</span>
          <span>{turns.length > 0 ? `${turns.length} ${turns.length === 1 ? "line" : "lines"} · scroll ↓` : "live"}</span>
        </div>
        <div className="convo-body" ref={convoRef} onScroll={onConvoScroll} aria-live="polite">
          {turns.length === 0 && !partial && (
            <p className="convo-empty">What you and the interviewer say will show up here.</p>
          )}
          {turns.map((t, i) => (
            <div key={i} className={`turn ${t.role}`}>
              <div className="who">{t.role === "agent" ? "Interviewer" : "You"}</div>
              <div>{t.text}</div>
            </div>
          ))}
          {partial && (
            <div className="turn user partial">
              <div className="who">You</div>
              <div>{partial}</div>
            </div>
          )}
        </div>
        {showJump && (
          <button className="convo-jump" onClick={jumpToLatest}>
            Jump to latest ↓
          </button>
        )}
      </section>
    </div>
  );
}
