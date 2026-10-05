// Mid-call "ask the expert": one question to the Deep Agent while Shubh covers the wait. Returns the answer, or null when it takes
// too long or fails (Shubh then says he will check, and the question is kept as an open item instead of a guess).
import type { SaniCore } from "./core.mts";
import { buildQuestion } from "./brief.mts";
import type { Item } from "./notes.mts";

const LANG = { "hi-IN": "Hindi (Devanagari; English words in English letters)", "bn-IN": "Bengali (Bengali script; English words in English letters)", "en-IN": "English" } as Record<string, string>;

export function makeExpert(core: SaniCore, o: { callId: string; items: () => Item[]; timeoutMs?: number; log?: (m: string) => void }) {
  return async (question: string, lang: string): Promise<string | null> => {
    const text = buildQuestion(question, { language: LANG[lang] ?? "the language he speaks", constraints: o.items().filter((i) => i.kind === "constraint") });
    const h = core.startRun(text, { agent: "deep", threadId: `voice-${o.callId}-expert` });
    const t0 = Date.now();
    const winner = await Promise.race([h.done, new Promise<null>((r) => setTimeout(() => r(null), o.timeoutMs ?? 22_000))]);
    if (!winner) { void h.cancel(); o.log?.(`expert: no answer in ${((Date.now() - t0) / 1000).toFixed(0)} s, cancelled`); return null; }
    if (winner.status !== "done" || !winner.response.trim()) { o.log?.(`expert: ${winner.status}: ${winner.response.slice(0, 120)}`); return null; }
    o.log?.(`expert: answered in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
    return winner.response.trim().slice(0, 1200);
  };
}
