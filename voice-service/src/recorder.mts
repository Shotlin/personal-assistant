// Keeps the call audio (16 kHz mono) on its real timeline so a call can be listened to or replayed later.
// Local files only (var/ is git-ignored).
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

const RATE = 16000;

export class Recorder {
  #chunks: { at: number; pcm: Float32Array }[] = [];
  #t0: number;
  samples = 0;
  constructor(t0 = Date.now()) { this.#t0 = t0; }
  add = (pcm: Float32Array): void => { this.#chunks.push({ at: Date.now() - this.#t0, pcm: pcm.slice() }); this.samples += pcm.length; };

  /** The whole recording laid on its real timeline: gaps (nothing arrived) become silence. */
  render = (): Float32Array => {
    let cursor = 0; const placed: { pos: number; pcm: Float32Array }[] = [];
    for (const c of this.#chunks) { const pos = Math.max(cursor, Math.round((c.at / 1000) * RATE) - c.pcm.length); placed.push({ pos, pcm: c.pcm }); cursor = pos + c.pcm.length; }
    const out = new Float32Array(cursor);
    for (const p of placed) out.set(p.pcm, p.pos);
    return out;
  };

  save = (path: string): void => saveWav(path, this.render());
}

export function saveWav(path: string, ...channels: Float32Array[]): void {
  mkdirSync(dirname(path), { recursive: true });
  const n = channels.length, len = Math.max(0, ...channels.map((c) => c.length));
  const pcm = Buffer.alloc(len * n * 2);
  for (let i = 0; i < len; i++) for (let c = 0; c < n; c++) {
    const v = channels[c][i] ?? 0;
    pcm.writeInt16LE(Math.round(Math.max(-1, Math.min(1, v)) * 32767), (i * n + c) * 2);
  }
  const h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVEfmt ", 8); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(n, 22); h.writeUInt32LE(RATE, 24); h.writeUInt32LE(RATE * 2 * n, 28);
  h.writeUInt16LE(2 * n, 32); h.writeUInt16LE(16, 34); h.write("data", 36); h.writeUInt32LE(pcm.length, 40);
  writeFileSync(path, Buffer.concat([h, pcm]));
}

/** Both sides on one timeline: you.wav, shubh.wav and a mixed mono file that is ready to just play. */
export function saveCall(dir: string, you: Recorder, shubh: Recorder, prefix = "last-call"): void {
  const a = you.render(), b = shubh.render();
  saveWav(`${dir}/${prefix}-you.wav`, a);
  saveWav(`${dir}/${prefix}-shubh.wav`, b);
  const len = Math.max(a.length, b.length), mix = new Float32Array(len);
  for (let i = 0; i < len; i++) mix[i] = Math.max(-1, Math.min(1, (a[i] ?? 0) + (b[i] ?? 0)));
  saveWav(`${dir}/${prefix}.wav`, mix);                  // both voices, mono: just play this one
  saveWav(`${dir}/${prefix}-stereo.wav`, a, b);          // you on the left, Shubh on the right
}
