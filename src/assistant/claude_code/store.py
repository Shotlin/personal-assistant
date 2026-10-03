"""Which Claude Code session belongs to which Sani chat, and the last usage seen.

Stored in Sani's own SQLite file so a chat resumes the same Claude Code session
after a restart. It holds ids and numbers only: no prompts, no output.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claude_code_sessions (
    conversation TEXT NOT NULL,
    project TEXT NOT NULL,
    session_id TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (conversation, project)
);
CREATE TABLE IF NOT EXISTS claude_code_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class SessionStore:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._ready = False
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        if not self._ready:
            conn.executescript(_SCHEMA)
            self._ready = True
        return conn

    async def _run(self, fn: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn)

    async def get_session(self, conversation: str, project: str) -> str:
        def work() -> str:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT session_id FROM claude_code_sessions"
                    " WHERE conversation=? AND project=?",
                    (conversation, project),
                ).fetchone()
                return str(row[0]) if row else ""

        return str(await self._run(work))

    async def set_session(self, conversation: str, project: str, session_id: str) -> None:
        def work() -> None:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "INSERT INTO claude_code_sessions"
                    "(conversation, project, session_id, updated_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(conversation, project) DO UPDATE SET"
                    " session_id=excluded.session_id, updated_at=excluded.updated_at",
                    (conversation, project, session_id, time.time()),
                )

        await self._run(work)

    async def forget(self, conversation: str, project: str) -> None:
        def work() -> None:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "DELETE FROM claude_code_sessions WHERE conversation=? AND project=?",
                    (conversation, project),
                )

        await self._run(work)

    async def put_state(self, key: str, value: dict[str, Any]) -> None:
        def work() -> None:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "INSERT INTO claude_code_state(key, value, updated_at) VALUES (?,?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                    " updated_at=excluded.updated_at",
                    (key, json.dumps(value), time.time()),
                )

        await self._run(work)

    async def get_state(self, key: str) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT value FROM claude_code_state WHERE key=?", (key,)
                ).fetchone()
                if not row:
                    return {}
                try:
                    value = json.loads(row[0])
                except json.JSONDecodeError:
                    return {}
                return value if isinstance(value, dict) else {}

        return dict(await self._run(work))
