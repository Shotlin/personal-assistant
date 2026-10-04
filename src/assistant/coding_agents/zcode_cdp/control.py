"""The ZCode control session: one open window connection, used by one run at a time.

The debug port is only open while a session is. A session opens when a run (or its pre-run check)
needs it, stays for a short idle time so back-to-back runs do not relaunch ZCode each time, then
ZCode is reopened normally and the port closes. Opening needs the user's standing "yes" (saved
once), because ZCode has to be quit and relaunched to get the port.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from assistant.coding_agents.zcode_cdp.client import CdpClient, CdpError
from assistant.coding_agents.zcode_cdp.contract import wait_for_contract
from assistant.coding_agents.zcode_cdp.driver import WindowDriver
from assistant.coding_agents.zcode_cdp.launcher import ZCodeLauncher
from assistant.coding_agents.zcode_cdp.reader import Snapshot, open_page, read_with_adapter

CONTROL_KEY = "zcode_control"
IDLE_SECONDS = 600.0
WATCH_SECONDS = 30.0


class ControlRefused(Exception):
    """Sani will not use ZCode's window now. The message is plain words for the user."""


class StateStore(Protocol):
    async def get_state(self, key: str) -> dict[str, Any]: ...

    async def put_state(self, key: str, value: dict[str, Any]) -> None: ...


class _Launcher(Protocol):
    port: int

    def installed(self) -> bool: ...

    async def running(self) -> bool: ...

    async def quit(self) -> bool: ...

    async def start_with_port(self) -> int: ...

    async def version(self) -> str: ...

    async def restore_normal(self) -> bool: ...


class ControlSession:
    def __init__(
        self,
        *,
        launcher_factory: Callable[[], _Launcher] = ZCodeLauncher,
        opener: Callable[[int], Awaitable[Any]] = open_page,
        idle_seconds: float = IDLE_SECONDS,
        watch_seconds: float = WATCH_SECONDS,
        on_account: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._launcher_factory = launcher_factory
        self._opener = opener
        self._idle = idle_seconds
        self._watch_every = watch_seconds
        self.on_account = on_account
        self._watch_task: asyncio.Task[None] | None = None
        self._clock = clock
        self._sleep = sleep
        self._store: StateStore | None = None
        self._lock = asyncio.Lock()
        self._launcher: _Launcher | None = None
        self._client: CdpClient | None = None
        self._driver: WindowDriver | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self.version = ""
        self.opened_at = 0.0

    def bind_store(self, store: StateStore | None) -> None:
        self._store = store

    # -- the user's standing yes ------------------------------------------------------

    async def enabled(self) -> bool:
        if self._store is None:
            return False
        return (await self._store.get_state(CONTROL_KEY)).get("enabled") is True

    async def set_enabled(self, enabled: bool) -> None:
        if self._store is not None:
            await self._store.put_state(CONTROL_KEY, {"enabled": enabled, "as_of": time.time()})
        if not enabled:
            await self.close()

    async def remember(self, key: str, value: dict[str, Any]) -> None:
        """Keep a small record (fresh balances, the last run) in Sani's own state."""
        if self._store is not None:
            await self._store.put_state(key, value)

    @property
    def is_open(self) -> bool:
        return self._client is not None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def _watch(self) -> None:
        """While the window is open, notice a different signed-in account within ~30 seconds."""
        while self._client is not None:
            await self._sleep(self._watch_every)
            if self._client is None or self.on_account is None:
                return
            async with self._lock:
                if self._driver is None:
                    return
                try:
                    account = await self._driver.pages.account()
                except CdpError:
                    continue
            await self.on_account(account)

    async def read_in_place(self) -> Snapshot | None:
        """Read account, models, balances and tasks through the already-open window."""
        if not self.is_open:
            return None
        async with self._lock:
            if self._driver is None or not await self._alive():
                return None
            snap = Snapshot(as_of=time.time())
            try:
                await read_with_adapter(self._driver.pages, self.version, snap)
            except CdpError as problem:
                snap.refusal = str(problem)
            return snap

    # -- opening and closing -----------------------------------------------------------

    async def _alive(self) -> bool:
        if self._client is None:
            return False
        try:
            return await self._client.evaluate("1+1", timeout=3.0) == 2
        except CdpError:
            return False

    async def _open(self) -> WindowDriver:
        launcher = self._launcher_factory()
        if not launcher.installed():
            raise ControlRefused("ZCode isn't installed.")
        if await launcher.running() and not await launcher.quit():
            raise ControlRefused(
                "ZCode did not close (it may be asking about unsaved work), so nothing was started."
            )
        client: CdpClient | None = None
        try:
            await launcher.start_with_port()
            self.version = await launcher.version()
            client = await self._opener(launcher.port)
            driver = WindowDriver(client)
            contract = await wait_for_contract(driver.pages, self.version)
            if not contract.ok:
                raise ControlRefused(contract.refusal())
            if not contract.version_verified:
                raise ControlRefused(
                    f"ZCode {self.version} is a version Sani has not been checked against, so it "
                    "will not click in it. Read-only checks still work."
                )
            account = await driver.pages.account()
            if not account.get("signed_in"):
                raise ControlRefused(
                    "ZCode is signed out. Open ZCode and sign in; Sani never types credentials."
                )
        except (CdpError, ControlRefused) as problem:
            if client is not None:
                await client.close()
            await launcher.restore_normal()
            if isinstance(problem, ControlRefused):
                raise
            raise ControlRefused(str(problem)) from problem
        self._launcher, self._client, self._driver = launcher, client, driver
        self.opened_at = self._clock()
        if self.on_account is not None:
            self._watch_task = asyncio.create_task(self._watch())
        return driver

    async def close(self) -> None:
        """Close the port: drop the connection and reopen ZCode normally."""
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        if self._watch_task is not None and self._watch_task is not asyncio.current_task():
            self._watch_task.cancel()
        self._watch_task = None
        client, launcher = self._client, self._launcher
        self._client = self._driver = self._launcher = None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()
        if launcher is not None:
            await launcher.restore_normal()

    async def _close_when_idle(self) -> None:
        await self._sleep(self._idle)
        if not self._lock.locked():
            await self.close()

    # -- using it ----------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[WindowDriver]:
        """Exclusive use of ZCode's window (one at a time, N4); opens the port if needed."""
        if not await self.enabled():
            raise ControlRefused(
                "ZCode control is off. The user turns it on once in Sani (it lets Sani close and "
                "reopen ZCode to read and drive it); nothing was started."
            )
        async with self._lock:
            if self._idle_task is not None:
                self._idle_task.cancel()
                self._idle_task = None
            driver = self._driver if await self._alive() else None
            if driver is None:
                await self.close()
                driver = await self._open()
            try:
                yield driver
            finally:
                if self._client is not None:
                    self._idle_task = asyncio.create_task(self._close_when_idle())
