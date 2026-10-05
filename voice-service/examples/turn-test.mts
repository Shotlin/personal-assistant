// Offline test of turn-taking (no phone): synthetic speech with human-sized pauses, replayed in real time
// through Sarvam's listener + the turn manager. Prints when Sani WOULD answer / be interrupted.
import "../src/quiet.mts";
import { execFileSync } from "node:child_process";
import { readFileSync, mkdirSync } from "node:fs";
import { SarvamStt } from "../src/sarvamStt.mts";
import { SteadyFeed } from "../src/sttFeed.mts";
import { TurnManager } from "../src/turns.mts";

const apiKey = process.env.SARVAM_API_KEY!;
mkdirSync("var/tt", { recursive: true });
function clip(name: string, text: string): Float32Array {
  execFileSync("say", ["-v", "Lekha", "-o", `var/tt/${name}.aiff`, text]);
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", "-i", `var/tt/${name}.aiff`, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", `var/tt/${name}.wav`]);
  const b = readFileSync(`var/tt/${name}.wav`);
  return Float32Array.from(new Int16Array(b.buffer.slice(b.byteOffset + 44, b.byteOffset + b.length - ((b.length - 44) % 2))), (v) => v / 32768);
}
const sil = (s: number) => new Float32Array(Math.round(s * 16000));
const cat = (...p: Float32Array[]) => { const o = new Float32Array(p.reduce((n, x) => n + x.length, 0)); let k = 0; for (const x of p) { o.set(x, k); k += x.length; } return o; };

async function run(title: string, audio: Float32Array, opts: { speakingUntilMs?: number }) {
  console.log(`\n=== ${title} ===`);
  const t0 = Date.now(); const at = () => `${((Date.now() - t0) / 1000).toFixed(2)}s`;
  const stt = new SarvamStt({ apiKey, languageCode: "auto", model: "saaras:v4", streamType: "fast", silenceMs: 500, minSpeechMs: 300 });
  const speaking = () => (opts.speakingUntilMs ?? 0) > Date.now() - t0;
  const turns = new TurnManager({
    isSpeaking: speaking, speakingSince: () => t0,
    log: (m) => console.log(at(), "   turn:", m),
    onInterrupt: (why) => console.log(at(), `>>> SANI STOPS (${why})`),
    onTurn: (text) => console.log(at(), `>>> SANI ANSWERS the merged turn: "${text}"`),
  });
  stt.on("speechStart", () => turns.speechStart()); stt.on("speechEnd", () => turns.speechEnd());
  stt.on("partial", (t) => turns.partial(t));
  stt.on("final", (t, l) => { console.log(at(), `   heard: ${t}`); turns.final(t, l); });
  await stt.connect();
  const feed = new SteadyFeed((p) => stt.push(p)); feed.start();
  for (let o = 0; o < audio.length; o += 320) { feed.add(audio.subarray(o, o + 320)); await new Promise((r) => setTimeout(r, 20)); }
  console.log(at(), "(audio finished)");
  await new Promise((r) => setTimeout(r, 4500));
  feed.stop(); turns.stop(); stt.close(); await new Promise((r) => setTimeout(r, 800));
}

// A: the user speaks in fragments with ~1 s pauses (exactly what the real call showed)
const a = cat(sil(0.5), clip("a1", "आज का जो काम है ना।"), sil(0.9), clip("a2", "मेरा जो ग्रोसरी एप्लीकेशन है उनका।"), sil(1.0), clip("a3", "आजकल रिफंड पॉलिसी में बहुत दिक्कत आ रहा है।"), sil(0.5));
await run("A: fragmented speech with pauses -> expect ONE answer, after the last fragment", a, {});
// B: Sani is speaking for 12 s; user first says "haan" (must be ignored), later says a real stop command
const b = cat(sil(1.0), clip("b1", "हाँ।"), sil(3.5), clip("b2", "नहीं रुको, मेरी बात सुनो।"), sil(1.0));
await run("B: while Sani talks -> ignore 'haan', stop for 'ruko, meri baat suno'", b, { speakingUntilMs: 12000 });
process.exit(0);
