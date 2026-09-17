"""Trusted desktop session ownership (master plan WP3 / section 7).

The gateway -- never the model -- owns driver session ids, cursor motion
configuration, and the desktop mutation lease:

- :class:`DesktopSessionManager.open` is a run-scoped async context
  manager. The driver session starts lazily on the FIRST desktop action
  (``ensure_started``); a pure chat run never touches the driver.
- :meth:`DesktopSessionManager.cancel` marks a local cancellation request;
  tool wrappers check it before every new action (local stop, no model
  round trip).
- Cleanup runs on every terminal path: completion, failure, cancellation,
  timeout, and client disconnect (the context manager's ``finally``).
- :class:`McpToolDesktopDriver` adapts the persistent MCP session's raw
  lifecycle tools; the transport is NOT recreated per run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger("assistant.runtime.session")


class DesktopRunCancelled(RuntimeError):
    """A local stop was requested before this action."""


class DesktopLeaseBusy(RuntimeError):
    """Another run already holds the single desktop mutation lease."""


class DesktopDriverError(RuntimeError):
    """A trusted controller call failed at the driver."""


class DesktopDriver(Protocol):
    """What a desktop session manager needs from the driver."""

    async def start_session(self, session: str, **kwargs: Any) -> Any: ...

    async def end_session(self, session: str, **kwargs: Any) -> Any: ...

    async def set_cursor_enabled(self, session: str, *, enabled: bool, **kwargs: Any) -> Any: ...

    async def set_cursor_motion(self, session: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class DesktopSessionConfig:
    """Cursor motion configuration (master plan 7.3 initial proposal).

    ``idle_hide_ms=0`` disables idle hiding for the whole active run: a
    model wait longer than the driver's 20-second default must not make
    the cursor vanish mid-task. Glide/dwell are UX choices, not speed
    evidence.
    """

    idle_hide_ms: int = 0
    glide_duration_ms: int = 120
    dwell_after_click_ms: int = 40


class McpToolDesktopDriver:
    """Adapter mapping trusted controller calls onto raw CUA MCP tools.

    Only session-lifecycle tool names are accepted; the caller supplies
    the raw (unwrapped) tools from the persistent connection, so no new
    transport is created per run.
    """

    _TOOL_NAMES = (
        "start_session",
        "end_session",
        "set_agent_cursor_enabled",
        "set_agent_cursor_motion",
    )

    def __init__(self, tools: Mapping[str, Any]) -> None:
        self._tools = {
            name: tool for name, tool in tools.items() if name in self._TOOL_NAMES
        }

    async def _invoke(self, name: str, args: dict[str, Any], *, required: bool) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            if required:
                raise DesktopDriverError(
                    f"{name} is not exposed by the installed Cua Driver; "
                    "cursor session continuity is unavailable"
                )
            return None
        outcome = await tool.ainvoke(args)
        status = getattr(outcome, "status", "ok")
        if status == "failed":
            text = str(getattr(outcome, "text", "") or "driver call failed")
            raise DesktopDriverError(f"{name} failed: {text[:200]}")
        return outcome

    async def start_session(self, session: str, **kwargs: Any) -> Any:
        return await self._invoke("start_session", {"session": session, **kwargs}, required=True)

    async def end_session(self, session: str, **kwargs: Any) -> Any:
        try:
            return await self._invoke("end_session", {"session": session, **kwargs}, required=False)
        except Exception as exc:  # noqa: BLE001 -- cleanup is best-effort
            logger.warning(
                "desktop_session_end_failed",
                extra={"event": "desktop_session_end_failed", "detail": str(exc)[:120]},
            )
            return None

    async def set_cursor_enabled(self, session: str, *, enabled: bool, **kwargs: Any) -> Any:
        return await self._invoke(
            "set_agent_cursor_enabled",
            {"session": session, "enabled": enabled, **kwargs},
            required=False,
        )

    async def set_cursor_motion(self, session: str, **kwargs: Any) -> Any:
        return await self._invoke(
            "set_agent_cursor_motion", {"session": session, **kwargs}, required=False
        )


class DesktopRun:
    """One run's desktop session handle (lazy, cancellable, self-cleaning)."""

    def __init__(
        self,
        run_id: str,
        session_id: str,
        driver: DesktopDriver,
        config: DesktopSessionConfig,
        *,
        enabled: bool = True,
        acquire_lease: Callable[[], None] = lambda: None,
    ) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self._driver = driver
        self._config = config
        self._enabled = enabled
        self.cancelled = False
        self._started = False
        self._closed = False
        self._start_attempted = False
        self._acquire_lease = acquire_lease
        self._activation_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()

    async def ensure_started(self) -> None:
        """Start once; uncertain startup must not be retried implicitly."""
        async with self._activation_lock:
            self.require_active()
            self._acquire_lease()
            if self._started or not self._enabled:
                return
            if self._start_attempted:
                raise DesktopDriverError("session startup outcome unknown; refusing retry")
            self._start_attempted = True
            await self._driver.start_session(self.session_id)
            self.require_active()
            await self._driver.set_cursor_motion(
                self.session_id,
                idle_hide_ms=self._config.idle_hide_ms,
                glide_duration_ms=self._config.glide_duration_ms,
                dwell_after_click_ms=self._config.dwell_after_click_ms,
            )
            self.require_active()
            await self._driver.set_cursor_enabled(self.session_id, enabled=True)
            self.require_active()
            self._started = True

    @asynccontextmanager
    async def action(self) -> AsyncIterator[None]:
        """Serialize native calls; recheck Stop after every awaited gate."""
        async with self._action_lock:
            self.require_active()
            await self.ensure_started()
            self.require_active()
            yield

    def require_active(self) -> None:
        """Raise when this run must not start any further action."""
        if self.cancelled:
            raise DesktopRunCancelled("desktop run was cancelled")
        if self._closed:
            raise DesktopRunCancelled("desktop run already closed")

    def cancel(self) -> None:
        """Mark a local stop; checked before every new action."""
        self.cancelled = True

    async def aclose(self) -> None:
        """End the driver session exactly once (idempotent, best-effort)."""
        if self._closed:
            return
        self._closed = True
        async with self._action_lock:
            async with self._activation_lock:
                if self._start_attempted and self._enabled:
                    async with asyncio.timeout(5):
                        await self._driver.end_session(self.session_id)


class DesktopSessionManager:
    """Owns run-scoped driver sessions and the single desktop lease."""

    def __init__(
        self,
        driver: DesktopDriver,
        *,
        config: DesktopSessionConfig | None = None,
        enabled: bool = True,
    ) -> None:
        self._driver = driver
        self._config = config or DesktopSessionConfig()
        self.enabled = enabled
        self._runs: dict[str, DesktopRun] = {}
        self._owner: str | None = None

    @asynccontextmanager
    async def open(self, run_id: str) -> AsyncIterator[DesktopRun]:
        """Open one run-scoped desktop session handle.

        Activation is lazy: nothing touches the driver until the first
        desktop action asks ``ensure_started``. Cleanup is unconditional.
        """
        if run_id in self._runs:
            raise DesktopLeaseBusy("run handle already registered")

        def acquire() -> None:
            if self._owner is not None and self._owner != run_id:
                raise DesktopLeaseBusy("desktop busy or cleanup uncertain")
            self._owner = run_id

        run = DesktopRun(
            run_id,
            session_id=f"assistant-{run_id}",
            driver=self._driver,
            config=self._config,
            enabled=self.enabled,
            acquire_lease=acquire,
        )
        self._runs[run_id] = run
        try:
            yield run
        finally:
            await run.aclose()
            self._runs.pop(run_id, None)
            if self._owner == run_id:
                self._owner = None

    def cancel(self, run_id: str) -> bool:
        """Mark a run cancelled locally; False when it is not active."""
        run = self._runs.get(run_id)
        if run is None:
            return False
        run.cancel()
        return True

    async def close_all(self) -> None:
        """Shut down every open run (process shutdown / restart recovery)."""
        runs = list(self._runs.values())
        self._runs.clear()
        self._owner = None
        for run in runs:
            await run.aclose()
