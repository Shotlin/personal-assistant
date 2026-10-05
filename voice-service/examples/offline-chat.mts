// Offline multi-turn check (no phone): playback is simulated in real time so the feeding/interrupt logic is exercised.
import "../src/quiet.mts";
import { Conversation } from "../src/conversation.mts";
const apiKey = process.env.SARVAM_API_KEY!;

// a fake phone line: audio plays out at real speed; pendingMs() = how much is still queued
function fakeLine() {
  let playUntil = 0, total = 0, peak = 0;
  return {
    voice: {
      pushAudio: (p: Float32Array) => { const now = Date.now(); playUntil = Math.max(playUntil, now) + (p.length / 16000) * 1000; total += p.length; for (const v of p) peak = Math.max(peak, Math.abs(v)); },
      clearAudio: () => { playUntil = 0; },
      pendingMs: () => Math.max(0, playUntil - Date.now()),
    },
    stats: () => ({ seconds: +(total / 16000).toFixed(1), peak: +peak.toFixed(2) }),
    reset: () => { total = 0; peak = 0; },
  };
}

const mode = process.argv[2] ?? "chat";
if (mode === "chat") {
  const line = fakeLine();
  const conv = new Conversation({ apiKey, voice: line.voice });
  const turns: [string, string][] = [
    ["আমার গ্রোসারি অ্যাপের রিফান্ড ফ্লোটা ঠিক করতে হবে।", "bn-IN"],
    ["হ্যাঁ, কাস্টমার অ্যাপ আর ড্যাশবোর্ড দুটোতেই।", "bn-IN"],
    ["तो हिंदी में बात कर सकते हो?", "hi-IN"],
    ["गुजराती आती है क्या?", "hi-IN"],
    ["ठीक है, तो उसमें UI मत बदलना, और टेस्ट ज़रूर चलाना।", "hi-IN"],
    ["हाँ, बिल्कुल सही।", "hi-IN"],
  ];
  for (const [t, l] of turns) {
    line.reset(); const before = conv.stats.ttsChars;
    const r = await conv.respond(t, l);
    console.log(`YOU : ${t}\nSANI: ${r}\n      [${conv.lastLatencyMs} ms to first sound | ${r.length} chars | ${line.stats().seconds}s of speech | peak ${line.stats().peak}]\n`);
  }
  const c = conv.costInr();
  console.log(`TOTAL: ${conv.stats.replies} replies, ${conv.stats.ttsChars} chars sent (avg ${Math.round(conv.stats.ttsChars / conv.stats.replies)}), voice Rs ${c.tts.toFixed(2)}, chat Rs ${c.llm.toFixed(3)} (${conv.stats.llmIn} in / ${conv.stats.llmOut} out tokens)`);
} else {
  // interruption test: a deliberately long reply (cap raised), cut off after 2 s of playback
  const long = "You are a talkative voice assistant. Answer in 4 to 5 full sentences.";
  for (const interrupt of [false, true]) {
    const line = fakeLine();
    const conv = new Conversation({ apiKey, voice: line.voice, systemPrompt: long, maxReplyChars: 600 });
    const p = conv.respond("Explain how a refund flow in a grocery app should work.", "en-IN");
    if (interrupt) { await new Promise((r) => setTimeout(r, 2200)); conv.bargeIn(); }
    const r = await p;
    console.log(`${interrupt ? "INTERRUPTED at 2.2 s" : "FULL REPLY           "}: ${conv.stats.ttsChars} chars sent to the voice (Rs ${conv.costInr().tts.toFixed(2)}), ${line.stats().seconds}s of audio generated | "${r.slice(0, 70)}..."`);
  }
}
process.exit(0);
