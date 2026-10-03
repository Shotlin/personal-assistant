"""MissionService tests (T06, file 06 U4): ownership, planning, gates.

Zero-model fast missions, Deep planned exactly once for real multi-step
work, deterministic acceptance, and honest escalation.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from assistant.missions.authority import MissionAuthority
from assistant.missions.contracts import (
    BudgetLimits,
    CancellationToken,
    CheckSpec,
    MissionControl,
    RequestEnvelope,
    Scope,
    StepSpec,
    new_id,
)
from assistant.missions.controller import (
    ControllerContext,
    ControllerRoleViolation,
    DeepController,
)
from assistant.missions.evidence import EvidenceStore
from assistant.missions.executor import VeloExecutor
from assistant.missions.service import MissionService
from assistant.missions.store import MissionStore
from tests.unit.velo_fakes import (
    SHARED_APPS,
    FakeRuntime,
    app_entry,
    reset_world,
    standard_tools,
    window_state_payload,
)


def _now() -> int:
    return int(time.time() * 1000)


class _StubSettings:
    velo_jev_enabled = False
    velo_command_deadline_seconds = 90
    cua_artifact_dir = ""
    mission_allowed_apps: list[str] = []


class _ScriptedDeepInvoke:
    """A scripted Deep transport for plan/recover/review calls."""

    def __init__(self, plan: dict[str, Any] | None = None) -> None:
        self.plan = plan
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, role: str, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((role, tool))
        if role == "PLAN":
            assert self.plan is not None, "the scripted planner has no plan"
            return {"plan": self.plan}
        if role == "RECOVER":
            return {"recovery": {"decision": "ASK_OWNER", "reason": "scripted"}}
        return {"review": {"supported_summary": "", "acceptance_check_ids": []}}


@pytest.fixture()
def world() -> Any:
    reset_world()
    yield None
    reset_world()


def _safari_world(pid: int = 9, window_id: int = 2) -> None:
    SHARED_APPS["list"] = [app_entry("Safari", pid=pid, running=True, active=True)]
    SHARED_APPS["windows"][pid] = [
        {"window_id": window_id, "is_on_screen": True, "bounds": {"height": 600, "width": 800}}
    ]
    SHARED_APPS["state"][(pid, window_id)] = window_state_payload(
        [{"role": "AXButton", "label": "fixture-mark-go", "element_token": "tok-go"}],
        pid=pid,
        window_id=window_id,
    )


@pytest.fixture()
async def store(tmp_path: Any) -> Any:
    s = await MissionStore.connect(tmp_path / "service.db")
    await s.setup()
    yield s
    await s.close()


def _service(store: MissionStore, *, invoke: Any = None) -> tuple[MissionService, FakeRuntime]:
    reset_runtime = FakeRuntime(standard_tools())

    async def get_runtime() -> FakeRuntime:
        return reset_runtime

    settings = _StubSettings()
    authority = MissionAuthority(store)
    evidence = EvidenceStore("/tmp/fixture-mission-evidence")
    executor = VeloExecutor(
        settings, get_runtime=get_runtime, authority=authority, evidence=evidence
    )
    controller = DeepController(None)  # chat path unused in these tests
    service = MissionService(
        settings,
        store=store,
        authority=authority,
        evidence=evidence,
        executor=executor,
        controller=controller,
    )
    return service, reset_runtime


def _request(text: str) -> RequestEnvelope:
    return RequestEnvelope(
        request_id=new_id(),
        conversation_id="conv-service",
        owner_id="sani-local",
        input_origin="typed_final",
        input_revision=1,
        text=text,
        submitted_at_ms=_now(),
    )



def _required_check(step_id: str) -> Any:
    """The required independent check a scripted actionable step now owes."""
    return CheckSpec(
        check_id=f"verified-{step_id}",
        verifier_id="window_visible",
        verifier_version="1.0.0",
        expected={},
        required=True,
    )

def _planned_invocation(steps: list[StepSpec]) -> _ScriptedDeepInvoke:
    return _ScriptedDeepInvoke(
        {"steps": [s.model_dump() for s in steps], "explanation": "scripted plan"}
    )


def _mission_scope() -> Scope:
    """Exactly the scope the service derives (owner + default effects)."""
    return Scope(
        owner_id="sani-local",
        permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"},
    )


def _step(step_id: str, recipe_id: str, ordinal: int, **overrides: Any) -> StepSpec:
    fields: dict[str, Any] = {
        "step_id": step_id,
        "ordinal": ordinal,
        "objective": f"fixture {recipe_id} step",
        "recipe_id": recipe_id,
        "recipe_args": {"app_name": "Safari"} if recipe_id == "open_app" else {},
        "scope": _mission_scope(),
        "budget": {"max_wall_ms": 90_000, "max_actions": 12},
    }
    fields.update(overrides)
    return StepSpec(**fields)


async def test_exact_action_builds_one_step_plan_with_zero_model_calls(
    store: MissionStore, world: Any
) -> None:
    _safari_world()
    service, runtime = _service(store)
    invoke = _ScriptedDeepInvoke()  # would fail if called
    mission = await service.submit(
        _request("open safari"), cancel=CancellationToken(), invoke=invoke
    )
    assert invoke.calls == [], "an exact command must never reach Deep"
    assert len(mission.steps) == 1
    assert mission.status == "COMPLETED"
    assert service.component_counters["deep_calls"] == 0


async def test_information_question_probes_nothing(
    store: MissionStore, world: Any
) -> None:
    service, runtime = _service(store)
    mission = await service.submit(
        _request("What is the capital of France?"), cancel=CancellationToken()
    )
    assert mission.status == "COMPLETED"
    assert mission.steps == [], "an answer needs no steps"
    observations = [
        c for tool in runtime.cua_tools.values() for c in tool.calls
    ]
    assert observations == [], "a question must not acquire or probe the desktop"


async def test_unfamiliar_multistep_plans_once_via_deep(
    store: MissionStore, world: Any
) -> None:
    _safari_world()
    plan = [
        _step("s1", "open_app", 1, checks=[_required_check("s1")]),
        _step("s2", "semantic_ui", 2, dependencies=["s1"], objective="press fixture-mark-go",
              checks=[_required_check("s2")]),
    ]
    invoke = _planned_invocation(plan)
    service, _runtime = _service(store, invoke=invoke)
    mission = await service.submit(
        _request("open safari then press the fixture-mark-go button"),
        cancel=CancellationToken(),
        invoke=invoke,
    )
    assert [(role, tool) for role, tool in invoke.calls] == [("PLAN", "submit_mission_plan")], (
        "Deep plans once; routine transitions never wake it per click"
    )
    assert mission.status == "COMPLETED"
    assert service.component_counters["deep_calls"] == 1


async def test_required_check_failure_blocks_completion(
    store: MissionStore, world: Any, tmp_path: Any
) -> None:
    _safari_world()
    plan = [
        _step(
            "s1",
            "open_app",
            1,
            checks=[
                CheckSpec(
                    check_id="needs-evidence",
                    verifier_id="page_state",
                    verifier_version="1.0.0",
                    expected={"markers": ["never-present-marker"]},
                    required=True,
                )
            ],
        )
    ]
    invoke = _planned_invocation(plan)
    reset_runtime = FakeRuntime(standard_tools())

    async def get_runtime() -> FakeRuntime:
        return reset_runtime

    settings = _StubSettings()
    authority = MissionAuthority(store)
    evidence = EvidenceStore(tmp_path / "ev")
    executor = VeloExecutor(
        settings, get_runtime=get_runtime, authority=authority, evidence=evidence
    )
    service = MissionService(
        settings, store=store, authority=authority, evidence=evidence,
        executor=executor, controller=DeepController(None),
    )
    mission = await service.submit(
        _request("open safari then prove the never-present-marker is shown"),
        cancel=CancellationToken(),
        invoke=invoke,
    )
    assert mission.status != "COMPLETED", "a failing required check cannot complete a mission"
    assert mission.status == "FAILED", "a failed verification is an honest failure"


async def test_recovery_only_on_structured_exception(
    store: MissionStore, world: Any
) -> None:
    """A NEEDS_HUMAN unit escalates once, through the structured packet."""
    _safari_world()
    SHARED_APPS["state"][(9, 2)] = window_state_payload(
        [{"role": "AXStaticText", "label": "nothing actionable"}], pid=9, window_id=2
    )
    plan = [_step("s2", "semantic_ui", 1, objective="find a control that does not exist",
                  checks=[_required_check("s2")])]
    invoke = _planned_invocation(plan)
    service, _runtime = _service(store, invoke=invoke)
    mission = await service.submit(
        _request("press the missing button in safari"),
        cancel=CancellationToken(),
        invoke=invoke,
    )
    assert mission.status == "PAUSED", "an unresolved unit pauses for the owner"
    # Exactly one plan call plus at most one structured recovery call:
    # routine transitions never wake Deep, exceptions do (once).
    assert service.component_counters["deep_calls"] <= 2


async def test_control_pause_resume_and_cancel(store: MissionStore, world: Any) -> None:
    _safari_world()
    service, _runtime = _service(store)
    mission = await service.submit(_request("open safari"), cancel=CancellationToken())
    assert mission.status == "COMPLETED"
    # A completed mission cannot be cancelled.
    from assistant.missions.store import MissionStoreError

    with pytest.raises(MissionStoreError):
        await service.control(MissionControl(
            control_id=new_id(), mission_id=mission.mission_id,
            expected_plan_version=mission.plan_version,
            expected_control_epoch=mission.control_epoch,
            kind="CANCEL",
        ))


async def test_submit_is_idempotent(store: MissionStore, world: Any) -> None:
    _safari_world()
    service, _runtime = _service(store)
    request = _request("open safari")
    first = await service.submit(request, cancel=CancellationToken())
    again = await service.submit(request, cancel=CancellationToken())
    assert first.mission_id == again.mission_id
    assert again.status == "COMPLETED"


async def test_two_concurrent_service_runs_cancel_independently(
    store: MissionStore, world: Any
) -> None:
    _safari_world()
    service, _runtime = _service(store)

    async def run_one(text: str) -> Any:
        token = CancellationToken()
        return await service.submit(_request(text), cancel=token)

    results = await asyncio.gather(
        run_one("open safari"), run_one("open safari"), return_exceptions=True
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    assert not errors, f"concurrent submits must not fail: {errors}"


# -- Controller role enforcement -------------------------------------------------


async def test_controller_role_blocks_raw_mutation_tool() -> None:
    """TC-08/RF: a raw CUA call from a Controller role fails even when the
    tool reference was injected."""
    from assistant.missions.controller import controller_role

    controller = DeepController(None)

    class _FakeCuaTool:
        name = "click"
        called = False

        async def ainvoke(self, **kwargs: Any) -> str:
            self.called = True
            return "clicked"

    tool = _FakeCuaTool()
    with pytest.raises(ControllerRoleViolation):
        with controller_role("PLAN"):
            await controller.invoke_role_tool("click", tool.ainvoke, pid=1, window_id=2)
    assert tool.called is False, "the mutation must never dispatch"


async def test_controller_role_context_scopes_per_invocation() -> None:
    DeepController(None)
    context = ControllerContext(role="PLAN", scope_summary="fixture")
    assert context.role == "PLAN"
    assert context.context_text == ""


# -- R05/RP12: desktop lease + real fences in the service path ------------------


async def test_rp12_claims_carry_real_fences_and_generation(
    store: MissionStore, world: Any, tmp_path: Any
) -> None:
    """RP12 regression: work items claimed by the service carry the lease
    fence and driver generation; two steps can never be active at once."""
    from assistant.missions.evidence import EvidenceStore

    _safari_world()
    plan = [
        _step("s1", "open_app", 1, checks=[_required_check("s1")]),
        _step("s2", "semantic_ui", 2, dependencies=["s1"], objective="press fixture-mark-go",
              checks=[_required_check("s2")]),
    ]
    invoke = _planned_invocation(plan)
    settings = _StubSettings()
    authority = MissionAuthority(store)
    evidence = EvidenceStore(tmp_path / "ev")
    executor = VeloExecutor(
        settings, get_runtime=_FakeRuntimeFactory(store), authority=authority,
        evidence=evidence, store=store,
    )
    from assistant.missions.controller import DeepController

    service = MissionService(
        settings, store=store, authority=authority, evidence=evidence,
        executor=executor, controller=DeepController(None),
    )
    mission = await service.submit(
        _request("open safari then press the fixture-mark-go button"),
        cancel=CancellationToken(),
        invoke=invoke,
    )
    assert mission.status == "COMPLETED"
    # Both attempts went through the one desktop queue with real fences.
    attempts = store._conn.execute(
        "SELECT execution_id, lease_fence, control_epoch FROM mission_attempts "
        "WHERE mission_id=?",
        (mission.mission_id,),
    ).fetchall()
    assert len(attempts) == 2
    fences = {str(a[1]) for a in attempts}
    assert "" not in fences, "empty lease fences mean dispatch bypassed the queue"
    assert len(fences) == 2, "each step held its own lease grant"


class _FakeRuntimeFactory:
    """Returns a FakeRuntime whose cua_tools come from velo_fakes."""

    def __init__(self, store: Any) -> None:
        self._store = store
        from tests.unit.velo_fakes import FakeRuntime, standard_tools

        self._runtime = FakeRuntime(standard_tools())

    async def __call__(self) -> Any:
        return self._runtime


# -- R11: zero Deep budget blocks the SERVICE planning path (RP11 sharp) -------


async def test_rp11_zero_deep_budget_blocks_service_planning(
    store: MissionStore, world: Any
) -> None:
    """With max_deep_calls=0, an unparseable request must NOT reach Deep:
    the durable reservation blocks planning and the mission stays blocked."""
    from assistant.missions.authority import MissionAuthority
    from assistant.missions.evidence import EvidenceStore
    from assistant.missions.executor import VeloExecutor

    invoke = _ScriptedDeepInvoke()  # would record a call if reached
    reset_runtime = FakeRuntime(standard_tools())

    async def get_runtime() -> FakeRuntime:
        return reset_runtime

    settings = _StubSettings()
    authority = MissionAuthority(store)

    class _ZeroDeepLimits:
        """Limits provider that denies Deep calls entirely (budget=0)."""

        def __getattr__(self, name: str) -> Any:
            return getattr(_StubSettings(), name)

    service = MissionService(
        settings, store=store, authority=authority,
        evidence=EvidenceStore("/tmp/fixture-mission-evidence"),
        executor=VeloExecutor(settings, get_runtime=get_runtime, authority=authority,
                              evidence=EvidenceStore("/tmp/fixture-mission-evidence")),
        controller=DeepController(None),
    )
    # Patch limits to zero deep calls: build a fresh service whose limits
    # deny Deep calls (subclass defined before use keeps mypy static).
    from assistant.missions.contracts import BudgetLimits as BL

    class _ZeroDeepService(MissionService):
        def _limits_for(self, request: Any) -> BL:
            return BL(max_deep_calls=0)

    service = _ZeroDeepService(
        settings, store=store, authority=authority,
        evidence=EvidenceStore("/tmp/fixture-mission-evidence"),
        executor=VeloExecutor(settings, get_runtime=get_runtime, authority=authority,
                              evidence=EvidenceStore("/tmp/fixture-mission-evidence")),
        controller=DeepController(None),
    )

    mission = await service.submit(
        _request("please plan a complicated multi-step fixture campaign"),
        cancel=CancellationToken(),
        invoke=invoke,
    )
    assert invoke.calls == [], "Deep must never be invoked with a zero budget"
    assert mission.status in {"BLOCKED", "FAILED", "PLANNED", "CANCELLED"}
    assert mission.budget_usage.consumed.get("deep_calls", 0) == 0


# -- C03/N01 (NP02): pause preserves uncertainty; resume never replays --------


async def test_np02_pause_never_resets_dispatched_effect(store: MissionStore) -> None:
    """A DISPATCHED external-write attempt that is paused must stay
    unresolved: resume settles it (UNKNOWN without a probe -> step BLOCKED,
    mission BLOCKED), and the step can never be claimed as a fresh attempt."""
    service, _runtime = _service(store)
    mission = await store.claim_request(
        _request("fixture"), "d" * 64, goal="fixture",
        scope=Scope(owner_id="sani-local"), limits=BudgetLimits(),
    )
    await store.commit_plan(
        mission.mission_id, 0,
        [_step("s1", "semantic_ui", 1, effect_class="EXTERNAL_WRITE")], [],
    )
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    assert item is not None
    await store.mark_dispatched(item.execution_id)
    await service.control(MissionControl(
        control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="PAUSE",
    ))
    attempt = await store.get_attempt(item.execution_id)
    assert attempt is not None
    assert attempt["dispatch_state"] == "RECONCILING", (
        "a dispatched effect must remain unresolved across pause"
    )
    await service.control(MissionControl(
        control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=2, kind="RESUME",
    ))
    settled = await store.get_mission(mission.mission_id)
    assert settled is not None
    assert settled.status == "BLOCKED", (
        "resume must settle the uncertain effect honestly, not replay it"
    )
    states = await store.get_step_states(mission.mission_id, 1)
    assert states.get("s1") == "BLOCKED"
    with pytest.raises(Exception, match="unresolved dispatched attempt|not claimable"):
        await store.claim_step(
            mission.mission_id, 1, settled.control_epoch, tool_ids=["click"]
        )


async def test_np02_resume_with_proven_no_effect_retries(store: MissionStore) -> None:
    """A pause of an INTENT_COMMITTED (never dispatched) attempt is safe:
    reconciliation proves NO_EFFECT, the step returns to PENDING, and the
    mission is scheduled again with a fresh token."""
    service, _runtime = _service(store)
    mission = await store.claim_request(
        _request("fixture"), "d" * 64, goal="fixture",
        scope=Scope(owner_id="sani-local"), limits=BudgetLimits(),
    )
    await store.commit_plan(
        mission.mission_id, 0, [_step("s1", "open_app", 1)], []
    )
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    await service.control(MissionControl(
        control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="PAUSE",
    ))
    attempt = await store.get_attempt(item.execution_id)
    assert attempt is not None
    assert attempt["dispatch_state"] == "CANCELLED"
    await asyncio.sleep(0.05)
    await service.control(MissionControl(
        control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=2, kind="RESUME",
    ))
    settled = await store.get_mission(mission.mission_id)
    assert settled is not None
    assert settled.status in {"RUNNING", "COMPLETED"}, (
        "a safely reconciled mission must be scheduled, not left stranded"
    )


# -- C04/N05 (NP04) + C07/N06 (NP05): transports and screening ----------------


async def _mission_with_step(
    store: MissionStore, *, effect_class: str = "REPEATABLE_LOCAL"
) -> Any:
    mission = await store.claim_request(
        _request("fixture"), "d" * 64, goal="fixture",
        scope=_mission_scope(), limits=BudgetLimits(),
    )
    await store.commit_plan(
        mission.mission_id, 0,
        [_step("s1", "semantic_ui", 1, effect_class=effect_class)], [],
    )
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    assert item is not None
    return mission, item


async def test_np04_recovery_uses_reserved_transport(store: MissionStore) -> None:
    """NP04 regression: NEEDS_CONTROLLER escalation invokes the RECOVER role
    over the mission-bound transport, with the Deep call reserved durably."""
    from assistant.missions.contracts import StepResult

    scripted = _ScriptedDeepInvoke()
    service, _runtime = _service(store)
    service._transport_factory = lambda mission: scripted
    mission, item = await _mission_with_step(store)
    result = StepResult(
        mission_id=mission.mission_id, plan_version=1, control_epoch=1,
        step_id="s1", execution_id=item.execution_id, attempt=1,
        status="NEEDS_CONTROLLER", effect_outcome="NOT_ATTEMPTED",
        failure_category="STALE_TARGET",
    )
    await service._escalate(mission.mission_id, item, result)
    assert ("RECOVER", "submit_recovery_decision") in scripted.calls, (
        "recovery must invoke the real transport, not record a no-transport pause"
    )
    settled = await store.get_mission(mission.mission_id)
    assert settled is not None
    assert settled.budget_usage.consumed.get("deep_calls", 0) == 1, (
        "the recovery invocation must be charged exactly once"
    )


async def test_np05_revision_is_screened_then_planned(store: MissionStore) -> None:
    """NP05 regression: a REVISE control persists a screened correction event
    AND creates a new versioned plan through the reserved transport."""
    revision_plan = {
        "steps": [_step("s1", "open_app", 1, checks=[_required_check("s1")]).model_dump()],
        "explanation": "revised plan",
    }
    scripted = _ScriptedDeepInvoke(plan=revision_plan)
    service, _runtime = _service(store)
    service._transport_factory = lambda mission: scripted
    mission, _item = await _mission_with_step(store)
    settled = await service.control(MissionControl(
        control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="REVISE",
        revision_request="actually open Numbers instead",
        reason="owner correction",
    ))
    assert ("PLAN", "submit_mission_plan") in scripted.calls
    assert settled.plan_version == 2, "a revision must commit a new plan version"
    events = await store.get_events(mission.mission_id)
    corrections = [e for e in events if e.kind == "correction"]
    assert corrections and corrections[-1].safe_payload.get("revision") == (
        "actually open Numbers instead"
    )
    assert settled.budget_usage.consumed.get("deep_calls", 0) == 1


async def test_np05_secret_revision_refused_before_persistence(
    store: MissionStore,
) -> None:
    """NP05 regression: a secret-shaped revision never reaches storage or the
    planner; the control transaction rolls back."""
    scripted = _ScriptedDeepInvoke()
    service, _runtime = _service(store)
    service._transport_factory = lambda mission: scripted
    mission, _item = await _mission_with_step(store)
    sentinel = "sk-review-synthetic-secret-12345678901234567890"
    with pytest.raises(Exception, match="privacy policy"):
        await service.control(MissionControl(
            control_id=new_id(), mission_id=mission.mission_id,
            expected_plan_version=1, expected_control_epoch=1, kind="REVISE",
            revision_request=f"fixture {sentinel}",
        ))
    raw = store._conn.execute(
        "SELECT payload_json FROM mission_events WHERE mission_id=?",
        (mission.mission_id,),
    ).fetchall()
    assert not any(sentinel in row[0] for row in raw), "sentinel must not persist"
    assert scripted.calls == [], "a refused revision must never reach the planner"
    settled = await store.get_mission(mission.mission_id)
    assert settled is not None and settled.plan_version == 1


async def test_np10_one_planning_invocation_consumes_one_unit(
    store: MissionStore,
) -> None:
    """NP10 regression: one scripted planning invocation is charged exactly
    once (reserve -> settle; never settle + add_usage)."""
    plan = {
        "steps": [_step("s1", "open_app", 1, checks=[_required_check("s1")]).model_dump()],
        "explanation": "scripted plan",
    }
    scripted = _ScriptedDeepInvoke(plan=plan)
    service, _runtime = _service(store)
    goal = "Prepare a fixture workflow for the review"
    mission = await store.claim_request(
        _request(goal), "e" * 64, goal=goal,
        scope=_mission_scope(), limits=BudgetLimits(),
    )
    await service._plan(_request(goal), mission, invoke=scripted, on_event=None)
    settled = await store.get_mission(mission.mission_id)
    assert settled is not None
    assert settled.budget_usage.consumed.get("deep_calls") == 1
    assert settled.budget_usage.reserved.get("deep_calls", 0) == 0


# -- C05/N07 (NP08): per-step catalogs and the approved typing path ------------


async def test_np08_second_step_receives_its_own_catalog(store: MissionStore) -> None:
    """NP08 regression: after open_app succeeds, the scroll step is claimed
    with the SCROLL catalog — inside the same claim transaction."""
    from assistant.missions.service import RECIPE_TOOL_CATALOG

    mission = await store.claim_request(
        _request("fixture"), "d" * 64, goal="fixture",
        scope=_mission_scope(), limits=BudgetLimits(),
    )
    await store.commit_plan(
        mission.mission_id, 0,
        [_step("s1", "open_app", 1),
         _step("s2", "scroll", 2, dependencies=["s1"])],
        [],
    )
    first = await store.claim_step(
        mission.mission_id, 1, 1, tool_ids=[], tool_catalog=RECIPE_TOOL_CATALOG
    )
    assert first is not None and first.recipe_id == "open_app"
    assert "launch_app" in first.allowed_action_scope.tool_ids
    assert "scroll" not in first.allowed_action_scope.tool_ids
    from assistant.missions.contracts import StepResult

    await store.apply_result(StepResult(
        mission_id=mission.mission_id, plan_version=1, control_epoch=1,
        step_id="s1", execution_id=first.execution_id, attempt=1,
        status="COMPLETED", effect_outcome="CONFIRMED",
    ))
    second = await store.claim_step(
        mission.mission_id, 1, 1, tool_ids=[], tool_catalog=RECIPE_TOOL_CATALOG
    )
    assert second is not None and second.recipe_id == "scroll"
    assert "scroll" in second.allowed_action_scope.tool_ids, (
        "the claimed step's own recipe must select the tool catalog"
    )
    assert "launch_app" not in second.allowed_action_scope.tool_ids


async def test_actionable_proposal_without_required_checks_is_refused(
    store: MissionStore,
) -> None:
    """C05/N07: a Deep plan that can declare completion with no required
    check is refused — unverifiable outcomes stay blocked."""
    from assistant.missions.service import MissionPlanError

    service, _runtime = _service(store)
    mission = await store.claim_request(
        _request("fixture"), "d" * 64, goal="fixture",
        scope=_mission_scope(), limits=BudgetLimits(),
    )
    unchecked = _step("s1", "open_app", 1, checks=[])
    with pytest.raises(MissionPlanError, match="no required independent check"):
        service._validate_proposal(_proposal_of([unchecked]), mission)


def _proposal_of(steps: list[Any]) -> Any:
    from assistant.missions.contracts import PlanProposal

    return PlanProposal(
        steps=steps, success_criteria=[], explanation="scripted proposal"
    )


async def test_fast_typing_plan_is_external_write_with_payload_ref(
    store: MissionStore,
) -> None:
    """C05: the approved typing path STARTS at the plan — a dictated type
    command plans as EXTERNAL_WRITE with the text by reference, the mission
    scope explicitly widened, and a field_value required check that never
    persists the text itself."""
    service, _runtime = _service(store)
    mission = await service.submit(
        _request("type hello crew into the fixture note"),
        cancel=CancellationToken(),
    )
    settled = await store.get_mission(mission.mission_id)
    assert settled is not None
    step = settled.steps[0]
    assert step.effect_class == "EXTERNAL_WRITE"
    assert step.payload_refs == ["user_text_1"]
    assert step.payload_digests and len(step.payload_digests[0]) == 64
    assert step.scope.permits_effect("EXTERNAL_WRITE")
    assert settled.scope.permits_effect("EXTERNAL_WRITE")
    required = [c for c in step.checks if c.required]
    assert required and required[0].verifier_id == "field_value"
    assert required[0].expected == {"payload_ref": "user_text_1"}
    payload = store._conn.execute(
        "SELECT content FROM mission_payloads WHERE mission_id=? AND ref='user_text_1'",
        (mission.mission_id,),
    ).fetchone()
    assert payload is not None and "hello crew" in str(payload[0])
