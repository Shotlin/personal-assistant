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
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

from assistant.claude_code import context as claude_context
from assistant.core.desktop import system_status
from assistant.core.identity import engine_identity
from assistant.core.protocol import AGENT_PROGRESS, AGENT_TOKEN
from assistant.core.registry import AgentDescriptor, AgentRegistry
from assistant.core.runtime import SaniRuntime
from assistant.missions.contracts import CancellationToken, RequestEnvelope, new_id
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
        claude_code: Any | None = None,
    ) -> None:
        self._settings = settings
        self._builder = agent_builder
        self._claude_code = claude_code
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
            self._cm = (
                SaniRuntime.open(self._settings, claude_code=self._claude_code)
                if self._claude_code is not None
                else SaniRuntime.open(self._settings)
            )
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


class _LoopGuard:
    """Bounds the Deep reasoning loop: steps, wall-clock, and repeated narration.

    Live 2026-10-01: a free-tier model repeated "let me find the second video"
    for minutes with no mutating action, so the mutating-action budget never
    tripped. Observation-only loops are bounded here, and a trip ends the run
    fail-closed with an honest message rather than spinning.
    """

    REPEAT_LIMIT = 3

    def __init__(self, *, max_steps: int, deadline_seconds: float) -> None:
        self._max_steps = max_steps
        self._deadline = time.monotonic() + deadline_seconds
        self._steps = 0
        self._texts: dict[str, int] = {}

    def tool_call(self) -> str | None:
        self._steps += 1
        if self._steps > self._max_steps:
            return f"it used {self._max_steps} tool steps without finishing"
        return self.check_time()

    def check_time(self) -> str | None:
        if time.monotonic() > self._deadline:
            return "it ran past its time limit"
        return None

    def message(self, text: str) -> str | None:
        key = " ".join(text.lower().split())[:160]
        if len(key) < 20:
            return None
        self._texts[key] = self._texts.get(key, 0) + 1
        if self._texts[key] >= self.REPEAT_LIMIT:
            return "it kept repeating the same step"
        return None


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
        self._settings = settings
        self._provider = provider or RuntimeProvider(settings, agent_builder=agent_builder)
        # A07 fix: cancellation is per run, not a shared boolean. Concurrent
        # runs over the same entry cancel independently; cancelling run A
        # can no longer poison run B.
        self._run_tokens: dict[str, CancellationToken] = {}

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            id="deep", name="Deep Agent", capabilities=("reasoning", "memory", "skills")
        )

    async def cancel(self, run_id: str | None = None) -> None:
        # Cooperative only: the model call itself is stopped by the sidecar
        # task cancellation (the app already task.cancel()s the run).
        if run_id is None:
            for active in list(self._run_tokens.values()):
                active.cancel()
            return
        token: CancellationToken | None = self._run_tokens.get(run_id)
        if token is not None:
            token.cancel()

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
        run_id: str = "",
        usage_recorder: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        token = CancellationToken()
        if run_id:
            self._run_tokens[run_id] = token
        try:
            return await self._run(
                text,
                thread_id=thread_id,
                on_event=on_event,
                cancel_check=cancel_check,
                token=token,
                usage_recorder=usage_recorder,
            )
        finally:
            if run_id:
                self._run_tokens.pop(run_id, None)

    async def _run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
        token: CancellationToken,
        usage_recorder: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Bind the run's event channel and conversation for the Claude Code tool.

        A tool call happens deep inside the graph, so it reads these from context
        variables rather than being handed them. They are reset when the run ends.
        """
        sink_token = claude_context.event_sink.set(on_event)
        conversation_token = claude_context.conversation_id.set(thread_id.strip())
        try:
            return await self._run_inner(
                text,
                thread_id=thread_id,
                on_event=on_event,
                cancel_check=cancel_check,
                token=token,
                usage_recorder=usage_recorder,
            )
        finally:
            claude_context.event_sink.reset(sink_token)
            claude_context.conversation_id.reset(conversation_token)

    async def _run_inner(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
        token: CancellationToken,
        usage_recorder: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        runtime = await self._provider.runtime()
        from langchain_core.messages import AIMessage, HumanMessage

        from assistant.agent.context import AgentContext
        from assistant.memory.namespaces import thread_id_for_sani
        from assistant.observability.usage import LedgerCallbackHandler, UsageLedger

        # A12: usage is metered at the provider boundary for every core run,
        # not only in legacy routes. Unknown usage stays unknown (never zero).
        ledger = UsageLedger()
        usage_handler = LedgerCallbackHandler(
            ledger, prefix=f"core-{uuid.uuid4().hex[:8]}", on_call=usage_recorder
        )

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
        # A coding run is minutes of real work, not a stuck reasoning loop, so
        # the deadline stretches when (and only when) the Claude Code tool is
        # bound. It stays under the 13 minute core ceiling.
        deadline = getattr(self._settings, "deep_run_deadline_seconds", 180)
        if getattr(runtime, "claude_code_enabled", False):
            deadline = max(deadline, int(self._settings.claude_code_run_seconds) + 60)
        guard = _LoopGuard(
            max_steps=getattr(self._settings, "deep_max_tool_steps", 40),
            deadline_seconds=deadline,
        )
        stopped_reason: str | None = None
        counted_calls: set[tuple[str, int]] = set()

        hard_limit = deadline + 30
        async with runtime.run_scope(
            f"core-{uuid.uuid4().hex[:8]}", conversation=conversation
        ) as budget:
            try:
                # A single hung model request produces no tokens, so the loop guard
                # alone never fires; the hard timeout ends the run regardless.
                async with asyncio.timeout(hard_limit):
                    async for event in runtime.agent.astream(
                        {"messages": [HumanMessage(text)]},
                        {"configurable": {"thread_id": thread}, "callbacks": [usage_handler]},
                        context=AgentContext(user_id="sani-local", chat_id=thread),
                        stream_mode="messages",
                    ):
                        if cancel_check() or token.is_cancelled:
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
                            call_key = (message_id, int(call.get("index") or 0))
                            if call_key not in counted_calls:
                                counted_calls.add(call_key)
                                stopped_reason = stopped_reason or guard.tool_call()
                            if name not in announced_tools:
                                announced_tools.add(name)
                                await on_event(AGENT_PROGRESS, {"message": f"Using {name}"})
                        if stopped_reason is not None:
                            break
                        delta = _message_text(message)
                        if not delta:
                            continue
                        if message_id not in partials:
                            if order:
                                stopped_reason = stopped_reason or guard.message(
                                    "".join(partials[order[-1]])
                                )
                            partials[message_id] = []
                            order.append(message_id)
                        partials[message_id].append(delta)
                        await on_event(AGENT_TOKEN, {"text": delta})
                        if stopped_reason is None:
                            stopped_reason = guard.check_time()
                        if stopped_reason is not None:
                            break
            except TimeoutError:
                stopped_reason = "it ran past its time limit"

        response = ""
        for message_id in reversed(order):
            if message_id in tool_backed:
                continue
            response = "".join(partials[message_id])
            break
        if stopped_reason is not None:
            # Fail closed: never present a loop that was cut off as a finish.
            response = (
                f"I stopped because {stopped_reason}, and I could not confirm the goal was "
                "reached. Tell me what you see on screen, or give a more specific "
                "instruction (for example the exact video title) and I will retry."
            )
        usage_snapshot = ledger.snapshot()
        # Decimal cost is str()'d for the JSON frame; unknown stays None.
        if usage_snapshot.get("cost_usd") is not None:
            usage_snapshot["cost_usd"] = str(usage_snapshot["cost_usd"])
        return {
            "status": "blocked" if stopped_reason is not None else "done",
            "thread_id": thread,
            "response": response,
            "cua_actions_used": budget.used,
            "usage": usage_snapshot,
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
    """Register the two names the UI offers over one shared runtime (R02).

    ``velo`` is what a persisted selection and the transcript call the
    assistant: the controller that resolves ordinary commands locally, asks
    JEV when interpretation is genuinely needed, and hands unfamiliar
    objectives to the Deep Agent. ``deep`` is the reasoning executor named
    plainly. With missions enabled, ``velo`` becomes the mission-backed
    entry over the same shared machinery.
    """
    return build_core_resources(settings).registry


@dataclass
class CoreResources:
    """ONE resource graph for the sani-core process (F08/R02).

    The runtime provider, Deep entry, mission service (store, authority,
    evidence, executor, controller) and the mission-backed registry entry
    are constructed once and shared by the run path AND the mission IPC
    surface. Approvals, budgets and run tokens therefore survive across
    calls; the host closes the graph once at shutdown.
    """

    settings: Settings
    provider: RuntimeProvider
    deep: DeepAgentEntry
    registry: AgentRegistry
    mission_entry: MissionEntry | None = None
    #: Always built (cheap): the UI asks whether Claude Code is installed and
    #: signed in even while the feature is off. The tool is bound only when enabled.
    claude_code: Any = None
    _service_lock: asyncio.Lock = dataclass_field(default_factory=asyncio.Lock)
    _service: Any = None
    _store: Any = None

    async def mission_service(self) -> Any:
        """The single MissionService instance (initialized once, safely)."""
        if self.mission_entry is None:
            raise RuntimeError("missions are not enabled in these resources")
        async with self._service_lock:
            if self._service is None:
                self._service = await self.mission_entry.create_service()
            return self._service

    async def aclose(self) -> None:
        """Close the shared store once (idempotent, best-effort)."""
        if self._store is not None:
            with contextlib.suppress(Exception):
                await self._store.close()
            self._store = None


def build_core_resources(settings: Settings) -> CoreResources:
    """Compose the process-wide resource graph (registry + mission IPC)."""
    from assistant.claude_code.toolkit import ClaudeCodeToolkit

    registry = AgentRegistry()
    claude_code = ClaudeCodeToolkit(settings)
    provider = RuntimeProvider(settings, claude_code=claude_code)
    deep = DeepAgentEntry(settings, provider=provider)

    def jev_factory() -> Any:
        if not settings.velo_jev_enabled:
            return None
        from assistant.velo.jev import TypeSafeJevService

        return TypeSafeJevService.from_settings(settings)

    resources = CoreResources(
        settings=settings,
        provider=provider,
        deep=deep,
        registry=registry,
        claude_code=claude_code,
    )

    registry.register(deep)
    if getattr(settings, "jarvis_missions_enabled", False):
        resources.mission_entry = MissionEntry(
            settings, resources=resources, deep=deep, jev_factory=jev_factory
        )
        registry.register(resources.mission_entry)
    else:
        registry.register(
            VeloEntry(
                settings,
                get_runtime=provider.runtime,
                deep_entry=deep,
                jev_factory=jev_factory,
            )
        )
    return resources


def build_mission_provider(resources: CoreResources) -> Callable[[], Awaitable[Any]] | None:
    """The mission.* IPC surface, wired only when missions are enabled."""
    if resources.mission_entry is None:
        return None
    return resources.mission_service


class MissionEntry:
    """The ``velo`` slot with durable mission ownership (default-off).

    Questions are answered as cheap chat WITHOUT desktop acquisition (the
    INFO role refuses desktop tools at the wrapped-tool boundary);
    everything else becomes a mission: idempotent request claim,
    deterministic fast plan for exact commands, role-scoped Deep planning
    otherwise, bounded execution, and a deterministic acceptance gate.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        resources: CoreResources,
        deep: DeepAgentEntry,
        jev_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._resources = resources
        self._deep = deep
        self._jev_factory = jev_factory
        self._run_tokens: dict[str, CancellationToken] = {}

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            id="velo",
            name="Velo",
            capabilities=("computer-control", "desktop", "reasoning", "memory", "missions"),
        )

    async def create_service(self) -> Any:
        """Build the ONE MissionService over the shared resources (F08)."""
        from assistant.missions.authority import MissionAuthority
        from assistant.missions.controller import DeepController
        from assistant.missions.evidence import EvidenceStore
        from assistant.missions.executor import VeloExecutor
        from assistant.missions.service import MissionService
        from assistant.missions.store import MissionStore

        settings = self._settings
        store = await MissionStore.connect(settings.sani_db_path)
        await store.setup()
        # D11: the transport's admission boundary now has durable accounting.
        from assistant.models.admission import get_admission_controller

        get_admission_controller().bind_store(store)
        # Startup recovery: uncertain attempts from a previous process are
        # marked for reconciliation before any new action (F07/R07); the
        # returned execution ids are CONSUMED through the real reconciler
        # below (C03/N05 — they are never ignored).
        uncertain_ids = await store.recover_inflight("startup")
        self._resources._store = store
        authority = MissionAuthority(store)
        evidence = EvidenceStore(Path(settings.sani_data_dir) / "mission-evidence", store)
        # The probe closes over a slot filled once the service exists: the
        # executor reads CURRENT queue ownership at dispatch (C06/N09).
        service_probe_holder: dict[str, Any] = {}

        def service_probe(mission_id: str) -> Any:
            probe = service_probe_holder.get("probe")
            return probe(mission_id) if probe is not None else None

        executor = VeloExecutor(
            settings,
            get_runtime=self._resources.provider.runtime,
            authority=authority,
            evidence=evidence,
            jev_factory=self._jev_factory,
            # C02/N04 (NP01): the production executor receives its REAL
            # dependencies. Without the store, no action ledger can be bound
            # and strict mode refuses every mutation; without the payload
            # resolver, dictated text resolves to nothing.
            store=store,
            # C06/N09: the executor reads CURRENT queue ownership at every
            # dispatch boundary, not a fence string copied at claim time.
            ownership_probe=service_probe,
        )
        controller = DeepController(self._deep)
        service = MissionService(
            settings,
            store=store,
            authority=authority,
            evidence=evidence,
            executor=executor,
            controller=controller,
            transport_factory=self._transport_for_mission,
        )
        # The resolver and ownership probe need the finished service (they
        # read through the shared store and desktop queue); bind after
        # construction — no second service, provider, or graph is created.
        executor._payload_resolver = service._resolve_payloads
        service_probe_holder["probe"] = service._ownership_probe
        await service.reconcile_startup(uncertain_ids)
        return service

    def _transport_for_mission(self, mission: Any) -> Any:
        """A detached mission-bound Deep transport for RECOVER/REVIEW/revision.

        Used when a control path (resume/recovery/revision) needs the
        Controller outside a live run: same graph, role-scoped, thread
        namespaced by mission identity, cancelled with the mission's token.
        """
        token = self._run_tokens.get(mission.mission_id)

        from assistant.models.admission import get_admission_controller

        return _DeepInvoke(
            self._deep,
            thread_id=mission.conversation_id,
            mission_id=mission.mission_id,
            plan_version=mission.plan_version,
            cancel_check=lambda: bool(token and token.is_cancelled),
            # D11: the invocation opens its own admission scope; the
            # transport admits before every request against the mission's
            # documented limits.
            admission=get_admission_controller(),
        )

    async def cancel(self, run_id: str | None = None) -> None:
        if run_id is None:
            for active in list(self._run_tokens.values()):
                active.cancel()
            await self._deep.cancel()
            return
        token: CancellationToken | None = self._run_tokens.get(run_id)
        if token is not None:
            token.cancel()

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]],
        cancel_check: Callable[[], bool],
        run_id: str = "",
    ) -> dict[str, Any]:
        from assistant.missions.service import action_shaped, looks_like_question

        token = CancellationToken()
        run_key = run_id or f"mission-{uuid.uuid4().hex[:8]}"
        self._run_tokens[run_key] = token
        try:
            if looks_like_question(text) and not action_shaped(text):
                # Pure information requests: cheap chat that CANNOT acquire
                # the desktop — the INFO role refuses desktop tools at the
                # wrapped-tool dispatch boundary (F02).
                from assistant.missions.controller import controller_role

                with controller_role("INFO"):
                    return await self._deep.run(
                        text,
                        thread_id=thread_id,
                        on_event=on_event,
                        cancel_check=cancel_check,
                        run_id=run_id,
                    )
            service = await self._resources.mission_service()
            conversation = thread_id.strip() or f"core-{uuid.uuid4().hex[:8]}"
            request = RequestEnvelope(
                request_id=run_id or new_id(),
                conversation_id=conversation,
                owner_id="sani-local",
                input_origin="typed_final",
                input_revision=1,
                text=text[:16000],
                submitted_at_ms=int(time.time() * 1000),
            )
            mission = await service.submit(
                request,
                cancel=token,
                on_event=on_event,
                invoke=_DeepInvoke(
                    self._deep,
                    thread_id=thread_id,
                    on_event=on_event,
                    cancel_check=lambda: token.is_cancelled or cancel_check(),
                ),
            )
            return self._result_of(mission, conversation)
        finally:
            self._run_tokens.pop(run_key, None)

    @staticmethod
    def _result_of(mission: Any, conversation: str) -> dict[str, Any]:
        from assistant.core.identity import engine_identity
        from assistant.memory.namespaces import thread_id_for_sani

        status_map = {
            "COMPLETED": "done",
            "FAILED": "failed",
            "CANCELLED": "cancelled",
            "BLOCKED": "blocked",
            "NEEDS_APPROVAL": "ASK_USER",
            "PAUSED": "ASK_USER",
            "WAITING_EXTERNAL": "blocked",
        }
        return {
            "status": status_map.get(mission.status, "blocked"),
            "mission_status": mission.status,
            "mission_id": mission.mission_id,
            "thread_id": thread_id_for_sani(conversation),
            "response": _mission_report(mission),
            "route": "mission",
            "verified": mission.status == "COMPLETED",
            "engine": engine_identity(),
        }


class _DeepInvoke:
    """The production Deep transport for Controller roles (R02/F01).

    Runs the real graph with an invocation-local submission capture: the
    model must answer by calling the role's structured submission tool; the
    captured payload is returned for schema validation. A response without
    a valid submission raises -- there is no raw-text fallback. The thread
    is namespaced by role AND mission identity (mission_id/plan_version are
    bound by the service), so planning never ingests unrelated history.
    """

    _KIND_BY_ROLE = {"PLAN": "plan", "RECOVER": "recovery", "REVIEW": "review"}

    def __init__(
        self,
        deep: DeepAgentEntry,
        *,
        thread_id: str,
        mission_id: str = "",
        plan_version: int = 0,
        on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        admission: Any = None,
    ) -> None:
        self._deep = deep
        self._thread_id = thread_id
        self._mission_id = mission_id
        self._plan_version = plan_version
        self._on_event = on_event or _noop_event
        self._cancel_check = cancel_check or (lambda: False)
        # D11: this invocation opens an admission SCOPE; every provider
        # request (graph sub-calls, retries, streams) is admitted at the
        # transport boundary BEFORE dispatch, against the mission's own
        # documented limits, and fails closed when accounting is down.
        self._admission = admission

    def bind(self, *, mission_id: str, plan_version: int) -> None:
        """Bind the mission identity so threads are namespaced per plan."""
        self._mission_id = mission_id
        self._plan_version = plan_version

    async def __call__(self, role: str, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        from assistant.missions.controller import controller_role
        from assistant.missions.submission import submission_capture

        kind = self._KIND_BY_ROLE[role]
        instruction = (
            f"{payload.get('prompt', '')}\n\n"
            f"Respond ONLY by calling the {tool} tool with the complete "
            "structured submission as its JSON payload."
        )
        capture: dict[str, Any] = {}
        capture_token = submission_capture.set(capture)
        scope = None
        if self._admission is not None:
            # D11: one admission scope per invocation, with the ceiling
            # taken from the mission's own durable budget limits.
            scope = await self._admission.open_scope(
                self._mission_id, self._plan_version,
                f"{kind}:{self._thread_id}",
            )
            self._admission.push_scope(scope)
        try:
            with controller_role(role):
                await self._deep.run(
                    instruction,
                    thread_id=(
                        f"{self._thread_id}:mission:{self._mission_id}:"
                        f"v{self._plan_version}:{kind}"
                    ),
                    on_event=self._on_event,
                    cancel_check=self._cancel_check,
                )
        except Exception as exc:
            # D14: the established producer — a provider rate limit on any
            # Controller role raises the typed wait signal instead of a
            # dead failure.
            text = str(exc).lower()
            if "rate limit" in text or "429" in text or "too many requests" in text:
                from assistant.missions.service import ExternalWaitRequested

                raise ExternalWaitRequested(
                    _retry_after_ms_from(exc), f"provider rate limited {role}"
                ) from exc
            raise
        finally:
            submission_capture.reset(capture_token)
            if scope is not None:
                self._admission.pop_scope(scope)
        # No valid structured submission: reject; the caller may allow one
        # bounded repair, but raw text is never parsed into authority.
        if capture.get(role) is None:
            raise ValueError(f"invalid {kind} submission: no {tool} call was made")
        return {kind: capture[role]}


def _retry_after_ms_from(exc: BaseException) -> int:
    """A bounded Retry-After from the provider error, else 30s (D14)."""
    import re as _re

    text = str(exc)
    match = _re.search(r"retry[- ]after(?:\D)*(\d{1,4})", text, _re.IGNORECASE)
    if match is not None:
        return min(int(match.group(1)), 3_600) * 1000
    return 30_000


async def _noop_event(kind: str, data: dict[str, Any]) -> None:
    return None


def _mission_report(mission: Any) -> str:
    """An honest sentence about the mission's terminal state."""
    if mission.status == "COMPLETED":
        return "Done -- every required check passed."
    if mission.status == "FAILED":
        return (
            "I could not complete this: a step failed its verification. "
            "Nothing was claimed as done."
        )
    if mission.status == "CANCELLED":
        return "Stopped before the mission could finish."
    if mission.status == "NEEDS_APPROVAL":
        return "I need your approval to continue."
    return "I paused this task: something needs your decision before I continue."


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
