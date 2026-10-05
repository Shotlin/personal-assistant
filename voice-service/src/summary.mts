// After the call: one short model call turns the conversation and the board into a summary that is worth remembering.
// If the model is unavailable the summary is built from the board in code, so a call is never lost.
import { screen } from "./secrets.mts";
import type { Item } from "./notes.mts";

export type CallSummary = { summary: string; decisions: string[]; topics: string[] };
const SCHEMA = { type: "object", additionalProperties: false, required: ["summary", "decisions", "topics"], properties: {
  summary: { type: "string" }, decisions: { type: "array", items: { type: "string" } }, topics: { type: "array", items: { type: "string" } } } } as const;

const SYSTEM = `You write the memory of a phone call between Sayan (the owner) and his voice assistant Shubh, so that Shubh can recall it in a later call.
You get the conversation (speech-to-text in Hindi, Bengali, English or a mix: words can be wrong, names are often mis-spelled) and the notes board.
Return:
- summary: 2 to 3 plain English sentences: what Sayan asked for, told, or decided, with the exact people, apps, projects, times and amounts. Do not invent anything that is not in the conversation. If nothing happened except a greeting, say so in one sentence.
- decisions: short English phrases for what was agreed or decided (empty list if none). A request to do something is not a decision unless it was confirmed.
- topics: 1 to 5 lowercase keywords (apps, people, projects) so the call can be found again.
Never include passwords, codes, API keys or tokens. Never write a file path: use only the folder or file name. Only record what Sayan himself said or decided on this call: not anything Shubh quoted from his memory or from the desktop assistant.`;

export function fallbackSummary(items: Item[], userTurns: number): CallSummary {
  if (!items.length) return { summary: userTurns ? "A short chat; nothing to remember." : "Sayan did not say anything on this call.", decisions: [], topics: [] };
  const topics = [...new Set(items.flatMap((i) => [i.project, i.who]).filter(Boolean).map((t) => t.toLowerCase()))].slice(0, 5);
  return { summary: "He told me: " + items.map((i) => i.what + (i.when ? ` (${i.when})` : "")).join("; ") + ".", decisions: items.filter((i) => i.kind === "decision").map((i) => i.what), topics };
}

export async function summarizeCall(apiKey: string, lines: { role: "user" | "assistant"; text: string }[], items: Item[]): Promise<CallSummary> {
  const userTurns = lines.filter((l) => l.role === "user").length;
  if (!userTurns) return fallbackSummary(items, 0);
  const convo = lines.map((l) => `${l.role === "user" ? "Sayan" : "Shubh"}: ${l.text}`).join("\n").slice(-7000);
  const board = items.length ? JSON.stringify(items.map(({ kind, what, who, when, amount, project, unclear }) => ({ kind, what, who, when, amount, project, unclear }))) : "[]";
  try {
    const res = await fetch("https://api.sarvam.ai/v1/chat/completions", {
      method: "POST",
      headers: { "api-subscription-key": apiKey, "Content-Type": "application/json" },
      body: JSON.stringify({ model: "sarvam-105b", reasoning_effort: null, temperature: 0.1, max_tokens: 700,
        messages: [{ role: "system", content: SYSTEM }, { role: "user", content: `CONVERSATION:\n${convo}\n\nNOTES BOARD:\n${board}\n\nWrite the memory.` }],
        response_format: { type: "json_schema", json_schema: { name: "call_memory", strict: true, schema: SCHEMA } } }),
      signal: AbortSignal.timeout(30_000),
    });
    if (!res.ok) throw new Error(`summary HTTP ${res.status}`);
    const j: any = await res.json();
    const out = JSON.parse(j.choices?.[0]?.message?.content ?? "");
    const clean = (s: unknown) => screen(String(s ?? "")).replace(/\s+/g, " ").trim();
    const summary = clean(out.summary);
    if (!summary) throw new Error("empty summary");
    return { summary: summary.slice(0, 700), decisions: (out.decisions ?? []).map(clean).filter(Boolean).slice(0, 8), topics: (out.topics ?? []).map((t: string) => clean(t).toLowerCase()).filter(Boolean).slice(0, 5) };
  } catch { return fallbackSummary(items, userTurns); }
}
