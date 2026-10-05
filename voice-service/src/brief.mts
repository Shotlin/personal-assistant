// The brief: what the Deep Agent receives from a phone call. Built in code from the notes board (not from the chat), so nothing is
// merged, dropped or invented. It states the goal, the context, his constraints, the owner's standing rules and what to reply.
import type { Item } from "./notes.mts";
import { screen } from "./secrets.mts";
import type { Requirement } from "./requirement.mts";

/** Kinds that are work for the agent. The rest (meetings, payments, reminders, facts) are context for it, or for him. */
export const WORK_KINDS = new Set(["task", "call_request"]);
export const hasWork = (items: Item[]): boolean => items.some((i) => i.kind === "task");

const line = (i: Item) => `- [${i.kind}] ${i.what}${i.who ? ` | who: ${i.who}` : ""}${i.when ? ` | when: ${i.when}` : ""}${i.amount ? ` | amount: ${i.amount}` : ""}${i.project ? ` | project: ${i.project}` : ""}${i.unclear ? ` | UNCLEAR: ${i.unclear}` : ""}`;

/** The owner's standing rules (decision 2, 2026-10-05): these actions ALWAYS need his spoken yes, so the agent proposes and stops. */
export const OWNER_RULES = `STANDING RULES FROM THE OWNER (never break these, even if a task seems to ask for it):
- Do NOT deploy or publish anything (Play Store, servers, websites, packages, git push).
- Do NOT spend money or start anything that costs money (payments, purchases, subscriptions, ads).
- Do NOT message, email or call anyone, and do not draft-and-send. You may prepare the text and name the person; he will approve each one by voice.
- Do NOT delete anything (files, data, messages, branches). Prefer reversible changes.
- You are working from a phone call: no computer control of the screen. Code edits only inside the folders you are allowed to use; ask for nothing outside them.
- If a step would break a rule, stop there and say exactly what you need his yes for.`;

/** How the Deep Agent should behave on work that comes from a phone call (nobody can answer it mid-run). */
export const WORKING_STYLE = `HOW TO WORK (nobody can answer you while you work: he is on a phone, away from the screen):
- Decide everything yourself. Colours, layout, wording, file names and technical choices are yours: pick sensible, modern defaults and say in the final reply what you chose.
- Find things yourself before you ask: the WORKSPACE section lists the folders you may use and what is in them. Read the files there instead of asking where something is. If the coding tool (ZCode) asks you a question, answer it yourself from this brief or from good engineering defaults.
- Ask him ONLY for things that are truly his and that you cannot find: his brand NAME, logo, real prices or contact details when they are not in this brief or in your saved notes. Do not stop for them: finish with a clearly marked placeholder and list exactly what to fill in.
- New work goes in a NEW folder inside an allowed folder (one project = one folder, short lowercase name). Never touch other projects.
- Do exactly what he asked: nothing less, and nothing more than he asked (defaults only where he said nothing). Do NOT write the code yourself in chat: YOU write the prompt, ZCode writes the code. Use your ZCode coding tool and give it ONE complete, detailed request that contains EVERY point of the REQUIREMENT (goal, content, sections, style, language, his rules, "a static site is fine, no build step" when it fits), then check the files it wrote. At most one corrected follow-up.
- Be efficient: every tool call and token costs real money. No extra screenshots, no re-reading unchanged files, no long write-ups.`;

export type BriefInput = { items: Item[]; quotes: string[]; today: string; callId: string; language?: string; question?: string; open?: string; workspace?: string; remembered?: string; requirement?: Requirement | null; followUp?: boolean };
export function buildBrief(i: BriefInput): string {
  const todo = i.items.filter((x) => x.kind === "task");
  const constraints = i.items.filter((x) => x.kind === "constraint");
  const context = i.items.filter((x) => x.kind !== "task" && x.kind !== "constraint");
  const unclear = i.items.filter((x) => x.unclear);
  const parts = [
    `VOICE HAND-OFF${i.followUp ? " (A FOLLOW-UP in the SAME project and session: change or add to what you already built, do not start over)" : ""}. Sayan spoke this to his voice assistant Shubh on a phone call (${i.today}). Shubh only talks; you do the work.`,
    "",
    ...(i.requirement ? ["REQUIREMENT (what he wants, as Shubh understood it from the WHOLE call; his exact words are at the end; if they differ, his words win):", `Goal: ${i.requirement.goal}`, ...i.requirement.details.map((d) => `- ${d}`), ...(i.requirement.rules.length ? ["His rules:", ...i.requirement.rules.map((d) => `- ${d}`)] : []), ...(i.requirement.unspecified.length ? ["Not specified (YOU decide sensible defaults; use clearly marked placeholders for names, prices and contact details):", ...i.requirement.unspecified.map((d) => `- ${d}`)] : []), ""] : []),
    "TO DO (items from his notes board; names, times and amounts are exactly as he said them):",
    ...(todo.length ? todo.map(line) : [i.requirement ? "- (see the REQUIREMENT above)" : "- (none: only answer or check what is asked below)"]),
    ...(constraints.length ? ["", "CONSTRAINTS (obey these):", ...constraints.map(line)] : []),
    ...(context.length ? ["", "CONTEXT from the same call (for your understanding; do not act on meetings, payments or reminders unless a task needs it):", ...context.map(line)] : []),
    ...(unclear.length ? ["", "OPEN QUESTIONS (things not clear; if one blocks the work, say so instead of guessing):", ...unclear.map((x) => `- ${x.what}: ${x.unclear}`)] : []),
    ...(i.open ? ["", "STILL OPEN from his earlier calls (background):", i.open] : []),
    ...(i.question ? ["", `ALSO ANSWER: ${i.question}`] : []),
    "",
    OWNER_RULES,
    "",
    WORKING_STYLE,
    ...(i.workspace ? ["", "WORKSPACE (folders you may work in, with what is inside; names only):", i.workspace] : []),
    ...(i.remembered ? ["", "WHAT IS KNOWN ABOUT HIM (from his earlier calls and notes; use it, e.g. for brand details):", i.remembered] : []),
    "",
    ...(i.quotes.length ? ["HIS OWN WORDS (speech-to-text, may be mis-heard; the requirement and items above are the cleaned version):", ...i.quotes.map((q) => `> ${q}`), ""] : []),
    "WHAT TO REPLY: at most 5 short plain sentences, no markdown, no lists, in this order: DONE (what now exists and in which folder), CHOSE (your main decisions), FILL IN (placeholders he must replace, or nothing), NEEDS HIS YES (only if a rule stopped you, or nothing). It will be read to him on the phone.",
  ];
  return screen(parts.join("\n")).slice(0, 12_000);
}

/** A mid-call question for the expert. Short, in his language, with only the constraints that matter. */
export function buildQuestion(question: string, o: { language: string; constraints: Item[]; project?: string }): string {
  return screen(`Sayan is on a phone call and asked you this. Answer in at most 3 short sentences, in plain speech (no markdown, no lists, no code blocks), in ${o.language}, with the facts first. If you are not sure or cannot check, say so in one sentence instead of guessing. You have at most about 20 seconds.\n${o.constraints.length ? `His constraints: ${o.constraints.map((c) => c.what).join("; ")}.\n` : ""}${o.project ? `Project: ${o.project}.\n` : ""}\nQUESTION: ${question}`).slice(0, 4000);
}

/** The readable first message of the session in the Sani app: what Shubh handed over (not the long rules), so it reads like a normal request. */
export function briefForUi(items: Item[], quotes: string[], deferred: string[] = [], req?: Requirement | null, followUp = false): string {
  const todo = items.filter((i) => i.kind === "task"), cons = items.filter((i) => i.kind === "constraint"), ctx = items.filter((i) => i.kind !== "task" && i.kind !== "constraint");
  const l = (i: Item) => `• ${i.what}${i.who ? ` (${i.who})` : ""}${i.when ? ` — ${i.when}` : ""}${i.project ? ` [${i.project}]` : ""}`;
  return screen([
    followUp ? "📞 Added during the same phone call with Shubh." : "📞 Sent from a phone call with Shubh.",
    ...(req ? ["", `Goal: ${req.goal}`, ...req.details.map((d) => `• ${d}`), ...(req.rules.length ? ["", "His rules:", ...req.rules.map((d) => `• ${d}`)] : []), ...(req.unspecified.length ? ["", "Left to the agent (defaults / placeholders):", ...req.unspecified.map((d) => `• ${d}`)] : [])]
      : ["", todo.length ? "Task:" : "Question:", ...(todo.length ? todo.map(l) : deferred.map((d) => `• ${d}`))]),
    ...(cons.length ? ["", "Constraints:", ...cons.map(l)] : []),
    ...(ctx.length && !req ? ["", "Also mentioned on the call:", ...ctx.map(l)] : []),
    ...(quotes.length ? ["", "What he said:", ...quotes.slice(-3).map((q) => `> ${q.slice(0, 300)}`)] : []),
    "", "(The Deep Agent works on this with ZCode. It never deploys, spends money, messages anyone or deletes without a spoken yes.)",
  ].join("\n")).slice(0, 4000);
}
