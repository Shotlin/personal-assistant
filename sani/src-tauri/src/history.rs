//! Sani-local conversation history (SQLite).
//!
//! UI history only -- the agent's real memory lives in the existing backend.
//! Raw microphone audio is never stored.

use parking_lot::Mutex;
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::Arc;

#[derive(Debug, Clone, Serialize)]
pub struct Conversation {
    pub id: String,
    pub title: String,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct StoredMessage {
    pub id: String,
    pub conversation_id: String,
    pub role: String,
    pub text: String,
    pub created_at: i64,
    pub run_id: Option<String>,
    /// Which registered agent produced an assistant message. NULL on user
    /// messages and on history written before attribution existed -- that is
    /// rendered as a neutral Sani, never guessed at.
    pub agent_id: Option<String>,
    pub agent_name: Option<String>,
    /// C08/N10: the durable mission this turn belongs to, when the run was
    /// mission-backed. Persisted so history/reopen retains correlation and
    /// the renderer can show truthful mission status without guessing.
    pub mission_id: Option<String>,
}

/// Non-secret operational metadata for one agent run. Transcript text, audio,
/// screenshots, clipboard contents, and credentials are deliberately absent.
#[derive(Debug, Clone, Serialize)]
pub struct ActivityRecord {
    pub sequence: i64,
    pub conversation_id: String,
    pub run_id: String,
    pub agent_id: String,
    pub event_type: String,
    pub timestamp: i64,
    pub label: String,
    pub status: String,
    /// Stable id of the step this row finishes (a tool call), so the renderer
    /// can match it to the live "running" row it replaces.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub step_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<i64>,
    /// Already screened for secrets and capped by the core and the host; shown
    /// only behind the quiet "Technical details" affordance.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TimingRecord {
    pub run_id: String,
    pub stage: String,
    pub elapsed_ms: i64,
    pub status: String,
}

/// Provenance recorded alongside one stored message.
#[derive(Clone, Default)]
pub struct Attribution<'a> {
    pub run_id: Option<&'a str>,
    pub agent_id: Option<&'a str>,
    pub agent_name: Option<&'a str>,
    pub mission_id: Option<&'a str>,
}

pub struct History {
    conn: Mutex<Connection>,
}

pub type SharedHistory = Arc<History>;

/// Add a column to `messages` only if it is missing, so reopening an existing
/// database is a no-op and never rewrites or drops rows.
fn ensure_column(conn: &Connection, name: &str, declaration: &str) -> Result<(), String> {
    ensure_table_column(conn, "messages", name, declaration)
}

/// Add a column in place when an older database lacks it. `table` is always a
/// literal from this file, never user input.
fn ensure_table_column(
    conn: &Connection,
    table: &str,
    name: &str,
    declaration: &str,
) -> Result<(), String> {
    let mut stmt = conn
        .prepare(&format!(
            "SELECT 1 FROM pragma_table_info('{table}') WHERE name = ?1"
        ))
        .map_err(|e| e.to_string())?;
    let present: bool = stmt.exists([name]).map_err(|e| e.to_string())?;
    if !present {
        conn.execute(
            &format!("ALTER TABLE {table} ADD COLUMN {name} {declaration}"),
            [],
        )
        .map_err(|e| e.to_string())?;
        log::info!("[history] migrated: added {table}.{name}");
    }
    Ok(())
}

impl History {
    pub fn open(db_path: PathBuf) -> Result<Self, String> {
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let conn = Connection::open(&db_path).map_err(|e| e.to_string())?;
        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             CREATE TABLE IF NOT EXISTS conversations (
                 id         TEXT PRIMARY KEY,
                 title      TEXT NOT NULL DEFAULT 'New conversation',
                 created_at INTEGER NOT NULL,
                 updated_at INTEGER NOT NULL
             );
             CREATE TABLE IF NOT EXISTS messages (
                 id              TEXT PRIMARY KEY,
                 conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                 role            TEXT NOT NULL,
                 text            TEXT NOT NULL,
                 created_at      INTEGER NOT NULL,
                 run_id          TEXT,
                 agent_id        TEXT,
                 agent_name      TEXT
             );
             CREATE INDEX IF NOT EXISTS messages_conversation_idx
                 ON messages (conversation_id, created_at);
             CREATE TABLE IF NOT EXISTS run_activity (
                 sequence INTEGER PRIMARY KEY,
                 conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                 run_id TEXT NOT NULL,
                 agent_id TEXT NOT NULL,
                 event_type TEXT NOT NULL,
                 timestamp INTEGER NOT NULL,
                 label TEXT NOT NULL,
                 status TEXT NOT NULL
             );
             CREATE INDEX IF NOT EXISTS run_activity_conversation_idx
                 ON run_activity (conversation_id, timestamp, sequence);
             CREATE INDEX IF NOT EXISTS run_activity_run_idx
                 ON run_activity (run_id, sequence);
             CREATE TABLE IF NOT EXISTS run_timing (
                 run_id TEXT NOT NULL, stage TEXT NOT NULL, elapsed_ms INTEGER NOT NULL,
                 status TEXT NOT NULL, PRIMARY KEY (run_id, stage)
             );",
        )
        .map_err(|e| e.to_string())?;
        // Databases created before multi-agent attribution lack these columns.
        // Adding them in place keeps every existing row valid: an old assistant
        // message simply has no agent and renders under the neutral Sani mark.
        ensure_column(&conn, "agent_id", "TEXT")?;
        ensure_column(&conn, "agent_name", "TEXT")?;
        // C08/N10: mission correlation on the message row itself, so history
        // and reopen keep showing the same mission truth the live run had.
        ensure_column(&conn, "mission_id", "TEXT")?;
        // Steps with a duration and detail (Claude Code companion). Rows written
        // before this simply have none of them and render as plain activity.
        ensure_table_column(&conn, "run_activity", "step_id", "TEXT")?;
        ensure_table_column(&conn, "run_activity", "tool", "TEXT")?;
        ensure_table_column(&conn, "run_activity", "duration_ms", "INTEGER")?;
        ensure_table_column(&conn, "run_activity", "detail", "TEXT")?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    pub fn create_conversation(&self, id: &str, title: &str, now: i64) -> Result<(), String> {
        let conn = self.conn.lock();
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?1, ?2, ?3, ?3)",
            params![id, title, now],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn list_conversations(&self) -> Result<Vec<Conversation>, String> {
        let conn = self.conn.lock();
        let mut stmt = conn
            .prepare("SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT 100")
            .map_err(|e| e.to_string())?;
        let rows = stmt
            .query_map([], |row| {
                Ok(Conversation {
                    id: row.get(0)?,
                    title: row.get(1)?,
                    created_at: row.get(2)?,
                    updated_at: row.get(3)?,
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())
    }

    pub fn get_conversation(&self, id: &str) -> Result<Option<Conversation>, String> {
        let conn = self.conn.lock();
        conn.query_row(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?1",
            params![id],
            |row| {
                Ok(Conversation {
                    id: row.get(0)?,
                    title: row.get(1)?,
                    created_at: row.get(2)?,
                    updated_at: row.get(3)?,
                })
            },
        )
        .optional()
        .map_err(|e| e.to_string())
    }

    pub fn rename_conversation(&self, id: &str, title: &str, now: i64) -> Result<(), String> {
        let conn = self.conn.lock();
        conn.execute(
            "UPDATE conversations SET title = ?2, updated_at = ?3 WHERE id = ?1 AND title = 'New conversation'",
            params![id, title, now],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn touch_conversation(&self, id: &str, now: i64) -> Result<(), String> {
        let conn = self.conn.lock();
        conn.execute(
            "UPDATE conversations SET updated_at = ?2 WHERE id = ?1",
            params![id, now],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn delete_conversation(&self, id: &str) -> Result<(), String> {
        let conn = self.conn.lock();
        conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?1",
            params![id],
        )
        .map_err(|e| e.to_string())?;
        conn.execute("DELETE FROM conversations WHERE id = ?1", params![id])
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn append_message(
        &self,
        id: &str,
        conversation_id: &str,
        role: &str,
        text: &str,
        now: i64,
        attribution: &Attribution<'_>,
    ) -> Result<(), String> {
        let conn = self.conn.lock();
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, text, created_at, run_id, agent_id, agent_name, mission_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                id,
                conversation_id,
                role,
                text,
                now,
                attribution.run_id,
                attribution.agent_id,
                attribution.agent_name,
                attribution.mission_id
            ],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn messages(&self, conversation_id: &str) -> Result<Vec<StoredMessage>, String> {
        let conn = self.conn.lock();
        let mut stmt = conn
            .prepare(
                "SELECT id, conversation_id, role, text, created_at, run_id, agent_id, agent_name, mission_id
                 FROM messages
                 WHERE conversation_id = ?1 ORDER BY created_at ASC, rowid ASC",
            )
            .map_err(|e| e.to_string())?;
        let rows = stmt
            .query_map(params![conversation_id], |row| {
                Ok(StoredMessage {
                    id: row.get(0)?,
                    conversation_id: row.get(1)?,
                    role: row.get(2)?,
                    text: row.get(3)?,
                    created_at: row.get(4)?,
                    run_id: row.get(5)?,
                    agent_id: row.get(6)?,
                    agent_name: row.get(7)?,
                    mission_id: row.get(8)?,
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())
    }

    pub fn append_activity(&self, record: &ActivityRecord) -> Result<i64, String> {
        // The sequence is assigned HERE, from the table's own maximum: a
        // process-local counter restarts at 1 on every app restart and then
        // collides with persisted rows (their PRIMARY KEY rejects the
        // insert), silently dropping all activity from that session. MAX+1
        // under the connection mutex is monotonic across restarts.
        let conn = self.conn.lock();
        conn.execute(
            "INSERT INTO run_activity (sequence, conversation_id, run_id, agent_id, event_type, timestamp, label, status, step_id, tool, duration_ms, detail)
             VALUES ((SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_activity), ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![record.conversation_id, record.run_id, record.agent_id, record.event_type, record.timestamp, record.label, record.status, record.step_id, record.tool, record.duration_ms, record.detail],
        ).map_err(|e| e.to_string())?;
        Ok(conn.last_insert_rowid())
    }

    pub fn activity(&self, conversation_id: &str) -> Result<Vec<ActivityRecord>, String> {
        let conn = self.conn.lock();
        let mut stmt = conn.prepare("SELECT sequence, conversation_id, run_id, agent_id, event_type, timestamp, label, status, step_id, tool, duration_ms, detail FROM run_activity WHERE conversation_id = ?1 ORDER BY sequence ASC").map_err(|e| e.to_string())?;
        let rows = stmt
            .query_map(params![conversation_id], |row| {
                Ok(ActivityRecord {
                    sequence: row.get(0)?,
                    conversation_id: row.get(1)?,
                    run_id: row.get(2)?,
                    agent_id: row.get(3)?,
                    event_type: row.get(4)?,
                    timestamp: row.get(5)?,
                    label: row.get(6)?,
                    status: row.get(7)?,
                    step_id: row.get(8)?,
                    tool: row.get(9)?,
                    duration_ms: row.get(10)?,
                    detail: row.get(11)?,
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())
    }

    /// Retention deliberately targets execution metadata only. Conversations
    /// and messages (the user's chat record) are never part of this query.
    pub fn prune_activity_before(&self, cutoff_ms: i64) -> Result<usize, String> {
        let conn = self.conn.lock();
        conn.execute(
            "DELETE FROM run_activity WHERE timestamp < ?1",
            params![cutoff_ms],
        )
        .map_err(|e| e.to_string())
    }

    pub fn clear_activity(&self) -> Result<usize, String> {
        let conn = self.conn.lock();
        conn.execute("DELETE FROM run_activity", [])
            .map_err(|e| e.to_string())
    }

    pub fn append_timing(&self, record: &TimingRecord) -> Result<(), String> {
        self.conn.lock().execute("INSERT OR IGNORE INTO run_timing (run_id, stage, elapsed_ms, status) VALUES (?1, ?2, ?3, ?4)", params![record.run_id, record.stage, record.elapsed_ms, record.status]).map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn recent_timing(&self) -> Result<Vec<TimingRecord>, String> {
        let conn = self.conn.lock();
        let mut stmt = conn.prepare("SELECT run_id, stage, elapsed_ms, status FROM run_timing ORDER BY rowid DESC LIMIT 100").map_err(|e| e.to_string())?;
        let rows = stmt
            .query_map([], |row| {
                Ok(TimingRecord {
                    run_id: row.get(0)?,
                    stage: row.get(1)?,
                    elapsed_ms: row.get(2)?,
                    status: row.get(3)?,
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())
    }
}

/// Derive a short conversation title from the first user utterance.
pub fn title_from_text(text: &str) -> String {
    let cleaned = text.trim().replace('\n', " ");
    let mut title: String = cleaned.chars().take(48).collect();
    if cleaned.chars().count() > 48 {
        title.push('…');
    }
    if title.is_empty() {
        "New conversation".into()
    } else {
        title
    }
}

#[cfg(test)]
mod step_tests {
    use super::*;

    fn record(step_id: Option<&str>, detail: Option<&str>) -> ActivityRecord {
        ActivityRecord {
            sequence: 0,
            conversation_id: "c1".into(),
            run_id: "r1".into(),
            agent_id: "deep".into(),
            event_type: "agent.step".into(),
            timestamp: 10,
            label: "Edited src/a.ts".into(),
            status: "complete".into(),
            step_id: step_id.map(String::from),
            tool: Some("Edit".into()),
            duration_ms: Some(840),
            detail: detail.map(String::from),
        }
    }

    #[test]
    fn a_step_round_trips_with_its_duration_and_detail() {
        let dir = std::env::temp_dir().join(format!("sani-hist-{}", uuid::Uuid::new_v4()));
        let history = History::open(dir.join("h.db")).expect("open");
        history.create_conversation("c1", "t", 1).expect("conv");
        history
            .append_activity(&record(Some("cc:1"), Some("- a\n+ b")))
            .expect("append");
        let rows = history.activity("c1").expect("rows");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].step_id.as_deref(), Some("cc:1"));
        assert_eq!(rows[0].duration_ms, Some(840));
        assert_eq!(rows[0].detail.as_deref(), Some("- a\n+ b"));
    }

    #[test]
    fn a_database_from_before_steps_is_upgraded_in_place() {
        let dir = std::env::temp_dir().join(format!("sani-hist-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).expect("dir");
        let path = dir.join("old.db");
        {
            let conn = Connection::open(&path).expect("raw open");
            conn.execute_batch(
                "CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
                 CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL);
                 CREATE TABLE run_activity (sequence INTEGER PRIMARY KEY,
                    conversation_id TEXT NOT NULL, run_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, timestamp INTEGER NOT NULL, label TEXT NOT NULL,
                    status TEXT NOT NULL);
                 INSERT INTO conversations VALUES ('c1', 't', 1, 1);
                 INSERT INTO run_activity VALUES (1, 'c1', 'r0', 'velo', 'agent.progress', 5,
                    'Opened Chrome', 'info');",
            )
            .expect("old schema");
        }
        let history = History::open(path).expect("upgrade");
        let rows = history.activity("c1").expect("rows");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].label, "Opened Chrome");
        assert!(rows[0].step_id.is_none() && rows[0].detail.is_none());
        history
            .append_activity(&record(Some("cc:2"), None))
            .expect("new row");
        assert_eq!(history.activity("c1").expect("rows").len(), 2);
    }
}
