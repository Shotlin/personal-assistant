"""Mid-run session-end recovery contract (live incident 2026-09-18).

A bounded daemon session can end mid-run; the driver's own error says
'... has ended and must be revived'. Observations must recover via one
explicit revive; mutating actions are never blindly retried.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

from assistant.runtime.session import (
    DesktopDriverError,
    DesktopSessionManager,
    _session_revive_demanded,
)
from tests.helpers.fake_driver import DriverCall, FakeDriver


class ReviveDriver(FakeDriver):
    """Refuses the first start with the live 'revive' error, then works."""

    def __init__(self) -> None:
        super().__init__()
        self.starts = 0

    async def start_session(self, session: str, **kwargs: Any) -> Any:
        self.calls.append(DriverCall("start_session", {"session": session}))
        if self.starts == 0:
            self.starts += 1
            raise DesktopDriverError(
                "start_session failed: session has ended and must be revived"
            )
        return {"active": True}


@asynccontextmanager
async def _run(manager: DesktopSessionManager, run_id: str):
    async with manager.open(run_id) as run:
        yield run


def test_ensure_started_revives_once_on_explicit_demand() -> None:
    async def scenario() -> None:
        driver = ReviveDriver()
        manager = DesktopSessionManager(driver)
        async with _run(manager, "r1") as run:
            await run.ensure_started()
            assert run._started is True
            starts = [c for c in driver.calls if c.name == "start_session"]
            assert len(starts) == 2  # first refused, one revival succeeded

    asyncio.run(scenario())


def test_ensure_started_still_fails_closed_on_other_errors() -> None:
    class PendingDriver(FakeDriver):
        async def start_session(self, session: str, **kwargs: Any) -> Any:
            raise DesktopDriverError("permissions_pending: not granted")

    async def scenario() -> None:
        async with _run(DesktopSessionManager(PendingDriver()), "r2") as run:
            with pytest.raises(DesktopDriverError, match="permissions_pending"):
                await run.ensure_started()

    asyncio.run(scenario())


def test_ensure_started_refuses_second_unknown_retry() -> None:
    class UncertainDriver(FakeDriver):
        async def start_session(self, session: str, **kwargs: Any) -> Any:
            raise TimeoutError("driver timeout")

    async def scenario() -> None:
        async with _run(DesktopSessionManager(UncertainDriver()), "r3") as run:
            with pytest.raises((DesktopDriverError, TimeoutError)):
                await run.ensure_started()
            with pytest.raises(DesktopDriverError, match="refusing retry"):
                await run.ensure_started()

    asyncio.run(scenario())


def test_revive_resets_and_restarts() -> None:
    async def scenario() -> None:
        driver = ReviveDriver()
        manager = DesktopSessionManager(driver)
        async with _run(manager, "r4") as run:
            await run.ensure_started()
            await driver.end_session(run.session_id)  # simulate mid-run end
            await run.revive()
            starts = [c for c in driver.calls if c.name == "start_session"]
            assert len(starts) >= 2
            assert run._started is True

    asyncio.run(scenario())


def test_session_end_error_text_matches_marker() -> None:
    assert _session_revive_demanded(
        "session mcp-1-2 has ended and must be revived before any action"
    )
    assert not _session_revive_demanded("wrong foreground app")
