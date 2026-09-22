"""Embedded SQLite run registry tests (Sani master doc sections 2-5, 15).

Ports the behavioral regressions of the Postgres-backed RunStore and
DesktopLease (tests/integration/test_run_ledger.py,
test_run_store_safety.py) onto one temporary sani.db: no Docker, no
PostgreSQL, fully deterministic.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from assistant.runtime.runs import ClaimResult
from assistant.runtime.runs_local import SQLiteDesktopLease, SQLiteRunStore


async def _make_store(tmp_path: Path) -> SQLiteRunStore:
    run_store = await SQLiteRunStore.connect(str(tmp_path / "sani.db"))
    await run_store.setup()
    return run_store


async def claim(
    store: SQLiteRunStore,
    run_id: str,
    *,
    digest: str = "digest",
    msg: str = "msg",
    user: str = "user",
    chat: str = "chat",
) -> ClaimResult:
    return await store.claim(
        user_id=user,
        chat_id=chat,
        user_message_id=msg,
        request_digest=digest,
        run_id=run_id,
    )


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SQLiteRunStore]:
    made = await _make_store(tmp_path)
    try:
        yield made
    finally:
        await made.close()


# -- claim ---------------------------------------------------------------------


async def test_first_claim_wins_and_duplicate_delivery_observes(
    store: SQLiteRunStore,
) -> None:
    first = await claim(store, "canonical")
    assert first.owned is True
    assert first.run_id == "canonical"
    assert first.status == "running"

    # Same id + same digest: duplicate delivery, not a second execution.
    duplicate = await claim(store, "proposed")
    assert duplicate.owned is False
    assert duplicate.run_id == "canonical"
    assert duplicate.reason == "already_claimed"
    assert duplicate.status == "running"


async def test_conflicting_digest_same_id_is_rejected(store: SQLiteRunStore) -> None:
    await claim(store, "canonical", digest="digest-1")
    conflict = await claim(store, "conflict", digest="digest-2")
    assert conflict.owned is False
    assert conflict.reason == "identity_conflict"
    assert conflict.run_id == "canonical"


async def test_same_text_new_id_is_a_new_intentional_command(
    store: SQLiteRunStore,
) -> None:
    a = await claim(store, "run-a", digest="text-digest", msg="msg-a")
    b = await claim(store, "run-b", digest="text-digest", msg="msg-b")
    assert a.owned and b.owned


async def test_run_id_collision_fails_closed(store: SQLiteRunStore) -> None:
    assert (await claim(store, "canonical", msg="turn-1")).owned
    collision = await claim(store, "canonical", msg="another-turn")
    assert not collision.owned
    assert collision.reason == "registry_error"
    # A PK collision must not poison the connection or insert another turn.
    assert (await claim(store, "fresh", msg="another-turn")).owned


async def test_failed_and_cancelled_runs_are_retryable(store: SQLiteRunStore) -> None:
    assert (await claim(store, "run-1", digest="d1", msg="m1")).owned
    await store.finish("run-1", "failed", "Requested app not observed.")
    retry = await claim(store, "run-1", digest="d1", msg="m1")
    assert retry.owned, "a terminal failure must be retryable"
    assert retry.run_id == "run-1"
    assert "retry" in retry.reason
    record = await store.get_run("run-1")
    assert record is not None and record.status == "running"
    assert record.failure_reason == "", "retry clears the stale failure reason"

    await store.finish("run-1", "cancelled")
    retry2 = await claim(store, "run-1", digest="d1", msg="m1")
    assert retry2.owned and retry2.run_id == "run-1"


async def test_inflight_and_completed_retries_still_dedup(store: SQLiteRunStore) -> None:
    assert (await claim(store, "live", msg="live-msg")).owned
    inflight = await claim(store, "other", msg="live-msg")
    assert not inflight.owned and inflight.reason == "already_claimed"

    await store.finish("live", "completed")
    done = await claim(store, "other", msg="live-msg")
    assert not done.owned and done.reason == "already_claimed"
    assert done.status == "completed"


async def test_identity_conflict_after_failure_still_rejected(
    store: SQLiteRunStore,
) -> None:
    assert (await claim(store, "id-run", digest="original", msg="id-msg")).owned
    await store.finish("id-run", "failed", "boom")
    conflict = await claim(store, "id-run", digest="different", msg="id-msg")
    assert not conflict.owned
    assert conflict.reason == "identity_conflict"


# -- registry ------------------------------------------------------------------


async def test_finish_and_get_run_roundtrip(store: SQLiteRunStore) -> None:
    await claim(store, "roundtrip", digest="dg", msg="m-1", user="u-1", chat="c-1")
    record = await store.get_run("roundtrip")
    assert record is not None
    assert record.run_id == "roundtrip"
    assert record.user_id == "u-1"
    assert record.chat_id == "c-1"
    assert record.user_message_id == "m-1"
    assert record.request_digest == "dg"
    assert record.status == "running"
    assert record.owner is None
    assert record.updated_at > 0.0
    assert record.actions == []
    assert record.failure_reason == ""

    await store.finish("roundtrip", "completed")
    record = await store.get_run("roundtrip")
    assert record is not None and record.status == "completed"
    assert record.owner is None

    assert await store.get_run("missing") is None

    # failure_reason is bounded tool/recipe error text (300 chars max).
    await store.finish("roundtrip", "failed", "x" * 400)
    failed = await store.get_run("roundtrip")
    assert failed is not None
    assert failed.failure_reason == "x" * 300


async def test_setup_repeat_preserves_history(store: SQLiteRunStore) -> None:
    assert (await claim(store, "canonical")).owned
    ledger_id = await store.record_action("canonical", "step", "click")
    await store.mark_action(ledger_id, "unknown", "evidence:original")
    before = await store.get_run("canonical")

    await store.setup()
    await store.setup()
    assert await store.get_run("canonical") == before


async def test_registry_persists_across_reopen(tmp_path: Path) -> None:
    store = await _make_store(tmp_path)
    await claim(store, "durable-run")
    await store.record_action("durable-run", "step-1", "click")
    await store.finish("durable-run", "completed", "all good")
    await store.close()

    reopened = await _make_store(tmp_path)
    try:
        record = await reopened.get_run("durable-run")
        assert record is not None
        assert record.status == "completed"
        assert record.failure_reason == "all good"
        assert [a["step_id"] for a in record.actions] == ["step-1"]
        duplicate = await claim(reopened, "proposed")
        assert not duplicate.owned and duplicate.reason == "already_claimed"
    finally:
        await reopened.close()


# -- action ledger -------------------------------------------------------------


async def test_action_ledger_flow(store: SQLiteRunStore) -> None:
    await claim(store, "ledger-run")
    first = await store.record_action("ledger-run", "step-1", "click", target_desc="calc 7")
    second = await store.record_action("ledger-run", "step-2", "type", args_digest="args-2")
    assert first < second  # ledger ids are monotonically assigned

    planned = await store.run_actions("ledger-run")
    assert [a["state"] for a in planned] == ["planned", "planned"]
    assert planned[0]["step_id"] == "step-1"
    assert planned[0]["tool_name"] == "click"
    assert planned[0]["target_desc"] == "calc 7"
    assert planned[0]["args_digest"] == ""

    # Crash-window state is first-class: unknown, never blindly replayed.
    await store.mark_action(first, "confirmed", "artifact:display-42")
    await store.mark_action(second, "unknown")
    actions = await store.run_actions("ledger-run")
    assert actions[0]["state"] == "confirmed"
    assert actions[0]["evidence_ref"] == "artifact:display-42"
    assert actions[1]["state"] == "unknown"
    assert actions[1]["evidence_ref"] == ""

    assert [a["step_id"] for a in await store.actions_in_state("ledger-run", "unknown")] == [
        "step-2"
    ]
    assert len(await store.actions_in_state("ledger-run", "confirmed")) == 1
    assert await store.actions_in_state("ledger-run", "failed") == []

    record = await store.get_run("ledger-run")
    assert record is not None
    assert record.actions == actions


# -- activity snapshot ---------------------------------------------------------


async def test_run_activity_snapshot_and_missing_run(store: SQLiteRunStore) -> None:
    await claim(store, "activity-run")
    ledger_id = await store.record_action("activity-run", "step-1", "click")
    await store.record_action("activity-run", "step-2", "type")
    await store.mark_action(ledger_id, "confirmed", "artifact:1")

    activity = await store.run_activity("activity-run")
    assert activity is not None
    assert activity["run_id"] == "activity-run"
    assert activity["status"] == "running"
    assert activity["failure_reason"] == ""
    assert activity["created_at"] > 0.0
    assert activity["updated_at"] >= activity["created_at"]

    actions = activity["actions"]
    assert [a["step_id"] for a in actions] == ["step-1", "step-2"]
    assert actions[0]["tool_name"] == "click"
    assert actions[0]["target_desc"] == ""
    assert actions[0]["state"] == "confirmed"
    assert actions[1]["state"] == "planned"
    assert all(a["updated_at"] > 0.0 for a in actions)

    assert await store.run_activity("missing") is None


# -- desktop lease -------------------------------------------------------------


async def test_desktop_lease_is_exclusive_across_owners(store: SQLiteRunStore) -> None:
    assert await store.acquire("owner-1") is True
    assert await store.acquire("owner-2") is False
    assert await store.current() == "owner-1"

    # Same owner re-acquiring just refreshes the lease.
    assert await store.acquire("owner-1", ttl_seconds=60) is True
    assert await store.acquire("owner-2") is False

    # Release is owner-checked: a foreign release is a no-op.
    await store.release("owner-2")
    assert await store.current() == "owner-1"

    await store.release("owner-1")
    assert await store.current() is None
    assert await store.acquire("owner-2") is True


async def test_expired_lease_is_stealable(store: SQLiteRunStore) -> None:
    assert await store.acquire("owner-1", ttl_seconds=0.05) is True
    await asyncio.sleep(0.15)
    assert await store.acquire("owner-2") is True
    assert await store.current() == "owner-2"
    await store.release("owner-2")


async def test_lease_class_port_matches_store_semantics(store: SQLiteRunStore) -> None:
    lease = SQLiteDesktopLease(store._conn, ttl_seconds=60)
    other = SQLiteDesktopLease(store._conn, ttl_seconds=60)
    assert await lease.acquire("owner-1") is True
    assert await other.acquire("owner-2") is False
    await lease.release("owner-1")
    assert await other.acquire("owner-2") is True
    await other.release("owner-2")
    assert await other.current() is None


@pytest.mark.parametrize("ttl", [0.0, -1.0, float("nan"), float("inf"), -float("inf")])
async def test_lease_rejects_invalid_ttl(store: SQLiteRunStore, ttl: float) -> None:
    with pytest.raises(ValueError, match="TTL"):
        SQLiteDesktopLease(store._conn, ttl_seconds=ttl)
    with pytest.raises(ValueError, match="TTL"):
        await store.acquire("owner", ttl_seconds=ttl)
    assert await store.current() is None


# -- concurrency ---------------------------------------------------------------


async def test_concurrent_claims_on_different_turns_both_owned(tmp_path: Path) -> None:
    # Two connections, like two processes sharing the embedded sani.db.
    store_a = await _make_store(tmp_path)
    store_b = await _make_store(tmp_path)
    try:
        first, second = await asyncio.gather(
            claim(store_a, "run-a", digest="d-a", msg="m-a"),
            claim(store_b, "run-b", digest="d-b", msg="m-b"),
        )
    finally:
        await store_a.close()
        await store_b.close()
    assert first.owned and second.owned
    assert {first.run_id, second.run_id} == {"run-a", "run-b"}


async def test_concurrent_duplicate_claims_have_single_winner(store: SQLiteRunStore) -> None:
    results = await asyncio.gather(*(claim(store, f"candidate-{i}") for i in range(12)))
    winners = [result for result in results if result.owned]
    assert len(winners) == 1
    assert {result.run_id for result in results} == {winners[0].run_id}
    assert all(r.owned or r.reason == "already_claimed" for r in results)


async def test_concurrent_retry_has_single_winner(tmp_path: Path) -> None:
    store_a = await _make_store(tmp_path)
    store_b = await _make_store(tmp_path)
    try:
        assert (await claim(store_a, "dup-run", msg="same")).owned
        await store_a.finish("dup-run", "failed", "boom")
        results = await asyncio.gather(
            claim(store_a, "dup-run", msg="same"),
            claim(store_b, "dup-run", msg="same"),
        )
    finally:
        await store_a.close()
        await store_b.close()
    owned = [result for result in results if result.owned]
    assert len(owned) == 1, f"exactly one retry winner, got {results}"
    assert owned[0].reason == "retry_after_terminal_failure"
    assert all(result.reason == "already_claimed" for result in results if not result.owned)
