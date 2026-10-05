// DRY RUN of the whole phone loop with NO phone: a fake WhatsApp client replays a recording of YOUR voice (var/last-call-in.wav) into the real
// pipeline (Sarvam listening -> Shubh -> Sarvam speaking), then a fake job finishes and Shubh "calls back" with the spoken report and a text copy.
// Costs ~Rs 3-4 (listening + speaking + chat). Uses temporary memory (var/test-dry/) and never writes to Sani's real memory.
//   npm run dryrun
import "../src/quiet.mts";
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { EventEmitter } from "node:events";
import { VoiceMemory } from "../src/memory.mts";
import { SaniCore } from "../src/core.mts";
import { Handoff } from "../src/handoff.mts";
import { Scheduler } from "../src/schedule.mts";
import { runLiveCall, type LiveShared } from "../src/liveCall.mts";
import { supervise } from "../src/supervisor.mts";
import { makePlaceCall } from "../src/placeCall.mts";

const apiKey = process.env.SARVAM_API_KEY!;
const DIR = "var/test-dry"; rmSync(DIR, { recursive: true, force: true }); mkdirSync(DIR, { recursive: true });
process.env.SANI_MEMORY_MIRROR = "0";
process.env.FAKE_CORE_LOG = `${process.cwd()}/${DIR}/prompts.jsonl`;
const SRC = "var/last-call-in.wav";
if (!existsSync(SRC)) { console.error(`needs ${SRC} (a recording of your side of a call)`); process.exit(1); }
copyFileSync(SRC, `${DIR}/in.wav`);
const wav = readFileSync(`${DIR}/in.wav`);
const i16 = new Int16Array(wav.buffer.slice(wav.byteOffset + 44, wav.byteOffset + wav.length - ((wav.length - 44) % 2)));
const all = Float32Array.from(i16, (v) => v / 32768);
// the recording starts at program start, so it begins with the ring + a long silence: start 1 s before the first real speech
let lead = 0; while (lead < all.length && Math.abs(all[lead]) < 0.02) lead++;
const f32 = all.subarray(Math.max(0, lead - 16000));

let fails = 0;
const check = (name: string, ok: boolean, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  " + extra : ""}`); if (!ok) fails++; };

/** A fake WhatsApp client: same surface runLiveCall uses. */
class FakeClient {
  sentSamples = 0; texts: string[] = []; calls = 0; answerNext = true; replaySeconds = 40; endedByUs = 0;
  onSentAudio: ((c: Float32Array) => void) | null = null;
  pushAudio = (p: Float32Array) => { this.sentSamples += p.length; this.onSentAudio?.(p); this.#queued += p.length; };
  #queued = 0; #t0 = 0;
  clearAudio = () => { this.#queued = 0; };
  pendingAudioMs = () => Math.max(0, (this.#queued / 16) - (Date.now() - this.#t0));
  sendText = async (_n: string, t: string) => { this.texts.push(t); };
  connect = async () => {}; disconnect = () => {};
  call = async (_number: string, _opts: any) => {
    this.calls++; const ev: any = new EventEmitter(); const answer = this.answerNext; let ended = false;
    let resolveEnd!: () => void; const endP = new Promise<void>((r) => (resolveEnd = r));
    const end = (reason: string) => { if (ended) return; ended = true; ev.emit("ended", reason); resolveEnd(); };
    ev.end = () => { this.endedByUs++; end("local_end"); };
    ev.waitForEnd = () => endP;
    setTimeout(() => ev.emit("ringing"), 100);
    if (answer) setTimeout(async () => {
      ev.emit("connected"); this.#t0 = Date.now(); this.#queued = 0;
      await new Promise((r) => setTimeout(r, 6000)); // he listens to the greeting / report first
      const frames = Math.min(f32.length, this.replaySeconds * 16000);
      for (let o = 0; o < frames && !ended; o += 320) { ev.emit("audio", f32.subarray(o, o + 320)); await new Promise((r) => setTimeout(r, 20)); }
      await new Promise((r) => setTimeout(r, 9000)); end("remote_end"); // he stops talking, and hangs up
    }, 300);
    return ev;
  };
}

const client = new FakeClient();
const core = await SaniCore.start({ command: ["python3", `${import.meta.dirname}/fake-core.py`] });
const mem = new VoiceMemory(`${DIR}/voice.db`);
const sh: LiveShared = { client: client as any, apiKey, number: "910000000000", voice: "shubh", mem, core, inCall: false, handoff: new Handoff(core, mem, "dry"), scheduler: new Scheduler(mem.db), notify: async (t) => { await client.sendText("x", t); } };

console.log("=== 1. the first call (your recorded voice, first 40 s) ===");
const first = await runLiveCall(sh, { kind: "conversation" }, { maxMs: 70_000, ringMs: 10_000 });
check("first call connected and ended", first.answered && first.seconds > 10, `${first.seconds} s, reason ${first.reason}`);
check("Shubh spoke into the call (greeting and replies)", client.sentSamples / 16000 > 3, `${(client.sentSamples / 16000).toFixed(1)} s of speech`);
check("the call was saved to memory", mem.recentCalls().length === 1, mem.recentCalls()[0]?.summary.slice(0, 120));
check("the line is free again after the call", sh.inCall === false);

console.log("\n=== 2. the Deep Agent finishes (stand-in core) and Shubh calls back ===");
sh.handoff!.submit("Build a simple SaaS-style website for the cookie brand", "VOICE HAND-OFF\nTO DO: build the website");
client.replaySeconds = 25; client.sentSamples = 0; client.texts.length = 0; const callsBefore = client.calls;
const res = await supervise({ handoff: sh.handoff, scheduler: sh.scheduler, notify: async (t) => { await sh.notify!(t); }, log: (m) => console.log(m), placeCall: makePlaceCall(sh, apiKey, "shubh", { maxMs: 60_000, ringMs: 10_000 }), maxMs: 120_000, tickMs: 500 });
check("one text copy and one report call", res.textCopies === 1 && res.reportCalls === 1 && client.calls - callsBefore === 1, JSON.stringify(res));
check("the text copy carries the agent's result", /Fixed the refund flow/.test(client.texts[0] ?? ""), (client.texts[0] ?? "").split("\n")[0]);
check("Shubh spoke the report (opening + body)", client.sentSamples / 16000 > 5, `${(client.sentSamples / 16000).toFixed(1)} s`);
check("the finished job is marked reported", sh.handoff!.jobs[0].reported === true);

console.log("\n=== 3. nobody picks up: two rings, then the text copy only ===");
sh.handoff!.submit("Second job", "VOICE HAND-OFF\nTO DO: another"); client.answerNext = false; client.texts.length = 0; const c2 = client.calls;
const res2 = await supervise({ handoff: sh.handoff, scheduler: sh.scheduler, notify: async (t) => { await sh.notify!(t); }, log: () => {}, placeCall: makePlaceCall(sh, apiKey, "shubh", { maxMs: 30_000, ringMs: 800 }), maxMs: 120_000, tickMs: 300, retryMs: 800 });
check("unanswered report: 2 rings that hang up by themselves, 1 text copy, gives up", client.calls - c2 === 2 && res2.textCopies === 1 && res2.gaveUp.length === 1, `${client.calls - c2} rings`);

console.log("\n=== 4. 'call me back' scheduled during a call is placed later ===");
client.answerNext = true; client.replaySeconds = 8; sh.scheduler!.add({ dueAt: Date.now() + 1500, note: "website status" }); const c3 = client.calls;
const res3 = await supervise({ handoff: sh.handoff, scheduler: sh.scheduler, notify: async () => {}, log: () => {}, placeCall: makePlaceCall(sh, apiKey, "shubh", { maxMs: 40_000, ringMs: 5000 }), maxMs: 120_000, tickMs: 300 });
check("the call-back rang once when due", client.calls - c3 === 1 && res3.callmeCalls === 1 && sh.scheduler!.pending().length === 0);

core.close();
console.log(`\n${fails ? fails + " FAILED" : "ALL PASSED"}`);
setTimeout(() => process.exit(fails ? 1 : 0), 1500);
