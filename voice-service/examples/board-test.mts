// Offline: your real long messages -> does the board hold every item, exactly, and flag what is unclear?
import "../src/quiet.mts";
import { NotesBoard } from "../src/notes.mts";
const k = process.env.SARVAM_API_KEY!;
const A = "खबर तो बढ़िया है। एक प्रॉब्लम भी हो गया है। बकलो का जो सर्वर है ना वो क्रैश हो गया है। एंड उसका डेटा रिकवरी कर लिया और मैं उसको डेटा सर्वर भी स्टेबल हो गया अभी। बट राजपूत जो बकलो का ओनर है उनके साथ दो बजे एक मीटिंग है। एक लॉन्ड्री एप्लीकेशन का भी सुन लो। लॉन्ड्री एप्लीकेशन का क्या हुआ ना उनका प्ले स्टोर में डालने के बाद अभी छह बंदों का और जरूरत है टेस्टिंग वगैरह करने के लिए प्ले स्टोर में। और एक चीज़ है, 6-7 बजे एक शुभम से पैसा लेना है क्योंकि उनका वेबसाइट का स्टार्ट हो गया है, बट उन्होंने अभी तक पैसा सेंड नहीं किया। तुम एक बार कॉल कर देना, उनको कॉल करके बता देना, सर आप ₹20,000 प्लस जीएसटी आप पेमेंट कर दीजिए। आपका काम ऑलरेडी स्टार्ट हो गया नहीं तो हम लोग इसके बाद जाके काम नहीं कर पाएंगे प्रोजेक्ट के साथ क्योंकि एडवांस पेमेंट का रूल है हम लोग का एसओपी है।";
const B = "एक चीज़ बदलनी है, पेमेंट की बात सौमेन को बोलना, शुभम को नहीं। और पेमेंट सात बजे नहीं, आठ बजे।";
const C = "अच्छा सुनो, मुझे ये बताओ कि हमें अपनी वेबसाइट का प्राइस कितना रखना चाहिए?";
const b = new NotesBoard(k, "var/board-test.json");
for (const [name, text] of [["1: long update", A], ["2: corrections (who + time)", B], ["3: a question, not a task", C]] as const) {
  await b.update(text);
  console.log(`\n=== after ${name}  (${b.ms} ms)\n${b.toText()}\nconflicts: ${b.conflicts.length ? JSON.stringify(b.conflicts) : "none"}`);
}
process.exit(0);
