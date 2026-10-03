"""The Velo bounded executor: one work item in, one honest StepResult out.

``execute_work_item`` is the ONLY way a mission reaches the desktop (file 03
§5). Inside one item:

- known recipes run exactly as the fast path runs them — same recipes, same
  policy-wrapped adapter, zero model calls;
- ``semantic_ui`` runs a bounded interpreter over trusted primitives
  (observe, choose, activate, set exact payload, verify). Candidates come
  from the fresh accessibility tree only; JEV, when enabled, picks one
  candidate ID and can invent nothing. No generated code, no arbitrary tool
  names, no nested planning;
- the unfamiliar route inside a unit returns NEEDS_CONTROLLER — it never
  calls Deep recursively. General work escapes to the Controller through
  the structured exception, not through another agent loop;
- outcomes are typed: COMPLETED means this unit's registered postconditions
  passed through a trusted verifier; an UNKNOWN effect is a first-class
  blocker and never a success.

Every mutating dispatch passes the mission authority guard installed by
:meth:`VeloExecutor.work_scope` — no permit, no effect (T03).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from assistant.missions.authority import AuthorityDenied, MissionAuthority
from assistant.missions.contracts import (
    ActionIntent,
    BoundedWorkItem,
    BudgetUsage,
    CancellationToken,
    CheckResult,
    ExceptionPacket,
    ScopeObservation,
    StepResult,
    new_id,
    text_digest,
)
from assistant.missions.evidence import EvidenceStore
from assistant.velo.adapter import AdapterContractError, CuaAdapter
from assistant.velo.contracts import (
    Candidate,
    CandidateKind,
    DecisionStatus,
    JevDecisionRequest,
    JevServiceError,
    OutcomeState,
    Route,
    TaskCancelled,
    TaskState,
)
from assistant.velo.recipes import RecipeResult, execute

logger = logging.getLogger("assistant.missions.executor")

#: Recipes a work item may name. Everything else is UNSUPPORTED_ACTION.
SUPPORTED_RECIPES = frozenset(
    {"open_app", "navigate", "search_browser", "scroll", "type_text", "press_ordinal",
         "semantic_ui"}
)

#: The trusted primitive tools semantic_ui may dispatch, by purpose.
SEMANTIC_UI_TOOLS = (
    "list_apps", "list_windows", "get_window_state", "click", "type_text",
    "set_value", "press_key", "scroll", "bring_to_front",
)

MAX_SEMANTIC_CANDIDATES = 8

OnEvent = Callable[[str, dict[str, Any]], Awaitable[None]]

#: The item under execution, readable by helpers inside the dispatch scope.
_current_item: ContextVar[BoundedWorkItem | None] = ContextVar(
    "mission_current_item", default=None
)
_current_cancel: ContextVar[CancellationToken | None] = ContextVar(
    "mission_current_cancel", default=None
)

#: Payload refs resolve exact user text at dispatch; the resolver is injected
#: by the service so the packet itself never carries payload content.
PayloadResolver = Callable[[BoundedWorkItem, list[str]], dict[str, str]]

#: D02: set when a dispatch is refused with APPROVAL_REQUIRED. NOTE: the
#: policy wrapper may run a tool coroutine in a child task (LangChain tool
#: invocation), so a ContextVar set at the guard does not reach the result
#: assembly. The pending approval therefore travels on the executor keyed by
#: execution id (``_pending_approval_by_exec``), which the same-thread guard
#: writes and ``execute_work_item`` reads and clears.


@dataclass
class ExecutorOutcome:
    """Internal result before the StepResult is assembled."""

    status: str  # ExecutionStatus value
    effect_outcome: str  # EffectOutcome value
    failure_category: str | None = None
    uncertainty: str = ""
    answer: str = ""
    external_operation_ids: list[str] | None = None
    suggested_next_action: str | None = None


class VeloExecutor:
    """Runs one BoundedWorkItem against the desktop through Velo's machinery."""

    def __init__(
        self,
        settings: Any,
        *,
        get_runtime: Callable[[], Awaitable[Any]],
        authority: MissionAuthority | None = None,
        evidence: EvidenceStore | None = None,
        jev_factory: Callable[[], Any] | None = None,
        payload_resolver: PayloadResolver | None = None,
        store: Any = None,
        ownership_probe: Callable[[str], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._get_runtime = get_runtime
        self._authority = authority
        self._evidence = evidence
        self._jev_factory = jev_factory
        self._payload_resolver = payload_resolver
        self._store = store
        # C06/N09: reads the CURRENT desktop-queue ownership (fence,
        # generation) for a mission at dispatch time — never a stored copy.
        self._ownership_probe = ownership_probe
        # D02: the owed approval of the execution currently refusing, keyed
        # by execution id (see the ContextVar note above for why not one).
        self._pending_approval_by_exec: dict[str, dict[str, Any]] = {}

    # -- dispatch scope ------------------------------------------------------------

    @asynccontextmanager
    async def work_scope(
        self, item: BoundedWorkItem, *, cancel: CancellationToken
    ) -> Any:
        """Bind the mission guards + policy scope for exactly one work item.

        The authority guard turns every mutating wrapped-tool call into an
        authorize() request; a denial is the refusal the executor sees, and
        the effect sink stays untouched.
        """
        runtime = await self._get_runtime()
        mission_token = _current_item.set(item)

        async def guard(tool: str, kwargs: dict[str, Any]) -> str | None:
            return await self._guard_dispatch(item, tool, kwargs, cancel)

        from assistant.missions.store import MissionActionLedger

        # R04/F04: the per-action intent ledger is bound to this execution;
        # the strict policy gate refuses mutations when it is missing.
        ledger = (
            MissionActionLedger(
                self._store, execution_id=item.execution_id, mission_id=item.mission_id
            )
            if self._store is not None
            else None
        )
        from assistant.tools.policy import mission_final_dispatch_check

        def final_check() -> str | None:
            if cancel.is_cancelled:
                return "Refused: CANCELLED: the mission was cancelled"
            if time.time() * 1000 >= item.deadline_at_ms:
                return "Refused: DEADLINE: the work item expired"
            if self._ownership_probe is not None and item.lease_fence:
                current = self._ownership_probe(item.mission_id)
                if inspect.isawaitable(current):
                    if inspect.iscoroutine(current):
                        current.close()
                    return "Refused: STALE_TARGET: final ownership check must be synchronous"
                fence = current[0] if isinstance(current, tuple) else current
                if fence != item.lease_fence:
                    return "Refused: STALE_TARGET: desktop ownership changed"
            return None

        cancel_token = _current_cancel.set(cancel)
        final_token = mission_final_dispatch_check.set(final_check)
        try:
            async with runtime.run_scope(
                f"mission-{item.execution_id[:8]}", item.mission_id, ledger=ledger,
                mission_guard=guard, mission_strict_audit=True,
                mission_withhold_screenshots=True, max_actions=min(item.budget.max_actions, 12),
            ):
                yield runtime
        finally:
            mission_final_dispatch_check.reset(final_token)
            _current_cancel.reset(cancel_token)
            _current_item.reset(mission_token)

    # -- entry ---------------------------------------------------------------------

    async def execute_work_item(
        self,
        item: BoundedWorkItem,
        *,
        cancel: CancellationToken,
        on_event: OnEvent | None = None,
    ) -> StepResult:
        started = time.monotonic()
        self._started_at_ms = int(time.time() * 1000)

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        usage = BudgetUsage()

        def base_result(status: str, effect_outcome: str, **extra: Any) -> StepResult:
            return StepResult(
                mission_id=item.mission_id,
                plan_version=item.plan_version,
                control_epoch=item.control_epoch,
                step_id=item.step_id,
                execution_id=item.execution_id,
                attempt=item.attempt,
                status=status,  # type: ignore[arg-type]
                effect_outcome=effect_outcome,  # type: ignore[arg-type]
                elapsed_ms=elapsed_ms(),
                usage=usage,
                **extra,
            )

        if cancel.is_cancelled:
            return base_result("CANCELLED", "NOT_ATTEMPTED", failure_category="CANCELLED")
        if item.recipe_id not in SUPPORTED_RECIPES:
            return base_result(
                "NEEDS_CONTROLLER",
                "NOT_ATTEMPTED",
                failure_category="UNSUPPORTED_ACTION",
                uncertainty=f"recipe {item.recipe_id!r} is not a supported unit",
            )
        if time.time() * 1000 > item.deadline_at_ms:
            return base_result("FAILED", "NOT_ATTEMPTED", failure_category="DEADLINE")

        self._pending_approval_by_exec.pop(item.execution_id, None)
        try:
            return await self._execute_dispatched(
                item, cancel, on_event, base_result, usage, elapsed_ms
            )
        finally:
            self._pending_approval_by_exec.pop(item.execution_id, None)

    async def _execute_dispatched(
        self,
        item: BoundedWorkItem,
        cancel: CancellationToken,
        on_event: OnEvent | None,
        base_result: Any,
        usage: BudgetUsage,
        elapsed_ms: Any,
    ) -> StepResult:
        async with self.work_scope(item, cancel=cancel) as runtime:

            async def _event(kind: str, data: dict[str, Any]) -> None:
                if on_event is not None:
                    await on_event(kind, data)

            adapter = CuaAdapter(
                getattr(runtime, "cua_tools", {}),
                on_event=_event,
                cancel_check=lambda: cancel.is_cancelled,
            )
            # C05/N07: preconditions are evaluated BEFORE any dispatch. A
            # failed precondition blocks the unit with zero effects — the
            # action is never sent onto a surface the plan says must not be
            # there.
            if item.preconditions:
                if self._evidence is None:
                    # Fail closed: a precondition the packet carries but no
                    # trusted verifier can evaluate is a blocked unit, never
                    # a silent skip into the action.
                    return base_result(
                        "BLOCKED",
                        "NOT_ATTEMPTED",
                        failure_category="VERIFICATION_FAILED",
                        uncertainty="packet carries preconditions but no evidence "
                        "store is bound to verify them",
                    )
                precondition_failures = await self._evaluate_preconditions(
                    item, adapter, usage
                )
                if precondition_failures:
                    return base_result(
                        "BLOCKED",
                        "NOT_ATTEMPTED",
                        failure_category="VERIFICATION_FAILED",
                        uncertainty=f"precondition failed: {precondition_failures[:480]}",
                    )
            task = self._task_from(item, cancel)
            # D06: recipes whose success is only provable by a before/after
            # diff capture the BEFORE observation now, so the verifier can
            # demand an exact, independent change (scroll offset moved, the
            # focused field now carries the payload, a control's activation
            # state flipped) instead of a re-observed window.
            before_ref = None
            if item.recipe_id in {"type_text", "scroll", "press_ordinal"} and (
                self._evidence is not None
            ):
                before_ref = await self._capture_before_evidence(item, adapter)
            try:
                if item.recipe_id == "semantic_ui":
                    outcome = await self._run_semantic_ui(item, task, adapter, usage)
                else:
                    kwargs = self._recipe_kwargs(item)
                    raw = await execute(item.recipe_id, task, adapter, **kwargs)
                    outcome = self._recipe_outcome(item, raw)
            except TaskCancelled as exc:
                if cancel.is_cancelled:
                    return base_result(
                        "CANCELLED", "NOT_ATTEMPTED",
                        failure_category="CANCELLED", uncertainty=str(exc)[:512],
                    )
                category = "DEADLINE" if "time" in str(exc) else "BUDGET_EXHAUSTED"
                return base_result(
                    "FAILED", "NOT_ATTEMPTED",
                    failure_category=category, uncertainty=str(exc)[:512],
                )
            except AuthorityDenied as exc:
                return base_result(
                    "BLOCKED", "NOT_ATTEMPTED",
                    failure_category=exc.category, uncertainty=exc.reason[:512],
                )
            except AdapterContractError as exc:
                # The step named a tool the installed driver does not expose:
                # an unsupported unit, never a silent skip.
                return base_result(
                    "NEEDS_CONTROLLER", "NOT_ATTEMPTED",
                    failure_category="UNSUPPORTED_ACTION", uncertainty=str(exc)[:512],
                )
            except asyncio.CancelledError:
                # Cancelled mid-dispatch: the effect may have happened.
                raise
            except Exception as exc:  # noqa: BLE001 -- transport/driver failure
                # Conservative: the intent is committed, the effect state is
                # UNKNOWN, and the unit escapes to the Controller (RF-17).
                logger.warning("work_item_transport_failure: %s", exc)
                return base_result(
                    "NEEDS_CONTROLLER", "UNKNOWN",
                    failure_category="TRANSPORT_LOST", uncertainty=str(exc)[:512],
                )

            # D02: an approval-gated refusal is a BLOCKED unit, not an
            # unknown effect — the guard refused BEFORE any dispatch, so no
            # effect is possible. The exact owed approval travels with the
            # result and is persisted with the blocked step for exact
            # release matching.
            pending = self._pending_approval_by_exec.get(item.execution_id)
            if outcome.status != "COMPLETED" and pending is not None:
                from assistant.missions.contracts import PendingApprovalDigest

                return base_result(
                    "BLOCKED", "NOT_ATTEMPTED",
                    failure_category="APPROVAL_REQUIRED",
                    uncertainty=(
                        "owner approval required for the exact dispatch of "
                        f"{pending.get('tool')}"
                    ),
                    pending_approval=PendingApprovalDigest(
                        step_id=item.step_id,
                        tool=str(pending.get("tool") or ""),
                        action_digest=str(pending.get("action_digest") or ""),
                        plan_version=item.plan_version,
                        control_epoch=item.control_epoch,
                        target_ref=pending.get("target_ref"),
                    ),
                )

            # C04/N08: actions/observations are accounted at their real
            # dispatch boundaries via durable reservations — they are NOT
            # self-reported again here (that double-counted every unit).

            # Postcondition verification through the trusted catalog only.
            checks: list[CheckResult] = []
            if item.expected_postconditions and self._evidence is None:
                # D15 fail-closed: the plan demands required independent
                # checks but no trusted verifier is bound — the unit can
                # never claim completion, with zero exceptions.
                required = [c.check_id for c in item.expected_postconditions
                            if c.required]
                if required:
                    return base_result(
                        "BLOCKED", outcome.effect_outcome
                        if outcome.effect_outcome != "CONFIRMED" else "UNKNOWN",
                        failure_category="VERIFICATION_FAILED",
                        uncertainty="no evidence store is bound; required checks "
                                    f"{required} cannot be verified",
                    )
            if self._evidence is not None and item.expected_postconditions:
                checks = await self._verify_postconditions(
                    item, adapter, task, usage, extra_refs=[before_ref] if before_ref else None
                )

            return self._assemble(item, outcome, checks, elapsed_ms(), usage)

    # -- recipe path -------------------------------------------------------------

    def _task_from(self, item: BoundedWorkItem, cancel: CancellationToken) -> TaskState:
        remaining_ms = max(1_000, item.deadline_at_ms - int(time.time() * 1000))
        task = TaskState(
            instruction=item.objective,
            conversation=item.mission_id,
            route=Route.LOCAL,
            max_actions=max(1, min(item.budget.max_actions, 12)),
        )
        task.deadline = time.monotonic() + remaining_ms / 1000
        task.cancelled = cancel.is_cancelled
        payloads = self._resolve_payloads(item)
        for index, (_ref, text) in enumerate(sorted(payloads.items()), start=1):
            task.payloads[f"user_text_{index}"] = text
        return task

    def _resolve_payloads(self, item: BoundedWorkItem) -> dict[str, str]:
        if not item.payload_refs or self._payload_resolver is None:
            return {}
        resolved = self._payload_resolver(item, item.payload_refs)
        return {
            ref: text
            for ref, text in resolved.items()
            if text_digest(text) in item.allowed_action_scope.payload_digests
            or not item.allowed_action_scope.payload_digests
        }

    def _recipe_kwargs(self, item: BoundedWorkItem) -> dict[str, Any]:
        """Recipe arguments from the packet's plan data, never from a model."""
        payloads = self._resolve_payloads(item)
        payload_text = next(iter(payloads.values()), "")
        kwargs: dict[str, Any] = dict(item.recipe_args)
        if item.recipe_id == "type_text" and payload_text:
            kwargs["text"] = payload_text
        return kwargs

    def _recipe_outcome(self, item: BoundedWorkItem, raw: RecipeResult) -> ExecutorOutcome:
        if raw.state is OutcomeState.CONFIRMED:
            return ExecutorOutcome("COMPLETED", "CONFIRMED", answer=raw.answer)
        if raw.state is OutcomeState.NO_EFFECT:
            return ExecutorOutcome("COMPLETED", "NO_EFFECT", answer=raw.answer)
        if raw.state is OutcomeState.CANCELLED:
            return ExecutorOutcome(
                "CANCELLED", "NOT_ATTEMPTED", failure_category="CANCELLED", answer=raw.answer
            )
        if raw.state is OutcomeState.UNKNOWN:
            return ExecutorOutcome(
                "FAILED",
                "UNKNOWN",
                failure_category="UNKNOWN_EFFECT",
                uncertainty=raw.answer[:512],
            )
        return ExecutorOutcome(
            "FAILED", "NOT_ATTEMPTED", failure_category="UNSUPPORTED_ACTION", answer=raw.answer
        )

    # -- semantic_ui path -----------------------------------------------------------

    async def _run_semantic_ui(
        self,
        item: BoundedWorkItem,
        task: TaskState,
        adapter: CuaAdapter,
        usage: BudgetUsage,
    ) -> ExecutorOutcome:
        """Bounded interpreter: observe, choose, act with exact payload, verify."""
        target = await self._observe_target(item, task, adapter)
        if target is None:
            return ExecutorOutcome(
                "NEEDS_CONTROLLER",
                "NOT_ATTEMPTED",
                failure_category="STALE_TARGET",
                uncertainty="no usable window matched the mission scope",
            )
        pid, window_id = target
        state = await adapter.observe_window(task, pid, window_id)
        elements = [e for e in (state.get("elements") or []) if isinstance(e, dict)]
        candidates = self._build_candidates(item, elements)
        if not candidates:
            return ExecutorOutcome(
                "NEEDS_CONTROLLER",
                "NOT_ATTEMPTED",
                failure_category="UNSUPPORTED_ACTION",
                uncertainty="no supported candidate control found for the objective",
            )
        chosen = await self._choose_candidate(item, candidates)
        if chosen is None:
            return ExecutorOutcome(
                "NEEDS_HUMAN",
                "NOT_ATTEMPTED",
                failure_category="APPROVAL_REQUIRED",
                uncertainty="candidate choice could not be made mechanically",
            )
        effect = "READ_ONLY" if item.effect_class == "READ_ONLY" else item.effect_class
        payload_text = next(iter(self._resolve_payloads(item).values()), "")
        try:
            is_field = chosen["role"].lower() in {
                "axtextfield", "axtextarea", "axcombobox", "axsearchfield"
            }
            if is_field and payload_text:
                reply = await adapter.acting_call(
                    task, "set_value",
                    pid=pid, window_id=window_id,
                    element_token=chosen["element_token"], value=payload_text,
                )
                if not reply.ok:
                    # R06/F05 (RP04): a refused action is NEVER a completed
                    # unit. The refusal text is the uncertainty; the gate
                    # decides.
                    return ExecutorOutcome(
                        "FAILED",
                        "UNKNOWN",
                        failure_category="PERMISSION_DENIED",
                        uncertainty=f"set_value refused: {reply.text[:200]}",
                    )
                task.used_actions += 1
            if effect != "READ_ONLY" or not is_field:
                reply = await adapter.acting_call(
                    task, "click",
                    pid=pid, window_id=window_id,
                    element_token=chosen["element_token"],
                )
                if not reply.ok:
                    return ExecutorOutcome(
                        "FAILED",
                        "UNKNOWN",
                        failure_category="PERMISSION_DENIED",
                        uncertainty=f"click refused: {reply.text[:200]}",
                    )
                task.used_actions += 1
        except TaskCancelled:
            raise
        return ExecutorOutcome(
            "COMPLETED",
            "CONFIRMED" if effect != "READ_ONLY" else "NOT_ATTEMPTED",
            answer=f"Activated {chosen.get('label') or chosen['element_token']!r}.",
        )

    async def _observe_target(
        self, item: BoundedWorkItem, task: TaskState, adapter: CuaAdapter
    ) -> tuple[int, int] | None:
        """The scope-checked target surface, from fresh observation only."""
        apps = await adapter.list_apps(task)
        for app in apps:
            if not app.running or app.pid is None:
                continue
            allowed = item.expected_scope.allowed_apps
            if allowed and app.bundle_id not in allowed:
                continue
            window_id = await adapter.front_window(task, app.pid)
            if window_id is not None:
                return app.pid, window_id
        return None

    def _build_candidates(
        self, item: BoundedWorkItem, elements: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """<=8 actionable candidates from fresh AX roles and tokens."""
        actionable_roles = {
            "axbutton", "axlink", "axmenuitem", "axtextfield", "axtextarea",
            "axcombobox", "axsearchfield", "axcheckbox", "axradiobutton", "axtab",
        }
        tokens = _objective_tokens(item.objective)
        candidates: list[dict[str, Any]] = []
        for element in elements:
            role = str(element.get("role") or "")
            token = element.get("element_token")
            if not token or role.lower() not in actionable_roles:
                continue
            label = str(element.get("label") or element.get("value") or "")
            lowered = label.lower()
            # R06/F05: no generic Submit/Send fallback — only controls whose
            # label matches the objective's own tokens are candidates. An
            # unrelated control is not an action the plan asked for.
            if not any(t in lowered for t in tokens):
                continue
            purpose = "primary"
            candidates.append(
                {
                    "element_token": str(token),
                    "role": role,
                    "label": label[:80],
                    "purpose": purpose,
                }
            )
            if len(candidates) >= MAX_SEMANTIC_CANDIDATES:
                break
        return candidates

    async def _choose_candidate(
        self, item: BoundedWorkItem, candidates: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """JEV picks one candidate ID when enabled; else deterministic first."""
        if not candidates:
            return None
        jev = self._jev() if self._jev_factory else None
        if jev is None:
            primary = next((c for c in candidates if c["purpose"] == "primary"), None)
            return primary or candidates[0]
        # C04/N08 (NP09): the JEV decision is a provider effect — it is
        # reserved BEFORE the call, against the mission's durable allowance.
        # Zero or exhausted JEV budget means no call, never one free call;
        # the unit escalates instead of choosing mechanically.
        try:
            reservation = await self._reserve_for(item, "jev_calls")
        except AuthorityDenied:
            return None
        payload_ids = tuple(f"user_text_{i}" for i in range(1, len(item.payload_refs) + 1))
        request = JevDecisionRequest(
            objective=item.objective,
            requested_app="",
            target_summary="",
            candidates=tuple(
                Candidate(
                    id=c["element_token"],
                    kind=CandidateKind.TARGET,
                    description=f"{c['role']}: {c['label']}",
                )
                for c in candidates
            ),
            payload_ids=payload_ids,
            reason="semantic_ui control selection",
        )
        try:
            decision = await jev.decide(request)
        except JevServiceError:
            return None
        finally:
            # The attempt consumed the allowance whatever the outcome: a
            # failed call is still metered (file 03 BudgetUsage).
            if reservation is not None and self._authority is not None:
                await self._authority.settle(reservation, consumed=True)
        if decision.status is not DecisionStatus.ACT:
            return None
        for candidate in candidates:
            if candidate["element_token"] == decision.selected_id:
                return candidate
        return None

    async def _reserve_for(self, item: BoundedWorkItem, resource: str) -> Any | None:
        """Reserve one unit of a bounded resource at its real boundary.

        With the durable authority bound (production), the reservation is a
        mission-level store row: zero/exhausted allowance refuses, a reused
        call key never authorizes a second effect. Without one (direct
        executor fixtures) the packet's own allowance is the ceiling.
        """
        from assistant.missions.authority import AuthorityDenied
        from assistant.missions.contracts import BudgetCharge

        if getattr(item.budget, f"max_{resource}", 0) <= 0:
            raise AuthorityDenied("BUDGET_EXHAUSTED", f"packet {resource} allowance is zero")
        if self._authority is not None:
            charge = BudgetCharge(
                resource=resource,
                amount=1,
                call_key=f"{resource}:{item.mission_id}:{item.execution_id}:{new_id()[:8]}",
            )
            try:
                return await self._authority.reserve(item.mission_id, charge, item=item)
            except AuthorityDenied:
                raise
        if getattr(item.budget, f"max_{resource}", 0) <= 0:
            raise AuthorityDenied(
                "BUDGET_EXHAUSTED", f"no {resource} allowance on this work item"
            )
        return None

    def _jev(self) -> Any | None:
        if self._jev_factory is None:
            return None
        return self._jev_factory()

    # -- verification -------------------------------------------------------------

    async def _evaluate_preconditions(
        self,
        item: BoundedWorkItem,
        adapter: CuaAdapter,
        usage: BudgetUsage,
    ) -> str:
        """Run the packet's precondition checks against a fresh observation.

        Returns an empty string when every precondition holds, otherwise a
        bounded summary of the first failure. The observation is evidence —
        the same trusted catalog that judges postconditions judges these.
        """
        assert self._evidence is not None
        verify_task = self._task_from(item, CancellationToken(item.control_epoch))
        refs: list[Any] = []
        observed = await self._observe_target(item, verify_task, adapter)
        if observed is None:
            return "no usable window matched the mission scope"
        pid, window_id = observed
        state = await adapter.observe_window(verify_task, pid, window_id)
        apps_snapshot = []
        for app in await adapter.list_apps(verify_task):
            apps_snapshot.append(
                {
                    "bundle_id": app.bundle_id,
                    "name": app.name,
                    "pid": app.pid,
                    "running": app.running,
                    "active": app.active,
                }
            )
        from assistant.missions.contracts import EvidenceCandidate

        candidate = EvidenceCandidate(
            kind="structured_facts",
            payload={
                "pid": pid,
                "window_id": window_id,
                "apps": apps_snapshot,
                "elements": [
                    {k: e.get(k) for k in ("role", "label", "value") if isinstance(e, dict)}
                    for e in (state.get("elements") or [])[:60]
                    if isinstance(e, dict)
                ],
            },
            captured_at_ms=int(time.time() * 1000),
        )
        refs.append(
            await self._evidence.put(
                candidate, mission_id=item.mission_id, execution_id=item.execution_id
            )
        )
        for spec in item.preconditions:
            result = await self._evidence.verify(
                spec, item, refs=refs, not_before_ms=self._started_at_ms
            )
            if not result.passed:
                return f"{spec.check_id}: {result.reason[:200]}"
        return ""

    async def _capture_before_evidence(self, item: BoundedWorkItem, adapter: CuaAdapter) -> Any:
        """One BEFORE observation bound to the target window (D06)."""
        assert self._evidence is not None
        verify_task = self._task_from(item, CancellationToken(item.control_epoch))
        observed = await self._observe_target(item, verify_task, adapter)
        if observed is None:
            return None
        pid, window_id = observed
        state = await adapter.observe_window(verify_task, pid, window_id,
                                             for_verification=True)
        from assistant.missions.contracts import EvidenceCandidate

        candidate = EvidenceCandidate(
            kind="ui_before",
            payload={
                "pid": pid,
                "window_id": window_id,
                "scroll_offset": state.get("scroll_offset"),
                "elements": [
                    {
                        k: e.get(k)
                        for k in ("role", "label", "value", "focused", "checked",
                                  "selected", "expanded", "element_token")
                        if isinstance(e, dict)
                    }
                    for e in (state.get("elements") or [])[:60]
                    if isinstance(e, dict)
                ],
            },
            captured_at_ms=int(time.time() * 1000),
        )
        return await self._evidence.put(
            candidate, mission_id=item.mission_id, execution_id=item.execution_id
        )

    async def _verify_postconditions(
        self,
        item: BoundedWorkItem,
        adapter: CuaAdapter,
        task: TaskState,
        usage: BudgetUsage,
        *,
        extra_refs: list[Any] | None = None,
    ) -> list[CheckResult]:
        """Gather fresh observation evidence, then run the trusted catalog."""
        assert self._evidence is not None
        # Verification observes once, after the work: it gets its own task
        # state so its observations never count as loop (no-)progress.
        verify_task = self._task_from(item, CancellationToken(item.control_epoch))
        refs = []
        observed = await self._observe_target(item, verify_task, adapter)
        if observed is not None:
            pid, window_id = observed
            state = await adapter.observe_window(verify_task, pid, window_id, for_verification=True)
            # The app identity snapshot powers the app_foreground verifier
            # (R06): an observed running/active app is independent evidence.
            apps_snapshot = []
            for app in await adapter.list_apps(verify_task):
                apps_snapshot.append(
                    {
                        "bundle_id": app.bundle_id,
                        "name": app.name,
                        "pid": app.pid,
                        "running": app.running,
                        "active": app.active,
                    }
                )
            from assistant.missions.contracts import EvidenceCandidate

            candidate = EvidenceCandidate(
                kind="structured_facts",
                payload={
                    "pid": pid,
                    "window_id": window_id,
                    # D06: the driver-reported scroll offset (when present)
                    # powers the exact scroll_effect verifier.
                    "scroll_offset": state.get("scroll_offset"),
                    "apps": apps_snapshot,
                    "elements": [
                        {
                            k: e.get(k)
                            for k in ("role", "label", "value", "focused", "checked",
                                      "selected", "expanded", "element_token")
                            if isinstance(e, dict)
                        }
                        for e in (state.get("elements") or [])[:60]
                        if isinstance(e, dict)
                    ],
                    # C05/N07: the trusted verification evidence carries the
                    # payloads resolved from the mission vault so the
                    # field_value verifier can compare WITHOUT the check
                    # spec persisting the text itself.
                    "resolved_payloads": self._resolve_payloads(item),
                },
                captured_at_ms=int(time.time() * 1000),
            )
            refs.append(
                await self._evidence.put(
                    candidate, mission_id=item.mission_id, execution_id=item.execution_id
                )
            )
        if extra_refs:
            refs = [*extra_refs, *refs]
        checks = []
        for spec in item.expected_postconditions:
            checks.append(
                await self._evidence.verify(
                    spec, item, refs=refs, not_before_ms=self._started_at_ms
                )
            )
        return checks

    # -- assembly -----------------------------------------------------------------

    def _assemble(
        self,
        item: BoundedWorkItem,
        outcome: ExecutorOutcome,
        checks: list[CheckResult],
        elapsed: int,
        usage: BudgetUsage,
    ) -> StepResult:
        required_checks = [
            c
            for c, spec in zip(checks, item.expected_postconditions, strict=False)
            if spec.required
        ]
        if required_checks:
            all_passed = all(c.passed for c in required_checks)
            if outcome.status == "COMPLETED" and not all_passed:
                # A check failure overrides fluent success (TC-35): the unit
                # reports VERIFICATION_FAILED, never a claimed done.
                outcome = ExecutorOutcome(
                    "FAILED",
                    outcome.effect_outcome if outcome.effect_outcome != "CONFIRMED" else "UNKNOWN",
                    failure_category="VERIFICATION_FAILED",
                    uncertainty="; ".join(
                        c.reason for c in required_checks if not c.passed
                    )[:512],
                )
            elif outcome.status in {"NEEDS_CONTROLLER", "NEEDS_HUMAN"} and not all_passed:
                pass  # escalation keeps its own category
        status = outcome.status
        if status == "COMPLETED" and outcome.effect_outcome == "UNKNOWN":
            status = "FAILED"
        result = StepResult(
            mission_id=item.mission_id,
            plan_version=item.plan_version,
            control_epoch=item.control_epoch,
            step_id=item.step_id,
            execution_id=item.execution_id,
            attempt=item.attempt,
            status=status,  # type: ignore[arg-type]
            postconditions=checks,
            failure_category=outcome.failure_category,
            uncertainty=outcome.uncertainty[:512],
            effect_outcome=outcome.effect_outcome,  # type: ignore[arg-type]
            elapsed_ms=elapsed,
            usage=usage,
            suggested_next_action=outcome.suggested_next_action,
        )
        if status in {"NEEDS_CONTROLLER", "NEEDS_HUMAN"}:
            logger.info(
                "work_item_escalation",
                extra={"event": "work_item_escalation", "category": outcome.failure_category},
            )
        return result

    # -- authority guard -----------------------------------------------------------

    async def _guard_dispatch(
        self,
        item: BoundedWorkItem,
        tool: str,
        kwargs: dict[str, Any],
        cancel: CancellationToken,
    ) -> str | None:
        """The mission authority check installed into the wrapped tools.

        R03: the observation handed to the authority is the RECORDED one —
        its timestamp comes from when the driver actually answered, not from
        now. Inventory reads (list_apps/list_windows) are the discovery
        path and pass unscoped; every other call must sit on a fresh
        observation of an in-scope surface.
        """
        if self._authority is None:
            return None
        if cancel.is_cancelled:
            return "Refused: CANCELLED: the mission was cancelled"
        from assistant.tools.policy import cua_target_state

        # C04/N08: the dispatch itself is a bounded resource. A mutating
        # call spends one durable action; a read spends one observation.
        # The reservation happens BEFORE the authority check so an exhausted
        # mission refuses at the boundary, and settles once per dispatch —
        # consumed when the permit is issued, released when it is refused.
        # C06/N09: the lease fence is checked against the queue's CURRENT
        # owner at the final dispatch boundary (after every await in the
        # unit) — a stored fence string confers nothing by itself.
        if self._ownership_probe is not None and item.lease_fence:
            current = self._ownership_probe(item.mission_id)
            if inspect.isawaitable(current):
                current = await current
            if current is None:
                return (
                    "Refused: STALE_TARGET: this execution no longer owns the "
                    "desktop lease; re-claim the step with a fresh fence"
                )
            current_fence = current[0] if isinstance(current, tuple) else str(current)
            if current_fence != item.lease_fence:
                return (
                    "Refused: STALE_TARGET: the desktop lease was superseded "
                    "(a stop or a newer grant took ownership); re-claim the step"
                )
        effect = _effect_for_tool(tool, item)
        resource = "observations" if effect == "READ_ONLY" else "actions"
        try:
            reservation = await self._reserve_for(item, resource)
        except AuthorityDenied as exc:
            return f"Refused: {exc.category}: {exc.reason}"
        state = cua_target_state.get() or {}
        apps = state.get("apps") or {}
        pid = kwargs.get("pid")
        app_bundle = apps.get(pid) if isinstance(pid, int) else None
        observed_at = state.get("observed_at_ms")
        if tool in {"list_windows", "get_window_state", "get_accessibility_tree"}:
            observed_at = observed_at or state.get("inventory_observed_at_ms")
        observed = ScopeObservation(
            app_bundle=str(app_bundle) if app_bundle else "",
            pid=pid if isinstance(pid, int) and pid > 0 else None,
            window_id=kwargs.get("window_id")
            if isinstance(kwargs.get("window_id"), int) and kwargs.get("window_id", 0) > 0
            else None,
            # D03: the driver's recorded origin for this exact surface, when
            # it reported one. Configured origin scopes refuse content calls
            # on unknown origins — reads included — so the observation must
            # carry what the driver actually saw.
            origin=(state.get("origins", {}) or {}).get(f"{pid}:{kwargs.get('window_id')}")
            if isinstance(pid, int)
            else None,
            captured_at_ms=int(observed_at) if observed_at else 0,
            driver_generation=item.driver_generation,
        )
        intent = ActionIntent(tool=tool, args=dict(kwargs), effect_class=effect)  # type: ignore[arg-type]
        try:
            permit = await self._authority.authorize(item, intent, observed)
            await self._authority.consume_permit(permit.permit_id, intent.args_digest)
        except AuthorityDenied as exc:
            if reservation is not None:
                await self._authority.settle(reservation, consumed=False)
            if exc.category == "APPROVAL_REQUIRED" and exc.pending is not None:
                # D02: surface the exact owed approval to the result assembly.
                self._pending_approval_by_exec[item.execution_id] = dict(exc.pending)
            return f"Refused: {exc.category}: {exc.reason}"
        if reservation is not None:
            await self._authority.settle(reservation, consumed=True)
        return None


#: Tools that only observe; inventory reads are the discovery path.
_INVENTORY_TOOLS = frozenset({"list_apps", "list_windows"})


def _effect_for_tool(tool: str, item: BoundedWorkItem) -> str:
    """The trusted effect class for one tool inside this work item.

    R03: typing/submit-like operations are classified conservatively — an
    arbitrary type_text can produce non-idempotent effects, so it is
    EXTERNAL_WRITE even when the step is nominally local.
    """
    if tool in _INVENTORY_TOOLS or tool in {
        "get_window_state", "get_accessibility_tree", "get_screen_size",
        "get_desktop_state", "verify_state", "zoom", "screenshot",
    }:
        return "READ_ONLY"
    if tool in {"type_text", "set_value"}:
        return "EXTERNAL_WRITE"
    return item.effect_class


def _objective_tokens(objective: str) -> list[str]:
    """Significant lowercase tokens from the objective, for label matching."""
    stopwords = {
        "the", "a", "an", "and", "or", "to", "in", "on", "for", "with", "of",
        "open", "click", "press", "type", "set", "into", "field", "then",
    }
    return [
        word.strip(".,!?'\"").lower()
        for word in objective.split()
        if len(word.strip(".,!?'\"")) > 2 and word.strip(".,!?'\"").lower() not in stopwords
    ]


def exception_packet_for(
    item: BoundedWorkItem, result: StepResult
) -> ExceptionPacket:
    """The compact exception a NEEDS_CONTROLLER/NEEDS_HUMAN unit owes."""
    from assistant.missions.store import _screen_private

    return ExceptionPacket(
        mission_id=item.mission_id,
        plan_version=item.plan_version,
        control_epoch=item.control_epoch,
        step_id=item.step_id,
        execution_id=item.execution_id,
        attempt=item.attempt,
        category=result.failure_category or "UNSUPPORTED_ACTION",
        expected_summary=item.objective[:512],
        # D07: the packet is MODEL EGRESS — the observed text is screened at
        # this boundary, so a secret seen in a refusal never reaches Deep.
        observed_summary=_screen_private(result.uncertainty or "")[:512],
        remaining_budget=result.usage,
        unresolved_effects=result.external_operation_ids,
        allowed_decisions=["REVISE", "ASK_OWNER", "BLOCK"]
        if result.status == "NEEDS_CONTROLLER"
        else ["ASK_OWNER", "BLOCK"],
    )


__all__ = [
    "ExecutorOutcome",
    "SUPPORTED_RECIPES",
    "SEMANTIC_UI_TOOLS",
    "VeloExecutor",
    "exception_packet_for",
]
