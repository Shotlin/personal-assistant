// Shubh phones you, talks with you, and then keeps his promises. Usage: npm run talk -- <your-number-digits-only>
//   1. Shubh calls you. You talk (Hindi / Bengali / English). He keeps notes, remembers earlier calls, answers, and, after your yes,
//      hands tasks to the Deep Agent (which builds things with ZCode / Claude Code).
//   2. After you hang up this window stays open. When the Deep Agent finishes or gets stuck, Shubh sends you a WhatsApp text copy and CALLS
//      you with a short spoken report. If you said "call me back in 2 minutes", he calls you then.
//   Ctrl-C stops everything. Only YOUR number is ever called or texted. Use the spare WhatsApp number (ban risk).
import "../src/quiet.mts";
import { VoipClient } from "baileys-caller";
import { appendFileSync } from "node:fs";
import { VoiceMemory } from "../src/memory.mts";
import { SaniCore } from "../src/core.mts";
import { Handoff } from "../src/handoff.mts";
import { SaniHistory } from "../src/saniHistory.mts";
import { Scheduler } from "../src/schedule.mts";
import { runLiveCall, type LiveShared } from "../src/liveCall.mts";
import { supervise } from "../src/supervisor.mts";
import { makePlaceCall } from "../src/placeCall.mts";
import { voiceCallsMarkdown } from "../src/context.mts";
import { mirrorToSani } from "../src/sani.mts";

const number = process.argv[2];
const apiKey = process.env.SARVAM_API_KEY;
if (!number || !/^\d{8,15}$/.test(number)) { console.error("Usage: npm run talk -- <digits only>"); process.exit(1); }
if (!apiKey) { console.error("SARVAM_API_KEY is missing. Put it in voice-service/.env"); process.exit(1); }
const VOICE = process.env.SANI_VOICE ?? "shubh";
const MAX_WAIT_MS = Number(process.env.VOICE_MAX_WAIT_MIN ?? 45) * 60_000; // how long this window stays open after the call for the agent / call-backs

let mem: VoiceMemory | null = null;
if (process.env.SANI_MEMORY !== "0") { try { mem = new VoiceMemory(); } catch (e: any) { console.log(`memory: unavailable (${e.message}); continuing without it`); } }
let core: SaniCore | null = null;
if (process.env.VOICE_AGENT !== "0") {
  try { core = await SaniCore.start({ coding: process.env.VOICE_CODING !== "0" }); console.log(`agent: Deep Agent ready (model key: ${core.keySource})`); }
  catch (e: any) { console.log(`agent: not available (${e.message.slice(0, 160)}); the call works without it`); }
}
const client = new VoipClient({ authDir: "./var/auth" });
await client.connect();
const sh: LiveShared = {
  client, apiKey, number, voice: VOICE, mem, core, inCall: false,
  handoff: core ? new Handoff(core, mem, "pending", process.env.VOICE_SESSIONS === "0" ? null : new SaniHistory()) : null,
  scheduler: mem && process.env.VOICE_CALLBACKS !== "0" ? new Scheduler(mem.db) : null,
  // the WhatsApp text copy goes ONLY to the number we call (the owner's own); VOICE_TEXT_COPY=0 turns it off
  notify: process.env.VOICE_TEXT_COPY === "0" ? undefined : async (text) => { await client.sendText(number, text); console.log(`text copy sent to WhatsApp (${text.split("\n")[0]})`); },
};

let stopping = false;
process.on("SIGINT", () => {
  if (stopping) process.exit(1);
  stopping = true; console.log("\nStopping (Ctrl-C again to force)...");
  sh.abort?.(); sh.handoff?.cancelAll();
});

// ---- 1. the first call
const first = await runLiveCall(sh, { kind: "conversation" });
if (!first.answered) console.log("The call was not answered.");

// ---- 2. keep promises: report finished work, call back when asked
const waiting = sh.handoff?.running.length || sh.handoff?.preparing || sh.scheduler?.pending().length;
if (!stopping && waiting) {
  console.log(`\nStaying open for up to ${Math.round(MAX_WAIT_MS / 60000)} minutes: ${sh.handoff?.running.length ? "the Deep Agent is working; " : ""}${sh.scheduler?.pending().length ? "a call-back is scheduled; " : ""}I will call you. (Ctrl-C stops.)`);
}
if (!stopping) {
  const result = await supervise({
    handoff: sh.handoff, scheduler: sh.scheduler, stopped: () => stopping, maxMs: MAX_WAIT_MS,
    notify: async (t) => { if (sh.notify) await sh.notify(t); },
    log: (m) => { console.log(m); appendFileSync("var/last-callback.log", m + "\n"); },
    placeCall: makePlaceCall(sh, apiKey, VOICE),
  });
  console.log(`\nDone: ${result.reportCalls} report call(s), ${result.callmeCalls} call-back(s), ${result.textCopies} text copies${result.gaveUp.length ? `, gave up on ${result.gaveUp.join(", ")}` : ""}${result.timedOut ? " (time limit reached)" : ""}.`);
}

// ---- 3. tidy up: the agent's results go into Sani's memory too
if (sh.handoff?.jobs.length && mem && process.env.SANI_MEMORY_MIRROR !== "0") { const why = mirrorToSani(voiceCallsMarkdown(mem)); console.log(`memory: Sani copy ${why || "updated with the agent's results"}`); }
sh.handoff?.cancelAll();
core?.close();
client.disconnect();
await new Promise((r) => setTimeout(r, 1600));
process.exit(0);
