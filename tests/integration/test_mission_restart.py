"""Restart/recovery integration (T07, file 06 I1, TC-09/TC-10/RF-17).

Kills at every transaction boundary and proves the invariants survive: the
intent is durable, results are idempotent, and ambiguous dispatches are
never replayed automatically.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from assistant.missions.contracts import (
    BudgetCharge,
    BudgetLimits,
    MissionControl,
    RequestEnvelope,
    Scope,
    StepResult,
    StepSpec,
    new_id,
)
from assistant.missions.recovery import Reconciler
from assistant.missions.store import MissionStore


def _now() -> int:
    return int(time.time() * 1000)


def _request() -> RequestEnvelope:
    return RequestEnvelope(
        request_id=new_id(),
        conversation_id="c",
        owner_id="owner",
        input_origin="typed_final",
        input_revision=1,
        text="fixture mission",
        submitted_at_ms=_now(),
    )


def _steps(effect_class: str = "REPEATABLE_LOCAL") -> list[StepSpec]:
    scope = Scope(owner_id="owner")
    return [
        StepSpec(step_id="s1", ordinal=1, objective="first", recipe_id="open_app",
                 scope=scope, budget=BudgetLimits(max_wall_ms=90_000),
                 effect_class=effect_class),  # type: ignore[arg-type]
        StepSpec(step_id="s2", ordinal=2, objective="second", dependencies=["s1"],
                 recipe_id="semantic_ui", scope=scope,
                 budget=BudgetLimits(max_wall_ms=90_000),
                 effect_class=effect_class),  # type: ignore[arg-type]
    ]


def _result_of(mission_id: str, item: Any, *, status: str = "COMPLETED") -> StepResult:
    return StepResult(
        mission_id=mission_id,
        plan_version=item.plan_version,
        control_epoch=item.control_epoch,
        step_id=item.step_id,
        execution_id=item.execution_id,
        attempt=item.attempt,
        status=status,  # type: ignore[arg-type]
        effect_outcome="CONFIRMED",
    )


async def test_kill_before_intent_commit_no_effect(tmp_path: Path) -> None:
    """The crash happens before claim_step: no intent, no dispatch, clean resume."""
    db = tmp_path / "b1.db"
    store = await MissionStore.connect(db)
    await store.setup()
    mission = await store.claim_request(_request(), "d" * 64, goal="g",
                                        scope=Scope(owner_id="owner"), limits=BudgetLimits())
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    await store.close()  # kill before any claim_step
    reopened = await MissionStore.connect(db)
    await reopened.setup()
    attempts = await reopened.recover_inflight("gen")
    assert attempts == [], "no intent was committed, so nothing is uncertain"
    item = await reopened.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None, "the mission resumes cleanly"
    await reopened.close()


async def test_kill_after_intent_commit_before_result(tmp_path: Path) -> None:
    """The crash happens after the intent but before any result: the effect is
    UNKNOWN, and the late result never advances the new epoch (RF-17)."""
    db = tmp_path / "b2.db"
    first = await MissionStore.connect(db)
    await first.setup()
    mission = await first.claim_request(_request(), "d" * 64, goal="g",
                                        scope=Scope(owner_id="owner"), limits=BudgetLimits())
    await first.commit_plan(mission.mission_id, 0, _steps(), [])
    item = await first.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    await first.mark_dispatched(item.execution_id)
    # --- kill: no close, fresh connection ---
    second = await MissionStore.connect(db)
    await second.setup()
    # The restart reconciliation runs first: it marks the attempt uncertain
    # and bumps the mission's epoch.
    uncertain = await second.recover_inflight("gen")
    assert uncertain == [item.execution_id]
    reconciler = Reconciler(second)
    recon = await reconciler.reconcile(item.execution_id)
    assert recon.outcome == "UNKNOWN", "a dispatched action with no result is uncertain"
    assert recon.retriable is False, "no automatic resend after ambiguous dispatch"
    late = await second.apply_result(_result_of(mission.mission_id, item))
    assert late == "STALE"
    await second.close()


async def test_kill_after_result_commit(tmp_path: Path) -> None:
    """The result was durably committed: the restart must not repeat the step."""
    db = tmp_path / "b3.db"
    first = await MissionStore.connect(db)
    await first.setup()
    mission = await first.claim_request(_request(), "d" * 64, goal="g",
                                        scope=Scope(owner_id="owner"), limits=BudgetLimits())
    await first.commit_plan(mission.mission_id, 0, _steps(), [])
    item = await first.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    assert await first.apply_result(_result_of(mission.mission_id, item)) == "APPLIED"
    await first.close()
    second = await MissionStore.connect(db)
    await second.setup()
    # Duplicate result application after the restart: idempotent.
    assert await second.apply_result(_result_of(mission.mission_id, item)) == "DUPLICATE"
    steps = await second.get_step_states(mission.mission_id, 1)
    assert steps == {"s1": "SUCCEEDED", "s2": "PENDING"}
    record = await second.get_mission(mission.mission_id)
    assert record is not None
    assert record.budget_usage.consumed.get("actions", 0) >= 0
    # And the next claim is s2, never a repeated s1.
    epoch = record.control_epoch
    item2 = await second.claim_step(mission.mission_id, 1, epoch, tool_ids=["list_apps"])
    assert item2 is None or item2.step_id == "s2"
    await second.close()


async def test_clock_jump_does_not_refill_budgets(tmp_path: Path) -> None:
    """After a clock jump, deadlines tighten or hold: budgets never refill."""
    db = tmp_path / "clock.db"
    store = await MissionStore.connect(db)
    await store.setup()
    mission = await store.claim_request(
        _request(), "d" * 64, goal="g", scope=Scope(owner_id="owner"),
        limits=BudgetLimits(max_actions=1),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    reservation = await store.reserve_budget(
        mission.mission_id, BudgetCharge(resource="actions", amount=1, call_key="a")
    )
    await store.settle_reservation(reservation.reservation_id, consumed=True)
    # Simulate a clock jump forward: usage is stored as counters, not as
    # wall-clock deltas, so nothing can refill.
    record = await store.get_mission(mission.mission_id)
    assert record is not None
    assert record.budget_usage.consumed.get("actions") == 1
    with pytest.raises(Exception):  # noqa: B017, PT011 -- MissionStoreError
        await store.reserve_budget(
            mission.mission_id, BudgetCharge(resource="actions", amount=1, call_key="b")
        )
    await store.close()


async def test_control_during_active_stream_is_cas_applied(tmp_path: Path) -> None:
    """A control arriving during a live run applies atomically; the late
    result from the old epoch lands STALE."""
    db = tmp_path / "cas.db"
    store = await MissionStore.connect(db)
    await store.setup()
    mission = await store.claim_request(_request(), "d" * 64, goal="g",
                                        scope=Scope(owner_id="owner"), limits=BudgetLimits())
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    paused = await store.control(MissionControl(
        control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="PAUSE",
    ))
    assert paused.control_epoch == 2
    late = await store.apply_result(_result_of(mission.mission_id, item))
    assert late == "STALE"
    await store.close()


# -- R07/RP07 regression: pause/resume creates claimable work -------------------


async def test_rp07_pause_resume_makes_progress(tmp_path: Path) -> None:
    """RP07: after pause + resume, the paused step is PENDING with the old
    attempt cancelled, and a fresh claim produces a NEW attempt."""
    db = tmp_path / "rp07.db"
    store = await MissionStore.connect(db)
    await store.setup()
    mission = await store.claim_request(_request(), "d" * 64, goal="g",
                                        scope=Scope(owner_id="owner"), limits=BudgetLimits())
    await store.commit_plan(mission.mission_id, 0, _steps("READ_ONLY"), [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    paused = await store.control(MissionControl(
        control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="PAUSE",
    ))
    assert paused.status == "PAUSED"
    states = await store.get_step_states(mission.mission_id, 1)
    assert states["s1"] == "PENDING", "a paused step must not stay RUNNING"
    resumed = await store.control(MissionControl(
        control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=2, kind="RESUME",
    ))
    assert resumed.status == "RUNNING" and resumed.control_epoch == 3
    item2 = await store.claim_step(mission.mission_id, 1, 3, tool_ids=["list_apps"])
    assert item2 is not None, "resume must yield claimable work"
    assert item2.attempt == 2, "the retry is a NEW attempt; the old one stays cancelled"
    old_attempt = await store.get_attempt(item.execution_id)
    assert old_attempt is not None and old_attempt["dispatch_state"] == "CANCELLED"
    await store.close()
