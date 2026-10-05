// Step 1: make the owner's phone ring over WhatsApp and play a short tone.
// Usage: npm run ring -- <your-number-digits-only>   e.g. 919876543210
// First run prints a QR: scan it with the SPARE WhatsApp (Linked Devices).
import "../src/quiet.mts";
import { writeFileSync, existsSync, mkdirSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { VoipClient } from "baileys-caller";

const number = process.argv[2];
if (!number || !/^\d{8,15}$/.test(number)) {
  console.error("Usage: npm run ring -- <digits only, with country code>");
  process.exit(1);
}

// 2-second 440 Hz beep-beep, 16 kHz mono WAV, so no audio file is needed.
function makeTone(path: string) {
  const rate = 16000, secs = 2, n = rate * secs;
  const pcm = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) {
    const on = Math.floor((i / rate) * 4) % 2 === 0;
    pcm.writeInt16LE(on ? Math.round(Math.sin((2 * Math.PI * 440 * i) / rate) * 9000) : 0, i * 2);
  }
  const h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVEfmt ", 8);
  h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22);
  h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate * 2, 28); h.writeUInt16LE(2, 32);
  h.writeUInt16LE(16, 34); h.write("data", 36); h.writeUInt32LE(pcm.length, 40);
  writeFileSync(path, Buffer.concat([h, pcm]));
}

mkdirSync("var", { recursive: true });
const tone = "var/tone.wav";
if (!existsSync(tone)) makeTone(tone);

const client = new VoipClient({ authDir: "./var/auth" });
await client.connect();

// SANI_AUDIO: speech (default, macOS voice) | sine (loud steady tone) | beep (2s file) | silence
const mode = process.env.SANI_AUDIO ?? "speech";
if (mode === "speech") {
  const text = "Hello Sayan. This is Sani. Can you hear me clearly? Please say something, and I will listen. ...";
  execFileSync("say", ["-o", "var/hello.aiff", text]);
}
const source =
  mode === "silence" ? "silence"
  : mode === "sine" ? "lavfi:sine=frequency=440:sample_rate=16000,volume=3"
  : mode === "beep" ? tone
  : "lavfi:amovie=var/hello.aiff:loop=0,aresample=16000,asetpts=N/SR/TB"; // loops until hang-up
console.log("audio source:", source);
const call = await client.call(number, { audioSource: source, durationMs: 30_000 });
call.on("ringing", () => console.log("ringing... pick up your phone"));
call.on("connected", () => console.log("connected, you should hear a beep"));
let frames = 0;
call.on("audio", () => { frames++; });
call.on("ended", (r) => console.log(`ended: ${r}; audio frames heard from you: ${frames}`));
call.on("error", (e) => console.error("call error:", e));
const hardStop = setTimeout(() => { console.log("forcing hang up"); call.end(); client.disconnect(); process.exit(0); }, 33_000);
await call.waitForEnd();
clearTimeout(hardStop);
client.disconnect();
process.exit(0);
