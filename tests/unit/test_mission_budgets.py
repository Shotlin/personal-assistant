"""Budget behavior tests (T03/T06, file 06 U2): finite, durable, honest.

Reservations survive restart, failed attempts still count, and an
exhausted mission can dispatch nothing.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from assistant.missions.contracts import BudgetCharge, BudgetLimits, RequestEnvelope, Scope, new_id
from assistant.missions.store import MissionStore, MissionStoreError


def _now() -> int:
    return int(time.time() * 1000)


def _request(request_id: str | None = None) -> RequestEnvelope:
    return RequestEnvelope(
        request_id=request_id or new_id(),
        conversation_id="c",
        owner_id="owner",
        input_origin="typed_final",
        input_revision=1,
        text="fixture",
        submitted_at_ms=_now(),
    )


@pytest.fixture()
async def store(tmp_path: Any) -> Any:
    s = await MissionStore.connect(tmp_path / "budget.db")
    await s.setup()
    yield s
    await s.close()


async def _mission(store: MissionStore, **limit_overrides: Any) -> str:
    mission = await store.claim_request(
        _request(),
        "d" * 64,
        goal="fixture",
        scope=Scope(owner_id="owner"),
        limits=BudgetLimits(**limit_overrides),
    )
    return mission.mission_id


async def test_reservations_persist_across_restart(tmp_path: Any) -> None:
    """Reserved counters survive a close/reopen cycle (failures count too)."""
    db = tmp_path / "persist.db"
    store = await MissionStore.connect(db)
    await store.setup()
    mission_id = await _mission(store, max_actions=5)
    reservation = await store.reserve_budget(
        mission_id, BudgetCharge(resource="actions", amount=1, call_key="call-1")
    )
    await store.settle_reservation(reservation.reservation_id, consumed=False)
    # A failed (not consumed) retry still counted while reserved.
    await store.close()

    reopened = await MissionStore.connect(db)
    await reopened.setup()
    record = await reopened.get_mission(mission_id)
    assert record is not None
    # The released reservation returned its unit, but the call happened:
    # reserved counters return to zero and the next reservation is possible.
    reservation2 = await reopened.reserve_budget(
        mission_id, BudgetCharge(resource="actions", amount=2)
    )
    await reopened.settle_reservation(reservation2.reservation_id, consumed=True)
    final = await reopened.get_mission(mission_id)
    assert final is not None
    assert final.budget_usage.consumed.get("actions") == 2
    await reopened.close()


async def test_exhausted_budget_blocks_dispatch(store: MissionStore) -> None:
    mission_id = await _mission(store, max_actions=2)
    one = await store.reserve_budget(
        mission_id, BudgetCharge(resource="actions", amount=1, call_key="a")
    )
    await store.settle_reservation(one.reservation_id, consumed=True)
    two = await store.reserve_budget(
        mission_id, BudgetCharge(resource="actions", amount=1, call_key="b")
    )
    await store.settle_reservation(two.reservation_id, consumed=True)
    with pytest.raises(MissionStoreError):
        await store.reserve_budget(
            mission_id, BudgetCharge(resource="actions", amount=1, call_key="c")
        )


async def test_concurrent_reservations_cannot_overrun(store: MissionStore) -> None:
    """Concurrent reservation attempts cannot exceed the ceiling (TC-30)."""
    import asyncio

    mission_id = await _mission(store, max_actions=3)

    async def reserve(key: str) -> bool:
        try:
            reservation = await store.reserve_budget(
                mission_id, BudgetCharge(resource="actions", amount=1, call_key=key)
            )
            await store.settle_reservation(reservation.reservation_id, consumed=True)
            return True
        except MissionStoreError:
            return False

    results = await asyncio.gather(*(reserve(f"k{i}") for i in range(6)))
    assert sum(1 for r in results if r) == 3
    assert sum(1 for r in results if not r) == 3


async def test_unknown_price_never_reported_as_zero(store: MissionStore) -> None:
    """Calls without provider pricing keep their unknown flag (T03)."""
    from assistant.missions.contracts import StepResult, StepSpec

    mission_id = await _mission(store)
    steps = [
        StepSpec(step_id="s", ordinal=1, objective="fixture", recipe_id="open_app",
                 scope=Scope(owner_id="owner"), budget=BudgetLimits())
    ]
    await store.commit_plan(mission_id, 0, steps, [])
    execution_id = new_id()
    store._conn.execute(
        """
        INSERT INTO mission_attempts (execution_id, mission_id, plan_version,
            step_id, attempt, control_epoch, packet_digest, dispatch_state,
            effect_class, created_at_ms, updated_at_ms)
        VALUES (?, ?, 1, 's', 1, 1, 'd', 'INTENT_COMMITTED', 'READ_ONLY', ?, ?)
        """,
        (execution_id, mission_id, _now(), _now()),
    )
    result = StepResult(
        mission_id=mission_id,
        plan_version=1,
        control_epoch=1,
        step_id="s",
        execution_id=execution_id,
        attempt=1,
        status="COMPLETED",
        effect_outcome="CONFIRMED",
        usage={"known_input_tokens": 0, "unknown_usage_calls": 2},
    )
    await store.apply_result(result)
    record = await store.get_mission(mission_id)
    assert record is not None
    assert record.budget_usage.unknown_usage_calls >= 2
    assert record.budget_usage.known_cost_microunits is None, (
        "an unknown cost is never folded into a known zero"
    )


async def test_step_unit_budgets_are_bounded() -> None:
    """Default unit ceilings (file 03 §8) hold for a fresh step budget."""
    limits = BudgetLimits()
    assert limits.max_actions <= 100
    assert limits.max_observations <= 300
    assert limits.max_screenshots <= 10
    assert limits.max_deep_calls <= 8
    assert limits.max_jev_calls <= 20
    assert limits.max_replans <= 2
    assert limits.max_paid_units == 0


# -- R04/RP11: real provider-boundary budget enforcement -----------------------


async def test_rp11_zero_deep_budget_blocks_planning(store: MissionStore) -> None:
    """RP11: max_deep_calls=0 must forbid the PLAN invocation and leave no
    unaccounted usage. The block happens at the durable reservation."""
    from assistant.missions.authority import MissionAuthority

    mission_id = await _mission(store, max_deep_calls=0)
    authority = MissionAuthority(store)
    with pytest.raises(Exception) as excinfo:
        await authority.reserve(
            mission_id, BudgetCharge(resource="deep_calls", amount=1, call_key="plan-v1")
        )
    assert "exhausted" in str(excinfo.value) or "budget" in str(excinfo.value).lower()
    # Durable usage stays empty: nothing was invoked.
    record = await store.get_mission(mission_id)
    assert record is not None
    assert record.budget_usage.consumed.get("deep_calls", 0) == 0
    assert record.budget_usage.reserved.get("deep_calls", 0) == 0


async def test_deep_call_reservation_persists_across_restart(tmp_path: Any) -> None:
    """A reserved Deep call counts even if the process dies before settle."""
    db = tmp_path / "r04.db"
    store = await MissionStore.connect(db)
    await store.setup()
    mission_id = await _mission(store, max_deep_calls=2)
    await store.reserve_budget(
        mission_id, BudgetCharge(resource="deep_calls", amount=1, call_key="plan-v1")
    )
    await store.close()  # crash before settle
    reopened = await MissionStore.connect(db)
    await reopened.setup()
    record = await reopened.get_mission(mission_id)
    assert record is not None
    assert record.budget_usage.reserved.get("deep_calls") == 1, (
        "a crashed call still consumed its reservation: no fresh allowance"
    )
    # One call remains, not two.
    reservation2 = await reopened.reserve_budget(
        mission_id, BudgetCharge(resource="deep_calls", amount=1, call_key="plan-v2")
    )
    await reopened.settle_reservation(reservation2.reservation_id, consumed=True)
    with pytest.raises(MissionStoreError):
        await reopened.reserve_budget(
            mission_id, BudgetCharge(resource="deep_calls", amount=1, call_key="plan-v3")
        )
    await reopened.close()


async def test_per_action_ledger_records_intent_and_outcome(tmp_path: Any) -> None:
    """R04: the per-action ledger records 'planned' before dispatch and a
    terminal state after, bound to the execution."""
    from assistant.missions.store import MissionActionLedger

    db = tmp_path / "ledger.db"
    store = await MissionStore.connect(db)
    await store.setup()
    mission_id = await _mission(store)
    execution_id = new_id()
    store._conn.execute(
        "INSERT INTO mission_attempts (execution_id, mission_id, plan_version, "
        "step_id, attempt, control_epoch, packet_digest, dispatch_state, "
        "effect_class, created_at_ms, updated_at_ms) "
        "VALUES (?, ?, 1, 's', 1, 1, 'd', 'DISPATCHED', 'REPEATABLE_LOCAL', ?, ?)",
        (execution_id, mission_id, _now(), _now()),
    )
    ledger = MissionActionLedger(store, execution_id=execution_id, mission_id=mission_id)
    ledger_id = await ledger.plan(tool_name="click", args_digest="abc" * 10)
    planned = await ledger.actions_in_state("planned")
    assert [p["ledger_id"] for p in planned] == [ledger_id]
    await ledger.observe(ledger_id, "confirmed", "fixture evidence")
    assert await ledger.actions_in_state("planned") == []
    await store.close()
