"""Real compose-Postgres safety regressions; never touch the public schema."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import psycopg
import pytest
from psycopg import sql

from assistant.runtime.runs import DesktopLease, RunStore
from tests.conftest import POSTGRES_URL

# Deliberately independent of production DDL: represent the legacy schema without
# the turn uniqueness index, including history that setup must never discard.
_LEGACY_TABLES = """
CREATE TABLE run_registry (
    run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL, request_digest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted', owner TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE action_ledger (
    ledger_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES run_registry(run_id),
    step_id TEXT NOT NULL, tool_name TEXT NOT NULL,
    target_desc TEXT NOT NULL DEFAULT '', args_digest TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'planned', evidence_ref TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@dataclass
class IsolatedSchema:
    name: str
    connections: list[Any] = field(default_factory=list)

    async def connect(self) -> Any:
        conn = await psycopg.AsyncConnection.connect(POSTGRES_URL, autocommit=True)
        self.connections.append(conn)
        await conn.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(self.name))
        )
        await conn.execute("SET statement_timeout = '10s'")
        return conn

    async def store(self) -> RunStore:
        conn = await self.connect()
        # Other regressions must reach their assertions even before fresh setup
        # is fixed. This creates only this test's private empty legacy tables.
        await conn.execute(_LEGACY_TABLES)
        store = RunStore(conn)
        await store.setup()
        return store


@pytest.fixture
async def isolated_schema(require_postgres: None) -> AsyncIterator[IsolatedSchema]:
    schema = IsolatedSchema(f"run_safety_{uuid.uuid4().hex}")
    async with await psycopg.AsyncConnection.connect(
        POSTGRES_URL, autocommit=True
    ) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema.name)))
        try:
            yield schema
        finally:
            for conn in schema.connections:
                await conn.close()
            # This identifier is generated here, never supplied by production.
            await admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema.name))
            )


async def claim(store: RunStore, run_id: str, *, digest: str = "digest", msg: str = "msg"):
    return await store.claim(
        user_id="user", chat_id="chat", user_message_id=msg,
        request_digest=digest, run_id=run_id,
    )


# Explicit columns: setup() may additive-migrate new columns (e.g.
# failure_reason) between the before/after snapshots; the safety contract
# is about legacy ROW data surviving, not the tuple shape of SELECT *.
_RUNS_SQL = (
    "SELECT run_id, user_id, chat_id, user_message_id, request_digest, "
    "status, owner, created_at, updated_at FROM run_registry ORDER BY run_id"
)
_LEDGER_SQL = "SELECT * FROM action_ledger ORDER BY ledger_id"


async def fetch_rows(conn: Any, query: str) -> list[tuple[Any, ...]]:
    return list(await (await conn.execute(query)).fetchall())


async def test_pristine_setup_and_repeat_preserve_history(isolated_schema: IsolatedSchema):
    conn = await isolated_schema.connect()
    store = RunStore(conn)
    await store.setup()
    assert (await claim(store, "canonical")).owned
    ledger_id = await store.record_action("canonical", "step", "click")
    await store.mark_action(ledger_id, "unknown", "evidence:original")
    before = await store.get_run("canonical")
    await store.setup()
    await store.setup()
    assert await store.get_run("canonical") == before
    assert await DesktopLease(conn).acquire("owner")


@pytest.mark.parametrize("same_timestamp", [False, True])
async def test_legacy_duplicates_fail_setup_without_deleting_history(
    isolated_schema: IsolatedSchema, same_timestamp: bool,
):
    conn = await isolated_schema.connect()
    await conn.execute(_LEGACY_TABLES)
    await conn.execute(
        "INSERT INTO run_registry "
        "(run_id, user_id, chat_id, user_message_id, request_digest, created_at) VALUES "
        "('old', 'user', 'chat', 'msg', 'a', '2020-01-01'), "
        "('new', 'user', 'chat', 'msg', 'b', %s)",
        ("2020-01-01" if same_timestamp else "2021-01-01",),
    )
    await conn.execute(
        "INSERT INTO action_ledger (run_id, step_id, tool_name, state, evidence_ref) "
        "VALUES ('old', 's1', 'click', 'unknown', 'old-evidence'), "
        "('new', 's2', 'type', 'confirmed', 'new-evidence')"
    )
    before_runs = await fetch_rows(conn, _RUNS_SQL)
    before_actions = await fetch_rows(conn, _LEDGER_SQL)
    # Failure is deliberately loud: resolving legacy identities needs an
    # operator migration, not destructive best-effort startup cleanup.
    for _ in range(2):
        try:
            with pytest.raises(psycopg.errors.UniqueViolation):
                await RunStore(conn).setup()
        finally:
            assert await fetch_rows(conn, _RUNS_SQL) == before_runs
            assert await fetch_rows(conn, _LEDGER_SQL) == before_actions


async def test_duplicate_returns_canonical_identity_and_conflicts_fail_closed(
    isolated_schema: IsolatedSchema,
):
    store = await isolated_schema.store()
    assert (await claim(store, "canonical")).owned
    await store.finish("canonical", "completed")
    duplicate = await claim(store, "proposed")
    assert not duplicate.owned
    assert duplicate.run_id == "canonical"
    assert duplicate.reason == "already_claimed"
    assert duplicate.status == "completed"
    conflict = await claim(store, "conflict", digest="changed")
    assert not conflict.owned
    assert conflict.reason == "identity_conflict"
    assert conflict.run_id == "canonical"
    collision = await claim(store, "canonical", msg="another-turn")
    assert not collision.owned
    assert collision.reason in {"identity_conflict", "registry_error"}
    # A PK collision must not poison the connection or insert another turn.
    assert (await claim(store, "fresh", msg="another-turn")).owned


async def test_failed_run_is_retryable_with_same_identity(
    isolated_schema: IsolatedSchema,
):
    """Live incident (2026-09-18): Open WebUI regeneration reuses the same
    user-message id ('2/2'), and the registry rejected the retry of a run
    that had terminally FAILED -- the user could never retry. A retry
    after a terminal failure/cancellation is a new intentional action."""
    store = await isolated_schema.store()
    first = await claim(store, "run-1", digest="d1", msg="m1")
    assert first.owned
    await store.finish("run-1", "failed", "Requested app was not observed in the foreground.")
    retry = await claim(store, "run-1", digest="d1", msg="m1")
    assert retry.owned, "a terminal failure must be retryable"
    assert retry.run_id == "run-1"
    assert "retry" in retry.reason
    record = await store.get_run("run-1")
    assert record is not None and record.status == "running"
    assert record.failure_reason == "", "retry clears the stale failure reason"
    # The cancelled terminal state is retryable for the same reason.
    await store.finish("run-1", "cancelled")
    retry2 = await claim(store, "run-1", digest="d1", msg="m1")
    assert retry2.owned and retry2.run_id == "run-1"


async def test_retry_claim_is_atomic_single_winner(isolated_schema: IsolatedSchema):
    await isolated_schema.store()  # create schema/tables in this test's search_path
    store_a = RunStore(await isolated_schema.connect())
    store_b = RunStore(await isolated_schema.connect())
    assert (await claim(store_a, "dup-run", msg="same")).owned
    await store_a.finish("dup-run", "failed", "boom")
    results = await asyncio.gather(
        claim(store_a, "dup-run", msg="same"), claim(store_b, "dup-run", msg="same")
    )
    owned = [r for r in results if r.owned]
    assert len(owned) == 1, f"exactly one retry winner, got {results}"
    assert all(r.reason == "already_claimed" for r in results if not r.owned)


async def test_inflight_and_completed_retries_still_dedup(
    isolated_schema: IsolatedSchema,
):
    store = await isolated_schema.store()
    assert (await claim(store, "live", msg="live-msg")).owned
    inflight = await claim(store, "other", msg="live-msg")
    assert not inflight.owned and inflight.reason == "already_claimed"
    await store.finish("live", "completed")
    done = await claim(store, "other", msg="live-msg")
    assert not done.owned and done.reason == "already_claimed"


async def test_identity_conflict_after_failure_still_rejected(
    isolated_schema: IsolatedSchema,
):
    store = await isolated_schema.store()
    assert (await claim(store, "id-run", digest="original", msg="id-msg")).owned
    await store.finish("id-run", "failed", "boom")
    conflict = await claim(store, "id-run", digest="different", msg="id-msg")
    assert not conflict.owned
    assert conflict.reason == "identity_conflict", (
        "a failed run does not authorize a different payload under the same id"
    )


@pytest.mark.parametrize("shared_connection", [False, True])
async def test_concurrent_claims_have_one_canonical_winner(
    isolated_schema: IsolatedSchema, shared_connection: bool,
):
    store = await isolated_schema.store()
    stores = [store] * 12 if shared_connection else [
        RunStore(await isolated_schema.connect()) for _ in range(12)
    ]
    gate = asyncio.Barrier(len(stores))

    async def contend(index: int, contender: RunStore):
        await gate.wait()
        return await claim(contender, f"candidate-{index}")

    results = await asyncio.wait_for(
        asyncio.gather(*(contend(i, s) for i, s in enumerate(stores))), timeout=15
    )
    winners = [result for result in results if result.owned]
    assert len(winners) == 1
    assert {result.run_id for result in results} == {winners[0].run_id}
    assert all(result.owned or result.reason == "already_claimed" for result in results)


@pytest.mark.parametrize("ttl", [0.0, -1.0, float("nan"), float("inf"), -float("inf")])
async def test_lease_rejects_invalid_ttl(isolated_schema: IsolatedSchema, ttl: float):
    store = await isolated_schema.store()
    with pytest.raises(ValueError, match="TTL"):
        DesktopLease(store._conn, ttl_seconds=ttl)
    lease = DesktopLease(store._conn)
    with pytest.raises(ValueError, match="TTL"):
        await lease.acquire("owner", ttl_seconds=ttl)
    assert await lease.current() is None


async def test_lease_uses_database_clock_and_owner_checked_release(
    isolated_schema: IsolatedSchema, monkeypatch: pytest.MonkeyPatch,
):
    store = await isolated_schema.store()
    lease = DesktopLease(store._conn, ttl_seconds=60)
    monkeypatch.setattr(time, "time", lambda: 1.0)
    assert await lease.acquire("owner")
    row = await (await store._conn.execute(
        "SELECT expires_at - EXTRACT(EPOCH FROM clock_timestamp()) FROM desktop_lease"
    )).fetchone()
    assert row is not None and 50 < float(row[0]) <= 60
    assert await lease.acquire("owner", ttl_seconds=120)
    assert not await lease.acquire("other")
    await lease.release("other")
    assert await lease.current() == "owner"
    await store._conn.execute("UPDATE desktop_lease SET expires_at = 0")
    assert await lease.acquire("other")
    await lease.release("owner")
    assert await lease.current() == "other"
    await lease.release("other")
    assert await lease.current() is None


async def test_concurrent_lease_acquisition_reports_only_actual_winner(
    isolated_schema: IsolatedSchema,
):
    store = await isolated_schema.store()
    control = store._conn
    connections = [await isolated_schema.connect() for _ in range(2)]
    key = uuid.uuid4().int % (2**63 - 1)
    # Gate both INSERT attempts in PostgreSQL, after the old implementation's
    # SELECTs. Both contenders see an empty lease before either can insert.
    await control.execute(sql.SQL("""
        CREATE FUNCTION gate_lease_insert() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock({});
            RETURN NEW;
        END $$;
        CREATE TRIGGER gate_lease BEFORE INSERT ON desktop_lease
            FOR EACH ROW EXECUTE FUNCTION gate_lease_insert();
    """).format(sql.Literal(key)))
    await control.execute("SELECT pg_advisory_lock(%s)", (key,))
    tasks = [asyncio.create_task(DesktopLease(conn).acquire(f"owner-{i}"))
             for i, conn in enumerate(connections)]
    try:
        async def both_waiting():
            while True:
                row = await (await control.execute(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND NOT granted AND pid = ANY(%s)",
                    ([conn.info.backend_pid for conn in connections],),
                )).fetchone()
                if row is not None and row[0] == 2:
                    return
                await asyncio.sleep(0.01)

        await asyncio.wait_for(both_waiting(), timeout=5)
        await control.execute("SELECT pg_advisory_unlock(%s)", (key,))
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        assert results.count(True) == 1
        winner = results.index(True)
        assert await DesktopLease(control).current() == f"owner-{winner}"
    finally:
        await control.execute("SELECT pg_advisory_unlock(%s)", (key,))
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
