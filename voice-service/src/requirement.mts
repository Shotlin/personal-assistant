// The summary Shubh sends the Deep Agent: what he wants, understood from the WHOLE call (not one sentence), in plain English, with his exact names and words.
// One small model call; the Deep Agent (a stronger model) still gets his own words next to it, so a mistake here cannot silently replace what he said.
import { screen } from "./secrets.mts";
import type { Item } from "./notes.mts";

export type Requirement = { goal: string; details: string[]; rules: string[]; unspecified: string[] };
const SCHEMA = { type: "object", additionalProperties: false, required: ["goal", "details", "rules", "unspecified"], properties: {
  goal: { type: "string" }, details: { type: "array", items: { type: "string" } }, rules: { type: "array", items: { type: "string" } }, unspecified: { type: "array", items: { type: "string" } } } } as const;

const SYSTEM = `You write the requirement that a voice assistant hands to a stronger coding agent, from what the owner (Sayan) said on a phone call. His words are speech-to-text (Hindi, Bengali, English, mixed; some words are mis-heard; names are spelled by sound).
Return JSON:
- goal: ONE English sentence: what he wants built / done.
- details: every distinct thing he asked for or described (what, for whom, content, style, sections, language, features), one short English line each, keeping his exact names, brand words and numbers. Do not add anything he did not say.
- rules: his constraints and "do not" instructions (for example "do not change the UI"), exactly.
- unspecified: the things a builder would normally need that he did NOT say (for example brand name, colours, prices, logo), as short English phrases, so the agent knows to choose defaults or use placeholders.
Use ONLY what is in WHAT HE SAID (and the notes board of this same call). Never bring in a name, project or topic from anywhere else: no other customers, apps or earlier jobs. Never invent a brand name, price, person or feature. Greetings and small talk are not requirements. A request to call him back is not a requirement. "Deep Agent" / "the agent" is software, not a person. If a word makes no sense in context (mis-heard), leave it out.`;

export async function summarizeRequirement(apiKey: string, userLines: string[], board: Item[]): Promise<Requirement | null> {
  const said = userLines.map((l) => l.replace(/\s+/g, " ").trim()).filter(Boolean).join("\n").slice(-5000);
  if (!said) return null;
  const notes = board.length ? `\nNOTES BOARD (already filed from the same call, may be incomplete):\n${JSON.stringify(board.map(({ kind, what, who, when, project }) => ({ kind, what, who, when, project })))}` : "";
  try {
    const res = await fetch("https://api.sarvam.ai/v1/chat/completions", {
      method: "POST", headers: { "api-subscription-key": apiKey, "Content-Type": "application/json" },
      body: JSON.stringify({ model: "sarvam-105b", reasoning_effort: null, temperature: 0.1, max_tokens: 900,
        messages: [{ role: "system", content: SYSTEM }, { role: "user", content: `WHAT HE SAID (in order):\n${said}${notes}\n\nWrite the requirement.` }],
        response_format: { type: "json_schema", json_schema: { name: "requirement", strict: true, schema: SCHEMA } } }),
      signal: AbortSignal.timeout(14_000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const out = JSON.parse(((await res.json()) as any).choices?.[0]?.message?.content ?? "{}");
    const clean = (a: unknown) => (Array.isArray(a) ? a : []).map((x) => screen(String(x)).replace(/\s+/g, " ").trim()).filter(Boolean).slice(0, 14);
    const goal = screen(String(out.goal ?? "")).replace(/\s+/g, " ").trim();
    if (!goal) return null;
    return { goal: goal.slice(0, 300), details: clean(out.details), rules: clean(out.rules), unspecified: clean(out.unspecified) };
  } catch { return null; }
}
