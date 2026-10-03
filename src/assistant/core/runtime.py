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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assistant.agent.build import build_agent
from assistant.agent.context import RunBudget
from assistant.agent.system_prompt import SYSTEM_PROMPT
from assistant.memory.local import open_local_memory_resources
from assistant.memory.postgres import open_memory_resources
from assistant.models import build_chat_model
from assistant.runtime.cua_faults import classify_exception
from assistant.runtime.session import DesktopSessionManager, McpToolDesktopDriver
from assistant.settings import Settings
from assistant.tools.cua import open_cua_connection
from assistant.tools.policy import cua_run_scope
from assistant.tools.registry import assemble_tool_inventory

logger = logging.getLogger("assistant.core.runtime")

_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"

#: How often to touch the driver between turns. This is transport warmth only:
#: the driver's capability policy no longer idles a standard-mode session out,
#: and a dropped lease now rebuilds itself on the next call rather than waiting
#: here to notice.
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
        except Exception as exc:  # noqa: BLE001 -- a failed ping is not an outage
            # Named, because "keepalive failed" and "macOS revoked the grant" look
            # identical in a log line and are not the same event at all.
            logger.warning(
                "cua_keepalive_ping_failed: %s",
                classify_exception(exc).value,
                exc_info=True,
            )


@dataclass
class SaniRuntime:
    """The built Deep Agent plus the handles needed to scope a run safely."""

    settings: Settings
    agent: Any
    desktop_sessions: DesktopSessionManager | None
    artifact_dir: str
    cua_enabled: bool
    #: The wrapped computer-control tools by name. The deterministic fast path
    #: drives these same objects, so the gate, targeting and budget it passes
    #: through are exactly the agent's own.
    cua_tools: dict[str, Any] = field(default_factory=dict)
    #: True when the `claude_code` tool is bound; the Deep entry then allows the
    #: longer run a coding task needs.
    claude_code_enabled: bool = False

    @property
    def tool_names(self) -> list[str]:
        from assistant.agent.build import exposed_tool_names

        return sorted(exposed_tool_names(self.agent))

    @classmethod
    @contextlib.asynccontextmanager
    async def open(
        cls, settings: Settings, *, claude_code: Any | None = None
    ) -> AsyncIterator[SaniRuntime]:
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
            # D11: every provider request is admitted at the transport
            # boundary BEFORE dispatch (graph sub-calls, retries, streams).
            from assistant.models.admission import (
                get_admission_controller,
                wrap_chat_model,
            )

            model = wrap_chat_model(build_chat_model(settings),
                                    get_admission_controller())
            connection: Any = await stack.enter_async_context(
                open_cua_connection(settings) if settings.cua_enabled else _null_cua_connection()
            )
            extra_tools = assemble_tool_inventory(list(getattr(connection, "tools", [])))
            if getattr(settings, "jarvis_missions_enabled", False):
                # R02: the trusted structured submission tools (plan/
                # recovery/review) are bound into the graph when missions
                # are enabled; role gating happens at invocation time.
                from assistant.missions.submission import build_submission_tools

                extra_tools = [*extra_tools, *build_submission_tools()]
            build_kwargs: dict[str, Any] = {}
            if claude_code is not None and settings.claude_code_enabled:
                from assistant.claude_code.toolkit import GUIDE

                extra_tools = [*extra_tools, claude_code.as_tool()]
                build_kwargs["system_prompt"] = SYSTEM_PROMPT + GUIDE
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
                **build_kwargs,
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
            cua_tools={
                tool.name: tool for tool in extra_tools if tool.name != "claude_code"
            },
            claude_code_enabled=bool(build_kwargs),
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
    async def run_scope(
        self,
        session_name: str,
        conversation: str = "",
        *,
        ledger: Any | None = None,
        mission_guard: Any | None = None,
        mission_strict_audit: bool = False,
        mission_withhold_screenshots: bool = False,
        max_actions: int | None = None,
    ) -> AsyncIterator[RunBudget]:
        """Bind one turn's desktop handle and mutating-action budget.

        Yields the budget so the caller can report how much of it was used;
        the context tokens are reset on exit and can never leak into the
        next run. ``conversation`` is what desktop continuity is keyed on.

        R05: mission dispatch passes the per-action ``ledger``, the
        authority ``mission_guard`` and the strict-audit/withhold flags
        through, so the REAL desktop session and the mission guards share
        one scope (never ``run=None`` for real dispatch).

        C02/N03: with missions enabled, a scope that carries NO explicit
        mission guard still cannot mutate the desktop. The default guard
        refuses every mutating call at the wrapped-tool boundary — the
        selectable Deep entry (chat, planning, questions) has no mission
        permit to spend, so an action-shaped prompt through that entry can
        produce zero effects. Reads stay available for answers.
        """
        if mission_guard is None and getattr(
            self.settings, "jarvis_missions_enabled", False
        ):
            from assistant.tools.policy import MUTATING_TOOL_NAMES

            async def _no_mission_mutation(
                tool: str, kwargs: dict[str, Any]
            ) -> str | None:
                if tool in MUTATING_TOOL_NAMES:
                    return (
                        f"Refused: APPROVAL_REQUIRED: {tool} would mutate the "
                        "desktop outside any mission. Explicit Deep selection "
                        "obeys mission authority for actions; submit the action "
                        "as a mission (the Velo entry) so it is claimed, scoped, "
                        "budgeted and verified."
                    )
                return None

            mission_guard = _no_mission_mutation
        # R04/R05: a mission unit clamps its own mutating-action ceiling
        # (file 03 §8: at most 12 per unit, zero stays zero).
        budget = RunBudget(max_actions=max_actions) if max_actions is not None else RunBudget()
        stack = contextlib.AsyncExitStack()
        try:
            run: Any = None
            if self.cua_enabled and self.desktop_sessions is not None:
                # The driver session belongs to the conversation, not the message:
                # a multi-turn task keeps the same session and the same cursor.
                session_id = f"assistant-{conversation}" if conversation else None
                run = await stack.enter_async_context(
                    self.desktop_sessions.open(session_name, session_id=session_id)
                )
            await stack.enter_async_context(
                cua_run_scope(
                    budget=budget,
                    run=run,
                    artifact_dir=self.artifact_dir,
                    ledger=ledger,
                    conversation=conversation,
                    mission_guard=mission_guard,
                    mission_strict_audit=mission_strict_audit,
                    mission_withhold_screenshots=mission_withhold_screenshots,
                )
            )
            yield budget
        finally:
            await stack.aclose()


def _cancel_keepalive(task: asyncio.Task[None]) -> None:
    task.cancel()
