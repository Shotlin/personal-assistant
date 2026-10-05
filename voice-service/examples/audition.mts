// Voice audition: the same Bengali + Hindi lines in several Bulbul v3 voices, played one after another.
// Usage: npm run audition                (generate once, then play all)
//        npm run audition -- priya,kavya (only these)       npm run audition -- --temp 0.4 ritu
import "../src/quiet.mts";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { TtsStream } from "../src/sarvamTts.mts";

const apiKey = process.env.SARVAM_API_KEY!;
let totalChars = 0;
const args = process.argv.slice(2);
const ti = args.indexOf("--temp");
const temperature = ti >= 0 ? Number(args.splice(ti, 2)[1]) : 0.6;
const FEMALE = ["ritu", "priya", "neha", "pooja", "simran", "kavya", "ishita", "roopa"];
const MALE = ["shubh", "aditya", "rahul", "rohan", "amit", "kabir", "advait", "anand"];
const names = args[0] ? args[0].split(",") : [...FEMALE.slice(0, 6), ...MALE.slice(0, 6)];

const LINES: [string, string][] = [
  ["bn-IN", "হ্যালো সায়ন, আমি সানি। তোমার গ্রোসারি অ্যাপের রিফান্ড ফ্লো আমি বুঝে নিয়েছি। কাজটা শুরু করে দিই, ঠিক আছে তো?"],
  ["hi-IN", "ठीक है, मैंने सब समझ लिया है। टेस्ट भी चलाऊँगा, और UI में कोई बदलाव नहीं करूँगा।"],
];

function wav(f32: Float32Array): Buffer {
  const pcm = Buffer.alloc(f32.length * 2);
  for (let i = 0; i < f32.length; i++) pcm.writeInt16LE(Math.round(Math.max(-1, Math.min(1, f32[i])) * 32767), i * 2);
  const h = Buffer.alloc(44); h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVEfmt ", 8); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(16000, 24); h.writeUInt32LE(32000, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34);
  h.write("data", 36); h.writeUInt32LE(pcm.length, 40); return Buffer.concat([h, pcm]);
}
async function synth(speaker: string, lang: string, text: string): Promise<Float32Array> {
  const t = new TtsStream(apiKey, lang, speaker, 1.0, { temperature });
  const parts: Float32Array[] = [];
  const done = new Promise<void>((r) => t.on("done", () => r()));
  t.on("audio", (p: Float32Array) => parts.push(p));
  await t.say(text); await t.flush(); await Promise.race([done, new Promise((r) => setTimeout(r, 20000))]); t.cancel(); totalChars += text.length;
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0)); let o = 0; for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

mkdirSync("var/voices", { recursive: true });
const tag = temperature === 0.6 ? "" : `-t${temperature}`;
const todo = names.filter((n) => LINES.some(([l]) => !existsSync(`var/voices/${n}-${l}${tag}.wav`)));
for (let i = 0; i < todo.length; i += 3) {                                   // 3 at a time
  await Promise.all(todo.slice(i, i + 3).flatMap((n) => LINES.map(async ([l, text]) => {
    const pcm = await synth(n, l, text);
    writeFileSync(`var/voices/${n}-${l}${tag}.wav`, wav(pcm));
  })));
}
if (totalChars) console.log(`(synthesis this run: ${totalChars} chars = Rs ${(totalChars * 0.003).toFixed(2)}; cached voices cost nothing)`);
if (process.env.NO_PLAY) { console.log(`generated ${names.length} voices in var/voices`); process.exit(0); }
console.log(`\nPlaying ${names.length} voices (Bengali, then Hindi). Note your favourites.\n`);
for (const n of names) {
  for (const [l] of LINES) {
    console.log(`  ${n.toUpperCase().padEnd(8)} ${l}`);
    execFileSync("afplay", [`var/voices/${n}-${l}${tag}.wav`]);
  }
  await new Promise((r) => setTimeout(r, 700));
}
console.log("\nDone. Tell me the names you liked.");
process.exit(0);
