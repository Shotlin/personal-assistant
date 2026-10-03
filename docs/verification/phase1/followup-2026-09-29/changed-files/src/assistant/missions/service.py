"""MissionService: one durable mission owner above the Velo fast path.

Composes the pieces the earlier tasks built (file 03 §2, §4):

- ``submit`` claims the request idempotently in the store, plans the mission
  (deterministically for exact commands -- zero Deep, zero JEV -- through
  the role-scoped Deep Controller otherwise), commits the plan, then runs
  dependency-ready steps to completion through the bounded executor;
- every result lands through ``apply_result`` (CAS, idempotent); a stale
  result never advances state;
- the terminal status is computed by a DETERMINISTIC gate from required
  steps and required checks -- the Controller's final review cannot set it;
- control commands (pause/resume/cancel/revise/priority) go through the
  store's compare-and-swap.

The Velo fast path itself is untouched: without ``JARVIS_MISSIONS_ENABLED``
the shell behaves exactly as before.
"""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path
from typing import Any, Literal

from assistant.missions.authority import MissionAuthority
from assistant.missions.contracts import (
    ApprovalRecord,
    BudgetLimits,
    CancellationToken,
    CheckSpec,
    MissionControl,
    MissionRecord,
    PlanProposal,
    RequestEnvelope,
    Scope,
    StepResult,
    StepSpec,
    new_id,
    text_digest,
)
from assistant.missions.controller import ControllerContext, DeepController
from assistant.missions.evidence import EvidenceStore
from assistant.missions.executor import VeloExecutor
from assistant.missions.observer import Observer, RecommendationStore
from assistant.missions.store import MissionStore, MissionStoreError, StaleControlError
from assistant.velo.parse import parse

logger = logging.getLogger("assistant.missions.service")

#: Trusted recipe -> tool-id catalog (file 03 §12: tool ids come only from
#: the verified inventory). The mission executor can dispatch nothing else.
RECIPE_TOOL_CATALOG: dict[str, list[str]] = {
    "open_app": ["list_apps", "list_windows", "launch_app", "bring_to_front", "get_window_state"],
    "navigate": ["list_apps", "get_window_state", "click", "type_text", "press_key", "set_value"],
    "search_browser": ["list_apps", "get_window_state", "click", "type_text", "press_key",
         "set_value"],
    "scroll": ["list_apps", "get_window_state", "scroll"],
    "type_text": ["list_apps", "get_window_state", "click", "type_text", "set_value"],
    "press_ordinal": ["list_apps", "get_window_state", "press_key", "click"],
    "semantic_ui": ["list_apps", "list_windows", "get_window_state", "click", "type_text",
                     "set_value", "press_key", "scroll", "bring_to_front"],
}

#: Question shapes that go to Deep for an answer, never to the desktop.
_QUESTION_PREFIXES = (
    "what", "who", "when", "where", "why", "how", "which", "is", "are", "was",
    "were", "do", "does", "did", "can", "could", "should", "would", "will",
    "tell me", "explain", "summarize", "define", "translate", "calculate",
)


def _url_markers(destination: str) -> list[str]:
    """Deterministic page markers from a navigation destination (C05)."""
    import re as _re

    tokens: list[str] = []
    host = _re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", destination)
    host = host.split("/", 1)[0]
    host = host.split(":", 1)[0]
    for part in host.split("."):
        if part and part not in {"www", "com", "org", "net", "gov", "edu"}:
            tokens.append(part.lower())
    return tokens


class MissionPlanError(MissionStoreError):
    """A Deep proposal failed validation; no partial plan is committed."""


class MissionService:
    """Owns mission state transitions, budgets, claims and dispatch."""

    def __init__(
        self,
        settings: Any,
        *,
        store: MissionStore,
        authority: MissionAuthority,
        evidence: EvidenceStore,
        executor: VeloExecutor,
        controller: DeepController,
        owner_id: str = "sani-local",
        host_generation: str = "",
        desktop_queue: Any = None,
        transport_factory: Any = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._authority = authority
        self._evidence = evidence
        self._executor = executor
        self._controller = controller
        self._owner_id = owner_id
        self._deep_calls = 0
        self._jev_calls = 0
        # C04/N05: one mission/version-bound Deep transport for EVERY
        # Controller role. The run path registers the invocation's transport;
        # recovery, review and revision reuse (or re-derive) it so no role
        # ever calls the Controller without a real transport.
        self._transport_factory = transport_factory
        self._transports: dict[str, Any] = {}
        # R09/F11: the read-only Observer consumes committed sanitized
        # events and writes to a SEPARATE recommendation sink. Its failures
        # never affect mission execution. The root is the app data dir when
        # configured, else a temp dir -- NEVER the process cwd, so no run
        # (including tests) litters the working tree.
        import tempfile

        data_dir = getattr(settings, "sani_data_dir", "") or ""
        observer_root = (
            Path(data_dir) if data_dir else Path(tempfile.gettempdir()) / "sani-observer"
        ) / "observer-recs"
        self._observer = Observer(RecommendationStore(observer_root))
        # R05/F06: ONE desktop ownership queue for the whole service; the
        # host generation is this process's lifecycle identity (the Rust
        # latch guards admission at the host layer).
        from assistant.runtime.desktop_queue import DesktopQueue

        self._desktop_queue = desktop_queue or DesktopQueue(timeout_seconds=120)
        self._host_generation = host_generation or f"core-{new_id()[:8]}"
        self._run_tokens: dict[str, CancellationToken] = {}

    # -- submit ------------------------------------------------------------------

    async def submit(
        self,
        request: RequestEnvelope,
        *,
        cancel: CancellationToken,
        on_event: Any = None,
        invoke: Any = None,
    ) -> MissionRecord:
        """Claim, plan, run, and gate one mission (file 03 §4 MissionService)."""
        scope = self._scope_for(request)
        digest = text_digest(f"{request.text}\x1f{request.input_origin}")
        mission = await self._store.claim_request(
            request, digest, goal=request.text, scope=scope,
            limits=self._limits_for(request),
        )
        if mission.status in {"COMPLETED", "FAILED", "CANCELLED"}:
            # Terminal missions do not reopen on duplicate run.start (§7).
            return mission
        self._run_tokens[mission.mission_id] = cancel
        if invoke is not None:
            # C04/N05: the run's transport is registered for the mission so
            # RECOVER/REVIEW/revision roles share the same real graph.
            self._transports[mission.mission_id] = invoke

        if mission.plan_version == 0:
            if invoke is not None and hasattr(invoke, "bind"):
                invoke.bind(mission_id=mission.mission_id, plan_version=1)
            try:
                proposal = await self._plan(request, mission, invoke=invoke, on_event=on_event)
            except MissionPlanError as exc:
                # A plan that cannot be produced (budget exhausted, invalid
                # submission, unsupported scope) leaves an honest blocker —
                # never a partial plan and never an implicit fallback.
                logger.warning("mission_plan_refused: %s", exc)
                await self._store.record_plan_refusal(
                    mission.mission_id, mission.control_epoch, str(exc)[:400]
                )
                return await self._store.get_mission(mission.mission_id)  # type: ignore[return-value]
            mission = await self._store.commit_plan(
                mission.mission_id, 0, proposal.steps, proposal.success_criteria,
                reason=proposal.explanation[:1000], creator="mission_service",
            )

        mission = await self.run_ready(mission.mission_id, cancel=cancel, on_event=on_event)
        return mission

    # -- planning -------------------------------------------------------------

    async def _plan(
        self,
        request: RequestEnvelope,
        mission: MissionRecord,
        *,
        invoke: Any = None,
        on_event: Any = None,
    ) -> PlanProposal:
        """Deterministic fast plan for exact commands; Deep for everything else."""
        command = parse(request.text)
        if command is not None:
            # One-step fast mission, zero Deep calls, zero JEV calls (§2).
            # R06/F05: a fast action still owes INDEPENDENT recipe-appropriate
            # evidence — no completion claim without a required check.
            recipe_args = {k: str(v) for k, v in command.kwargs.items()
                           if isinstance(v, (str, int))}
            # R06: dictated text travels by REFERENCE and digest, never
            # inside the recipe args or the packet text.
            payload_refs: list[str] = []
            payload_digests: list[str] = []
            if command.recipe == "type_text" and recipe_args.get("text"):
                payload_text = recipe_args.pop("text")
                payload_refs = ["user_text_1"]
                payload_digests = [text_digest(payload_text)]
                await self.put_payload(mission.mission_id, "user_text_1", payload_text)
            # C05/N07: the approved typing path. Typing/setting values is
            # EXTERNAL_WRITE (non-idempotent), so the step — and only then
            # the mission scope — explicitly carries the effect class; the
            # authority still demands an owner-issued approval consumed at
            # the exact dispatch.
            step_effects: set[str] = set()
            if command.recipe in {"type_text", "set_value"}:
                step_effects = {"EXTERNAL_WRITE"}
            step_scope = mission.scope
            if step_effects:
                await self._store.add_permitted_effects(mission.mission_id, step_effects)
                refreshed = await self._store.get_mission(mission.mission_id)
                assert refreshed is not None
                step_scope = refreshed.scope
            step = StepSpec(
                step_id="s1",
                ordinal=1,
                objective=request.text[:2000],
                recipe_id=command.recipe,
                recipe_args=recipe_args,
                payload_refs=payload_refs,
                payload_digests=payload_digests,
                scope=step_scope,
                budget=BudgetLimits(max_wall_ms=90_000, max_actions=12),
                effect_class="EXTERNAL_WRITE" if step_effects else "REPEATABLE_LOCAL",
                checks=_fast_checks_for(command.recipe, recipe_args),
            )
            return PlanProposal(
                steps=[step],
                success_criteria=[],
                explanation="deterministic local command; no planning model used",
            )
        if _looks_like_question(request.text):
            # Information request: Deep answers; no desktop acquisition.
            return PlanProposal(steps=[], success_criteria=[],
                                explanation="information request; answered in chat")
        # R04/RP11: a Deep call is reserved BEFORE the provider invocation;
        # a zero/exhausted budget blocks planning instead of invoking. The
        # reservation is durable, so a failed or crashed call still counts.
        reservation = await self._reserve_deep_call(mission, f"plan-v{mission.plan_version + 1}")
        try:
            proposal = await self._controller.plan(
                request,
                ControllerContext(
                    role="PLAN",
                    scope_summary=f"apps={mission.scope.allowed_apps}",
                    remaining_budget={"deep_calls_left": mission.budget_limits.max_deep_calls},
                    context_text=request.text[:2000],
                ),
                invoke=invoke,
            )
        except BaseException:
            # A failed/cancelled invocation still consumed its reservation:
            # no fresh allowance appears after an error or a crash.
            await self._authority.settle(reservation, consumed=True)
            raise
        self._deep_calls += proposal.deep_calls_used
        # C04/N08 (NP10): the reservation settles ONCE — settle_reservation
        # already moves reserved -> consumed. Adding the unit again here
        # double-charged every planning invocation.
        await self._authority.settle(reservation, consumed=True)
        if on_event is not None and proposal.deep_calls_used:
            await on_event("agent.progress", {"message": "Planned the mission"})
        return self._validate_proposal(proposal.payload, mission)

    async def _reserve_deep_call(self, mission: MissionRecord, call_key: str) -> Any:
        """Reserve one Deep call durably; zero/exhausted budget is a blocker."""
        from assistant.missions.authority import AuthorityDenied
        from assistant.missions.contracts import BudgetCharge

        try:
            return await self._authority.reserve(
                mission.mission_id,
                BudgetCharge(resource="deep_calls", amount=1, call_key=call_key),
            )
        except AuthorityDenied as exc:
            raise MissionPlanError(f"Deep budget exhausted before planning: {exc.reason}") from exc

    def _validate_proposal(self, proposal: PlanProposal, mission: MissionRecord) -> PlanProposal:
        """Deep proposed, the deterministic layer disposes (§5)."""
        if len(proposal.steps) > 20:
            raise MissionPlanError("proposed plan exceeds 20 steps")
        mission_scope = mission.scope
        for step in proposal.steps:
            # R03: the hash is recomputed on construction, so equality alone
            # is sound again — but containment is the real check: a step may
            # narrow the mission scope, never broaden it.
            if step.scope.scope_hash != mission_scope.scope_hash and (
                not mission_scope.contains(step.scope)
            ):
                raise MissionPlanError(f"step {step.step_id} exceeds the mission scope")
            if step.budget.max_actions > mission.budget_limits.max_actions:
                raise MissionPlanError(f"step {step.step_id} asks for more actions than allowed")
            if step.recipe_id not in RECIPE_TOOL_CATALOG:
                raise MissionPlanError(f"recipe {step.recipe_id} is not in the trusted catalog")
            if step.effect_class == "DESTRUCTIVE":
                raise MissionPlanError("destructive effects are denied in Phase 1")
            # C05/N07: no actionable step may declare completion without a
            # meaningful required check — an empty required set would make
            # the store's completeness gate vacuous. Every recipe in the
            # trusted catalog is an action recipe, so every step owes a
            # required check (information-only plans carry no steps at all).
            if step.recipe_id in RECIPE_TOOL_CATALOG and not any(
                c.required for c in step.checks
            ):
                raise MissionPlanError(
                    f"step {step.step_id} ({step.recipe_id}) proposes no required "
                    "independent check; unverifiable outcomes stay blocked"
                )
        return proposal

    # -- execution ------------------------------------------------------------

    async def run_ready(
        self, mission_id: str, *, cancel: CancellationToken, on_event: Any = None
    ) -> MissionRecord:
        """Run every dependency-ready step until the plan is exhausted."""
        mission = await self._store.get_mission(mission_id)
        if mission is None:
            raise MissionStoreError(f"unknown mission: {mission_id}")
        while not cancel.is_cancelled:
            mission = await self._store.get_mission(mission_id)
            assert mission is not None
            if mission.status in {"COMPLETED", "FAILED", "CANCELLED", "PAUSED",
                                  "BLOCKED", "NEEDS_APPROVAL", "VERIFYING"}:
                break
            # R05/F06 (RP12): the desktop lease is acquired BEFORE the claim
            # and the claim records its real fence + generation, so two
            # desktop steps can never be active at once and a stale packet
            # carries no authority.
            lease = await self._desktop_queue.acquire(f"mission:{mission_id}")
            try:
                item = await self._store.claim_step(
                    mission_id,
                    mission.plan_version,
                    mission.control_epoch,
                    tool_ids=[],
                    # C05/N07 (NP08): the CLAIMED step's recipe selects its
                    # own catalog inside the claim transaction.
                    tool_catalog=RECIPE_TOOL_CATALOG,
                    lease_fence=lease.fence,
                    driver_generation=self._host_generation,
                )
                if item is None:
                    break
                result = await self._executor.execute_work_item(
                    item, cancel=cancel, on_event=on_event
                )
                outcome = await self._store.apply_result(result)
            finally:
                await self._desktop_queue.release(f"mission:{mission_id}")
            if outcome == "STALE":
                break
            if result.status in {"NEEDS_CONTROLLER", "NEEDS_HUMAN"}:
                mission = await self._escalate(mission_id, item, result, on_event=on_event)
                if mission.status in {"BLOCKED", "NEEDS_APPROVAL", "PAUSED", "CANCELLED"}:
                    break
        mission = await self._store.get_mission(mission_id)
        assert mission is not None
        if mission.status == "PLANNED" and not mission.steps and not cancel.is_cancelled:
            # A plan that needs no steps (an answered question) verifies and
            # completes through the same deterministic gate.
            mission = await self._store.mark_verifying(mission.mission_id, mission.control_epoch)
        if mission.status == "VERIFYING" and not cancel.is_cancelled:
            mission = await self._finalize(mission)
        await self._observe(mission)
        return mission

    async def _observe(self, mission: MissionRecord) -> None:
        """Run the read-only Observer over this mission's committed events."""
        try:
            events = await self._store.get_events(mission.mission_id)
            await self._observer.analyze(events)
        except Exception as exc:  # noqa: BLE001 -- observation never blocks execution
            logger.warning("mission_observer_failed: %s", exc)

    def _ownership_probe(self, mission_id: str) -> tuple[str, int] | None:
        """The CURRENT desktop-queue ownership for one mission (C06/N09)."""
        owner = f"mission:{mission_id}"
        queue = self._desktop_queue
        if queue.current_owner == owner:
            return (queue.owner_fence, queue.generation)
        return None

    async def put_payload(self, mission_id: str, ref: str, content: str) -> str:
        """Screen and store exact user text for dispatch-time resolution."""
        return await self._store.put_payload(mission_id, ref, content)

    def _resolve_payloads(self, item: Any, refs: list[str]) -> dict[str, str]:
        """Resolve payload refs to exact stored text at dispatch time."""
        resolved: dict[str, str] = {}
        for ref in refs:
            content = self._store.get_payload_sync(item.mission_id, ref)
            if content is not None:
                resolved[ref] = content
        return resolved

    def _tool_ids_for(self, record: MissionRecord) -> list[str]:
        """The selected step's own tool catalog (R06: per-step, not
        mission-wide) - claimed steps may name different recipes."""
        for step in record.steps:
            if step.recipe_id != "semantic_ui":
                return RECIPE_TOOL_CATALOG.get(step.recipe_id, ["list_apps"])
        return RECIPE_TOOL_CATALOG["semantic_ui"]

    def _recipe_of(self, record: MissionRecord) -> str:
        """The dominant recipe of the current plan (single-recipe plans in P1)."""
        for step in record.steps:
            if step.recipe_id != "semantic_ui":
                return step.recipe_id
        return "semantic_ui"

    async def _escalate(
        self, mission_id: str, item: Any, result: StepResult, *, on_event: Any = None
    ) -> MissionRecord:
        """One bounded recovery, then an honest stop (§7)."""
        from assistant.missions.executor import exception_packet_for

        mission = await self._store.get_mission(mission_id)
        assert mission is not None
        packet = exception_packet_for(item, result)
        if result.status == "NEEDS_HUMAN":
            if on_event is not None:
                await on_event("agent.progress", {"message": "Needs your approval to continue"})
            await self._store.control(MissionControl(
                control_id=new_id(), mission_id=mission_id,
                expected_plan_version=mission.plan_version,
                expected_control_epoch=mission.control_epoch,
                kind="PAUSE", reason=f"NEEDS_HUMAN: {packet.category}",
            ))
            return await self._store.get_mission(mission_id)  # type: ignore[return-value]
        # NEEDS_CONTROLLER: ask the Controller for one bounded recovery.
        # C04/N05 (NP04): recovery is a real RECOVER-role invocation over the
        # mission-bound transport, with its Deep call reserved first.
        snapshot = _snapshot_of(mission)
        try:
            recovery = await self._controller_invoke(
                "recover",
                mission,
                snapshot=snapshot,
                packet=packet,
                on_event=on_event,
            )
        except Exception as exc:  # noqa: BLE001 -- a failed recovery is an owner stop
            logger.warning("mission_recovery_failed: %s", exc)
            await self._store.control(MissionControl(
                control_id=new_id(), mission_id=mission_id,
                expected_plan_version=mission.plan_version,
                expected_control_epoch=mission.control_epoch,
                kind="PAUSE", reason=f"recovery unavailable: {str(exc)[:200]}",
            ))
            return await self._store.get_mission(mission_id)  # type: ignore[return-value]
        decision = recovery.payload
        if decision.decision == "RETRY_SAFE" and result.failure_category in {
            "TRANSPORT_LOST", "STALE_TARGET", "NO_PROGRESS"
        }:
            # Read-only or proven-safe retries re-claim the step with a fresh
            # observation; anything ambiguous stays blocked (§7 table).
            return await self._store.get_mission(mission_id)  # type: ignore[return-value]
        reason = f"{decision.decision}: {decision.reason[:200]}"
        kind = "CANCEL" if decision.decision == "FAIL" else "PAUSE"
        try:
            await self._store.control(MissionControl(
                control_id=new_id(), mission_id=mission_id,
                expected_plan_version=mission.plan_version,
                expected_control_epoch=mission.control_epoch,
                kind=kind,  # type: ignore[arg-type]
                reason=reason,
            ))
        except StaleControlError:
            pass
        return await self._store.get_mission(mission_id)  # type: ignore[return-value]

    async def _controller_invoke(
        self,
        kind: str,
        mission: MissionRecord,
        *,
        snapshot: Any = None,
        packet: Any = None,
        checks: list[Any] | None = None,
        request: Any = None,
        on_event: Any = None,
        **prompt_context: Any,
    ) -> Any:
        """One reserved, transport-bound Controller call of any role.

        C04/N05: PLAN, RECOVER and REVIEW all go through the same
        mission/version-bound Deep transport, with the Deep call reserved
        durably BEFORE the invocation and settled exactly once after it
        (a failed call still consumed its allowance).
        """
        call_key = f"{kind}-v{mission.plan_version}:{new_id()[:8]}"
        reservation = await self._reserve_deep_call(mission, call_key)
        try:
            transport = self._transport_for(mission, on_event=on_event)
            if kind == "plan":
                assert request is not None
                result = await self._controller.plan(
                    request,
                    ControllerContext(
                        role="PLAN",
                        scope_summary=f"apps={mission.scope.allowed_apps}",
                        remaining_budget={"deep_calls_left": mission.budget_limits.max_deep_calls},
                        context_text=f"revision of: {mission.original_goal[:500]}",
                    ),
                    invoke=transport,
                )
            elif kind == "recover":
                assert snapshot is not None and packet is not None
                result = await self._controller.recover(snapshot, packet, invoke=transport)
            elif kind == "review":
                result = await self._controller.review(
                    snapshot, checks or [], invoke=transport
                )
            else:  # pragma: no cover - kind is internal
                raise MissionPlanError(f"unknown controller invocation {kind}")
        except BaseException:
            await self._authority.settle(reservation, consumed=True)
            raise
        self._deep_calls += result.deep_calls_used
        await self._authority.settle(reservation, consumed=True)
        return result

    def _transport_for(self, mission: MissionRecord, *, on_event: Any = None) -> Any:
        """The mission-bound Deep transport, re-derived when detached."""
        invoke = self._transports.get(mission.mission_id)
        if invoke is not None and hasattr(invoke, "bind"):
            invoke.bind(mission_id=mission.mission_id, plan_version=mission.plan_version)
            return invoke
        if self._transport_factory is not None:
            invoke = self._transport_factory(mission)
            self._transports[mission.mission_id] = invoke
            if hasattr(invoke, "bind"):
                invoke.bind(mission_id=mission.mission_id, plan_version=mission.plan_version)
            return invoke
        raise RuntimeError(
            "no Deep transport is wired into the controller for this mission"
        )

    # -- acceptance gate --------------------------------------------------------

    async def _finalize(self, mission: MissionRecord) -> MissionRecord:
        """The deterministic gate: COMPLETED only with every required check.

        C04/N05: the final REVIEW role runs here, advisory only — it may
        name unresolved acceptance issues in the trace, but the terminal
        status comes from the deterministic check gate alone. Review text
        never grants terminal success.
        """
        steps = {s.step_id: s for s in mission.steps}
        states = await self._store.get_step_states(mission.mission_id, mission.plan_version)
        required_steps = [sid for sid, spec in steps.items() if not spec.optional]
        all_required_succeeded = all(states.get(sid) == "SUCCEEDED" for sid in required_steps)
        # Required success criteria need CheckResults attached to step results.
        criteria_checks = await self._store.get_check_results(mission.mission_id,
             mission.plan_version)
        criteria_ok = True
        for criterion in mission.success_criteria:
            if not criterion.required:
                continue
            result = criteria_checks.get(criterion.check_id)
            if result is None or not result.passed:
                criteria_ok = False
                break
        final_status: str
        if all_required_succeeded and criteria_ok:
            final_status = "COMPLETED"
        elif any(states.get(sid) == "FAILED" for sid in required_steps):
            final_status = "FAILED"
        else:
            final_status = "BLOCKED"
        final_status = await self._final_review(mission, final_status)
        return await self._store.finalize_mission(
            mission.mission_id, mission.control_epoch, final_status
        )

    async def _final_review(self, mission: MissionRecord, final_status: str) -> str:
        """The advisory REVIEW pass, recorded but never decisive."""
        if not mission.steps or mission.budget_usage.total("deep_calls") == 0:
            return final_status
        criteria_checks = await self._store.get_check_results(
            mission.mission_id, mission.plan_version
        )
        try:
            result = await self._controller_invoke(
                "review",
                mission,
                snapshot=_snapshot_of(mission),
                checks=list(criteria_checks.values()),
            )
        except Exception as exc:  # noqa: BLE001 -- advisory review never blocks the gate
            logger.warning("mission_final_review_unavailable: %s", exc)
            return final_status
        review = result.payload
        unresolved = list(getattr(review, "unresolved_issues", []) or [])
        await self._store.record_final_review(
            mission.mission_id, mission.control_epoch,
            summary=str(getattr(review, "summary", ""))[:400],
            unresolved_issues=[str(u)[:200] for u in unresolved][:8],
        )
        # Advisory only: a review claiming problems cannot flip a passing
        # gate, and a review claiming success cannot flip a failing one.
        return final_status

    # -- control surface -----------------------------------------------------------

    async def control(self, command: MissionControl) -> MissionRecord:
        """CAS control that also signals the live executor token (R07/F07)."""
        mission = await self._store.control(command)
        token: CancellationToken | None = self._run_tokens.get(command.mission_id)
        if token is not None and command.kind in {"PAUSE", "CANCEL", "REVISE"}:
            token.cancel()
        if command.kind == "REVISE" and command.revision_request:
            mission = await self._replan(
                mission, revision=command.revision_request, reason=command.reason
            )
        if command.kind == "RESUME":
            # C03/N05: resume is not a database flip. Uncertain dispatched
            # attempts settle FIRST (an unknown external effect is never
            # replayed), and only a genuinely runnable mission is scheduled
            # with a FRESH execution token — the old one died with the pause.
            mission = await self._reconcile_uncertain(command.mission_id)
            if mission is not None and mission.status == "RUNNING":
                # C05: the approved typing loop — a step refused for
                # APPROVAL_REQUIRED re-queues only when a fresh owner
                # approval exists for this epoch (bounded by retries).
                released = await self._store.release_approved_blocked_steps(
                    command.mission_id, mission.plan_version
                )
                states = await self._store.get_step_states(
                    command.mission_id, mission.plan_version
                )
                if released or "PENDING" in states.values():
                    self._schedule_resume(command.mission_id)
        return mission

    async def reconcile_startup(self, execution_ids: list[str]) -> None:
        """Consume startup reconciliation ids through the real reconciler.

        C03/N05: ``recover_inflight`` marks uncertain attempts and returns
        their ids; this settles each one honestly (steps released only on
        proven NO_EFFECT, missions BLOCKED on confirmed/unknown effects).
        Attempts left RECONCILING by a pause or an earlier crash settle here
        too. Startup never replays GUI work.
        """
        mission_ids: set[str] = set()
        for execution_id in execution_ids:
            attempt = await self._store.get_attempt(execution_id)
            if attempt is not None:
                mission_ids.add(str(attempt["mission_id"]))
        mission_ids.update(await self._store.missions_with_reconciling())
        for mission_id in sorted(mission_ids):
            try:
                await self._reconcile_uncertain(mission_id)
            except Exception as exc:  # noqa: BLE001 -- one bad attempt must not wedge startup
                logger.warning("startup_reconciliation_failed mission=%s: %s", mission_id, exc)

    async def _reconcile_uncertain(
        self, mission_id: str, *, probe: Any = None
    ) -> MissionRecord:
        """Settle every RECONCILING attempt of one mission, honestly.

        Each attempt's external operations are reconciled (all IDs, per
        C03/N02); a proven NO_EFFECT plus remaining retry allowance returns
        the step to PENDING; anything confirmed or unknown blocks the step
        and the mission — an ambiguous effect can never dispatch twice.
        """
        from assistant.missions.recovery import Reconciler

        attempts = await self._store.reconciling_attempts(mission_id)
        if not attempts:
            return await self._store.get_mission(mission_id)  # type: ignore[return-value]
        reconciler = Reconciler(self._store, probe=probe)
        blocked_reason = ""
        for attempt in attempts:
            reconciliation = await reconciler.reconcile(attempt["execution_id"])
            await self._store.mark_attempt_reconciled(
                attempt["execution_id"], reconciliation.outcome
            )
            if reconciliation.retriable:
                released = await self._store.release_step_for_retry(
                    mission_id, attempt["plan_version"], attempt["step_id"]
                )
                if not released:
                    blocked_reason = (
                        f"step {attempt['step_id']}: proven NO_EFFECT but the "
                        "retry allowance is exhausted"
                    )
            else:
                blocked_reason = (
                    f"step {attempt['step_id']}: effect {reconciliation.outcome} "
                    f"({reconciliation.reason})"
                )
        if blocked_reason:
            return await self._store.mark_mission_blocked(mission_id, blocked_reason)
        return await self._store.get_mission(mission_id)  # type: ignore[return-value]

    def _schedule_resume(self, mission_id: str) -> None:
        """Run ready work under a fresh token (the paused one is dead)."""
        import asyncio

        token = CancellationToken()
        self._run_tokens[mission_id] = token

        async def _resume() -> None:
            try:
                await self.run_ready(mission_id, cancel=token)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- a detached resume reports honestly
                logger.warning("mission_resume_failed: %s", exc)
                with contextlib.suppress(Exception):
                    await self._store.mark_mission_blocked(
                        mission_id, f"resume failed: {str(exc)[:200]}"
                    )
            finally:
                if self._run_tokens.get(mission_id) is token:
                    self._run_tokens.pop(mission_id, None)

        asyncio.get_running_loop().create_task(
            _resume(), name=f"mission-resume-{mission_id[:8]}"
        )

    async def _replan(self, mission: MissionRecord, *, revision: str, reason: str) -> MissionRecord:
        """A revision creates a NEW versioned plan from the Controller.

        C03/N05: the old plan's uncertain attempts settle BEFORE a new
        version is committed — a revision never erases effect uncertainty —
        and the new plan is validated before it can run (an invalid revision
        leaves the previous state untouched and the refusal recorded).
        """
        reconciled = await self._reconcile_uncertain(mission.mission_id)
        if reconciled.status == "BLOCKED":
            return reconciled
        refreshed = await self._store.get_mission(mission.mission_id)
        assert refreshed is not None
        mission = refreshed
        request = RequestEnvelope(
            request_id=new_id(),
            conversation_id=mission.conversation_id,
            owner_id=mission.owner_id,
            input_origin="typed_final",
            input_revision=mission.plan_version + 1,
            text=revision[:16000],
            submitted_at_ms=int(time.time() * 1000),
        )
        proposal = await self._controller_invoke(
            "plan",
            mission,
            invoke_kind="replan",
            revision_request=revision,
            reason=reason,
            request=request,
        )
        validated = self._validate_proposal(proposal.payload, mission)
        mission = await self._store.commit_plan(
            mission.mission_id,
            mission.plan_version,
            validated.steps,
            validated.success_criteria,
            reason=f"revision: {reason[:200]}",
            creator="revision",
        )
        return mission

    async def get(self, mission_id: str) -> MissionRecord | None:
        return await self._store.get_mission(mission_id)

    async def list(self, *, status: str | None = None, limit: int = 50) -> list[MissionRecord]:
        return await self._store.list_missions(owner_id=self._owner_id, status=status, limit=limit)

    async def accept_result(self, result: StepResult) -> Literal["APPLIED", "DUPLICATE", "STALE"]:
        return await self._store.apply_result(result)

    @property
    def store(self) -> MissionStore:
        """The durable store, for the mission.events IPC surface."""
        return self._store

    async def issue_approval(self, approval: ApprovalRecord) -> None:
        """The trusted host mints approvals; nothing else can."""
        await self._store.record_approval(approval)
        self._authority.register_approvals(
            approval.mission_id, approval.control_epoch, [approval.approval_id]
        )

    # -- configuration ------------------------------------------------------------

    def _scope_for(self, request: RequestEnvelope) -> Scope:
        # C02/N04: the trusted scope source is host configuration (settings),
        # never a model output or a nonexistent field. An empty list leaves
        # every app out of scope: actions on an identified surface refuse.
        allowed = list(getattr(self._settings, "mission_allowed_apps", []) or [])
        return Scope(
            owner_id=request.owner_id or self._owner_id,
            allowed_apps=allowed,
            permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"},
        )

    def _limits_for(self, request: RequestEnvelope) -> BudgetLimits:
        return BudgetLimits()  # reviewed defaults from file 03 §8

    @property
    def component_counters(self) -> dict[str, int]:
        """Cost/latency evidence per component (§8 metering)."""
        return {"deep_calls": self._deep_calls, "jev_calls": self._jev_calls}


_POLITE_PREFIXES = (
    "can you please", "could you please", "can you", "could you", "would you",
    "will you", "please", "kindly",
)

_ACTION_VERB_PREFIXES = (
    "open ", "close ", "search", "look up", "type ", "click ", "scroll ",
    "press ", "launch ", "go to ", "navigate ", "fill ", "submit ",
)


def _strip_politeness(text: str) -> str:
    """Remove leading politeness so "can you open safari" is an action."""
    stripped = text.strip()
    lowered = stripped.lower()
    changed = True
    while changed:
        changed = False
        for prefix in _POLITE_PREFIXES:
            if lowered.startswith(prefix + " "):
                stripped = stripped[len(prefix) + 1 :]
                lowered = stripped.lower()
                changed = True
                break
    return stripped


def _fast_checks_for(recipe_id: str, recipe_args: dict[str, str]) -> list[CheckSpec]:
    """Independent, recipe-appropriate required checks for fast missions.

    C05/N07 (NP06): EVERY mutating recipe owes at least one deterministic,
    zero-model check from the trusted catalog — an open that never reaches
    the foreground, a navigation whose markers never appear, a typing whose
    field never carries the payload cannot complete.
    """
    version = "1.0.0"

    def _check(check_id: str, verifier_id: str, expected: dict[str, Any]) -> CheckSpec:
        return CheckSpec(
            check_id=check_id,
            verifier_id=verifier_id,
            verifier_version=version,
            expected=expected,
            required=True,
        )

    if recipe_id == "open_app" and recipe_args.get("app_name"):
        app = str(recipe_args["app_name"]).lower()
        return [_check(f"app-foreground-{app}", "app_foreground", {"app": app})]
    if recipe_id == "navigate" and recipe_args.get("destination"):
        destination = str(recipe_args["destination"])
        markers = [
            token
            for token in _url_markers(destination)
            if token
        ]
        if markers:
            return [_check("page-shows-destination", "page_state", {"markers": markers[:4]})]
    if recipe_id == "search_browser" and recipe_args.get("query"):
        query = str(recipe_args["query"]).lower()
        markers = [w for w in query.split() if w][:4]
        if markers:
            return [_check("page-shows-query", "page_state", {"markers": markers})]
    if recipe_id == "type_text":
        # The payload itself never enters the check spec: the verifier
        # compares the observed field value against the vault-resolved text.
        return [_check("field-carries-payload", "field_value", {"payload_ref": "user_text_1"})]
    if recipe_id in {"scroll", "press_ordinal"}:
        return [_check("window-reobserved", "window_visible", {})]
    return []


def looks_like_question(text: str) -> bool:
    """A question is an information request: no desktop probing to answer it."""
    stripped = _strip_politeness(text).lower()
    if stripped.endswith("?"):
        return True
    return any(stripped.startswith(prefix + " ") for prefix in _QUESTION_PREFIXES)


def action_shaped(text: str) -> bool:
    """An explicit action verb overrides the question heuristic (F02)."""
    stripped = _strip_politeness(text).lower()
    return any(stripped.startswith(verb) for verb in _ACTION_VERB_PREFIXES)


# Backward-compatible aliases for the pre-R02 private names.
_looks_like_question = looks_like_question


def _snapshot_of(mission: MissionRecord) -> Any:
    from assistant.missions.contracts import MissionSnapshot

    return MissionSnapshot(
        mission_id=mission.mission_id,
        status=mission.status,
        plan_version=mission.plan_version,
        control_epoch=mission.control_epoch,
        original_goal=mission.original_goal,
        steps=list(mission.steps),
        remaining_budget=mission.budget_usage,
    )


__all__ = [
    "MissionPlanError",
    "MissionService",
    "RECIPE_TOOL_CATALOG",
]
