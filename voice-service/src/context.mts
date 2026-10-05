// The private background Shubh gets at the start of a call: what he remembers from earlier calls, what is still open, and what
// the Sani desktop app knows. Built in code (no model), bounded in size, and always secret-screened.
import { ACTIONABLE, type CallRecord, type StoredItem, type VoiceMemory } from "./memory.mts";
import { loadSaniContext, type SaniContext } from "./sani.mts";
import { screen } from "./secrets.mts";

const DAY = 86_400_000;
const startOfDay = (t: number) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };
const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "today", "yesterday", "3 days ago (Fri 2 Oct)": the model must not have to do date arithmetic. */
export function dayLabel(ts: number, now = Date.now()): string {
  const n = Math.round((startOfDay(now) - startOfDay(ts)) / DAY);
  const d = new Date(ts);
  const date = `${WEEKDAY[d.getDay()]} ${d.getDate()} ${MONTH[d.getMonth()]}`;
  return n <= 0 ? `today (${date})` : n === 1 ? `yesterday (${date})` : `${n} days ago (${date})`;
}
const clock = (t: number) => { const d = new Date(t); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };

const itemLine = (i: StoredItem, now: number) =>
  `- [${i.kind}] ${i.what}${i.who ? ` | who: ${i.who}` : ""}${i.when ? ` | when: ${i.when}` : ""}${i.amount ? ` | amount: ${i.amount}` : ""}${i.project ? ` | project: ${i.project}` : ""}${i.unclear ? ` | UNCLEAR: ${i.unclear}` : ""} (said ${dayLabel(i.createdAt, now)})`;
const callLine = (c: CallRecord, now: number) => `- ${dayLabel(c.startedAt, now)}, ${Math.max(1, Math.round(c.durationS / 60))} min: ${c.summary}${c.decisions.length ? ` Decided: ${c.decisions.join("; ")}.` : ""}`;

export type ContextOptions = { now?: number; maxChars?: number; sani?: SaniContext | null; maxCalls?: number; maxOpen?: number };
/** The background text (without the instructions), or "" when there is nothing to remember yet. Least important parts are dropped first if it is too long. */
export function buildContext(mem: VoiceMemory | null, o: ContextOptions = {}): string {
  const now = o.now ?? Date.now();
  const sani = o.sani === undefined ? loadSaniContext() : o.sani;
  const calls = mem ? mem.recentCalls(o.maxCalls ?? 8, now - 30 * DAY) : [];
  const open = mem ? mem.openItems(now, o.maxOpen ?? 14).filter((i) => ACTIONABLE.has(i.kind)) : [];
  const facts = mem ? mem.openItems(now, 12).filter((i) => !ACTIONABLE.has(i.kind)) : [];

  const sections: { title: string; lines: string[] }[] = [
    { title: "EARLIER PHONE CALLS WITH SAYAN (newest first)", lines: calls.map((c) => callLine(c, now)) },
    { title: "STILL OPEN from earlier calls (not done, as far as I know)", lines: open.map((i) => itemLine(i, now)) },
    { title: "WORK I HANDED TO HIS DESKTOP AGENT (newest first)", lines: (mem ? mem.recentJobs(4, now - 14 * DAY) : []).map((j) => `- ${dayLabel(j.createdAt, now)}: ${j.goal} => ${j.status}${j.result ? `: ${j.result.replace(/\s+/g, " ").slice(0, 260)}` : j.progress ? ` (${j.progress})` : ""}`) },
    { title: "RULES AND FACTS he told me before", lines: facts.map((i) => itemLine(i, now)) },
    { title: "WHAT HIS DESKTOP ASSISTANT SANI KNOWS (its saved notes)", lines: (sani?.files ?? []).map((f) => `- ${f.name}: ${f.text.replace(/\s+/g, " ")}`) },
    { title: "HIS RECENT REQUESTS TO THE DESKTOP ASSISTANT", lines: (sani?.chats ?? []).map((c) => `- ${dayLabel(c.at, now)}: ${c.title}`) },
  ];
  const render = () => sections.filter((s) => s.lines.length).map((s) => `${s.title}:\n${s.lines.join("\n")}`).join("\n\n");
  const max = o.maxChars ?? 3600;
  // drop the least important lines first: desktop chats, then desktop notes, then old facts, then the oldest calls/items
  for (const idx of [5, 4, 3, 2, 1, 0]) while (render().length > max && sections[idx].lines.length > 1) sections[idx].lines.pop();
  let text = render();
  if (text.length > max) text = text.slice(0, max);
  return screen(text);
}

/** The system prompt plus the background and the rules for using it. */
export function withBackground(prompt: string, background: string, now = Date.now()): string {
  if (!background.trim()) return prompt;
  const d = new Date(now);
  return `${prompt}

YOUR MEMORY (private background, from earlier calls and his desktop assistant; today is ${WEEKDAY[d.getDay()]} ${d.getDate()} ${MONTH[d.getMonth()]} ${d.getFullYear()}, ${clock(now)}):
${background}

HOW TO USE YOUR MEMORY:
- It is background only. Never read it out, never mention that you have "notes" or "memory", and never bring it up on your own, except one short heads-up if what he says now clearly clashes with it.
- When he asks about something earlier ("what did I say yesterday about...", "what is pending", "last time"), answer from it in one or two short sentences with the exact names, apps, times and amounts, and say when he said it ("kal", "পরশু", "on Friday") the way a person would.
- If the answer is not in it, say simply that you do not remember that. NEVER guess or invent something he supposedly said.
- "HIS RECENT REQUESTS TO THE DESKTOP ASSISTANT" are things he TYPED on the computer, not things he said to you on the phone. Never say "you told me" about them; mention one only if he asks about his desktop assistant, and say "on the desktop assistant".
- Never read a file path or URL aloud: say only the folder or file name.
- If it holds an item that is still open and fits what he is saying now, you may connect the two in a few words (for example "that is the one from yesterday").`;
}

/** The markdown file for the Deep Agent (/memories/voice-calls.md): what was said on the phone, newest first, plus what is open. */
export function voiceCallsMarkdown(mem: VoiceMemory, now = Date.now()): string {
  const calls = mem.recentCalls(10, now - 60 * DAY);
  const open = mem.openItems(now + 1000, 25).filter((i) => ACTIONABLE.has(i.kind));
  const parts = ["# Phone calls with Sayan (Shubh, the voice assistant)", "", "Written by the voice service after each call. Newest first. Spoken Hindi/Bengali/English, summarised in English.", ""];
  for (const c of calls) parts.push(`## ${dayLabel(c.startedAt, now)} ${clock(c.startedAt)}, ${Math.max(1, Math.round(c.durationS / 60))} min`, c.summary, ...(c.decisions.length ? ["Decisions: " + c.decisions.join("; ")] : []), "");
  const jobs = mem.recentJobs(6, now - 30 * DAY);
  if (jobs.length) parts.push("## Work handed to you from phone calls", ...jobs.map((j) => `- ${dayLabel(j.createdAt, now)}: ${j.goal} => ${j.status}${j.result ? `: ${j.result.replace(/\s+/g, " ").slice(0, 400)}` : ""}`), "");
  if (open.length) parts.push("## Still open from phone calls", ...open.map((i) => itemLine(i, now)), "");
  return screen(parts.join("\n")).slice(0, 12_000);
}

/** Project and person names Sayan has used in earlier calls, for the board: the speech recogniser mishears them ("POS" -> "US"). */
export function knownNames(mem: VoiceMemory, now = Date.now()): string {
  const names = new Set<string>();
  for (const i of mem.openItems(now, 60)) { if (i.project) names.add(i.project); if (i.who) names.add(i.who); for (const w of i.what.match(/\b[A-Z][A-Za-z]*(?: (?:POS|app|system|server)\b)?/g) ?? []) if (w.length > 2 && !/^(The|Sayan|Shubh|Need|Collect|Call|Meeting|Do|Solve|Laundry's)$/.test(w)) names.add(w); }
  for (const c of mem.recentCalls(8, now - 30 * DAY)) for (const t of c.topics) names.add(t);
  return [...names].slice(0, 30).join(", ");
}

/** Repairs speech-recogniser mishearings of names he has used before, in code (the small model does not do it reliably).
 *  Only when the name is in his memory: "US system" / "यूएस सिस्टम" -> "POS system" if a POS is known. */
export function fixHeard(text: string, known: string): string {
  let t = text;
  if (/\bPOS\b/i.test(known)) t = t.replace(/(?<![\p{L}\p{N}])(US|U\.S\.?|यूएस|यू एस|ऊएस)\s+(वाला\s+)?(system|सिस्टम)(?![\p{L}\p{N}])/giu, "POS system");
  return t;
}
