// Tune one voice: same short Bengali + Hindi lines at different Bulbul 'temperature' (steady <-> expressive).
// Usage: npm run tune [-- pooja]      Clips are cached in var/voices, so replaying is free.
import "../src/quiet.mts";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { TtsStream } from "../src/sarvamTts.mts";
import { MALE_VOICES } from "../src/conversation.mts";

const apiKey = process.env.SARVAM_API_KEY!;
const voice = process.argv[2] ?? "pooja";
const TEMPS = (process.env.TEMPS ?? "0.3,0.6,0.9").split(",").map(Number);
const LINES: [string, string][] = [
  ["bn-IN", "ঠিক আছে সায়ন, আমি বুঝে গেছি। কাজটা এখনই পাঠিয়ে দিচ্ছি।"],
  ["en-IN", "Okay Sayan, got it. I'll pass this on right after the call."],
  ["hi-IN", MALE_VOICES.has(voice) ? "ठीक है सायन, मैं समझ गया। काम अभी भेज देता हूँ।" : "ठीक है सायन, मैं समझ गई। काम अभी भेज देती हूँ।"],
];
let chars = 0;
function wav(f32: Float32Array): Buffer {
  const pcm = Buffer.alloc(f32.length * 2);
  for (let i = 0; i < f32.length; i++) pcm.writeInt16LE(Math.round(Math.max(-1, Math.min(1, f32[i])) * 32767), i * 2);
  const h = Buffer.alloc(44); h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVEfmt ", 8); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(16000, 24); h.writeUInt32LE(32000, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34);
  h.write("data", 36); h.writeUInt32LE(pcm.length, 40); return Buffer.concat([h, pcm]);
}
async function synth(lang: string, text: string, temperature: number): Promise<Float32Array> {
  const t = new TtsStream(apiKey, lang, voice, 1.0, { temperature });
  const parts: Float32Array[] = []; const done = new Promise<void>((r) => t.on("done", () => r()));
  t.on("audio", (p: Float32Array) => parts.push(p));
  await t.say(text); await t.flush(); await Promise.race([done, new Promise((r) => setTimeout(r, 20000))]); t.cancel(); chars += text.length;
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0)); let o = 0; for (const p of parts) { out.set(p, o); o += p.length; } return out;
}
mkdirSync("var/voices", { recursive: true });
const path = (l: string, t: number) => `var/voices/tune-${voice}-${l}-t${t}.wav`;
await Promise.all(TEMPS.flatMap((t) => LINES.map(async ([l, text]) => { if (!existsSync(path(l, t))) writeFileSync(path(l, t), wav(await synth(l, text, t))); })));
if (chars) console.log(`(synthesis this run: ${chars} chars = Rs ${(chars * 0.003).toFixed(2)})`);
if (process.env.NO_PLAY) { console.log("generated"); process.exit(0); }
for (const t of TEMPS) {
  for (const [l] of LINES) { console.log(`  ${voice.toUpperCase()}  temperature ${t}  ${l}`); execFileSync("afplay", [path(l, t)]); }
  await new Promise((r) => setTimeout(r, 700));
}
console.log("\nTell me which temperature sounded best.");
process.exit(0);
