"""DesktopSessionManager: trusted run-scoped desktop sessions (master plan WP3 / section 7).

No driver required: the driver is faked; the contract under test is the
application-side lifecycle -- lazy start, forced cleanup on every terminal
path, local cancellation, and the single desktop mutation lease.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from assistant.runtime.session import (
    DesktopLeaseBusy,
    DesktopRunCancelled,
    DesktopSessionConfig,
    DesktopSessionManager,
)
from tests.helpers.fake_driver import DriverCall, FakeDriver, call_args, call_names


async def test_non_desktop_request_does_not_start_a_driver_session() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    async with manager.open("read-only-chat"):
        pass
    assert driver.calls == []


async def test_session_is_closed_after_an_exception() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        async with manager.open("desktop-run") as session:
            await session.ensure_started()
            raise RuntimeError("simulated interruption")
    names = call_names(driver)
    assert names.count("start_session") == 1
    assert names.count("end_session") == 1


async def test_lazy_activation_configures_motion_once_and_cleans_up() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver, config=DesktopSessionConfig())
    async with manager.open("run-1") as session:
        await session.ensure_started()
        await session.ensure_started()  # idempotent: still exactly one start
    names = call_names(driver)
    assert names.count("start_session") == 1
    assert names.count("set_agent_cursor_motion") == 1
    motion = call_args(driver, "set_agent_cursor_motion")
    # idle_hide_ms=0 keeps the cursor visible during the whole active run
    # (no disappearance during >30s waits); glide/dwell are UX choices.
    assert motion["idle_hide_ms"] == 0
    assert names.count("end_session") == 1


async def test_never_started_close_makes_no_driver_calls() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    async with manager.open("chat-only"):
        pass  # pure chat run: no desktop action, no session
    assert driver.calls == []


async def test_cancel_prevents_start_and_reports_locally() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    async with manager.open("run-2") as session:
        assert manager.cancel("run-2") is True
        assert session.cancelled is True
        with pytest.raises(DesktopRunCancelled):
            await session.ensure_started()
        with pytest.raises(DesktopRunCancelled):
            session.require_active()
    assert call_names(driver) == []  # nothing was ever dispatched natively


async def test_cancel_unknown_or_closed_run_returns_false() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    assert manager.cancel("missing") is False
    async with manager.open("run-3"):
        pass
    assert manager.cancel("run-3") is False  # run is gone after cleanup


async def test_second_desktop_run_is_rejected_until_lease_released() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    async with manager.open("run-a") as first:
        await first.ensure_started()
        async with manager.open("run-b") as second:
            with pytest.raises(DesktopLeaseBusy):
                await second.ensure_started()
    # Merely opening a chat must not take the desktop lease.
    async with manager.open("run-c") as third:
        await third.ensure_started()


async def test_closed_handle_cannot_start_again() -> None:
    manager = DesktopSessionManager(FakeDriver())
    async with manager.open("closed") as session:
        await session.ensure_started()
    with pytest.raises(DesktopRunCancelled):
        await session.ensure_started()


async def test_concurrent_activation_starts_once() -> None:
    class YieldingDriver(FakeDriver):
        async def start_session(self, session: str, **kwargs: Any) -> None:
            await asyncio.sleep(0)
            await super().start_session(session, **kwargs)

    driver = YieldingDriver()
    manager = DesktopSessionManager(driver)
    async with manager.open("parallel") as session:
        await asyncio.gather(session.ensure_started(), session.ensure_started())
    assert call_names(driver).count("start_session") == 1


async def test_cleanup_failure_quarantines_desktop() -> None:
    class FailingEnd(FakeDriver):
        async def end_session(self, session: str, **kwargs: Any) -> None:
            raise RuntimeError("cleanup unknown")

    manager = DesktopSessionManager(FailingEnd())
    with pytest.raises(RuntimeError, match="cleanup unknown"):
        async with manager.open("broken") as first:
            await first.ensure_started()
    async with manager.open("next") as second:
        with pytest.raises(DesktopLeaseBusy):
            await second.ensure_started()


async def test_driver_start_failure_still_attempts_cleanup_once() -> None:
    class FailingStart(FakeDriver):
        async def start_session(self, session: str, **kwargs: Any) -> None:
            self.calls.append(DriverCall("start_session", {"session": session}))
            raise RuntimeError("driver down")

    driver = FailingStart()
    manager = DesktopSessionManager(driver)
    async with manager.open("run-4") as session:
        with pytest.raises(RuntimeError, match="driver down"):
            await session.ensure_started()
    names = call_names(driver)
    assert names.count("end_session") == 1


async def test_disabled_manager_never_calls_the_driver() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver, enabled=False)
    async with manager.open("run-5") as session:
        await session.ensure_started()
        session.require_active()
    assert driver.calls == []


async def test_manager_closes_all_open_runs_at_shutdown() -> None:
    driver = FakeDriver()
    manager = DesktopSessionManager(driver)
    context = manager.open("run-a")
    session = await context.__aenter__()
    await session.ensure_started()
    await manager.close_all()
    assert call_names(driver).count("end_session") == 1
    # Exiting a run already closed by close_all must not end it twice.
    await context.__aexit__(None, None, None)
    assert call_names(driver).count("end_session") == 1
