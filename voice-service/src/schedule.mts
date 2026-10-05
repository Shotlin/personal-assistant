// "Call me back in two minutes" / "call me at 6": a small schedule of calls to the OWNER's own number, kept in voice.db.
// Time is parsed in code where possible (relative: "in 2 minutes", "दो मिनट में") and by a tiny model call otherwise (clock times: "at 6 pm");
// the model only extracts what he said, the arithmetic is done here.
import type { DatabaseSync } from "node:sqlite";
import { screen } from "./secrets.mts";

export type CallbackRequest = { dueAt: number; note: string };
export type ScheduledCall = { id: number; dueAt: number; note: string; status: "pending" | "done" | "failed"; attempts: number };

/** He is asking to be called BACK (not asking Shubh to call somebody else). */
export const CALLBACK_CUE = /(कॉल|फोन|कौल)[ -]?बैक|call[ -]?back|मुझे[^।.?!]{0,30}(कॉल|फोन)|आप[^।.?!]{0,20}(कॉल|फोन) करना|call (me|back)|ring me|give me a (call|ring)|phone me|callback|call me up|मुझे (वापस |फिर से |दोबारा )?(कॉल|फोन)|मुझको (कॉल|फोन)|वापस (कॉल|फोन)|दोबारा (कॉल|फोन) करना|फिर (कॉल|फोन) करना|আমাকে (আবার )?(কল|ফোন)|আবার (কল|ফোন) করো|ফিরতি (কল|ফোন)/i;

const NUM: Record<string, number> = { एक: 1, दो: 2, तीन: 3, चार: 4, पाँच: 5, पांच: 5, छह: 6, छः: 6, सात: 7, आठ: 8, नौ: 9, दस: 10, पंद्रह: 15, बीस: 20, तीस: 30, पैंतालीस: 45, साठ: 60, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, fifteen: 15, twenty: 20, thirty: 30, forty: 40, sixty: 60, এক: 1, দুই: 2, তিন: 3, চার: 4, পাঁচ: 5, ছয়: 6, সাত: 7, আট: 8, নয়: 9, দশ: 10, পনেরো: 15, বিশ: 20, ত্রিশ: 30 };

/** Relative time in minutes from his words ("in 2 minutes", "5 मिनट बाद", "आधे घंटे में", "an hour"), or null. */
export function relativeMinutes(text: string): number | null {
  const t = text.toLowerCase();
  if (/(आधे घंटे|आधा घंटा|half an hour|half hour|আধ ঘণ্টা)/.test(t)) return 30;
  if (/\b(an|one) hour\b|एक घंटे|एक घंटा|এক ঘণ্টা/.test(t)) return 60;
  // a correction ends with the TRUE value ("not ten minutes, two minutes"): take the LAST number + unit
  const all = [...t.matchAll(/(\d+|[a-z\u0900-\u097f\u0980-\u09ff]+)\s*(minutes?|mins?|मिनट|मिनिट|মিনিট|घंटे|घंटा|hours?|hrs?|ঘণ্টা)/g)].filter((x) => /^\d+$/.test(x[1]) || NUM[x[1]]);
  const m = all[all.length - 1];
  if (!m) return null;
  const n = /^\d+$/.test(m[1]) ? Number(m[1]) : NUM[m[1]];
  if (!n) return null;
  return /hour|hrs?|घंटे|घंटा|ঘণ্টা/.test(m[2]) ? n * 60 : n;
}

/** The clock hour (1-12) he literally said ("छह बजे", "at 6", "6 pm", "ছয়টায"), or null. Used to correct the model when it mis-reads a number word. */
export function spokenHour(text: string): number | null {
  const t = text.toLowerCase();
  const m = /(?:at|around|by)\s+(\d{1,2})(?::\d{2})?\b|(\d{1,2}|[\u0900-\u097f]+|[\u0980-\u09ff]+)\s*(?::\d{2})?\s*(?:बजे|बजकर|baje|pm|am|o'clock|টায়|টা)/.exec(t);
  if (!m) return null;
  const raw = m[1] ?? m[2];
  const n = /^\d+$/.test(raw) ? Number(raw) : NUM[raw];
  return n && n >= 1 && n <= 12 ? n : null;
}

const SCHEMA = { type: "object", additionalProperties: false, required: ["is_callback_to_me", "kind", "minutes", "hhmm", "note"], properties: {
  is_callback_to_me: { type: "boolean" }, kind: { type: "string", enum: ["relative", "clock", "none"] }, minutes: { type: "number" }, hhmm: { type: "string" }, note: { type: "string" } } } as const;

/** Ask a tiny model what he asked for. Returns null when it is not a request to be called back, or no time can be found. */
export async function extractCallback(apiKey: string, text: string, now = new Date()): Promise<CallbackRequest | null> {
  // the small model sometimes answers "no" to the very same sentence it accepted a moment ago: a sentence that clearly asks to be called gets a second look
  // "call me when it is ready" has NO time: that is the automatic report when the work finishes, not a scheduled call
  if (relativeMinutes(text) === null && spokenHour(text) === null && !/(सुबह|दोपहर|शाम|रात|morning|afternoon|evening|night|noon|tonight|tomorrow|कल|সকাল|দুপুর|বিকেল|সন্ধ্যা|রাত)/i.test(text)) return null;
  const first = await extractOnce(apiKey, text, now);
  if (first || !CALLBACK_CUE.test(text)) return first;
  return extractOnce(apiKey, text, now);
}

async function extractOnce(apiKey: string, text: string, now: Date): Promise<CallbackRequest | null> {
  const rel = relativeMinutes(text);
  const hh = String(now.getHours()).padStart(2, "0"), mm = String(now.getMinutes()).padStart(2, "0");
  try {
    const res = await fetch("https://api.sarvam.ai/v1/chat/completions", {
      method: "POST", headers: { "api-subscription-key": apiKey, "Content-Type": "application/json" },
      body: JSON.stringify({ model: "sarvam-105b", reasoning_effort: null, temperature: 0, max_tokens: 150,
        messages: [{ role: "system", content: `You read a speech-to-text transcript (Hindi, Bengali, English, mixed; words may be mis-heard) of an owner talking to his phone assistant. Decide whether he asks the assistant to CALL HIM BACK (to himself), and when. Not a callback to him: "call Shubham", "call Amit and tell him...". Return JSON: is_callback_to_me; kind "relative" with minutes (from now) or "clock" with hhmm (24h, the next time that clock time occurs; "evening" = 18:00, "morning" = 09:00, "night" = 21:00) or "none"; note = what to talk about when calling back (English, short, or "").` },
          { role: "user", content: `Current time: ${hh}:${mm}.\nHe said: """${text.slice(0, 600)}"""` }],
        response_format: { type: "json_schema", json_schema: { name: "callback", strict: true, schema: SCHEMA } } }),
      signal: AbortSignal.timeout(6000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const out = JSON.parse(((await res.json()) as any).choices?.[0]?.message?.content ?? "{}");
    if (!out.is_callback_to_me) return null;
    const note = screen(String(out.note ?? "")).slice(0, 200);
    if (rel && rel > 0 && rel <= 24 * 60) return { dueAt: now.getTime() + rel * 60_000, note }; // code beats the model on "in two minutes"
    if (out.kind === "relative" && out.minutes > 0 && out.minutes <= 24 * 60) return { dueAt: now.getTime() + Math.round(out.minutes) * 60_000, note };
    const c = /^(\d{1,2}):(\d{2})$/.exec(String(out.hhmm ?? ""));
    if (out.kind === "clock" && c) {
      let hour = Number(c[1]); const said = spokenHour(text);
      if (said !== null && hour % 12 !== said % 12) hour = (said % 12) + (hour >= 12 ? 12 : 0); // the model misread the number he said: trust his words
      const d = new Date(now); d.setHours(hour, Number(c[2]), 0, 0); if (d.getTime() <= now.getTime() + 60_000) d.setDate(d.getDate() + 1); return { dueAt: d.getTime(), note }; }
  } catch { /* fall through */ }
  return rel && rel > 0 && rel <= 24 * 60 ? { dueAt: now.getTime() + rel * 60_000, note: "" } : null;
}

export class Scheduler {
  constructor(private readonly db: DatabaseSync) {
    db.exec("CREATE TABLE IF NOT EXISTS callbacks (id INTEGER PRIMARY KEY AUTOINCREMENT, due_at INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)");
  }
  add(r: CallbackRequest): number {
    return Number(this.db.prepare("INSERT INTO callbacks (due_at, note, created_at) VALUES (?,?,?)").run(r.dueAt, screen(r.note).slice(0, 200), Date.now()).lastInsertRowid);
  }
  /** A newer request replaces the ones made earlier in the same call ("not 10 minutes, 2 minutes"). */
  cancelPendingSince(sinceMs: number): number { return Number(this.db.prepare("UPDATE callbacks SET status='failed' WHERE status='pending' AND created_at >= ?").run(sinceMs).changes); }
  pending(): ScheduledCall[] { return this.#rows("SELECT * FROM callbacks WHERE status='pending' ORDER BY due_at"); }
  due(now = Date.now()): ScheduledCall[] { return this.pending().filter((c) => c.dueAt <= now); }
  attempt(id: number) { this.db.prepare("UPDATE callbacks SET attempts = attempts + 1 WHERE id=?").run(id); }
  finish(id: number, status: "done" | "failed") { this.db.prepare("UPDATE callbacks SET status=? WHERE id=?").run(status, id); }
  /** A callback that was due long ago (the program was not running) is not placed any more: it would be a confusing call. */
  expire(now = Date.now(), graceMs = 30 * 60_000): number { return Number(this.db.prepare("UPDATE callbacks SET status='failed' WHERE status='pending' AND due_at < ?").run(now - graceMs).changes); }
  #rows(sql: string): ScheduledCall[] { return (this.db.prepare(sql).all() as any[]).map((r) => ({ id: r.id, dueAt: r.due_at, note: r.note, status: r.status, attempts: r.attempts })); }
}
