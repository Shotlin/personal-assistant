// Offline check (no phone, no WhatsApp): text in -> Sarvam chat -> Bulbul -> audio stats + a WAV file.
// Usage: npm run offline -- "Hello, I want you to fix the refund bug in my grocery app"
import "../src/quiet.mts";
import { writeFileSync, mkdirSync } from "node:fs";
import { Conversation } from "../src/conversation.mts";

const apiKey = process.env.SARVAM_API_KEY;
if (!apiKey) { console.error("SARVAM_API_KEY missing in .env"); process.exit(1); }
const text = process.argv[2] ?? "Hello Sani, I want you to fix the refund bug in my grocery app.";
const lang = process.argv[3]; // optional: bn-IN / hi-IN / en-IN

const chunks: Float32Array[] = [];
const conv = new Conversation({ apiKey, voice: { pushAudio: (p) => chunks.push(p), clearAudio: () => {} }, speaker: process.env.SANI_VOICE ?? "shubh" });
console.log("USER :", text);
const reply = await conv.respond(text, lang);
console.log("SANI :", reply);
const total = chunks.reduce((n, c) => n + c.length, 0);
const all = new Float32Array(total); let o = 0; for (const c of chunks) { all.set(c, o); o += c.length; }
let peak = 0, nan = 0; for (const v of all) { if (Number.isNaN(v)) nan++; else peak = Math.max(peak, Math.abs(v)); }
console.log(JSON.stringify({ firstAudioMs: conv.lastLatencyMs, seconds: +(total / 16000).toFixed(2), peak: +peak.toFixed(3), nan }));
mkdirSync("var", { recursive: true });
const pcm = Buffer.alloc(total * 2); for (let i = 0; i < total; i++) pcm.writeInt16LE(Math.round(all[i] * 32767), i * 2);
const h = Buffer.alloc(44); h.write("RIFF", 0); h.writeUInt32LE(36 + pcm.length, 4); h.write("WAVEfmt ", 8); h.writeUInt32LE(16, 16);
h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(16000, 24); h.writeUInt32LE(32000, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write("data", 36); h.writeUInt32LE(pcm.length, 40);
writeFileSync("var/offline-reply.wav", Buffer.concat([h, pcm]));
console.log("saved var/offline-reply.wav");
process.exit(0);
