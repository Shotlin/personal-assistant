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
}

pub struct History {
    conn: Mutex<Connection>,
}

pub type SharedHistory = Arc<History>;

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
                 run_id          TEXT
             );
             CREATE INDEX IF NOT EXISTS messages_conversation_idx
                 ON messages (conversation_id, created_at);",
        )
        .map_err(|e| e.to_string())?;
        Ok(Self { conn: Mutex::new(conn) })
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
        rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
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
        conn.execute("DELETE FROM messages WHERE conversation_id = ?1", params![id])
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
        run_id: Option<&str>,
    ) -> Result<(), String> {
        let conn = self.conn.lock();
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, text, created_at, run_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![id, conversation_id, role, text, now, run_id],
        )
        .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn messages(&self, conversation_id: &str) -> Result<Vec<StoredMessage>, String> {
        let conn = self.conn.lock();
        let mut stmt = conn
            .prepare(
                "SELECT id, conversation_id, role, text, created_at, run_id FROM messages
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
                })
            })
            .map_err(|e| e.to_string())?;
        rows.collect::<Result<Vec<_>, _>>().map_err(|e| e.to_string())
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
