// The bridge to what the Sani desktop app already knows, through its own local files (no server, nothing leaves the Mac):
//  READ  its long-term memory (/memories/*.md, written by the Deep Agent) and the titles of its recent chats;
//  WRITE one file, /memories/voice-calls.md, so the Deep Agent can see what was said on the phone.
// Everything read is secret-screened before it is shown to the model, and nothing here ever throws: a missing, locked or
// older database just means "no extra context". The Rust app and sani-core stay the owners of these databases; we open
// the history DB read-only, and write only a single row into the memory store (WAL + a busy timeout, like any other writer).
import { DatabaseSync } from "node:sqlite";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { containsSecret, screen } from "./secrets.mts";

const SEP = "\x1f";
/** sani-core runs every Deep Agent turn with user_id "sani-local" (src/assistant/core/agents.py). */
export const SANI_NAMESPACE = ["users", "sani-local", "assistant-memory"].join(SEP);
export const MIRROR_KEY = "/voice-calls.md";
export const saniDir = () => process.env.SANI_DATA_DIR ?? join(homedir(), "Library", "Application Support", "app.sani.local");

export type SaniContext = { files: { name: string; text: string }[]; chats: { title: string; at: number }[]; problems: string[] };

function openRo(path: string): DatabaseSync | null {
  if (!existsSync(path)) return null;
  try { return new DatabaseSync(path, { readOnly: true, timeout: 2000 }); } catch { return null; }
}

/** What Sani knows: its memory files (profile, preferences, anything else the Deep Agent saved) and recent chat titles. */
export function loadSaniContext(dir = saniDir(), opts: { recentDays?: number; maxChats?: number; maxFileChars?: number } = {}): SaniContext {
  const out: SaniContext = { files: [], chats: [], problems: [] };
  const maxFile = opts.maxFileChars ?? 700;
  const mem = openRo(join(dir, "sani.db"));
  if (!mem) out.problems.push("sani.db not readable");
  else {
    try {
      const rows = mem.prepare("SELECT key, value FROM store_items WHERE namespace = ? ORDER BY updated_at DESC LIMIT 20").all(SANI_NAMESPACE) as { key: string; value: string }[];
      for (const r of rows) {
        if (r.key === MIRROR_KEY) continue; // our own mirror: the calls come from voice.db, not from here
        let text = "";
        try { const v = JSON.parse(r.value); text = Array.isArray(v.content) ? v.content.join("\n") : String(v.content ?? ""); } catch { continue; }
        if (!text.trim()) continue;
        out.files.push({ name: r.key.replace(/^\//, ""), text: screen(text).trim().slice(0, maxFile) });
      }
    } catch (e: any) { out.problems.push(`memory: ${e.message}`); } finally { mem.close(); }
  }
  const hist = openRo(join(dir, "sani-history.db"));
  if (hist) {
    try {
      const since = Date.now() - (opts.recentDays ?? 14) * 86_400_000;
      const rows = hist.prepare("SELECT title, updated_at FROM conversations WHERE updated_at >= ? ORDER BY updated_at DESC LIMIT ?").all(since, (opts.maxChats ?? 6) * 2) as { title: string; updated_at: number }[];
      for (const r of rows) {
        const title = screen(String(r.title)).replace(/(?:~|\/Users)\/[^\s]*\/([^\s/]+)/g, "$1").replace(/\s+/g, " ").trim().slice(0, 90); // a path becomes its last folder name: paths are never read aloud
        if (!title || /^new conversation$/i.test(title) || containsSecret(r.title)) continue;
        out.chats.push({ title, at: r.updated_at });
        if (out.chats.length >= (opts.maxChats ?? 6)) break;
      }
    } catch (e: any) { out.problems.push(`history: ${e.message}`); } finally { hist.close(); }
  }
  return out;
}

/** Write (replace) the single file the Deep Agent can read at /memories/voice-calls.md. Returns "" on success or why it was skipped.
 *  Same format deepagents' StoreBackend uses ({content, encoding, created_at, modified_at}); checked against it in tests. */
export function mirrorToSani(markdown: string, dir = saniDir()): string {
  const bad = containsSecret(markdown);
  if (bad) return `not written: ${bad}`;
  const path = join(dir, "sani.db");
  if (!existsSync(path)) return "sani.db not found";
  let db: DatabaseSync | null = null;
  try {
    db = new DatabaseSync(path, { timeout: 5000 });
    const has = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='store_items'").get();
    if (!has) return "sani.db has no memory table yet (start the Sani app once)";
    const now = new Date().toISOString();
    const old = db.prepare("SELECT value FROM store_items WHERE namespace = ? AND key = ?").get(SANI_NAMESPACE, MIRROR_KEY) as { value: string } | undefined;
    let created = now; try { if (old) created = JSON.parse(old.value).created_at ?? now; } catch { /* keep now */ }
    const value = JSON.stringify({ content: markdown, encoding: "utf-8", created_at: created, modified_at: now });
    db.prepare(`INSERT INTO store_items (namespace, key, value, created_at, updated_at) VALUES (?,?,?,?,?)
      ON CONFLICT(namespace, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`).run(SANI_NAMESPACE, MIRROR_KEY, value, created, now);
    return "";
  } catch (e: any) { return `not written: ${e.message}`; } finally { try { db?.close(); } catch { /* ignore */ } }
}
