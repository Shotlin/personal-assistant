// Checks that a job handed over on the phone shows up as a real session in the Sani app's history database, with the same rows the app writes
// itself. Uses a TEMPORARY database with the exact schema of sani/src-tauri/src/history.rs, and the stand-in core. No cost.
//   npm run session-test             temporary database only
//   npm run session-test -- real     ALSO writes one clearly-labelled demo session into your REAL Sani history (delete it from the sidebar afterwards)
import { mkdirSync, rmSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { SaniCore } from "../src/core.mts";
import { Handoff } from "../src/handoff.mts";
import { SaniHistory } from "../src/saniHistory.mts";
import { VoiceMemory } from "../src/memory.mts";

const DIR = "var/test-session"; rmSync(DIR, { recursive: true, force: true }); mkdirSync(DIR, { recursive: true });
let fails = 0;
const check = (name: string, ok: boolean, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  " + extra : ""}`); if (!ok) fails++; };

// the exact tables the Rust host creates (history.rs), plus its in-place column additions
const db = new DatabaseSync(`${DIR}/sani-history.db`);
db.exec(`PRAGMA journal_mode = WAL;
 CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT 'New conversation', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
 CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, role TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL, run_id TEXT, agent_id TEXT, agent_name TEXT, mission_id TEXT);
 CREATE TABLE run_activity (sequence INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE, run_id TEXT NOT NULL, agent_id TEXT NOT NULL, event_type TEXT NOT NULL, timestamp INTEGER NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL, step_id TEXT, tool TEXT, duration_ms INTEGER, detail TEXT, kind TEXT, step_group TEXT);
 INSERT INTO conversations VALUES ('existing', 'hi', 1, 1);`);

const bad = new SaniHistory(`${DIR}/nope`); check("missing database: no crash, reports why", !bad.ok && /not found/.test(bad.problem));
const h = new SaniHistory(DIR); check("history opens and the schema matches the app's", h.ok, h.problem);

const sid = h.startSession("Voice: build a simple SaaS-style website selling cookies for the brand, with hero, menu, about and contact sections");
check("a session is created with a short title", !!sid && (db.prepare("SELECT title FROM conversations WHERE id=?").get(sid!) as any).title.length <= 48, (db.prepare("SELECT title FROM conversations WHERE id=?").get(sid!) as any)?.title);

// the full path: Handoff + stand-in core
const core = await SaniCore.start({ command: ["python3", `${import.meta.dirname}/fake-core.py`] });
const mem = new VoiceMemory(`${DIR}/voice.db`);
const handoff = new Handoff(core, mem, "sess", h);
const job = handoff.submit("Build a simple SaaS-style cookie website", "VOICE HAND-OFF\nTO DO: build the website", "📞 Sent from a phone call with Shubh.\n\nTask:\n• Build a simple SaaS-style cookie website");
check("the job got a session and runs on that session's thread", !!job.sessionId, job.sessionId ?? "");
await job.handle.done; await new Promise((r) => setTimeout(r, 300));
const sess = job.sessionId!;
const msgs = db.prepare("SELECT role, text, run_id, agent_id, agent_name FROM messages WHERE conversation_id=? ORDER BY created_at ASC, rowid ASC").all(sess) as any[];
check("two messages: his task, then the Deep Agent's answer", msgs.length === 2 && msgs[0].role === "user" && msgs[1].role === "assistant", msgs.map((m) => m.role).join());
check("the assistant message is attributed to the Deep Agent with the run id", msgs[1]?.agent_id === "deep" && msgs[1]?.agent_name === "Deep Agent" && !!msgs[1]?.run_id);
check("the user message is readable (not the long rules)", /Sent from a phone call/.test(msgs[0].text) && !/STANDING RULES/.test(msgs[0].text));
check("the answer text is the agent's own result", /Fixed the refund flow/.test(msgs[1]?.text ?? ""));
const act = db.prepare("SELECT sequence, event_type, label, status, agent_id FROM run_activity WHERE conversation_id=? ORDER BY sequence ASC").all(sess) as any[];
check("the agent's progress is stored as activity (Using claude_code, Using run_tests)", act.length >= 2 && act.every((a) => a.event_type === "agent.progress" && a.status === "info" && a.agent_id === "deep"), act.map((a) => a.label).join(" | "));
check("activity sequence numbers are unique and increasing", new Set(act.map((a) => a.sequence)).size === act.length && act.every((a, i) => i === 0 || a.sequence > act[i - 1].sequence));
h.addActivity(sess, "r", { step: { id: "s1", label: "Wrote index.html", status: "running" } }); h.addActivity(sess, "r", { step: { id: "s1", label: "Wrote index.html", status: "complete", tool: "write", duration_ms: 1200, kind: "step" } });
const steps = db.prepare("SELECT status, step_id, tool, duration_ms FROM run_activity WHERE event_type='agent.step'").all() as any[];
check("a structured step is stored once, when finished (a running one is skipped, like the app does)", steps.length === 1 && steps[0].status === "complete" && steps[0].step_id === "s1" && steps[0].duration_ms === 1200);
check("the existing conversation is untouched", (db.prepare("SELECT title FROM conversations WHERE id='existing'").get() as any).title === "hi");
check("the session floats to the top of the sidebar (newest updated_at)", (db.prepare("SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1").get() as any).id === sess);
h.addMessage(sess, "assistant", "my api_key = sk_live_abcdefghijklmnop1234", { agent: true });
check("a secret in an agent message is hidden before it is stored", !/abcdefghijklmnop/.test((db.prepare("SELECT text FROM messages WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1").get(sess) as any).text));

if (process.argv[2] === "real") {
  const real = new SaniHistory();
  if (!real.ok) console.log(`SKIP real: ${real.problem}`);
  else {
    const rh = new Handoff(core, mem, "demo", real);
    const j = rh.submit("(demo, safe to delete) Voice session preview", "VOICE HAND-OFF\nTO DO: demo", "📞 Sent from a phone call with Shubh.\n\nThis is a DEMO session created by the voice service so you can see how a phone job appears in Sani. It is safe to delete.\n\nTask:\n• Build a simple SaaS-style cookie website");
    await j.handle.done; await new Promise((r) => setTimeout(r, 300));
    console.log(`REAL: wrote demo session ${j.sessionId} into the Sani history. In the Sani app it appears as "Voice: (demo, safe to delete)…".`);
    real.close();
  }
}
h.close(); core.close();
console.log(`\n${fails ? fails + " FAILED" : "ALL PASSED"}`);
setTimeout(() => process.exit(fails ? 1 : 0), 1500);
