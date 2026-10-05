// Sarvam real-time speech-to-text over WebSocket (saaras:v4, auto language).
// Docs: https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming
import { EventEmitter } from "node:events";
import WebSocket from "ws";

export type SttOptions = {
  apiKey: string;
  languageCode?: string; // "auto" detects Bengali / Hindi / English / mixed
  model?: "saaras:v4" | "saaras:v3-realtime";
  mode?: "transcribe" | "translate" | "codemix" | "verbatim" | "translit";
  streamType?: "fast" | "balanced" | "simulated";
  silenceMs?: number; // end-of-turn silence
  minSpeechMs?: number; // shorter sounds (coughs, clicks) are not speech
};

export interface SttEvents {
  ready: () => void;
  speechStart: () => void; // use for barge-in (stop talking when the user starts)
  speechEnd: () => void;
  partial: (text: string, language?: string) => void;
  final: (text: string, language?: string) => void;
  error: (err: Error) => void;
  closed: (info: { audioSeconds?: number }) => void;
}

export class SarvamStt extends EventEmitter {
  #ws: WebSocket | null = null;
  #ping: NodeJS.Timeout | null = null;
  #pending: Int16Array[] = [];
  #pendingSamples = 0;
  ready = false;
  audioSeconds: number | undefined;

  constructor(private readonly opts: SttOptions) {
    super();
  }

  connect = (): Promise<void> =>
    new Promise((resolve, reject) => {
      const o = this.opts;
      const q = new URLSearchParams({
        language_code: o.languageCode ?? "auto",
        model: o.model ?? "saaras:v4",
        mode: o.mode ?? "transcribe",
        stream_type: o.streamType ?? "fast",
        encoding: "linear16",
        sample_rate: "16000",
        silence_duration_ms: String(o.silenceMs ?? 500),
        min_speech_duration_ms: String(o.minSpeechMs ?? 300),
      });
      const ws = new WebSocket(`wss://api.sarvam.ai/speech-to-text-realtime/ws?${q}`, {
        headers: { "Api-Subscription-Key": o.apiKey },
      });
      this.#ws = ws;
      ws.on("unexpected-response", (_req, res) => {
        reject(new Error(`Sarvam refused the connection (HTTP ${res.statusCode}). Check the API key.`));
      });
      ws.on("error", (e) => (this.ready ? this.emit("error", e) : reject(e)));
      ws.on("close", (code, reason) => {
        if (this.#ping) clearInterval(this.#ping);
        if (!this.ready) reject(new Error(`Sarvam closed the connection (${code} ${reason})`));
        this.emit("closed", { audioSeconds: this.audioSeconds });
      });
      ws.on("message", (raw) => {
        let m: any;
        try { m = JSON.parse(raw.toString()); } catch { return; }
        switch (m.event) {
          case "session.begin":
            this.ready = true;
            this.#ping = setInterval(() => this.#send({ event: "ping" }), 15_000);
            this.emit("ready");
            resolve();
            break;
          case "vad.speech_start": this.emit("speechStart", m.utterance_idx); break;
          case "vad.speech_end": this.emit("speechEnd", m.utterance_idx); break;
          case "transcript.partial": this.emit("partial", m.text, m.language, m.utterance_idx); break;
          case "transcript.final": this.emit("final", m.text, m.language, m.utterance_idx); break;
          case "session.end": this.audioSeconds = Number(m.audio_duration_s); break;
          case "error": {
            const err = new Error(`Sarvam STT error ${m.code}: ${m.message}`);
            if (m.is_fatal && !this.ready) reject(err); else this.emit("error", err);
            break;
          }
        }
      });
    });

  /** Feed 16 kHz mono float PCM (what the WhatsApp call gives us). Batched to ~100 ms. */
  push = (pcm: Float32Array): void => {
    if (!this.ready) return;
    const i16 = new Int16Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) {
      const s = Math.max(-1, Math.min(1, pcm[i]));
      i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.#pending.push(i16);
    this.#pendingSamples += i16.length;
    if (this.#pendingSamples >= 1600) this.#flush();
  };

  #flush = (): void => {
    if (!this.#pendingSamples) return;
    const all = new Int16Array(this.#pendingSamples);
    let off = 0;
    for (const c of this.#pending) { all.set(c, off); off += c.length; }
    this.#pending = [];
    this.#pendingSamples = 0;
    this.#send({ event: "audio_input", audio: Buffer.from(all.buffer).toString("base64") });
  };

  #send = (msg: object): void => {
    if (this.#ws?.readyState === WebSocket.OPEN) this.#ws.send(JSON.stringify(msg));
  };

  close = (): void => {
    this.#flush();
    this.#send({ event: "end" });
    setTimeout(() => this.#ws?.close(), 1500);
  };
}
