// Checks the "just say it" flow on varied, ordinary requests: no magic phrase, no extra yes. Shubh understands, says he is starting, the task is
// sent a few seconds later with a summary of the WHOLE call, the Deep Agent is told to write the ZCode prompt itself, and "wait" cancels.
// Stand-in Deep Agent, no phone. ~Rs 3.
import "../src/quiet.mts";
import { mkdirSync, readFileSync, rmSync, existsSync } from "node:fs";
import { Conversation } from "../src/conversation.mts";
import { routeTurn } from "../src/judge.mts";
import { SaniCore } from "../src/core.mts";
import { Handoff } from "../src/handoff.mts";
import { VoiceMemory } from "../src/memory.mts";
import { SendSettler } from "../src/settle.mts";
import { summarizeRequirement } from "../src/requirement.mts";
import { workspaceMap } from "../src/workspace.mts";

const apiKey = process.env.SARVAM_API_KEY!;
const DIR = "var/test-flex"; rmSync(DIR, { recursive: true, force: true }); mkdirSync(DIR, { recursive: true });
process.env.FAKE_CORE_LOG = `${process.cwd()}/${DIR}/prompts.jsonl`;
let fails = 0;
const check = (name: string, ok: boolean, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  " + extra : ""}`); if (!ok) fails++; };
const hello = "[excited] हैलो सायन! मैं शुभ बोल रहा हूँ। बताओ, क्या खबर है?";

// ---- A. any ordinary wording is recognised as work (the router), and non-work is not
const work = ["मुझे एक वेबसाइट चाहिए जो कुकीज़ बेचती हो, मेरे ब्रांड के साथ।", "ek simple landing page bana do cookies ke liye", "Make me a SaaS-style website for my cookie brand.", "আমার কুকিজ ব্র্যান্ডের জন্য একটা সিম্পল ওয়েবসাইট বানিয়ে দাও।", "रिफंड फ्लो में जो बग है उसे ठीक कर दो, UI मत छूना।", "तुम डीप एजेंट को बोलो कि लॉन्ड्री ऐप का लोगो बदल दे।", "इस ऐप में डार्क मोड भी जोड़ दो।"];
const notWork = ["अच्छा ये बताओ, हमें अपनी वेबसाइट का प्राइस कितना रखना चाहिए?", "कल मैंने लॉन्ड्री ऐप के बारे में क्या बोला था?", "ठीक है, समझ गया, धन्यवाद।", "कल तीन बजे अमित के साथ मीटिंग है और उससे पाँच हज़ार लेने हैं।", "nginx में 502 bad gateway आ रहा है उसका क्या मतलब है?"];
let ok = 0; for (const t of work) { const r = await routeTurn(apiKey, t, hello); if (r === "TASK") ok++; else console.log(`   (work, routed ${r}): ${t.slice(0, 60)}`); }
check("ordinary requests are routed as TASK, whatever the wording", ok >= work.length - 1, `${ok}/${work.length}`);
let ok2 = 0; for (const t of notWork) { const r = await routeTurn(apiKey, t, hello); if (r !== "TASK") ok2++; else console.log(`   (not work, routed TASK): ${t.slice(0, 60)}`); }
check("questions, recall, notes and thanks are NOT tasks", ok2 === notWork.length, `${ok2}/${notWork.length}`);

// ---- B. the settle timing (fast timers)
{
  let sent = 0; const st = new SendSettler(() => sent++, { quietMs: 150, maxMs: 600 });
  st.arm(); await new Promise((r) => setTimeout(r, 300)); check("settler: sends after the quiet period", sent === 1 && !st.pending);
  sent = 0; st.arm(); await new Promise((r) => setTimeout(r, 100)); st.pause(); await new Promise((r) => setTimeout(r, 300)); check("settler: never sends while he is speaking (paused)", sent === 0 && st.pending);
  st.touch(); await new Promise((r) => setTimeout(r, 300)); check("settler: after his turn another quiet period passes, then it sends", sent === 1);
  sent = 0; st.arm(); st.cancel(); await new Promise((r) => setTimeout(r, 300)); check("settler: 'wait' cancels it", sent === 0 && !st.pending);
  sent = 0; st.arm(); st.flush(); check("settler: hang-up sends at once", sent === 1);
  sent = 0; const st2 = new SendSettler(() => sent++, { quietMs: 400, maxMs: 500 }); st2.arm(); for (let i = 0; i < 4; i++) { await new Promise((r) => setTimeout(r, 150)); st2.touch(); } await new Promise((r) => setTimeout(r, 250));
  check("settler: if he keeps talking it still sends by the maximum wait", sent === 1);
}

// ---- C. the requirement summary of a whole, messy briefing (his real style)
const lines = ["खबर तो कुछ नहीं है अभी। अभी तो मुझे एक वेबसाइट चाहिए जो एक नॉर्मल कुकीज़ सेलिंग वेबसाइट हो, SaaS लेवल की, सिंपल।", "उसमें मेरे ब्रांड का नाम दिखना चाहिए, ब्रांड का नाम है क्रंची क्रंब।", "एक होम पेज हो, कुकीज़ का मेन्यू हो, और कॉन्टैक्ट सेक्शन हो। रंग भूरा-क्रीम रखना। और हाँ, डीप एजेंट को बोलो कि कोई डिप्लॉय मत करे।"];
const req = await summarizeRequirement(apiKey, lines, []);
console.log("\nREQUIREMENT:", JSON.stringify(req, null, 1).slice(0, 900));
check("summary understood the goal (cookie website)", !!req && /(cookie|website)/i.test(req.goal));
check("summary keeps his brand name exactly and the sections", !!req && /(crunchy|crumb|क्रंची|क्रंब)/i.test(JSON.stringify(req)) && /(menu|मेन्यू)/i.test(JSON.stringify(req)) && /(contact|कॉन्टैक्ट)/i.test(JSON.stringify(req)));
check("summary keeps his rule (do not deploy) and the colour", !!req && /(deploy|डिप्लॉय)/i.test(JSON.stringify(req)) && /(brown|cream|भूरा|क्रीम)/i.test(JSON.stringify(req)));
check("summary does not turn the Deep Agent into a person", !!req && !/deepak/i.test(JSON.stringify(req)));

// the REAL lines of the live call that went wrong (2026-10-05 15:05): no names from earlier calls may leak in
{
  const real = ["अरे खबर तो बढ़िया, तुम कैसे हो?", "आज एक काम निपटाना है। जो काम निपटाना है ना, एक मुझे कुकीज़ वेबसाइट चाहिए। तुम थोड़ा कुकीज़ वेबसाइट बना दो ना।", "नहीं नहीं कुछ ऐड नहीं करना भाई बस एक काम करो तुम मुझे एक मिनट बाद कॉल बैक कर सकते हो क्या अपडेट देने के लिए?", "ओके।", "Ha ha rakho rakho okay. एक मिनट बाद कॉल करता हूँ।"];
  const r2 = await summarizeRequirement(apiKey, real, [{ id: "w", kind: "task", what: "Build a cookies website", who: "", when: "", amount: "", project: "", unclear: "" }] as any);
  console.log("\nREAL-CALL REQUIREMENT:", JSON.stringify(r2).slice(0, 500));
  check("real call: no Deepak / Laundry / Baklo / GST / POS leaks into the requirement", !!r2 && !/(deepak|laundry|baklo|gst|pos\b|us system)/i.test(JSON.stringify(r2)));
  check("real call: it is just a cookies website, the call-back request is not a requirement", !!r2 && /cookie/i.test(r2.goal) && !/call/i.test(JSON.stringify(r2.details)));
}

// ---- D. the conversation: plain requests start work, wait cancels, a detail is merged
const core = await SaniCore.start({ command: ["python3", `${import.meta.dirname}/fake-core.py`] });
const mem = new VoiceMemory(`${DIR}/voice.db`);
let until = 0; const voice = { pushAudio: (p: Float32Array) => { until = Math.max(until, Date.now()) + p.length / 16; }, clearAudio() { until = 0; }, pendingMs: () => Math.max(0, until - Date.now()) };
const mk = () => { const c = new Conversation({ apiKey, voice, speaker: "shubh", canSend: true }); c.history.push({ role: "assistant", content: hello }); return c; };
for (const [label, text, lang] of [["Hindi", "मुझे एक वेबसाइट चाहिए जो कुकीज़ बेचती हो, मेरे ब्रांड क्रंची क्रंब के नाम से।", "hi-IN"], ["English", "Make me a SaaS-style website for my cookie brand Crunchy Crumb.", "en-IN"], ["Bengali", "আমার কুকিজ ব্র্যান্ডের জন্য একটা সিম্পল ওয়েবসাইট বানিয়ে দাও।", "bn-IN"], ["short", "वेबसাইट बना दो।".replace("বসাইট", "बसाइट"), "hi-IN"]] as const) {
  const c = mk(); const r = await c.respond(text, lang); console.log(`\n[${label}] YOU: ${text}\n   SHUBH [${c.lastMode}]: ${r}`);
  check(`${label}: a plain request starts work (no magic phrase, no extra yes)`, c.taskRequested && !c.sendRequested, `[${c.lastMode}]`);
  check(`${label}: Shubh does not ask permission or a question`, !/(भेज दूँ|send it\?|shall I|क्या मैं|পাঠাব\?|\?\s*$)/i.test(r.replace(/\bright\?/i, "")), `"${r.slice(0, 120)}"`);
  check(`${label}: he says it is starting (and calls when ready) but never that it is done`, !/(हो गया|done|finished|তৈরি হয়ে গেছে|ready है)/i.test(r.split(/[।.]/)[0] ?? ""), "");
}
{
  const c = mk(); await c.respond("मुझे एक वेबसाइट चाहिए जो कुकीज़ बेचती हो।", "hi-IN");
  check("before the wait ends, the task is pending", c.taskRequested);
  const r = await c.respond("रुको रुको, अभी मत भेजना।", "hi-IN"); console.log(`\nYOU: रुको रुको, अभी मत भेजना।\n   SHUBH: ${r}`);
  check("'wait, don't send' cancels the pending send", c.sendCancelled && !c.taskRequested);
}
{
  const c = mk(); const r = await c.respond("अच्छा ये बताओ, हमें अपनी वेबसाइट का प्राइस कितना रखना चाहिए?", "hi-IN");
  check("an ordinary question never starts work", !c.taskRequested && !c.sendRequested, `[${c.lastMode}]`);
}

// ---- E. the hand-off: whole-call summary + his words + 'you write the ZCode prompt' reach the agent; a follow-up reuses the session
const handoff = new Handoff(core, mem, "flex");
const job = handoff.sendFromBoard({ items: [], quotes: lines, today: "Mon 5 Oct", workspace: workspaceMap(), requirement: req, taskText: lines[0] })!;
check("a job is created from the summary even when the board has no task", !!job && /(cookie|website)/i.test(job.goal), job?.goal.slice(0, 80));
await job.handle.done; await new Promise((r) => setTimeout(r, 300));
const sent = JSON.parse(readFileSync(process.env.FAKE_CORE_LOG!, "utf8").trim().split("\n")[0]).text as string;
check("brief: REQUIREMENT first, his own words included, no invention", /REQUIREMENT[\s\S]*Goal:/.test(sent) && /HIS OWN WORDS[\s\S]*क्रंची क्रंब/.test(sent));
check("brief: the agent must write the ZCode prompt itself and do exactly what he asked", /YOU write the prompt, ZCode writes the code/.test(sent) && /Do exactly what he asked/.test(sent) && /EVERY point of the REQUIREMENT/.test(sent));
check("brief: standing rules still there", /Do NOT deploy/.test(sent));
const follow = handoff.sendFromBoard({ items: [], quotes: ["और एक कॉन्टैक्ट फॉर्म भी जोड़ दो।"], today: "Mon 5 Oct", requirement: { goal: "Add a contact form", details: ["contact form"], rules: [], unspecified: [] }, followUp: true, sessionId: job.sessionId })!;
await follow.handle.done; await new Promise((r) => setTimeout(r, 300));
const sent2 = JSON.parse(readFileSync(process.env.FAKE_CORE_LOG!, "utf8").trim().split("\n")[1]).text as string;
check("a follow-up is marked as a follow-up in the same project", /FOLLOW-UP in the SAME project/.test(sent2) && /contact form/.test(sent2));

core.close();
console.log(`\ncost: ~Rs 3.  ${fails ? fails + " FAILED" : "ALL PASSED"}`);
setTimeout(() => process.exit(fails ? 1 : 0), 1500);
