// Replays the owner's REAL sentences from the live call of 2026-10-05 14:21 (the one where nothing was sent) and checks each failure is gone.
// Stand-in Deep Agent, no phone. ~Rs 2.
import "../src/quiet.mts";
import { mkdirSync, readFileSync, rmSync, existsSync } from "node:fs";
import { Conversation } from "../src/conversation.mts";
import { VoiceMemory } from "../src/memory.mts";
import { SaniCore } from "../src/core.mts";
import { Handoff } from "../src/handoff.mts";
import { Scheduler } from "../src/schedule.mts";
import { workspaceMap } from "../src/workspace.mts";

const apiKey = process.env.SARVAM_API_KEY!;
const DIR = "var/test-live"; rmSync(DIR, { recursive: true, force: true }); mkdirSync(DIR, { recursive: true });
process.env.FAKE_CORE_LOG = `${process.cwd()}/${DIR}/prompts.jsonl`;
let fails = 0;
const check = (name: string, ok: boolean, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  " + extra : ""}`); if (!ok) fails++; };

const core = await SaniCore.start({ command: ["python3", `${import.meta.dirname}/fake-core.py`] });
const mem = new VoiceMemory(`${DIR}/voice.db`); const sched = new Scheduler(mem.db); const handoff = new Handoff(core, mem, "live");
// yesterday's call had a person called Deepak: the thing that made "Deep Agent" sound like a person
mem.saveCall({ id: "old", startedAt: Date.now() - 20 * 3600_000, endedAt: Date.now() - 20 * 3600_000 + 60000, durationS: 60, language: "hi-IN", summary: "Sayan will collect Rs 20,000 plus GST from Deepak in the evening.", decisions: [], topics: ["deepak"], costInr: 0 },
  [{ id: "collect-deepak", kind: "payment", what: "Collect Rs 20,000 plus GST from Deepak", who: "Deepak", when: "evening", amount: "Rs 20,000 plus GST", project: "", unclear: "" }]);
let until = 0; const voice = { pushAudio: (p: Float32Array) => { until = Math.max(until, Date.now()) + p.length / 16; }, clearAudio() { until = 0; }, pendingMs: () => Math.max(0, until - Date.now()) };
let T0 = Date.now();
const conv: Conversation = new Conversation({ apiKey, voice, speaker: "shubh", canSend: true, jobStatus: () => handoff.statusText(),
  context: "EARLIER PHONE CALLS WITH SAYAN (newest first):\n- yesterday: Sayan will collect Rs 20,000 plus GST from Deepak in the evening.\nSTILL OPEN from earlier calls:\n- [payment] Collect Rs 20,000 plus GST from Deepak | who: Deepak | when: evening",
  scheduleCallback: (r) => { sched.cancelPendingSince(T0); sched.add(r); } });
conv.board.hints = "Deepak, Baklo";
conv.history.push({ role: "assistant", content: "[excited] हैलो सायन! मैं शुभ बोल रहा हूँ। बताओ, क्या खबर है?" });
const say = async (t: string) => { const r = await conv.respond(t, "hi-IN"); await conv.board.updating; console.log(`\nYOU: ${t}\nSHUBH [${conv.lastMode}]: ${r}`); return r; };

// 1. the sentence that started it all
const r1 = await say("खबर तो कुछ नहीं है अभी। अभी तो मुझे एक वेबसाइट चाहिए, तुम जाके डीप एजेंट को बोलो वेबसाइट बनाने के लिए जो एक नॉर्मल कुकीज़ सेलिंग वेबसाइट हो, नॉर्मल।");
check("telling the Deep Agent to build it is recognised as work to start (no extra yes needed)", conv.taskRequested || conv.sendRequested);
check("Shubh does not turn the agent into a person (no Deepak)", !/(deepak|दीपक|दीप को|Dipak)/i.test(r1), `"${r1.slice(0, 110)}"`);
const job = conv.taskRequested || conv.sendRequested ? handoff.sendFromBoard({ items: conv.board.items, quotes: conv.transcript().filter((l) => l.role === "user").map((l) => l.text), today: "Mon 5 Oct", workspace: workspaceMap(), deferred: conv.deferred.splice(0), taskText: "cookie website" }) : null;
check("the job was really created from his words (cookie website)", !!job && /(cookie|कुकी|website|वेबसाइट)/i.test(job.goal), job?.goal.slice(0, 100));
await new Promise((r) => setTimeout(r, 500));
const prompt = existsSync(process.env.FAKE_CORE_LOG!) ? JSON.parse(readFileSync(process.env.FAKE_CORE_LOG!, "utf8").trim().split("\n")[0]).text as string : "";
check("the brief has the task, the working style and no Deepak", /TO DO/.test(prompt) && /(cookie|कुकी)/i.test(prompt) && /Decide everything yourself/.test(prompt) && !/(who: Deepak)/.test(prompt.split("CONTEXT")[0]));
check("Shubh says he is starting it and will call when it is ready, no time given", /(कॉल|call|ফোন)/i.test(r1) && !/\b\d+\s*(मिनट|minute)/i.test(r1), `"${r1.slice(0, 120)}"`);

// 2. a detail that comes after
await say("एक लैंडिंग पेज बना के दो मुझे। बस।");

// 3. "call me when it is ready": NOT a timed call-back
const r3 = await say("वैसे रेडी होते ही मुझे कॉल करना।");
check("'call me when it is ready' schedules nothing (the report call happens by itself)", sched.pending().length === 0, `${sched.pending().length} scheduled`);
check("and Shubh does not invent a number of minutes", !/\b(१०|10|दस)\s*(मिनट|minute)/i.test(r3), `"${r3}"`);

// 4. a real timed request, said twice (a correction)
await say("दस मिनट नहीं, तीन मिनट में मुझे कॉल करके अपडेट दो।");
const r4 = await say("दस मिनट नहीं, दो मिनट में मुझे कॉल बैक करके अपडेट देना।");
check("only ONE call-back is pending (the newest replaces the earlier one)", sched.pending().length === 1, `${sched.pending().length} pending`);
const p = sched.pending()[0]; const mins = p ? Math.round((p.dueAt - Date.now()) / 60000) : -1;
check("it is due in about 2 minutes", mins >= 1 && mins <= 3, `${mins} min`);
check("Shubh confirms exactly 2 minutes, not 10", /(दो|2|২)/.test(r4) && !/(१०|10|दस|১০)/.test(r4), `"${r4}"`);

// 5. consent is still needed when it is not an explicit instruction
const conv2 = new Conversation({ apiKey, voice, speaker: "shubh", canSend: true }); conv2.history.push({ role: "assistant", content: "[excited] हैलो सायन!" });
await conv2.respond("डीप एजेंट आजकल कैसा चल रहा है, कुछ पता है?", "hi-IN");
check("merely mentioning the Deep Agent sends nothing", !conv2.sendRequested);
core.close();
console.log(`\ncost: voice Rs ${conv.costInr().tts.toFixed(2)}, chat Rs ${conv.costInr().llm.toFixed(2)}.  ${fails ? fails + " FAILED" : "ALL PASSED"}`);
setTimeout(() => process.exit(fails ? 1 : 0), 1500);
