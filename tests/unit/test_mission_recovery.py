"""Recovery tests (T07, file 06 U4): reconcile, never replay blindly."""

from __future__ import annotations

import time
from typing import Any

import pytest

from assistant.missions.contracts import BudgetLimits, Scope, StepSpec, new_id
from assistant.missions.recovery import Reconciler
from assistant.missions.store import MissionStore


def _now() -> int:
    return int(time.time() * 1000)


@pytest.fixture()
async def store(tmp_path: Any) -> Any:
    s = await MissionStore.connect(tmp_path / "recovery.db")
    await s.setup()
    yield s
    await s.close()


async def _mission_with_uncertain_attempt(
    store: MissionStore, *, effect_class: str = "REPEATABLE_LOCAL", external: str = ""
) -> str:
    from assistant.missions.contracts import RequestEnvelope

    request = RequestEnvelope(
        request_id=new_id(),
        conversation_id="c",
        owner_id="owner",
        input_origin="typed_final",
        input_revision=1,
        text="fixture",
        submitted_at_ms=_now(),
    )
    mission = await store.claim_request(
        request, "d" * 64, goal="fixture", scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    steps = [
        StepSpec(step_id="s1", ordinal=1, objective="fixture", recipe_id="open_app",
                 scope=Scope(owner_id="owner"), budget=BudgetLimits(),
                 effect_class=effect_class)  # type: ignore[arg-type]
    ]
    await store.commit_plan(mission.mission_id, 0, steps, [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    # Mark the attempt uncertain (as a restart would).
    await store.recover_inflight("host-gen")
    if external:
        store._conn.execute(
            "UPDATE mission_attempts SET external_ids=? WHERE execution_id=?",
            (external, item.execution_id),
        )
    return item.execution_id


async def test_no_probe_local_effect_stays_unknown(store: MissionStore) -> None:
    """Without a probe, an uncertain local mutation is UNKNOWN (RF-17)."""
    execution_id = await _mission_with_uncertain_attempt(store)
    reconciler = Reconciler(store)
    result = await reconciler.reconcile(execution_id)
    assert result.outcome == "UNKNOWN"
    assert result.retriable is False


async def test_read_only_attempt_reconciles_to_no_effect(store: MissionStore) -> None:
    execution_id = await _mission_with_uncertain_attempt(store, effect_class="READ_ONLY")
    reconciler = Reconciler(store)
    result = await reconciler.reconcile(execution_id)
    assert result.outcome == "NO_EFFECT"
    assert result.retriable is True, "reads may retry within budget after reconnect"


async def test_probe_confirmation_blocks_replay(store: MissionStore) -> None:
    """A probe proving the effect landed: CONFIRMED, never retried."""
    execution_id = await _mission_with_uncertain_attempt(store, external="op-1")

    from assistant.missions.recovery import ReconciliationOutcome

    async def probe(op_id: str) -> ReconciliationOutcome:
        return "CONFIRMED"

    reconciler = Reconciler(store, probe=probe)
    result = await reconciler.reconcile(execution_id)
    assert result.outcome == "CONFIRMED"
    assert result.retriable is False


async def test_probe_no_effect_permits_one_retry(store: MissionStore) -> None:
    execution_id = await _mission_with_uncertain_attempt(store, external="op-2")

    from assistant.missions.recovery import ReconciliationOutcome

    async def probe(op_id: str) -> ReconciliationOutcome:
        return "NO_EFFECT"

    reconciler = Reconciler(store, probe=probe)
    result = await reconciler.reconcile(execution_id)
    assert result.outcome == "NO_EFFECT"
    assert result.retriable is True, "proven NO_EFFECT with valid allowance may retry"


async def test_failing_probe_stays_unknown(store: MissionStore) -> None:
    execution_id = await _mission_with_uncertain_attempt(store, external="op-3")

    from assistant.missions.recovery import ReconciliationOutcome

    async def probe(op_id: str) -> ReconciliationOutcome:
        raise RuntimeError("probe transport down")

    reconciler = Reconciler(store, probe=probe)
    result = await reconciler.reconcile(execution_id)
    assert result.outcome == "UNKNOWN"
    assert result.retriable is False


async def test_unknown_execution_reconciles_safe(store: MissionStore) -> None:
    reconciler = Reconciler(store)
    result = await reconciler.reconcile(new_id())
    assert result.outcome == "UNKNOWN"


async def test_reconciliation_recorded_on_attempt(store: MissionStore) -> None:
    execution_id = await _mission_with_uncertain_attempt(store, external="op-4")

    from assistant.missions.recovery import ReconciliationOutcome

    async def probe(op_id: str) -> ReconciliationOutcome:
        return "CONFIRMED"

    reconciler = Reconciler(store, probe=probe)
    await reconciler.reconcile(execution_id)
    attempt = await store.get_attempt(execution_id)
    assert attempt is not None
    assert attempt["effect_outcome"] == "CONFIRMED"


async def test_np03_reconciliation_aggregates_all_operations(store: MissionStore) -> None:
    """NP03 regression: mixed NO_EFFECT and CONFIRMED across operations must
    aggregate conservatively — every ID is probed, and one confirmed
    operation prevents replay of the whole attempt."""
    execution_id = await _mission_with_uncertain_attempt(store, external="first,second")
    probed: list[str] = []

    from assistant.missions.recovery import ReconciliationOutcome

    async def probe(op_id: str) -> ReconciliationOutcome:
        probed.append(op_id)
        return "NO_EFFECT" if op_id == "first" else "CONFIRMED"

    reconciler = Reconciler(store, probe=probe)
    result = await reconciler.reconcile(execution_id)
    assert sorted(probed) == ["first", "second"], "every external ID must be probed"
    assert result.outcome == "CONFIRMED"
    assert result.retriable is False


async def test_np03_all_no_effect_is_required_for_retry(store: MissionStore) -> None:
    """Retry is only on the table when EVERY operation is proven NO_EFFECT."""
    execution_id = await _mission_with_uncertain_attempt(store, external="a,b,c")

    from assistant.missions.recovery import ReconciliationOutcome

    async def probe(op_id: str) -> ReconciliationOutcome:
        return "NO_EFFECT"

    result = await Reconciler(store, probe=probe).reconcile(execution_id)
    assert result.retriable is True
    assert result.outcome == "NO_EFFECT"
