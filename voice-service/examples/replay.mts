// Replay a recorded call (your side) into Sarvam STT at real-time pace and print a timeline.
// Usage: npm run replay [-- var/last-call-in.wav] [silence_ms]
import "../src/quiet.mts";
import { readFileSync } from "node:fs";
import { SarvamStt } from "../src/sarvamStt.mts";
import { SteadyFeed } from "../src/sttFeed.mts";

const apiKey = process.env.SARVAM_API_KEY!;
const file = process.argv[2] ?? "var/last-call-in.wav";
const silenceMs = Number(process.argv[3] ?? 700);
const wav = readFileSync(file);
const i16 = new Int16Array(wav.buffer.slice(wav.byteOffset + 44, wav.byteOffset + wav.length - ((wav.length - 44) % 2)));
const f32 = Float32Array.from(i16, (v) => v / 32768);
const t0 = performance.now();
const at = () => `${((performance.now() - t0) / 1000).toFixed(2)}s`;
const stt = new SarvamStt({ apiKey, languageCode: "auto", model: "saaras:v4", streamType: "fast", silenceMs });
stt.on("speechStart", (i) => console.log(at(), `speech start #${i}`));
stt.on("speechEnd", (i) => console.log(at(), `speech END   #${i}`));
stt.on("partial", (t, _l, i) => console.log(at(), `  partial #${i}: ${t}`));
stt.on("final", (t, l, i) => console.log(at(), `FINAL [${l}] #${i}: ${t}`));
await stt.connect();
const feed = new SteadyFeed((p) => stt.push(p));
feed.start();
const END_MS = Date.now();
for (let o = 0; o < f32.length; o += 320) {          // real frames arrive at call pace, then NOTHING (like silence on WhatsApp)
  feed.add(f32.subarray(o, o + 320));
  await new Promise((r) => setTimeout(r, 20));
}
console.log(at(), "(speech sent; now no audio arrives at all, as with WhatsApp silence)");
console.log(at(), "audio finished; waiting for the last results");
await new Promise((r) => setTimeout(r, 4000));
feed.stop();
console.log(`real frames ${feed.realFrames}, filled-with-silence frames ${feed.filledFrames}`);
stt.close();
setTimeout(() => process.exit(0), 2000);
