"""Embedded SQLite run registry: identity, atomic claim, action ledger.

Sani master doc sections 2-5/15: run metadata (``run_registry``,
``action_ledger``, ``desktop_lease``) lives in the SAME embedded ``sani.db``
as agent memory, with no PostgreSQL server. This module is a semantic port of
:mod:`assistant.runtime.runs` to the SQLite dialect; the public surface
(``claim``/``finish``/``get_run``/``record_action``/``mark_action``/
``run_actions``/``actions_in_state``/``run_activity`` plus the desktop lease)
and the dataclasses (``ClaimResult``, ``RunRecord``) are shared with the
Postgres module so callers can swap backends unchanged.

Port decisions:

- Timestamps are unixepoch REAL (float seconds since epoch, UTC), sampled in
  Python. This matches ``RunRecord.updated_at: float`` (Postgres
  ``EXTRACT(EPOCH FROM updated_at)``) and the lease's ``DOUBLE PRECISION``
  ``expires_at``; ``run_activity`` therefore reports epoch floats where the
  Postgres module reported timezone-aware datetimes.
- Tables are created fresh with the final column set (the Postgres
  ``ALTER TABLE ... ADD COLUMN`` statements existed only to migrate legacy PG
  databases); the schema version marker is ``PRAGMA user_version``.
- ``claim`` keeps the same ``(user_id, agent_id, user_message_id)`` unique
  index for cross-connection dedup, and the multi-statement claim sequence is
  serialized in-process behind a single-writer :class:`asyncio.Lock` because
  SQLite has no ``FOR UPDATE SKIP LOCKED``. Single-statement writes stay
  lock-free: they are atomic in SQLite by themselves.
- ``RETURNING`` (SQLite >= 3.35) is used exactly where the Postgres module
  uses it, and the lease's conditional takeover is an
  ``INSERT ... ON CONFLICT ... DO UPDATE ... WHERE`` upsert.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from assistant.runtime.runs import ActionState, ClaimResult, RunRecord

_SCHEMA_VERSION = 1

_RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_registry (
    run_id          TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    chat_id         TEXT NOT NULL,
    user_message_id TEXT NOT NULL,
    request_digest  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'accepted',
    owner           TEXT,
    agent_id        TEXT NOT NULL DEFAULT '',
    revision_id     TEXT NOT NULL DEFAULT '',
    attempt         INTEGER NOT NULL DEFAULT 1,
    failure_reason  TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS run_registry_turn_agent_idx
    ON run_registry (user_id, agent_id, user_message_id);
CREATE TABLE IF NOT EXISTS action_ledger (
    ledger_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES run_registry(run_id),
    step_id      TEXT NOT NULL,
    tool_name    TEXT NOT NULL,
    target_desc  TEXT NOT NULL DEFAULT '',
    args_digest  TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'planned',
    evidence_ref TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS action_ledger_run_idx ON action_ledger(run_id);
"""

_LEASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS desktop_lease (
    id         INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    owner      TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""


class SQLiteRunStore:
    """SQLite-backed run registry + action ledger over one embedded file.

    Async surface mirrors :class:`assistant.runtime.runs.RunStore`; blocking
    SQLite calls run on worker threads via :func:`asyncio.to_thread`.
    ``acquire``/``release``/``current`` surface the embedded desktop lease on
    the store itself (the Postgres module exposes the same lease row through
    the separate :class:`SQLiteDesktopLease` class, which is also ported).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()
        self._lease = SQLiteDesktopLease(conn)

    @classmethod
    async def connect(cls, db_path: str) -> SQLiteRunStore:
        """Open and PRAGMA-configure the embedded database at ``db_path``."""
        path = Path(db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await asyncio.to_thread(cls._connect_sync, str(path))
        return cls(conn)

    @staticmethod
    def _connect_sync(path: str) -> sqlite3.Connection:
        # autocommit=True mirrors psycopg's autocommit=True in RunStore: each
        # statement is its own transaction, exactly like the Postgres module.
        conn = sqlite3.connect(path, check_same_thread=False, autocommit=True)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def setup(self) -> None:
        """Create tables/index if missing (idempotent, never deletes rows)."""
        async with self._lock:
            await asyncio.to_thread(self._setup_sync)

    def _setup_sync(self) -> None:
        self._conn.executescript(_RUN_SCHEMA)
        self._conn.executescript(_LEASE_SCHEMA)
        self._conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)

    # -- run registry ---------------------------------------------------------

    async def claim(
        self,
        *,
        user_id: str,
        chat_id: str,
        user_message_id: str,
        request_digest: str,
        run_id: str,
        agent_id: str = "",
        revision_id: str = "",
    ) -> ClaimResult:
        """Atomically claim execution for one user turn (master plan 11.1).

        The INSERT itself is the claim: the unique index on
        ``(user_id, agent_id, user_message_id)`` makes concurrent duplicate
        deliveries resolve to exactly one execution. Same text with a new id
        is a new intentional command; same id with a conflicting digest is an
        identity collision and is rejected.

        Locking port: Postgres relied on that unique index plus READ
        COMMITTED snapshots across connections (no SKIP LOCKED needed). The
        port keeps the same unique index -- so cross-connection dedup still
        holds -- and serializes this multi-statement sequence in-process
        behind ``self._lock``, which replaces snapshot separation with
        deterministic single-writer ordering. A duplicate delivery therefore
        cannot double-start: either its INSERT loses the unique race (it
        observes the winner) or, for a terminal-failure retry, its
        conditional UPDATE loses the takeover race and dedups.
        """
        async with self._lock:
            return await asyncio.to_thread(
                self._claim_sync,
                user_id,
                chat_id,
                user_message_id,
                request_digest,
                run_id,
                agent_id,
                revision_id,
            )

    def _claim_sync(
        self,
        user_id: str,
        chat_id: str,
        user_message_id: str,
        request_digest: str,
        run_id: str,
        agent_id: str,
        revision_id: str,
    ) -> ClaimResult:
        now = time.time()
        # One statement first, not overlapping transaction contexts; the
        # targetless ON CONFLICT catches every unique conflict, including a
        # proposed run_id that already belongs to an unrelated turn.
        inserted = self._conn.execute(
            """
            INSERT INTO run_registry
                (run_id, user_id, chat_id, user_message_id, request_digest,
                 status, agent_id, revision_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            RETURNING run_id, status
            """,
            (
                run_id,
                user_id,
                chat_id,
                user_message_id,
                request_digest,
                agent_id,
                revision_id,
                now,
                now,
            ),
        ).fetchone()
        if inserted is not None:
            return ClaimResult(owned=True, run_id=str(inserted[0]), status=str(inserted[1]))

        # A separate statement reads the winning row after the competing
        # INSERT committed (same reason the Postgres module re-SELECTs).
        row = self._conn.execute(
            """
            SELECT run_id, status, request_digest FROM run_registry
            WHERE user_id = ? AND agent_id = ? AND user_message_id = ?
            """,
            (user_id, agent_id, user_message_id),
        ).fetchone()
        if row is None:  # e.g. run_id collision with an unrelated turn: fail closed
            return ClaimResult(owned=False, run_id=run_id, reason="registry_error")
        existing_run_id, status, digest = str(row[0]), str(row[1]), str(row[2])
        if digest != request_digest:
            return ClaimResult(
                owned=False, run_id=existing_run_id, status=status, reason="identity_conflict"
            )
        if status in ("failed", "cancelled"):
            # A retry after a TERMINAL failure/cancellation is a new
            # intentional action (Open WebUI regeneration reuses the same
            # user-message id). The conditional UPDATE is the atomic
            # takeover: a concurrent retry loses (status no longer terminal)
            # and dedups.
            retried = self._conn.execute(
                """
                UPDATE run_registry SET status='running', updated_at=?, failure_reason=''
                WHERE run_id=? AND status IN ('failed', 'cancelled')
                RETURNING run_id
                """,
                (time.time(), existing_run_id),
            ).fetchone()
            if retried is not None:
                return ClaimResult(
                    owned=True,
                    run_id=existing_run_id,
                    status="running",
                    reason="retry_after_terminal_failure",
                )
        return ClaimResult(
            owned=False, run_id=existing_run_id, status=status, reason="already_claimed"
        )

    async def finish(self, run_id: str, status: str, failure_reason: str = "") -> None:
        """Mark terminal status ('completed'|'failed'|'cancelled').

        ``failure_reason`` (WP4 diagnostics): bounded tool/recipe error text
        for failed runs -- never prompt bodies. Empty for successes.
        """
        await asyncio.to_thread(self._finish_sync, run_id, status, failure_reason)

    def _finish_sync(self, run_id: str, status: str, failure_reason: str) -> None:
        self._conn.execute(
            "UPDATE run_registry SET status=?, owner=NULL, updated_at=?, "
            "failure_reason=? WHERE run_id=?",
            (status, time.time(), failure_reason[:300], run_id),
        )

    async def get_run(self, run_id: str) -> RunRecord | None:
        return await asyncio.to_thread(self._get_run_sync, run_id)

    def _get_run_sync(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT run_id, user_id, chat_id, user_message_id, request_digest, status, owner, "
            "updated_at, failure_reason FROM run_registry WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        record = RunRecord(
            run_id=str(row[0]),
            user_id=str(row[1]),
            chat_id=str(row[2]),
            user_message_id=str(row[3]),
            request_digest=str(row[4]),
            status=str(row[5]),
            owner=row[6] if row[6] is None else str(row[6]),
            updated_at=float(row[7] or 0.0),
            failure_reason=str(row[8] or ""),
        )
        record.actions = self._run_actions_sync(run_id)
        return record

    # -- action ledger --------------------------------------------------------

    async def record_action(
        self,
        run_id: str,
        step_id: str,
        tool_name: str,
        *,
        target_desc: str = "",
        args_digest: str = "",
    ) -> int:
        """Insert a 'planned' row BEFORE dispatch (master plan 11.2)."""
        return await asyncio.to_thread(
            self._record_action_sync, run_id, step_id, tool_name, target_desc, args_digest
        )

    def _record_action_sync(
        self,
        run_id: str,
        step_id: str,
        tool_name: str,
        target_desc: str,
        args_digest: str,
    ) -> int:
        now = time.time()
        row = self._conn.execute(
            """
            INSERT INTO action_ledger
                (run_id, step_id, tool_name, target_desc, args_digest, state,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'planned', ?, ?)
            RETURNING ledger_id
            """,
            (run_id, step_id, tool_name, target_desc, args_digest, now, now),
        ).fetchone()
        assert row is not None  # RETURNING always yields the inserted ledger_id
        return int(row[0])

    async def mark_action(self, ledger_id: int, state: ActionState, evidence_ref: str = "") -> None:
        await asyncio.to_thread(self._mark_action_sync, ledger_id, state, evidence_ref)

    def _mark_action_sync(self, ledger_id: int, state: ActionState, evidence_ref: str) -> None:
        self._conn.execute(
            "UPDATE action_ledger SET state=?, evidence_ref=?, updated_at=? WHERE ledger_id=?",
            (state, evidence_ref, time.time(), ledger_id),
        )

    async def run_actions(self, run_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._run_actions_sync, run_id)

    def _run_actions_sync(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT step_id, tool_name, target_desc, args_digest, state, evidence_ref "
            "FROM action_ledger WHERE run_id=? ORDER BY ledger_id",
            (run_id,),
        ).fetchall()
        return [
            {
                "step_id": r[0],
                "tool_name": r[1],
                "target_desc": r[2],
                "args_digest": r[3],
                "state": r[4],
                "evidence_ref": r[5],
            }
            for r in rows
        ]

    async def actions_in_state(self, run_id: str, state: str) -> list[dict[str, Any]]:
        return [a for a in await self.run_actions(run_id) if str(a.get("state")) == state]

    async def run_activity(self, run_id: str) -> dict[str, Any] | None:
        """One safe activity snapshot for external run-event streaming.

        Read-only view over the same registry/ledger rows: run status plus
        every action row with its timestamps. Contains no payloads, prompts,
        or tool arguments -- only tool names, bounded target descriptions,
        and states. Timestamps are epoch floats (see module docstring).
        """
        # Two statements: hold the single-writer lock for one consistent view.
        async with self._lock:
            return await asyncio.to_thread(self._run_activity_sync, run_id)

    def _run_activity_sync(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT status, created_at, updated_at, failure_reason "
            "FROM run_registry WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        status, created_at, updated_at, failure_reason = row
        actions = self._conn.execute(
            "SELECT step_id, tool_name, target_desc, state, created_at, updated_at "
            "FROM action_ledger WHERE run_id=? ORDER BY ledger_id",
            (run_id,),
        ).fetchall()
        return {
            "run_id": run_id,
            "status": str(status),
            "created_at": created_at,
            "updated_at": updated_at,
            "failure_reason": str(failure_reason or ""),
            "actions": [
                {
                    "step_id": str(a[0]),
                    "tool_name": str(a[1]),
                    "target_desc": str(a[2]),
                    "state": str(a[3]),
                    "created_at": a[4],
                    "updated_at": a[5],
                }
                for a in actions
            ],
        }

    # -- desktop lease --------------------------------------------------------

    async def acquire(self, owner: str, *, ttl_seconds: float | None = None) -> bool:
        """Try to hold the singleton desktop lease (default TTL 300s)."""
        return await self._lease.acquire(owner, ttl_seconds=ttl_seconds)

    async def release(self, owner: str) -> None:
        await self._lease.release(owner)

    async def current(self) -> str | None:
        return await self._lease.current()


class SQLiteDesktopLease:
    """Atomic lease primitive, not production desktop fencing (WP4/11.3).

    Row 1 of ``desktop_lease`` holds (owner, expires_at). An expired lease is
    stealable; an active lease blocks acquisition by another owner. Release
    is owner-checked; callers must use a unique owner per holder. Expiry
    cannot stop a paused/stale holder from acting: do NOT use this alone to
    guarantee exclusive desktop effects or wire it to dispatch.
    """

    def __init__(self, conn: sqlite3.Connection, *, ttl_seconds: float = 300.0) -> None:
        self._validate_ttl(ttl_seconds)
        self._conn = conn
        self.ttl_seconds = ttl_seconds
        self._owner: str | None = None

    @staticmethod
    def _validate_ttl(ttl: float) -> None:
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("Lease TTL must be finite and positive")

    async def acquire(self, owner: str, *, ttl_seconds: float | None = None) -> bool:
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        self._validate_ttl(ttl)
        return await asyncio.to_thread(self._acquire_sync, owner, ttl)

    def _acquire_sync(self, owner: str, ttl: float) -> bool:
        # One conditional upsert is the whole primitive: same owner refreshes,
        # an expired lease is stealable, an active foreign lease blocks (no
        # RETURNING row -> False). The Postgres module samples the database
        # clock inside the statement; here Python's clock is sampled once per
        # call, equivalent at lease granularity.
        now = time.time()
        row = self._conn.execute(
            """
            INSERT INTO desktop_lease (id, owner, expires_at)
            VALUES (1, ?, ?)
            ON CONFLICT (id) DO UPDATE
            SET owner = excluded.owner,
                expires_at = excluded.expires_at
            WHERE desktop_lease.owner = excluded.owner
               OR desktop_lease.expires_at <= ?
            RETURNING owner
            """,
            (owner, now + ttl, now),
        ).fetchone()
        if row is None:
            return False
        self._owner = str(row[0])
        return self._owner == owner

    async def release(self, owner: str) -> None:
        await asyncio.to_thread(self._release_sync, owner)

    def _release_sync(self, owner: str) -> None:
        self._conn.execute("DELETE FROM desktop_lease WHERE id=1 AND owner=?", (owner,))
        if self._owner == owner:
            self._owner = None

    async def current(self) -> str | None:
        return await asyncio.to_thread(self._current_sync)

    def _current_sync(self) -> str | None:
        row = self._conn.execute("SELECT owner FROM desktop_lease WHERE id=1").fetchone()
        return str(row[0]) if row else None
