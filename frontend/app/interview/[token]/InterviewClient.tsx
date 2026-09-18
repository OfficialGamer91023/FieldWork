"use client";

import { useEffect, useRef, useState } from "react";

type Status = "idle" | "connecting" | "live" | "ended";

export default function InterviewClient({
  token,
  wsBase,
  title,
}: {
  token: string;
  wsBase: string;
  title: string;
}) {
  const [status, setStatus] = useState<Status>("idle");
  const [turns, setTurns] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const micRef = useRef<MediaStream | null>(null);

  function teardownAudio() {
    try {
      micRef.current?.getTracks().forEach((t) => t.stop());
    } catch {}
    micRef.current = null;
    try {
      if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
        audioCtxRef.current.close();
      }
    } catch {}
    audioCtxRef.current = null;
  }

  function stop() {
    teardownAudio();
    try {
      wsRef.current?.close();
    } catch {}
    wsRef.current = null;
    try {
      window.speechSynthesis.cancel();
    } catch {}
    setStatus("ended");
  }

  // Ensure we release the mic + socket if the participant navigates away.
  useEffect(() => () => stop(), []);

  async function startAudio(ws: WebSocket) {
    const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    micRef.current = mic;

    const ctx = new AudioContext({ sampleRate: 16000 });
    audioCtxRef.current = ctx;

    const source = ctx.createMediaStreamSource(mic);
    await ctx.audioWorklet.addModule("/processor.js");
    const node = new AudioWorkletNode(ctx, "pcm-processor");

    node.port.onmessage = (event: MessageEvent) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(event.data); // ArrayBuffer of Int16 PCM
      }
    };

    source.connect(node);
    node.connect(ctx.destination);
  }

  function start() {
    setError(null);
    setTurns([]);
    setStatus("connecting");

    const ws = new WebSocket(`${wsBase}/ws?token=${encodeURIComponent(token)}`);
    wsRef.current = ws;

    ws.onopen = async () => {
      setStatus("live");
      try {
        await startAudio(ws);
      } catch (e) {
        console.error(e);
        setError("Microphone access is required to run the interview.");
        stop();
      }
    };

    ws.onmessage = (event) => {
      if (typeof event.data !== "string" || !event.data) return;
      const text = event.data;
      setTurns((prev) => [...prev, text]);
      try {
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
      } catch {}
    };

    ws.onerror = () => {
      setError("Connection error. Please try again.");
    };

    ws.onclose = (event) => {
      teardownAudio();
      // 1008 = policy violation: the study isn't live / bad token.
      if (event.code === 1008) {
        setError("This interview isn't available right now.");
      }
      setStatus("ended");
    };
  }

  const dotClass =
    status === "live" ? "dot live" : status === "connecting" ? "dot connecting" : "dot";
  const statusLabel =
    status === "idle"
      ? "Ready when you are"
      : status === "connecting"
      ? "Connecting…"
      : status === "live"
      ? "Listening — speak naturally"
      : "Interview ended";

  return (
    <div className="interview-stage">
      <h1>{title}</h1>
      <p className="mic-status">
        <span className={dotClass} />
        {statusLabel}
      </p>

      {error && <div className="error-box">{error}</div>}

      {status === "idle" || status === "ended" ? (
        <button className="btn big" onClick={start}>
          {status === "ended" ? "Start again" : "Start interview"}
        </button>
      ) : (
        <button className="btn big secondary" onClick={stop}>
          End interview
        </button>
      )}

      {turns.length > 0 && (
        <div className="transcript">
          {turns.map((t, i) => (
            <div key={i} className="turn">
              <div className="who">Interviewer</div>
              <div>{t}</div>
            </div>
          ))}
        </div>
      )}

      <p className="hint mt-6">
        This conversation is recorded and transcribed for research. Use headphones to avoid echo.
      </p>
    </div>
  );
}
