// Phones the owner ONCE with the spoken report of the most recent finished job (what a call-back does automatically after `npm run talk`).
// Use it to hear a report again, or when a call-back could not be placed. Usage: npm run callback -- <your-number>
import "../src/quiet.mts";
import { VoipClient } from "baileys-caller";
import { VoiceMemory } from "../src/memory.mts";
import { runLiveCall, type LiveShared } from "../src/liveCall.mts";
import { composeReport, kindOf, reportOpening } from "../src/report.mts";

const number = process.argv[2]; const apiKey = process.env.SARVAM_API_KEY; const VOICE = process.env.SANI_VOICE ?? "shubh";
if (!number || !/^\d{8,15}$/.test(number) || !apiKey) { console.error("Usage: npm run callback -- <digits only> (needs SARVAM_API_KEY in .env)"); process.exit(1); }
const mem = new VoiceMemory();
const job = mem.recentJobs(1)[0];
if (!job || !job.result) { console.log("No finished job to report."); process.exit(0); }
const report = { kind: kindOf(job.status), goal: job.goal, result: job.result };
const client = new VoipClient({ authDir: "./var/auth" });
await client.connect();
const sh: LiveShared = { client, apiKey, number, voice: VOICE, mem, core: null, handoff: null, scheduler: null, inCall: false };
const spoken = await composeReport(apiKey, report, VOICE);
const lines = [reportOpening(VOICE, report.kind), ...spoken.split(/(?=\[[a-z]+\])/).map((s) => s.trim()).filter(Boolean)];
const r = await runLiveCall(sh, { kind: "report", lines });
console.log(r.answered ? `Call finished (${r.seconds} s).` : "Not answered.");
client.disconnect(); await new Promise((res) => setTimeout(res, 1500)); process.exit(0);
