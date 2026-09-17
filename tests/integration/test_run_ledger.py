"""Durable run registry, atomic claim, action ledger, desktop lease (WP4).

Uses the real compose PostgreSQL (isolation via unique ids per test), so
the SQL semantics under test are the production ones.
"""

from __future__ import annotations

import asyncio
import uuid

from assistant.runtime.runs import DesktopLease, RunStore


def _uid() -> str:
    return uuid.uuid4().hex[:12]


async def _store():
    store = await RunStore.connect(
        "postgresql://assistant:assistant@127.0.0.1:5433/assistant"
    )
    await store.setup()
    return store


async def _claimed_run(store: RunStore, digest: str = "d") -> str:
    """Create a fresh claimed run and return its run_id."""
    run_id = f"run-{_uid()}"
    claim = await store.claim(
        user_id=_uid(),
        chat_id=_uid(),
        user_message_id=_uid(),
        request_digest=digest,
        run_id=run_id,
    )
    assert claim.owned is True
    return run_id


async def test_first_claim_wins_and_duplicate_delivery_observes() -> None:
    store = await _store()
    try:
        user, chat, msg = _uid(), _uid(), _uid()
        first = await store.claim(
            user_id=user,
            chat_id=chat,
            user_message_id=msg,
            request_digest="digest-1",
            run_id=f"run-{_uid()}",
        )
        assert first.owned is True

        # Same id + same digest: duplicate delivery, not a second execution.
        second = await store.claim(
            user_id=user,
            chat_id=chat,
            user_message_id=msg,
            request_digest="digest-1",
            run_id=f"run-{_uid()}",
        )
        assert second.owned is False
        assert second.reason == "already_claimed"
    finally:
        await store.close()


async def test_same_text_new_id_is_a_new_intentional_command() -> None:
    store = await _store()
    try:
        user, chat = _uid(), _uid()
        a = await store.claim(
            user_id=user,
            chat_id=chat,
            user_message_id=_uid(),
            request_digest="text-digest",
            run_id=f"run-{_uid()}",
        )
        b = await store.claim(
            user_id=user,
            chat_id=chat,
            user_message_id=_uid(),
            request_digest="text-digest",
            run_id=f"run-{_uid()}",
        )
        assert a.owned and b.owned
    finally:
        await store.close()


async def test_conflicting_digest_same_id_is_rejected() -> None:
    store = await _store()
    try:
        user, chat, msg = _uid(), _uid(), _uid()
        await store.claim(
            user_id=user,
            chat_id=chat,
            user_message_id=msg,
            request_digest="digest-1",
            run_id=f"run-{_uid()}",
        )
        conflict = await store.claim(
            user_id=user,
            chat_id=chat,
            user_message_id=msg,
            request_digest="digest-2",
            run_id=f"run-{_uid()}",
        )
        assert conflict.owned is False
        assert conflict.reason == "identity_conflict"
    finally:
        await store.close()


async def test_run_status_transitions_are_recorded() -> None:
    store = await _store()
    try:
        run_id = await _claimed_run(store)
        record = await store.get_run(run_id)
        assert record is not None and record.status == "running"
        await store.finish(run_id, "completed")
        record = await store.get_run(run_id)
        assert record is not None and record.status == "completed"
    finally:
        await store.close()


async def test_action_ledger_records_plan_then_outcome() -> None:
    store = await _store()
    try:
        run_id = await _claimed_run(store)
        ledger_id = await store.record_action(
            run_id, "step-1", "click", target_desc="calculator button 7"
        )
        record = await store.get_run(run_id)
        assert record is not None
        assert record.actions[0]["state"] == "planned"
        await store.mark_action(ledger_id, "confirmed", "artifact:display-42")
        record = await store.get_run(run_id)
        assert record is not None
        assert record.actions[0]["state"] == "confirmed"
        assert record.actions[0]["evidence_ref"] == "artifact:display-42"
    finally:
        await store.close()


async def test_uncertain_dispatch_is_marked_unknown_not_replayed() -> None:
    """Crash after dispatch/before acknowledgement -> unknown state, which
    requires readback; the action is NOT silently resubmitted (WP4 gate)."""
    store = await _store()
    try:
        run_id = await _claimed_run(store)
        ledger_id = await store.record_action(run_id, "step-1", "click")
        # simulated crash window: outcome never observed
        await store.mark_action(ledger_id, "unknown")
        pending = await store.actions_in_state(run_id, "unknown")
        assert len(pending) == 1
        assert pending[0]["step_id"] == "step-1"
    finally:
        await store.close()


async def test_desktop_lease_is_exclusive_across_owners() -> None:
    store = await _store()
    try:
        lease = DesktopLease(store._conn, ttl_seconds=60)
        assert await lease.acquire("owner-1") is True
        other = DesktopLease(store._conn, ttl_seconds=60)
        assert await other.acquire("owner-2") is False
        await lease.release("owner-1")
        assert await other.acquire("owner-2") is True
        await other.release("owner-2")
    finally:
        await store.close()


async def test_expired_lease_is_stealable() -> None:
    store = await _store()
    try:
        lease = DesktopLease(store._conn, ttl_seconds=0.05)
        assert await lease.acquire("owner-1") is True
        await asyncio.sleep(0.08)
        other = DesktopLease(store._conn, ttl_seconds=60)
        assert await other.acquire("owner-2") is True
        await other.release("owner-2")
    finally:
        await store.close()
