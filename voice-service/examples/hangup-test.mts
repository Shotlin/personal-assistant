// Offline: does Shubh hang up exactly when it should, and never when it should not?
import "../src/quiet.mts";
import { Conversation } from "../src/conversation.mts";
const apiKey = process.env.SARVAM_API_KEY!;
const mk = () => new Conversation({ apiKey, speaker: "shubh", voice: { pushAudio: () => {}, clearAudio: () => {} } });
async function scenario(title: string, turns: string[], expectEnd: boolean[]) {
  console.log(`\n=== ${title}`);
  const c = mk(); let good = true;
  for (let i = 0; i < turns.length; i++) {
    const r = await c.respond(turns[i], /[ঀ-৿]/.test(turns[i]) ? "bn-IN" : "hi-IN");
    const ok = c.endRequested === expectEnd[i]; good &&= ok;
    console.log(`${ok ? "ok  " : "FAIL"} YOU: ${turns[i]}\n         SHUBH: ${c.history[c.history.length - 1].content}\n         hang up now: ${c.endRequested} (wanted ${expectEnd[i]})`);
  }
  return good;
}
const results = [
  await scenario("A: 'that's all' -> offer -> garbled yes ('राखी') -> hangs up",
    ["मुझे कल सुबह दस बजे राजपूत के साथ मीटिंग रखनी है।", "बस इतना ही, और कुछ नहीं।", "राखी।"], [false, false, true]),
  await scenario("B: offer -> 'no, one more thing' -> stays connected",
    ["बस इतना ही था।", "नहीं नहीं, एक काम और है, लॉन्ड्री ऐप का टेस्टिंग।"], [false, false]),
  await scenario("C: 'send it' must NOT hang up",
    ["रिफंड फ्लो ठीक करना है, UI मत बदलना।", "हाँ सही है, तुम भेज दो।"], [false, false]),
  await scenario("D: direct goodbye in Bengali -> hangs up",
    ["আচ্ছা, আজকের জন্য এটুকুই, ফোনটা রাখো।"], [true]),
];
console.log(`\n${results.filter(Boolean).length} of ${results.length} scenarios passed`);
process.exit(0);
