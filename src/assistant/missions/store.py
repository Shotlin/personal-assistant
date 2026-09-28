"""Durable mission persistence: one SQLite owner for the Jarvis foundation.

All mission state lives in the existing embedded ``sani.db`` next to memory
and the legacy run registry (file 03 §6). Invariants enforced here, inside
single ``BEGIN IMMEDIATE`` transactions serialized behind one asyncio lock:

- request identity: one mission per ``(owner_id, request_id)``; same digest
  returns the existing mission, a different digest is a collision;
- intent before dispatch: ``claim_step`` records the attempt row (with its
  dedup key and epoch) before the executor may act;
- atomic apply: a step result, its step state, the budget usage merge, the
  resume cursor and the trace event commit together or not at all;
- CAS everywhere a plan/epoch/attempt could race: stale writes are rejected
  or classified STALE, never silently dropped (unresolved effects are kept
  in a reconciliation event);
- independent migration tracking in ``mission_schema_migrations`` — this
  module never touches ``PRAGMA user_version`` or the memory store's
  ``schema_migrations``.

No transaction ever awaits: every method hands one sync function to a worker
thread and returns. Blocking SQLite problems (disk full, corruption, lock
timeout) surface as :class:`MissionStoreError` with the driver's own words.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

from assistant.missions.contracts import (
    SCHEMA_VERSION,
    ActionScopeRecord,
    ApprovalRecord,
    BoundedWorkItem,
    BudgetCharge,
    BudgetLimits,
    BudgetReservation,
    BudgetUsage,
    CheckResult,
    CheckSpec,
    EvidenceRef,
    EvidenceRequirements,
    MissionControl,
    MissionRecord,
    Scope,
    StepResult,
    StepSpec,
    TraceEvent,
    canonical_json,
    digest_of,
    new_id,
    text_digest,
)

_MISSION_SCHEMA_VERSION = 1

_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS mission_schema_migrations (
    version      INTEGER PRIMARY KEY,
    applied_at_ms INTEGER NOT NULL,
    checksum     TEXT NOT NULL
);
"""

_MISSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    mission_id     TEXT PRIMARY KEY,
    owner_id       TEXT NOT NULL,
    request_id     TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    original_goal  TEXT NOT NULL,
    scope_json     TEXT NOT NULL,
    criteria_json  TEXT NOT NULL,
    limits_json    TEXT NOT NULL,
    usage_json     TEXT NOT NULL,
    status         TEXT NOT NULL,
    plan_version   INTEGER NOT NULL DEFAULT 0,
    control_epoch  INTEGER NOT NULL DEFAULT 1,
    priority       INTEGER NOT NULL DEFAULT 4,
    resume_cursor  TEXT,
    created_at_ms  INTEGER NOT NULL,
    updated_at_ms  INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS missions_owner_request_idx
    ON missions (owner_id, request_id);
CREATE INDEX IF NOT EXISTS missions_status_idx ON missions (status);

CREATE TABLE IF NOT EXISTS mission_plans (
    mission_id    TEXT NOT NULL REFERENCES missions(mission_id),
    plan_version  INTEGER NOT NULL,
    plan_json     TEXT NOT NULL,
    digest        TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    creator       TEXT NOT NULL DEFAULT '',
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (mission_id, plan_version)
);

CREATE TABLE IF NOT EXISTS mission_steps (
    mission_id    TEXT NOT NULL,
    plan_version  INTEGER NOT NULL,
    step_id       TEXT NOT NULL,
    step_json     TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'PENDING',
    active_execution_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (mission_id, plan_version, step_id)
);

CREATE TABLE IF NOT EXISTS mission_attempts (
    execution_id  TEXT PRIMARY KEY,
    mission_id    TEXT NOT NULL,
    plan_version  INTEGER NOT NULL,
    step_id       TEXT NOT NULL,
    attempt       INTEGER NOT NULL,
    control_epoch INTEGER NOT NULL,
    packet_digest TEXT NOT NULL,
    run_id        TEXT,
    dispatch_state TEXT NOT NULL,
    effect_class  TEXT NOT NULL,
    effect_outcome TEXT NOT NULL DEFAULT 'NOT_ATTEMPTED',
    external_ids  TEXT NOT NULL DEFAULT '',
    result_json   TEXT,
    result_digest TEXT,
    lease_fence   TEXT NOT NULL DEFAULT '',
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS mission_attempts_step_attempt_idx
    ON mission_attempts (mission_id, plan_version, step_id, attempt);
CREATE INDEX IF NOT EXISTS mission_attempts_dispatch_idx
    ON mission_attempts (dispatch_state);

CREATE TABLE IF NOT EXISTS mission_events (
    sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    mission_id   TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    control_epoch INTEGER NOT NULL,
    step_id      TEXT,
    execution_id TEXT,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    event_hash   TEXT NOT NULL,
    previous_hash TEXT NOT NULL DEFAULT '',
    occurred_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mission_events_mission_idx
    ON mission_events (mission_id, sequence);

CREATE TABLE IF NOT EXISTS mission_approvals (
    approval_id  TEXT PRIMARY KEY,
    mission_id   TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    control_epoch INTEGER NOT NULL,
    action_digest TEXT NOT NULL,
    scope_hash   TEXT NOT NULL,
    target_ref   TEXT,
    account_ref  TEXT,
    workspace_ref TEXT,
    effect_class TEXT NOT NULL,
    maximum_units INTEGER NOT NULL,
    issued_by    TEXT NOT NULL,
    issued_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    single_use   INTEGER NOT NULL DEFAULT 1,
    consumed_at_ms INTEGER,
    revoked      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mission_budget_reservations (
    reservation_id TEXT PRIMARY KEY,
    mission_id     TEXT NOT NULL,
    resource       TEXT NOT NULL,
    amount         INTEGER NOT NULL,
    call_key       TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'RESERVED',
    created_at_ms  INTEGER NOT NULL,
    updated_at_ms  INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS mission_budget_callkey_idx
    ON mission_budget_reservations (mission_id, resource, call_key)
    WHERE call_key != '';

CREATE TABLE IF NOT EXISTS mission_evidence (
    evidence_id  TEXT PRIMARY KEY,
    mission_id   TEXT NOT NULL,
    execution_id TEXT,
    kind         TEXT NOT NULL,
    relative_path TEXT,
    sha256       TEXT NOT NULL,
    classification TEXT NOT NULL,
    redaction_status TEXT NOT NULL,
    redaction_version TEXT NOT NULL DEFAULT 'v1',
    inline_json   TEXT,
    captured_at_ms INTEGER NOT NULL,
    expires_at_ms INTEGER,
    deleted      INTEGER NOT NULL DEFAULT 0
);
"""

#: R04: the per-action intent ledger arrives as migration 2 so existing
#: mission databases upgrade additively (checksum-verified, never rebuild).
_ACTION_LEDGER_MIGRATION = """
CREATE TABLE IF NOT EXISTS mission_payloads (
    mission_id TEXT NOT NULL,
    ref        TEXT NOT NULL,
    content    TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (mission_id, ref)
);

CREATE TABLE IF NOT EXISTS mission_action_ledger (
    ledger_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    mission_id  TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    target_desc TEXT NOT NULL DEFAULT '',
    args_digest TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'planned',
    evidence_ref TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mission_action_ledger_exec_idx
    ON mission_action_ledger (execution_id);
"""

_RETENTION_LOG_MIGRATION = """
CREATE TABLE IF NOT EXISTS mission_retention_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at_ms INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    subject_id TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS mission_retention_log_time_idx
    ON mission_retention_log (recorded_at_ms);
"""

#: Applied in order; each entry is (version, checksum, statements).
_MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (_MISSION_SCHEMA_VERSION, digest_of(_MISSION_SCHEMA), _MISSION_SCHEMA),
    (2, digest_of(_ACTION_LEDGER_MIGRATION), _ACTION_LEDGER_MIGRATION),
    (3, digest_of(_RETENTION_LOG_MIGRATION), _RETENTION_LOG_MIGRATION),
)

ACTIVE_DISPATCH_STATES = frozenset({"INTENT_COMMITTED", "DISPATCHED", "RECONCILING"})


class MissionStoreError(RuntimeError):
    """A persistence failure the caller must see (disk full, corruption...)."""


class MissionIdentityCollision(MissionStoreError):
    """Same request_id, different content: an identity collision."""


class StaleControlError(MissionStoreError):
    """A control command lost its compare-and-swap."""


class ResultConflict(MissionStoreError):
    """A different result for an execution that already has one."""


class UnknownExecution(MissionStoreError):
    """A result arrived for an execution this store never recorded."""


ApplyOutcome = Literal["APPLIED", "DUPLICATE", "STALE"]


def _now_ms() -> int:
    return int(time.time() * 1000)


class MissionActionLedger:
    """Per-action intent ledger bound to one execution (R04/F04).

    Implements the RunActionLedger interface (plan/observe) over the
    mission store: every actual tool dispatch records a 'planned' row
    BEFORE the call and a terminal state after. The policy wrapper's
    strict-audit mode fails closed when this ledger is absent or its write
    fails -- no durable intent, no mutation.
    """

    def __init__(self, store: MissionStore, *, execution_id: str, mission_id: str) -> None:
        self._store = store
        self.execution_id = execution_id
        self.mission_id = mission_id

    async def plan(self, *, tool_name: str, args_digest: str) -> int:
        """Insert a 'planned' row before dispatch; returns the ledger id."""
        return await self._store._run_tx(self._plan_sync, tool_name, args_digest)

    def _plan_sync(self, tool_name: str, args_digest: str) -> int:
        now = _now_ms()
        row = self._store._conn.execute(
            """
            INSERT INTO mission_action_ledger (execution_id, mission_id, tool_name,
                args_digest, state, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'planned', ?, ?)
            RETURNING ledger_id
            """,
            (self.execution_id, self.mission_id, tool_name, args_digest, now, now),
        ).fetchone()
        assert row is not None
        return int(row[0])

    async def observe(self, ledger_id: int, outcome: str, evidence: str = "") -> None:
        await self._store._run_tx(self._observe_sync, ledger_id, outcome, evidence)

    def _observe_sync(self, ledger_id: int, outcome: str, evidence: str) -> None:
        self._store._conn.execute(
            "UPDATE mission_action_ledger SET state=?, evidence_ref=?, updated_at=? "
            "WHERE ledger_id=?",
            (outcome, evidence[:200], _now_ms(), ledger_id),
        )

    async def actions_in_state(self, state: str) -> list[dict[str, Any]]:
        rows = await self._store._run_tx(self._actions_sync, state)
        return [
            {
                "ledger_id": r[0],
                "tool_name": r[1],
                "args_digest": r[2],
                "state": r[3],
                "evidence_ref": r[4],
            }
            for r in rows
        ]

    def _actions_sync(self, state: str) -> list[tuple[Any, ...]]:
        return self._store._conn.execute(
            "SELECT ledger_id, tool_name, args_digest, state, evidence_ref "
            "FROM mission_action_ledger WHERE execution_id=? AND state=? "
            "ORDER BY ledger_id",
            (self.execution_id, state),
        ).fetchall()


class MissionStore:
    """Owns the mission tables in one embedded SQLite file."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    # -- lifecycle ------------------------------------------------------------

    @classmethod
    async def connect(cls, db_path: str | Path) -> MissionStore:
        path = Path(db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await asyncio.to_thread(cls._connect_sync, str(path))
        return cls(conn)

    @staticmethod
    def _connect_sync(path: str) -> sqlite3.Connection:
        # isolation_level=None: explicit BEGIN IMMEDIATE / COMMIT / ROLLBACK
        # are issued by the store, never implied per statement (Python 3.12
        # sqlite autocommit semantics are deliberately pinned here).
        conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        # FULL synchronous: mission effect-intent commits must survive a
        # process crash; this store writes desktop-scale volumes.
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    async def setup(self) -> None:
        """Apply pending migrations; idempotent, additive, never destructive."""
        async with self._lock:
            await asyncio.to_thread(self._setup_sync)

    def _setup_sync(self) -> None:
        self._conn.execute(_MIGRATIONS_TABLE)
        applied = {
            int(row[0])
            for row in self._conn.execute(
                "SELECT version FROM mission_schema_migrations"
            ).fetchall()
        }
        for version, checksum, statements in _MIGRATIONS:
            if version in applied:
                continue
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Statement-by-statement, NOT executescript(): executescript
                # issues its own implicit COMMIT, which would break the
                # all-or-nothing migration transaction.
                for statement in statements.split(";"):
                    if statement.strip():
                        self._conn.execute(statement)
                self._conn.execute(
                    "INSERT INTO mission_schema_migrations (version, applied_at_ms, checksum) "
                    "VALUES (?, ?, ?)",
                    (version, _now_ms(), checksum),
                )
                self._conn.execute("COMMIT")
            except sqlite3.Error:
                self._rollback_quietly()
                raise
        # Link legacy action-ledger rows to mission executions, additively.
        columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(action_ledger)").fetchall()
        }
        if columns and "execution_id" not in columns:
            self._conn.execute(
                "ALTER TABLE action_ledger ADD COLUMN execution_id TEXT NOT NULL DEFAULT ''"
            )

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)

    # -- transaction helper -----------------------------------------------------

    async def _run_tx(self, fn: Any, *args: Any) -> Any:
        """Run one sync transaction on a worker thread behind the write lock."""
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    def _rollback_quietly(self) -> None:
        """Best-effort rollback: SQLite may have already rolled back itself
        (e.g. SQLITE_FULL/SQLITE_CORRUPT end the transaction implicitly)."""
        with contextlib.suppress(sqlite3.Error):
            self._conn.execute("ROLLBACK")

    def _tx(self, fn: Any, *args: Any) -> Any:
        """BEGIN IMMEDIATE ... COMMIT/ROLLBACK around ``fn`` (sync context)."""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise MissionStoreError(f"cannot begin transaction: {exc}") from exc
        try:
            result = fn()
        except sqlite3.Error as exc:
            self._rollback_quietly()
            raise MissionStoreError(str(exc)) from exc
        except BaseException:
            self._rollback_quietly()
            raise
        try:
            self._conn.execute("COMMIT")
        except sqlite3.Error as exc:
            self._rollback_quietly()
            raise MissionStoreError(f"commit failed: {exc}") from exc
        return result

    # -- row/JSON mapping helpers ------------------------------------------------

    @staticmethod
    def _mission_from_row(row: tuple[Any, ...]) -> MissionRecord:
        return MissionRecord.model_validate(
            {
                "schema_version": SCHEMA_VERSION,
                "mission_id": row[0],
                "owner_id": row[1],
                "request_id": row[2],
                "conversation_id": row[3],
                "original_goal": row[4],
                "scope": Scope.model_validate(json.loads(row[5])),
                "success_criteria": [
                    CheckSpec.model_validate(c) for c in json.loads(row[6])
                ],
                "budget_limits": BudgetLimits.model_validate(json.loads(row[7])),
                "budget_usage": BudgetUsage.model_validate(json.loads(row[8])),
                "status": row[9],
                "plan_version": row[10],
                "control_epoch": row[11],
                "priority": row[12],
                "resume_cursor": row[13],
                "created_at_ms": row[14],
                "updated_at_ms": row[15],
            }
        )

    @staticmethod
    def _usage_dict(row_json: str) -> dict[str, Any]:
        import json

        return json.loads(row_json)

    # -- request claim -------------------------------------------------------------

    async def claim_request(
        self, request: Any, digest: str, *, goal: str, scope: Scope, limits: BudgetLimits
    ) -> MissionRecord:
        """Idempotent request claim (file 03 §6 ``claim_request``)."""
        return await self._run_tx(
            self._claim_request_sync, request, digest, goal, scope, limits
        )

    def _claim_request_sync(
        self, request: Any, digest: str, goal: str, scope: Scope, limits: BudgetLimits
    ) -> MissionRecord:
        # R09/F11 (RP08): goals are user text and reach durable storage --
        # secret-shaped content is REFUSED (the memory policy precedent),
        # never stored verbatim.
        from assistant.memory.policy import contains_secret

        privacy_reason = contains_secret(goal)
        if privacy_reason is not None:
            raise MissionStoreError(
                f"request refused by privacy policy: {privacy_reason}; "
                "remove the secret and re-submit"
            )
        now = _now_ms()
        mission_id = new_id()

        def tx() -> MissionRecord:
            inserted = self._conn.execute(
                """
                INSERT INTO missions
                    (mission_id, owner_id, request_id, conversation_id, request_digest,
                     original_goal, scope_json, criteria_json, limits_json, usage_json,
                     status, plan_version, control_epoch, created_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PLANNED', 0, 1, ?, ?)
                ON CONFLICT (owner_id, request_id) DO NOTHING
                RETURNING mission_id
                """,
                (
                    mission_id,
                    request.owner_id,
                    request.request_id,
                    request.conversation_id,
                    digest,
                    goal,
                    canonical_json(scope.model_dump()),
                    "[]",
                    canonical_json(limits.model_dump()),
                    canonical_json(BudgetUsage().model_dump()),
                    now,
                    now,
                ),
            ).fetchone()
            if inserted is not None:
                self._append_event(
                    mission_id=mission_id,
                    plan_version=0,
                    control_epoch=1,
                    kind="request",
                    payload={
                        "request_id": request.request_id,
                        "input_origin": request.input_origin,
                        "input_revision": request.input_revision,
                        "text_digest": digest,
                        "conversation_id": request.conversation_id,
                    },
                    occurred_at_ms=now,
                )
                return self._get_mission_sync(mission_id)  # type: ignore[return-value]
            row = self._conn.execute(
                "SELECT mission_id, request_digest FROM missions "
                "WHERE owner_id=? AND request_id=?",
                (request.owner_id, request.request_id),
            ).fetchone()
            if row is None:  # pragma: no cover - conflict reported but row vanished
                raise MissionStoreError("request claim conflict without a winner row")
            existing_id, existing_digest = str(row[0]), str(row[1])
            if existing_digest != digest:
                raise MissionIdentityCollision(
                    "same request_id with a different digest; treat as a new request"
                )
            return self._get_mission_sync(existing_id)  # type: ignore[return-value]

        return self._tx(tx)

    def _get_mission_sync(self, mission_id: str) -> MissionRecord | None:
        row = self._conn.execute(
            "SELECT mission_id, owner_id, request_id, conversation_id, original_goal, "
            "scope_json, criteria_json, limits_json, usage_json, status, plan_version, "
            "control_epoch, priority, resume_cursor, created_at_ms, updated_at_ms "
            "FROM missions WHERE mission_id=?",
            (mission_id,),
        ).fetchone()
        if row is None:
            return None
        record = self._mission_from_row(row)
        steps = self._conn.execute(
            "SELECT step_json FROM mission_steps WHERE mission_id=? AND plan_version=?",
            (mission_id, record.plan_version),
        ).fetchall()
        record.steps = [StepSpec.model_validate(json.loads(r[0])) for r in steps]
        record.steps.sort(key=lambda s: s.ordinal)
        return record

    async def get_mission(self, mission_id: str) -> MissionRecord | None:
        return await self._run_tx(self._get_mission_sync, mission_id)

    async def list_missions(
        self, *, owner_id: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[MissionRecord]:
        return await self._run_tx(self._list_missions_sync, owner_id, status, limit)

    def _list_missions_sync(
        self, owner_id: str | None, status: str | None, limit: int
    ) -> list[MissionRecord]:
        query = (
            "SELECT mission_id, owner_id, request_id, conversation_id, original_goal, "
            "scope_json, criteria_json, limits_json, usage_json, status, plan_version, "
            "control_epoch, priority, resume_cursor, created_at_ms, updated_at_ms "
            "FROM missions"
        )
        conditions: list[str] = []
        params: list[Any] = []
        if owner_id is not None:
            conditions.append("owner_id=?")
            params.append(owner_id)
        if status is not None:
            conditions.append("status=?")
            params.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY priority ASC, created_at_ms ASC LIMIT ?"
        params.append(limit)
        return [self._mission_from_row(row) for row in self._conn.execute(query, params)]

    # -- plan commit ------------------------------------------------------------

    async def commit_plan(
        self,
        mission_id: str,
        expected_version: int,
        steps: list[StepSpec],
        criteria: list[CheckSpec],
        *,
        reason: str = "",
        creator: str = "",
    ) -> MissionRecord:
        return await self._run_tx(
            self._commit_plan_sync, mission_id, expected_version, steps, criteria, reason, creator
        )

    def _commit_plan_sync(
        self,
        mission_id: str,
        expected_version: int,
        steps: list[StepSpec],
        criteria: list[CheckSpec],
        reason: str,
        creator: str,
    ) -> MissionRecord:
        now = _now_ms()

        def tx() -> MissionRecord:
            row = self._conn.execute(
                "SELECT plan_version, status FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            current_version, status = int(row[0]), str(row[1])
            if current_version != expected_version:
                raise StaleControlError(
                    f"plan CAS failed: expected v{expected_version}, at v{current_version}"
                )
            if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                raise MissionStoreError(f"cannot plan a terminal mission ({status})")
            new_version = expected_version + 1
            plan_doc = {
                "steps": [s.model_dump() for s in steps],
                "criteria": [c.model_dump() for c in criteria],
            }
            self._conn.execute(
                """
                INSERT INTO mission_plans (mission_id, plan_version, plan_json, digest,
                    reason, creator, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    new_version,
                    canonical_json(plan_doc),
                    digest_of(plan_doc),
                    reason[:1000],
                    creator[:128],
                    now,
                ),
            )
            for step in steps:
                self._conn.execute(
                    """
                    INSERT INTO mission_steps (mission_id, plan_version, step_id, step_json,
                        state, updated_at_ms)
                    VALUES (?, ?, ?, ?, 'PENDING', ?)
                    """,
                    (mission_id, new_version, step.step_id, canonical_json(step.model_dump()), now),
                )
            if new_version > 1:
                # A revision invalidates old approvals and in-flight packets.
                # C03/N01: dispatched attempts carry effect uncertainty into
                # the new plan version as RECONCILING, never a replayable
                # CANCELLED; only intents that never left may be cancelled.
                self._conn.execute(
                    "UPDATE mission_approvals SET revoked=1 WHERE mission_id=? AND plan_version<=?",
                    (mission_id, expected_version),
                )
                self._conn.execute(
                    "UPDATE mission_attempts SET dispatch_state='RECONCILING', updated_at_ms=? "
                    "WHERE mission_id=? AND plan_version<=? AND dispatch_state='DISPATCHED'",
                    (now, mission_id, expected_version),
                )
                self._conn.execute(
                    "UPDATE mission_attempts SET dispatch_state='CANCELLED', updated_at_ms=? "
                    "WHERE mission_id=? AND plan_version<=? AND dispatch_state='INTENT_COMMITTED'",
                    (now, mission_id, expected_version),
                )
                self._conn.execute(
                    "UPDATE mission_steps SET active_execution_id=NULL, "
                    "state=CASE WHEN state IN ('PENDING','RUNNING') THEN state ELSE state END, "
                    "updated_at_ms=? WHERE mission_id=? AND plan_version<=? AND state='RUNNING'",
                    (now, mission_id, expected_version),
                )
            self._conn.execute(
                "UPDATE missions SET plan_version=?, status='PLANNED', updated_at_ms=? "
                "WHERE mission_id=?",
                (new_version, now, mission_id),
            )
            self._append_event(
                mission_id=mission_id,
                plan_version=new_version,
                control_epoch=self._epoch_sync(mission_id),
                kind="plan",
                payload={
                    "plan_version": new_version,
                    "reason": reason[:1000],
                    "step_ids": [s.step_id for s in steps],
                },
                occurred_at_ms=now,
            )
            return self._get_mission_sync(mission_id)  # type: ignore[return-value]

        return self._tx(tx)

    def _epoch_sync(self, mission_id: str) -> int:
        row = self._conn.execute(
            "SELECT control_epoch FROM missions WHERE mission_id=?", (mission_id,)
        ).fetchone()
        return int(row[0]) if row else 1

    # -- step claim ---------------------------------------------------------------

    async def claim_step(
        self,
        mission_id: str,
        expected_version: int,
        expected_epoch: int,
        *,
        step_id: str | None = None,
        tool_ids: list[str],
        payload_digests: list[str] | None = None,
        driver_generation: str = "",
        lease_fence: str = "",
        conversation_ref: str = "",
        tool_catalog: dict[str, list[str]] | None = None,
    ) -> BoundedWorkItem | None:
        """Record dispatch intent atomically and return the bounded packet.

        Returns ``None`` when no dependency-ready step exists. Validates
        plan/epoch CAS, dependency readiness and single active attempt before
        writing the intent row; the packet digests the exact intent.

        C05/N07: when ``tool_catalog`` (the trusted recipe -> tools map) is
        given, the claimed STEP's own recipe selects the catalog inside the
        same claim transaction — a mixed-recipe mission can no longer hand
        step 2 the tool list of step 1.
        """
        return await self._run_tx(
            self._claim_step_sync,
            mission_id,
            expected_version,
            expected_epoch,
            step_id,
            tool_ids,
            payload_digests or [],
            driver_generation,
            lease_fence,
            conversation_ref,
            tool_catalog,
        )

    def _claim_step_sync(
        self,
        mission_id: str,
        expected_version: int,
        expected_epoch: int,
        step_id: str | None,
        tool_ids: list[str],
        payload_digests: list[str],
        driver_generation: str,
        lease_fence: str,
        conversation_ref: str,
        tool_catalog: dict[str, list[str]] | None = None,
    ) -> BoundedWorkItem | None:
        now = _now_ms()

        def tx() -> BoundedWorkItem | None:
            row = self._conn.execute(
                "SELECT plan_version, control_epoch, status, limits_json, usage_json "
                "FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            plan_version, epoch, status = int(row[0]), int(row[1]), str(row[2])
            if plan_version != expected_version or epoch != expected_epoch:
                raise StaleControlError(
                    f"claim CAS failed: expected v{expected_version}/e{expected_epoch}, "
                    f"at v{plan_version}/e{epoch}"
                )
            if status not in {"PLANNED", "RUNNING"}:
                raise MissionStoreError(f"mission {mission_id} is {status}; not claimable")
            limits = BudgetLimits.model_validate(self._usage_dict(row[3]))
            usage = BudgetUsage.model_validate(self._usage_dict(row[4]))
            if not usage.within(limits, "actions"):
                raise MissionStoreError("mission action budget exhausted")

            steps = [
                (r[0], r[1], r[2], r[3])
                for r in self._conn.execute(
                    "SELECT step_id, state, active_execution_id, step_json FROM mission_steps "
                    "WHERE mission_id=? AND plan_version=?",
                    (mission_id, plan_version),
                ).fetchall()
            ]
            specs = {sid: StepSpec.model_validate(self._usage_dict(sjson)) for sid, _, _,
                 sjson in steps}
            chosen: str | None = step_id
            if chosen is None:
                for sid, state, active_exec, _ in sorted(steps, key=lambda s: specs[s[0]].ordinal):
                    if state != "PENDING" or active_exec:
                        continue
                    deps = specs[sid].dependencies
                    states = {other: st for other, st, _, _ in steps}
                    if all(states.get(d) in {"SUCCEEDED", "SKIPPED"} for d in deps):
                        chosen = sid
                        break
                if chosen is None:
                    return None
            else:
                match = next((s for s in steps if s[0] == chosen), None)
                if match is None:
                    raise MissionStoreError(f"unknown step {chosen} for plan v{plan_version}")
                if match[1] != "PENDING" or match[2]:
                    raise MissionStoreError(
                        f"step {chosen} is {match[1]} (active={bool(match[2])}); not claimable"
                    )
                for dep in specs[chosen].dependencies:
                    dep_state = next((s[1] for s in steps if s[0] == dep), None)
                    if dep_state not in {"SUCCEEDED", "SKIPPED"}:
                        raise MissionStoreError(
                            f"dependency {dep} of {chosen} is {dep_state}; not ready"
                        )
            # C03/N01: an unresolved dispatched attempt blocks any new claim
            # of the same step — retry requires durable reconciliation proof
            # (NO_EFFECT) plus the explicit safe transition, never a silent
            # next attempt over an uncertain effect.
            unresolved = self._conn.execute(
                "SELECT 1 FROM mission_attempts WHERE mission_id=? AND plan_version=? "
                "AND step_id=? AND dispatch_state='RECONCILING' LIMIT 1",
                (mission_id, plan_version, chosen),
            ).fetchone()
            if unresolved is not None:
                raise MissionStoreError(
                    f"step {chosen} has an unresolved dispatched attempt; "
                    "reconcile its external effects before any retry"
                )

            spec = specs[chosen]  # type: ignore[index]
            # C05/N07: the tool catalog comes from the CLAIMED step's recipe
            # through the trusted map — never from a mission-wide guess.
            effective_tool_ids = (
                list(tool_catalog.get(spec.recipe_id) or [])
                if tool_catalog is not None
                else tool_ids
            )
            if not effective_tool_ids:
                raise MissionStoreError(
                    f"recipe {spec.recipe_id!r} has no trusted tool catalog entry"
                )
            attempt = self._next_attempt_sync(mission_id, plan_version, chosen)  # type: ignore[arg-type]
            execution_id = new_id()
            # C02/N04: a step that may act may also OBSERVE — discovery is
            # inherent to any GUI step, so the packet's action scope always
            # carries READ_ONLY alongside the step's own effect class. The
            # authority layer grants inventory reads a bounded bootstrap
            # (no prior observation yet exists to sit on); every mutating
            # effect still needs its full surface proof.
            action_scope = ActionScopeRecord(
                tool_ids=effective_tool_ids,
                target_scope_hash=spec.scope.scope_hash,
                payload_digests=list(spec.payload_digests) or payload_digests,
                permitted_effects={spec.effect_class, "READ_ONLY"},
            )
            # The step's unit budget bounds the packet deadline; the mission
            # wall clock is the other ceiling (min of the two).
            unit_wall_ms = min(spec.budget.max_wall_ms, limits.max_wall_ms)
            item = BoundedWorkItem(
                mission_id=mission_id,
                plan_version=plan_version,
                control_epoch=epoch,
                step_id=chosen,  # type: ignore[arg-type]
                execution_id=execution_id,
                attempt=attempt,
                objective=spec.objective,
                expected_scope=spec.scope,
                # C05/N07: preconditions are evaluated before dispatch — but
                # a step's own required POSTconditions are NOT its
                # preconditions (the app-foreground check of open_app must
                # run AFTER the app opens, not before). Preconditions come
                # only from plan data that names them explicitly.
                preconditions=[],
                allowed_action_scope=action_scope,
                recipe_id=spec.recipe_id,
                recipe_args=dict(spec.recipe_args),
                payload_refs=spec.payload_refs,
                expected_postconditions=list(spec.checks),
                evidence_requirements=EvidenceRequirements(
                    required_check_ids=[c.check_id for c in spec.checks if c.required]
                ),
                deadline_at_ms=now + unit_wall_ms,
                budget=spec.budget,
                effect_class=spec.effect_class,
                deduplication_key=f"{mission_id}:{plan_version}:{chosen}:{attempt}",
                escalation_conditions=list(spec.escalation_conditions),
                driver_generation=driver_generation,
                lease_fence=lease_fence,
            )
            self._conn.execute(
                """
                INSERT INTO mission_attempts (execution_id, mission_id, plan_version,
                    step_id, attempt, control_epoch, packet_digest, dispatch_state,
                    effect_class, lease_fence, created_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'INTENT_COMMITTED', ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    mission_id,
                    plan_version,
                    chosen,
                    attempt,
                    epoch,
                    digest_of(item.model_dump()),
                    spec.effect_class,
                    lease_fence,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """
                UPDATE mission_steps SET active_execution_id=?, attempt_count=attempt_count+1,
                    state='RUNNING', updated_at_ms=?
                WHERE mission_id=? AND plan_version=? AND step_id=?
                """,
                (execution_id, now, mission_id, plan_version, chosen),
            )
            if status == "PLANNED":
                self._conn.execute(
                    "UPDATE missions SET status='RUNNING', updated_at_ms=? WHERE mission_id=?",
                    (now, mission_id),
                )
            self._append_event(
                mission_id=mission_id,
                plan_version=plan_version,
                control_epoch=epoch,
                step_id=chosen,
                execution_id=execution_id,
                kind="action",
                payload={
                    "dispatch": "intent",
                    "attempt": attempt,
                    "recipe_id": spec.recipe_id,
                    "effect_class": spec.effect_class,
                    "packet_digest": digest_of(item.model_dump()),
                },
                occurred_at_ms=now,
            )
            return item

        return self._tx(tx)

    def _next_attempt_sync(self, mission_id: str, plan_version: int, step_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(attempt),0)+1 FROM mission_attempts "
            "WHERE mission_id=? AND plan_version=? AND step_id=?",
            (mission_id, plan_version, step_id),
        ).fetchone()
        return int(row[0])

    # -- result application ----------------------------------------------------------

    async def apply_result(self, result: StepResult) -> ApplyOutcome:
        return await self._run_tx(self._apply_result_sync, result)

    def _apply_result_sync(self, result: StepResult) -> ApplyOutcome:
        now = _now_ms()

        def tx() -> ApplyOutcome:
            row = self._conn.execute(
                "SELECT mission_id, plan_version, step_id, attempt, control_epoch, "
                "dispatch_state, result_digest FROM mission_attempts WHERE execution_id=?",
                (result.execution_id,),
            ).fetchone()
            if row is None:
                raise UnknownExecution(f"unknown execution: {result.execution_id}")
            (
                attempt_mission,
                attempt_plan,
                attempt_step,
                attempt_n,
                attempt_epoch,
                dispatch_state,
                existing_digest,
            ) = row
            attempt_mission, attempt_plan, attempt_step = (
                str(attempt_mission),
                int(attempt_plan),
                str(attempt_step),
            )
            if result.mission_id != attempt_mission or result.step_id != attempt_step:
                raise ResultConflict("result identity does not match the recorded attempt")

            result_digest = digest_of(
                {
                    "status": result.status,
                    "postconditions": [c.model_dump() for c in result.postconditions],
                    "effect_outcome": result.effect_outcome,
                    "evidence_ids": result.evidence_ids,
                    "external_operation_ids": result.external_operation_ids,
                }
            )
            if existing_digest is not None:
                if str(existing_digest) == result_digest:
                    return "DUPLICATE"
                raise ResultConflict(
                    "conflicting result for an execution that already reported one"
                )

            mission_row = self._conn.execute(
                "SELECT plan_version, control_epoch, status, usage_json FROM missions "
                "WHERE mission_id=?",
                (attempt_mission,),
            ).fetchone()
            if mission_row is None:  # pragma: no cover - FK guarantees the row
                raise MissionStoreError(f"mission vanished: {attempt_mission}")
            mission_plan, mission_epoch, mission_status = (
                int(mission_row[0]),
                int(mission_row[1]),
                str(mission_row[2]),
            )
            if (
                attempt_plan != mission_plan
                or attempt_epoch != mission_epoch
                or result.plan_version != mission_plan
                or result.control_epoch != mission_epoch
                or result.attempt != int(attempt_n)
            ):
                # Late/stale result: keep the effect facts in a reconciliation
                # event; never advance state (file 03 §6).
                self._append_event(
                    mission_id=attempt_mission,
                    plan_version=attempt_plan,
                    control_epoch=attempt_epoch,
                    step_id=attempt_step,
                    execution_id=result.execution_id,
                    kind="recovery",
                    payload={
                        "stale_result": True,
                        "status": result.status,
                        "effect_outcome": result.effect_outcome,
                        "external_operation_ids": result.external_operation_ids,
                        "result_plan": result.plan_version,
                        "result_epoch": result.control_epoch,
                    },
                    occurred_at_ms=now,
                )
                return "STALE"

            # Verifier-bound postconditions: every reported check must be one
            # the plan's spec registered, with the same verifier identity.
            expected_checks = self._step_checks_sync(attempt_mission, attempt_plan, attempt_step)
            expected_by_id = {c.check_id: c for c in expected_checks}
            for check in result.postconditions:
                spec = expected_by_id.get(check.check_id)
                if spec is None:
                    raise ResultConflict(
                        f"CheckResult {check.check_id} is not a registered postcondition"
                    )
                if (
                    check.verifier_id != spec.verifier_id
                    or check.verifier_version != spec.verifier_version
                ):
                    raise ResultConflict(
                        f"CheckResult {check.check_id} names verifier "
                        f"{check.verifier_id}@{check.verifier_version}, expected "
                        f"{spec.verifier_id}@{spec.verifier_version}"
                    )

            step_state = {
                "COMPLETED": "SUCCEEDED",
                "FAILED": "FAILED",
                "BLOCKED": "BLOCKED",
                "CANCELLED": "CANCELLED",
                "NEEDS_CONTROLLER": "BLOCKED",
                "NEEDS_HUMAN": "BLOCKED",
            }.get(result.status)
            if step_state is None:
                raise ResultConflict(f"terminal result expected, got {result.status}")
            # R06/F05: a unit may succeed ONLY when every REQUIRED check of
            # its spec is present and passing. A missing check is not a pass.
            if step_state == "SUCCEEDED":
                required_ids = {c.check_id for c in expected_checks if c.required}
                passed_ids = {c.check_id for c in result.postconditions if c.passed}
                missing = required_ids - passed_ids
                if missing:
                    raise ResultConflict(
                        f"step {attempt_step} cannot succeed: required checks "
                        f"missing or failed: {sorted(missing)}"
                    )

            self._conn.execute(
                """
                UPDATE mission_attempts SET dispatch_state='RESULT_APPLIED',
                    effect_outcome=?, result_json=?, result_digest=?, updated_at_ms=?
                WHERE execution_id=?
                """,
                (
                    result.effect_outcome,
                    canonical_json(result.model_dump()),
                    result_digest,
                    now,
                    result.execution_id,
                ),
            )
            self._conn.execute(
                """
                UPDATE mission_steps SET state=?, active_execution_id=NULL, updated_at_ms=?
                WHERE mission_id=? AND plan_version=? AND step_id=?
                """,
                (step_state, now, attempt_mission, attempt_plan, attempt_step),
            )
            usage = BudgetUsage.model_validate(self._usage_dict(mission_row[3]))
            for resource, amount in result.usage.consumed.items():
                usage.consumed[resource] = usage.consumed.get(resource, 0) + int(amount)
            for resource, amount in result.usage.reserved.items():
                usage.reserved[resource] = usage.reserved.get(resource, 0) + int(amount)
            usage.known_input_tokens += result.usage.known_input_tokens
            usage.known_output_tokens += result.usage.known_output_tokens
            usage.known_cache_tokens += result.usage.known_cache_tokens
            usage.known_reasoning_tokens += result.usage.known_reasoning_tokens
            usage.unknown_usage_calls += result.usage.unknown_usage_calls
            usage.external_wait_ms += result.usage.external_wait_ms
            usage.active_ms += result.usage.active_ms
            if (
                usage.known_cost_microunits is not None
                and result.usage.known_cost_microunits is not None
            ):
                usage.known_cost_microunits += result.usage.known_cost_microunits
            elif result.usage.known_cost_microunits is not None:
                usage.known_cost_microunits = result.usage.known_cost_microunits
            self._conn.execute(
                "UPDATE missions SET usage_json=?, updated_at_ms=? WHERE mission_id=?",
                (canonical_json(usage.model_dump()), now, attempt_mission),
            )
            self._append_event(
                mission_id=attempt_mission,
                plan_version=attempt_plan,
                control_epoch=mission_epoch,
                step_id=attempt_step,
                execution_id=result.execution_id,
                kind="outcome",
                payload={
                    "status": result.status,
                    "effect_outcome": result.effect_outcome,
                    "failure_category": result.failure_category,
                    "attempt": result.attempt,
                },
                occurred_at_ms=now,
            )
            # When every step of the current plan is terminal, the mission is
            # ready for its acceptance gate (VERIFYING); the service owns the
            # final verdict from here.
            states = self._conn.execute(
                "SELECT state FROM mission_steps WHERE mission_id=? AND plan_version=?",
                (attempt_mission, attempt_plan),
            ).fetchall()
            if all(str(s[0]) in {"SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED",
                 "SKIPPED"} for s in states):
                new_status = "VERIFYING" if mission_status == "RUNNING" else mission_status
                self._conn.execute(
                    "UPDATE missions SET status=?, resume_cursor=?, updated_at_ms=? "
                    "WHERE mission_id=?",
                    (new_status, attempt_step, now, attempt_mission),
                )
            return "APPLIED"

        return self._tx(tx)

    def _step_checks_sync(self, mission_id: str, plan_version: int,
         step_id: str) -> list[CheckSpec]:
        row = self._conn.execute(
            "SELECT step_json FROM mission_steps "
            "WHERE mission_id=? AND plan_version=? AND step_id=?",
            (mission_id, plan_version, step_id),
        ).fetchone()
        if row is None:
            return []
        spec = StepSpec.model_validate(self._usage_dict(row[0]))
        return list(spec.checks)

    # -- controls ------------------------------------------------------------------

    async def control(self, command: MissionControl) -> MissionRecord:
        return await self._run_tx(self._control_sync, command)

    def _control_sync(self, command: MissionControl) -> MissionRecord:
        now = _now_ms()

        def tx() -> MissionRecord:
            row = self._conn.execute(
                "SELECT plan_version, control_epoch, status FROM missions WHERE mission_id=?",
                (command.mission_id,),
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {command.mission_id}")
            plan_version, epoch, status = int(row[0]), int(row[1]), str(row[2])
            if command.expected_plan_version != plan_version or (
                command.expected_control_epoch != epoch
            ):
                raise StaleControlError(
                    f"control CAS failed: expected v{command.expected_plan_version}/"
                    f"e{command.expected_control_epoch}, at v{plan_version}/e{epoch}"
                )
            if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                # Terminal missions do not reopen (file 03 §7).
                raise MissionStoreError(f"mission is terminal ({status}); control refused")
            new_status = status
            new_epoch = epoch
            match command.kind:
                case "PAUSE":
                    new_status, new_epoch = "PAUSED", epoch + 1
                case "RESUME":
                    if status != "PAUSED":
                        raise MissionStoreError(f"RESUME requires PAUSED, got {status}")
                    new_status, new_epoch = "RUNNING", epoch + 1
                case "CANCEL":
                    new_status, new_epoch = "CANCELLED", epoch + 1
                case "REVISE":
                    new_status, new_epoch = "PLANNED", epoch + 1
                case "SET_PRIORITY":
                    if command.priority is None:
                        raise MissionStoreError("SET_PRIORITY requires a priority value")
                    self._conn.execute(
                        "UPDATE missions SET priority=?, updated_at_ms=? WHERE mission_id=?",
                        (command.priority, now, command.mission_id),
                    )
                    self._append_event(
                        mission_id=command.mission_id,
                        plan_version=plan_version,
                        control_epoch=epoch,
                        kind="control",
                        payload={"kind": command.kind, "priority": command.priority},
                        occurred_at_ms=now,
                    )
                    return self._get_mission_sync(command.mission_id)  # type: ignore[return-value]
            if new_epoch != epoch:
                # The epoch bump invalidates queued packets and open approvals.
                # C03/N01: attempt cancellation is separated from effect
                # outcome. INTENT_COMMITTED never left the executor, so it is
                # safe to cancel outright. DISPATCHED means the packet left:
                # the effect may exist, so the attempt becomes RECONCILING —
                # never a replayable CANCELLED — and its step stays BLOCKED
                # until reconciliation proves the effect state.
                if command.kind in {"PAUSE", "REVISE", "CANCEL"}:
                    self._conn.execute(
                        "UPDATE mission_attempts SET dispatch_state='RECONCILING', "
                        "updated_at_ms=? WHERE mission_id=? AND control_epoch=? AND "
                        "dispatch_state='DISPATCHED'",
                        (now, command.mission_id, epoch),
                    )
                    self._conn.execute(
                        "UPDATE mission_attempts SET dispatch_state='CANCELLED', "
                        "updated_at_ms=? WHERE mission_id=? AND control_epoch=? AND "
                        "dispatch_state='INTENT_COMMITTED'",
                        (now, command.mission_id, epoch),
                    )
                pause_resume = command.kind in {"PAUSE", "REVISE"}
                if pause_resume:
                    # R07/F07 (RP07): a paused step never strands RUNNING —
                    # but only a step whose attempt was SAFELY cancelled
                    # returns to PENDING. A step whose attempt was
                    # dispatched stays BLOCKED pending reconciliation, so
                    # resume can never silently replay an uncertain effect.
                    self._conn.execute(
                        "UPDATE mission_steps SET active_execution_id=NULL, "
                        "state=CASE WHEN state='RUNNING' AND NOT EXISTS ("
                        "  SELECT 1 FROM mission_attempts a WHERE "
                        "  a.mission_id=mission_steps.mission_id AND "
                        "  a.plan_version=mission_steps.plan_version AND "
                        "  a.step_id=mission_steps.step_id AND "
                        "  a.dispatch_state='RECONCILING') "
                        "THEN 'PENDING' ELSE CASE WHEN state='RUNNING' THEN 'BLOCKED' "
                        "ELSE state END END, updated_at_ms=? "
                        "WHERE mission_id=? AND state='RUNNING'",
                        (now, command.mission_id),
                    )
                else:
                    self._conn.execute(
                        "UPDATE mission_steps SET active_execution_id=NULL, updated_at_ms=? "
                        "WHERE mission_id=? AND state='RUNNING'",
                        (now, command.mission_id),
                    )
                if command.kind == "RESUME":
                    # C05: the owner's resume REAFFIRMS a pending approval —
                    # live approvals carry across the epoch bump so the
                    # approved typing loop can re-claim and consume them.
                    self._conn.execute(
                        "UPDATE mission_approvals SET control_epoch=? "
                        "WHERE mission_id=? AND control_epoch=? AND revoked=0 "
                        "AND consumed_at_ms IS NULL AND expires_at_ms>?",
                        (new_epoch, command.mission_id, epoch, now),
                    )
                else:
                    self._conn.execute(
                        "UPDATE mission_approvals SET revoked=1 "
                        "WHERE mission_id=? AND control_epoch=?",
                        (command.mission_id, epoch),
                    )
            if command.kind == "CANCEL":
                # Steps still PENDING/RUNNING are CANCELLED; terminal states stay.
                self._conn.execute(
                    "UPDATE mission_steps SET state=CASE WHEN state IN ('PENDING','RUNNING') "
                    "THEN 'CANCELLED' ELSE state END, active_execution_id=NULL, updated_at_ms=? "
                    "WHERE mission_id=?",
                    (now, command.mission_id),
                )
            self._conn.execute(
                "UPDATE missions SET status=?, control_epoch=?, updated_at_ms=? WHERE mission_id=?",
                (new_status, new_epoch, now, command.mission_id),
            )
            # C07/N06: revision and reason text are user-controlled and reach
            # durable storage inside this event. A REVISE whose text screens
            # as a secret is refused WHOLESALE (the transaction rolls back —
            # the text never persists and never reaches the planner); any
            # other control proceeds with the secret-shaped text replaced by
            # an explicit withheld marker, so a safety control is never
            # blocked by its own reason text.
            from assistant.memory.policy import contains_secret

            revision_text = (command.revision_request or "")[:2000]
            reason_text = command.reason[:1000]
            if command.kind == "REVISE":
                privacy = contains_secret(revision_text) or contains_secret(reason_text)
                if privacy is not None:
                    raise MissionStoreError(
                        f"revision refused by privacy policy: {privacy}"
                    )
            else:
                if contains_secret(revision_text) is not None:
                    revision_text = "[withheld by privacy policy]"
                if contains_secret(reason_text) is not None:
                    reason_text = "[withheld by privacy policy]"
            self._append_event(
                mission_id=command.mission_id,
                plan_version=plan_version,
                control_epoch=new_epoch,
                kind="correction" if command.kind == "REVISE" else "control",
                payload={
                    "kind": command.kind,
                    "reason": reason_text,
                    "revision": revision_text,
                    "from_status": status,
                    "to_status": new_status,
                },
                occurred_at_ms=now,
            )
            return self._get_mission_sync(command.mission_id)  # type: ignore[return-value]

        return self._tx(tx)

    # -- recovery ---------------------------------------------------------------------

    async def recover_inflight(self, host_generation: str) -> list[str]:
        """Mark uncertain attempts after a restart; bump epoch per mission.

        Returns the execution ids that now need reconciliation. This never
        replays GUI work: uncertainty is the honest state (file 03 §7).
        """
        return await self._run_tx(self._recover_inflight_sync, host_generation)

    def _recover_inflight_sync(self, host_generation: str) -> list[str]:
        now = _now_ms()

        def tx() -> list[str]:
            # Only attempts still owned by a LIVE execution transition here;
            # rows already RECONCILING (left by a pause or an earlier crash)
            # are settled through the service's reconciliation path, so a
            # repeated recovery over the same state finds nothing new.
            rows = self._conn.execute(
                "SELECT execution_id, mission_id, control_epoch FROM mission_attempts "
                "WHERE dispatch_state IN ('INTENT_COMMITTED','DISPATCHED')"
            ).fetchall()
            execution_ids: list[str] = []
            missions_touched: set[str] = set()
            for execution_id, mission_id, _epoch in rows:
                execution_ids.append(str(execution_id))
                missions_touched.add(str(mission_id))
                self._conn.execute(
                    "UPDATE mission_attempts SET dispatch_state='RECONCILING', updated_at_ms=? "
                    "WHERE execution_id=?",
                    (now, execution_id),
                )
            for mission_id in missions_touched:
                row = self._conn.execute(
                    "SELECT control_epoch, status FROM missions WHERE mission_id=?",
                    (mission_id,),
                ).fetchone()
                if row is None:
                    continue
                epoch, status = int(row[0]), str(row[1])
                if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    continue
                self._conn.execute(
                    "UPDATE missions SET control_epoch=?, updated_at_ms=? WHERE mission_id=?",
                    (epoch + 1, now, mission_id),
                )
                self._append_event(
                    mission_id=mission_id,
                    plan_version=self._plan_version_sync(mission_id),
                    control_epoch=epoch + 1,
                    kind="recovery",
                    payload={
                        "reason": "restart_reconciliation",
                        "host_generation": host_generation[:128],
                        "uncertain_executions": execution_ids,
                    },
                    occurred_at_ms=now,
                )
            return execution_ids

        return self._tx(tx)

    def _plan_version_sync(self, mission_id: str) -> int:
        row = self._conn.execute(
            "SELECT plan_version FROM missions WHERE mission_id=?", (mission_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    # -- events -----------------------------------------------------------------------

    def _append_event(
        self,
        *,
        mission_id: str,
        plan_version: int,
        control_epoch: int,
        kind: str,
        payload: dict[str, Any],
        occurred_at_ms: int,
        step_id: str | None = None,
        execution_id: str | None = None,
    ) -> TraceEvent:
        """Extend the mission's hash chain inside the caller's transaction."""
        row = self._conn.execute(
            "SELECT event_hash FROM mission_events WHERE mission_id=? "
            "ORDER BY sequence DESC LIMIT 1",
            (mission_id,),
        ).fetchone()
        previous_hash = str(row[0]) if row else ""
        # The sequence is the table's global AUTOINCREMENT value: the hash
        # must cover exactly what is stored, so it is sampled BEFORE the
        # event body is finalized (not per-mission, which diverges from the
        # rowid the table assigns).
        sequence_row = self._conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM mission_events"
        ).fetchone()
        sequence = int(sequence_row[0])
        event = TraceEvent(
            event_id=new_id(),
            sequence=sequence,
            trace_id=mission_id,
            mission_id=mission_id,
            plan_version=plan_version,
            control_epoch=control_epoch,
            step_id=step_id,
            execution_id=execution_id,
            kind=kind,
            safe_payload=payload,
            occurred_at_ms=occurred_at_ms,
            previous_hash=previous_hash,
        )
        self._conn.execute(
            """
            INSERT INTO mission_events (sequence, event_id, mission_id, plan_version,
                control_epoch, step_id, execution_id, kind, payload_json, event_hash,
                previous_hash, occurred_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sequence,
                event.event_id,
                mission_id,
                plan_version,
                control_epoch,
                step_id,
                execution_id,
                kind,
                canonical_json(payload),
                event.event_hash,
                previous_hash,
                occurred_at_ms,
            ),
        )
        return event

    async def get_events(self, mission_id: str, *, after_sequence: int = 0,
         limit: int = 200) -> list[TraceEvent]:
        return await self._run_tx(self._get_events_sync, mission_id, after_sequence, limit)

    def _get_events_sync(self, mission_id: str, after_sequence: int,
         limit: int) -> list[TraceEvent]:
        rows = self._conn.execute(
            "SELECT event_id, sequence, mission_id, plan_version, control_epoch, step_id, "
            "execution_id, kind, payload_json, event_hash, previous_hash, occurred_at_ms "
            "FROM mission_events WHERE mission_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (mission_id, after_sequence, limit),
        ).fetchall()
        events: list[TraceEvent] = []
        for row in rows:
            events.append(
                TraceEvent(
                    event_id=str(row[0]),
                    sequence=int(row[1]),
                    trace_id=mission_id,
                    mission_id=str(row[2]),
                    plan_version=int(row[3]),
                    control_epoch=int(row[4]),
                    step_id=row[5] if row[5] is None else str(row[5]),
                    execution_id=row[6] if row[6] is None else str(row[6]),
                    kind=str(row[7]),
                    safe_payload=self._usage_dict(row[8]),
                    event_hash=str(row[9]),
                    previous_hash=str(row[10]),
                    occurred_at_ms=int(row[11]),
                )
            )
        return events

    async def verify_chain(self, mission_id: str) -> bool:
        """Recompute the hash chain; False means the trace was modified."""
        return await self._run_tx(self._verify_chain_sync, mission_id)

    def _verify_chain_sync(self, mission_id: str) -> bool:
        previous = ""
        for event in self._get_events_sync(mission_id, 0, 10**9):
            if event.previous_hash != previous:
                return False
            if event.event_hash != event.compute_hash():
                return False
            previous = event.event_hash
        return True

    # -- approvals ----------------------------------------------------------------------

    async def record_approval(self, approval: ApprovalRecord) -> None:
        return await self._run_tx(self._record_approval_sync, approval)

    def _record_approval_sync(self, approval: ApprovalRecord) -> None:
        def tx() -> None:
            self._conn.execute(
                """
                INSERT INTO mission_approvals (approval_id, mission_id, plan_version,
                    control_epoch, action_digest, scope_hash, target_ref, account_ref,
                    workspace_ref, effect_class, maximum_units, issued_by, issued_at_ms,
                    expires_at_ms, single_use, consumed_at_ms, revoked)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)
                """,
                (
                    approval.approval_id,
                    approval.mission_id,
                    approval.plan_version,
                    approval.control_epoch,
                    approval.action_digest,
                    approval.scope_hash,
                    approval.target_ref,
                    approval.account_ref,
                    approval.workspace_ref,
                    approval.effect_class,
                    approval.maximum_units,
                    approval.issued_by,
                    approval.issued_at_ms,
                    approval.expires_at_ms,
                    1 if approval.single_use else 0,
                ),
            )
            self._append_event(
                mission_id=approval.mission_id,
                plan_version=approval.plan_version,
                control_epoch=approval.control_epoch,
                kind="approval",
                payload={
                    "approval_id": approval.approval_id,
                    "effect_class": approval.effect_class,
                    "action_digest": approval.action_digest,
                    "expires_at_ms": approval.expires_at_ms,
                },
                occurred_at_ms=approval.issued_at_ms,
            )

        self._tx(tx)

    async def consume_approval(self, approval_id: str, action_digest: str) -> bool:
        return await self._run_tx(self._consume_approval_sync, approval_id, action_digest)

    def _consume_approval_sync(self, approval_id: str, action_digest: str) -> bool:
        now = _now_ms()

        def tx() -> bool:
            row = self._conn.execute(
                "SELECT action_digest, expires_at_ms, consumed_at_ms, "
                "revoked FROM mission_approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            if row is None:
                return False
            stored_digest, expires_at, consumed_at, revoked = row
            if consumed_at is not None or int(revoked or 0) == 1:
                return False
            if int(expires_at) <= now:
                return False
            if str(stored_digest) != action_digest:
                return False
            self._conn.execute(
                "UPDATE mission_approvals SET consumed_at_ms=? WHERE approval_id=?",
                (now, approval_id),
            )
            return True

        return self._tx(tx)

    async def active_approval_ids(self, mission_id: str, control_epoch: int) -> list[str]:
        """Unconsumed, unrevoked, unexpired approvals for a mission/epoch.

        C02: approvals live in durable state, so a restart does not strand a
        pending approval behind an in-memory registration.
        """
        return await self._run_tx(self._active_approval_ids_sync, mission_id, control_epoch)

    def _active_approval_ids_sync(self, mission_id: str, control_epoch: int) -> list[str]:
        now = _now_ms()
        rows = self._conn.execute(
            "SELECT approval_id FROM mission_approvals "
            "WHERE mission_id=? AND control_epoch=? AND revoked=0 "
            "AND consumed_at_ms IS NULL AND expires_at_ms>?",
            (mission_id, control_epoch, now),
        ).fetchall()
        return [str(r[0]) for r in rows]

    # -- budget reservations ----------------------------------------------------------

    async def reserve_budget(self, mission_id: str, charge: BudgetCharge) -> BudgetReservation:
        return await self._run_tx(self._reserve_budget_sync, mission_id, charge)

    def _reserve_budget_sync(self, mission_id: str, charge: BudgetCharge) -> BudgetReservation:
        now = _now_ms()

        def tx() -> BudgetReservation:
            row = self._conn.execute(
                "SELECT limits_json, usage_json FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            limits = BudgetLimits.model_validate(self._usage_dict(row[0]))
            usage = BudgetUsage.model_validate(self._usage_dict(row[1]))
            if charge.call_key:
                existing = self._conn.execute(
                    "SELECT reservation_id, amount, status FROM mission_budget_reservations "
                    "WHERE mission_id=? AND resource=? AND call_key=?",
                    (mission_id, charge.resource, charge.call_key),
                ).fetchone()
                if existing is not None:
                    return BudgetReservation(
                        reservation_id=str(existing[0]),
                        mission_id=mission_id,
                        resource=charge.resource,
                        amount=int(existing[1]),
                        call_key=charge.call_key,
                        created_at_ms=now,
                        consumed=str(existing[2]) == "CONSUMED",
                    )
            ceiling = getattr(limits, f"max_{charge.resource}")
            if ceiling is not None and usage.total(charge.resource) + charge.amount > ceiling:
                raise MissionStoreError(
                    f"budget exhausted for {charge.resource}: "
                    f"{usage.total(charge.resource)}+{charge.amount} > {ceiling}"
                )
            reservation_id = new_id()
            usage.reserved[charge.resource] = (
                usage.reserved.get(charge.resource, 0) + charge.amount
            )
            self._conn.execute(
                """
                INSERT INTO mission_budget_reservations (reservation_id, mission_id, resource,
                    amount, call_key, status, created_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, 'RESERVED', ?, ?)
                """,
                (reservation_id, mission_id, charge.resource, charge.amount, charge.call_key, now,
                     now),
            )
            self._conn.execute(
                "UPDATE missions SET usage_json=?, updated_at_ms=? WHERE mission_id=?",
                (canonical_json(usage.model_dump()), now, mission_id),
            )
            return BudgetReservation(
                reservation_id=reservation_id,
                mission_id=mission_id,
                resource=charge.resource,
                amount=charge.amount,
                call_key=charge.call_key,
                created_at_ms=now,
            )

        return self._tx(tx)

    async def settle_reservation(
        self, reservation_id: str, *, consumed: bool
    ) -> None:
        """Move a reservation to CONSUMED or RELEASED atomically.

        Paid units are never released: an uncertain paid call stays charged
        (file 03 §6). Releasing other resources moves the amount back out of
        the reserved counter.
        """
        return await self._run_tx(
            self._settle_reservation_sync, reservation_id, consumed
        )

    def _settle_reservation_sync(self, reservation_id: str, consumed: bool) -> None:
        now = _now_ms()

        def tx() -> None:
            row = self._conn.execute(
                "SELECT mission_id, resource, amount, status FROM mission_budget_reservations "
                "WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown reservation: {reservation_id}")
            mission_id, resource, amount, status = (
                str(row[0]),
                str(row[1]),
                int(row[2]),
                str(row[3]),
            )
            if status != "RESERVED":
                return
            mrow = self._conn.execute(
                "SELECT usage_json FROM missions WHERE mission_id=?", (mission_id,)
            ).fetchone()
            usage = BudgetUsage.model_validate(self._usage_dict(mrow[0]))
            if consumed:
                usage.reserved[resource] = max(0, usage.reserved.get(resource, 0) - amount)
                usage.consumed[resource] = usage.consumed.get(resource, 0) + amount
                new_status = "CONSUMED"
            else:
                if resource == "paid_units":
                    raise MissionStoreError(
                        "an uncertain paid call is never released from the reservation"
                    )
                usage.reserved[resource] = max(0, usage.reserved.get(resource, 0) - amount)
                new_status = "RELEASED"
            self._conn.execute(
                "UPDATE mission_budget_reservations SET status=?, updated_at_ms=? "
                "WHERE reservation_id=?",
                (new_status, now, reservation_id),
            )
            self._conn.execute(
                "UPDATE missions SET usage_json=?, updated_at_ms=? WHERE mission_id=?",
                (canonical_json(usage.model_dump()), now, mission_id),
            )

        self._tx(tx)

    # -- evidence -----------------------------------------------------------------------

    async def put_evidence(self, ref: EvidenceRef) -> None:
        return await self._run_tx(self._put_evidence_sync, ref)

    def _put_evidence_sync(self, ref: EvidenceRef) -> None:
        def tx() -> None:
            self._conn.execute(
                """
                INSERT INTO mission_evidence (evidence_id, mission_id, execution_id, kind,
                    relative_path, sha256, classification, redaction_status,
                    redaction_version, inline_json, captured_at_ms, expires_at_ms, deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    ref.evidence_id,
                    ref.mission_id,
                    ref.execution_id,
                    ref.kind,
                    ref.relative_path,
                    ref.sha256,
                    ref.sensitivity,
                    ref.redaction_status,
                    ref.redaction_version,
                    canonical_json(ref.inline_facts) if ref.inline_facts is not None else None,
                    ref.captured_at_ms,
                    ref.expires_at_ms,
                ),
            )

        self._tx(tx)

    async def add_permitted_effects(self, mission_id: str, effects: set[str]) -> None:
        """Widen the mission scope's permitted effects (plan-driven, C05).

        The default mission scope is conservative; a committed plan that
        needs an EXTERNAL_WRITE step widens the scope EXPLICITLY, with an
        event recording exactly which effect was added and why. DESTRUCTIVE
        is never added (denied in Phase 1 regardless of configuration).
        """
        return await self._run_tx(self._add_permitted_effects_sync, mission_id, effects)

    def _add_permitted_effects_sync(self, mission_id: str, effects: set[str]) -> None:
        def tx() -> None:
            now = _now_ms()
            row = self._conn.execute(
                "SELECT scope_json FROM missions WHERE mission_id=?", (mission_id,)
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            scope = Scope.model_validate(self._usage_dict(row[0]))
            clean = {e for e in effects if e != "DESTRUCTIVE"}
            if not clean or clean <= scope.permitted_effects:
                return
            widened = Scope(
                owner_id=scope.owner_id,
                project_id=scope.project_id,
                account_ref=scope.account_ref,
                workspace_ref=scope.workspace_ref,
                allowed_apps=scope.allowed_apps,
                allowed_roots=scope.allowed_roots,
                allowed_origins=scope.allowed_origins,
                permitted_effects=set(scope.permitted_effects) | clean,
                policy_version=scope.policy_version,
            )
            self._conn.execute(
                "UPDATE missions SET scope_json=?, updated_at_ms=? WHERE mission_id=?",
                (canonical_json(widened.model_dump()), now, mission_id),
            )
            self._append_event(
                mission_id=mission_id,
                plan_version=self._plan_version_sync(mission_id),
                control_epoch=self._epoch_sync(mission_id),
                kind="plan",
                payload={
                    "scope_widened": sorted(clean),
                    "reason": "committed plan names a step of this effect class",
                },
                occurred_at_ms=now,
            )

        self._tx(tx)

    async def get_evidence(self, mission_id: str) -> list[EvidenceRef]:
        return await self._run_tx(self._get_evidence_sync, mission_id)

    def _get_evidence_sync(self, mission_id: str) -> list[EvidenceRef]:
        rows = self._conn.execute(
            "SELECT evidence_id, mission_id, execution_id, kind, relative_path, sha256, "
            "classification, redaction_status, redaction_version, inline_json, "
            "captured_at_ms, expires_at_ms, deleted "
            "FROM mission_evidence WHERE mission_id=? AND deleted=0",
            (mission_id,),
        ).fetchall()
        refs: list[EvidenceRef] = []
        for row in rows:
            refs.append(
                EvidenceRef(
                    evidence_id=str(row[0]),
                    mission_id=str(row[1]),
                    execution_id=row[2] if row[2] is None else str(row[2]),
                    kind=str(row[3]),
                    relative_path=row[4] if row[4] is None else str(row[4]),
                    sha256=str(row[5]),
                    sensitivity=str(row[6]),  # type: ignore[arg-type]
                    redaction_status=str(row[7]),  # type: ignore[arg-type]
                    redaction_version=str(row[8]),
                    inline_facts=self._usage_dict(row[9]) if row[9] else None,
                    captured_at_ms=int(row[10]),
                    expires_at_ms=row[11] if row[11] is None else int(row[11]),
                )
            )
        return refs

    # -- attempts --------------------------------------------------------------------------

    async def get_all_evidence_paths(self) -> list[str]:
        """Relative paths of every LIVE (non-deleted) evidence row."""
        return await self._run_tx(self._get_all_evidence_paths_sync)

    def _get_all_evidence_paths_sync(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT relative_path FROM mission_evidence WHERE deleted=0 AND "
            "relative_path IS NOT NULL"
        ).fetchall()
        return [str(r[0]) for r in rows]

    async def get_attempt(self, execution_id: str) -> dict[str, Any] | None:
        return await self._run_tx(self._get_attempt_sync, execution_id)

    def _get_attempt_sync(self, execution_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT execution_id, mission_id, plan_version, step_id, attempt, control_epoch, "
            "dispatch_state, effect_class, effect_outcome, result_json, lease_fence, external_ids "
            "FROM mission_attempts WHERE execution_id=?",
            (execution_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "execution_id": row[0],
            "mission_id": row[1],
            "plan_version": row[2],
            "step_id": row[3],
            "attempt": row[4],
            "control_epoch": row[5],
            "dispatch_state": row[6],
            "effect_class": row[7],
            "effect_outcome": row[8],
            "result": self._usage_dict(row[9]) if row[9] else None,
            "lease_fence": row[10],
            "external_ids": row[11],
        }

    async def mark_dispatched(self, execution_id: str) -> None:
        """Record that the packet actually left for the executor."""
        return await self._run_tx(self._mark_dispatched_sync, execution_id)

    def _mark_dispatched_sync(self, execution_id: str) -> None:
        def tx() -> None:
            now = _now_ms()
            row = self._conn.execute(
                "SELECT mission_id, plan_version, control_epoch, dispatch_state "
                "FROM mission_attempts WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise UnknownExecution(f"unknown execution: {execution_id}")
            if str(row[3]) != "INTENT_COMMITTED":
                raise MissionStoreError(
                    f"cannot dispatch from state {row[3]}; intent was invalidated"
                )
            self._conn.execute(
                "UPDATE mission_attempts SET dispatch_state='DISPATCHED', updated_at_ms=? "
                "WHERE execution_id=?",
                (now, execution_id),
            )
            self._append_event(
                mission_id=str(row[0]),
                plan_version=int(row[1]),
                control_epoch=int(row[2]),
                execution_id=execution_id,
                kind="action",
                payload={"dispatch": "sent"},
                occurred_at_ms=now,
            )

        self._tx(tx)

    # -- step states / check results / finalization ----------------------------------

    async def get_step_states(self, mission_id: str, plan_version: int) -> dict[str, str]:
        return await self._run_tx(self._get_step_states_sync, mission_id, plan_version)

    def _get_step_states_sync(self, mission_id: str, plan_version: int) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT step_id, state FROM mission_steps WHERE mission_id=? AND plan_version=?",
            (mission_id, plan_version),
        ).fetchall()
        return {str(r[0]): str(r[1]) for r in rows}

    async def get_check_results(self, mission_id: str, plan_version: int) -> dict[str, CheckResult]:
        """All CheckResults recorded by applied results for this plan."""
        return await self._run_tx(self._get_check_results_sync, mission_id, plan_version)

    def _get_check_results_sync(self, mission_id: str, plan_version: int) -> dict[str, CheckResult]:
        results: dict[str, CheckResult] = {}
        rows = self._conn.execute(
            "SELECT result_json FROM mission_attempts WHERE mission_id=? AND plan_version=? "
            "AND result_json IS NOT NULL",
            (mission_id, plan_version),
        ).fetchall()
        for row in rows:
            result = StepResult.model_validate(self._usage_dict(row[0]))
            for check in result.postconditions:
                results[check.check_id] = check
        return results

    async def record_plan_refusal(
        self, mission_id: str, expected_epoch: int, reason: str
    ) -> None:
        """Record an honest planning refusal (budget/scope/unsupported)."""
        return await self._run_tx(
            self._record_plan_refusal_sync, mission_id, expected_epoch, reason
        )

    def _record_plan_refusal_sync(
        self, mission_id: str, expected_epoch: int, reason: str
    ) -> None:
        def tx() -> None:
            now = _now_ms()
            row = self._conn.execute(
                "SELECT control_epoch, status FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            epoch, status = int(row[0]), str(row[1])
            if epoch != expected_epoch:
                raise StaleControlError("plan refusal lost its CAS")
            if status == "PLANNED":
                self._conn.execute(
                    "UPDATE missions SET status='BLOCKED', updated_at_ms=? WHERE mission_id=?",
                    (now, mission_id),
                )
            # C07/N06: refusal text may quote the refused submission — screen
            # before it lands in the trace.
            from assistant.memory.policy import contains_secret

            screened_reason = reason
            if contains_secret(reason) is not None:
                screened_reason = "[withheld by privacy policy]"
            self._append_event(
                mission_id=mission_id,
                plan_version=self._plan_version_sync(mission_id),
                control_epoch=epoch,
                kind="plan",
                payload={"refused": True, "reason": screened_reason[:400]},
                occurred_at_ms=now,
            )

        self._tx(tx)

    async def add_usage(self, mission_id: str, *, deltas: dict[str, Any]) -> None:
        """Merge one provider/resource usage delta into mission usage."""
        return await self._run_tx(self._add_usage_sync, mission_id, deltas)

    def _add_usage_sync(self, mission_id: str, deltas: dict[str, Any]) -> None:
        def tx() -> None:
            now = _now_ms()
            row = self._conn.execute(
                "SELECT usage_json FROM missions WHERE mission_id=?", (mission_id,)
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            usage = BudgetUsage.model_validate(self._usage_dict(row[0]))
            for resource, amount in deltas.get("consumed", {}).items():
                usage.consumed[resource] = usage.consumed.get(resource, 0) + int(amount)
            for resource, amount in deltas.get("reserved", {}).items():
                usage.reserved[resource] = usage.reserved.get(resource, 0) + int(amount)
            usage.known_input_tokens += int(deltas.get("known_input_tokens", 0))
            usage.known_output_tokens += int(deltas.get("known_output_tokens", 0))
            usage.known_cache_tokens += int(deltas.get("known_cache_tokens", 0))
            usage.known_reasoning_tokens += int(deltas.get("known_reasoning_tokens", 0))
            usage.unknown_usage_calls += int(deltas.get("unknown_usage_calls", 0))
            usage.external_wait_ms += int(deltas.get("external_wait_ms", 0))
            usage.active_ms += int(deltas.get("active_ms", 0))
            self._conn.execute(
                "UPDATE missions SET usage_json=?, updated_at_ms=? WHERE mission_id=?",
                (canonical_json(usage.model_dump()), now, mission_id),
            )

        self._tx(tx)

    async def put_payload(self, mission_id: str, ref: str, content: str) -> str:
        """Store mission-owned exact user text, privacy-screened (R06/R09).

        Secret-shaped payloads are REFUSED: dictated text that screens as a
        secret never reaches durable storage. Returns the content digest.
        """
        from assistant.memory.policy import contains_secret

        reason = contains_secret(content)
        if reason is not None:
            raise MissionStoreError(
                f"payload refused by privacy policy: {reason}"
            )
        digest = text_digest(content)
        await self._run_tx(self._put_payload_sync, mission_id, ref, content, digest)
        return digest

    def _put_payload_sync(
        self, mission_id: str, ref: str, content: str, digest: str
    ) -> None:
        def tx() -> None:
            self._conn.execute(
                """
                INSERT INTO mission_payloads (mission_id, ref, content,
                    content_digest, created_at_ms)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (mission_id, ref) DO NOTHING
                """,
                (mission_id, ref, content, digest, _now_ms()),
            )

        self._tx(tx)

    async def get_payload(self, mission_id: str, ref: str) -> str | None:
        return await self._run_tx(self._get_payload_sync, mission_id, ref)

    def get_payload_sync(self, mission_id: str, ref: str) -> str | None:
        return self._get_payload_sync(mission_id, ref)

    def _get_payload_sync(self, mission_id: str, ref: str) -> str | None:
        row = self._conn.execute(
            "SELECT content FROM mission_payloads WHERE mission_id=? AND ref=?",
            (mission_id, ref),
        ).fetchone()
        return str(row[0]) if row else None

    async def record_reconciliation(self, execution_id: str, outcome: str) -> None:
        """Persist a reconciler's answer on an uncertain attempt."""
        return await self._run_tx(self._record_reconciliation_sync, execution_id, outcome)

    def _record_reconciliation_sync(self, execution_id: str, outcome: str) -> None:
        def tx() -> None:
            now = _now_ms()
            row = self._conn.execute(
                "SELECT mission_id FROM mission_attempts WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise UnknownExecution(f"unknown execution: {execution_id}")
            self._conn.execute(
                "UPDATE mission_attempts SET effect_outcome=?, updated_at_ms=? "
                "WHERE execution_id=?",
                (outcome, now, execution_id),
            )
            self._append_event(
                mission_id=str(row[0]),
                plan_version=self._plan_version_sync(str(row[0])),
                control_epoch=self._epoch_sync(str(row[0])),
                execution_id=execution_id,
                kind="recovery",
                payload={"reconciliation": outcome},
                occurred_at_ms=now,
            )

        self._tx(tx)

    # -- reconciliation settlement (C03/N01) --------------------------------------

    async def reconciling_attempts(self, mission_id: str) -> list[dict[str, Any]]:
        """The uncertain dispatched attempts of one mission awaiting settlement."""
        return await self._run_tx(self._reconciling_attempts_sync, mission_id)

    async def missions_with_reconciling(self) -> list[str]:
        """Mission ids holding any RECONCILING attempt (startup settlement)."""
        return await self._run_tx(self._missions_with_reconciling_sync)

    def _missions_with_reconciling_sync(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT mission_id FROM mission_attempts WHERE dispatch_state='RECONCILING'"
        ).fetchall()
        return [str(r[0]) for r in rows]

    def _reconciling_attempts_sync(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT execution_id, step_id, plan_version, effect_class FROM mission_attempts "
            "WHERE mission_id=? AND dispatch_state='RECONCILING'",
            (mission_id,),
        ).fetchall()
        return [
            {
                "execution_id": str(r[0]),
                "step_id": str(r[1]),
                "plan_version": int(r[2]),
                "effect_class": str(r[3]),
            }
            for r in rows
        ]

    async def mark_attempt_reconciled(self, execution_id: str, outcome: str) -> None:
        """RECONCILING -> RECONCILED with the reconciled effect outcome."""
        return await self._run_tx(
            self._mark_attempt_reconciled_sync, execution_id, outcome
        )

    def _mark_attempt_reconciled_sync(self, execution_id: str, outcome: str) -> None:
        def tx() -> None:
            now = _now_ms()
            row = self._conn.execute(
                "SELECT mission_id, dispatch_state FROM mission_attempts WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise UnknownExecution(f"unknown execution: {execution_id}")
            if str(row[1]) != "RECONCILING":
                return  # already settled (idempotent)
            self._conn.execute(
                "UPDATE mission_attempts SET dispatch_state='RECONCILED', effect_outcome=?, "
                "updated_at_ms=? WHERE execution_id=?",
                (outcome, now, execution_id),
            )
            self._append_event(
                mission_id=str(row[0]),
                plan_version=self._plan_version_sync(str(row[0])),
                control_epoch=self._epoch_sync(str(row[0])),
                execution_id=execution_id,
                kind="recovery",
                payload={"reconciled": outcome, "state": "RECONCILED"},
                occurred_at_ms=now,
            )

        self._tx(tx)

    async def release_step_for_retry(self, mission_id: str, plan_version: int,
         step_id: str) -> bool:
        """Return a reconciled-proven-NO_EFFECT step to PENDING for one retry.

        C03: a proven NO_EFFECT is necessary but not sufficient — the retry
        budget is the other gate. Returns False (and leaves the step
        BLOCKED) when the step's retry allowance is exhausted.
        """
        return await self._run_tx(
            self._release_step_for_retry_sync, mission_id, plan_version, step_id
        )

    def _release_step_for_retry_sync(self, mission_id: str, plan_version: int,
         step_id: str) -> bool:
        now = _now_ms()

        def tx() -> bool:
            row = self._conn.execute(
                "SELECT attempt_count, state, step_json FROM mission_steps "
                "WHERE mission_id=? AND plan_version=? AND step_id=?",
                (mission_id, plan_version, step_id),
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown step {step_id} for retry release")
            attempt_count, state = int(row[0]), str(row[1])
            limits_row = self._conn.execute(
                "SELECT limits_json FROM missions WHERE mission_id=?", (mission_id,)
            ).fetchone()
            limits = BudgetLimits.model_validate(self._usage_dict(limits_row[0]))
            if attempt_count > limits.max_retries:
                self._append_event(
                    mission_id=mission_id,
                    plan_version=plan_version,
                    control_epoch=self._epoch_sync(mission_id),
                    step_id=step_id,
                    kind="recovery",
                    payload={
                        "retry_refused": True,
                        "reason": f"attempt {attempt_count} exceeds max_retries "
                        f"{limits.max_retries}",
                    },
                    occurred_at_ms=now,
                )
                return False
            if state in {"BLOCKED", "PENDING"}:
                self._conn.execute(
                    "UPDATE mission_steps SET state='PENDING', active_execution_id=NULL, "
                    "updated_at_ms=? WHERE mission_id=? AND plan_version=? AND step_id=?",
                    (now, mission_id, plan_version, step_id),
                )
                return True
            return False

        return self._tx(tx)

    async def release_approved_blocked_steps(self, mission_id: str, plan_version: int) -> int:
        """Re-queue BLOCKED steps when a fresh owner approval exists.

        C05: the approved typing loop — a step refused for APPROVAL_REQUIRED
        blocks; once the host mints an approval for the pending action and
        the owner resumes, the step may re-claim (still within its retry
        budget). Returns the number of steps released.
        """
        return await self._run_tx(
            self._release_approved_blocked_steps_sync, mission_id, plan_version
        )

    def _release_approved_blocked_steps_sync(self, mission_id: str, plan_version: int) -> int:
        now = _now_ms()

        def tx() -> int:
            approvals = self._conn.execute(
                "SELECT COUNT(*) FROM mission_approvals WHERE mission_id=? AND control_epoch="
                "(SELECT control_epoch FROM missions WHERE mission_id=?) AND revoked=0 "
                "AND consumed_at_ms IS NULL AND expires_at_ms>?",
                (mission_id, mission_id, now),
            ).fetchone()
            if not approvals or int(approvals[0]) == 0:
                return 0
            limits_row = self._conn.execute(
                "SELECT limits_json FROM missions WHERE mission_id=?", (mission_id,)
            ).fetchone()
            limits = BudgetLimits.model_validate(self._usage_dict(limits_row[0]))
            released = 0
            rows = self._conn.execute(
                "SELECT step_id, attempt_count, state FROM mission_steps "
                "WHERE mission_id=? AND plan_version=? AND state='BLOCKED'",
                (mission_id, plan_version),
            ).fetchall()
            for step_id, attempt_count, _state in rows:
                if int(attempt_count) > limits.max_retries:
                    continue
                self._conn.execute(
                    "UPDATE mission_steps SET state='PENDING', active_execution_id=NULL, "
                    "updated_at_ms=? WHERE mission_id=? AND plan_version=? AND step_id=?",
                    (now, mission_id, plan_version, str(step_id)),
                )
                self._append_event(
                    mission_id=mission_id,
                    plan_version=plan_version,
                    control_epoch=self._epoch_sync(mission_id),
                    step_id=str(step_id),
                    kind="control",
                    payload={
                        "kind": "RESUME",
                        "reason": "approval present; blocked step re-queued",
                    },
                    occurred_at_ms=now,
                )
                released += 1
            return released

        return self._tx(tx)

    async def mark_mission_blocked(self, mission_id: str, reason: str) -> MissionRecord:
        """RUNNING/WAITING -> BLOCKED with an honest reason (no epoch bump:
        reconciliation settlement does not invalidate anything new)."""
        return await self._run_tx(self._mark_mission_blocked_sync, mission_id, reason)

    def _mark_mission_blocked_sync(self, mission_id: str, reason: str) -> MissionRecord:
        now = _now_ms()

        def tx() -> MissionRecord:
            row = self._conn.execute(
                "SELECT status FROM missions WHERE mission_id=?", (mission_id,)
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            status = str(row[0])
            if status in {"COMPLETED", "FAILED", "CANCELLED", "BLOCKED"}:
                return self._get_mission_sync(mission_id)  # type: ignore[return-value]
            self._conn.execute(
                "UPDATE missions SET status='BLOCKED', updated_at_ms=? WHERE mission_id=?",
                (now, mission_id),
            )
            self._append_event(
                mission_id=mission_id,
                plan_version=self._plan_version_sync(mission_id),
                control_epoch=self._epoch_sync(mission_id),
                kind="recovery",
                payload={"blocked": True, "reason": reason[:400]},
                occurred_at_ms=now,
            )
            return self._get_mission_sync(mission_id)  # type: ignore[return-value]

        return self._tx(tx)

    async def mark_verifying(self, mission_id: str, expected_epoch: int) -> MissionRecord:
        """PLANNED/RUNNING -> VERIFYING (e.g. a plan that needs no steps)."""
        return await self._run_tx(self._mark_verifying_sync, mission_id, expected_epoch)

    def _mark_verifying_sync(self, mission_id: str, expected_epoch: int) -> MissionRecord:
        now = _now_ms()

        def tx() -> MissionRecord:
            row = self._conn.execute(
                "SELECT control_epoch, status FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            epoch, status = int(row[0]), str(row[1])
            if epoch != expected_epoch:
                raise StaleControlError("verify transition lost its CAS")
            if status not in {"PLANNED", "RUNNING"}:
                raise MissionStoreError(f"cannot verify from {status}")
            self._conn.execute(
                "UPDATE missions SET status='VERIFYING', updated_at_ms=? WHERE mission_id=?",
                (now, mission_id),
            )
            return self._get_mission_sync(mission_id)  # type: ignore[return-value]

        return self._tx(tx)

    async def finalize_mission(
        self, mission_id: str, expected_epoch: int, final_status: str
    ) -> MissionRecord:
        """VERIFYING -> COMPLETED/FAILED/BLOCKED, computed by the caller's gate.

        Only a mission in VERIFYING can finalize; the status must be one of
        the three honest terminal states.
        """
        return await self._run_tx(self._finalize_mission_sync, mission_id, expected_epoch,
             final_status)

    def _finalize_mission_sync(
        self, mission_id: str, expected_epoch: int, final_status: str
    ) -> MissionRecord:
        now = _now_ms()

        def tx() -> MissionRecord:
            row = self._conn.execute(
                "SELECT control_epoch, status FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            epoch, status = int(row[0]), str(row[1])
            if epoch != expected_epoch:
                raise StaleControlError("finalization lost its CAS")
            if status != "VERIFYING":
                raise MissionStoreError(f"only VERIFYING missions finalize; this is {status}")
            if final_status not in {"COMPLETED", "FAILED", "BLOCKED"}:
                raise MissionStoreError(f"invalid final status {final_status}")
            self._conn.execute(
                "UPDATE missions SET status=?, updated_at_ms=? WHERE mission_id=?",
                (final_status, now, mission_id),
            )
            self._append_event(
                mission_id=mission_id,
                plan_version=self._plan_version_sync(mission_id),
                control_epoch=epoch,
                kind="verification",
                payload={"final_status": final_status, "gate": "deterministic_acceptance"},
                occurred_at_ms=now,
            )
            return self._get_mission_sync(mission_id)  # type: ignore[return-value]

        return self._tx(tx)

    async def record_final_review(
        self, mission_id: str, expected_epoch: int, *, summary: str,
        unresolved_issues: list[str],
    ) -> None:
        """Persist the advisory final REVIEW next to the deterministic gate.

        The review is evidence in the trace only: it cannot set, flip, or
        defer the terminal status (file 03 §7 — the deterministic gate owns
        completion).
        """
        return await self._run_tx(
            self._record_final_review_sync, mission_id, expected_epoch, summary,
            unresolved_issues,
        )

    def _record_final_review_sync(
        self, mission_id: str, expected_epoch: int, summary: str,
        unresolved_issues: list[str],
    ) -> None:
        def tx() -> None:
            now = _now_ms()
            row = self._conn.execute(
                "SELECT control_epoch FROM missions WHERE mission_id=?", (mission_id,)
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            epoch = int(row[0])
            if epoch != expected_epoch:
                raise StaleControlError("final review lost its CAS")
            self._append_event(
                mission_id=mission_id,
                plan_version=self._plan_version_sync(mission_id),
                control_epoch=epoch,
                kind="verification",
                payload={
                    "final_review": {
                        "summary": summary,
                        "unresolved_issues": unresolved_issues,
                    },
                    "advisory": True,
                },
                occurred_at_ms=now,
            )

        self._tx(tx)

    # -- retention (C07/N06) -----------------------------------------------------------

    async def enforce_retention(
        self, now_ms: int, *, hold_missions: frozenset[str] | set[str] = frozenset()
    ) -> dict[str, Any]:
        """Owner-bounded retention sweep over mission evidence.

        C07: evidence past its expiry is deleted (row marked deleted, file
        removed by the EvidenceStore sweep) UNLESS its mission is under an
        explicit investigation hold; evidence rows whose mission has
        vanished are orphans and go too. Every deletion writes a tombstone
        event on the mission's trace (or the retention log for orphans).
        Trace events themselves are NEVER deleted by this sweep: they are
        hash-chained, and a silent event delete would be indistinguishable
        from tampering — metadata expiry beyond evidence is an explicit
        owner action, not an automatic one.
        """
        return await self._run_tx(self._enforce_retention_sync, now_ms, set(hold_missions))

    def _enforce_retention_sync(self, now_ms: int, holds: set[str]) -> dict[str, Any]:
        def tx() -> dict[str, Any]:
            deleted_by_mission: dict[str, list[str]] = {}
            orphan_paths: list[str] = []
            # Expired, non-held evidence...
            rows = self._conn.execute(
                "SELECT evidence_id, mission_id, relative_path, expires_at_ms "
                "FROM mission_evidence WHERE deleted=0 AND expires_at_ms IS NOT NULL "
                "AND expires_at_ms<=?",
                (now_ms,),
            ).fetchall()
            # ...and C07 orphans: rows whose mission vanished. Their
            # deletion does not wait for an expiry that may never have been
            # set.
            rows += self._conn.execute(
                "SELECT evidence_id, mission_id, relative_path, NULL FROM mission_evidence "
                "WHERE deleted=0 AND mission_id NOT IN (SELECT mission_id FROM missions)"
            ).fetchall()
            for evidence_id, mission_id, relative_path, _expires in rows:
                evidence_id, mission_id = str(evidence_id), str(mission_id)
                path = str(relative_path or "")
                known = self._conn.execute(
                    "SELECT 1 FROM missions WHERE mission_id=?", (mission_id,)
                ).fetchone()
                if known is not None and mission_id in holds:
                    continue
                self._conn.execute(
                    "UPDATE mission_evidence SET deleted=1 WHERE evidence_id=?",
                    (evidence_id,),
                )
                if known is None:
                    orphan_paths.append(path)
                    self._conn.execute(
                        "INSERT INTO mission_retention_log "
                        "(recorded_at_ms, kind, subject_id, detail_json) VALUES (?, ?, ?, ?)",
                        (
                            now_ms,
                            "orphan_evidence",
                            evidence_id,
                            canonical_json({"mission_id": mission_id, "path": path}),
                        ),
                    )
                    continue
                deleted_by_mission.setdefault(mission_id, []).append(
                    path or evidence_id
                )
            for mission_id, evidence in deleted_by_mission.items():
                self._append_event(
                    mission_id=mission_id,
                    plan_version=self._plan_version_sync(mission_id),
                    control_epoch=self._epoch_sync(mission_id),
                    kind="retention",
                    payload={
                        "deleted_evidence": evidence[:32],
                        "tombstone": True,
                        "holds_preserved": sorted(holds)[:16],
                    },
                    occurred_at_ms=now_ms,
                )
            return {
                "deleted_evidence": sum(len(v) for v in deleted_by_mission.values()),
                "orphan_evidence": len(orphan_paths),
                "deleted_paths": [
                    str(p)
                    for paths in deleted_by_mission.values()
                    for p in paths
                ] + orphan_paths,
            }

        return self._tx(tx)

    # -- goal revisions (append-only) --------------------------------------------------

    async def record_goal_revision(
        self, mission_id: str, expected_epoch: int, revised_goal: str, reason: str
    ) -> MissionRecord:
        return await self._run_tx(
            self._record_goal_revision_sync, mission_id, expected_epoch, revised_goal, reason
        )

    def _record_goal_revision_sync(
        self, mission_id: str, expected_epoch: int, revised_goal: str, reason: str
    ) -> MissionRecord:
        from assistant.memory.policy import contains_secret

        revision_privacy = contains_secret(revised_goal)
        if revision_privacy is not None:
            raise MissionStoreError(
                f"revision refused by privacy policy: {revision_privacy}"
            )
        now = _now_ms()

        def tx() -> MissionRecord:
            row = self._conn.execute(
                "SELECT control_epoch, status, original_goal FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if row is None:
                raise MissionStoreError(f"unknown mission: {mission_id}")
            epoch, status, original_goal = int(row[0]), str(row[1]), str(row[2])
            if epoch != expected_epoch:
                raise StaleControlError("goal revision lost its CAS")
            if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                raise MissionStoreError("cannot revise a terminal mission")
            self._append_event(
                mission_id=mission_id,
                plan_version=self._plan_version_sync(mission_id),
                control_epoch=epoch,
                kind="correction",
                payload={
                    "original_goal": original_goal,
                    "revision": revised_goal[:4000],
                    "reason": reason[:1000],
                },
                occurred_at_ms=now,
            )
            return self._get_mission_sync(mission_id)  # type: ignore[return-value]

        return self._tx(tx)


__all__ = [
    "ApplyOutcome",
    "MissionIdentityCollision",
    "MissionStore",
    "MissionStoreError",
    "ResultConflict",
    "StaleControlError",
    "UnknownExecution",
    "text_digest",
]
