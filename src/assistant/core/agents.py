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
from typing import Any

from assistant.agent.context import RunBudget
from assistant.core.desktop import system_status
from assistant.core.protocol import AGENT_PROGRESS, AGENT_TOKEN
from assistant.core.registry import AgentDescriptor, AgentRegistry
from assistant.core.runtime import SaniRuntime
from assistant.runtime.session import DesktopSessionManager, McpToolDesktopDriver
from assistant.settings import Settings
from assistant.velo.agent import VeloAgent
from assistant.velo.cua_adapter import VeloCuaAdapter, allowed_apps_from_manifest
from assistant.velo.jev import JevDecisionEngine, text_candidates_from_objective
from assistant.velo.types import VeloLimits, VeloObjective, VeloResult, VeloStatus


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
        thread_id: str,
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
                    on_progress=lambda line: events.put_nowait(
                        (AGENT_PROGRESS, {"message": line})
                    ),
                )
        finally:
            self._active_cancel = None
            events.put_nowait(None)
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
        return {
            "status": result.status.value,
            "reason": result.reason,
            "response": velo_response(result),
            "metrics": result.metrics.as_dict(),
        }


#: What the user sees when Velo's structured outcome reaches the transcript.
#: Derived from the terminal status only -- never a guess about the desktop.
_VELO_FAILURE_LABELS: dict[VeloStatus, str] = {
    VeloStatus.ASK_USER: "I need you to decide:",
    VeloStatus.STOPPED: "Stopped.",
    VeloStatus.FAILED: "I couldn't finish that.",
}


def velo_response(result: VeloResult) -> str:
    """A one-line, honest answer for a completed Velo run.

    Velo reports status/reason/metrics, not prose, so the transcript would
    otherwise have nothing to attribute to it.
    """
    reason = result.reason.strip()
    if result.status is VeloStatus.DONE:
        return reason or "Done."
    label = _VELO_FAILURE_LABELS.get(result.status, "Stopped.")
    return f"{label} {reason}".strip()


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
        self._build_lock = asyncio.Lock()
        self._cancelled = False
        # The runtime owns the memory + CUA + agent handles; it is opened once
        # and held for the process lifetime, and the entry holds the only
        # reference so nothing is garbage-collected mid-run.
        self._runtime: SaniRuntime | None = None
        self._runtime_cm: Any = None

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            id="deep", name="Deep Agent", capabilities=("reasoning", "memory", "skills")
        )

    async def cancel(self) -> None:
        # Cooperative only: the model call itself is stopped by the sidecar
        # task cancellation (the app already task.cancel()s the run).
        self._cancelled = True

    async def _ensure_runtime(self) -> SaniRuntime:
        async with self._build_lock:
            if self._runtime is not None:
                return self._runtime
            if self._builder is not None:
                # An injected agent carries its own transport; no runtime is
                # opened, so tests never touch the driver or a provider.
                self._runtime = SaniRuntime.open_with_agent(await self._builder(), self._settings)
                return self._runtime
            self._runtime_cm = SaniRuntime.open(self._settings)
            try:
                self._runtime = await self._runtime_cm.__aenter__()
            except BaseException:
                self._runtime_cm = None
                raise
            return self._runtime

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        self._cancelled = False
        runtime = await self._ensure_runtime()
        from langchain_core.messages import AIMessage, HumanMessage

        from assistant.agent.context import AgentContext
        from assistant.memory.namespaces import thread_id_for_sani

        # The host's conversation id IS the agent thread, so turn two of a chat
        # resumes the memory of turn one. Without one, this stays a one-shot
        # thread rather than silently borrowing another conversation's memory.
        conversation = thread_id.strip() or f"core-{uuid.uuid4().hex[:8]}"
        thread = thread_id_for_sani(conversation)

        # The answer is whatever the model actually streamed, minus any message
        # that turned out to be a tool-call preamble. Tracking it here (rather
        # than re-reading the final graph state) guarantees the persisted text
        # is exactly the text the user watched arrive.
        partials: dict[str, list[str]] = {}
        order: list[str] = []
        tool_backed: set[str] = set()
        announced_tools: set[str] = set()

        async with runtime.run_scope(f"core-{uuid.uuid4().hex[:8]}") as budget:
            async for event in runtime.agent.astream(
                {"messages": [HumanMessage(text)]},
                {"configurable": {"thread_id": thread}},
                context=AgentContext(user_id="sani-local", chat_id=thread),
                stream_mode="messages",
            ):
                if cancel_check() or self._cancelled:
                    raise asyncio.CancelledError
                message = event[0] if isinstance(event, tuple) and event else event
                # isinstance, not `message.type == "ai"`: a streamed
                # AIMessageChunk reports its type as "AIMessageChunk", so the
                # attribute test that works on finished messages silently
                # matches nothing on the stream.
                if not isinstance(message, AIMessage):
                    continue
                message_id = str(getattr(message, "id", "") or "")
                for call in getattr(message, "tool_call_chunks", None) or ():
                    name = str(call.get("name") or "")
                    if not name:
                        continue
                    tool_backed.add(message_id)
                    if name not in announced_tools:
                        announced_tools.add(name)
                        await on_event(AGENT_PROGRESS, {"message": f"Using {name}"})
                delta = _message_text(message)
                if not delta:
                    continue
                if message_id not in partials:
                    partials[message_id] = []
                    order.append(message_id)
                partials[message_id].append(delta)
                await on_event(AGENT_TOKEN, {"text": delta})

        response = ""
        for message_id in reversed(order):
            if message_id in tool_backed:
                continue
            response = "".join(partials[message_id])
            break
        return {
            "status": "done",
            "thread_id": thread,
            "response": response,
            "cua_actions_used": budget.used,
        }


def _message_text(message: Any) -> str:
    """Plain text of a message or stream chunk, tolerating content blocks."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return "" if content is None else str(content)


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
