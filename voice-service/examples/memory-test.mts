// Offline end-to-end check of Phase 2 on the owner's real sentences. Uses a TEMPORARY memory file and a TEMPORARY copy of the
// Sani folder layout (var/test-memory/), so nothing real is touched. Costs ~Rs 2 (a few spoken replies + board/summary calls).
//   npm run memory-test            run everything
//   npm run memory-test -- real    also print the context built from the REAL Sani folder (read-only)
import "../src/quiet.mts";
import { DatabaseSync } from "node:sqlite";
import { mkdirSync, rmSync } from "node:fs";
import { Conversation } from "../src/conversation.mts";
import { VoiceMemory } from "../src/memory.mts";
import { buildContext, voiceCallsMarkdown, fixHeard } from "../src/context.mts";
import { rememberCall } from "../src/callMemory.mts";
import { loadSaniContext, mirrorToSani, SANI_NAMESPACE } from "../src/sani.mts";
import { containsSecret } from "../src/secrets.mts";

const apiKey = process.env.SARVAM_API_KEY!;
const DIR = "var/test-memory";
rmSync(DIR, { recursive: true, force: true }); mkdirSync(DIR, { recursive: true });
let fails = 0;
const check = (name: string, ok: boolean, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  " + extra : ""}`); if (!ok) fails++; };

// a stand-in Sani folder: the same two files, the same table layouts
const sani = new DatabaseSync(`${DIR}/sani.db`);
sani.exec("CREATE TABLE store_items (namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (namespace, key))");
const now = new Date().toISOString();
sani.prepare("INSERT INTO store_items VALUES (?,?,?,?,?)").run(SANI_NAMESPACE, "/profile.md", JSON.stringify({ content: "Sayan runs a small software company. Projects: Baklo (grocery/POS), a laundry app, client websites. Prefers short answers. api_key = sk_live_abcdefghijklmnop1234", encoding: "utf-8", created_at: now, modified_at: now }), now, now);
sani.close();
const hist = new DatabaseSync(`${DIR}/sani-history.db`);
hist.exec("CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)");
hist.prepare("INSERT INTO conversations VALUES (?,?,?,?)").run("c1", "Use ZCode in sani_test to build the refund page", Date.now() - 3 * 86400000, Date.now() - 3 * 86400000);
hist.prepare("INSERT INTO conversations VALUES (?,?,?,?)").run("c2", "New conversation", Date.now(), Date.now());
hist.close();

check("US system is repaired to POS system only when a POS is known", fixHeard("लॉन्ड्री का यूएस वाला सिस्टम ठीक करो, the US system", "Laundry POS, Baklo") === "लॉन्ड्री का POS system ठीक करो, the POS system" && fixHeard("the US system", "Baklo") === "the US system");

// ---- 1. YESTERDAY'S CALL (his real sentences), stored the way the live call stores it
const mem = new VoiceMemory(`${DIR}/voice.db`);
const yesterday = Date.now() - 26 * 3600_000;
const call1 = new Conversation({ apiKey, speaker: "shubh", voice: { pushAudio() {}, clearAudio() {} } });
const said1 = [
  "खबर तो एक बुरी खबर भी है एक अच्छी खबर भी है। पहला वो है ना बकालो का जो सर्वर है ना क्रैश हो गया है। बट हमने उसका डेटा रिकवरी कर लिया है। अभी तो लाइव चल रहा है। और एक चीज़ है ना लॉन्ड्री का जो POS सिस्टम था ना उसमें कुछ दिक्कत आ गया उनका GST भी सही से नहीं निकल रहा और कुछ फॉर्म वगैरह भी नहीं निकल रहा। तो इसको थोड़ा नोट वगैरह करिएगा।",
  "राजपूत जो बकलो का ओनर है उनके साथ दो बजे एक मीटिंग है। लॉन्ड्री एप्लीकेशन को प्ले स्टोर में डालने के बाद छह बंदों की जरूरत है टेस्टिंग के लिए। और छह से सात बजे शुभम से पैसा लेना है क्योंकि उनका वेबसाइट का काम स्टार्ट हो गया है बट उन्होंने पैसा सेंड नहीं किया। तुम उनको कॉल करके बता देना कि सर बीस हजार प्लस जीएसटी पेमेंट कर दीजिए, नहीं तो हम काम नहीं कर पाएंगे, एडवांस पेमेंट हमारा एसओपी है। मेरा पासवर्ड है 48215 वो भी याद रखना।",
];
for (const t of said1) { call1.history.push({ role: "user", content: t }); call1.history.push({ role: "assistant", content: "[warm] ठीक है, समझ गया।" }); await call1.board.update(t); }
console.log(`yesterday's board: ${call1.board.items.length} items (${call1.board.ms} ms)`);
console.log(await rememberCall({ apiKey, mem, conv: call1, startedAt: yesterday, endedAt: yesterday + 7 * 60_000, language: "hi-IN", mirror: false }));
const stored = mem.recentCalls();
check("call stored", stored.length === 1 && stored[0].summary.length > 20);
check("summary names the key facts", /laundry|लॉन्ड्री/i.test(stored[0].summary) && /baklo|bakalo|बक/i.test(stored[0].summary), `"${stored[0].summary.slice(0, 160)}"`);
check("items stored", mem.openItems(Date.now(), 50).length >= 4, `${mem.openItems(Date.now(), 50).length} open`);
const dump = JSON.stringify([stored, mem.openItems(Date.now(), 50)]);
check("password is NOT stored", !/48215/.test(dump) && !containsSecret(dump));

// ---- 2. TODAY'S CALL: the context, then questions about yesterday
const ctx = buildContext(mem, { sani: loadSaniContext(DIR) });
console.log("\n--- BACKGROUND GIVEN TO SHUBH ---\n" + ctx + `\n--- (${ctx.length} chars) ---\n`);
check("context has yesterday", /yesterday/.test(ctx) && /EARLIER PHONE CALLS/.test(ctx));
check("context has the open items", /STILL OPEN/.test(ctx));
check("context has Sani's profile", /software company/.test(ctx) && /Sani KNOWS|SANI KNOWS/i.test(ctx));
check("Sani secret removed", !/sk_live/.test(ctx) && !/abcdefghijklmnop/.test(ctx));
check("Sani chat title shown, empty one skipped", /refund page/.test(ctx) && !/New conversation/i.test(ctx));
check("context is bounded", ctx.length <= 3600);

const line = (() => { let until = 0; return { pushAudio: (p: Float32Array) => { until = Math.max(until, Date.now()) + (p.length / 16000) * 1000; }, clearAudio() { until = 0; }, pendingMs: () => Math.max(0, until - Date.now()) }; })();
const conv = new Conversation({ apiKey, speaker: "shubh", voice: line, context: ctx });
conv.history.push({ role: "assistant", content: "[excited] हैलो सायन! मैं शुभ बोल रहा हूँ। बताओ, क्या खबर है?" });
const ask = async (label: string, text: string, lang: string, expect: RegExp[], forbid: RegExp[] = [], mode?: string) => {
  const r = await conv.respond(text, lang); await conv.board.updating;
  console.log(`\n[${conv.lastMode}] YOU: ${text}\n        SHUBH: ${r}`);
  check(label, expect.every((e) => e.test(r)) && !forbid.some((f) => f.test(r)) && (!mode || conv.lastMode === mode), `(${conv.lastMode}, ${r.length} chars)`);
};
await ask("recalls the laundry request", "कल मैंने लॉन्ड्री ऐप के बारे में क्या बोला था?", "hi-IN", [/(6|छह|छः|six)/i, /(टेस्ट|test)/i], [], "recall");
await ask("lists what is pending", "और बाकी क्या पेंडिंग है मेरा?", "hi-IN", [/(राजपूत|Rajput)/i, /(शुभम|Shubham)/i, /(6|छह|छः|six)/i], [], "recall");
await ask("who to call", "पिछली बार मैंने किसको कॉल करने को बोला था?", "hi-IN", [/(शुभम|Shubham)/i], [/(सौमेन|Soumen)/i], "recall");
await ask("does NOT invent what it was never told", "कल मैंने होटल बुकिंग के बारे में क्या बोला था?", "hi-IN", [/(याद नहीं|नहीं पता|ध्यान नहीं|नहीं बताया|no record|remember|नहीं बोला|नहीं कहा|कोई|नहीं मिला)/i], [/(मुंबई|दिल्ली|Goa|गोवा|तारीख)/i]);
await ask("Bengali recall, answers in Bengali", "কাল আমি বকলো সার্ভার নিয়ে কী বলেছিলাম?", "bn-IN", [/[ঀ-৿]/, /(ক্র্যাশ|crash|রিকভার|recover|লাইভ|live)/i]);
await ask("small talk does not recite memory", "हाय, कैसे हो तुम?", "hi-IN", [], [/(राजपूत|Rajput|शुभम|Shubham|लॉन्ड्री|laundry|POS|बकलो|Baklo)/i]);
check("system prompt never says notes/memory to the user", !/notes board/i.test(conv.history.filter((m) => m.role === "assistant").map((m) => m.content).join(" ")));

// ---- 3. Call 2 -> stored; the item he restated replaces yesterday's; mirror file in the real Deep Agent format
const call2 = new Conversation({ apiKey, speaker: "shubh", voice: { pushAudio() {}, clearAudio() {} } });
const t2 = "राजपूत के साथ मीटिंग अब दो बजे नहीं, चार बजे होगी।";
call2.history.push({ role: "user", content: t2 }, { role: "assistant", content: "[firm] ठीक है, चार बजे।" }); await call2.board.update(t2);
console.log("\n" + await rememberCall({ apiKey, mem, conv: call2, startedAt: Date.now() - 60_000, endedAt: Date.now(), language: "hi-IN", mirror: false }));
const open = mem.openItems(Date.now() + 1000, 50);
const meetings = open.filter((i) => /rajput|राजपूत/i.test(i.what + i.who));
check("only one Rajput meeting stays open (the new one)", meetings.length === 1 && /4|चार/.test(meetings[0].when + meetings[0].what), meetings.map((m) => `${m.what} @ ${m.when}`).join(" / "));
const md = voiceCallsMarkdown(mem);
check("Deep Agent file written", /Phone calls with Sayan/.test(md) && /yesterday/.test(md), `${md.length} chars`);
const why = mirrorToSani(md, DIR);
check("mirror wrote to the Sani memory table", why === "", why);
const back = new DatabaseSync(`${DIR}/sani.db`).prepare("SELECT key, value FROM store_items WHERE key = '/voice-calls.md'").get() as any;
check("mirror row has the deepagents format", !!back && JSON.parse(back.value).encoding === "utf-8" && /Phone calls/.test(JSON.parse(back.value).content));
check("mirror refuses a secret", mirrorToSani("my api_key = sk_live_abcdefghijklmnop1234", DIR) !== "");

if (process.argv[2] === "real") {
  const real = loadSaniContext();
  console.log(`\nREAL Sani folder (read-only): ${real.files.length} memory files, ${real.chats.length} recent chats, problems: ${real.problems.join("; ") || "none"}`);
  for (const c of real.chats) console.log(`  chat: ${c.title}`);
}
console.log(`\ncost: voice Rs ${conv.costInr().tts.toFixed(2)}, chat Rs ${conv.costInr().llm.toFixed(2)}.  ${fails ? fails + " FAILED" : "ALL PASSED"}`);
process.exit(fails ? 1 : 0);
