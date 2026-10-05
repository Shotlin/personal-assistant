// Natural-voice check: user lines from real calls -> expressive reply (text with [tags]) -> audio files.
// Usage: npm run natural              (generate + print the text)       PLAY=1 npm run natural   (also play)
//        FLAT_AB=1 PLAY=1 npm run natural   (also make a flat version of each reply, to hear the difference)
import "../src/quiet.mts";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { Conversation } from "../src/conversation.mts";
import { TtsStream, temperatureFor } from "../src/sarvamTts.mts";
import { characterName, deliveryFor } from "../src/delivery.mts";

const apiKey = process.env.SARVAM_API_KEY!;
const VOICE = process.env.SANI_VOICE ?? "shubh";
const PACE = Number(process.env.SANI_PACE ?? 0.96);
const wav = (f32: Float32Array) => {
  const pcm = Buffer.alloc(f32.length * 2);
  for (let i = 0; i < f32.length; i++) pcm.writeInt16LE(Math.round(Math.max(-1, Math.min(1, f32[i])) * 32767), i * 2);
  const h = Buffer.alloc(44); h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVEfmt ", 8); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(16000, 24); h.writeUInt32LE(32000, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34);
  h.write("data", 36); h.writeUInt32LE(pcm.length, 40); return Buffer.concat([h, pcm]);
};
const join = (parts: Float32Array[]) => { const o = new Float32Array(parts.reduce((n, p) => n + p.length, 0)); let k = 0; for (const p of parts) { o.set(p, k); k += p.length; } return o; };

const TURNS: [string, string, string][] = [
  ["bn-IN", "calm business talk", "আজকের মিটিংগুলো শিডিউল করো। দশটার সময় একটা মিটিং, দুপুর দুটোয় শুভমের সাথে আর বিকাল ছটায় শান্তনুর সাথে ওয়েবসাইট নিয়ে।"],
  ["bn-IN", "money / worry", "আমার দুটো পেমেন্ট কালেকশনও আছে। শান্তনুর কাছ থেকে কুড়ি হাজার টাকা পাই, প্লাস জিএসটি।"],
  ["bn-IN", "good news", "আরে জানো, আজ আমরা একটা নতুন ক্লায়েন্ট পেয়ে গেলাম!"],
  ["hi-IN", "stressed", "यार, मेरा सर्वर कल रात से डाउन है और मुझे समझ नहीं आ रहा क्या करूँ।"],
  ["hi-IN", "casual chat", "अच्छा ये बताओ, तुम्हें हिंदी में बात करना कैसा लगता है?"],
];

mkdirSync("var/natural", { recursive: true });
let cur: Float32Array[] = [];
const conv = new Conversation({ apiKey, speaker: VOICE, pace: PACE, voice: { pushAudio: (p) => cur.push(p.slice()), clearAudio: () => {} } });
const files: string[] = [];
let i = 0;
for (const [lang, mood, user] of TURNS) {
  i++; cur = [];
  const spoken = await conv.respond(user, lang);
  const tagged = conv.history[conv.history.length - 1].content;
  console.log(`\n[${i}] ${mood}\nYOU    : ${user}\n${characterName(VOICE).toUpperCase().padEnd(7)}: ${tagged}\n         (${spoken.length} chars, first sound ${conv.lastLatencyMs} ms)`);
  const f = `var/natural/${i}-expressive.wav`; writeFileSync(f, wav(join(cur))); files.push(f);
  if (process.env.FLAT_AB && (i === 3 || i === 4)) { // the two emotional turns only (keeps the test cheap) // same words, one fixed neutral delivery
    const t = new TtsStream(apiKey, lang, VOICE, PACE, {}); const parts: Float32Array[] = []; const done = new Promise<void>((r) => t.on("done", () => r()));
    t.on("audio", (p: Float32Array) => parts.push(p));
    await t.style(deliveryFor("neutral", PACE, temperatureFor(lang))); await t.say(spoken); await t.flush(); await Promise.race([done, new Promise((r) => setTimeout(r, 20000))]); t.cancel();
    const g = `var/natural/${i}-flat.wav`; writeFileSync(g, wav(join(parts))); files.push(g); conv.stats.ttsChars += spoken.length;
  }
}
const c = conv.costInr();
console.log(`\nvoice chars sent: ${conv.stats.ttsChars} (Rs ${c.tts.toFixed(2)}) | chat tokens ${conv.stats.llmIn + conv.stats.llmOut} (Rs ${c.llm.toFixed(2)})`);
if (process.env.PLAY) for (const f of files) { console.log(`  playing ${f}`); execFileSync("afplay", [f]); await new Promise((r) => setTimeout(r, 500)); }
process.exit(0);
