// Offline: your real long message from the last call -> does Sani now cover EVERY item?
import "../src/quiet.mts";
import { Conversation, chooseRoute } from "../src/conversation.mts";
const apiKey = process.env.SARVAM_API_KEY!;
const reasoning = (process.env.RECAP_REASONING || null) as "low" | null;
const LONG = "खबर तो बहुत बढ़िया है। एक सर्वर हम लोगों का क्या है ना बकलो का जो हम लोगों ने सर्वर बनाया था वो क्रैश हो गया है। एंड बट कोई दिक्कत नहीं है। हम लोगों ने पूरा डेटा रिकवर कर लिया है और दो बजे राजपूत के साथ मीटिंग है। एंड जो लॉन्ड्री एप्लीकेशन है, लॉन्ड्री एप्लीकेशन प्ले स्टोर में अपलोड होने के लिए और छह बंदों की जरूरत है क्योंकि क्या है ना टेस्टिंग वगैरह करना पड़ता है, तुम्हें तो पता ही होगा प्ले स्टोर में टेस्टिंग करना पड़ता है। तो वो चीज़ रहता है और 6 बजे एक काम करो और एक 7 बजे ना एक जो कल हम लोगों ने मीटिंग किया था और उन्होंने पैसा देने का बात किया था वो आज। सात बजे पेमेंट करने वाला है, उसका रिमाइंडर भी तुम सेट करके रखो क्योंकि मुझे भी कॉल करना होगा और तुम कॉल कर दो ना मेरे वी-ऐप से। मैं ना कॉल करके तुम कॉल कर दो और उनको बोलो आज पैसा पेमेंट करें नहीं तो क्या है ना। हम लोग काम स्टार्ट नहीं करेंगे फिर क्योंकि ये दो दिन लेट हो गया ऑलरेडी हम लोगों ने काम स्टार्ट कर दिया उन्होंने अभी तक पेमेंट नहीं किया। तो तुम्हें ये चीज़ उनको बता देना चाहिए।";
const AGAIN = "नहीं छूट तो बहुत कुछ गया है ना। मैंने बताया था बकलों का जो मीटिंग था वो बताया, लॉन्ड्री का, प्ले स्टोर का वो बताया। आज शाम में एक पेमेंट भी लेना है वो भी बताया। तो तीनों का एक बता दो ना मुझे, एक बार रिपीट कर दो मेरा बात।";
console.log("mode(long update) =", await chooseRoute(apiKey, LONG), "| mode('हाँ सही है') =", await chooseRoute(apiKey, "हाँ सही है"), "| mode(repeat request) =", await chooseRoute(apiKey, AGAIN));
const conv = new Conversation({ apiKey, speaker: "shubh", recapReasoning: reasoning, voice: { pushAudio: () => {}, clearAudio: () => {} } });
conv.history.push({ role: "assistant", content: "[excited] हैलो सायन! मैं शुभ बोल रहा हूँ। बताओ, क्या खबर है?" });
for (const [label, text] of [["LONG UPDATE", LONG], ["REPEAT REQUEST", AGAIN]] as const) {
  const t = Date.now();
  const r = await conv.respond(text, "hi-IN");
  console.log(`\n=== ${label} (reasoning: ${reasoning ?? "off"}) ===\n${conv.history[conv.history.length - 1].content}\n(${r.length} chars, first sound ${conv.lastLatencyMs} ms, total ${Date.now() - t} ms)`);
}
console.log(`\nvoice chars ${conv.stats.ttsChars} = Rs ${conv.costInr().tts.toFixed(2)}`);
process.exit(0);
