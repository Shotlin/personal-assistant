"""Fake desktop driver for DesktopSessionManager tests (no real driver)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DriverCall:
    """One recorded trusted-controller call."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


class FakeDriver:
    """In-memory DesktopDriver double recording every call."""

    def __init__(self) -> None:
        self.calls: list[DriverCall] = []

    async def start_session(self, session: str, **kwargs: Any) -> None:
        self.calls.append(DriverCall("start_session", {"session": session, **kwargs}))

    async def end_session(self, session: str, **kwargs: Any) -> None:
        self.calls.append(DriverCall("end_session", {"session": session, **kwargs}))

    async def set_cursor_enabled(self, session: str, *, enabled: bool, **kwargs: Any) -> None:
        self.calls.append(
            DriverCall("set_agent_cursor_enabled", {"session": session, "enabled": enabled})
        )

    async def set_cursor_motion(self, session: str, **kwargs: Any) -> None:
        self.calls.append(DriverCall("set_agent_cursor_motion", {"session": session, **kwargs}))


def call_names(driver: FakeDriver) -> list[str]:
    return [call.name for call in driver.calls]


def call_args(driver: FakeDriver, name: str) -> Mapping[str, Any]:
    """Args of the first recorded call with the given tool name."""
    for call in driver.calls:
        if call.name == name:
            return call.args
    raise AssertionError(f"no recorded call named {name!r}")
