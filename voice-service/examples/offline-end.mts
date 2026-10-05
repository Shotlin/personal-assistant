import "../src/quiet.mts";
import { Conversation } from "../src/conversation.mts";
const apiKey = process.env.SARVAM_API_KEY!;
let spokenSamples = 0;
const conv = new Conversation({ apiKey, speaker: "shubh", pace: 0.88, voice: { pushAudio: (p) => { spokenSamples += p.length; }, clearAudio: () => {} } });
const turns: [string, string][] = [
  ["मुझे ग्रोसरी ऐप का रिफंड फ्लो ठीक करवाना है, और UI मत बदलना।", "hi-IN"],
  ["हाँ बिल्कुल यही चाहिए, तुम भेज दो।", "hi-IN"],
  ["ठीक है, बस इतना ही। अब रखो कॉल, बाय।", "hi-IN"],
  ["আচ্ছা, তাহলে ফোনটা রাখো।", "bn-IN"],
];
for (const [t, l] of turns) {
  conv.endRequested = false;
  const r = await conv.respond(t, l);
  console.log(`YOU : ${t}\nSANI: ${r}\n      endRequested=${conv.endRequested}  marker-leaked-into-speech=${r.includes("[[")}\n`);
}
process.exit(0);
