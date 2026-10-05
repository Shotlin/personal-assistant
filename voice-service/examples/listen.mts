// Step 2: call the owner, say a short greeting, then print what Sarvam hears.
// Usage: npm run listen -- <your-number-digits-only>
// Needs SARVAM_API_KEY in voice-service/.env (never paste it into chat or git).
import "../src/quiet.mts";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { VoipClient } from "baileys-caller";
import { SarvamStt } from "../src/sarvamStt.mts";

const number = process.argv[2];
const apiKey = process.env.SARVAM_API_KEY;
if (!number || !/^\d{8,15}$/.test(number)) { console.error("Usage: npm run listen -- <digits only>"); process.exit(1); }
if (!apiKey) { console.error("SARVAM_API_KEY is missing. Put it in voice-service/.env"); process.exit(1); }

mkdirSync("var", { recursive: true });
execFileSync("say", ["-o", "var/greet.aiff",
  "Hello Sayan, this is Sani. Please speak in Bengali, Hindi, or English. I will show on screen what I hear."]);
// 6 s of silence first so the greeting starts about when you pick up.
const greeting = "lavfi:amovie=var/greet.aiff,aresample=16000,adelay=6000,asetpts=N/SR/TB";

const stt = new SarvamStt({ apiKey, languageCode: "auto", model: "saaras:v4", streamType: "fast" });
stt.on("speechStart", () => console.log("  [you started speaking]"));
stt.on("partial", (t, lang) => process.stdout.write(`\r  ... ${t} ${lang ? `(${lang})` : ""}        `));
stt.on("final", (t, lang) => console.log(`\nHEARD${lang ? ` [${lang}]` : ""}: ${t}`));
stt.on("error", (e) => console.error("stt error:", e.message));
await stt.connect();
console.log("Sarvam listener ready");

const client = new VoipClient({ authDir: "./var/auth" });
await client.connect();
const call = await client.call(number, { audioSource: greeting, durationMs: 60_000 });
call.on("ringing", () => console.log("ringing... pick up"));
let connected = false;
call.on("connected", () => { connected = true; console.log("connected. Talk now."); });
call.on("audio", (pcm) => { if (connected) stt.push(pcm); });
call.on("ended", (r) => console.log(`\ncall ended: ${r}`));
const hardStop = setTimeout(() => { call.end(); client.disconnect(); process.exit(0); }, 63_000);
await call.waitForEnd();
clearTimeout(hardStop);
stt.close();
await new Promise((r) => setTimeout(r, 2500));
if (stt.audioSeconds !== undefined) console.log(`audio billed by Sarvam: ${stt.audioSeconds}s`);
client.disconnect();
process.exit(0);
