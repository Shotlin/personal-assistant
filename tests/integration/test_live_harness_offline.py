"""Offline validation of the authorized live harnesses (D21).

The measurement logic the live suites use — scope admission, budget
admission, stop-during-work, wrong-focus refusal — is validated HERE with
fakes before any authorized live run offers itself. No live driver, audio
device, or bundle launch happens in this file.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from assistant.missions.authority import MissionAuthority
from assistant.missions.contracts import (
    BudgetLimits,
    RequestEnvelope,
    Scope,
    StepResult,
    StepSpec,
    new_id,
)
from assistant.missions.controller import DeepController
from assistant.missions.evidence import EvidenceStore
from assistant.missions.executor import VeloExecutor
from assistant.missions.service import MissionService
from assistant.missions.store import MissionStore
from tests.e2e._live import budgets_from_config, mission_scope_from_config


def test_scope_admission_fails_on_missing_fields() -> None:
    """Unknown or absent required authorization fields fail BEFORE any
    effect — the harness never guesses a scope."""
    with pytest.raises(AssertionError, match="allowed_apps"):
        mission_scope_from_config({})
    with pytest.raises(AssertionError, match="account_ref"):
        mission_scope_from_config({"allowed_apps": ["com.fixture.safe"]})
    with pytest.raises(AssertionError, match="max_deep_calls"):
        budgets_from_config({"allowed_apps": ["com.fixture.safe"],
                             "account_ref": "a", "max_jev_calls": 2})
    scope = mission_scope_from_config({
        "allowed_apps": ["com.fixture.safe"], "account_ref": "acct-1",
        "allowed_origins": ["https://trusted.example"]})
    assert scope.account_ref == "acct-1"
    assert scope.allowed_origins == ["https://trusted.example"]


async def test_stop_during_work_measurement_with_fake_service(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The stop-during-work measurement is validated offline against a fake
    runtime: the stop fires while the mission OWNS the lease, and the
    report carries an honest latency and post-stop status."""
    from tests.e2e._live import run_stop_during_work
    from tests.unit.velo_fakes import (
        SHARED_APPS,
        FakeRuntime,
        app_entry,
        reset_world,
        standard_tools,
        window_state_payload,
    )

    reset_world()
    SHARED_APPS["list"] = [app_entry("Fixture", pid=9, running=True, active=True,
                                     bundle_id="com.fixture.safe")]
    SHARED_APPS["windows"][9] = [
        {"window_id": 2, "is_on_screen": True, "bounds": {"height": 600, "width": 800}}]
    SHARED_APPS["state"][(9, 2)] = window_state_payload(
        [{"role": "AXButton", "label": "fixture-mark-go", "element_token": "tok-go"}],
        pid=9, window_id=2)
    store = await MissionStore.connect(
        tmp_path / "harness.db")
    await store.setup()
    scope = Scope(owner_id="sani-local", allowed_apps=["com.fixture.safe"],
                  permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"})
    request = RequestEnvelope(request_id=new_id(), conversation_id="h",
                              owner_id="sani-local", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(request, "d" * 64, goal="x", scope=scope,
                                        limits=BudgetLimits())
    await store.commit_plan(mission.mission_id, 0, [StepSpec(
        step_id="s1", ordinal=1, objective="click fixture", recipe_id="semantic_ui",
        scope=scope, budget=BudgetLimits())], [])
    runtime = FakeRuntime(standard_tools())

    async def get_runtime() -> FakeRuntime:
        return runtime

    service = MissionService(
        type("S", (), {})(), store=store, authority=MissionAuthority(store),
        evidence=EvidenceStore(tmp_path / "ev", store),
        executor=VeloExecutor(
            type("S", (), {})(), get_runtime=get_runtime,
            authority=MissionAuthority(store),
            evidence=EvidenceStore(tmp_path / "ev", store),
            store=store),
        controller=DeepController(None),
    )
    # A deterministic slow unit keeps the lease window open long enough for
    # the stop-during-work measurement to fire mid-work.
    from assistant.missions import executor as _exec_mod

    original_execute = _exec_mod.VeloExecutor.execute_work_item

    async def slow_execute(self: Any, item: Any, **kwargs: Any) -> StepResult:
        await asyncio.sleep(1.0)
        return await original_execute(self, item, **kwargs)

    monkeypatch.setattr(_exec_mod.VeloExecutor, "execute_work_item", slow_execute)
    config = {"max_stop_latency_ms": 1000}
    report = await run_stop_during_work(service, config, "scroll down 3")
    assert report["stopped"] is True
    assert report["latency_within_bound"] is True
    # The in-flight unit settles right after the lease stop; poll briefly.
    ran = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ran = await store.get_mission(str(report["mission_id"]))
        assert ran is not None
        if ran.status not in {"RUNNING", "PLANNED", "WAITING_EXTERNAL"}:
            break
        await asyncio.sleep(0.05)
    assert ran is not None and ran.status in {
        "PAUSED", "CANCELLED", "BLOCKED", "COMPLETED", "FAILED",
        "NEEDS_APPROVAL"}, report
    reset_world()
    await store.close()


async def test_wrong_focus_refusal_measurement(tmp_path: Any) -> None:
    """The wrong-focus measurement reports a non-completed mission for a
    command outside the authorized scope — validated with the fake world."""
    from tests.e2e._live import run_wrong_focus
    from tests.unit.velo_fakes import (
        SHARED_APPS,
        FakeRuntime,
        app_entry,
        reset_world,
        standard_tools,
        window_state_payload,
    )

    reset_world()
    SHARED_APPS["list"] = [app_entry("Other", pid=3, running=True, active=True,
                                     bundle_id="com.other.app")]
    SHARED_APPS["windows"][3] = [
        {"window_id": 1, "is_on_screen": True, "bounds": {"height": 600, "width": 800}}]
    SHARED_APPS["state"][(3, 1)] = window_state_payload(
        [{"role": "AXButton", "label": "other-mark", "element_token": "tok-o"}],
        pid=3, window_id=1)
    store = await MissionStore.connect(
        tmp_path / "wf.db")
    await store.setup()
    scope = Scope(owner_id="sani-local", allowed_apps=["com.fixture.safe"],
                  permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"})
    request = RequestEnvelope(request_id=new_id(), conversation_id="wf",
                              owner_id="sani-local", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(request, "d" * 64, goal="x", scope=scope,
                                        limits=BudgetLimits())
    await store.commit_plan(mission.mission_id, 0, [StepSpec(
        step_id="s1", ordinal=1, objective="fixture", recipe_id="semantic_ui",
        scope=scope, budget=BudgetLimits())], [])
    runtime = FakeRuntime(standard_tools())

    async def get_runtime() -> FakeRuntime:
        return runtime

    service = MissionService(
        type("S", (), {})(), store=store, authority=MissionAuthority(store),
        evidence=EvidenceStore(tmp_path / "ev", store),
        executor=VeloExecutor(
            type("S", (), {})(), get_runtime=get_runtime,
            authority=MissionAuthority(store),
            evidence=EvidenceStore(tmp_path / "ev", store),
            store=store),
        controller=DeepController(None),
    )
    report = await run_wrong_focus(service, {}, "open the other app")
    assert report["completed"] is False, (
        "an out-of-scope command must not complete")
    reset_world()
    await store.close()
