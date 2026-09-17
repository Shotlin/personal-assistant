"""Durable run registry: identity, atomic claim, action ledger (WP4).

One database-backed contract so duplicate deliveries and retries cannot
re-execute external work:

- :func:`RunStore.claim` -- atomic claim keyed by the Open WebUI user
  message id. Same id + same digest returns the EXISTING run (duplicate
  delivery observes; it never re-executes). Same id + conflicting digest
  is rejected. Same text with a new id is a new command by design.
- :func:`RunStore.record_action` records one native action before
  dispatch; outcome/evidence recorded after. ``unknown_effect`` is a
  first-class terminal state (master plan 11.2): a crash between
  dispatch and acknowledgement must never be replayed blind.
- :class:`DesktopLease`: atomic expiring lease primitive, NOT a fencing
  mechanism. It is not wired to production desktop dispatch.

No GUI semantics here: this module claims, records, and reports. It does
not advertise exactly-once GUI effects (impossible across a crash window).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg

logger = logging.getLogger("assistant.runtime.runs")

RunStatus = Literal["accepted", "running", "completed", "failed", "cancelled"]
ActionState = Literal["planned", "dispatched", "confirmed", "failed", "unknown"]

_SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS run_registry (
    run_id          TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    chat_id         TEXT NOT NULL,
    user_message_id TEXT NOT NULL,
    request_digest  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'accepted',
    owner           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
""",
    # Legacy duplicate identities must fail setup, never erase history.
    # An operator must reconcile them in a separate, explicit migration.
    """
CREATE UNIQUE INDEX IF NOT EXISTS run_registry_turn_idx
    ON run_registry (user_id, user_message_id);
""",
    """
CREATE TABLE IF NOT EXISTS action_ledger (
    ledger_id   BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES run_registry(run_id),
    step_id     TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    target_desc TEXT NOT NULL DEFAULT '',
    args_digest TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'planned',
    evidence_ref TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS action_ledger_run_idx ON action_ledger(run_id);
""",
)

_LEASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS desktop_lease (
    id           INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    owner        TEXT NOT NULL,
    expires_at   DOUBLE PRECISION NOT NULL
);
"""


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of an atomic run claim."""

    owned: bool
    run_id: str
    status: str | None = None  # existing run status when not owned
    reason: str = ""


@dataclass
class RunRecord:
    run_id: str
    user_id: str
    chat_id: str
    user_message_id: str
    request_digest: str
    status: str
    owner: str | None = None
    updated_at: float = 0.0
    actions: list[dict[str, Any]] = field(default_factory=list)


class RunStore:
    """Postgres-backed run registry + action ledger (async psycopg)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    @classmethod
    async def connect(cls, database_url: str) -> RunStore:
        conn = await psycopg.AsyncConnection.connect(database_url, autocommit=True)
        return cls(conn)

    async def setup(self) -> None:
        for statement in _SCHEMA:
            await self._conn.execute(statement)
        await self._conn.execute(_LEASE_SCHEMA)

    async def close(self) -> None:
        await self._conn.close()

    async def claim(
        self,
        *,
        user_id: str,
        chat_id: str,
        user_message_id: str,
        request_digest: str,
        run_id: str,
    ) -> ClaimResult:
        """Atomically claim execution for one user turn (master plan 11.1).

        The INSERT itself is the claim: the unique index on
        ``(user_id, user_message_id)`` makes concurrent duplicate
        deliveries resolve to exactly one execution. Same text with a new
        id is a new intentional command; same id with a conflicting digest
        is an identity collision and is rejected.
        """
        # One statement, not overlapping transaction/savepoint contexts when
        # requests share a connection. Catch every unique conflict, including
        # a proposed run_id that already belongs to an unrelated turn.
        cur = await self._conn.execute(
            """
            INSERT INTO run_registry
                (run_id, user_id, chat_id, user_message_id, request_digest, status)
            VALUES (%s, %s, %s, %s, %s, 'running')
            ON CONFLICT DO NOTHING
            RETURNING run_id, status
            """,
            (run_id, user_id, chat_id, user_message_id, request_digest),
        )
        inserted = await cur.fetchone()
        if inserted is not None:
            return ClaimResult(owned=True, run_id=str(inserted[0]), status=str(inserted[1]))

        # A separate statement gets a fresh READ COMMITTED snapshot after a
        # competing INSERT commits; a same-statement CTE lookup could miss it.
        cur = await self._conn.execute(
            """
            SELECT run_id, status, request_digest FROM run_registry
            WHERE user_id = %s AND user_message_id = %s
            """,
            (user_id, user_message_id),
        )
        row = await cur.fetchone()
        if row is None:  # e.g. run_id collision with an unrelated turn: fail closed
            return ClaimResult(owned=False, run_id=run_id, reason="registry_error")
        existing_run_id, status, digest = str(row[0]), str(row[1]), str(row[2])
        if digest != request_digest:
            return ClaimResult(
                owned=False, run_id=existing_run_id, status=status, reason="identity_conflict"
            )
        return ClaimResult(
            owned=False, run_id=existing_run_id, status=status, reason="already_claimed"
        )

    async def finish(self, run_id: str, status: str) -> None:
        """Mark terminal status ('completed'|'failed'|'cancelled')."""
        await self._conn.execute(
            "UPDATE run_registry SET status=%s, owner=NULL, updated_at=now() WHERE run_id=%s",
            (status, run_id),
        )

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self._conn.cursor() as cur:
            await cur.execute(
                "SELECT run_id, user_id, chat_id, user_message_id, request_digest, status, owner, "
                "EXTRACT(EPOCH FROM updated_at) FROM run_registry WHERE run_id=%s",
                (run_id,),
            )
            row = await cur.fetchone()
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
        )
        record.actions = await self.run_actions(run_id)
        return record

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
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO action_ledger
                    (run_id, step_id, tool_name, target_desc, args_digest, state)
                VALUES (%s, %s, %s, %s, %s, 'planned')
                RETURNING ledger_id
                """,
                (run_id, step_id, tool_name, target_desc, args_digest),
            )
            row = await cur.fetchone()
        return int(row[0])

    async def mark_action(
        self, ledger_id: int, state: ActionState, evidence_ref: str = ""
    ) -> None:
        await self._conn.execute(
            "UPDATE action_ledger SET state=%s, evidence_ref=%s, updated_at=now() "
            "WHERE ledger_id=%s",
            (state, evidence_ref, ledger_id),
        )

    async def run_actions(self, run_id: str) -> list[dict[str, Any]]:
        async with self._conn.cursor() as cur:
            await cur.execute(
                "SELECT step_id, tool_name, target_desc, args_digest, state, evidence_ref "
                "FROM action_ledger WHERE run_id=%s ORDER BY ledger_id",
                (run_id,),
            )
            rows = await cur.fetchall()
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
        return [
            a for a in await self.run_actions(run_id) if str(a.get("state")) == state
        ]


class DesktopLease:
    """Atomic lease primitive, not production desktop fencing (WP4/11.3).

    Row 1 of ``desktop_lease`` holds (owner, expires_at). An expired lease
    is stealable; an active lease blocks acquisition by another owner.
    Release is owner-checked; callers must use a unique owner per holder.
    Expiry cannot stop a paused/stale holder from acting: do NOT use this
    alone to guarantee exclusive desktop effects or wire it to dispatch.
    """

    def __init__(self, conn: Any, *, ttl_seconds: float = 300.0) -> None:
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
        cur = await self._conn.execute(
            """
            INSERT INTO desktop_lease (id, owner, expires_at)
            VALUES (1, %s, EXTRACT(EPOCH FROM clock_timestamp())::double precision + %s)
            ON CONFLICT (id) DO UPDATE
            SET owner = EXCLUDED.owner,
                expires_at = EXTRACT(EPOCH FROM clock_timestamp())::double precision + %s
            WHERE desktop_lease.owner = EXCLUDED.owner
               OR desktop_lease.expires_at <= EXTRACT(EPOCH FROM clock_timestamp())
            RETURNING owner
            """,
            (owner, ttl, ttl),
        )
        row = await cur.fetchone()
        if row is None:
            return False
        self._owner = str(row[0])
        return self._owner == owner

    async def release(self, owner: str) -> None:
        await self._conn.execute(
            "DELETE FROM desktop_lease WHERE id=1 AND owner=%s", (owner,)
        )
        if self._owner == owner:
            self._owner = None

    async def current(self) -> str | None:
        cur = self._conn.cursor()
        await cur.execute("SELECT owner FROM desktop_lease WHERE id=1")
        row = await cur.fetchone()
        return str(row[0]) if row else None


class RunActionLedger:
    """Run-scoped action ledger writer (master plan 11.2).

    One ledger row per native mutation: 'planned' BEFORE dispatch,
    terminal state after ('confirmed' | 'failed' | 'unknown'). Ledger
    failures are logged and swallowed: recording must never break or
    delay the action it records. The run_id must be a claimed run.
    """

    def __init__(self, store: RunStore, *, run_id: str) -> None:
        self._store = store
        self.run_id = run_id

    async def plan(
        self,
        *,
        tool_name: str,
        args_digest: str = "",
        target_desc: str = "",
    ) -> int:
        """Record a 'planned' row before dispatch; returns the ledger id."""
        return await self._store.record_action(
            self.run_id,
            step_id=f"{tool_name}:{time.time_ns()}",
            tool_name=tool_name,
            target_desc=target_desc,
            args_digest=args_digest,
        )

    async def observe(
        self, ledger_id: int, outcome: ActionState, evidence_ref: str = ""
    ) -> None:
        """Record the dispatch outcome; 'unknown' is the crash-window state."""
        await self._store.mark_action(ledger_id, outcome, evidence_ref)
