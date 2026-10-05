import "../src/quiet.mts";
import { NotesBoard } from "../src/notes.mts";
const k = process.env.SARVAM_API_KEY!;
const A = "बकलो का जो सर्वर है ना वो क्रैश हो गया है। उसका डेटा रिकवरी कर लिया, अब स्टेबल है। राजपूत के साथ दो बजे एक मीटिंग है। लॉन्ड्री एप्लीकेशन को प्ले स्टोर में डालने के बाद छह बंदों की जरूरत है टेस्टिंग के लिए। 6-7 बजे शुभम से पैसा लेना है, ₹20,000 प्लस जीएसटी, उनको कॉल करके बता देना। एडवांस पेमेंट हमारा एसओपी है।";
for (const eff of [null, "low", "medium"] as const) {
  const b = new NotesBoard(k, "var/board-test.json", eff);
  await b.update(A);
  console.log(`\n=== reasoning ${eff ?? "off"}: ${b.ms} ms ${b.error ? "ERROR " + b.error : ""}\n${b.toText()}`);
}
process.exit(0);
