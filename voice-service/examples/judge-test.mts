import "../src/quiet.mts";
import { agreesToEnd, askedToEnd, messageKind } from "../src/judge.mts";
const k = process.env.SARVAM_API_KEY!;
const time = async <T,>(f: () => Promise<T>) => { const t = Date.now(); const r = await f(); return [r, Date.now() - t] as const; };
let ok = 0, bad = 0, ms: number[] = [];
const check = async (label: string, got: Promise<readonly [any, number]>, want: any) => { const [g, t] = await got; ms.push(t); const pass = g === want; pass ? ok++ : bad++; console.log(`${pass ? "ok  " : "FAIL"} ${label.padEnd(58)} -> ${g} (${t} ms)`); };
const Q = "क्या अभी call cut करूँ?";
console.log("--- he was asked 'shall I cut the call?'");
for (const [u, w] of [["ओके राखो", true], ["राखी।", true], ["ઓકે રાખો.", true], ["हाँ कर दो", true], ["হ্যাঁ রেখে দাও", true], ["okay bye", true], ["नहीं नहीं, अभी एक काम और है", false], ["रुको, पहले एक बात सुनो", false], ["না, আরেকটা কথা আছে", false]] as [string, boolean][])
  await check(`agree? "${u}"`, time(() => agreesToEnd(k, Q, u)), w);
console.log("--- did the model's [[END]] have a real basis?");
for (const [u, w] of [["हाँ सही है, तुम भेज दो", false], ["ठीक है, बस इतना ही। अब रखो कॉल, बाय।", true], ["okay, send it to the agent", false], ["আচ্ছা, তাহলে ফোনটা রাখো", true], ["ओके, अभी रख सकते हो तुम", true]] as [string, boolean][])
  await check(`asked to end? "${u}"`, time(() => askedToEnd(k, u)), w);
console.log("--- long message: tasks to remember, or something to answer?");
const LONG = "खबर तो बढ़िया है। एक प्रॉब्लम भी हो गया है। बकलो का जो सर्वर है ना वो क्रैश हो गया है। उसका डेटा रिकवरी कर लिया। राजपूत के साथ दो बजे एक मीटिंग है। लॉन्ड्री एप्लीकेशन को प्ले स्टोर में डालने के बाद छह बंदों की जरूरत है टेस्टिंग के लिए। सात बजे शुभम से पैसा लेना है, बीस हजार प्लस जीएसटी, तुम उनको कॉल करके बता देना।";
const Q2 = "कुछ छूटा नहीं है, बट एक बार मुझे थोड़ा समराइज़ करके बताओ कि वेबसाइट में बेसिकली होता क्या है? एक वेबसाइट मुझे एक बंदे को सेल्स स्पीच देना है। अब मैं सपोज़ तुम। सोचो तुम एक सेलर हो मतलब तुम शॉपलिंक का सेल्स टीम से हो और तुम मुझे वैसा ही सेल करोगे कैसे सेल करोगे?";
const R = "नहीं छूट तो बहुत कुछ गया है ना। मैंने बताया था बकलों का जो मीटिंग था, लॉन्ड्री का, प्ले स्टोर का, आज शाम में एक पेमेंट भी लेना है। तो तीनों का एक बता दो ना मुझे, एक बार रिपीट कर दो मेरा बात।";
await check("long update with tasks", time(() => messageKind(k, LONG)), "RECAP");
await check("sales-pitch role-play question (the misfire)", time(() => messageKind(k, Q2)), "ANSWER");
await check("'repeat all three for me'", time(() => messageKind(k, R)), "RECAP");
await check("long casual chat question", time(() => messageKind(k, "अच्छा सुनो, मुझे ये बताओ कि तुम्हें क्या लगता है, हमें अपनी वेबसाइट का प्राइस कितना रखना चाहिए, क्योंकि कॉम्पिटिटर्स बहुत सस्ते में दे रहे हैं और हमारे पास क्वालिटी ज्यादा है।")), "ANSWER");
console.log(`\n${ok} correct, ${bad} wrong; average ${Math.round(ms.reduce((a, b) => a + b, 0) / ms.length)} ms per decision`);
process.exit(0);
