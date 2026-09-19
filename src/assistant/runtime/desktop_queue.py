"""Serialized desktop lease queue (P7, R05).

When multiple runs contend for desktop execution (CUA), this queue
serializes them rather than failing outright. Waiting runs report
state "Waiting for desktop" until the previous holder releases or
a timeout expires.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from assistant.runtime.session import DesktopLeaseBusy

logger = logging.getLogger("assistant.runtime.desktop_queue")

DEFAULT_QUEUE_TIMEOUT_SECONDS = 30.0


@dataclass
class _QueueWaiter:
    run_id: str
    future: asyncio.Future[None]
    queued_at: float = field(default_factory=time.monotonic)


class DesktopQueue:
    """FIFO queue managing serialized access to the single desktop session."""

    def __init__(self, timeout_seconds: float = DEFAULT_QUEUE_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout_seconds
        self._owner: str | None = None
        self._waiters: list[_QueueWaiter] = []
        self._lock = asyncio.Lock()

    @property
    def current_owner(self) -> str | None:
        return self._owner

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

    async def acquire(self, run_id: str, *, timeout: float | None = None) -> None:
        """Acquire the desktop lease, queuing if another run owns it."""
        wait_limit = self._timeout if timeout is None else timeout
        waiter: _QueueWaiter | None = None

        async with self._lock:
            if self._owner is None or self._owner == run_id:
                self._owner = run_id
                return

            loop = asyncio.get_running_loop()
            waiter = _QueueWaiter(run_id=run_id, future=loop.create_future())
            self._waiters.append(waiter)
            logger.info(
                "desktop_queue_waiting",
                extra={
                    "event": "desktop_queue_waiting",
                    "run_id": run_id,
                    "owner": self._owner,
                    "position": len(self._waiters),
                    "status": "Waiting for desktop",
                },
            )

        try:
            await asyncio.wait_for(waiter.future, timeout=wait_limit)
        except TimeoutError as exc:
            async with self._lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
            raise DesktopLeaseBusy(
                f"Timed out waiting for desktop lease after {wait_limit}s (held by {self._owner!r})"
            ) from exc
        except asyncio.CancelledError:
            async with self._lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
            raise

    async def release(self, run_id: str) -> None:
        """Release the desktop lease and grant it to the next queued waiter."""
        async with self._lock:
            if self._owner != run_id:
                # If this run was in the wait queue, remove it
                self._waiters = [w for w in self._waiters if w.run_id != run_id]
                return

            self._owner = None
            while self._waiters:
                next_waiter = self._waiters.pop(0)
                if not next_waiter.future.cancelled():
                    self._owner = next_waiter.run_id
                    next_waiter.future.set_result(None)
                    logger.info(
                        "desktop_queue_granted",
                        extra={
                            "event": "desktop_queue_granted",
                            "run_id": next_waiter.run_id,
                        },
                    )
                    break


class QueuedDesktopSessionManager:
    """DesktopSessionManager wrapper that queues concurrent desktop runs."""

    def __init__(self, inner_manager: Any, queue: DesktopQueue | None = None) -> None:
        self.inner = inner_manager
        self.queue = queue or DesktopQueue()

    async def open_run(self, run_id: str, timeout: float | None = None) -> AsyncIterator[Any]:
        await self.queue.acquire(run_id, timeout=timeout)
        try:
            async with self.inner.open(run_id) as run:
                yield run
        finally:
            await self.queue.release(run_id)
