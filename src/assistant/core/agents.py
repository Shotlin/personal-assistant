"""The executor entries sani-core runs, under the two names the UI offers.

Velo owns the user's intent, task state, and execution: ordinary commands
resolve and execute locally as verified recipes, interpretation goes through
one structured JEV decision, and unfamiliar goals go to the Deep Agent's
reasoning loop -- which enters the same policy, adapter and verification path
as everything else. "Deep Agent" is that reasoning loop named plainly; it is
no longer a second routing or decision engine, and Velo is no longer merely
its label (master plan sections 3-4).

One entry set means one MCP transport, one checkpointer and one runtime: both
names draw their desktop tools and memory from the same lazily-opened
:SaniRuntime:, so the two names cannot drift apart mid-conversation.

Construction is lazy: nothing touches the network, the driver, or the model
provider until a run actually starts, and tests inject fakes for everything
heavy.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from assistant.core.desktop import system_status
from assistant.core.identity import engine_identity
from assistant.core.protocol import AGENT_PROGRESS, AGENT_TOKEN
from assistant.core.registry import AgentDescriptor, AgentRegistry
from assistant.core.runtime import SaniRuntime
from assistant.settings import Settings
from assistant.velo.controller import VeloEntry


class RuntimeProvider:
    """Opens the one process-lifetime :SaniRuntime: for both agent entries.

    Held here, not per entry, so ``velo`` and ``deep`` cannot end up with two
    transports or two checkpointers. An injected agent builder carries its own
    transport: no runtime is opened and tests never touch the driver or a
    provider.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        agent_builder: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self._settings = settings
        self._builder = agent_builder
        self._lock = asyncio.Lock()
        self._runtime: SaniRuntime | None = None
        self._cm: Any = None

    async def runtime(self) -> SaniRuntime:
        async with self._lock:
            if self._runtime is not None:
                return self._runtime
            if self._builder is not None:
                self._runtime = SaniRuntime.open_with_agent(await self._builder(), self._settings)
                return self._runtime
            self._cm = SaniRuntime.open(self._settings)
            try:
                self._runtime = await self._cm.__aenter__()
            except BaseException:
                self._cm = None
                raise
            return self._runtime

    async def aclose(self) -> None:
        """Close the held runtime once (idempotent, best-effort)."""
        cm, self._cm = self._cm, None
        self._runtime = None
        if cm is None:
            return
        with contextlib.suppress(Exception):
            await cm.__aexit__(None, None, None)


class DeepAgentEntry:
    """The LangChain Deep Agent as a sani-core agent: reasoning + memory.

    The general reasoning executor (master plan section 3): it plans and
    reasons over unfamiliar multi-step objectives and streams its answer. It
    does not route, parse imperatives, or own task state -- that is Velo's
    job -- and it dispatches desktop work through the same wrapped tools,
    policy and budget as every other route.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        agent_builder: Callable[[], Awaitable[Any]] | None = None,
        provider: RuntimeProvider | None = None,
    ) -> None:
        if provider is not None and agent_builder is not None:
            raise ValueError("give the provider the builder, not both")
        self._provider = provider or RuntimeProvider(settings, agent_builder=agent_builder)
        self._cancelled = False

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            id="deep", name="Deep Agent", capabilities=("reasoning", "memory", "skills")
        )

    async def cancel(self) -> None:
        # Cooperative only: the model call itself is stopped by the sidecar
        # task cancellation (the app already task.cancel()s the run).
        self._cancelled = True

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        self._cancelled = False
        runtime = await self._provider.runtime()
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

        async with runtime.run_scope(
            f"core-{uuid.uuid4().hex[:8]}", conversation=conversation
        ) as budget:
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
            "engine": engine_identity(),
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


class LabeledAgentEntry:
    """One executor, presented under another name.

    Kept for hosts that present one executor twice; it is no longer how Velo
    relates to the Deep Agent -- Velo is its own controller now.
    """

    def __init__(self, inner: Any, descriptor: AgentDescriptor) -> None:
        self._inner = inner
        self._descriptor = descriptor

    @property
    def descriptor(self) -> AgentDescriptor:
        return self._descriptor

    async def cancel(self) -> None:
        await self._inner.cancel()

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
    ) -> dict[str, Any]:
        return await self._inner.run(
            text, thread_id=thread_id, on_event=on_event, cancel_check=cancel_check
        )


def build_default_registry(settings: Settings) -> AgentRegistry:
    """Register the two names the UI offers, over one shared runtime.

    ``velo`` is what a persisted selection and the transcript call the
    assistant: the controller that resolves ordinary commands locally, asks
    JEV when interpretation is genuinely needed, and hands unfamiliar
    objectives to the Deep Agent. ``deep`` is the reasoning executor named
    plainly.
    """
    registry = AgentRegistry()
    provider = RuntimeProvider(settings)
    deep = DeepAgentEntry(settings, provider=provider)

    def jev_factory() -> Any:
        if not settings.velo_jev_enabled:
            return None
        from assistant.velo.jev import TypeSafeJevService

        return TypeSafeJevService.from_settings(settings)

    registry.register(deep)
    registry.register(
        VeloEntry(
            settings,
            get_runtime=provider.runtime,
            deep_entry=deep,
            jev_factory=jev_factory,
        )
    )
    return registry


def build_status_provider(
    settings: Settings,
) -> Callable[[], Awaitable[dict[str, Any]]]:
    async def status() -> dict[str, Any]:
        return await system_status(settings)

    return status


# Re-exported for the protocol contract the app loop and tests rely on.
__all__ = [
    "DeepAgentEntry",
    "LabeledAgentEntry",
    "RuntimeProvider",
    "VeloEntry",
    "build_default_registry",
    "build_status_provider",
]
