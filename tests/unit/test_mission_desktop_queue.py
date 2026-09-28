"""Desktop queue + mission lease tests (T05, file 06 U4, TC-12).

The queue must survive a cancellation landing exactly at the grant, report
honest stop state, and never hand a stale lease to a second owner.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from assistant.runtime.desktop_queue import DesktopQueue, QueuedDesktopSessionManager
from assistant.runtime.session import DesktopLeaseBusy


async def test_cancel_exactly_at_grant_does_not_strand_queue() -> None:
    """The grant/cancel race: the queue must not keep a ghost owner."""
    queue = DesktopQueue(timeout_seconds=5)
    owner_task = asyncio.create_task(_hold(queue, "owner", hold=0.05))
    await asyncio.sleep(0.01)
    victim = asyncio.create_task(_hold(queue, "victim", hold=0.2, cancel_after=0.045))
    results = await asyncio.gather(owner_task, victim, return_exceptions=True)
    cancelled = any(isinstance(r, asyncio.CancelledError) for r in results)
    assert cancelled, "the victim was expected to be cancelled mid-queue"
    # Whatever happened, the queue must end empty and grantable.
    assert queue.current_owner is None
    _handle = await queue.acquire("next-run", timeout=1)
    assert queue.current_owner == "next-run"
    await queue.release("next-run")


async def _hold(
    queue: DesktopQueue,
    run_id: str,
    *,
    hold: float,
    cancel_after: float | None = None,
) -> None:
    task = asyncio.current_task()
    _handle = await queue.acquire(run_id)
    try:
        if cancel_after is not None and task is not None:
            await asyncio.sleep(cancel_after)
            task.cancel()
            await asyncio.sleep(hold)  # inside cancellation delivery
            return
        await asyncio.sleep(hold)
    finally:
        await queue.release(run_id)


async def test_ownership_log_reconstructs_grant_release() -> None:
    queue = DesktopQueue(timeout_seconds=2)
    handle_a = await queue.acquire("a")
    assert queue.check_usable(handle_a)
    await queue.release("a")
    handle_b = await queue.acquire("b")
    assert queue.check_usable(handle_b)
    await queue.release("b")
    events = [entry["event"] for entry in queue.ownership_log()]
    assert events.count("granted") >= 2 and events.count("released") >= 2
    assert queue.ownership_log()[0]["run_id"] == "a"


async def test_stop_owner_reports_actual_state() -> None:
    queue = DesktopQueue(timeout_seconds=2)
    not_holding = await queue.stop_owner("ghost")
    assert not_holding == {"stopped": False, "owner": None, "certain": True}
    await queue.acquire("a")
    stopped = await queue.stop_owner("a")
    assert stopped["stopped"] is True and stopped["certain"] is True
    assert queue.current_owner is None


async def test_generation_invalidates_old_lease() -> None:
    queue = DesktopQueue(timeout_seconds=2)
    handle = await queue.acquire("a")
    assert queue.check_usable(handle)
    await queue.stop_owner("a")
    assert not queue.check_usable(handle), "a stopped generation can never act again"
    next_handle = await queue.acquire("b")
    assert next_handle.generation > handle.generation
    assert next_handle.fence != handle.fence
    await queue.release("b")


async def test_lease_context_manager_cleans_up_on_cancellation() -> None:
    """Cancelling inside the lease releases the desktop for the next run."""

    class _Inner:
        def __init__(self) -> None:
            self.opened = 0

        @asynccontextmanager
        async def open(self, run_id: str):  # type: ignore[no-untyped-def]
            self.opened += 1

            class _Run:
                cancelled = False

                def cancel(self) -> None:
                    self.cancelled = True

            yield _Run()

    inner = _Inner()
    manager = QueuedDesktopSessionManager(inner, queue=DesktopQueue(timeout_seconds=2))

    async def victim() -> None:
        async with manager.lease("v") as (_run, _handle):
            await asyncio.sleep(10)

    task = asyncio.create_task(victim())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.queue.current_owner is None
    _handle = await manager.queue.acquire("next")
    await manager.queue.release("next")


async def test_interleaved_missions_one_owner_at_a_time() -> None:
    """TC-12: two missions, interleaved grants; exactly one owner holds."""
    queue = DesktopQueue(timeout_seconds=5)
    order: list[str] = []

    async def mission(name: str, work: float) -> None:
        handle = await queue.acquire(name)
        order.append(f"{name}:granted")
        await asyncio.sleep(work)
        assert queue.check_usable(handle), f"{name} lost its lease mid-work"
        await queue.release(name)
        order.append(f"{name}:released")

    await asyncio.gather(mission("m1", 0.05), mission("m2", 0.05))
    # Serialize: each grant follows the other's release.
    assert order.index("m1:released") < order.index("m2:granted") or (
        order.index("m2:released") < order.index("m1:granted")
    )
    assert queue.current_owner is None


async def test_timeout_while_waiting_releases_any_grant_race() -> None:
    """A waiter that times out exactly as the grant lands releases cleanly."""
    queue = DesktopQueue(timeout_seconds=2)
    await queue.acquire("holder")
    async def late_waiter() -> None:
        with pytest.raises(DesktopLeaseBusy):
            await queue.acquire("impatient", timeout=0.05)
    task = asyncio.create_task(late_waiter())
    await asyncio.sleep(0.06)
    await queue.release("holder")
    await task
    assert queue.current_owner is None


# -- C06/N09: single-flight grants, fence-aware release -----------------------


async def test_concurrent_same_run_acquire_cannot_double_grant() -> None:
    """C06/N09: two concurrent acquires by the SAME run id must not both
    hold leases with different fences — the second queues or times out."""
    import asyncio

    queue = DesktopQueue(timeout_seconds=0.2)
    first = await queue.acquire("mission-a")
    task = asyncio.ensure_future(queue.acquire("mission-a"))
    await asyncio.sleep(0.01)
    assert queue.queue_length == 1, "the duplicate acquire must queue, not regrant"
    await queue.release("mission-a", fence=first.fence)
    try:
        second = await task
    except DesktopLeaseBusy:
        second = None
    if second is not None:
        assert second.fence != first.fence
        assert queue.owner_fence == second.fence
        assert not queue.check_usable(first), "the first lease must be dead"


async def test_stale_releaser_cannot_release_newer_lease() -> None:
    """C06/N09: release by fence — a holder whose lease was superseded
    (stop bumped the generation) cannot release the newer lease."""
    queue = DesktopQueue(timeout_seconds=5)
    first = await queue.acquire("mission-a")
    await queue.stop_owner("mission-a")
    second = await queue.acquire("mission-b")
    # The stopped old holder tries to release with its dead fence.
    await queue.release("mission-a", fence=first.fence)
    assert queue.current_owner == "mission-b", "the newer lease must survive"
    assert queue.check_usable(second)
    # The real holder releases by its own (current) fence.
    await queue.release("mission-b", fence=second.fence)
    assert queue.current_owner is None


async def test_release_with_wrong_fence_is_refused() -> None:
    queue = DesktopQueue(timeout_seconds=5)
    handle = await queue.acquire("mission-a")
    await queue.release("mission-a", fence="not-the-fence")
    assert queue.current_owner == "mission-a", (
        "a mismatched fence cannot release someone else's active lease"
    )
    await queue.release("mission-a", fence=handle.fence)
    assert queue.current_owner is None
