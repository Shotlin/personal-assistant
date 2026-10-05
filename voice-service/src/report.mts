// What Shubh says when HE phones the owner: a short spoken report of finished (or stuck) work, written by one small model call from the
// agent's own final answer, plus a WhatsApp text copy. The model may only use facts that are in the result; if it fails, a plain template is used.
import { MALE_VOICES } from "./conversation.mts";
import { NAME_SCRIPTS, characterName } from "./delivery.mts";
import { screen } from "./secrets.mts";

export type ReportKind = "done" | "blocked" | "failed" | "cancelled" | "progress";
export type Report = { kind: ReportKind; goal: string; result: string };

export const kindOf = (status: string): ReportKind => status === "done" ? "done" : status === "blocked" ? "blocked" : status === "cancelled" ? "cancelled" : "failed";

const clip = (s: string, n: number) => s.replace(/\s+/g, " ").trim().slice(0, n);

/** The line spoken first: who is calling and why (Hindi, correct gender). */
export function reportOpening(speaker: string, kind: ReportKind): string {
  const name = NAME_SCRIPTS[speaker]?.hi ?? characterName(speaker);
  const am = MALE_VOICES.has(speaker) ? "बोल रहा" : "बोल रही";
  if (kind === "progress") return `[warm] हैलो सायन! मैं ${name} ${am} हूँ। तुमने अपडेट के लिए कहा था।`;
  return kind === "done" ? `[excited] हैलो सायन! मैं ${name} ${am} हूँ। तुम्हारा काम हो गया है।` : `[calm] हैलो सायन! मैं ${name} ${am} हूँ। काम के बारे में एक बात है।`;
}

/** Plain template, used when the model is unavailable. Never invents anything beyond the agent's own words. */
export function fallbackReport(r: Report, speaker: string): string {
  const m = MALE_VOICES.has(speaker);
  const res = clip(r.result, 220);
  if (r.kind === "progress") return `[warm] ${res || "काम अभी चल रहा है।"} [warm] पूरा होते ही मैं दोबारा कॉल करूँगा।`;
  if (r.kind === "done") return `[warm] ${res} [warm] देख लो, कुछ बदलवाना हो तो बताओ।`;
  if (r.kind === "blocked") return `[calm] काम बीच में रुक गया है। ${res} [thinking] ${m ? "बताओ, क्या करूँ" : "बताओ, क्या करूँ"}?`;
  return `[soft] काम पूरा नहीं हो पाया। ${res} [thinking] दोबारा कोशिश करूँ?`;
}

export async function composeReport(apiKey: string, r: Report, speaker: string): Promise<string> {
  const male = MALE_VOICES.has(speaker);
  const system = `You write what a voice assistant says when it PHONES its owner Sayan to report on work its desktop agent did. Spoken Hindi in Devanagari, but English words (website, folder, button, page, deploy, brand, logo, price) in English letters. ${male ? "The assistant is a man: masculine forms about himself." : "The assistant is a woman: feminine forms about herself."} Informal "तुम".
Rules:
- 2 or 3 SHORT sentences, 260 characters at most in total. Start every sentence with ONE delivery tag from [warm] [excited] [calm] [soft] [thinking] [firm].
- Use ONLY facts that are in the RESULT. Never invent a file, a number, a feature or a link. Never read a long path or URL aloud: say the project folder name and where it is in a few words ("in sani_test, folder cookie-shop").
- progress: the work is STILL RUNNING. Say in plain words what stage it is at, from the PROGRESS NOTES (for example "ZCode is writing the page", "it has created index.html", "it just started") and how long it has been, and that you will call again when it is finished. Do not say it is done. Do not ask a question.
- done: say what exists now, and anything he must fill in (a placeholder). Then ONE question: ask him to have a look and say what to change.
- blocked: say what it needs from him (its question) as ONE specific question, nothing else.
- failed or cancelled: say plainly that it did not finish and why in a few words, then ask whether to try again.
- If the RESULT says something needs his yes (deploy, publish, money, messaging, deleting), say that clearly and ask for the yes.
- No lists, no markdown, no emoji.`;
  try {
    const res = await fetch("https://api.sarvam.ai/v1/chat/completions", {
      method: "POST", headers: { "api-subscription-key": apiKey, "Content-Type": "application/json" },
      body: JSON.stringify({ model: "sarvam-105b-conversations", reasoning_effort: null, temperature: 0.3, max_tokens: 260,
        messages: [{ role: "system", content: system }, { role: "user", content: `KIND: ${r.kind}\nTASK: ${clip(r.goal, 300)}\nRESULT / PROGRESS NOTES FROM THE AGENT: """${clip(screen(r.result), 1400)}"""\n\nWrite what to say.` }] }),
      signal: AbortSignal.timeout(12_000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = String(((await res.json()) as any).choices?.[0]?.message?.content ?? "").trim();
    if (text.length < 15) throw new Error("empty");
    return text.slice(0, 600);
  } catch { return fallbackReport(r, speaker); }
}

/** The WhatsApp text copy (to the owner only): what was sent, or what came back. Plain, readable, bounded. */
export function textCopy(kind: "sent" | ReportKind, goal: string, result = ""): string {
  const head = kind === "sent" ? "📤 Sent to your agent" : kind === "done" ? "✅ Your agent finished" : kind === "blocked" ? "⏸️ Your agent needs you" : kind === "cancelled" ? "⛔ Stopped" : "⚠️ Your agent could not finish";
  return screen(`${head}\n\nTask: ${clip(goal, 500)}${result ? `\n\n${result.trim().slice(0, 1400)}` : ""}\n\n— Shubh`).slice(0, 2000);
}
