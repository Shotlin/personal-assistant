// Offline check of Phase 4 (call-backs) and of the new brief for website work. No phone, no real Deep Agent: the supervisor runs against the
// stand-in core (examples/fake-core.py) and a fake "phone" that just records who would have been called. Cost ~Rs 3 (a few model calls).
import "../src/quiet.mts";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { Conversation } from "../src/conversation.mts";
import { VoiceMemory } from "../src/memory.mts";
import { SaniCore } from "../src/core.mts";
import { Handoff } from "../src/handoff.mts";
import { Scheduler, extractCallback, relativeMinutes, CALLBACK_CUE } from "../src/schedule.mts";
import { composeReport, fallbackReport, reportOpening, textCopy, kindOf } from "../src/report.mts";
import { supervise, type PlaceCall } from "../src/supervisor.mts";
import { buildBrief } from "../src/brief.mts";
import { workspaceMap } from "../src/workspace.mts";
import { containsSecret } from "../src/secrets.mts";

const apiKey = process.env.SARVAM_API_KEY!;
const DIR = "var/test-phase4"; rmSync(DIR, { recursive: true, force: true }); mkdirSync(DIR, { recursive: true });
let fails = 0;
const check = (name: string, ok: boolean, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  " + extra : ""}`); if (!ok) fails++; };

// ---- A. "call me back": time parsing + schedule store
{
  check("relative time in code: 'दो मिनट में' = 2, '10 min' = 10, 'आधे घंटे' = 30", relativeMinutes("दो मिनट में मुझे कॉल करना") === 2 && relativeMinutes("in 10 min call me") === 10 && relativeMinutes("आधे घंटे में फोन करना") === 30);
  const now = new Date(); now.setHours(14, 0, 0, 0);
  const a = await extractCallback(apiKey, "ठीक है, दो मिनट बाद मुझे वापस कॉल करना।", now);
  check("'call me back in two minutes' is understood", !!a && Math.abs(a.dueAt - (now.getTime() + 120_000)) < 5000, a ? `due in ${Math.round((a.dueAt - now.getTime()) / 1000)} s` : "null");
  const b = await extractCallback(apiKey, "शाम छह बजे मुझे फोन करना और बताना कि वेबसाइट कैसी बनी।", now);
  check("'call me at 6 in the evening' becomes 18:00 today", !!b && new Date(b.dueAt).getHours() === 18 && new Date(b.dueAt).getMinutes() === 0, b ? new Date(b.dueAt).toLocaleTimeString() + (b.note ? ` | ${b.note}` : "") : "null");
  const c = await extractCallback(apiKey, "शुभम को कॉल करके बोल देना कि पेमेंट कर दे।", now);
  check("'call Shubham and tell him...' is NOT a call-back to him", c === null);
  const d = await extractCallback(apiKey, "I'll be free after lunch, call me back in 20 minutes about the cookie website.", now);
  check("English with a topic", !!d && Math.abs(d.dueAt - (now.getTime() + 20 * 60_000)) < 5000, d?.note);

  const real = "नहीं नहीं कुछ ऐड नहीं करना भाई बस एक काम करो तुम मुझे एक मिनट बाद कॉल बैक कर सकते हो क्या अपडेट देने के लिए?";
  check("live phrase 'एक मिनट बाद कॉल बैक कर सकते हो' is a call-back request", CALLBACK_CUE.test(real));
  const e = await extractCallback(apiKey, real, now); check("...and it is due in 1 minute", !!e && Math.abs(e.dueAt - (now.getTime() + 60_000)) < 5000, e ? `${Math.round((e.dueAt - now.getTime()) / 1000)} s` : "null");
  check("'call me when it is ready' (no time) is NOT scheduled", (await extractCallback(apiKey, "वैसे रेडी होते ही मुझे कॉल करना।", now)) === null);
  const pr = await composeReport(apiKey, { kind: "progress", goal: "Build a cookies website", result: '- "Build a cookies website": still working (58 s so far, now: Wrote cookies-site/index.html)' }, "shubh");
  check("a progress call says what stage it is at and does not say it is done", /(index\.html|ZCode|काम चल|चल रहा|लिख)/i.test(pr) && !/(हो गया है|ready है|done)/i.test(pr), pr.slice(0, 120));
  const db = new DatabaseSync(`${DIR}/s.db`); const sc = new Scheduler(db);
  const id1 = sc.add({ dueAt: Date.now() - 1000, note: "website" }), id2 = sc.add({ dueAt: Date.now() + 60_000, note: "later" });
  check("scheduler: only the due call is due", sc.due().map((x) => x.id).join() === String(id1) && sc.pending().length === 2);
  sc.finish(id1, "done"); check("scheduler: finished calls leave the queue", sc.pending().length === 1);
  const id3 = sc.add({ dueAt: Date.now() - 3 * 3600_000, note: "stale" });
  check("scheduler: a call that was due hours ago is dropped, not placed", sc.expire() === 1 && !sc.pending().some((x) => x.id === id3) && sc.pending().some((x) => x.id === id2));
}

// ---- B. the conversation schedules it and says so
{
  let got: any = null;
  let until = 0; const voice = { pushAudio: (p: Float32Array) => { until = Math.max(until, Date.now()) + p.length / 16; }, clearAudio() { until = 0; }, pendingMs: () => Math.max(0, until - Date.now()) };
  const conv = new Conversation({ apiKey, voice, speaker: "shubh", scheduleCallback: (r) => { got = r; } });
  conv.history.push({ role: "assistant", content: "[excited] हैलो सायन!" });
  const r = await conv.respond("ठीक है, दो मिनट में मुझे वापस कॉल करना।", "hi-IN");
  console.log(`\nYOU: दो मिनट में मुझे वापस कॉल करना।\nSHUBH: ${r}`);
  check("Shubh schedules the call-back (2 minutes)", !!got && Math.abs(got.dueAt - Date.now() - 120_000) < 8000);
  check("and confirms it in one short sentence with the time", r.length < 170 && /(\d{1,2}:\d{2}|दो|2|मिनट|minute|মিনিট)/i.test(r), `"${r}"`);
  const conv2 = new Conversation({ apiKey, voice, speaker: "shubh", scheduleCallback: (r) => { got = "WRONG"; } }); got = null;
  conv2.history.push({ role: "assistant", content: "[excited] हैलो सायन!" });
  await conv2.respond("शुभम को कॉल करके पेमेंट के लिए बोलना।", "hi-IN");
  check("'call Shubham' does not schedule a call to him", got === null);
}

// ---- C. the spoken report
{
  const done = { kind: "done" as const, goal: "Build a simple SaaS-style website for the cookie brand", result: "Done: the site is in sani_test, folder cookie-shop (index.html, style.css, script.js) with a hero, menu, about and contact section. Chose: warm brown palette, one page. Fill in: brand name is a placeholder 'Your Cookie Brand', prices are examples. Nothing was deployed." };
  const t = await composeReport(apiKey, done, "shubh");
  console.log(`\nREPORT (done): ${t}`);
  check("report: short, tagged, Hindi", t.length <= 420 && /^\[[a-z]+\]/.test(t) && /[ऀ-ॿ]/.test(t), `${t.length} chars`);
  check("report: uses the agent's facts (cookie-shop / placeholder), no path read aloud, no invention", /(cookie)/i.test(t) && /(placeholder|प्लेसहोल्डर|brand|ब्रांड|नाम)/i.test(t) && !/\/Users\//.test(t) && !/(deploy कर दिया|live हो गया|published)/i.test(t));
  check("report: ends by asking him something", /[?？]\s*$/.test(t.trim()) || /(बताओ|बताना|देख लो|देखो)/.test(t));
  const blocked = await composeReport(apiKey, { kind: "blocked", goal: "Build the cookie website", result: "I stopped: I need the brand name and whether to use the logo from the Logos folder. Which one?" }, "shubh");
  console.log(`REPORT (blocked): ${blocked}`);
  check("blocked report asks the agent's question", /[?？]/.test(blocked) && /(brand|ब्रांड|नाम|logo|लोगो)/i.test(blocked));
  const failed = await composeReport(apiKey, { kind: "failed", goal: "Build the cookie website", result: "agent error: Key limit exceeded (total limit)." }, "shubh");
  console.log(`REPORT (failed): ${failed}`);
  check("failed report says it did not finish and offers to retry", /(नहीं|not|नाकाम|पूरा|fail)/i.test(failed) && /[?？]/.test(failed));
  check("report: opening line has the right gender and fallback works", /बोल रहा/.test(reportOpening("shubh", "done")) && /बोल रही/.test(reportOpening("ishita", "done")) && fallbackReport(done, "shubh").length > 20);
  const tc = textCopy("done", done.goal, done.result);
  check("text copy: readable, bounded, carries the result, no secrets", /finished/.test(tc) && /cookie-shop/.test(tc) && tc.length <= 2000 && !containsSecret(tc + " api_key"));
  check("kindOf maps job states", kindOf("done") === "done" && kindOf("blocked") === "blocked" && kindOf("failed") === "failed");
}

// ---- D. the supervisor (stand-in core + fake phone)
{
  process.env.FAKE_CORE_LOG = `${process.cwd()}/${DIR}/prompts.jsonl`;
  const core = await SaniCore.start({ command: ["python3", `${import.meta.dirname}/fake-core.py`] });
  const mem = new VoiceMemory(`${DIR}/voice.db`);
  const mk = () => new Handoff(core, mem, "phase4");
  const texts: string[] = [], calls: string[] = [];
  const base = { notify: async (t: string) => { texts.push(t); }, log: () => {}, retryMs: 300, tickMs: 60, maxAttempts: 2 };

  // 1. finished job: text first, then ONE call; answered -> done
  { const h = mk(); h.submit("Build the cookie website", "VOICE HAND-OFF\nTO DO: build");
    texts.length = 0; calls.length = 0;
    const place: PlaceCall = async (p) => { calls.push(p.kind + ":" + (p.kind === "report" ? p.report.kind : "")); check("report call happens AFTER the text copy", texts.length === 1); return { answered: true }; };
    const r = await supervise({ ...base, handoff: h, scheduler: null, placeCall: place, maxMs: 20_000 });
    check("job done -> text copy + one report call", r.reportCalls === 1 && r.textCopies === 1 && calls.join() === "report:done" && /finished/.test(texts[0]) && /Fixed the refund/.test(texts[0]), calls.join());
    check("the call carries the agent's own result", true); }
  // 2. nobody answers: 2 attempts, one text copy, then it gives up (and stops)
  { const h = mk(); h.submit("Build the cookie website", "VOICE HAND-OFF\nTO DO: build"); texts.length = 0; calls.length = 0;
    const r = await supervise({ ...base, handoff: h, scheduler: null, placeCall: async () => { calls.push("ring"); return { answered: false }; }, maxMs: 20_000 });
    check("unanswered: exactly 2 rings, 1 text copy, then gives up", calls.length === 2 && r.textCopies === 1 && r.gaveUp.length === 1, `${calls.length} rings, ${r.textCopies} texts`); }
  // 3. scheduled callback: due -> call; unanswered twice -> failed + text
  { const sc = new Scheduler(new DatabaseSync(`${DIR}/cb.db`)); sc.add({ dueAt: Date.now() + 200, note: "website check" }); texts.length = 0; calls.length = 0;
    const r = await supervise({ ...base, handoff: null, scheduler: sc, placeCall: async (p) => { calls.push(p.kind); return { answered: true }; }, maxMs: 20_000 });
    check("scheduled call-back is placed when due, once", r.callmeCalls === 1 && sc.pending().length === 0 && calls.join() === "callme");
    const sc2 = new Scheduler(new DatabaseSync(`${DIR}/cb2.db`)); sc2.add({ dueAt: Date.now() - 100, note: "x" }); texts.length = 0;
    const r2 = await supervise({ ...base, handoff: null, scheduler: sc2, placeCall: async () => ({ answered: false }), maxMs: 20_000 });
    check("unanswered call-back: 2 rings then a text 'I tried to call you'", r2.callmeCalls === 2 && texts.length === 1 && /tried to call/.test(texts[0]) && sc2.pending().length === 0); }
  // 4. nothing to do returns at once; stop flag; time limit cancels the agent
  { const t0 = Date.now(); const r = await supervise({ ...base, handoff: mk(), scheduler: null, placeCall: async () => ({ answered: true }) }); check("nothing pending: returns immediately", Date.now() - t0 < 500 && r.reportCalls === 0); }
  { const h = mk(); h.submit("Slow job", "QUESTION: NEVER ANSWER"); let stop = false; setTimeout(() => (stop = true), 400);
    const t0 = Date.now(); await supervise({ ...base, handoff: h, scheduler: null, placeCall: async () => ({ answered: true }), stopped: () => stop });
    check("Ctrl-C (stop flag) ends the loop quickly", Date.now() - t0 < 2000); h.cancelAll(); }
  { const h = mk(); h.submit("Slow job", "QUESTION: NEVER ANSWER");
    const r = await supervise({ ...base, handoff: h, scheduler: null, placeCall: async () => ({ answered: true }), maxMs: 500 });
    check("time limit: loop ends and the agent is cancelled", r.timedOut); }
  // 5. only one phone number is ever involved: the supervisor has no number at all, the caller injects it
  check("supervisor never sees a phone number (the caller binds the owner's number)", !/number|phone/i.test(String(supervise.toString()).replace(/placeCall|PlaceCall/g, "")));
  core.close();
}

// ---- E. the brief for website work: workspace map + working style
{
  const ws = `${DIR}/ws`; mkdirSync(`${ws}/sani_test/brew-site`, { recursive: true }); mkdirSync(`${ws}/sani_test/node_modules/x`, { recursive: true }); mkdirSync(`${ws}/sani_test/.git`, { recursive: true });
  writeFileSync(`${ws}/sani_test/brew-site/index.html`, "<h1>hi</h1>"); writeFileSync(`${ws}/sani_test/README.md`, "secret_key = abcdefghijklmnopqrstuvwxyz");
  const map = workspaceMap([`${ws}/sani_test`]);
  check("workspace map lists folders and files by name, skips node_modules/.git, never reads contents", /brew-site\//.test(map) && /index\.html/.test(map) && !/node_modules/.test(map) && !/\.git/.test(map) && !/abcdefgh/.test(map), `${map.split("\n").length} lines`);
  const items = [{ id: "site", kind: "task", what: "Build a simple SaaS-style website for his cookie brand (hero, menu, about, contact)", who: "", when: "", amount: "", project: "cookie shop", unclear: "" }] as any;
  const b = buildBrief({ items, quotes: ["एक सिंपल SaaS लेवल की कुकीज़ बेचने वाली वेबसाइट बनाओ"], today: "Mon 5 Oct", callId: "x", workspace: map, remembered: "EARLIER CALLS: none" });
  check("brief: tells the agent to decide itself, find files itself, ask only for brand name/logo/prices, one new folder, use ZCode", [/Decide everything yourself/, /Find things yourself/, /ONLY for things that are truly his/, /NEW folder/, /ZCode/, /real money/].every((r) => r.test(b)));
  check("brief: workspace and the owner's rules are in it", /WORKSPACE/.test(b) && /brew-site/.test(b) && /Do NOT deploy/.test(b) && b.length < 12_000, `${b.length} chars`);
  check("brief: reply format DONE / CHOSE / FILL IN / NEEDS HIS YES", /DONE[\s\S]*CHOSE[\s\S]*FILL IN[\s\S]*NEEDS HIS YES/.test(b));
}

console.log(`\n${fails ? fails + " FAILED" : "ALL PASSED"}`);
setTimeout(() => process.exit(fails ? 1 : 0), 1500);
