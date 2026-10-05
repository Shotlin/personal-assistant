import "../src/quiet.mts";
import { routeTurn, type Route } from "../src/judge.mts";
const k = process.env.SARVAM_API_KEY!;
const cases: [string, Route, string?][] = [
  ["How are you?", "CHAT"], ["हाँ सही है, तुम भेज दो।", "CHAT", "ठीक है ना?"], ["ओके।", "CHAT"], ["कल सुबह दस बजे राजपूत के साथ मीटिंग रखनी है।", "CHAT"],
  ["तो एक नोट लिखो मुझे ग्रोसरी एप्लीकेशन में दिक्कत आ रहा है।", "CHAT"], ["আজ আমরা একটা নতুন ক্লায়েন্ট পেয়ে গেলাম!", "CHAT"],
  ["अच्छा ये बताओ, हमें अपनी वेबसाइट का प्राइस कितना रखना चाहिए?", "ADVICE"], ["यार मेरा सर्वर कल रात से डाउन है, क्या करूँ?", "ADVICE"],
  ["तुम एक सेलर बनो और मुझे शॉपलिंक बेचो, कैसे बेचोगे?", "ADVICE"], ["আমার কি আগে অ্যাপটা প্লে স্টোরে দেওয়া উচিত নাকি ওয়েবসাইট?", "ADVICE"],
  ["I think we should launch on Friday and skip testing, what do you think?", "ADVICE"],
  ["बकलो का सर्वर क्रैश हुआ, डेटा रिकवर हो गया। दो बजे राजपूत के साथ मीटिंग है। लॉन्ड्री ऐप के लिए छह टेस्टर चाहिए। सात बजे शुभम से बीस हजार लेना है, उसको कॉल करना।", "RECAP"],
  ["एक बार मेरी सारी बात रिपीट कर दो।", "RECAP"],
  // memory questions (Phase 2): about an earlier day or call -> RECALL; a normal question or a repeat on this call must not become RECALL
  ["कल मैंने लॉन्ड्री ऐप के बारे में तुमसे क्या कहा था, ज़रा याद करके बताओ?", "RECALL"], ["पिछली बार की कॉल में मैंने किसको फोन करने को बोला था?", "RECALL"],
  ["আমার কী কী কাজ এখনও বাকি আছে, আগের কল থেকে?", "RECALL"], ["What did I ask you yesterday about the refund flow, can you remind me?", "RECALL"],
  // technical / precise questions (Phase 3) -> EXPERT; opinions stay ADVICE
  ["nginx में 502 bad gateway आ रहा है, इसका मतलब क्या है और कैसे ठीक करूँ?", "EXPERT"], ["एक लाख पच्चीस हज़ार पर अठारह प्रतिशत GST कितना होगा, ठीक-ठीक बताओ?", "EXPERT"],
  ["हमारे ऐप का रिफंड कोड ठीक-ठीक क्या करता है, किस फाइल में है?", "EXPERT"],
  ["अच्छा ये बताओ, लॉन्ड्री ऐप को पहले प्ले स्टोर पर डालना चाहिए या वेबसाइट बनानी चाहिए?", "ADVICE"],
];
let ok = 0, ms = 0;
for (const [u, want, prev] of cases) { const t = Date.now(); const got = await routeTurn(k, u, prev); ms += Date.now() - t; if (got === want) ok++; console.log(`${got === want ? "ok  " : "FAIL"} ${want.padEnd(6)} got ${got.padEnd(6)} ${u.slice(0, 62)}`); }
console.log(`\n${ok}/${cases.length} correct, ${Math.round(ms / cases.length)} ms each`);
process.exit(0);
