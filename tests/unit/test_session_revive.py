"""Mid-run session-end recovery contract (live incidents 2026-09-18, 2026-09-24).

A daemon session can end mid-run and the driver invites exactly one revival. Its
wording changed between driver builds -- 0.28.2 says "session 'x' has ended; tool
call 'y' was rejected. Call start_session with this id to revive it" -- so the
classification is asserted against both shapes, because a missed match turns a
recoverable fault into a failed run.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

from assistant.runtime.cua_faults import CuaFault, classify_text
from assistant.runtime.session import (
    DesktopDriverError,
    DesktopSessionManager,
    McpToolDesktopDriver,
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
                "start_session failed: session has ended and must be revived",
                CuaFault.SESSION,
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
            raise DesktopDriverError(
                "permissions_pending: not granted", CuaFault.PERMISSION
            )

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


ENDED_0_28_2 = (
    "session 'p2' has ended; tool call 'get_screen_size' was rejected. Call "
    "start_session with this id to revive it before issuing further actions, "
    "or use a new session id."
)


def test_both_driver_wordings_of_an_ended_session_classify_as_revivable() -> None:
    assert classify_text(
        "session mcp-1-2 has ended and must be revived before any action"
    ) is CuaFault.SESSION
    assert classify_text(ENDED_0_28_2) is CuaFault.SESSION
    assert classify_text("wrong foreground app") is CuaFault.UNKNOWN


def test_the_controller_attaches_the_fault_from_real_driver_text() -> None:
    """The retry policy reads the fault, so the wiring is the contract."""

    class _RejectingTool:
        async def ainvoke(self, arguments: object) -> object:
            from assistant.tools.result_normalizer import ToolOutcome

            return ToolOutcome("failed", "unverifiable", text=ENDED_0_28_2)

    async def scenario() -> None:
        driver = McpToolDesktopDriver({"start_session": _RejectingTool()})
        with pytest.raises(DesktopDriverError) as raised:
            await driver.start_session("assistant-x")
        assert raised.value.fault is CuaFault.SESSION
        assert raised.value.recovery.value == "revive_session"

    asyncio.run(scenario())


def test_a_session_the_driver_invited_back_to_life_really_comes_back() -> None:
    """Revival is not just classified, it is exercised on a live daemon."""

    class EndedThenLive(FakeDriver):
        def __init__(self) -> None:
            super().__init__()
            self.starts = 0

        async def start_session(self, session: str, **kwargs: Any) -> Any:
            self.calls.append(DriverCall("start_session", {"session": session}))
            if self.starts == 0:
                self.starts += 1
                raise DesktopDriverError(ENDED_0_28_2, CuaFault.SESSION)
            return {"active": True}

    async def scenario() -> None:
        driver = EndedThenLive()
        async with DesktopSessionManager(driver).open("r9") as run:
            await run.ensure_started()
            assert run._started is True
            assert len([c for c in driver.calls if c.name == "start_session"]) == 2

    asyncio.run(scenario())
