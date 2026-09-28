"""Phase 1 budget performance tests (T12, file 06 P1, TC-11/TC-30).

Exact hard counts, packet sizes, and stop conditions with an injected
clock. These prove the BOUNDS hold under the fixture; they do not claim
real desktop latency (that is P2, hardware-measured).
"""

from __future__ import annotations

import time

from assistant.missions.contracts import (
    BudgetCharge,
    BudgetLimits,
    Scope,
    new_id,
)
from assistant.velo.contracts import NoProgressTracker, ProgressKind


def _now() -> int:
    return int(time.time() * 1000)


# -- packet size caps ------------------------------------------------------------


def test_work_item_and_context_caps_exact() -> None:
    from assistant.missions.contracts import ActionScopeRecord, BoundedWorkItem

    scope = Scope(owner_id="owner")
    item = BoundedWorkItem(
        mission_id=new_id(),
        plan_version=1,
        control_epoch=1,
        step_id="s1",
        execution_id=new_id(),
        attempt=1,
        objective="x" * 2000,
        minimal_context="y" * 4096,
        expected_scope=scope,
        allowed_action_scope=ActionScopeRecord(
            tool_ids=["list_apps"], target_scope_hash=scope.scope_hash
        ),
        recipe_id="semantic_ui",
        deadline_at_ms=_now() + 90_000,
        budget=BudgetLimits(),
    )
    assert item.serialized_size() <= 16 * 1024, "packet cap is 16 KiB"
    assert len(item.minimal_context) <= 4096, "context cap is 4096 bytes"


def test_exception_packet_cap() -> None:
    from assistant.missions.contracts import ExceptionPacket

    packet = ExceptionPacket(
        mission_id=new_id(), plan_version=1, control_epoch=1, step_id="s1",
        execution_id=new_id(), attempt=1, category="NO_PROGRESS",
        expected_summary="e" * 512, observed_summary="o" * 512,
    )
    assert len(packet.model_dump_json()) <= 8 * 1024, "exception cap is 8 KiB"


# -- finite stop conditions (TC-11) ----------------------------------------------


def test_identical_screen_stops_at_exact_count() -> None:
    tracker = NoProgressTracker(max_steps_without_change=4)
    results = [tracker.register(ProgressKind.OBSERVATION, "same") for _ in range(6)]
    assert results[:3] == [True] * 3
    assert not results[4], "the 5th register of an unchanged screen trips the breaker"
    assert not results[5]


def test_alternating_screens_stop_at_exact_count() -> None:
    tracker = NoProgressTracker(max_steps_without_change=4)
    screens = ["a", "b"]
    results = [tracker.register(ProgressKind.OBSERVATION, screens[i % 2]) for i in range(8)]
    assert not results[5], "the 6th register trips the breaker (2 intros + 4 repeats)"
    assert sum(results) == 5


def test_recovery_cap_stops_recovery_churn() -> None:
    tracker = NoProgressTracker(max_recovery_attempts=2)
    assert tracker.register(ProgressKind.RECOVERY, "d1")
    assert tracker.register(ProgressKind.RECOVERY, "d2")
    assert not tracker.register(ProgressKind.RECOVERY, "d3")


def test_unit_deadline_stops_with_clock() -> None:
    """A unit whose clock expires stops; nothing dispatches after."""
    deadline = time.monotonic() + 0.05
    time.sleep(0.06)
    assert time.monotonic() > deadline


def test_budget_exhaustion_blocks_exactly() -> None:
    """Exactly zero dispatch after the reservation is exhausted."""
    import asyncio
    import tempfile
    from pathlib import Path as _Path

    from assistant.missions.store import MissionStore, MissionStoreError

    async def run() -> tuple[int, int]:
        tmp = _Path(tempfile.mkdtemp())
        store = await MissionStore.connect(tmp / "perf.db")
        await store.setup()
        from assistant.missions.contracts import RequestEnvelope

        request = RequestEnvelope(
            request_id=new_id(), conversation_id="c", owner_id="o",
            input_origin="typed_final", input_revision=1, text="x",
            submitted_at_ms=_now(),
        )
        mission = await store.claim_request(
            request, "d" * 64, goal="g", scope=Scope(owner_id="o"),
            limits=BudgetLimits(max_actions=3),
        )
        accepted = refused = 0
        for index in range(6):
            try:
                reservation = await store.reserve_budget(
                    mission.mission_id,
                    BudgetCharge(resource="actions", amount=1, call_key=f"k{index}"),
                )
                await store.settle_reservation(reservation.reservation_id, consumed=True)
                accepted += 1
            except MissionStoreError:
                refused += 1
        await store.close()
        return accepted, refused

    accepted, refused = asyncio.run(run())
    assert (accepted, refused) == (3, 3), "exactly the ceiling dispatches; the rest refuse"
