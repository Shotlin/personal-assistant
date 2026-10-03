"""Serialized desktop lease queue (P7, R05) with mission-grade fencing (T05).

When multiple runs contend for desktop execution (CUA), this queue
serializes them rather than failing outright. Phase 1 additions:

- the grant/cancel race is fixed: a waiter cancelled exactly when the lease
  is granted can no longer strand the queue with a ghost owner (TC-12);
- every grant and release is logged with owner, fence and generation so the
  ownership history reconstructs from logs;
- ``lease()`` is the one cancellation-safe context manager missions use;
- ``stop_owner()`` reports the ACTUAL state it produced (stopped / not
  owner / uncertain), never a claim that input was released when the
  driver cannot prove it. A lost release certainty is BLOCKED, and the
  SQLite TTL lease is explicitly NOT fencing.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from assistant.runtime.session import DesktopLeaseBusy

logger = logging.getLogger("assistant.runtime.desktop_queue")

DEFAULT_QUEUE_TIMEOUT_SECONDS = 30.0


@dataclass
class _QueueWaiter:
    run_id: str
    future: asyncio.Future[LeaseHandle]
    granted: LeaseHandle | None = None
    queued_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class LeaseHandle:
    """What one granted lease carries: identity, fence, generation."""

    run_id: str
    fence: str
    generation: int
    granted_at: float


class DesktopQueue:
    """FIFO queue managing serialized access to the single desktop session."""

    def __init__(self, timeout_seconds: float = DEFAULT_QUEUE_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds
        self._owner: str | None = None
        self._owner_fence: str = ""
        self._generation = 0
        self._waiters: list[_QueueWaiter] = []
        self._lock = asyncio.Lock()
        self._history: list[dict[str, Any]] = []

    @property
    def current_owner(self) -> str | None:
        return self._owner

    @property
    def owner_fence(self) -> str:
        return self._owner_fence

    @property
    def generation(self) -> int:
        """Bumped on every stop; a stale generation can never act."""
        return self._generation

    @property
    def queue_length(self) -> int:
        return len(self._waiters)

    def is_waiting(self, run_id: str) -> bool:
        return any(w.run_id == run_id for w in self._waiters)

    def status_for(self, run_id: str) -> str:
        if self._owner == run_id:
            return "active"
        if self.is_waiting(run_id):
            return "Waiting for desktop"
        return "idle"

    def ownership_log(self) -> list[dict[str, Any]]:
        """The grant/release history, for reconstructing who owned what."""
        return list(self._history)

    def _log(self, event: str, run_id: str, **extra: Any) -> None:
        entry = {"event": event, "run_id": run_id, "at": time.monotonic(), **extra}
        self._history.append(entry)
        if len(self._history) > 256:
            self._history.pop(0)
        logger.info("desktop_queue_%s", event, extra={"event": f"desktop_queue_{event}",
                                                      "run_id": run_id, **extra})

    def _grant_locked(self, run_id: str, *, queued: bool) -> LeaseHandle:
        self._generation += 1
        self._owner = run_id
        self._owner_fence = uuid.uuid4().hex
        handle = LeaseHandle(run_id, self._owner_fence, self._generation, time.monotonic())
        self._log("granted", run_id, generation=handle.generation,
                  fence=handle.fence, queued=queued)
        return handle

    def _grant_next_locked(self) -> None:
        while self._waiters:
            waiter = self._waiters.pop(0)
            if waiter.future.done():
                continue
            # Reserve ownership BEFORE waking the task. An arriving caller
            # cannot barge into the gap between notification and resumption.
            waiter.granted = self._grant_locked(waiter.run_id, queued=True)
            waiter.future.set_result(waiter.granted)
            return

    async def acquire(self, run_id: str, *, timeout: float | None = None) -> LeaseHandle:
        """Acquire one unique lease; repeated run IDs are separate waiters."""
        wait_limit = self._timeout if timeout is None else timeout
        async with self._lock:
            if self._owner is None:
                return self._grant_locked(run_id, queued=False)
            waiter = _QueueWaiter(run_id, asyncio.get_running_loop().create_future())
            self._waiters.append(waiter)
            self._log("waiting", run_id, owner=self._owner, position=len(self._waiters))
        try:
            handle = await asyncio.wait_for(waiter.future, timeout=wait_limit)
            async with self._lock:
                if not self.check_usable(handle):
                    raise DesktopLeaseBusy("lease invalidated before acquisition completed")
                return handle
        except (TimeoutError, asyncio.CancelledError) as exc:
            async with self._lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                # Run identity alone cannot prove THIS waiter owns a grant.
                if waiter.granted is not None:
                    self._release_locked(run_id, reason="abandoned_grant",
                                         fence=waiter.granted.fence)
            if isinstance(exc, TimeoutError):
                raise DesktopLeaseBusy(
                    f"Timed out waiting for desktop lease after {wait_limit}s"
                ) from exc
            raise

    def _release_locked(self, run_id: str, *, reason: str, fence: str = "") -> bool:
        """Release only the exact lease the caller still holds.

        C06/N09: when a fence is supplied it must match the CURRENT lease —
        a stale releaser (whose lease was already superseded by a stop or a
        newer grant) cannot release the newer lease out from under it.
        """
        if self._owner != run_id:
            return False
        if fence and fence != self._owner_fence:
            self._log("stale_release_refused", run_id,
                      held_fence=self._owner_fence, caller_fence=fence)
            return False
        self._owner = None
        self._owner_fence = ""
        self._log("released", run_id, reason=reason)
        self._grant_next_locked()
        return True

    async def release(self, run_id: str, *, fence: str = "") -> None:
        """Release the desktop lease and grant it to the next queued waiter.

        With ``fence`` supplied, a stale releaser is refused: only the
        holder of the CURRENT fence can release it.
        """
        async with self._lock:
            self._release_locked(run_id, reason="normal_release", fence=fence)

    async def stop_owner(self, run_id: str | None = None) -> dict[str, Any]:
        """Stop the current owner and report the ACTUAL state produced.

        Returns ``{"stopped": bool, "owner": str | None, "certain": bool}``.
        Certainty is about whether stopping produced the claimed state
        locally; whether any held physical input was released is a driver
        capability question that only a live capability test can answer --
        this method never claims it did.
        """
        async with self._lock:
            target = run_id or self._owner
            if target is None or self._owner != target:
                self._generation += 1
                self._log("stop_noop", target or "", generation=self._generation)
                return {"stopped": False, "owner": self._owner, "certain": True}
            self._generation += 1
            self._owner = None
            self._owner_fence = ""
            self._log("stopped", target, generation=self._generation)
            self._grant_next_locked()
            return {"stopped": True, "owner": target, "certain": True}

    def check_usable(self, handle: LeaseHandle) -> bool:
        """A lease survives only under the same generation and fence."""
        return (
            self._owner == handle.run_id
            and self._owner_fence == handle.fence
            and self._generation == handle.generation
        )


class QueuedDesktopSessionManager:
    """DesktopSessionManager wrapper that queues concurrent desktop runs."""

    def __init__(self, inner_manager: Any, queue: DesktopQueue | None = None) -> None:
        self.inner = inner_manager
        self.queue = queue or DesktopQueue()

    async def open_run(self, run_id: str, timeout: float | None = None) -> AsyncIterator[Any]:
        handle = await self.queue.acquire(run_id, timeout=timeout)
        try:
            async with self.inner.open(run_id) as run:
                yield run
        finally:
            await self.queue.release(run_id, fence=handle.fence)

    @asynccontextmanager
    async def lease(
        self, run_id: str, *, timeout: float | None = None
    ) -> AsyncIterator[tuple[Any, LeaseHandle]]:
        """The one cancellation-safe mission lease: queue + session handle.

        Yields ``(desktop_run, lease_handle)``. A cancellation at any point
        (before the grant, exactly at the grant, during the session) leaves
        the queue consistent: either this run owns the lease and cleans it
        up, or it never owned it.
        """
        handle = await self.queue.acquire(run_id, timeout=timeout)
        try:
            if not self.queue.check_usable(handle):
                raise DesktopLeaseBusy("lease invalidated before the session opened")
            async with self.inner.open(run_id) as run:
                if not self.queue.check_usable(handle):
                    raise DesktopLeaseBusy("lease invalidated during session open")
                yield run, handle
        finally:
            # Fence-aware release: a superseded holder cannot drop a newer lease.
            await self.queue.release(run_id, fence=handle.fence)


__all__ = ["DesktopQueue", "LeaseHandle", "QueuedDesktopSessionManager"]
