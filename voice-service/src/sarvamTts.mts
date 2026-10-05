// Sarvam streaming text-to-speech (bulbul:v3) -> 16 kHz mono float PCM for the call.
// One short-lived socket per reply, so barge-in = cancel() and the next reply starts clean.
import { EventEmitter } from "node:events";
import WebSocket from "ws";
import { Resample24to16, lift } from "./audioDsp.mts";
import type { Delivery } from "./delivery.mts";

export type TtsTuning = { temperature?: number; gain?: number };
/** Chosen by ear (pooja): Bengali is best at 0.9, Hindi at 1.0; other languages sit in between. */
export const TEMPERATURE_BY_LANG: Record<string, number> = { "bn-IN": 0.9, "hi-IN": 1.0 };
export const DEFAULT_TEMPERATURE = 0.95;
export const temperatureFor = (language: string, tuning: TtsTuning = {}): number =>
  tuning.temperature ?? TEMPERATURE_BY_LANG[language] ?? DEFAULT_TEMPERATURE;

const TTS_LANGS = new Set(["bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"]);
/** Map what the listener detected to a language Bulbul can speak. */
export function ttsLang(detected?: string): string {
  if (detected === "or-IN") return "od-IN";
  return detected && TTS_LANGS.has(detected) ? detected : "en-IN";
}

export class TtsStream extends EventEmitter {
  #ws: WebSocket;
  #opened: Promise<void>;
  #cancelled = false;
  #carry: Buffer = Buffer.alloc(0);
  bytes = 0;
  charsSent = 0; // what Bulbul will bill (per character)
  #flushes = 0;
  #finals = 0;
  closed = false;
  /** Bulbul answers every flush with its own 'final' event; the reply is complete only when all have arrived. */
  allDone = (): boolean => this.#finals >= this.#flushes;

  #rs = new Resample24to16();
  #gain: number;
  #language = ""; #speaker = ""; #pace = 1; #temperature = 0.9;
  #styleKey = "";

  constructor(apiKey: string, language: string, speaker: string, pace: number, tuning: TtsTuning = {}) {
    super();
    this.#gain = tuning.gain ?? 1.15;
    this.#ws = new WebSocket("wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3&send_completion_event=true", {
      headers: { "Api-Subscription-Key": apiKey },
    });
    this.#language = language; this.#speaker = speaker; this.#pace = pace; this.#temperature = temperatureFor(language, tuning);
    this.#opened = new Promise((res, rej) => {
      this.#ws.once("open", () => {
        this.#sendConfig();
        res();
      });
      this.#ws.once("unexpected-response", (_r, resp) => rej(new Error(`Sarvam TTS refused (HTTP ${resp.statusCode})`)));
      this.#ws.once("error", rej);
    });
    this.#opened.catch(() => {}); // surfaced through say()/error event
    this.#ws.on("message", (raw) => {
      if (this.#cancelled) return;
      let m: any; try { m = JSON.parse(raw.toString()); } catch { return; }
      if (m.type === "audio") this.#onAudio(m.data);
      else if (m.type === "event" && m.data?.event_type === "final") { this.#finals++; if (this.#finals >= this.#flushes) this.emit("done"); }
      else if (m.type === "error") this.emit("error", new Error(`TTS error: ${m.data?.message}`));
    });
    this.#ws.on("close", () => { this.closed = true; if (!this.#cancelled) this.emit("done"); });
  }

  #sendConfig(): void {
    this.#ws.send(JSON.stringify({
      type: "config",
      data: {
        language_code: this.#language, speaker: this.#speaker, pace: this.#pace, temperature: this.#temperature,
        speech_sample_rate: "24000", output_audio_codec: "linear16", // native rate; we downsample properly ourselves
        min_buffer_size: 60, max_chunk_length: 250,
      },
    }));
  }

  /** Change how the NEXT text is delivered (pace / expressiveness). Bulbul applies config updates between flushes. */
  style = async (d: Delivery): Promise<void> => {
    await this.#opened;
    if (this.#cancelled) return;
    const key = `${d.pace}|${d.temperature}`;
    if (key === this.#styleKey || (this.#styleKey === "" && d.pace === this.#pace && d.temperature === this.#temperature)) { this.#styleKey = key; return; }
    this.#styleKey = key; this.#pace = d.pace; this.#temperature = d.temperature;
    this.#sendConfig();
  };

  #onAudio(d: { audio: string; content_type?: string }): void {
    let buf = Buffer.from(d.audio, "base64");
    if (buf.subarray(0, 4).toString() === "RIFF") buf = buf.subarray(44); // strip WAV header if present
    if (this.#carry.length) buf = Buffer.concat([this.#carry, buf]);
    const even = buf.length - (buf.length % 2);
    this.#carry = buf.subarray(even);
    if (!even) return;
    const i16 = new Int16Array(buf.buffer.slice(buf.byteOffset, buf.byteOffset + even));
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
    this.bytes += even;
    this.emit("raw24", f32);
    this.emit("audio", lift(this.#rs.push(f32), this.#gain));
  }

  say = async (text: string): Promise<void> => {
    await this.#opened;
    if (this.#cancelled) return;
    this.charsSent += text.length;
    this.#ws.send(JSON.stringify({ type: "text", data: { text } }));
  };
  flush = async (): Promise<void> => {
    await this.#opened;
    if (this.#cancelled) return;
    this.#flushes++;
    this.#ws.send(JSON.stringify({ type: "flush" }));
  };
  cancel = (): void => {
    if (this.#cancelled) return;
    this.#cancelled = true;
    try { this.#ws.close(); } catch {}
    this.emit("done"); // never leave a waiter hanging after an interruption
  };
}
