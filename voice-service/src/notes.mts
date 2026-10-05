// The notes board: what Sayan has told Shubh during this call, as clean items instead of raw chat.
// After each thing he says, a background step (never on the speaking path) files it and also notices
// contradictions with what he said earlier. Recaps and the later hand-off brief (Phase 3) read the board,
// not the chat, so nothing is merged, dropped or invented.
import { writeFileSync, mkdirSync } from "node:fs";

export type Item = {
  id: string;
  kind: "meeting" | "task" | "payment" | "reminder" | "call_request" | "constraint" | "decision" | "fact" | "question";
  what: string;      // one line, English
  who: string;       // person / company ("" if none)
  when: string;      // time or date exactly as said ("" if none)
  amount: string;    // money / quantity ("" if none)
  project: string;   // app / project / system ("" if none)
  unclear: string;   // what is missing or ambiguous ("" if clear)
};
export type Conflict = { about: string; note: string };

const ITEM = { type: "object", additionalProperties: false,
  required: ["id", "kind", "what", "who", "when", "amount", "project", "unclear"],
  properties: {
    id: { type: "string" },
    kind: { type: "string", enum: ["meeting", "task", "payment", "reminder", "call_request", "constraint", "decision", "fact", "question"] },
    what: { type: "string" }, who: { type: "string" }, when: { type: "string" }, amount: { type: "string" }, project: { type: "string" }, unclear: { type: "string" },
  } } as const;
// The model reports only CHANGES (new or changed items, cancelled ids); code applies them. Unchanged items can never drift.
const SCHEMA = {
  type: "object", additionalProperties: false, required: ["upserts", "removes", "conflicts"],
  properties: {
    upserts: { type: "array", items: ITEM },
    removes: { type: "array", items: { type: "string" } },
    conflicts: { type: "array", items: { type: "object", additionalProperties: false, required: ["about", "note"],
      properties: { about: { type: "string" }, note: { type: "string" } } } },
  },
} as const;

const SYSTEM = `You keep the notes board for a voice assistant on a live phone call with its owner, Sayan.
You get the CURRENT BOARD (JSON, each item has an id) and what Sayan just said (a speech-to-text transcript in Hindi, Bengali, English or a mix; words can be wrong, names are often mis-spelled). Return ONLY THE CHANGES:
- upserts: items that are NEW, or that CHANGED (reuse the exact id of the existing item to change it; a new item gets a new short id like "meet-rajput-2pm"). Do not repeat unchanged items.
- removes: ids of items he cancelled, or that a new item REPLACES.
- conflicts: real contradictions with the board (see below).
If the message adds nothing to remember, return empty lists.

What an item is: a meeting, a task, a payment to collect or make, a reminder, a request to call or tell someone something, a constraint ("do not change the UI", "advance payment is our SOP"), a decision, or a fact worth remembering (an outage, a status).
- Write "what" in plain English, one line. Copy people, apps, times and amounts EXACTLY as Sayan said them (keep a name's spelling as it sounds: Rajput, Shubham, Baklo). Never invent. Never turn a time into a different time. Keep ranges exactly: "6-7 बजे" is "6-7 pm".
- Numbers in words: एक=1 दो=2 तीन=3 चार=4 पाँच=5 छह=6 सात=7 आठ=8 नौ=9 दस=10 (Bengali: এক দুই তিন চার পাঁচ ছয় সাত আট নয় দশ). "छह बंदे/लोग" = 6 people; "6-7 बजे" is a time. Never mix a count with a time.
- Go through the message sentence by sentence. Every sentence that states a fact, status, plan, request or rule must be recorded. Do not skip an outage, a status update, a rule or a constraint just because it is not an action.
- If an item is incomplete or ambiguous (a time with no task, a name not caught, "6 or 7"), fill "unclear" with what is missing in a few words, else "".
- "when" holds only a real time or date as said ("2 pm", "tomorrow", "6-7 pm"); otherwise "", never "now".
- ONE item per thing. A payment to collect and a request to call someone about it are TWO items. A rule or constraint is its own item (never fold it into another item's text).
- "unclear" is SHORT (a few words naming what is missing, e.g. "who will test?") or "". Never put a description, a copy of the task, or a sentence there.
- WORKED EXAMPLE. He says: "कल तीन बजे अमित के साथ मीटिंग है। अमित से पांच हजार लेना है, तुम उसे कॉल करके बोल देना। ऐप का लोगो बदलना है, UI मत छूना। और बकलो का सर्वर आज रात डाउन था, अब ठीक है।"
  upserts:
  {"id":"meet-amit-3pm","kind":"meeting","what":"Meeting with Amit","who":"Amit","when":"tomorrow 3 pm","amount":"","project":"","unclear":""}
  {"id":"collect-amit-5000","kind":"payment","what":"Collect ₹5,000 from Amit","who":"Amit","when":"","amount":"₹5,000","project":"","unclear":""}
  {"id":"call-amit-pay","kind":"call_request","what":"Call Amit and tell him to pay ₹5,000","who":"Amit","when":"","amount":"₹5,000","project":"","unclear":""}
  {"id":"change-logo","kind":"task","what":"Change the app's logo","who":"","when":"","amount":"","project":"app","unclear":""}
  {"id":"no-ui-change","kind":"constraint","what":"Do not touch the UI","who":"","when":"","amount":"","project":"app","unclear":""}
  {"id":"baklo-server-down","kind":"fact","what":"Baklo's server was down last night and is fine now","who":"","when":"last night","amount":"","project":"Baklo","unclear":""}
- "Deep Agent" / "डीप एजेंट" / "the agent" is the assistant's own SOFTWARE agent, never a person: never write it as Deepak/दीपक in "who". An instruction to tell the agent to do something ("go and tell the Deep Agent to build a cookie website") is a TASK (the task itself, e.g. "Build a simple cookie-selling website"), with who empty.
- A request to CALL HIM BACK or to be told when it is ready ("call me when it is ready", "call me in 2 minutes") is NOT an item (the phone system handles it). Never let such a request replace or change a real task.
- NOT items: greetings, small talk, and QUESTIONS Sayan asks the assistant (advice, explanations, "what price should we set?"). Only what he tells it to remember or do.
- CORRECTIONS ARE THE MOST IMPORTANT PART. "not X, Y", "instead", "change it to", "actually", "X को नहीं, Y को" means: upsert the SAME id with the new who / when / amount AND a rewritten "what" that names the new person and time (never leave the old name in "what"), and mention it in conflicts. Never leave the old value.
  Example: board has {id:"call-shubham-pay", who:"Shubham", when:"7pm"}. He says "tell Soumen, not Shubham, and make it 8 not 7". upserts: the same id with who "Soumen", when "8pm"; conflicts: "Payment call changed from Shubham at 7pm to Soumen at 8pm - confirm the money is still due from Shubham".
- Two different things at the same time, or a person/amount that no longer matches the earlier one, are conflicts. conflicts are one short English sentence each; otherwise [].`;

const norm = (v: string) => v.toLowerCase().replace(/\s+/g, " ").trim();
const words = (v: string) => new Set(norm(v).replace(/[^\p{L}\p{N} ]/gu, "").split(" ").filter((w) => w.length > 2));
const overlap = (a: string, b: string) => { const x = words(a), y = words(b); let k = 0; for (const w of x) if (y.has(w)) k++; return k / Math.max(1, Math.min(x.size, y.size)); };
/** How strongly two items are about the same thing (0..1.7). */
export const sameThing = (o: Item, n: Item) => overlap(o.what, n.what) + (n.kind === o.kind ? 0.3 : 0) + (o.project && n.project && norm(o.project) === norm(n.project) ? 0.4 : 0);

/** Words that mean "I am changing/cancelling something". Without one of them an existing item is never overwritten or removed:
 *  a vague sentence must not be able to silently replace a meeting. */
export const CORRECTION_CUES = /\b(not|instead|cancel(led)?|change[ds]?|actually|wrong|rather|correct(ion)?|replace|postpone[d]?|reschedule[d]?|no longer|forget)\b|नहीं|बदल|बजाय|की जगह|रद्द|कैंसल|छोड़ो|हटा|गलत|दरअसल|पोस्टपोन|না|বদল|বদলে|পরিবর্তন|বাতিল|আসলে|ভুল|ছেড়ে দাও|সরিয়ে/i;

/** A person, time or amount that differs between the old and the changed version of the same item is a conflict
 *  (found in code: the model is not trusted to mention it). */
export function fieldChanges(o: Item, n: Item): Conflict[] {
  const out: Conflict[] = [];
  for (const [field, label] of [["who", "person"], ["when", "time"], ["amount", "amount"]] as const)
    if (o[field] && n[field] && norm(o[field]) !== norm(n[field])) out.push({ about: o.id, note: `"${n.what}": the ${label} changed from ${o[field]} to ${n[field]}` });
  return out;
}

const NO_CONFLICT = /\b(no (real )?(conflict|contradiction)|not a (real )?(conflict|contradiction)|consistent|does ?n[o']?t (actually )?(conflict|contradict)|nothing to confirm)\b/i;

export class NotesBoard {
  items: Item[] = [];
  conflicts: Conflict[] = [];     // conflicts found by the latest update
  version = 0;
  updating: Promise<void> = Promise.resolve();
  ms = 0;                          // how long the last update took
  hints = "";                     // names known from earlier calls (projects, people): the recogniser mishears them
  error = "";                      // last failure, if any (the board keeps its previous content)
  constructor(private readonly apiKey: string, private readonly file = "var/last-call-board.json", private readonly effort: "low" | "medium" | null = process.env.BOARD_EFFORT === "medium" ? "medium" : process.env.BOARD_EFFORT === "low" ? "low" : null) {}

  /** File what he just said. Runs in the background; calls are queued so updates never overlap. */
  update = (userSaid: string): Promise<void> => {
    // a short acknowledgement ("ठीक है, समझ गया") has nothing to file; skipping it also removes any chance of a bad rewrite
    if (userSaid.trim().split(/\s+/).length < 6 && !/\d/.test(userSaid)) return this.updating;
    this.updating = this.updating.then(() => this.#run(userSaid)).then(() => { this.error = ""; }).catch((e) => { this.error = String(e?.message ?? e); });
    return this.updating;
  };

  /** One model call that returns a patch. */
  async #ask(userMsg: string, effort: "low" | "medium" | null): Promise<{ upserts: Item[]; removes: string[]; conflicts: Conflict[] }> {
    const res = await fetch("https://api.sarvam.ai/v1/chat/completions", {
      method: "POST",
      headers: { "api-subscription-key": this.apiKey, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "sarvam-105b",
        messages: [{ role: "system", content: SYSTEM }, { role: "user", content: userMsg }],
        reasoning_effort: effort, temperature: 0.1, max_tokens: effort ? 6000 : 2000,
        response_format: { type: "json_schema", json_schema: { name: "board", strict: true, schema: SCHEMA } },
      }),
      signal: AbortSignal.timeout(effort ? 90000 : 30000),
    });
    if (!res.ok) throw new Error(`board HTTP ${res.status}`);
    const j: any = await res.json();
    const raw = j.choices?.[0]?.message?.content;
    if (!raw) throw new Error(`board: empty answer (finish_reason ${j.choices?.[0]?.finish_reason}, tokens ${JSON.stringify(j.usage)})`);
    return JSON.parse(raw);
  }

  async #run(userSaid: string): Promise<void> {
    const t0 = Date.now();
    const long = userSaid.trim().split(/\s+/).length >= 30;
    const out = await this.#ask(`${this.#known()}CURRENT BOARD:\n${JSON.stringify(this.items)}\n\nSAYAN JUST SAID:\n"""${userSaid.slice(0, 3000)}"""\n\nReturn only the changes.`, this.effort);
    this.#apply(out, userSaid);
    // a long hand-over: a second quick pass only looks for what the first one missed or got wrong
    if (long) {
      // a review that finds something suggests there may be more: look again (at most twice)
      for (let pass = 0; pass < 2; pass++) {
        try {
          const fix = await this.#ask(`${this.#known()}CURRENT BOARD (after earlier passes):\n${JSON.stringify(this.items)}\n\nSAYAN SAID:\n"""${userSaid.slice(0, 3000)}"""\n\nREVIEW: go through what Sayan said sentence by sentence. Is any fact, task, payment, call request, time, person, amount or rule missing from the board, or wrong on it? A payment to collect and a request to call about it are two items; a rule is its own item. Return ONLY the missing items and fixes (reuse ids for fixes). If the board is complete and right, return empty lists.`, this.effort);
          this.#apply(fix, userSaid, /*review*/ true);
          if (!(fix.upserts ?? []).length) break;
        } catch { break; /* the passes so far stand */ }
      }
    }
    this.version++; this.ms = Date.now() - t0;
    try { mkdirSync("var", { recursive: true }); writeFileSync(this.file, JSON.stringify({ items: this.items, conflicts: this.conflicts }, null, 1)); } catch { /* debug copy only */ }
  }

  #known = () => this.hints ? `KNOWN NAMES from his earlier calls (the speech recogniser often mishears them; if a word sounds like one of these and fits the sentence, write the known name, e.g. "US system" -> "POS system"): ${this.hints}\n\n` : "";

  /** Apply a patch to the board in code (see the guards below). */
  #apply(out: { upserts: Item[]; removes: string[]; conflicts: Conflict[] }, userSaid: string, review = false): void {
    // the model sometimes writes a "conflict" that says there is none ("this is consistent, no conflict needed"): never raise those
    const found: Conflict[] = (out.conflicts ?? []).filter((c) => !NO_CONFLICT.test(c.note));
    const items = this.items.map((i) => ({ ...i }));
    const removed = new Set<string>();
    const corrects = CORRECTION_CUES.test(userSaid);
    const shortMsg = userSaid.trim().split(/\s+/).length < 12;
    // removes: only when he said a correction word, and a short message cannot wipe several items (that is drift)
    const wantRemove = corrects ? (out.removes ?? []).filter((id) => items.some((i) => i.id === id)) : [];
    for (const id of shortMsg && wantRemove.length > 2 ? [] : wantRemove) removed.add(id);
    let seq = 0;
    for (const u of out.upserts ?? []) {
      if (/^(now|currently|right now)$/i.test(u.when.trim())) u.when = ""; // not a real time
      let at = items.findIndex((i) => i.id === u.id);
      if (at < 0 && corrects) { // a new id that is really an existing item reworded (it often renames an item when correcting it)
        let best = -1, bestScore = 0;
        items.forEach((i, k) => { const sc = sameThing(i, u); if (sc > bestScore) { bestScore = sc; best = k; } });
        if (best >= 0 && bestScore >= 0.9) at = best;
      }
      // the model sometimes reuses the id of an item for something unrelated: that is a NEW item, never an overwrite
      if (at >= 0 && items[at].kind === u.kind && overlap(items[at].what, u.what) < 0.15 && !(items[at].who && items[at].who === u.who)) { u.id = `${u.id}-n${++seq}`; at = -1; }
      if (at >= 0) {
        const ch = fieldChanges(items[at], u);
        if (ch.length && !corrects) {
          // no correction word, yet a person/time/amount differs: keep BOTH and flag it, never overwrite silently
          items.push({ ...u, id: `${u.id}-${++seq}` });
          found.push(...ch.map((c) => ({ about: c.about, note: `${c.note} (not confirmed: you did not say it was a correction)` })));
          continue;
        }
        found.push(...ch);
        items[at] = { ...u, id: items[at].id };
        removed.delete(items[at].id);
      } else {
        for (const r of removed) { const old = items.find((i) => i.id === r); if (old && sameThing(old, u) >= 0.7) found.push(...fieldChanges(old, u)); }
        items.push(u);
      }
    }
    this.items = items.filter((i) => !removed.has(i.id));
    this.conflicts = review ? [...this.conflicts, ...found] : found;
  }

  /** Compact text for the model. */
  toText = (): string => this.items.length
    ? this.items.map((i) => `- [${i.kind}] ${i.what}${i.who ? ` | who: ${i.who}` : ""}${i.when ? ` | when: ${i.when}` : ""}${i.amount ? ` | amount: ${i.amount}` : ""}${i.project ? ` | project: ${i.project}` : ""}${i.unclear ? ` | UNCLEAR: ${i.unclear}` : ""}`).join("\n")
    : "(empty)";
}
