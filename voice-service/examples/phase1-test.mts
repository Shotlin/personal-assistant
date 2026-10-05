// Offline end-to-end check of Phase 1 on the owner's real words: board + router + conflict + advice + recap-from-board.
import "../src/quiet.mts";
import { Conversation } from "../src/conversation.mts";
const apiKey = process.env.SARVAM_API_KEY!;
const noted: string[] = [];
const conv = new Conversation({ apiKey, speaker: "shubh", voice: { pushAudio: () => {}, clearAudio: () => {} }, onConflict: (c) => noted.push(...c.map((x) => x.note)) });
conv.history.push({ role: "assistant", content: "[excited] हैलो सायन! मैं शुभ बोल रहा हूँ। बताओ, क्या खबर है?" });
const A = "खबर तो बढ़िया है। एक प्रॉब्लम भी हो गया है। बकलो का जो सर्वर है ना वो क्रैश हो गया है। उसका डेटा रिकवरी कर लिया और अब स्टेबल है। राजपूत जो बकलो का ओनर है उनके साथ दो बजे एक मीटिंग है। लॉन्ड्री एप्लीकेशन को प्ले स्टोर में डालने के बाद छह बंदों की जरूरत है टेस्टिंग के लिए। और एक चीज़ है, 6-7 बजे शुभम से पैसा लेना है क्योंकि उनका वेबसाइट का काम स्टार्ट हो गया है बट उन्होंने पैसा सेंड नहीं किया। तुम उनको कॉल करके बता देना कि सर बीस हजार प्लस जीएसटी पेमेंट कर दीजिए, नहीं तो हम काम नहीं कर पाएंगे, एडवांस पेमेंट हमारा एसओपी है।";
const turns: [string, string][] = [
  ["1 long update", A],
  ["2 correction", "एक चीज़ बदलनी है, पेमेंट की बात सौमेन को बोलना, शुभम को नहीं। और पेमेंट सात बजे नहीं, आठ बजे।"],
  ["3 advice question", "अच्छा ये बताओ, हमें अपनी वेबसाइट का प्राइस कितना रखना चाहिए जब कॉम्पिटिटर्स सस्ते दे रहे हैं?"],
  ["4 plain chat", "ठीक है, समझ गया।"],
  ["5 repeat everything", "एक बार मेरी सारी बात रिपीट कर दो।"],
];
for (const [label, text] of turns) {
  noted.length = 0;
  const t = Date.now(); const r = await conv.respond(text, "hi-IN");
  await conv.board.updating;                       // let the background board finish before the next turn
  console.log(`\n=== ${label}  [mode ${conv.lastMode}, first sound ${conv.lastLatencyMs} ms, ${r.length} chars, board ${conv.board.ms} ms]\n${conv.history[conv.history.length - 1].content}`);
  if (conv.pendingConflicts.length) {
    const note = conv.takeConflictNote();
    console.log(`  >> BOARD NOTICED: ${note}`);
    const raised = await conv.raise(note, "hi-IN");
    console.log(`  >> SHUBH RAISES IT: ${conv.history[conv.history.length - 1].content}`);
  }
}
console.log(`\n--- final board ---\n${conv.board.toText()}\n\nvoice Rs ${conv.costInr().tts.toFixed(2)}, chat Rs ${conv.costInr().llm.toFixed(2)}`);
process.exit(0);
