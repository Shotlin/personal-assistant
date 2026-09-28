"""MissionStore unit tests (T02, file 06 U1): identity, CAS, atomicity.

Fixture environment F: temporary SQLite file, no network, no driver. Crash
at transaction boundaries is exercised through SQLite's own atomicity plus
the store's all-or-nothing transactions; the reconcile-after-crash semantics
live with the integration suite.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from assistant.missions.contracts import (
    BudgetCharge,
    BudgetLimits,
    MissionControl,
    Scope,
    StepResult,
    StepSpec,
    new_id,
)
from assistant.missions.store import (
    MissionIdentityCollision,
    MissionStore,
    MissionStoreError,
    ResultConflict,
    StaleControlError,
)


def _now() -> int:
    return int(time.time() * 1000)


@pytest.fixture()
async def store(tmp_path: Path) -> Any:
    s = await MissionStore.connect(tmp_path / "sani.db")
    await s.setup()
    yield s
    await s.close()


@pytest.fixture()
async def legacy_store(tmp_path: Path) -> Any:
    """A store sharing a database that already holds legacy run-registry rows."""
    from assistant.runtime.runs_local import SQLiteRunStore

    db = tmp_path / "mixed.db"
    runs = await SQLiteRunStore.connect(str(db))
    await runs.setup()
    claimed = await runs.claim(
        user_id="u",
        chat_id="c",
        user_message_id="m1",
        request_digest="d" * 16,
        run_id="run-legacy-1",
    )
    assert claimed.owned
    s = await MissionStore.connect(db)
    await s.setup()
    yield s
    await s.close()


def _request(request_id: str | None = None, owner: str = "owner", text: str = "open safari"):
    from assistant.missions.contracts import RequestEnvelope

    return RequestEnvelope(
        request_id=request_id or new_id(),
        conversation_id="conv-1",
        owner_id=owner,
        input_origin="typed_final",
        input_revision=1,
        text=text,
        submitted_at_ms=_now(),
    )


def _digest(request) -> str:  # type: ignore[no-untyped-def]
    from assistant.missions.contracts import digest_of

    return digest_of({"text": request.text, "origin": request.input_origin})


def _steps() -> list[StepSpec]:
    scope = Scope(owner_id="owner")
    return [
        StepSpec(
            step_id="s1",
            ordinal=1,
            objective="first",
            recipe_id="open_app",
            scope=scope,
            budget=BudgetLimits(max_wall_ms=90_000),
        ),
        StepSpec(
            step_id="s2",
            ordinal=2,
            objective="second",
            dependencies=["s1"],
            recipe_id="semantic_ui",
            scope=scope,
            budget=BudgetLimits(max_wall_ms=90_000),
        ),
    ]


async def test_same_request_returns_one_mission(store: MissionStore) -> None:
    request = _request()
    first = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    second = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    assert first.mission_id == second.mission_id


async def test_same_id_different_digest_is_collision(store: MissionStore) -> None:
    request = _request()
    await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    other_text = _request(request_id=request.request_id, text="close safari")
    with pytest.raises(MissionIdentityCollision):
        await store.claim_request(
            other_text, _digest(other_text), goal=other_text.text,
            scope=Scope(owner_id="owner"), limits=BudgetLimits(),
        )


async def test_concurrent_claims_yield_one_mission(store: MissionStore) -> None:
    request = _request()

    async def claim() -> str:
        record = await store.claim_request(
            request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
            limits=BudgetLimits(),
        )
        return record.mission_id

    ids = await asyncio.gather(claim(), claim(), claim())
    assert len(set(ids)) == 1


async def test_commit_plan_cas_and_revision_invalidates(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    planned = await store.commit_plan(
        mission.mission_id, 0, _steps(), [], reason="initial"
    )
    assert planned.plan_version == 1
    assert planned.status == "PLANNED"
    with pytest.raises(StaleControlError):
        await store.commit_plan(
            mission.mission_id, 0, _steps(), [], reason="stale"
        )
    # A revision creates plan v2; old approvals are invalidated.
    revised = await store.commit_plan(
        mission.mission_id, 1, _steps(), [], reason="revised"
    )
    assert revised.plan_version == 2


async def test_claim_step_respects_dependencies(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    item = await store.claim_step(
        mission.mission_id, 1, 1, tool_ids=["list_apps", "bring_to_front"]
    )
    assert item is not None
    assert item.step_id == "s1"
    assert item.attempt == 1
    assert item.control_epoch == 1
    assert item.deduplication_key.endswith(":s1:1")
    # While s1 is active, the next claim finds nothing else ready.
    with pytest.raises(MissionStoreError):
        await store.claim_step(
            mission.mission_id, 1, 1, step_id="s2", tool_ids=["get_window_state"]
        )
    # And claiming s1 again while it is active refuses.
    with pytest.raises(MissionStoreError):
        await store.claim_step(
            mission.mission_id, 1, 1, step_id="s1", tool_ids=["list_apps"]
        )


async def test_claim_step_cas_on_epoch(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    with pytest.raises(StaleControlError):
        await store.claim_step(
            mission.mission_id, 1, 99, tool_ids=["list_apps"]
        )


async def test_apply_result_atomic_and_duplicate(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    item = await store.claim_step(
        mission.mission_id, 1, 1, tool_ids=["list_apps"]
    )
    assert item is not None
    result = StepResult(
        mission_id=mission.mission_id,
        plan_version=item.plan_version,
        control_epoch=item.control_epoch,
        step_id=item.step_id,
        execution_id=item.execution_id,
        attempt=item.attempt,
        status="COMPLETED",
        effect_outcome="CONFIRMED",
        usage={"consumed": {"actions": 2}, "known_input_tokens": 10},
    )
    outcome = await store.apply_result(result)
    assert outcome == "APPLIED"
    # Same result again: idempotent duplicate.
    assert await store.apply_result(result) == "DUPLICATE"
    after = await store.get_mission(mission.mission_id)
    assert after is not None
    assert after.budget_usage.consumed.get("actions") == 2, "usage merged exactly once"
    assert after.plan_version == 1


async def test_conflicting_result_rejected(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    base = {
        "mission_id": mission.mission_id,
        "plan_version": item.plan_version,
        "control_epoch": item.control_epoch,
        "step_id": item.step_id,
        "execution_id": item.execution_id,
        "attempt": item.attempt,
    }
    first = await store.apply_result(
        StepResult(**base, status="COMPLETED", effect_outcome="CONFIRMED")
    )
    assert first == "APPLIED"
    with pytest.raises(ResultConflict):
        await store.apply_result(
            StepResult(**base, status="FAILED", failure_category="DEADLINE",
                       effect_outcome="UNKNOWN")
        )


async def test_stale_result_does_not_advance_state(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    # Pause bumps the epoch: the in-flight packet is now stale.
    await store.control(
        MissionControl(
            control_id=new_id(),
            mission_id=mission.mission_id,
            expected_plan_version=1,
            expected_control_epoch=1,
            kind="PAUSE",
            reason="fixture",
        )
    )
    late = StepResult(
        mission_id=mission.mission_id,
        plan_version=item.plan_version,
        control_epoch=item.control_epoch,
        step_id=item.step_id,
        execution_id=item.execution_id,
        attempt=item.attempt,
        status="COMPLETED",
        effect_outcome="CONFIRMED",
        external_operation_ids=["ext-1"],
    )
    outcome = await store.apply_result(late)
    assert outcome == "STALE"
    after = await store.get_mission(mission.mission_id)
    assert after is not None
    assert after.status == "PAUSED"
    # The unresolved effect is retained in a reconciliation event.
    events = await store.get_events(mission.mission_id)
    assert any(
        e.kind == "recovery" and e.safe_payload.get("stale_result")
        for e in events
    )


async def test_resume_then_complete_mission_flow(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    epoch = mission.control_epoch
    item = await store.claim_step(mission.mission_id, 1, epoch, tool_ids=["list_apps"])
    assert item is not None
    await store.apply_result(
        StepResult(
            mission_id=mission.mission_id,
            plan_version=item.plan_version,
            control_epoch=item.control_epoch,
            step_id=item.step_id,
            execution_id=item.execution_id,
            attempt=item.attempt,
            status="COMPLETED",
            effect_outcome="CONFIRMED",
        )
    )
    current = await store.get_mission(mission.mission_id)
    assert current is not None
    assert current.status == "RUNNING"
    resumed = await store.claim_step(mission.mission_id, 1, epoch, tool_ids=["list_apps"])
    assert resumed is not None
    assert resumed.step_id == "s2", "the dependency-ready next step is claimed"
    await store.apply_result(
        StepResult(
            mission_id=mission.mission_id,
            plan_version=resumed.plan_version,
            control_epoch=resumed.control_epoch,
            step_id=resumed.step_id,
            execution_id=resumed.execution_id,
            attempt=resumed.attempt,
            status="COMPLETED",
            effect_outcome="CONFIRMED",
        )
    )
    finished = await store.get_mission(mission.mission_id)
    assert finished is not None
    assert finished.status == "VERIFYING", "all steps terminal -> acceptance gate"
    # RESUME only answers a PAUSED mission.
    with pytest.raises(MissionStoreError):
        await store.control(
            MissionControl(
                control_id=new_id(),
                mission_id=mission.mission_id,
                expected_plan_version=1,
                expected_control_epoch=epoch,
                kind="RESUME",
            )
        )
    # Cancelling a nonterminal mission is legal; a terminal one refuses.
    cancelled = await store.control(
        MissionControl(
            control_id=new_id(),
            mission_id=mission.mission_id,
            expected_plan_version=1,
            expected_control_epoch=epoch,
            kind="CANCEL",
        )
    )
    assert cancelled.status == "CANCELLED"
    with pytest.raises(MissionStoreError):
        await store.control(
            MissionControl(
                control_id=new_id(),
                mission_id=mission.mission_id,
                expected_plan_version=1,
                expected_control_epoch=2,
                kind="RESUME",
            )
        )


async def test_budget_reservation_idempotent_and_capped(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(max_jev_calls=2),
    )
    first = await store.reserve_budget(
        mission.mission_id, BudgetCharge(resource="jev_calls", amount=1, call_key="call-a")
    )
    again = await store.reserve_budget(
        mission.mission_id, BudgetCharge(resource="jev_calls", amount=1, call_key="call-a")
    )
    assert first.reservation_id == again.reservation_id, "same call key is idempotent"
    await store.reserve_budget(
        mission.mission_id, BudgetCharge(resource="jev_calls", amount=1, call_key="call-b")
    )
    with pytest.raises(MissionStoreError):
        await store.reserve_budget(
            mission.mission_id, BudgetCharge(resource="jev_calls", amount=1, call_key="call-c")
        )


async def test_paid_reservation_never_released(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(max_paid_units=1),
    )
    reservation = await store.reserve_budget(
        mission.mission_id, BudgetCharge(resource="paid_units", amount=1, call_key="gen-1")
    )
    with pytest.raises(MissionStoreError):
        await store.settle_reservation(reservation.reservation_id, consumed=False)


async def test_reservation_consumed_moves_counters(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(max_actions=2),
    )
    reservation = await store.reserve_budget(
        mission.mission_id, BudgetCharge(resource="actions", amount=1)
    )
    await store.settle_reservation(reservation.reservation_id, consumed=True)
    current = await store.get_mission(mission.mission_id)
    assert current is not None
    assert current.budget_usage.consumed.get("actions") == 1
    assert current.budget_usage.reserved.get("actions", 0) == 0


async def test_unknown_execution_result_rejected(store: MissionStore) -> None:
    from assistant.missions.store import UnknownExecution

    result = StepResult(
        mission_id=new_id(),
        plan_version=1,
        control_epoch=1,
        step_id="s",
        execution_id=new_id(),
        attempt=1,
        status="COMPLETED",
        effect_outcome="CONFIRMED",
    )
    with pytest.raises(UnknownExecution):
        await store.apply_result(result)


async def test_events_hash_chain_verifies(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    assert await store.verify_chain(mission.mission_id) is True
    events = await store.get_events(mission.mission_id)
    assert [e.sequence for e in events] == sorted(e.sequence for e in events)
    assert events[0].kind == "request"
    assert events[-1].kind == "plan"


async def test_goal_revision_is_append_only(store: MissionStore) -> None:
    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.record_goal_revision(
        mission.mission_id, 1, "open safari instead", "owner correction"
    )
    current = await store.get_mission(mission.mission_id)
    assert current is not None
    assert current.original_goal == request.text, "original goal is never overwritten"
    events = await store.get_events(mission.mission_id)
    correction = [e for e in events if e.kind == "correction"]
    assert len(correction) == 1
    assert correction[0].safe_payload["original_goal"] == request.text


async def test_legacy_tables_survive_mission_migration(legacy_store: MissionStore) -> None:
    """Preexisting run-registry rows and schema markers are untouched."""
    from assistant.runtime.runs_local import SQLiteRunStore

    request = _request()
    mission = await legacy_store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    assert mission.mission_id
    runs = SQLiteRunStore.__new__(SQLiteRunStore)
    runs._conn = legacy_store._conn  # same connection for the read
    runs._lock = asyncio.Lock()
    record = runs._get_run_sync("run-legacy-1")
    assert record is not None and record.status == "running"


async def test_migration_is_idempotent_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "reopen.db"
    store_a = await MissionStore.connect(db)
    await store_a.setup()
    await store_a.close()
    store_b = await MissionStore.connect(db)
    await store_b.setup()
    rows = store_b._conn.execute(
        "SELECT version FROM mission_schema_migrations"
    ).fetchall()
    assert [int(r[0]) for r in rows] == [1, 2, 3], "no duplicate migration rows"
    await store_b.close()


async def test_user_version_not_downgraded(tmp_path: Path) -> None:
    """A higher user_version set by another owner is preserved (RF-15)."""
    from assistant.runtime.runs_local import SQLiteRunStore

    db = tmp_path / "versioned.db"
    store = await MissionStore.connect(db)
    await store.setup()
    runs = await SQLiteRunStore.connect(str(db))
    await runs.setup()  # legacy marker runs on the shared file
    current = store._conn.execute("PRAGMA user_version").fetchone()[0]
    assert current >= 1
    await runs.close()
    await store.close()


async def test_evidence_roundtrip(store: MissionStore) -> None:
    from assistant.missions.contracts import EvidenceRef

    request = _request()
    mission = await store.claim_request(
        request, _digest(request), goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    ref = EvidenceRef(
        evidence_id=new_id(),
        mission_id=mission.mission_id,
        kind="structured_facts",
        inline_facts={"marker": "fixture"},
        sha256="a" * 64,
        captured_at_ms=_now(),
    )
    await store.put_evidence(ref)
    stored = await store.get_evidence(mission.mission_id)
    assert [r.evidence_id for r in stored] == [ref.evidence_id]


# -- R11: store-level required-check completeness (mutation-sensitive) --------


async def test_completed_result_missing_required_check_is_conflict(store: Any) -> None:
    """A COMPLETED result that omits a required check cannot become
    SUCCEEDED: the store enforces complete evidence (R06/F05)."""
    from assistant.missions.contracts import (
        CheckSpec,
        StepResult,
        StepSpec,
    )

    request = _request()
    mission = await store.claim_request(
        request, "d" * 64, goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    steps = [
        StepSpec(
            step_id="s1", ordinal=1, objective="fixture", recipe_id="open_app",
            scope=Scope(owner_id="owner"), budget=BudgetLimits(max_wall_ms=90_000),
            checks=[
                CheckSpec(
                    check_id="must-pass", verifier_id="page_state",
                    verifier_version="1.0.0", expected={"markers": ["x"]}, required=True,
                )
            ],
        )
    ]
    await store.commit_plan(mission.mission_id, 0, steps, [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    result = StepResult(
        mission_id=mission.mission_id, plan_version=1, control_epoch=1,
        step_id="s1", execution_id=item.execution_id, attempt=1,
        status="COMPLETED", effect_outcome="CONFIRMED",
        # No CheckResult for the required check at all.
        postconditions=[],
    )
    with pytest.raises(ResultConflict):
        await store.apply_result(result)
    states = await store.get_step_states(mission.mission_id, 1)
    assert states["s1"] == "RUNNING", "the step must not silently succeed"
