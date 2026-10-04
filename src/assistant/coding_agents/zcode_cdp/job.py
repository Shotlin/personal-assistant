"""One "read ZCode" job at a time, started from Settings and polled through the status.

Reading closes and reopens ZCode and takes a minute or two, longer than a host request may
wait, so the button only starts the job and the status says how it is going.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from assistant.coding_agents.zcode_cdp.reader import Snapshot, StateStore, save_snapshot
from assistant.coding_agents.zcode_cdp.sync import AccountSync, credential_signal, plan_names

Reader = Callable[[], Awaitable[Snapshot]]


class ReadJob:
    def __init__(
        self, store: StateStore | None, reader: Reader, clock: Callable[[], float] = time.time
    ) -> None:
        self._store = store
        self._reader = reader
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._state = "idle"
        self._message = ""
        self._started = 0.0
        self._finished = 0.0
        self._port_closed: bool | None = None

    @property
    def reading(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, *, confirmed: bool) -> str:
        """Begin a read. Returns an error in plain words, or empty when it began."""
        if self.reading:
            return ""
        if not confirmed:
            return "ZCode has to be closed and reopened to be read. Confirm that first."
        if self._store is None:
            return "Nowhere to keep what is read."
        self._state, self._message = "reading", ""
        self._started, self._finished, self._port_closed = self._clock(), 0.0, None
        self._task = asyncio.create_task(self._run())
        return ""

    async def _run(self) -> None:
        try:
            snap = await self._reader()
            await self._sync(snap)
            if snap.refusal:
                self._state, self._message = "refused", snap.refusal
            else:
                if self._store is not None:
                    await save_snapshot(self._store, snap)
                unread = ", ".join(snap.not_read) if snap.not_read else ""
                self._state = "done"
                self._message = f"Could not read: {unread}." if unread else ""
            self._port_closed = snap.port_closed
        except Exception as exc:  # a failed read must never take the core down
            self._state, self._message = "refused", f"Reading ZCode failed: {type(exc).__name__}"
        finally:
            self._finished = self._clock()

    async def _sync(self, snap: Snapshot) -> None:
        """Compare this read with the last one *before* anything new is saved."""
        if self._store is None:
            return
        sync = AccountSync(self._store, self._clock)  # type: ignore[arg-type]
        fingerprint, _ = credential_signal()
        if snap.account.get("signed_in") is False:
            await sync.observe(signed_in=False, name="", plans=None, fingerprint=fingerprint)
        elif not snap.refusal and snap.account.get("name"):
            plans = plan_names(snap.balances) if snap.balances.get("items") else None
            await sync.observe(
                signed_in=True,
                name=str(snap.account["name"]),
                plans=plans,
                fingerprint=fingerprint,
            )

    def view(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "message": self._message,
            "started_at": self._started or None,
            "finished_at": self._finished or None,
            "port_closed": self._port_closed,
        }

    async def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
