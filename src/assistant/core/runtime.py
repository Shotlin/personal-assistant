"""The Deep Agent's real dependencies, opened once per sani-core process.

The gateway assembles these inside its FastAPI lifespan; sani-core has the
same needs and no lifespan. This module is deliberately the single place that
wires memory + the persistent CUA connection + desktop sessions + the agent,
so the security-relevant parts (manifest-checked fail-closed transport, the
allowlist-filtered tool inventory, the per-run mutating-action budget) can
never differ between the two hosts.

Lifetime: the returned runtime holds an open AsyncExitStack for the process
lifetime. The CUA transport is *not* reopened per turn -- driver-side sessions
idle out, and a fresh transport per action is exactly what the bounded manifest
review assumed would not happen.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.agent.build import build_agent
from assistant.agent.context import RunBudget
from assistant.memory.local import open_local_memory_resources
from assistant.memory.postgres import open_memory_resources
from assistant.models import build_chat_model
from assistant.runtime.session import DesktopSessionManager, McpToolDesktopDriver
from assistant.settings import Settings
from assistant.tools.cua import open_cua_connection
from assistant.tools.policy import cua_run_scope
from assistant.tools.registry import assemble_tool_inventory

logger = logging.getLogger("assistant.core.runtime")

_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"

#: The daemon's manifest idles sessions out after 30m; one bounded read-only
#: ping every 5 minutes keeps the transport's session alive between turns.
KEEPALIVE_INTERVAL_SECONDS = 300


@contextlib.asynccontextmanager
async def _null_cua_connection() -> AsyncIterator[dict[str, Any]]:
    """Stand-in for a disabled desktop driver: an empty inventory, no transport."""
    yield {}


async def _driver_keepalive(connection: Any) -> None:
    """Periodic read-only ping. Never mutates, never breaks the runtime."""
    tools_by_name = dict(getattr(connection, "tools_by_name", {}) or {})
    ping = tools_by_name.get("get_screen_size")
    if ping is None:
        return
    while True:
        await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)
        try:
            await asyncio.wait_for(ping.ainvoke({}), timeout=30)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a failed ping is not an outage
            logger.warning("cua_keepalive_ping_failed", exc_info=True)


@dataclass
class SaniRuntime:
    """The built Deep Agent plus the handles needed to scope a run safely."""

    settings: Settings
    agent: Any
    desktop_sessions: DesktopSessionManager | None
    artifact_dir: str
    cua_enabled: bool

    @property
    def tool_names(self) -> list[str]:
        from assistant.agent.build import exposed_tool_names

        return sorted(exposed_tool_names(self.agent))

    @classmethod
    @contextlib.asynccontextmanager
    async def open(cls, settings: Settings) -> AsyncIterator[SaniRuntime]:
        stack = contextlib.AsyncExitStack()
        try:
            if settings.memory_backend == "sqlite":
                resources: Any = await stack.enter_async_context(
                    open_local_memory_resources(settings.sani_db_path)
                )
            else:
                resources = await stack.enter_async_context(
                    open_memory_resources(settings.database_url)
                )
            model = build_chat_model(settings)
            connection: Any = await stack.enter_async_context(
                open_cua_connection(settings) if settings.cua_enabled else _null_cua_connection()
            )
            extra_tools = assemble_tool_inventory(list(getattr(connection, "tools", [])))
            keepalive = asyncio.create_task(
                _driver_keepalive(connection), name="sani-core-cua-keepalive"
            )
            stack.callback(_cancel_keepalive, keepalive)
            lifecycle = dict(getattr(connection, "lifecycle_tools_by_name", {}) or {})
            desktop_sessions = DesktopSessionManager(
                McpToolDesktopDriver(lifecycle),
                enabled=settings.active_cursor_persistence_enabled,
            )
            stack.push_async_callback(desktop_sessions.close_all)
            bundle = build_agent(
                model=model,
                checkpointer=resources.saver,
                store=resources.store,
                skills_root=_SKILLS_ROOT,
                extra_tools=extra_tools,
            )
        except BaseException:
            await stack.aclose()
            raise
        yield cls(
            settings=settings,
            agent=bundle.agent,
            desktop_sessions=desktop_sessions if settings.cua_enabled else None,
            artifact_dir=str(Path(settings.cua_artifact_dir).resolve()),
            cua_enabled=settings.cua_enabled,
        )
        await stack.aclose()

    @staticmethod
    def open_with_agent(agent: Any, settings: Settings) -> SaniRuntime:
        """A runtime over an already-built agent (tests, or a host-owned build).

        Carries no exit stack, so `close` is a no-op and no transport is held.
        """
        return SaniRuntime(
            settings=settings,
            agent=agent,
            desktop_sessions=None,
            artifact_dir=str(Path(settings.cua_artifact_dir).resolve()),
            cua_enabled=False,
        )

    @contextlib.asynccontextmanager
    async def run_scope(self, session_name: str) -> AsyncIterator[RunBudget]:
        """Bind one turn's desktop handle and mutating-action budget.

        Yields the budget so the caller can report how much of it was used;
        the context tokens are reset on exit and can never leak into the
        next run.
        """
        budget = RunBudget()
        stack = contextlib.AsyncExitStack()
        try:
            run: Any = None
            if self.cua_enabled and self.desktop_sessions is not None:
                run = await stack.enter_async_context(self.desktop_sessions.open(session_name))
            await stack.enter_async_context(
                cua_run_scope(budget=budget, run=run, artifact_dir=self.artifact_dir)
            )
            yield budget
        finally:
            await stack.aclose()


def _cancel_keepalive(task: asyncio.Task[None]) -> None:
    task.cancel()
