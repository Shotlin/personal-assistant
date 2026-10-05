// Offline end-to-end check of Phase 3 on the owner's real sentences. The Deep Agent is a STAND-IN core that speaks the real framed protocol
// (examples/fake-core.py), so it costs only the voice/chat calls (~Rs 3-4). Part B talks to the REAL core (agents.list is free; a real run is
// attempted and reported as SKIP if the model key is out of credit).
import "../src/quiet.mts";
import { mkdirSync, readFileSync, rmSync, existsSync } from "node:fs";
import { Conversation } from "../src/conversation.mts";
import { VoiceMemory } from "../src/memory.mts";
import { SaniCore, coreEnv } from "../src/core.mts";
import { Handoff } from "../src/handoff.mts";
import { makeExpert } from "../src/expert.mts";
import { buildBrief, OWNER_RULES } from "../src/brief.mts";
import { buildContext } from "../src/context.mts";
import { containsSecret } from "../src/secrets.mts";

const apiKey = process.env.SARVAM_API_KEY!;
const DIR = "var/test-phase3"; rmSync(DIR, { recursive: true, force: true }); mkdirSync(DIR, { recursive: true });
process.env.FAKE_CORE_LOG = `${process.cwd()}/${DIR}/core-prompts.jsonl`;
let fails = 0;
const check = (name: string, ok: boolean, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  " + extra : ""}`); if (!ok) fails++; };

// ---- A. brief (no model)
{
  const items = [
    { id: "fix-refund", kind: "task", what: "Fix the refund flow in the grocery app (customer app and dashboard)", who: "", when: "", amount: "", project: "grocery app", unclear: "" },
    { id: "no-ui", kind: "constraint", what: "Do not change the UI", who: "", when: "", amount: "", project: "grocery app", unclear: "" },
    { id: "meet", kind: "meeting", what: "Meeting with Rajput", who: "Rajput", when: "2 pm", amount: "", project: "Baklo", unclear: "" },
    { id: "pay", kind: "payment", what: "Collect Rs 20,000 plus GST from Shubham", who: "Shubham", when: "", amount: "Rs 20,000 plus GST", project: "", unclear: "which invoice?" },
  ] as any;
  const b = buildBrief({ items, quotes: ["रिफंड फ्लो ठीक करना है, UI मत छूना। मेरा password है 48215"], today: "Mon 5 Oct 2026", callId: "t" });
  check("brief: task under TO DO, constraint listed, meeting only as context", /TO DO[\s\S]*Fix the refund flow/.test(b) && /CONSTRAINTS[\s\S]*Do not change the UI/.test(b) && /CONTEXT[\s\S]*Meeting with Rajput/.test(b) && !/TO DO[^]*?Meeting with Rajput[^]*?CONSTRAINTS/.test(b));
  check("brief: owner's four standing rules present", [/deploy or publish/i, /spend money/i, /message, email or call/i, /delete/i].every((r) => r.test(OWNER_RULES) && r.test(b)));
  check("brief: unclear item becomes an open question", /OPEN QUESTIONS[\s\S]*which invoice\?/.test(b));
  check("brief: a spoken password never reaches the agent", !/48215/.test(b) && !containsSecret(b));
  check("brief: bounded size", b.length < 12_000, `${b.length} chars`);
}

// ---- B. the real core: protocol handshake (free) and one real run
{
  const env = coreEnv({ coding: false });
  check("core env: computer control is OFF and coding is capped", env.CUA_ENABLED === "false" && coreEnv().CLAUDE_CODE_PERMISSION !== "run");
  check("core env: the voice key is never passed on", !("SARVAM_API_KEY" in env));
  try {
    const t0 = Date.now(); const real = await SaniCore.start({ coding: false });
    const a = await real.request("agents.list", {});
    check("real sani-core boots and lists agents", a.agents.some((x: any) => x.id === "deep"), `${((Date.now() - t0) / 1000).toFixed(1)} s, protocol ${a.protocol_version}`);
    const h = real.startRun("Reply with exactly the word: pong", { threadId: "voice-test-real" }); const r = await h.done;
    if (r.status === "done") check("real Deep Agent answers a run", /pong/i.test(r.response), `"${r.response.slice(0, 60)}"`);
    else console.log(`SKIP  real Deep Agent run: ${r.status}: ${r.response.slice(0, 140)}`);
    real.close();
  } catch (e: any) { check("real sani-core boots", false, e.message.slice(0, 200)); }
}

// ---- C. the conversation with a stand-in core
const core = await SaniCore.start({ command: ["python3", `${import.meta.dirname}/fake-core.py`] });
const mem = new VoiceMemory(`${DIR}/voice.db`);
const callId = "phase3-test";
let until = 0; const voice = { pushAudio: (p: Float32Array) => { until = Math.max(until, Date.now()) + p.length / 16; }, clearAudio() { until = 0; }, pendingMs: () => Math.max(0, until - Date.now()) };
const handoff = new Handoff(core, mem, callId);
let conv: Conversation;
const logs: string[] = [];
const expert = makeExpert(core, { callId, items: () => conv.board.items, log: (m) => logs.push(m), timeoutMs: 9000 });
conv = new Conversation({ apiKey, speaker: "shubh", voice, expert, canSend: true, jobStatus: () => handoff.statusText(), context: buildContext(mem, { sani: null }) });
conv.history.push({ role: "assistant", content: "[excited] हैलो सायन! मैं शुभ बोल रहा हूँ। बताओ, क्या खबर है?" });
const say = async (text: string, lang = "hi-IN") => { const r = await conv.respond(text, lang); await conv.board.updating; console.log(`\n[${conv.lastMode}] YOU: ${text}\n        SHUBH: ${r}`); return r; };
const spokenAll: string[] = [];

spokenAll.push(await say("मेरे ग्रोसरी ऐप का रिफंड फ्लो ठीक करना है, कस्टमर ऐप और डैशबोर्ड दोनों में। UI मत छूना और टेस्ट ज़रूर चलाना।"));
check("a plain request starts work: no magic phrase, no permission question", conv.taskRequested && !/(भेज दूँ|क्या मैं|shall I)/i.test(spokenAll[0]), `"${spokenAll[0].slice(0, 120)}"`);
check("nothing is sent while he can still add or cancel (the caller sends after the quiet period)", handoff.jobs.length === 0);
const job = conv.taskRequested ? handoff.sendFromBoard({ items: conv.board.items, quotes: conv.transcript().filter((l) => l.role === "user").map((l) => l.text), today: "Mon 5 Oct", deferred: conv.deferred.splice(0), taskText: "refund flow" }) : null;
check("job started with the task", !!job && /refund|रिफंड/i.test(job.goal), job?.goal.slice(0, 80));
const sentLine = spokenAll[spokenAll.length - 1];
check("Shubh says he is starting it, in a short reply, without naming a time", sentLine.length < 330 && !/\b\d+\s*(मिनट|minute|min)\b/i.test(sentLine), `"${sentLine}"`);
check("no marker is ever spoken", !spokenAll.some((t) => /\[\[|\]\]/.test(t)));
await new Promise((r) => setTimeout(r, 400));
const prompt = JSON.parse(readFileSync(process.env.FAKE_CORE_LOG!, "utf8").trim().split("\n")[0]);
check("the agent received the brief with the UI constraint and the rules", /Do not (change|touch)[^\n]*UI/i.test(prompt.text) && /deploy or publish/.test(prompt.text) && /TO DO/.test(prompt.text), `thread ${prompt.thread}`);

// a second yes must not send the same work twice
const again = handoff.sendFromBoard({ items: conv.board.items, quotes: [], today: "x" });
check("the same task is never sent twice", again === null);

// status while it works, then after
spokenAll.push(await say("अच्छा, उसका क्या हुआ? कहाँ तक पहुँचा?"));
check("status question is answered from the job (still working)", conv.lastMode === "recall" && /(काम|चल रहा|working|कर रहा|रहा है|शुरू|test|टेस्ट|refund|रिफंड)/i.test(spokenAll[spokenAll.length - 1]), `[${conv.lastMode}] "${spokenAll[spokenAll.length - 1].slice(0, 100)}"`);
await job!.handle.done;
check("job result stored in voice.db", mem.recentJobs()[0]?.status === "done" && /refund/i.test(mem.recentJobs()[0].result));
spokenAll.push(await say("और अब? हो गया क्या?"));
check("after it finished Shubh reports the result", /(हो गया|ठीक|fixed|पास|pass|test|टेस्ट|नहीं छुआ|untouched|deploy)/i.test(spokenAll[spokenAll.length - 1]), `"${spokenAll[spokenAll.length - 1].slice(0, 120)}"`);
console.log(`   job report -> ${mem.recentJobs()[0].result}`);

// expert question mid-call
const spokenBefore = conv.stats.ttsChars + conv.stats.cachedChars;
const ex = await say("एक चीज़ बताओ, nginx में 502 bad gateway आ रहा है उसका क्या मतलब है और कैसे ठीक करूँ?");
check("a technical question goes to the agent (expert mode)", conv.lastMode === "expert", `[${conv.lastMode}]`);
check("the answer uses what the agent said (502, restart, log)", /(502)/.test(ex) && /(restart|रीस्टार्ट|log|लॉग|crash|क्रैश|रिस्टार्ट)/i.test(ex), `"${ex.slice(0, 140)}"`);
check("a waiting line was spoken while the agent worked", conv.stats.ttsChars + conv.stats.cachedChars - spokenBefore > ex.length + 5);
check("expert answered quickly", logs.some((l) => /answered in/.test(l)), logs.at(-1) ?? "");

// the agent does not answer in time: no guessing, the question is kept
const slowCore = makeExpert(core, { callId, items: () => [], log: (m) => logs.push(m), timeoutMs: 3000 });
const conv2 = new Conversation({ apiKey, speaker: "shubh", voice, expert: (q, l) => slowCore(q + " NEVER ANSWER", l), canSend: true, context: "x" });
conv2.history.push({ role: "assistant", content: "[excited] हैलो सायन!" });
const slow = await conv2.respond("ये बताओ, मेरे सर्वर में memory leak कहाँ से आ रहा है, ठीक-ठीक कौन सा function?", "hi-IN");
console.log(`\n[${conv2.lastMode}] (agent silent) SHUBH: ${slow}`);
check("when the agent is silent Shubh does not invent an answer and keeps the question", conv2.lastMode === "expert" && conv2.deferred.length === 1 && !/(function|फंक्शन)\s*[A-Za-z_]+\(\)/.test(slow), slow.slice(0, 100));

// consent: no yes -> nothing is sent
const conv3 = new Conversation({ apiKey, speaker: "shubh", voice, canSend: true });
conv3.history.push({ role: "assistant", content: "[excited] हैलो सायन!" });
await conv3.respond("लॉन्ड्री ऐप का लोगो बदलना है, UI वही रखना।", "hi-IN"); await conv3.board.updating;
await conv3.respond("ठीक है, तो रुको, एक चीज़ और है, पहले मैं सोच लेता हूँ।", "hi-IN");
check("'wait, one more thing' does not send anything", !conv3.sendRequested);
const conv4 = new Conversation({ apiKey, speaker: "shubh", voice, canSend: true });
conv4.history.push({ role: "assistant", content: "[excited] हैलो सायन!" });
await conv4.respond("कल तीन बजे अमित के साथ मीटिंग है और अमित से पाँच हज़ार लेने हैं।", "hi-IN"); await conv4.board.updating;
await conv4.respond("भेज दो एजेंट को।", "hi-IN");
check("'send it' with only a meeting and a payment on the board sends nothing (they are notes, not work)", !conv4.sendRequested);

const cost = conv.costInr();
console.log(`\ncost: voice Rs ${cost.tts.toFixed(2)}, chat Rs ${cost.llm.toFixed(2)}.  ${fails ? fails + " FAILED" : "ALL PASSED"}`);
core.close(); setTimeout(() => process.exit(fails ? 1 : 0), 1800);
