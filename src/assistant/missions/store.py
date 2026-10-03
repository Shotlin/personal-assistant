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
    PendingApprovalDigest,
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

#: D02: exact approval binding + durable external waits arrive as migration 4
#: so existing mission databases upgrade additively (checksum-verified).
#: ``pending_action_digest`` is the exact digest the BLOCKED step owes an
#: owner approval for; ``mission_external_waits`` persists wait reason,
#: retry-after/deadline and the resume checkpoint across restarts.
_EXACT_APPROVAL_WAIT_MIGRATION = """
ALTER TABLE mission_steps ADD COLUMN pending_action_digest TEXT NOT NULL DEFAULT '';
ALTER TABLE mission_steps ADD COLUMN pending_tool TEXT NOT NULL DEFAULT '';
ALTER TABLE mission_steps ADD COLUMN pending_target_ref TEXT;

CREATE TABLE IF NOT EXISTS mission_external_waits (
    wait_id     TEXT PRIMARY KEY,
    mission_id  TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    step_id     TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    retry_after_ms INTEGER NOT NULL,
    deadline_ms  INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL,
    released_at_ms INTEGER
);
CREATE INDEX IF NOT EXISTS mission_external_waits_open_idx
    ON mission_external_waits (mission_id, released_at_ms);

CREATE TABLE IF NOT EXISTS mission_provider_requests (
    request_id  TEXT PRIMARY KEY,
    mission_id  TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    role        TEXT NOT NULL DEFAULT '',
    call_key    TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER,
    output_tokens INTEGER,
    created_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mission_provider_requests_idx
    ON mission_provider_requests (mission_id, plan_version);
"""

#: D11: attempted/completed/failed request states arrive as migration 5 so
#: existing mission databases upgrade additively (checksum-verified).
_PROVIDER_STATE_MIGRATION = """
ALTER TABLE mission_provider_requests ADD COLUMN state TEXT NOT NULL DEFAULT 'COMPLETED';
ALTER TABLE mission_provider_requests ADD COLUMN updated_at_ms INTEGER NOT NULL DEFAULT 0;
"""

#: D16: durable retention holds + metadata-retention tombstones arrive as
#: migration 6. A hold survives restarts and is honored by BOTH the normal
#: (mission-observed) and idle (startup) retention sweeps.
_RETENTION_HOLDS_MIGRATION = """
CREATE TABLE IF NOT EXISTS mission_retention_holds (
    mission_id  TEXT PRIMARY KEY,
    reason      TEXT NOT NULL DEFAULT '',
    held_at_ms  INTEGER NOT NULL,
    released_at_ms INTEGER
);
"""

# D14: a wait is authority-bearing resume state.  Its plan and control epoch
# must survive restart and be compared inside the release transaction.
_WAIT_EPOCH_MIGRATION = """
ALTER TABLE mission_external_waits ADD COLUMN control_epoch INTEGER NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS mission_external_waits_identity_idx
    ON mission_external_waits (mission_id, plan_version, control_epoch, released_at_ms);
"""

# D16: a row can be safely tombstoned before its artifact file is removed,
# but an unlink failure must remain durable and retryable across restart.
_FILE_DELETION_QUEUE_MIGRATION = """
CREATE TABLE IF NOT EXISTS mission_file_deletions (
    relative_path TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    queued_at_ms INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS mission_file_deletions_mission_idx
    ON mission_file_deletions (mission_id);
"""

#: Applied in order; each entry is (version, checksum, statements).
_MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (_MISSION_SCHEMA_VERSION, digest_of(_MISSION_SCHEMA), _MISSION_SCHEMA),
    (2, digest_of(_ACTION_LEDGER_MIGRATION), _ACTION_LEDGER_MIGRATION),
    (3, digest_of(_RETENTION_LOG_MIGRATION), _RETENTION_LOG_MIGRATION),
    (4, digest_of(_EXACT_APPROVAL_WAIT_MIGRATION), _EXACT_APPROVAL_WAIT_MIGRATION),
    (5, digest_of(_PROVIDER_STATE_MIGRATION), _PROVIDER_STATE_MIGRATION),
    (6, digest_of(_RETENTION_HOLDS_MIGRATION), _RETENTION_HOLDS_MIGRATION),
    (7, digest_of(_WAIT_EPOCH_MIGRATION), _WAIT_EPOCH_MIGRATION),
    (8, digest_of(_FILE_DELETION_QUEUE_MIGRATION), _FILE_DELETION_QUEUE_MIGRATION),
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


def _screen_private(text: str) -> str:
    """Screen one text at a persistence/model sink (D07).

    Secret-shaped content is replaced by an explicit withheld marker; the
    fact that something was withheld stays honest, the content never
    persists.
    """
    if not text:
        return text
    from assistant.memory.policy import contains_secret

    return "[withheld by privacy policy]" if contains_secret(text) is not None else text


_CHECKPOINT_MAX_KEYS = 16
_CHECKPOINT_MAX_VALUE_CHARS = 200
_CHECKPOINT_MAX_DEPTH = 3


def _safe_checkpoint_json(raw: Any) -> dict[str, Any]:
    """D16: a damaged durable row is reported, never fatal to recovery."""
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {"checkpoint": parsed}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"damaged": True}


def _bound_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """D16: bound a checkpoint by STRUCTURE after screening, so the
    serialized form is always valid JSON within a bounded size."""
    def walk(value: Any, depth: int) -> Any:
        if depth > _CHECKPOINT_MAX_DEPTH:
            return "[depth-bound]"
        if isinstance(value, dict):
            bounded: dict[str, Any] = {}
            for key in sorted(value.keys())[:_CHECKPOINT_MAX_KEYS]:
                # Keys reach durable JSON just as values do.  A token-shaped
                # map key is still a secret and must be screened before any
                # truncation/bounding preserves it.
                safe_key = _screen_private(str(key))[:64]
                bounded[safe_key] = walk(value[key], depth + 1)
            return bounded
        if isinstance(value, list):
            return [walk(item, depth + 1) for item in value[:_CHECKPOINT_MAX_KEYS]]
        if isinstance(value, str):
            return _screen_private(value)[:_CHECKPOINT_MAX_VALUE_CHARS]
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, int):
            return value
        return _screen_private(str(value))[:_CHECKPOINT_MAX_VALUE_CHARS]

    if not isinstance(checkpoint, dict):
        return {"checkpoint": _screen_private(str(checkpoint))[:_CHECKPOINT_MAX_VALUE_CHARS]}
    return walk(checkpoint, 0)


def _screen_result(result: StepResult) -> dict[str, Any]:
    """A StepResult projection with sink-screened free text (D07)."""
    dump = result.model_dump()
    dump["uncertainty"] = _screen_private(str(dump.get("uncertainty") or ""))[:512]
    dump["suggested_next_action"] = _screen_private(
        str(dump.get("suggested_next_action") or "")
    )[:512] if dump.get("suggested_next_action") is not None else None
    return dump


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
        def tx() -> int:
            now = _now_ms()
            # The ledger is the last durable boundary before a mutating tool.
            # Persist uncertainty in the SAME transaction as its action row.
            row = self._store._conn.execute(
                "SELECT a.dispatch_state, a.plan_version, a.control_epoch, "
                "m.plan_version, m.control_epoch, m.status FROM mission_attempts a "
                "JOIN missions m ON m.mission_id=a.mission_id "
                "WHERE a.execution_id=? AND a.mission_id=?",
                (self.execution_id, self.mission_id),
            ).fetchone()
            if row is None or str(row[0]) not in {"INTENT_COMMITTED", "DISPATCHED"}:
                raise MissionStoreError("action intent is no longer dispatchable")
            if row[1] != row[3] or row[2] != row[4] or row[5] != "RUNNING":
                raise StaleControlError("action intent lost its current mission epoch")
            self._store._conn.execute(
                "UPDATE mission_attempts SET dispatch_state='DISPATCHED', updated_at_ms=? "
                "WHERE execution_id=?", (now, self.execution_id),
            )
            ledger = self._store._conn.execute(
                "INSERT INTO mission_action_ledger (execution_id, mission_id, tool_name, "
                "args_digest, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'planned', ?, ?) RETURNING ledger_id",
                (self.execution_id, self.mission_id, tool_name, args_digest, now, now),
            ).fetchone()
            assert ledger is not None
            return int(ledger[0])

        return self._store._tx(tx)

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
        # D02/D10: the exact approvals the host UI owes right now, read from
        # durable step state — never reconstructed from prose or model text.
        pending = self._conn.execute(
            "SELECT step_id, pending_tool, pending_action_digest, pending_target_ref "
            "FROM mission_steps "
            "WHERE mission_id=? AND plan_version=? AND state='BLOCKED' "
            "AND pending_action_digest!=''",
            (mission_id, record.plan_version),
        ).fetchall()
        by_step = {step.step_id: step for step in record.steps}
        record.pending_approvals = []
        for step_id, tool, action_digest, target_ref in pending:
            step = by_step.get(str(step_id))
            if step is None:
                continue
            record.pending_approvals.append(PendingApprovalDigest(
                step_id=str(step_id), tool=str(tool), action_digest=str(action_digest),
                plan_version=record.plan_version, control_epoch=record.control_epoch,
                target_ref=target_ref if target_ref is None else str(target_ref),
                account_ref=step.scope.account_ref,
                workspace_ref=step.scope.workspace_ref,
                effect_class=step.effect_class,
            ))
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
            unresolved = self._conn.execute(
                "SELECT 1 FROM mission_attempts WHERE mission_id=? AND "
                "(dispatch_state IN ('DISPATCHED', 'RECONCILING') OR "
                "(dispatch_state='RECONCILED' AND effect_outcome!='NO_EFFECT')) LIMIT 1",
                (mission_id,),
            ).fetchone()
            if unresolved is not None:
                raise MissionStoreError("cannot replace a plan with an unresolved effect")
            new_version = expected_version + 1
            plan_doc = {
                "steps": [s.model_dump() for s in steps],
                "criteria": [c.model_dump() for c in criteria],
            }
            from assistant.memory.policy import contains_secret

            if contains_secret(canonical_json(plan_doc)) or contains_secret(reason + creator):
                raise MissionStoreError("plan refused by privacy policy")
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
                    # D07: the outcome sink is screened — a canary riding in
                    # an exception or refusal text never persists verbatim.
                    canonical_json(_screen_result(result)),
                    result_digest,
                    now,
                    result.execution_id,
                ),
            )
            self._conn.execute(
                """
                UPDATE mission_steps SET state=?, active_execution_id=NULL, updated_at_ms=?,
                    pending_action_digest=?, pending_tool=?, pending_target_ref=?
                WHERE mission_id=? AND plan_version=? AND step_id=?
                """,
                (
                    step_state,
                    now,
                    # D02: persist the exact approval this BLOCKED step owes.
                    # Any other transition clears it — a stale digest must
                    # never survive onto a step that left the approval gate.
                    (
                        result.pending_approval.action_digest
                        if step_state == "BLOCKED" and result.pending_approval else ""
                    ),
                    (
                        result.pending_approval.tool
                        if step_state == "BLOCKED" and result.pending_approval else ""
                    ),
                    (
                        result.pending_approval.target_ref
                        if step_state == "BLOCKED" and result.pending_approval else None
                    ),
                    attempt_mission,
                    attempt_plan,
                    attempt_step,
                ),
            )
            if step_state == "BLOCKED" and result.pending_approval is not None:
                # D13: an approval block holds the mission at
                # NEEDS_APPROVAL immediately — even with dependent PENDING
                # steps — so the owner's resume path is always available.
                if mission_status == "RUNNING":
                    self._conn.execute(
                        "UPDATE missions SET status='NEEDS_APPROVAL', "
                        "resume_cursor=?, updated_at_ms=? WHERE mission_id=?",
                        (attempt_step, now, attempt_mission),
                    )
                self._append_event(
                    mission_id=attempt_mission,
                    plan_version=attempt_plan,
                    control_epoch=mission_epoch,
                    step_id=attempt_step,
                    kind="approval",
                    payload={
                        "required": True,
                        "step_id": attempt_step,
                        "tool": result.pending_approval.tool,
                        "action_digest": result.pending_approval.action_digest,
                        "plan_version": attempt_plan,
                        "control_epoch": mission_epoch,
                    },
                    occurred_at_ms=now,
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
            # final verdict from here. D02: a step blocked at the approval
            # gate holds the mission at NEEDS_APPROVAL instead — the owner,
            # not the acceptance gate, decides what happens next.
            states = self._conn.execute(
                "SELECT state FROM mission_steps WHERE mission_id=? AND plan_version=?",
                (attempt_mission, attempt_plan),
            ).fetchall()
            if all(str(s[0]) in {"SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED",
                 "SKIPPED"} for s in states):
                new_status = "VERIFYING" if mission_status == "RUNNING" else mission_status
                if new_status == "VERIFYING" and (
                    step_state == "BLOCKED" and result.pending_approval is not None
                ):
                    new_status = "NEEDS_APPROVAL"
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
                    if status == "PAUSED":
                        # The epoch bump revokes open approvals: a new epoch
                        # never silently inherits an old digest (D02).
                        new_status, new_epoch = "RUNNING", epoch + 1
                    elif status in {"NEEDS_APPROVAL", "WAITING_EXTERNAL"}:
                        # D02: the approval-gate/wait resume. The epoch does
                        # NOT change — the owner's freshly minted approval is
                        # bound to the CURRENT epoch and stays consumable, and
                        # no dispatched work is in flight to invalidate.
                        new_status, new_epoch = "RUNNING", epoch
                    else:
                        raise MissionStoreError(
                            f"RESUME requires PAUSED/NEEDS_APPROVAL, got {status}"
                        )
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
                    # D02: the epoch bump voids the old approval gate — the
                    # persisted pending digest was minted for the OLD epoch,
                    # so no later release may match it.
                    # D13: an approval-blocked step was NEVER dispatched
                    # (the guard refused before any effect), so re-observing
                    # is safe: it returns to PENDING and earns a FRESH
                    # obligation at the next dispatch. Blocks from uncertain
                    # effects stay BLOCKED.
                    self._conn.execute(
                        "UPDATE mission_steps SET state=CASE WHEN state='BLOCKED' "
                        "AND pending_action_digest!='' THEN 'PENDING' ELSE state END, "
                        "pending_action_digest='', pending_tool='', pending_target_ref=NULL, "
                        "updated_at_ms=? WHERE mission_id=? AND state='BLOCKED'",
                        (now, command.mission_id),
                    )
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
                if command.kind == "CANCEL":
                    self._conn.execute(
                        "UPDATE mission_external_waits SET released_at_ms=? "
                        "WHERE mission_id=? AND released_at_ms IS NULL",
                        (now, command.mission_id),
                    )
                if command.kind == "RESUME":
                    # D02: approvals are NOT carried across an epoch bump. An
                    # approval minted for epoch N is bound (cryptographically,
                    # via the action digest) to that epoch; a new epoch must
                    # re-earn an owner approval for ITS OWN digest. Open
                    # external waits end here too — the resume supersedes
                    # them.
                    self._conn.execute(
                        "UPDATE mission_external_waits SET released_at_ms=? "
                        "WHERE mission_id=? AND released_at_ms IS NULL",
                        (now, command.mission_id),
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

    async def consume_approval(
        self,
        approval_id: str,
        action_digest: str,
        *,
        plan_version: int | None = None,
        control_epoch: int | None = None,
        target_ref: str | None = None,
    ) -> bool:
        """Consume one approval, bound to the exact action identity (D02).

        The digest already binds mission/step/plan-version/epoch/tool/args;
        the row's own plan_version/control_epoch and the target ref must
        match the dispatch as well, so an approval minted for another epoch
        or target can never be consumed here.
        """
        return await self._run_tx(
            self._consume_approval_sync,
            approval_id, action_digest, plan_version, control_epoch, target_ref,
        )

    def _consume_approval_sync(
        self,
        approval_id: str,
        action_digest: str,
        plan_version: int | None,
        control_epoch: int | None,
        target_ref: str | None,
    ) -> bool:
        now = _now_ms()

        def tx() -> bool:
            row = self._conn.execute(
                "SELECT action_digest, expires_at_ms, consumed_at_ms, revoked, "
                "plan_version, control_epoch, target_ref FROM mission_approvals "
                "WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            if row is None:
                return False
            (stored_digest, expires_at, consumed_at, revoked,
             approval_plan, approval_epoch, approval_target) = row
            if consumed_at is not None or int(revoked or 0) == 1:
                return False
            if int(expires_at) <= now:
                return False
            if str(stored_digest) != action_digest:
                return False
            # D02: the approval's own binding must match the dispatch — a
            # new epoch (or plan) never inherits an old digest, and a
            # target-bound approval never releases a different target.
            if plan_version is not None and int(approval_plan) != int(plan_version):
                return False
            if control_epoch is not None and int(approval_epoch) != int(control_epoch):
                return False
            if (str(approval_target) if approval_target is not None else None) != (
                str(target_ref) if target_ref is not None else None
            ):
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

    # -- durable external waits (D02) ------------------------------------------------

    async def record_external_wait(
        self,
        mission_id: str,
        plan_version: int,
        step_id: str,
        *,
        reason: str,
        checkpoint: dict[str, Any],
        retry_after_ms: int,
        deadline_ms: int,
        control_epoch: int | None = None,
    ) -> str:
        """Persist a WAITING_EXTERNAL checkpoint: reason, retry-after,
        deadline and resume state. Returns the wait id."""
        return await self._run_tx(
            self._record_external_wait_sync,
            mission_id, plan_version, step_id, reason, checkpoint,
            retry_after_ms, deadline_ms, control_epoch,
        )

    def _record_external_wait_sync(
        self,
        mission_id: str,
        plan_version: int,
        step_id: str,
        reason: str,
        checkpoint: dict[str, Any],
        retry_after_ms: int,
        deadline_ms: int,
        control_epoch: int | None,
    ) -> str:
        now = _now_ms()

        def tx() -> str:
            # D14: wait admission binds the reconciled effect state — a
            # step whose latest attempt ended UNKNOWN cannot enter a wait,
            # because a timer must never make an unknown effect replayable.
            unresolved = self._conn.execute(
                "SELECT COUNT(*) FROM mission_attempts WHERE mission_id=? AND "
                "plan_version=? AND step_id=? AND effect_outcome='UNKNOWN' AND "
                "dispatch_state='RESULT_APPLIED'",
                (mission_id, plan_version, step_id),
            ).fetchone()
            if unresolved is not None and int(unresolved[0]) > 0:
                raise MissionStoreError(
                    f"wait refused: step {step_id} has an unresolved UNKNOWN "
                    "effect; reconciliation must settle it first"
                )
            mission_row = self._conn.execute(
                "SELECT plan_version, control_epoch, status FROM missions WHERE mission_id=?",
                (mission_id,),
            ).fetchone()
            if mission_row is None:
                raise MissionStoreError(f"wait refused: unknown mission {mission_id}")
            current_plan, current_epoch, current_status = (
                int(mission_row[0]), int(mission_row[1]), str(mission_row[2])
            )
            bound_epoch = current_epoch if control_epoch is None else int(control_epoch)
            if current_plan != int(plan_version) or current_epoch != bound_epoch:
                raise MissionStoreError("wait refused: stale plan or control epoch")
            if current_status not in {"RUNNING", "PLANNED", "VERIFYING", "WAITING_EXTERNAL"}:
                raise MissionStoreError(f"wait refused: mission is {current_status}")
            wait_id = new_id()
            # D16: screen BEFORE truncation and BEFORE the disk sink — a
            # canary in the reason or the checkpoint never persists. The
            # checkpoint is bounded by STRUCTURE (keys/values, after
            # screening), never by slicing serialized JSON.
            screened_reason = _screen_private(reason)[:400]
            bounded = _bound_checkpoint(checkpoint)
            self._conn.execute(
                "INSERT INTO mission_external_waits (wait_id, mission_id, plan_version, "
                "control_epoch, step_id, reason, checkpoint_json, retry_after_ms, deadline_ms, "
                "created_at_ms, released_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    wait_id, mission_id, plan_version, bound_epoch, step_id,
                    screened_reason, canonical_json(bounded),
                    int(retry_after_ms), int(deadline_ms), now,
                ),
            )
            if current_status in {"RUNNING", "PLANNED", "VERIFYING"}:
                # D14: the one-step failure path reaches VERIFYING before
                # the escalation decides; a wait is still enterable there.
                self._conn.execute(
                    "UPDATE missions SET status='WAITING_EXTERNAL', updated_at_ms=? "
                    "WHERE mission_id=?",
                    (now, mission_id),
                )
            self._append_event(
                mission_id=mission_id,
                plan_version=plan_version,
                control_epoch=bound_epoch,
                step_id=step_id,
                kind="recovery",
                payload={
                    "waiting_external": True,
                    "wait_id": wait_id,
                    "reason": screened_reason,
                    "retry_after_ms": int(retry_after_ms),
                    "deadline_ms": int(deadline_ms),
                    "checkpoint": bounded,
                },
                occurred_at_ms=now,
            )
            return wait_id

        return self._tx(tx)

    async def open_waits(self, mission_id: str | None = None) -> list[dict[str, Any]]:
        """Unreleased waits (optionally one mission's), oldest first."""
        return await self._run_tx(self._open_waits_sync, mission_id)

    def _open_waits_sync(self, mission_id: str | None) -> list[dict[str, Any]]:
        if mission_id is None:
            rows = self._conn.execute(
                "SELECT wait_id, mission_id, plan_version, control_epoch, step_id, reason, "
                "checkpoint_json, retry_after_ms, deadline_ms, created_at_ms "
                "FROM mission_external_waits WHERE released_at_ms IS NULL "
                "ORDER BY created_at_ms"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT wait_id, mission_id, plan_version, control_epoch, step_id, reason, "
                "checkpoint_json, retry_after_ms, deadline_ms, created_at_ms "
                "FROM mission_external_waits WHERE released_at_ms IS NULL AND mission_id=? "
                "ORDER BY created_at_ms",
                (mission_id,),
            ).fetchall()
        return [
            {
                "wait_id": str(r[0]),
                "mission_id": str(r[1]),
                "plan_version": int(r[2]),
                "control_epoch": int(r[3]),
                "step_id": str(r[4]),
                "reason": str(r[5]),
                "checkpoint": _safe_checkpoint_json(r[6]),
                "damaged_row": isinstance(r[6], str) and not r[6].strip().startswith("{"),
                "retry_after_ms": int(r[7]),
                "deadline_ms": int(r[8]),
                "created_at_ms": int(r[9]),
            }
            for r in rows
        ]

    async def release_wait(self, wait_id: str) -> MissionRecord | None:
        """Release one wait: requeue its step and return the mission to RUNNING."""
        return await self._run_tx(self._release_wait_sync, wait_id)

    def _release_wait_sync(self, wait_id: str) -> MissionRecord | None:
        now = _now_ms()

        def tx() -> MissionRecord | None:
            row = self._conn.execute(
                "SELECT mission_id, plan_version, control_epoch, step_id, released_at_ms, "
                "deadline_ms "
                "FROM mission_external_waits WHERE wait_id=?",
                (wait_id,),
            ).fetchone()
            if row is None:
                return None
            mission_id, plan_version, control_epoch, step_id, released_at, deadline_ms = row
            if released_at is not None:
                return self._get_mission_sync(str(mission_id))
            current = self._conn.execute(
                "SELECT plan_version, control_epoch, status FROM missions WHERE mission_id=?",
                (str(mission_id),),
            ).fetchone()
            if current is None:
                return None
            current_plan = int(current[0])
            current_epoch = int(current[1])
            current_status = str(current[2])
            # A malformed/legacy wait must never turn a known-ambiguous
            # effect into a runnable retry, even if the mission has already
            # moved away from WAITING_EXTERNAL.
            unresolved = self._conn.execute(
                "SELECT COUNT(*) FROM mission_attempts WHERE mission_id=? AND "
                "plan_version=? AND step_id=? AND effect_outcome='UNKNOWN' AND "
                "dispatch_state='RESULT_APPLIED'",
                (str(mission_id), int(plan_version), str(step_id)),
            ).fetchone()
            if unresolved is not None and int(unresolved[0]) > 0:
                self._conn.execute(
                    "UPDATE mission_external_waits SET released_at_ms=? WHERE wait_id=?",
                    (now, wait_id),
                )
                self._conn.execute(
                    "UPDATE missions SET status='BLOCKED', updated_at_ms=? WHERE mission_id=? "
                    "AND status IN ('WAITING_EXTERNAL','VERIFYING','RUNNING','PLANNED')",
                    (now, str(mission_id)),
                )
                self._append_event(
                    mission_id=str(mission_id), plan_version=int(plan_version),
                    control_epoch=int(control_epoch), step_id=str(step_id), kind="recovery",
                    payload={"wait_release_refused": True, "wait_id": wait_id,
                             "reason": "unresolved UNKNOWN effect; reconciliation required"},
                    occurred_at_ms=now,
                )
                return self._get_mission_sync(str(mission_id))
            if now >= int(deadline_ms):
                self._conn.execute(
                    "UPDATE mission_external_waits SET released_at_ms=? WHERE wait_id=?",
                    (now, wait_id),
                )
                self._conn.execute(
                    "UPDATE missions SET status='BLOCKED', updated_at_ms=? WHERE mission_id=? "
                    "AND status='WAITING_EXTERNAL'",
                    (now, str(mission_id)),
                )
                return self._get_mission_sync(str(mission_id))
            if (current_plan, current_epoch, current_status) != (
                int(plan_version), int(control_epoch), "WAITING_EXTERNAL"
            ):
                # A revised/paused/cancelled mission must never be revived by
                # a stale timer.  Consume this wait so restart cannot re-arm it.
                self._conn.execute(
                    "UPDATE mission_external_waits SET released_at_ms=? WHERE wait_id=?",
                    (now, wait_id),
                )
                return self._get_mission_sync(str(mission_id))
            self._conn.execute(
                "UPDATE mission_external_waits SET released_at_ms=? WHERE wait_id=?",
                (now, wait_id),
            )
            if not str(step_id).startswith("__controller_"):
                self._conn.execute(
                    "UPDATE mission_steps SET state=CASE WHEN state='BLOCKED' THEN 'PENDING' "
                    "ELSE state END, active_execution_id=NULL, updated_at_ms=? "
                    "WHERE mission_id=? AND plan_version=? AND step_id=?",
                    (now, str(mission_id), int(plan_version), str(step_id)),
                )
            open_count = self._conn.execute(
                "SELECT COUNT(*) FROM mission_external_waits WHERE mission_id=? "
                "AND released_at_ms IS NULL",
                (str(mission_id),),
            ).fetchone()
            if open_count is not None and int(open_count[0]) == 0:
                self._conn.execute(
                    "UPDATE missions SET status='RUNNING', updated_at_ms=? "
                    "WHERE mission_id=? AND plan_version=? AND control_epoch=? "
                    "AND status='WAITING_EXTERNAL'",
                    (now, str(mission_id), int(plan_version), int(control_epoch)),
                )
            self._append_event(
                mission_id=str(mission_id),
                plan_version=int(plan_version),
                control_epoch=int(control_epoch),
                step_id=str(step_id),
                kind="control",
                payload={
                    "kind": "WAIT_RELEASED",
                    "wait_id": wait_id,
                    "deadline_ms": int(deadline_ms),
                },
                occurred_at_ms=now,
            )
            return self._get_mission_sync(str(mission_id))

        return self._tx(tx)

    async def mark_mission_evidence_deleted(self, mission_id: str) -> list[str]:
        """D12: tombstone exactly THIS mission's live evidence rows and
        return their file paths (relative). Other missions' rows are never
        selected; idempotent (already-deleted rows return nothing)."""
        return await self._run_tx(self._mark_mission_evidence_deleted_sync, mission_id)

    def _mark_mission_evidence_deleted_sync(self, mission_id: str) -> list[str]:
        def tx() -> list[str]:
            rows = self._conn.execute(
                "SELECT evidence_id, relative_path FROM mission_evidence "
                "WHERE mission_id=? AND deleted=0",
                (mission_id,),
            ).fetchall()
            paths: list[str] = []
            for evidence_id, relative_path in rows:
                self._conn.execute(
                    "UPDATE mission_evidence SET deleted=1 "
                    "WHERE evidence_id=?",
                    (str(evidence_id),),
                )
                if relative_path:
                    path = str(relative_path)
                    paths.append(path)
                    self._queue_file_deletion_sync(
                        path, mission_id=mission_id, reason="owner_deletion"
                    )
            return paths

        return self._tx(tx)

    def _queue_file_deletion_sync(
        self, relative_path: str, *, mission_id: str, reason: str
    ) -> None:
        """Queue a confined artifact unlink in the same transaction as its
        row tombstone. Empty paths have no filesystem operation to retry."""
        if not relative_path:
            return
        self._conn.execute(
            "INSERT OR IGNORE INTO mission_file_deletions "
            "(relative_path, mission_id, reason, queued_at_ms, attempts) VALUES (?, ?, ?, ?, 0)",
            (relative_path, mission_id, reason, _now_ms()),
        )

    async def pending_file_deletions(self) -> list[str]:
        return await self._run_tx(self._pending_file_deletions_sync)

    def _pending_file_deletions_sync(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT relative_path FROM mission_file_deletions ORDER BY queued_at_ms, relative_path"
        ).fetchall()
        return [str(row[0]) for row in rows]

    async def acknowledge_file_deletions(self, paths: list[str]) -> None:
        if paths:
            await self._run_tx(self._acknowledge_file_deletions_sync, paths)

    def _acknowledge_file_deletions_sync(self, paths: list[str]) -> None:
        self._conn.executemany(
            "DELETE FROM mission_file_deletions WHERE relative_path=?",
            [(path,) for path in dict.fromkeys(path for path in paths if path)],
        )

    async def note_file_deletion_failures(self, paths: list[str]) -> None:
        if paths:
            await self._run_tx(self._note_file_deletion_failures_sync, paths)

    def _note_file_deletion_failures_sync(self, paths: list[str]) -> None:
        self._conn.executemany(
            "UPDATE mission_file_deletions SET attempts=attempts+1 WHERE relative_path=?",
            [(path,) for path in dict.fromkeys(path for path in paths if path)],
        )

    async def record_deletion_tombstone(
        self, mission_id: str, evidence_files: int, recommendations: list[str]
    ) -> None:
        """D07: a safe tombstone for an owner deletion — the content is
        gone, the fact of the deletion stays auditable."""
        return await self._run_tx(
            self._record_deletion_tombstone_sync, mission_id, evidence_files, recommendations
        )

    def _record_deletion_tombstone_sync(
        self, mission_id: str, evidence_files: int, recommendations: list[str]
    ) -> None:
        now = _now_ms()

        def tx() -> None:
            self._conn.execute(
                "INSERT INTO mission_retention_log (recorded_at_ms, kind, subject_id, "
                "detail_json) VALUES (?, ?, ?, ?)",
                (
                    now,
                    "owner_deletion",
                    mission_id,
                    canonical_json({
                        "evidence_files_deleted": int(evidence_files),
                        "recommendations_deleted": [str(r) for r in recommendations][:64],
                        "tombstone": True,
                    }),
                ),
            )

        return self._tx(tx)

    # -- provider request accounting (D05) ---------------------------------------------

    async def record_provider_request(
        self,
        mission_id: str,
        plan_version: int,
        request_id: str,
        *,
        role: str = "",
        call_key: str = "",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> int:
        """Persist ONE actual provider transport request (D05).

        Graph-internal sub-calls and retries each get their own durable row
        keyed by the transport's per-request call id. This is evidence and
        ceiling enforcement — it never re-charges the outer deep_calls
        budget, which the service settles exactly once per invocation.
        """
        return await self._run_tx(
            self._record_provider_request_sync,
            mission_id, plan_version, request_id, role, call_key,
            input_tokens, output_tokens,
        )

    def _record_provider_request_sync(
        self,
        mission_id: str,
        plan_version: int,
        request_id: str,
        role: str,
        call_key: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> int:
        now = _now_ms()

        def tx() -> int:
            self._conn.execute(
                "INSERT OR IGNORE INTO mission_provider_requests (request_id, mission_id, "
                "plan_version, role, call_key, input_tokens, output_tokens, created_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id, mission_id, plan_version, role[:32], call_key[:200],
                    int(input_tokens) if isinstance(input_tokens, int) else None,
                    int(output_tokens) if isinstance(output_tokens, int) else None,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT COUNT(*) FROM mission_provider_requests "
                "WHERE mission_id=? AND plan_version=?",
                (mission_id, plan_version),
            ).fetchone()
            return int(row[0]) if row is not None else 0

        return self._tx(tx)

    def provider_request_count_sync(self, mission_id: str, plan_version: int) -> int:
        """Synchronous count probe for the transport's ceiling check."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM mission_provider_requests "
            "WHERE mission_id=? AND plan_version=?",
            (mission_id, plan_version),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def admit_provider_request_sync(
        self,
        request_id: str,
        *,
        mission_id: str = "",
        plan_version: int = 0,
        role: str = "",
        call_key: str = "",
    ) -> int:
        """Durably record one ATTEMPTED provider request (D11).

        Called at the transport boundary BEFORE the provider is invoked so
        an attempted-but-denied request is still accounted. Synchronous:
        the callback path runs on the event loop thread between awaits.
        """
        now = _now_ms()

        def tx() -> int:
            self._conn.execute(
                "INSERT OR IGNORE INTO mission_provider_requests (request_id, mission_id, "
                "plan_version, role, call_key, state, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, ?, ?, ?, 'ATTEMPTED', ?, ?)",
                (
                    request_id, mission_id, plan_version, role[:32], call_key[:200],
                    now, now,
                ),
            )
            row = self._conn.execute(
                "SELECT COUNT(*) FROM mission_provider_requests "
                "WHERE mission_id=? AND plan_version=?",
                (mission_id, plan_version),
            ).fetchone()
            return int(row[0]) if row is not None else 0

        return self._tx(tx)

    def settle_provider_request_sync(
        self,
        request_id: str,
        state: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Settle one admitted request: COMPLETED or FAILED (D11)."""
        assert state in {"COMPLETED", "FAILED"}
        now = _now_ms()

        def tx() -> None:
            self._conn.execute(
                "UPDATE mission_provider_requests SET state=?, input_tokens=?, "
                "output_tokens=?, updated_at_ms=? WHERE request_id=?",
                (
                    state,
                    int(input_tokens) if isinstance(input_tokens, int) else None,
                    int(output_tokens) if isinstance(output_tokens, int) else None,
                    now, request_id,
                ),
            )

        return self._tx(tx)

    async def provider_requests(self, mission_id: str) -> list[dict[str, Any]]:
        """The durable per-request transport rows for one mission."""
        return await self._run_tx(self._provider_requests_sync, mission_id)

    def _provider_requests_sync(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT request_id, plan_version, role, call_key, input_tokens, "
            "output_tokens, created_at_ms FROM mission_provider_requests "
            "WHERE mission_id=? ORDER BY created_at_ms",
            (mission_id,),
        ).fetchall()
        return [
            {
                "request_id": str(r[0]),
                "plan_version": int(r[1]),
                "role": str(r[2]),
                "call_key": str(r[3]),
                "input_tokens": r[4] if r[4] is None else int(r[4]),
                "output_tokens": r[5] if r[5] is None else int(r[5]),
                "created_at_ms": int(r[6]),
            }
            for r in rows
        ]

    # -- budget reservations ----------------------------------------------------------

    async def reserve_budget(
        self, mission_id: str, charge: BudgetCharge, *, item: BoundedWorkItem | None = None
    ) -> BudgetReservation:
        return await self._run_tx(self._reserve_budget_sync, mission_id, charge, item)

    def _reserve_budget_sync(
        self, mission_id: str, charge: BudgetCharge, item: BoundedWorkItem | None = None
    ) -> BudgetReservation:
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
            if item is not None:
                prefix = f"{charge.resource}:{mission_id}:{item.execution_id}:"
                if item.mission_id != mission_id or not charge.call_key.startswith(prefix):
                    raise MissionStoreError("packet budget charge identity mismatch")
                step = self._conn.execute(
                    "SELECT s.step_json FROM mission_steps s JOIN mission_attempts a "
                    "ON a.mission_id=s.mission_id AND a.plan_version=s.plan_version "
                    "AND a.step_id=s.step_id WHERE a.execution_id=? AND a.mission_id=?",
                    (item.execution_id, mission_id),
                ).fetchone()
                if step is None:
                    raise UnknownExecution("packet budget has no committed execution")
                stored = StepSpec.model_validate(self._usage_dict(step[0]))
                packet_ceiling = min(getattr(item.budget, f"max_{charge.resource}"),
                                     getattr(stored.budget, f"max_{charge.resource}"))
                spent = self._conn.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM mission_budget_reservations "
                    "WHERE mission_id=? AND resource=? AND call_key LIKE ? "
                    "AND status IN ('RESERVED','CONSUMED')",
                    (mission_id, charge.resource, prefix + "%"),
                ).fetchone()[0]
                if int(spent) + charge.amount > packet_ceiling:
                    raise MissionStoreError(f"packet budget exhausted for {charge.resource}")
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
            epoch = self._epoch_sync(mission_id)
            limits_row = self._conn.execute(
                "SELECT limits_json FROM missions WHERE mission_id=?", (mission_id,)
            ).fetchone()
            limits = BudgetLimits.model_validate(self._usage_dict(limits_row[0]))
            released = 0
            # D02: release only the step whose PERSISTED pending digest an
            # active, current-epoch approval actually names. An unrelated
            # approval (other step, other action, other epoch) releases
            # nothing.
            rows = self._conn.execute(
                "SELECT s.step_id, s.attempt_count, s.pending_action_digest, "
                "s.pending_tool FROM mission_steps s "
                "WHERE s.mission_id=? AND s.plan_version=? AND s.state='BLOCKED' "
                "AND s.pending_action_digest!='' AND EXISTS ("
                "  SELECT 1 FROM mission_approvals a WHERE a.mission_id=s.mission_id "
                "  AND a.action_digest=s.pending_action_digest AND a.control_epoch=? "
                "  AND a.revoked=0 AND a.consumed_at_ms IS NULL AND a.expires_at_ms>? "
                "  AND a.target_ref IS s.pending_target_ref)",
                (mission_id, plan_version, epoch, now),
            ).fetchall()
            for step_id, attempt_count, _digest, _tool in rows:
                if int(attempt_count) > limits.max_retries:
                    continue
                self._conn.execute(
                    "UPDATE mission_steps SET state='PENDING', active_execution_id=NULL, "
                    "pending_action_digest='', pending_tool='', pending_target_ref=NULL, "
                    "updated_at_ms=? WHERE mission_id=? AND plan_version=? AND step_id=?",
                    (now, mission_id, plan_version, str(step_id)),
                )
                self._append_event(
                    mission_id=mission_id,
                    plan_version=plan_version,
                    control_epoch=epoch,
                    step_id=str(step_id),
                    kind="control",
                    payload={
                        "kind": "RESUME",
                        "reason": "matching owner approval; blocked step re-queued",
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
                        # D07: the final-review sink is screened the same way
                        # as revisions — a canary in review text never lands.
                        "summary": _screen_private(summary),
                        "unresolved_issues": [
                            _screen_private(str(issue))[:200] for issue in unresolved_issues
                        ],
                    },
                    "advisory": True,
                },
                occurred_at_ms=now,
            )

        self._tx(tx)

    # -- durable retention holds (D16) -------------------------------------------------

    async def set_retention_hold(self, mission_id: str, *, reason: str) -> None:
        """Persist an owner investigation hold; it survives restarts."""
        return await self._run_tx(self._set_retention_hold_sync, mission_id, reason)

    def _set_retention_hold_sync(self, mission_id: str, reason: str) -> None:
        now = _now_ms()

        def tx() -> None:
            self._conn.execute(
                "INSERT INTO mission_retention_holds (mission_id, reason, held_at_ms, "
                "released_at_ms) VALUES (?, ?, ?, NULL) "
                "ON CONFLICT(mission_id) DO UPDATE SET reason=excluded.reason, "
                "held_at_ms=excluded.held_at_ms, released_at_ms=NULL",
                (mission_id, reason[:400], now),
            )

        return self._tx(tx)

    async def release_retention_hold(self, mission_id: str) -> None:
        return await self._run_tx(self._release_retention_hold_sync, mission_id)

    def _release_retention_hold_sync(self, mission_id: str) -> None:
        now = _now_ms()

        def tx() -> None:
            self._conn.execute(
                "UPDATE mission_retention_holds SET released_at_ms=? "
                "WHERE mission_id=? AND released_at_ms IS NULL",
                (now, mission_id),
            )

        return self._tx(tx)

    async def active_retention_holds(self) -> set[str]:
        """The durable, unreleased hold set (D16)."""
        return await self._run_tx(self._active_holds_sync)

    def _active_holds_sync(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT mission_id FROM mission_retention_holds "
            "WHERE released_at_ms IS NULL"
        ).fetchall()
        return {str(r[0]) for r in rows}

    # -- retention (C07/N06) -----------------------------------------------------------

    #: D16: the planned metadata policy — terminal mission metadata
    #: (mission row, events, steps, attempts) older than 30 days is purged
    #: entirely unless held; evidence expires at creation (7 days for
    #: screenshot-kind content, 30 days for structured metadata).
    METADATA_RETENTION_MS = 30 * 24 * 60 * 60 * 1000

    async def enforce_retention(
        self, now_ms: int, *, hold_missions: set[str] | None = None
    ) -> dict[str, Any]:
        """Owner-bounded retention sweep over mission data.

        C07/D16: expired evidence is tombstoned and its files unlinked;
        DURABLE holds (mission_retention_holds) always win — the optional
        ``hold_missions`` parameter ADDS transient holds for tests. Evidence
        rows whose mission has vanished are orphans and go too. Terminal
        mission METADATA older than the 30-day policy horizon is purged
        entirely (mission + events + steps + attempts) with a retention-log
        tombstone — trace data is not preserved indefinitely by calling it
        audit metadata.
        """
        if hold_missions is None:
            hold_missions = set()
        return await self._run_tx(self._enforce_retention_sync, now_ms, hold_missions)

    def _enforce_retention_sync(self, now_ms: int, holds: set[str]) -> dict[str, Any]:
        holds = holds | self._active_holds_sync()

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
                self._queue_file_deletion_sync(
                    path, mission_id=mission_id,
                    reason="orphan_evidence" if known is None else "retention_expiry",
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
            # D16: the 30-day metadata policy. Terminal missions whose last
            # update passed the horizon are purged WHOLE (never partially —
            # a hash chain must never gain a hole), unless a hold names
            # them. The purge itself is tombstoned in the retention log.
            purged_missions: list[str] = []
            stale = self._conn.execute(
                "SELECT mission_id FROM missions WHERE status IN "
                "('COMPLETED','FAILED','CANCELLED') AND updated_at_ms<=? "
                "AND mission_id NOT IN (SELECT mission_id FROM mission_retention_holds "
                "WHERE released_at_ms IS NULL)",
                (now_ms - self.METADATA_RETENTION_MS,),
            ).fetchall()
            for (mission_id,) in stale:
                mission_id = str(mission_id)
                artifact_rows = self._conn.execute(
                    "SELECT relative_path FROM mission_evidence "
                    "WHERE mission_id=? AND relative_path IS NOT NULL",
                    (mission_id,),
                ).fetchall()
                for (relative_path,) in artifact_rows:
                    self._queue_file_deletion_sync(
                        str(relative_path), mission_id=mission_id, reason="metadata_purge"
                    )
                for table in ("mission_events", "mission_steps", "mission_attempts",
                              "mission_evidence", "mission_payloads", "mission_approvals",
                              "mission_budget_reservations", "mission_external_waits",
                              "mission_provider_requests", "mission_action_ledger",
                              "mission_plans"):
                    self._conn.execute(
                        f"DELETE FROM {table} WHERE mission_id=?", (mission_id,))
                self._conn.execute(
                    "DELETE FROM missions WHERE mission_id=?", (mission_id,))
                self._conn.execute(
                    "INSERT INTO mission_retention_log (recorded_at_ms, kind, subject_id, "
                    "detail_json) VALUES (?, 'metadata_purge', ?, ?)",
                    (now_ms, mission_id,
                     canonical_json({"policy": "30d", "tombstone": True})),
                )
                purged_missions.append(mission_id)
            return {
                "deleted_evidence": sum(len(v) for v in deleted_by_mission.values()),
                "orphan_evidence": len(orphan_paths),
                "purged_missions": purged_missions,
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
