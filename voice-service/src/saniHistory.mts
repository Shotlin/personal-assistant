// A job handed over on the phone becomes a real SESSION in the Sani desktop app: a conversation with the task, the agent's step-by-step
// activity (Using zcode, Wrote index.html, ...) and its final answer, so the owner can open it in the sidebar and see exactly what the Deep Agent
// did. The app keeps its UI history in plain SQLite (sani-history.db: conversations, messages, run_activity; schema in sani/src-tauri/src/history.rs);
// we write the same rows the Rust host writes for its own runs, and nothing else. The job runs on the SAME thread id as the conversation
// (the app passes the conversation id as the agent thread), so if he types a follow-up in that session, the Deep Agent remembers the whole job.
// Never throws: if the file is missing or locked the job still runs, it is just not shown.
import { DatabaseSync } from "node:sqlite";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { saniDir } from "./sani.mts";
import { screen } from "./secrets.mts";

export type StepFields = { id?: string; label?: string; status?: string; tool?: string; duration_ms?: number; detail?: string; kind?: string; group?: string };

export class SaniHistory {
  #db: DatabaseSync | null = null;
  problem = "";
  constructor(dir = saniDir()) {
    const path = join(dir, "sani-history.db");
    if (!existsSync(path)) { this.problem = "sani-history.db not found (start the Sani app once)"; return; }
    try {
      this.#db = new DatabaseSync(path, { timeout: 5000 });
      const cols = (t: string) => (this.#db!.prepare(`SELECT name FROM pragma_table_info('${t}')`).all() as { name: string }[]).map((c) => c.name);
      const need: Record<string, string[]> = { conversations: ["id", "title", "created_at", "updated_at"], messages: ["id", "conversation_id", "role", "text", "created_at", "run_id", "agent_id", "agent_name"],
        run_activity: ["sequence", "conversation_id", "run_id", "agent_id", "event_type", "timestamp", "label", "status"] };
      for (const [t, want] of Object.entries(need)) { const have = cols(t); const miss = want.filter((c) => !have.includes(c)); if (miss.length) { this.problem = `${t} lacks ${miss.join(",")}: the app's history format changed`; this.#db.close(); this.#db = null; return; } }
    } catch (e: any) { this.problem = e.message; this.#db = null; }
  }
  get ok() { return !!this.#db; }

  #run<T>(fn: (db: DatabaseSync) => T): T | null {
    if (!this.#db) return null;
    try { return fn(this.#db); } catch (e: any) { this.problem = e.message; return null; }
  }

  /** A new conversation (it appears at the top of the sidebar). Returns its id, or null when the app's history is not available. */
  startSession(title: string): string | null {
    const id = randomUUID(), now = Date.now();
    const clean = screen(title).replace(/\s+/g, " ").trim();
    const t = clean.length > 48 ? clean.slice(0, 47) + "…" : clean;
    return this.#run((db) => { db.prepare("INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?,?,?,?)").run(id, t || "Voice task", now, now); return id; });
  }

  addMessage(conversationId: string, role: "user" | "assistant", text: string, o: { runId?: string; agent?: boolean } = {}): void {
    const now = Date.now();
    this.#run((db) => {
      db.prepare("INSERT INTO messages (id, conversation_id, role, text, created_at, run_id, agent_id, agent_name) VALUES (?,?,?,?,?,?,?,?)")
        .run(randomUUID(), conversationId, role, screen(text).slice(0, 20_000), now, o.runId ?? null, o.agent ? "deep" : null, o.agent ? "Deep Agent" : null);
      db.prepare("UPDATE conversations SET updated_at = ? WHERE id = ?").run(now, conversationId);
    });
  }

  /** One line of activity, in the shape the app's own runs use: "agent.progress" for a plain note, "agent.step" for a structured step.
   *  A step that is still running is not stored (the app shows it live and replaces it with the finished row): same rule here. */
  addActivity(conversationId: string, runId: string, o: { note?: string; step?: StepFields }): void {
    const now = Date.now();
    if (o.step) {
      const s = o.step;
      if (!s.label || s.status === "running") return;
      this.#row(conversationId, runId, now, "agent.step", s.label, s.status ?? "complete", s);
    } else if (o.note) this.#row(conversationId, runId, now, "agent.progress", o.note, "info", {});
  }
  #row(conv: string, runId: string, ts: number, type: string, label: string, status: string, s: StepFields) {
    for (let attempt = 0; attempt < 3; attempt++) { // the app computes MAX+1 too: on a rare collision, try again
      const ok = this.#run((db) => {
        db.prepare("INSERT INTO run_activity (sequence, conversation_id, run_id, agent_id, event_type, timestamp, label, status, step_id, tool, duration_ms, detail, kind, step_group) VALUES ((SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_activity), ?,?,?,?,?,?,?,?,?,?,?,?,?)")
          .run(conv, runId, "deep", type, ts, screen(label).slice(0, 300), status, s.id ?? null, s.tool ?? null, s.duration_ms ?? null, s.detail ? screen(s.detail).slice(0, 2000) : null, s.kind ?? null, s.group ?? null);
        return true;
      });
      if (ok) return;
    }
  }
  close() { try { this.#db?.close(); } catch { /* gone */ } this.#db = null; }
}
