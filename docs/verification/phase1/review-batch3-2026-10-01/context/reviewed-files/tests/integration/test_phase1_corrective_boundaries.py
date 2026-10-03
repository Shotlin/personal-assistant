"""Held-out Phase 1 regressions using real persistence and policy boundaries."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from assistant.missions.authority import MissionAuthority
from assistant.missions.contracts import (
    BudgetLimits,
    CancellationToken,
    MissionControl,
    RequestEnvelope,
    Scope,
    StepSpec,
    new_id,
)
from assistant.missions.controller import ControllerRole
from assistant.missions.executor import VeloExecutor
from assistant.missions.store import MissionActionLedger, MissionStore
from assistant.tools.policy import apply_tool_policy, cua_run_scope, cua_target_state


@pytest.fixture
async def store(tmp_path: Any) -> Any:
    value = await MissionStore.connect(tmp_path / "corrective.db")
    await value.setup()
    yield value
    await value.close()


async def claimed(store: MissionStore) -> Any:
    scope = Scope(owner_id="owner", allowed_apps=["com.fixture.safe"],
                  permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"})
    request = RequestEnvelope(request_id=new_id(), conversation_id="corrective",
                              owner_id="owner", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(request, "d" * 64, goal="fixture", scope=scope,
                                        limits=BudgetLimits())
    mission = await store.commit_plan(mission.mission_id, 0, [StepSpec(
        step_id="s", ordinal=1, objective="click fixture", recipe_id="semantic_ui",
        scope=scope, budget=BudgetLimits(), effect_class="REPEATABLE_LOCAL")], [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    return mission, item


async def test_real_wrapped_effect_remains_uncertain_after_pause(store: MissionStore) -> None:
    """A delayed tool reply must not let pause turn a real effect into a safe retry."""
    from types import SimpleNamespace

    mission, item = await claimed(store)
    entered, release = asyncio.Event(), asyncio.Event()
    effects: list[str] = []

    async def click(pid: int, window_id: int) -> str:
        """Perform an in-memory effect then delay acknowledgment."""
        effects.append("clicked")
        entered.set()
        await release.wait()
        return "clicked"

    async def no_runtime() -> None:
        return None

    executor = VeloExecutor(SimpleNamespace(), get_runtime=no_runtime,
                            authority=MissionAuthority(store), store=store)
    cancel = CancellationToken()

    async def guard(name: str, args: dict[str, Any]) -> str | None:
        return await executor._guard_dispatch(item, name, args, cancel)

    tools, _ = apply_tool_policy([StructuredTool.from_function(coroutine=click, name="click")])
    ledger = MissionActionLedger(store, execution_id=item.execution_id,
                                mission_id=mission.mission_id)
    async with cua_run_scope(budget=None, run=None, ledger=ledger,
                             mission_guard=guard, mission_strict_audit=True):
        state = cua_target_state.get()
        assert state is not None
        state["apps"] = {9: "com.fixture.safe"}
        state["observed_at_ms"] = int(time.time() * 1000)
        task = asyncio.create_task(tools[0].ainvoke({"pid": 9, "window_id": 2}))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            await store.control(MissionControl(control_id=new_id(), mission_id=mission.mission_id,
                expected_plan_version=1, expected_control_epoch=1, kind="PAUSE"))
            attempt = await store.get_attempt(item.execution_id)
            assert attempt is not None
            assert attempt["dispatch_state"] == "RECONCILING"
            resumed = await store.control(MissionControl(control_id=new_id(),
                mission_id=mission.mission_id, expected_plan_version=1,
                expected_control_epoch=2, kind="RESUME"))
            assert await store.claim_step(mission.mission_id, 1, resumed.control_epoch,
                                          tool_ids=["click"]) is None
            assert effects == ["clicked"]
        finally:
            release.set()
            await task


async def test_invalidated_intent_cannot_append_dispatch_ledger(store: MissionStore) -> None:
    mission, item = await claimed(store)
    await store.control(MissionControl(control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="PAUSE"))
    ledger = MissionActionLedger(store, execution_id=item.execution_id,
                                mission_id=mission.mission_id)
    from assistant.missions.store import MissionStoreError
    with pytest.raises(MissionStoreError):
        await ledger.plan(tool_name="click", args_digest="fixture")


@pytest.mark.parametrize("outcome", ["UNKNOWN", "CONFIRMED"])
async def test_revision_cannot_erase_reconciled_effect(store: MissionStore, outcome: str) -> None:
    from assistant.missions.store import MissionStoreError
    mission, item = await claimed(store)
    await store.mark_dispatched(item.execution_id)
    await store.control(MissionControl(control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="REVISE", revision_request="again"))
    await store.mark_attempt_reconciled(item.execution_id, outcome)
    with pytest.raises(MissionStoreError):
        await store.commit_plan(mission.mission_id, 1, mission.steps, [])


async def test_normal_resume_schedules_pending_work(store: MissionStore, monkeypatch: Any) -> None:
    from tests.unit.test_mission_service import _service

    mission, _ = await claimed(store)
    svc, _ = _service(store)
    scheduled: list[str] = []
    monkeypatch.setattr(svc, "_schedule_resume", scheduled.append)
    await svc.control(MissionControl(control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="PAUSE"))
    await svc.control(MissionControl(control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=2, kind="RESUME"))
    assert scheduled == [mission.mission_id]


@pytest.mark.parametrize("role,registered", [
    ("RECOVER", "submit_recovery_decision"), ("REVIEW", "submit_final_review")])
async def test_controller_selects_registered_submission(
    role: ControllerRole, registered: str,
) -> None:
    from assistant.missions.controller import DeepController
    async def transport(actual_role: str, tool: str, payload: Any) -> Any:
        assert actual_role == role
        assert tool == registered
        return {}
    await DeepController(None)._call(role, "{}", transport)


async def test_plan_secret_is_rejected_before_any_row(store: MissionStore) -> None:
    from assistant.missions.store import MissionStoreError
    mission, _ = await claimed(store)
    secret = "sk-corrective-synthetic-secret-12345678901234567890"
    step = mission.steps[0].model_copy(update={"objective": secret})
    with pytest.raises(MissionStoreError):
        await store.commit_plan(mission.mission_id, 1, [step], [])
    rows = store._conn.execute("SELECT plan_json FROM mission_plans").fetchall()
    assert all(secret not in row[0] for row in rows)


async def test_zero_packet_jev_does_not_call_provider_with_authority(store: MissionStore) -> None:
    from assistant.velo.contracts import DecisionStatus, JevDecision
    from tests.unit.test_mission_executor import _executor, _RecordingJev
    from tests.unit.velo_fakes import standard_tools
    _, item = await claimed(store)
    item = item.model_copy(update={"budget": BudgetLimits(max_jev_calls=0)})
    jev = _RecordingJev(JevDecision(status=DecisionStatus.ACT, selected_id="candidate"))
    executor, _ = _executor(standard_tools(), jev=jev, authority=MissionAuthority(store))
    await executor._choose_candidate(item, [{"element_token": "candidate", "role": "AXButton",
                                            "label": "fixture", "purpose": "primary"}])
    assert jev.requests == []


async def test_targeted_discovery_requires_named_fresh_scope(store: MissionStore) -> None:
    from assistant.missions.authority import AuthorityDenied
    from assistant.missions.contracts import ActionIntent, ScopeObservation
    mission, item = await claimed(store)
    item = item.model_copy(update={"allowed_action_scope": item.allowed_action_scope.model_copy(
        update={"tool_ids": ["get_window_state"]})})
    # _check_surface is the production containment check; intent integrity is
    # orthogonal to whether an unknown PID may be read.
    authority = MissionAuthority(store)
    action = ActionIntent(tool="get_window_state", args={"pid": 99, "window_id": 1},
                          effect_class="READ_ONLY")
    with pytest.raises(AuthorityDenied):
        authority._check_surface(item, item.expected_scope, action, ScopeObservation(
            app_bundle="", pid=99, window_id=1, captured_at_ms=0))
    authority._check_surface(item, item.expected_scope, action, ScopeObservation(
        app_bundle="com.fixture.safe", pid=99, window_id=1,
        captured_at_ms=int(time.time() * 1000)))


async def test_packet_budget_counts_more_than_zero(store: MissionStore) -> None:
    from types import SimpleNamespace

    from assistant.missions.authority import AuthorityDenied
    _, item = await claimed(store)
    item = item.model_copy(update={"budget": BudgetLimits(max_jev_calls=1)})
    async def no_runtime() -> None:
        return None
    authority = MissionAuthority(store)
    executor = VeloExecutor(SimpleNamespace(), get_runtime=no_runtime,
                            authority=authority, store=store)
    first = await executor._reserve_for(item, "jev_calls")
    assert first is not None
    await authority.settle(first, consumed=True)
    # Fresh executor rules out an in-memory-only ceiling.
    resumed = VeloExecutor(SimpleNamespace(), get_runtime=no_runtime,
                           authority=authority, store=store)
    with pytest.raises(AuthorityDenied):
        await resumed._reserve_for(item, "jev_calls")


async def test_expected_payload_cannot_satisfy_page_observation(store: MissionStore,
                                                               tmp_path: Any) -> None:
    from assistant.missions.contracts import CheckSpec, EvidenceCandidate
    from assistant.missions.evidence import EvidenceStore
    mission, item = await claimed(store)
    evidence = EvidenceStore(tmp_path / "e", store)
    ref = await evidence.put(EvidenceCandidate(kind="ui_state", payload={
        "elements": [{"role": "AXStaticText", "label": "unrelated screen"}],
        "resolved_payloads": {"user_text_1": "expected-marker"}},
        captured_at_ms=int(time.time() * 1000)), mission_id=mission.mission_id)
    result = await evidence.verify(CheckSpec(check_id="page", verifier_id="page_state",
        verifier_version="1.0.0", expected={"markers": ["expected-marker"]}), item, refs=[ref])
    assert not result.passed


def test_short_search_has_required_outcome() -> None:
    from assistant.missions.service import _fast_checks_for
    assert any(c.required for c in _fast_checks_for("search_browser", {"query": "AI"}))


async def test_fast_path_does_not_invoke_review_provider(store: MissionStore) -> None:
    from tests.unit.test_mission_service import _request, _safari_world, _service
    from tests.unit.velo_fakes import reset_world
    reset_world()
    _safari_world()
    service, _ = _service(store)
    calls: list[str] = []
    async def transport(role: str, tool: str, payload: Any) -> Any:
        calls.append(role)
        return {"review": {"supported_summary": "fixture"}}
    service._transport_factory = lambda mission: transport
    try:
        result = await service.submit(_request("open safari"), cancel=CancellationToken())
        assert result.status == "COMPLETED"
        assert calls == []
        assert result.budget_usage.total("deep_calls") == 0
    finally:
        reset_world()


async def test_stop_during_ledger_await_prevents_actual_effect(store: MissionStore,
                                                             monkeypatch: Any) -> None:
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import assistant.missions.store as storage

    mission, item = await claimed(store)
    cancel = CancellationToken()
    effects: list[str] = []
    async def click(pid: int, window_id: int) -> str:
        """Synthetic device effect."""
        effects.append("effect")
        return "clicked"
    wrapped, _ = apply_tool_policy([StructuredTool.from_function(coroutine=click, name="click")])
    @asynccontextmanager
    async def run_scope(*args: Any, **kwargs: Any) -> Any:
        # Only the device/session boundary is synthetic; policy scope,
        # authority, ledger, executor and the final effect wrapper are real.
        async with cua_run_scope(budget=None, run=None, ledger=kwargs["ledger"],
                                 mission_guard=kwargs["mission_guard"],
                                 mission_strict_audit=True):
            yield None
    runtime = SimpleNamespace(run_scope=run_scope)
    async def get_runtime() -> Any:
        return runtime
    executor = VeloExecutor(SimpleNamespace(), get_runtime=get_runtime,
                            authority=MissionAuthority(store), store=store)
    original = storage.MissionActionLedger.plan
    async def interleaved(ledger: Any, **kwargs: Any) -> int:
        result = await original(ledger, **kwargs)
        cancel.cancel()
        return result
    monkeypatch.setattr(storage.MissionActionLedger, "plan", interleaved)
    async with executor.work_scope(item, cancel=cancel):
        state = cua_target_state.get()
        assert state is not None
        state["apps"] = {9: "com.fixture.safe"}
        state["observed_at_ms"] = int(time.time() * 1000)
        result = await wrapped[0].ainvoke({"pid": 9, "window_id": 2})
    assert effects == []
    assert "cancel" in result.lower()
