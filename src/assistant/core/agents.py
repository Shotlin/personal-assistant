"""Registry entries wiring Velo and the Deep Agent into sani-core (Stage G).

Each entry adapts an existing, independently tested implementation to the
sidecar's small agent contract (identity, run, cancel) -- no rewrites, no
agent-to-agent wiring. Both are fail-closed: a missing credential or an
unavailable CUA runtime surfaces as a run error for the desktop UI, never
as a fallback to another model (Sani master doc sections 5, 10-12).

Default construction is lazy: nothing touches the network, the driver, or
the model provider until a run actually starts, and tests inject fakes for
everything heavy.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from assistant.agent.context import RunBudget
from assistant.core.desktop import system_status
from assistant.core.registry import AgentDescriptor, AgentRegistry
from assistant.runtime.session import DesktopSessionManager, McpToolDesktopDriver
from assistant.settings import Settings
from assistant.velo.agent import VeloAgent
from assistant.velo.cua_adapter import VeloCuaAdapter, allowed_apps_from_manifest
from assistant.velo.jev import JevDecisionEngine, text_candidates_from_objective
from assistant.velo.types import VeloLimits, VeloObjective

_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def _default_velo_context(
    settings: Settings,
) -> contextlib.AbstractAsyncContextManager[tuple[VeloAgent, Callable[[], None]]]:
    """Open the persistent CUA connection and build the real Velo run stack.

    The transport lives for exactly one run -- same lifetime as
    scripts/run_velo.py; no new transport per action inside the run.
    """

    @contextlib.asynccontextmanager
    async def context() -> AsyncIterator[tuple[VeloAgent, Callable[[], None]]]:
        from assistant.tools.cua import open_cua_connection

        async with open_cua_connection(settings) as connection:
            manager = DesktopSessionManager(
                McpToolDesktopDriver(connection.lifecycle_tools_by_name),
                enabled=settings.active_cursor_persistence_enabled,
            )
            async with manager.open(f"core-{uuid.uuid4().hex[:8]}") as desktop_run:
                budget = RunBudget(max_actions=settings.velo_max_steps)
                adapter = VeloCuaAdapter(
                    connection,
                    desktop_run,
                    budget,
                    allowed_apps=allowed_apps_from_manifest(settings.cua_capability_manifest_path),
                )
                jev = JevDecisionEngine.from_settings(settings)
                limits = VeloLimits(
                    max_steps=settings.velo_max_steps,
                    max_runtime_seconds=float(settings.velo_max_runtime_seconds),
                    max_same_action_repeats=settings.velo_max_same_action_repeats,
                    max_consecutive_failed_actions=settings.velo_max_consecutive_failed_actions,
                    recent_history_steps=settings.velo_recent_history_steps,
                )
                agent = VeloAgent(adapter, jev, limits)
                yield agent, desktop_run.cancel

    return context()


class VeloAgentEntry:
    """Velo (JEV + CUA) as a sani-core agent: quick computer control."""

    def __init__(
        self,
        settings: Settings,
        *,
        context_factory: Callable[
            [Settings], contextlib.AbstractAsyncContextManager[tuple[VeloAgent, Callable[[], None]]]
        ]
        | None = None,
    ) -> None:
        self._settings = settings
        self._context_factory = context_factory or _default_velo_context
        self._cancel_requested = False
        self._active_cancel: Callable[[], None] | None = None

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(id="velo", name="Velo", capabilities=("computer-control", "desktop"))

    async def cancel(self) -> None:
        self._cancel_requested = True
        if self._active_cancel is not None:
            self._active_cancel()

    async def run(
        self,
        text: str,
        *,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        self._cancel_requested = False
        events: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        async def pump() -> None:
            while True:
                item = await events.get()
                if item is None:
                    return
                await on_event(item[0], item[1])

        pump_task = asyncio.create_task(pump(), name="velo-entry-events")
        try:
            async with self._context_factory(self._settings) as (agent, cancel_hook):
                self._active_cancel = cancel_hook
                result = await agent.run(
                    VeloObjective(text=text, text_candidates=text_candidates_from_objective(text)),
                    cancel_check=lambda: cancel_check() or self._cancel_requested,
                    on_progress=lambda line: events.put_nowait(("progress", {"line": line})),
                )
        finally:
            self._active_cancel = None
            events.put_nowait(None)
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
        return {
            "status": result.status.value,
            "reason": result.reason,
            "metrics": result.metrics.as_dict(),
        }


class DeepAgentEntry:
    """The LangChain Deep Agent as a sani-core agent: reasoning + memory."""

    def __init__(
        self,
        settings: Settings,
        *,
        agent_builder: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self._settings = settings
        self._builder = agent_builder
        self._agent: Any = None
        self._build_lock = asyncio.Lock()
        self._cancelled = False
        # Owns the memory-resources stack after the first default build;
        # held open for the process lifetime (see _build_default).
        self._stack: contextlib.AsyncExitStack | None = None

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            id="deep", name="Deep Agent", capabilities=("reasoning", "memory", "skills")
        )

    async def cancel(self) -> None:
        # Cooperative only: the model call itself is stopped by the sidecar
        # task cancellation (the app already task.cancel()s the run).
        self._cancelled = True

    async def _ensure_agent(self) -> Any:
        async with self._build_lock:
            if self._agent is not None:
                return self._agent
            if self._builder is not None:
                self._agent = await self._builder()
                return self._agent
            self._agent = await self._build_default()
            return self._agent

    async def _build_default(self) -> Any:
        from assistant.agent.build import build_agent
        from assistant.memory.local import open_local_memory_resources
        from assistant.memory.postgres import open_memory_resources
        from assistant.models import build_chat_model

        # The memory-resources stack is intentionally kept open for the
        # process lifetime: sani-core owns it, and the registry entry holds
        # the only reference so it is never garbage-collected mid-run.
        stack = contextlib.AsyncExitStack()
        try:
            if self._settings.memory_backend == "sqlite":
                resources: Any = await stack.enter_async_context(
                    open_local_memory_resources(self._settings.sani_db_path)
                )
            else:
                resources = await stack.enter_async_context(
                    open_memory_resources(self._settings.database_url)
                )
            model = build_chat_model(self._settings)
            bundle = build_agent(
                model=model,
                checkpointer=resources.saver,
                store=resources.store,
                skills_root=_SKILLS_ROOT,
            )
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        return bundle.agent

    async def run(
        self,
        text: str,
        *,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        self._cancelled = False
        await on_event("started", {"agent": "deep"})
        agent = await self._ensure_agent()
        from langchain_core.messages import HumanMessage

        from assistant.agent.context import AgentContext
        from assistant.memory.namespaces import thread_id_for_sani

        thread_id = thread_id_for_sani(f"core-{uuid.uuid4().hex[:8]}")
        result = await agent.ainvoke(
            {"messages": [HumanMessage(text)]},
            {"configurable": {"thread_id": thread_id}},
            context=AgentContext(user_id="sani-local", chat_id=thread_id),
        )
        response = ""
        for message in reversed(result["messages"]):
            if getattr(message, "type", "") == "ai" and not getattr(message, "tool_calls", None):
                text_value = getattr(message, "text", None)
                response = str(text_value() if callable(text_value) else message.content or "")
                break
        return {"status": "done", "thread_id": thread_id, "response": response}


def build_default_registry(settings: Settings) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(VeloAgentEntry(settings))
    registry.register(DeepAgentEntry(settings))
    return registry


def build_status_provider(
    settings: Settings,
) -> Callable[[], Awaitable[dict[str, Any]]]:
    async def status() -> dict[str, Any]:
        return await system_status(settings)

    return status
