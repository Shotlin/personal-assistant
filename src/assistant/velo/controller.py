"""The Velo controller: one owner of intent, task state, and execution.

Three routes, one execution authority (master plan section 4):

- **Route A** -- a fully resolved ordinary command resolves locally and
  executes immediately as a recipe. JEV is not called merely to approve a
  decision the explicit input already determined.
- **Route B** -- interpretation or target selection needed: a bounded
  candidate set is built from the instruction and the scene, JEV makes ONE
  structured choice, and the selected recipe executes locally. A late JEV
  answer after a stop produces no action; an unavailable JEV is reported
  accurately and never silently replaced by the general model.
- **Route C** -- an unfamiliar multi-step objective goes to the general
  reasoning executor, which enters the same policy/adapter path.

The controller carries the conversation's bound target forward between
commands (validated, never assumed), tracks no progress across actions,
observations, waits and recovery attempts, and reports timing honestly:
acknowledgement is never presented as completion.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from assistant.core.protocol import AGENT_PROGRESS
from assistant.core.registry import AgentDescriptor
from assistant.observability.timing import RunTimeline
from assistant.memory.namespaces import thread_id_for_sani
from assistant.tools.policy import cua_target_state
from assistant.velo.adapter import CuaAdapter
from assistant.velo.contracts import (
    Candidate,
    CandidateKind,
    DecisionStatus,
    JevDecisionRequest,
    JevDecisionService,
    JevServiceError,
    NoProgressTracker,
    OutcomeState,
    Route,
    TaskCancelled,
    TaskState,
)
from assistant.velo.parse import ParsedCommand, parse
from assistant.velo.recipes import RecipeResult, execute

logger = logging.getLogger("assistant.velo.controller")

OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]
IsCancelled = Callable[[], bool]


class VeloEntry:
    """The ``velo`` agent: controller, JEV when needed, one executor below."""

    def __init__(
        self,
        settings: Any,
        *,
        get_runtime: Callable[[], Awaitable[Any]],
        deep_entry: Any,
        jev_factory: Callable[[], JevDecisionService | None] | None = None,
    ) -> None:
        self._settings = settings
        self._get_runtime = get_runtime
        self._deep = deep_entry
        self._jev_factory = jev_factory
        self._cancelled = False

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            id="velo",
            name="Velo",
            capabilities=("computer-control", "desktop", "reasoning", "memory"),
        )

    async def cancel(self) -> None:
        self._cancelled = True
        await self._deep.cancel()

    # -- entry ----------------------------------------------------------------

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: OnEvent,
        cancel_check: IsCancelled,
    ) -> dict[str, Any]:
        self._cancelled = False
        conversation = thread_id.strip() or f"core-{uuid.uuid4().hex[:8]}"
        timeline = RunTimeline(f"velo-{uuid.uuid4().hex[:8]}")

        timeline_events = {"first_action": 0}

        async def event_with_timing(kind: str, data: dict[str, Any]) -> None:
            if kind == AGENT_PROGRESS and timeline_events["first_action"] == 0:
                timeline_events["first_action"] = timeline.elapsed_ms
            await on_event(kind, data)

        command = parse(text)

        if command is not None:
            # Route A: explicit input decided this; no model call belongs here.
            timeline.mark("route_decided", metadata={"route": Route.LOCAL.value})
            result = await self._run_local(
                command, conversation, event_with_timing, cancel_check
            )
        else:
            candidates = await self._candidates(text, event_with_timing, cancel_check)
            if cancel_check() or self._cancelled:
                # A stop that landed while probing the scene ends the run. It
                # must never fall through to the planner, which acts.
                result = self._stopped_result(conversation, route=Route.JEV.value)
            elif candidates:
                timeline.mark("route_decided", metadata={"route": Route.JEV.value})
                result = await self._run_jev(
                    text,
                    candidates,
                    conversation,
                    event_with_timing,
                    cancel_check,
                )
            else:
                timeline.mark("route_decided", metadata={"route": Route.PLAN.value})
                result = await self._deep.run(
                    text,
                    thread_id=thread_id,
                    on_event=event_with_timing,
                    cancel_check=lambda: cancel_check() or self._cancelled,
                )
                result["route"] = Route.PLAN.value

        first_action = timeline_events["first_action"]
        result["timing"] = {
            "route": result.get("route", Route.LOCAL.value),
            "first_action_ms": first_action or None,
            "completed_ms": timeline.elapsed_ms,
        }
        timeline.mark_terminal("run_finished")
        return result

    @staticmethod
    def _stopped_result(conversation: str, *, route: str) -> dict[str, Any]:
        return {
            "status": "cancelled",
            "thread_id": thread_id_for_sani(conversation) if conversation else "",
            "response": "Stopped.",
            "route": route,
            "verified": False,
        }

    # -- Route A ---------------------------------------------------------------

    async def _run_local(
        self,
        command: ParsedCommand,
        conversation: str,
        on_event: OnEvent,
        cancel_check: IsCancelled,
    ) -> dict[str, Any]:
        runtime = await self._get_runtime()
        async with runtime.run_scope(
            f"core-{uuid.uuid4().hex[:8]}", conversation=conversation
        ) as budget:
            task = self._task_from(command, conversation, cancel_check)
            self._seed_carried_target(task)
            adapter = CuaAdapter(
                runtime.cua_tools, on_event=on_event, cancel_check=cancel_check
            )
            try:
                outcome = await execute(command.recipe, task, adapter, **command.kwargs)
            except TaskCancelled as exc:
                return self._local_result(
                    task, RecipeResult(command.recipe, OutcomeState.CANCELLED, f"Stopped: {exc}"),
                    budget.used,
                )
            return self._local_result(task, outcome, budget.used)

    def _task_from(
        self, command: ParsedCommand, conversation: str, cancel_check: IsCancelled
    ) -> TaskState:
        task = TaskState(
            instruction=command.utterance,
            conversation=conversation,
            requested_app=command.requested_app,
            route=Route.LOCAL,
            max_actions=12,
        )
        deadline = getattr(self._settings, "velo_command_deadline_seconds", 90)
        task.deadline = time.monotonic() + deadline
        task.cancelled = cancel_check()
        return task

    @staticmethod
    def _seed_carried_target(task: TaskState) -> None:
        """The conversation's validated surface, carried into the new command.

        The policy store remembers which pid/window this conversation last
        acted on; recipes re-validate it against a fresh list_apps before use.
        """
        state = cua_target_state.get() or {}
        focus = state.get("focus")
        if isinstance(focus, (tuple, list)) and focus and isinstance(focus[0], int):
            from assistant.velo.contracts import AppIdentity, Target, TargetOwnership

            pid = int(focus[0])
            window_id = int(focus[1]) if len(focus) > 1 and isinstance(focus[1], int) else None
            task.resolved = Target(
                app=AppIdentity(name="", pid=pid, running=True),
                window_id=window_id,
                ownership=TargetOwnership.OBSERVED,
                version=task.version,
            )

    @staticmethod
    def _local_result(
        task: TaskState, outcome: RecipeResult, actions_used: int
    ) -> dict[str, Any]:
        from assistant.core.identity import engine_identity

        return {
            "status": "done",
            "thread_id": thread_id_for_sani(task.conversation) if task.conversation else "",
            "response": outcome.answer,
            "route": Route.LOCAL.value,
            "recipe": outcome.recipe,
            "outcome": outcome.state.value,
            "verified": bool(outcome.verification and outcome.verification.satisfied),
            "cua_actions_used": actions_used,
            "engine": engine_identity(),
        }

    # -- Route B ---------------------------------------------------------------

    async def _candidates(
        self, text: str, on_event: OnEvent, cancel_check: IsCancelled
    ) -> tuple[Candidate, ...]:
        """A bounded candidate set for an instruction that did not parse.

        Only mechanical readings of the text and the scene become candidates:
        apps whose identity matches a word of the instruction, running
        browsers for an unscoped search. Nothing here invents content.
        """
        cleaned = text.strip()
        lowered = cleaned.lower()
        candidates: list[Candidate] = []
        runtime = await self._get_runtime()
        adapter = CuaAdapter(
            runtime.cua_tools, on_event=on_event, cancel_check=cancel_check
        )
        try:
            async with runtime.run_scope(
                f"core-{uuid.uuid4().hex[:8]}", conversation=""
            ) as budget:
                probe = TaskState(instruction=cleaned, route=Route.JEV)
                apps = await adapter.list_apps(probe)
            running = [a for a in apps if a.running and a.pid]
            words = {w.strip(".,!?'\"").lower() for w in cleaned.split()}
            for app in running:
                head = app.name.lower().split()[0] if app.name else ""
                if head and head in words and len(candidates) < 6:
                    candidates.append(
                        Candidate(
                            id=f"open:{app.name}",
                            kind=CandidateKind.RECIPE,
                            description=f"Open or bring {app.name} to the front.",
                            recipe="open_app",
                            args={"app_name": app.name},
                        )
                    )
            if lowered.startswith(("search", "look up")) and len(candidates) < 6:
                browsers = [a for a in running if _is_browser(a)]
                query = cleaned
                for prefix in ("search for", "search", "look up"):
                    if lowered.startswith(prefix):
                        query = cleaned[len(prefix):].strip()
                        break
                for browser in browsers[:3]:
                    candidates.append(
                        Candidate(
                            id=f"search:{browser.name}",
                            kind=CandidateKind.RECIPE,
                            description=(
                                f"Search for {query!r} in {browser.name}, which is already running."
                            ),
                            recipe="search_browser",
                            args={"query": query, "app_name": browser.name},
                        )
                    )
        except TaskCancelled:
            return ()
        return tuple(candidates)

    async def _run_jev(
        self,
        text: str,
        candidates: tuple[Candidate, ...],
        conversation: str,
        on_event: OnEvent,
        cancel_check: IsCancelled,
    ) -> dict[str, Any]:
        """One JEV decision, then local execution of the chosen recipe."""
        jev = self._jev()
        if jev is None:
            # JEV is disabled by configuration, not failing mid-flight. Dead-
            # ending here (live 2026-09-26 03:16) left ordinary phrasing
            # unusable, so the run defers -- disclosed -- to the reasoning
            # executor, which enters the same policy path. An ENABLED service
            # that fails still stops honestly, below.
            result = await self._deep.run(
                text,
                # The Deep Agent prefixes the conversation id itself; hand it
                # the raw id so the thread (and its memory) stays the same one.
                thread_id=conversation,
                on_event=on_event,
                cancel_check=lambda: cancel_check() or self._cancelled,
            )
            result["route"] = Route.PLAN.value
            result["route_note"] = (
                "Route B deferred to the reasoning executor: the decision "
                "service is disabled (VELO_JEV_ENABLED=false)"
            )
            return result
        request = JevDecisionRequest(
            objective=text,
            requested_app="",
            target_summary="",
            scene_facts=(),
            candidates=candidates,
            payload_ids=tuple(),
            reason="the instruction did not resolve to one command mechanically",
        )
        try:
            decision = await jev.decide(request)
        except JevServiceError as exc:
            logger.warning("velo_jev_unavailable: %s", exc)
            return {
                "status": "done",
                "thread_id": thread_id_for_sani(conversation),
                "response": (
                    f"I need to interpret {text!r}, but the decision service failed "
                    f"({exc}), so I did not guess rather than act on a wrong reading."
                ),
                "route": Route.JEV.value,
                "outcome": OutcomeState.UNKNOWN.value,
                "verified": False,
            }
        # A stop that landed during the request outranks the late answer.
        if cancel_check() or self._cancelled:
            return {
                "status": "cancelled",
                "thread_id": thread_id_for_sani(conversation),
                "response": "Stopped.",
                "route": Route.JEV.value,
                "verified": False,
            }
        if decision.need_more_evidence:
            # One targeted refresh, then the candidates are re-asked; if the
            # screen cannot disambiguate either, the user decides.
            candidates = await self._candidates(text, on_event, cancel_check)
            if candidates:
                request = JevDecisionRequest(
                    objective=text,
                    requested_app="",
                    target_summary="",
                    candidates=candidates,
                    reason="the first decision wanted better evidence",
                )
                decision = await jev.decide(request)
            if decision.need_more_evidence:
                return self._ask_user(text, candidates, conversation)
        if decision.status is DecisionStatus.ASK_USER:
            return self._ask_user(text, candidates, conversation)
        if decision.status is DecisionStatus.STOP:
            return {
                "status": "done",
                "thread_id": thread_id_for_sani(conversation),
                "response": (
                    f"I looked at what is running and cannot carry out {text!r} from "
                    "here. Nothing was changed."
                ),
                "route": Route.JEV.value,
                "outcome": OutcomeState.NO_EFFECT.value,
                "verified": False,
            }
        if decision.status is DecisionStatus.DONE:
            return {
                "status": "done",
                "thread_id": thread_id_for_sani(conversation),
                "response": f"That looks already done: {text!r}.",
                "route": Route.JEV.value,
                "outcome": OutcomeState.CONFIRMED.value,
                "verified": False,
            }
        selected = next((c for c in candidates if c.id == decision.selected_id), None)
        if selected is None or selected.kind is not CandidateKind.RECIPE:
            return self._ask_user(text, candidates, conversation)
        runtime = await self._get_runtime()
        async with runtime.run_scope(
            f"core-{uuid.uuid4().hex[:8]}", conversation=conversation
        ) as budget:
            task = TaskState(
                instruction=text,
                conversation=conversation,
                requested_app=selected.args.get("app_name", ""),
                route=Route.JEV,
                max_actions=12,
            )
            task.deadline = time.monotonic() + getattr(
                self._settings, "velo_command_deadline_seconds", 90
            )
            self._seed_carried_target(task)
            adapter = CuaAdapter(
                runtime.cua_tools, on_event=on_event, cancel_check=cancel_check
            )
            try:
                outcome = await execute(selected.recipe, task, adapter, **selected.args)
            except TaskCancelled as exc:
                return self._local_result(
                    task, RecipeResult(selected.recipe, OutcomeState.CANCELLED, f"Stopped: {exc}"),
                    budget.used,
                )
            result = self._local_result(task, outcome, budget.used)
            result["route"] = Route.JEV.value
            return result

    def _ask_user(
        self, text: str, candidates: tuple[Candidate, ...], conversation: str
    ) -> dict[str, Any]:
        options = "; ".join(c.description for c in candidates[:4])
        return {
            "status": "ASK_USER",
            "thread_id": thread_id_for_sani(conversation),
            "response": (
                f"{text!r} could mean more than one thing. {options} -- "
                "which one did you mean?"
            ),
            "route": Route.JEV.value,
            "outcome": OutcomeState.UNKNOWN.value,
            "verified": False,
        }

    def _jev(self) -> JevDecisionService | None:
        """The configured decision service, or None when it is not enabled."""
        if not getattr(self._settings, "velo_jev_enabled", False):
            return None
        if self._jev_factory is None:
            from assistant.velo.jev import TypeSafeJevService

            return TypeSafeJevService.from_settings(self._settings)
        return self._jev_factory()


def _is_browser(app: Any) -> bool:
    markers = ("chrome", "safari", "arc", "firefox", "edge", "brave", "opera")
    name = (app.name or "").lower()
    bundle = (app.bundle_id or "").lower()
    return any(marker in name or marker in bundle for marker in markers)


__all__ = ["VeloEntry"]
