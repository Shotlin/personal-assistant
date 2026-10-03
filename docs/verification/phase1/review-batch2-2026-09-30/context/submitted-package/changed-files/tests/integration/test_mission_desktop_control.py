"""Mission desktop-control integration (T05, file 06 I1, TC-12/TC-34).

Process-level environment with fake drivers: one owner at a time, stop
acknowledgement reflects the actual state, and a dead owner's lease can
never be reused by a stale core. Real macOS input behavior stays behind the
L1 live gate (BLOCKED without an authorized fixture).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from assistant.runtime.desktop_queue import DesktopQueue, QueuedDesktopSessionManager
from assistant.runtime.session import DesktopSessionManager


class _FakeDriver:
    """In-memory DesktopDriver (mirrors tests/helpers/fake_driver.py)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def start_session(self, session: str, **kwargs: Any) -> Any:
        self.calls.append(("start_session", {"session": session}))
        return {"session": session}

    async def end_session(self, session: str, **kwargs: Any) -> Any:
        self.calls.append(("end_session", {"session": session}))
        return {"session": session}

    async def set_cursor_enabled(self, session: str, *, enabled: bool, **kwargs: Any) -> Any:
        self.calls.append(("set_agent_cursor_enabled", {"enabled": enabled}))
        return None

    async def set_cursor_motion(self, session: str, **kwargs: Any) -> Any:
        self.calls.append(("set_agent_cursor_motion", {}))
        return None


@pytest.fixture()
def driver() -> _FakeDriver:
    return _FakeDriver()


@pytest.fixture()
def manager(driver: _FakeDriver) -> DesktopSessionManager:
    return DesktopSessionManager(driver, enabled=True)


async def test_owner_dies_lease_never_reused(
    manager: DesktopSessionManager, driver: _FakeDriver
) -> None:
    """The owner's context is killed mid-session; the lease is re-fenced."""
    queue = DesktopQueue(timeout_seconds=2)
    qm = QueuedDesktopSessionManager(manager, queue=queue)
    died = asyncio.Event()

    async def owner() -> None:
        async with qm.lease("owner") as (run, handle):
            assert queue.check_usable(handle)
            await run.ensure_started()
            died.set()
            raise RuntimeError("owner process died")

    async def survivor() -> None:
        await died.wait()
        await asyncio.sleep(0.02)
        async with qm.lease("next") as (_run, handle):
            assert queue.check_usable(handle)
            assert handle.generation > 1

    await asyncio.gather(owner(), survivor(), return_exceptions=True)
    assert queue.current_owner is None
    # The dead owner's driver session was ended during cleanup.
    ended = [call for call in driver.calls if call[0] == "end_session"]
    assert ended, "the dead owner's session was cleaned up"


async def test_stop_reports_and_fences(manager: DesktopSessionManager) -> None:
    queue = DesktopQueue(timeout_seconds=2)
    handle = await queue.acquire("worker")
    assert queue.check_usable(handle)
    report = await queue.stop_owner("worker")
    assert report["stopped"] is True and report["certain"] is True
    assert not queue.check_usable(handle)
    assert queue.current_owner is None


async def test_held_input_release_certainty_is_not_claimed(manager: DesktopSessionManager) -> None:
    """The stop report never claims physical input release (L1-gated)."""
    queue = DesktopQueue(timeout_seconds=2)
    await queue.acquire("worker")
    report = await queue.stop_owner("worker")
    assert "input_released" not in report, "physical release is a live-driver capability"
    await manager.close_all()


async def test_stale_core_wakes_and_must_not_take_over(manager: DesktopSessionManager) -> None:
    """A stale holder cannot act merely because time passed (A08): the
    fence check, not a TTL, governs usability."""
    queue = DesktopQueue(timeout_seconds=2)
    handle = await queue.acquire("stale")
    await asyncio.sleep(0.01)
    # A takeover happens (stop bumps the generation), then the stale holder
    # wakes up holding a lease that is no longer usable.
    takeover = await queue.stop_owner("stale")
    assert takeover["stopped"] is True
    fresh = await queue.acquire("fresh-owner")
    assert fresh.generation > handle.generation
    assert not queue.check_usable(handle), "the stale lease must never be usable"
    await queue.release("stale")  # a no-op: stale is not the current owner
    assert queue.current_owner == "fresh-owner"
    await queue.release("fresh-owner")


async def test_concurrent_sessions_serialize_through_one_manager(
    manager: DesktopSessionManager, driver: _FakeDriver
) -> None:
    queue = DesktopQueue(timeout_seconds=5)
    qm = QueuedDesktopSessionManager(manager, queue=queue)
    active: list[str] = []
    overlaps: list[int] = []

    async def worker(name: str) -> None:
        async with qm.lease(name) as (_run, _handle):
            active.append(name)
            await asyncio.sleep(0.03)
            overlaps.append(len(active))
            active.remove(name)

    await asyncio.gather(worker("a"), worker("b"), worker("c"))
    assert max(overlaps) == 1, "two sessions were open at once"
    assert queue.current_owner is None


async def test_idle_stop_is_safe() -> None:
    """D08: a stop naming nothing is a NO-OP — it must not increment the
    shared generation. The old oracle (generation >= 2) pinned the bug
    where a no-op stop invalidated an unrelated valid lease."""
    queue = DesktopQueue(timeout_seconds=2)
    report = await queue.stop_owner(None)
    assert report == {"stopped": False, "owner": None, "certain": True}
    assert queue.generation == 0, "a no-op stop must not move the generation"
    _handle = await queue.acquire("after")
    await queue.release("after")
    assert queue.generation == 1, "only the grant itself bumps the generation"


async def test_no_second_owner_after_emergency_stop(manager: DesktopSessionManager) -> None:
    """After an emergency stop, pending waiters may grant but must observe
    the new generation and re-check before dispatching."""
    queue = DesktopQueue(timeout_seconds=5)
    generation_before = queue.generation
    handle = await queue.acquire("first")
    waiter = asyncio.create_task(queue.acquire("second"))
    await asyncio.sleep(0.02)
    await queue.stop_owner("first")
    new_handle = await asyncio.wait_for(waiter, timeout=2)
    assert new_handle.generation > generation_before
    assert handle.generation < new_handle.generation
    assert queue.current_owner == "second"
    await queue.release("second")
