// What Shubh remembers between calls: one small local SQLite file (var/voice.db, Mac only, git-ignored).
// A call leaves behind (1) a short summary with decisions and (2) its board items. Items stay "open" until a later
// call replaces them or something marks them done (the Deep Agent will, in Phase 3). Secrets never reach the file.
// Not stored on purpose: the raw transcript and the audio (those are only the debug copies in var/last-call.*).
import { DatabaseSync } from "node:sqlite";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { sameThing, type Item } from "./notes.mts";
import { containsSecret, screen } from "./secrets.mts";

export type CallRecord = { id: string; startedAt: number; endedAt: number; durationS: number; language: string; summary: string; decisions: string[]; topics: string[]; costInr: number };
export type JobRecord = { id: string; callId: string; runId: string; goal: string; status: string; progress: string; result: string; createdAt: number; finishedAt: number | null };
export type StoredItem = Item & { callId: string; status: "open" | "done" | "superseded" | "dropped"; createdAt: number };

/** Kinds that are work still to be done (the rest are things to remember). */
export const ACTIONABLE = new Set(["meeting", "task", "payment", "reminder", "call_request", "question"]);
const MAX_OPEN_AGE_DAYS = 21;

export class VoiceMemory {
  readonly db: DatabaseSync;
  constructor(path = process.env.VOICE_DB ?? "var/voice.db") {
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path, { timeout: 5000 });
    this.db.exec(`PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, started_at INTEGER NOT NULL, ended_at INTEGER NOT NULL, duration_s INTEGER NOT NULL,
        language TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL, decisions TEXT NOT NULL DEFAULT '[]', topics TEXT NOT NULL DEFAULT '[]', cost_inr REAL NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS items (call_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, what TEXT NOT NULL, who TEXT NOT NULL DEFAULT '', whn TEXT NOT NULL DEFAULT '',
        amount TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '', unclear TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', created_at INTEGER NOT NULL, closed_at INTEGER,
        PRIMARY KEY (call_id, id));
      CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, call_id TEXT NOT NULL, run_id TEXT NOT NULL, goal TEXT NOT NULL, brief TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running',
        progress TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, finished_at INTEGER);
      CREATE INDEX IF NOT EXISTS items_open ON items (status, created_at);`);
  }
  close() { this.db.close(); }

  /** Store one finished call and its items. Items that restate an earlier still-open item replace it. Secrets are screened out. */
  saveCall(call: CallRecord, items: Item[]): void {
    const clean = (s: string) => screen(s).trim();
    this.db.exec("BEGIN");
    try {
      this.db.prepare("INSERT OR REPLACE INTO calls VALUES (?,?,?,?,?,?,?,?,?)").run(call.id, call.startedAt, call.endedAt, call.durationS, call.language, clean(call.summary),
        JSON.stringify(call.decisions.map(clean)), JSON.stringify(call.topics.map(clean)), call.costInr);
      const open = this.openItems(call.startedAt, 10_000, call.id);
      const ins = this.db.prepare("INSERT OR REPLACE INTO items (call_id,id,kind,what,who,whn,amount,project,unclear,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,'open',?)");
      const sup = this.db.prepare("UPDATE items SET status='superseded', closed_at=? WHERE call_id=? AND id=? AND status='open'");
      for (const raw of items) {
        if (containsSecret(JSON.stringify(raw))) continue; // an item that carries a password/code/key is dropped whole, never half-kept
        const it: Item = { ...raw, what: clean(raw.what), who: clean(raw.who), when: clean(raw.when), amount: clean(raw.amount), project: clean(raw.project), unclear: clean(raw.unclear) };
        for (const old of open) if (old.kind === it.kind && sameThing(old, it) >= 1.0) sup.run(call.startedAt, old.callId, old.id);
        ins.run(call.id, it.id, it.kind, it.what, it.who, it.when, it.amount, it.project, it.unclear, call.startedAt);
      }
      this.db.exec("COMMIT");
    } catch (e) { this.db.exec("ROLLBACK"); throw e; }
  }

  #rowToCall(r: any): CallRecord {
    return { id: r.id, startedAt: r.started_at, endedAt: r.ended_at, durationS: r.duration_s, language: r.language, summary: r.summary, decisions: JSON.parse(r.decisions), topics: JSON.parse(r.topics), costInr: r.cost_inr };
  }
  /** Newest first. */
  recentCalls(limit = 8, sinceMs = 0): CallRecord[] {
    return this.db.prepare("SELECT * FROM calls WHERE started_at >= ? ORDER BY started_at DESC LIMIT ?").all(sinceMs, limit).map((r) => this.#rowToCall(r));
  }
  /** Items still open (newest first), from calls before `before`, at most MAX_OPEN_AGE_DAYS old. */
  openItems(before = Date.now(), limit = 40, exceptCall = ""): StoredItem[] {
    const since = before - MAX_OPEN_AGE_DAYS * 86_400_000;
    return this.db.prepare("SELECT * FROM items WHERE status='open' AND created_at >= ? AND created_at < ? AND call_id != ? ORDER BY created_at DESC LIMIT ?").all(since, before, exceptCall, limit)
      .map((r: any) => ({ callId: r.call_id, id: r.id, kind: r.kind, what: r.what, who: r.who, when: r.whn, amount: r.amount, project: r.project, unclear: r.unclear, status: r.status, createdAt: r.created_at }));
  }
  addJob(j: { id: string; callId: string; runId: string; goal: string; brief: string }): void {
    this.db.prepare("INSERT OR REPLACE INTO jobs (id,call_id,run_id,goal,brief,status,created_at) VALUES (?,?,?,?,?,'running',?)").run(j.id, j.callId, j.runId, screen(j.goal).slice(0, 400), screen(j.brief).slice(0, 8000), Date.now());
  }
  updateJob(id: string, u: { status?: string; progress?: string; result?: string; finished?: boolean }): void {
    const cur = this.db.prepare("SELECT status, progress, result FROM jobs WHERE id=?").get(id) as any; if (!cur) return;
    this.db.prepare("UPDATE jobs SET status=?, progress=?, result=?, finished_at=? WHERE id=?").run(u.status ?? cur.status, screen(u.progress ?? cur.progress).slice(0, 400), screen(u.result ?? cur.result).slice(0, 1500), u.finished ? Date.now() : null, id);
  }
  recentJobs(limit = 5, sinceMs = 0): JobRecord[] {
    return this.db.prepare("SELECT * FROM jobs WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?").all(sinceMs, limit).map((r: any) => ({ id: r.id, callId: r.call_id, runId: r.run_id, goal: r.goal, status: r.status, progress: r.progress, result: r.result, createdAt: r.created_at, finishedAt: r.finished_at }));
  }

  /** Mark an item done (used when the Deep Agent finishes the work, or he says it is finished). */
  markDone(callId: string, id: string, status: "done" | "dropped" = "done"): void {
    this.db.prepare("UPDATE items SET status=?, closed_at=? WHERE call_id=? AND id=?").run(status, Date.now(), callId, id);
  }
  /** Delete calls (and their items) older than `days`. The retention rule is the owner's call (open decision 4). */
  prune(days: number): number {
    const cut = Date.now() - days * 86_400_000;
    this.db.prepare("DELETE FROM items WHERE call_id IN (SELECT id FROM calls WHERE started_at < ?)").run(cut);
    return Number(this.db.prepare("DELETE FROM calls WHERE started_at < ?").run(cut).changes);
  }
}
