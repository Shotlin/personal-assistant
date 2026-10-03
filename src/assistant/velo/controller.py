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

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from assistant.missions.contracts import CancellationToken

from assistant.core.protocol import AGENT_PROGRESS
from assistant.core.registry import AgentDescriptor
from assistant.memory.namespaces import thread_id_for_sani
from assistant.observability.timing import RunTimeline
from assistant.tools.policy import cua_target_state
from assistant.velo import scene
from assistant.velo.adapter import CuaAdapter
from assistant.velo.contracts import (
    Candidate,
    CandidateKind,
    DecisionStatus,
    JevDecisionRequest,
    JevDecisionService,
    JevServiceError,
    OutcomeState,
    Route,
    TaskCancelled,
    TaskState,
)
from assistant.velo.parse import ParsedCommand, parse, parse_chain
from assistant.velo.planner import Plan, looks_like_a_task, make_plan
from assistant.velo.recipes import RecipeResult, execute
from assistant.velo.scene import PENDING, SCENES
from assistant.velo.sight import SightKit
from assistant.velo.uimemory import UiMemory
from assistant.velo.vision import VisionLocator

logger = logging.getLogger("assistant.velo.controller")

#: Bounds on a planned run: re-plans after a failed step, and total wall-clock.
MAX_REPLANS = 2
MAX_PLAN_SECONDS = 150

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
        # A07 fix: per-run cancellation instead of one shared boolean.
        self._run_tokens: dict[str, CancellationToken] = {}
        self._sight = self._build_sight(settings)
        self._plan_model: Any = None

    @staticmethod
    def _build_sight(settings: Any) -> SightKit:
        """Eyes plus remembered spots; memory is skipped when there is no data dir."""
        memory = None
        data_dir = str(getattr(settings, "sani_data_dir", "") or "")
        if data_dir:
            try:
                memory = UiMemory(Path(data_dir).expanduser() / "ui-memory.db")
            except Exception:  # noqa: BLE001 -- memory is a bonus; never block startup
                logger.warning("ui_memory_unavailable", exc_info=True)
        return SightKit(VisionLocator(settings), memory)

    @property
    def descriptor(self) -> AgentDescriptor:
        return AgentDescriptor(
            id="velo",
            name="Velo",
            capabilities=("computer-control", "desktop", "reasoning", "memory"),
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

    def _is_cancelled(self, run_key: str, cancel_check: IsCancelled) -> bool:
        token = self._run_tokens.get(run_key)
        return bool(token and token.is_cancelled) or cancel_check()

    # -- entry ----------------------------------------------------------------

    async def run(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: OnEvent,
        cancel_check: IsCancelled,
        run_id: str = "",
    ) -> dict[str, Any]:
        from assistant.missions.contracts import CancellationToken

        token = CancellationToken()
        run_key = run_id or f"velo-{uuid.uuid4().hex[:8]}"
        self._run_tokens[run_key] = token
        try:
            return await self._run_scoped(
                text,
                thread_id=thread_id,
                on_event=on_event,
                cancel_check=cancel_check,
                run_key=run_key,
                token=token,
            )
        finally:
            self._run_tokens.pop(run_key, None)

    async def _run_scoped(
        self,
        text: str,
        *,
        thread_id: str,
        on_event: OnEvent,
        cancel_check: IsCancelled,
        run_key: str,
        token: CancellationToken,
    ) -> dict[str, Any]:
        conversation = thread_id.strip() or f"core-{uuid.uuid4().hex[:8]}"
        timeline = RunTimeline(f"velo-{uuid.uuid4().hex[:8]}")

        timeline_events = {"first_action": 0}

        async def event_with_timing(kind: str, data: dict[str, Any]) -> None:
            if kind == AGENT_PROGRESS and timeline_events["first_action"] == 0:
                timeline_events["first_action"] = timeline.elapsed_ms
            await on_event(kind, data)

        command = self._answer_to_pending(text, conversation) or parse(text)
        command = command or self._title_command(text, conversation)

        chain = parse_chain(text) if command is None else None
        plan = None
        if command is None and chain is None and self._planner_on() and looks_like_a_task(text):
            plan = await self._plan(text)
        if plan is not None and plan.kind == "plan":
            timeline.mark("route_decided", metadata={"route": Route.PLAN.value})
            result = await self._run_plan(
                plan.steps, text, conversation, event_with_timing, cancel_check, run_key
            )
        elif plan is not None and plan.kind == "ask":
            timeline.mark("route_decided", metadata={"route": Route.PLAN.value})
            result = self._question_result(plan.question, conversation)
        elif chain is not None:
            timeline.mark("route_decided", metadata={"route": Route.LOCAL.value})
            result = await self._run_chain(
                chain, conversation, event_with_timing, cancel_check, run_key
            )
        elif command is not None:
            # Route A: explicit input decided this; no model call belongs here.
            timeline.mark("route_decided", metadata={"route": Route.LOCAL.value})
            result = await self._run_local(
                command, conversation, event_with_timing, cancel_check, run_key
            )
        else:
            candidates = await self._candidates(text, event_with_timing, cancel_check, run_key)
            if self._is_cancelled(run_key, cancel_check):
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
                    run_key,
                )
            else:
                timeline.mark("route_decided", metadata={"route": Route.PLAN.value})
                result = await self._deep.run(
                    text,
                    thread_id=thread_id,
                    on_event=event_with_timing,
                    cancel_check=lambda: self._is_cancelled(run_key, cancel_check),
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

    def _planner_on(self) -> bool:
        return bool(getattr(self._settings, "velo_planner_enabled", False))

    async def _plan(self, text: str, *, replan: dict[str, str] | None = None) -> Plan | None:
        """One short model call: the request as typed steps (or a question). Fails to ``None``."""
        try:
            from assistant.models import build_chat_model

            if self._plan_model is None:
                self._plan_model = build_chat_model(self._settings)
            return await make_plan(self._plan_model, text, replan=replan)
        except Exception as exc:  # noqa: BLE001 -- the old route is the fallback
            logger.warning("velo_planner_unavailable: %s", exc)
            return None

    @staticmethod
    def _question_result(question: str, conversation: str) -> dict[str, Any]:
        return {
            "status": "ASK_USER",
            "thread_id": thread_id_for_sani(conversation) if conversation else "",
            "response": question,
            "route": Route.PLAN.value,
            "outcome": OutcomeState.UNKNOWN.value,
            "verified": False,
        }

    async def _screen_facts(
        self, conversation: str, on_event: OnEvent, cancel_check: IsCancelled
    ) -> str:
        """The visible controls right now, compactly: evidence for a judge or a re-plan."""
        from assistant.velo import recipes

        try:
            runtime = await self._get_runtime()
            async with runtime.run_scope(
                f"core-{uuid.uuid4().hex[:8]}", conversation=conversation
            ):
                task = TaskState(instruction="facts", conversation=conversation)
                self._seed_carried_target(task)
                adapter = CuaAdapter(
                    runtime.cua_tools, on_event=on_event, cancel_check=cancel_check,
                    sight=self._sight,
                )
                surface = await recipes._scene_surface(task, adapter)
                if isinstance(surface, str):
                    return surface
                _target, pid, window_id = surface
                state = await adapter.observe_window(
                    task, pid, window_id, max_elements=1500, settle=True
                )
                return (scene.compact_observation(state) or "")[:1800]
        except Exception as exc:  # noqa: BLE001 -- facts are best effort
            logger.info("velo_screen_facts_failed: %s", exc)
            return ""

    async def _judge(
        self, command: ParsedCommand, response: str, facts: str
    ) -> bool | None:
        """JEV answers one closed question: did this step work? ``None`` = no answer."""
        jev = self._jev()
        if jev is None:
            return None
        request = JevDecisionRequest(
            objective=(
                f"Did this step succeed: {command.recipe} {command.kwargs}? "
                f"The tool said: {response[:200]}"
            ),
            requested_app=command.requested_app,
            target_summary="",
            scene_facts=tuple(facts.splitlines()[:25]),
            candidates=(
                Candidate("step_ok", CandidateKind.RECIPE, "The step worked; continue.",
                          recipe="noop"),
                Candidate("step_failed", CandidateKind.RECIPE,
                          "The step did not work; re-plan.", recipe="noop"),
            ),
            reason="verify a step whose own check could not confirm it",
        )
        try:
            decision = await jev.decide(request)
        except JevServiceError:
            return None
        if decision.status is not DecisionStatus.ACT:
            return None
        return decision.selected_id == "step_ok"

    async def _run_plan(
        self,
        steps: list[ParsedCommand],
        text: str,
        conversation: str,
        on_event: OnEvent,
        cancel_check: IsCancelled,
        run_key: str,
    ) -> dict[str, Any]:
        """Run typed steps one by one; verify; re-plan only the remainder, at most twice.

        A step runs on the deterministic recipes. When its own check cannot confirm
        it, JEV (if enabled) answers a closed yes/no with the screen as evidence.
        A failed step triggers a re-plan from the REAL current screen, never a retry
        of the same blind action, and the whole run is bounded.
        """
        queue = list(steps)
        done: list[str] = []
        said: list[str] = []
        replans = 0
        started = time.monotonic()
        result: dict[str, Any] = {}
        while queue:
            if self._is_cancelled(run_key, cancel_check):
                return self._stopped_result(conversation, route=Route.PLAN.value)
            if time.monotonic() - started > MAX_PLAN_SECONDS:
                said.append("I ran out of time on this one.")
                break
            command = queue.pop(0)
            result = await self._run_local(command, conversation, on_event, cancel_check, run_key)
            response = str(result.get("response") or "")
            outcome = result.get("outcome")
            ok = outcome in (OutcomeState.CONFIRMED.value, OutcomeState.ACCEPTED.value)
            if not ok and outcome == OutcomeState.UNKNOWN.value:
                facts = await self._screen_facts(conversation, on_event, cancel_check)
                ok = (await self._judge(command, response, facts)) is True
            if ok:
                shown = json.dumps(command.kwargs, ensure_ascii=False)[:80]
                done.append(f"{command.recipe} {shown}")
                said.append(response)
                if queue:
                    await asyncio.sleep(0.3)
                continue
            # The step did not work. One re-plan from what is really on screen.
            if replans >= MAX_REPLANS:
                said.append(response)
                break
            facts = await self._screen_facts(conversation, on_event, cancel_check)
            new = await self._plan(text, replan={
                "done": "; ".join(done) or "none",
                "failed": f"{command.recipe} {json.dumps(command.kwargs, ensure_ascii=False)[:80]}",
                "answer": response[:200],
                "screen": facts or "(unreadable)",
            })
            replans += 1
            if new is not None and new.kind == "ask":
                return self._question_result(new.question, conversation)
            if new is None or new.kind != "plan":
                said.append(response)
                break
            queue = list(new.steps)
        else:
            result["status"] = result.get("status") or "done"
            result["response"] = " ".join(p for p in said if p)
            result["recipe"] = "plan"
            result["route"] = Route.PLAN.value
            result["planned_steps"] = len(done)
            return result
        if result.get("status") == "done":
            result["status"] = "blocked"
        result["response"] = " ".join(p for p in said if p)
        result["recipe"] = "plan"
        result["route"] = Route.PLAN.value
        result["planned_steps"] = len(done)
        return result

    @staticmethod
    def _answer_to_pending(text: str, conversation: str) -> ParsedCommand | None:
        """A short reply to "which one?" ("the second one", "create") resolves the question."""
        if len(text) > 80:
            PENDING.clear(conversation)
            return None
        picked = PENDING.take(conversation, text)
        if picked is None:
            PENDING.clear(conversation)  # an unrelated message retires a stale question
            return None
        recipe, option = picked
        return ParsedCommand(recipe=recipe, kwargs={"label": option}, utterance=text.strip())

    @staticmethod
    def _title_command(text: str, conversation: str) -> ParsedCommand | None:
        """"Play <title>" resolves locally only when it names an item just described.

        Anything else ("play some jazz") still goes to the reasoning route.
        """
        match = re.match(
            r"^(?:play|open|watch|click|select)\s+(?:the\s+)?(?P<t>.{4,120}?)\s*[.!]?$",
            " ".join(text.split()),
            re.I,
        )
        if not match or not SCENES.recall(conversation):
            return None
        title = match.group("t")
        if scene.match_title(SCENES.recall(conversation), title) is None:
            return None
        return ParsedCommand(
            recipe="press_item", kwargs={"title": title}, utterance=text.strip()
        )

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

    async def _compose(self, brief: str) -> str:
        """One short model call that writes the text to type. No tools, bounded.

        The user said what they want ("a prompt for a simple SaaS website");
        the words themselves are model work, everything after is deterministic.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        from assistant.models import build_chat_model

        model = build_chat_model(self._settings)
        reply = await asyncio.wait_for(
            model.ainvoke(
                [
                    SystemMessage(
                        "You write the exact text the user wants typed into an input box. "
                        "Reply with ONLY that text: no quotes, no preamble, no explanation. "
                        "When they want a prompt for building something, write one clear, "
                        "specific, self-contained prompt (goal, pages and features, visual "
                        "style, constraints) in under 180 words. Ignore speech-recognition "
                        "noise in their request and infer what they meant."
                    ),
                    HumanMessage(brief),
                ]
            ),
            timeout=45,
        )
        content = getattr(reply, "content", "")
        if isinstance(content, list):
            content = "".join(
                str(b.get("text", "")) for b in content if isinstance(b, dict)
            )
        return str(content).strip().strip('"').strip()

    async def _run_local(
        self,
        command: ParsedCommand,
        conversation: str,
        on_event: OnEvent,
        cancel_check: IsCancelled,
        run_key: str = "",
    ) -> dict[str, Any]:
        written = ""
        if command.recipe == "compose_fill":
            try:
                written = await self._compose(str(command.kwargs.get("brief", "")))
            except Exception as exc:  # noqa: BLE001 -- say what failed and what to do next
                logger.warning("velo_compose_failed: %s", exc)
                written = ""
            if not written:
                return {
                    "status": "blocked",
                    "thread_id": thread_id_for_sani(conversation) if conversation else "",
                    "response": (
                        "I couldn't get the text written just now (the language model did not "
                        "answer). Say it again in a moment, or dictate the exact text and "
                        "I'll type it."
                    ),
                    "route": Route.LOCAL.value,
                    "outcome": OutcomeState.UNKNOWN.value,
                    "verified": False,
                }
            command = ParsedCommand(
                recipe="fill_field",
                kwargs={
                    "text": written,
                    "target": "",
                    "submit": bool(command.kwargs.get("submit")),
                },
                utterance=command.utterance,
            )
        runtime = await self._get_runtime()
        async with runtime.run_scope(
            f"core-{uuid.uuid4().hex[:8]}", conversation=conversation
        ) as budget:
            task = self._task_from(command, conversation, cancel_check)
            self._seed_carried_target(task)
            adapter = CuaAdapter(
                runtime.cua_tools, on_event=on_event, cancel_check=cancel_check,
                sight=self._sight,
            )
            problem = await self._permission_problem(adapter)
            if problem is not None:
                return self._local_result(
                    task, RecipeResult(command.recipe, OutcomeState.UNKNOWN, problem), 0
                )
            try:
                outcome = await execute(command.recipe, task, adapter, **command.kwargs)
            except TaskCancelled as exc:
                return self._local_result(
                    task, RecipeResult(command.recipe, OutcomeState.CANCELLED, f"Stopped: {exc}"),
                    budget.used,
                )
            if outcome.choices:
                # Ask, then remember the options: the next short answer picks one.
                PENDING.ask(conversation, outcome.recipe, outcome.choices)
            if written and outcome.state in (OutcomeState.CONFIRMED, OutcomeState.UNKNOWN):
                lead = "I wrote the prompt and entered it" + (
                    "" if outcome.state is OutcomeState.CONFIRMED
                    else ", but I could not read the box back to confirm"
                )
                outcome = RecipeResult(
                    outcome.recipe, outcome.state,
                    f"{lead}. It starts: {written[:140]}",
                    outcome.verification,
                )
            return self._local_result(task, outcome, budget.used)

    async def _run_chain(
        self,
        commands: list[ParsedCommand],
        conversation: str,
        on_event: OnEvent,
        cancel_check: IsCancelled,
        run_key: str,
    ) -> dict[str, Any]:
        """Run fully-parsed local steps in order; stop at the first that is not confirmed.

        Fail closed: a step that did not demonstrably work ends the chain and
        says which step it was, so later steps never act on a screen the
        earlier ones did not produce.
        """
        said: list[str] = []
        result: dict[str, Any] = {}
        for number, command in enumerate(commands, 1):
            if self._is_cancelled(run_key, cancel_check):
                return self._stopped_result(conversation, route=Route.LOCAL.value)
            result = await self._run_local(command, conversation, on_event, cancel_check, run_key)
            said.append(str(result.get("response") or ""))
            ok = result.get("outcome") in (
                OutcomeState.CONFIRMED.value,
                OutcomeState.ACCEPTED.value,
            )
            if not ok:
                left = len(commands) - number
                said.append(
                    f"I stopped at step {number} of {len(commands)}"
                    + (f" and did not do the other {left}." if left else ".")
                )
                if result.get("status") == "done":
                    result["status"] = "blocked"
                break
            if number < len(commands):
                await asyncio.sleep(0.4)
        result["response"] = " ".join(p for p in said if p)
        result["recipe"] = "chain"
        return result

    @staticmethod
    async def _permission_problem(adapter: CuaAdapter) -> str | None:
        """A plain sentence when macOS has not granted Sani screen access, else ``None``.

        Without Accessibility and Screen Recording the window tree is unreadable,
        and every recipe ends in a vague "could not confirm". Asking first turns
        that into one clear instruction. A driver that cannot answer (or has no
        such tool) is never treated as a denial.
        """
        try:
            reply = await adapter.call("check_permissions")
        except Exception:  # noqa: BLE001 -- an unanswerable preflight must not block work
            return None
        granted = reply.structured or {}
        missing = [
            label
            for key, label in (("accessibility", "Accessibility"),
                               ("screen_recording", "Screen Recording"))
            if granted.get(key) is False
        ]
        if not missing:
            return None
        return (
            "I can't see or control your screen yet because macOS has not allowed Sani: "
            f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} off. Open System "
            "Settings, Privacy and Security, turn Sani on there, then quit and reopen Sani."
        )

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

        # A02/RF-01: the turn status must match what actually happened. Only a
        # confirmed or honestly-negative outcome reads as a completed turn; an
        # UNKNOWN or cancelled unit is reported as blocked/cancelled, never
        # laundered into "done" (the host maps done -> completed).
        status = {
            OutcomeState.CONFIRMED: "done",
            OutcomeState.NO_EFFECT: "done",
            OutcomeState.ACCEPTED: "done",
            OutcomeState.DISPATCHED: "blocked",
            OutcomeState.UNKNOWN: "blocked",
            OutcomeState.FAILED: "failed",
            OutcomeState.CANCELLED: "cancelled",
        }.get(outcome.state, "blocked")
        return {
            "status": status,
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
        self, text: str, on_event: OnEvent, cancel_check: IsCancelled, run_key: str = ""
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
            runtime.cua_tools, on_event=on_event, cancel_check=cancel_check,
            sight=self._sight,
        )
        try:
            async with runtime.run_scope(
                f"core-{uuid.uuid4().hex[:8]}", conversation=""
            ):
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
        run_key: str = "",
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
                cancel_check=lambda: self._is_cancelled(run_key, cancel_check),
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
        if self._is_cancelled(run_key, cancel_check):
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
            candidates = await self._candidates(text, on_event, cancel_check, run_key)
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
                runtime.cua_tools, on_event=on_event, cancel_check=cancel_check,
                sight=self._sight,
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
