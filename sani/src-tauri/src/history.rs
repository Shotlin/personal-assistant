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
}

/// Provenance recorded alongside one stored message.
#[derive(Clone, Default)]
pub struct Attribution<'a> {
    pub run_id: Option<&'a str>,
    pub agent_id: Option<&'a str>,
    pub agent_name: Option<&'a str>,
}

pub struct History {
    conn: Mutex<Connection>,
}

pub type SharedHistory = Arc<History>;

/// Add a column to `messages` only if it is missing, so reopening an existing
/// database is a no-op and never rewrites or drops rows.
fn ensure_column(conn: &Connection, name: &str, declaration: &str) -> Result<(), String> {
    let mut stmt = conn
        .prepare("SELECT 1 FROM pragma_table_info('messages') WHERE name = ?1")
        .map_err(|e| e.to_string())?;
    let present: bool = stmt.exists([name]).map_err(|e| e.to_string())?;
    if !present {
        conn.execute(
            &format!("ALTER TABLE messages ADD COLUMN {name} {declaration}"),
            [],
        )
        .map_err(|e| e.to_string())?;
        log::info!("[history] migrated: added messages.{name}");
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
                 ON run_activity (run_id, sequence);",
        )
        .map_err(|e| e.to_string())?;
        // Databases created before multi-agent attribution lack these columns.
        // Adding them in place keeps every existing row valid: an old assistant
        // message simply has no agent and renders under the neutral Sani mark.
        ensure_column(&conn, "agent_id", "TEXT")?;
        ensure_column(&conn, "agent_name", "TEXT")?;
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
            "INSERT INTO messages (id, conversation_id, role, text, created_at, run_id, agent_id, agent_name)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                id,
                conversation_id,
                role,
                text,
                now,
                attribution.run_id,
                attribution.agent_id,
                attribution.agent_name
            ],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn messages(&self, conversation_id: &str) -> Result<Vec<StoredMessage>, String> {
        let conn = self.conn.lock();
        let mut stmt = conn
            .prepare(
                "SELECT id, conversation_id, role, text, created_at, run_id, agent_id, agent_name
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
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())
    }

    pub fn append_activity(&self, record: &ActivityRecord) -> Result<(), String> {
        let conn = self.conn.lock();
        conn.execute(
            "INSERT INTO run_activity (sequence, conversation_id, run_id, agent_id, event_type, timestamp, label, status)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![record.sequence, record.conversation_id, record.run_id, record.agent_id, record.event_type, record.timestamp, record.label, record.status],
        ).map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn activity(&self, conversation_id: &str) -> Result<Vec<ActivityRecord>, String> {
        let conn = self.conn.lock();
        let mut stmt = conn.prepare("SELECT sequence, conversation_id, run_id, agent_id, event_type, timestamp, label, status FROM run_activity WHERE conversation_id = ?1 ORDER BY sequence ASC").map_err(|e| e.to_string())?;
        let rows = stmt.query_map(params![conversation_id], |row| Ok(ActivityRecord { sequence: row.get(0)?, conversation_id: row.get(1)?, run_id: row.get(2)?, agent_id: row.get(3)?, event_type: row.get(4)?, timestamp: row.get(5)?, label: row.get(6)?, status: row.get(7)? })).map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
    }

    /// Retention deliberately targets execution metadata only. Conversations
    /// and messages (the user's chat record) are never part of this query.
    pub fn prune_activity_before(&self, cutoff_ms: i64) -> Result<usize, String> {
        let conn = self.conn.lock();
        conn.execute("DELETE FROM run_activity WHERE timestamp < ?1", params![cutoff_ms])
            .map_err(|e| e.to_string())
    }

    pub fn clear_activity(&self) -> Result<usize, String> {
        let conn = self.conn.lock();
        conn.execute("DELETE FROM run_activity", []).map_err(|e| e.to_string())
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
