"""Mission persistence integration tests (T02, file 06 I1).

Process-level environment P: a real SQLite file exercised the way the
sidecar does — separate connections for "before/after a restart", writes
that must survive a crash, and coexistence with the legacy run registry and
memory store on the shared sani.db.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from assistant.missions.contracts import (
    BudgetLimits,
    MissionControl,
    Scope,
    StepResult,
    StepSpec,
    new_id,
)
from assistant.missions.store import MissionStore, MissionStoreError, StaleControlError


def _now() -> int:
    return int(time.time() * 1000)


def _request(text: str = "fixture mission", owner: str = "owner"):
    from assistant.missions.contracts import RequestEnvelope

    return RequestEnvelope(
        request_id=new_id(),
        conversation_id="conv-int",
        owner_id=owner,
        input_origin="typed_final",
        input_revision=1,
        text=text,
        submitted_at_ms=_now(),
    )


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


def _result_of(
    mission_id: str, item, *, status: str = "COMPLETED", outcome: str = "CONFIRMED"
) -> StepResult:
    return StepResult(
        mission_id=mission_id,
        plan_version=item.plan_version,
        control_epoch=item.control_epoch,
        step_id=item.step_id,
        execution_id=item.execution_id,
        attempt=item.attempt,
        status=status,  # type: ignore[arg-type]
        effect_outcome=outcome,  # type: ignore[arg-type]
    )


async def test_intent_committed_survives_process_death(tmp_path: Path) -> None:
    """Kill after intent, reopen on a fresh connection: the intent is durable.

    The new process marks the attempt uncertain (RECONCILING) and bumps the
    epoch; the old packet's late result then lands STALE (RF-17).
    """
    db = tmp_path / "crash.db"
    first = await MissionStore.connect(db)
    await first.setup()
    request = _request()
    mission = await first.claim_request(
        request, "d" * 64, goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await first.commit_plan(mission.mission_id, 0, _steps(), [])
    item = await first.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    # --- process death: the first connection is abandoned, never closed ---
    second = await MissionStore.connect(db)
    await second.setup()
    attempts = await second.recover_inflight("host-gen-1")
    assert attempts == [item.execution_id]
    reopened = await second.get_mission(mission.mission_id)
    assert reopened is not None
    assert reopened.control_epoch == 2, "restart increments the epoch"
    attempt = await second.get_attempt(item.execution_id)
    assert attempt is not None
    assert attempt["dispatch_state"] == "RECONCILING"
    # The stale packet's result must not advance the new epoch.
    outcome = await second.apply_result(_result_of(mission.mission_id, item))
    assert outcome == "STALE"
    after = await second.get_mission(mission.mission_id)
    assert after is not None
    assert after.status == "RUNNING"
    await second.close()
    await first.close()


async def test_recovery_is_idempotent_and_terminal_missions_untouched(tmp_path: Path) -> None:
    db = tmp_path / "recover.db"
    store = await MissionStore.connect(db)
    await store.setup()
    request = _request()
    mission = await store.claim_request(
        request, "d" * 64, goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    epoch_before = (await store.get_mission(mission.mission_id)).control_epoch  # type: ignore[union-attr]
    # Recovery with nothing in flight is a no-op, twice.
    assert await store.recover_inflight("gen") == []
    assert await store.recover_inflight("gen") == []
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    item = await store.claim_step(mission.mission_id, 1, epoch_before, tool_ids=["list_apps"])
    assert item is not None
    # Recovery now reconciles the one active attempt and bumps the epoch.
    assert await store.recover_inflight("gen") == [item.execution_id]
    # A second recovery over the same state finds nothing new to reconcile.
    assert await store.recover_inflight("gen") == []
    # Cancel the mission (now at epoch 2), then recovery leaves it alone.
    await store.control(
        MissionControl(
            control_id=new_id(),
            mission_id=mission.mission_id,
            expected_plan_version=1,
            expected_control_epoch=2,
            kind="CANCEL",
        )
    )
    attempts_after_cancel = await store.recover_inflight("gen")
    assert attempts_after_cancel == [], "terminal missions are not reconciled"
    after = await store.get_mission(mission.mission_id)
    assert after is not None and after.status == "CANCELLED"
    await store.close()


async def test_disk_full_blocks_mutation_with_readable_error(tmp_path: Path) -> None:
    """A full disk refuses new mutations; existing committed data stays readable."""
    from assistant.missions.contracts import CheckSpec

    db = tmp_path / "full.db"
    store = await MissionStore.connect(db)
    await store.setup()
    request = _request()
    mission = await store.claim_request(
        request, "d" * 64, goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    # Simulate disk exhaustion by capping the database page count; the plan
    # below is tens of KB, so the write cannot fit in existing pages.
    store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    page_count = store._conn.execute("PRAGMA page_count").fetchone()[0]
    store._conn.execute(f"PRAGMA max_page_count = {page_count}")
    big_check = CheckSpec(
        check_id="c", verifier_id="fixture", verifier_version="1",
        expected={"blob": "x" * 4000},
    )
    big_steps = [
        StepSpec(
            step_id=f"s{i}", ordinal=i + 1, objective="filler " * 285,
            recipe_id="open_app", scope=Scope(owner_id="owner"),
            budget=BudgetLimits(max_wall_ms=90_000), checks=[big_check] * 3,
        )
        for i in range(20)
    ]
    with pytest.raises(MissionStoreError) as excinfo:
        await store.commit_plan(mission.mission_id, 0, big_steps, [])
    assert "disk is full" in str(excinfo.value)
    # Reads still work after the failed mutation, and the plan is unchanged.
    readable = await store.get_mission(mission.mission_id)
    assert readable is not None
    assert readable.plan_version == 0
    store._conn.execute("PRAGMA max_page_count = 0")  # lift the cap again
    planned = await store.commit_plan(mission.mission_id, 0, _steps(), [])
    assert planned.plan_version == 1
    await store.close()


async def test_mission_tables_coexist_with_memory_store(tmp_path: Path) -> None:
    """Memory store items survive mission migrations, twice, across reopen."""
    from assistant.memory.local import SqliteStore

    db = tmp_path / "shared.db"
    memory = SqliteStore.open(str(db))
    await memory.aput(("mem",), "fixture-key", {"value": 42})
    store = await MissionStore.connect(db)
    await store.setup()
    request = _request()
    mission = await store.claim_request(
        request, "d" * 64, goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    await store.close()
    # Reopen both, run migrations again, verify the memory item survived.
    memory2 = SqliteStore.open(str(db))
    item = await memory2.aget(("mem",), "fixture-key")
    assert item is not None and item.value == {"value": 42}
    store2 = await MissionStore.connect(db)
    await store2.setup()
    got = await store2.get_mission(mission.mission_id)
    assert got is not None and got.plan_version == 1
    await store2.close()
    memory2.close()


async def test_chain_survives_restart_and_detects_tampering(tmp_path: Path) -> None:
    db = tmp_path / "chain.db"
    store = await MissionStore.connect(db)
    await store.setup()
    request = _request()
    mission = await store.claim_request(
        request, "d" * 64, goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    await store.apply_result(_result_of(mission.mission_id, item))
    assert await store.verify_chain(mission.mission_id)
    await store.close()

    # Reopen: the chain still verifies.
    store2 = await MissionStore.connect(db)
    assert await store2.verify_chain(mission.mission_id)
    # Tamper with a payload in place: the chain must detect it.
    store2._conn.execute(
        "UPDATE mission_events SET payload_json='{\"tampered\":true}' "
        "WHERE mission_id=? AND kind='plan'",
        (mission.mission_id,),
    )
    assert not await store2.verify_chain(mission.mission_id)
    await store2.close()


async def test_control_during_active_stream_cas(tmp_path: Path) -> None:
    """A control arriving mid-stream loses its CAS against a newer epoch."""
    db = tmp_path / "cas.db"
    store = await MissionStore.connect(db)
    await store.setup()
    request = _request()
    mission = await store.claim_request(
        request, "d" * 64, goal=request.text, scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),
    )
    await store.commit_plan(mission.mission_id, 0, _steps(), [])
    paused = await store.control(
        MissionControl(
            control_id=new_id(),
            mission_id=mission.mission_id,
            expected_plan_version=1,
            expected_control_epoch=1,
            kind="PAUSE",
        )
    )
    assert paused.status == "PAUSED" and paused.control_epoch == 2
    with pytest.raises(StaleControlError):
        await store.control(
            MissionControl(
                control_id=new_id(),
                mission_id=mission.mission_id,
                expected_plan_version=1,
                expected_control_epoch=1,  # stale epoch
                kind="CANCEL",
            )
        )
    resumed = await store.control(
        MissionControl(
            control_id=new_id(),
            mission_id=mission.mission_id,
            expected_plan_version=1,
            expected_control_epoch=2,
            kind="RESUME",
        )
    )
    assert resumed.status == "RUNNING" and resumed.control_epoch == 3
    await store.close()


async def test_checksum_ledger_records_migration(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    store = await MissionStore.connect(db)
    await store.setup()
    rows = store._conn.execute(
        "SELECT version, checksum FROM mission_schema_migrations"
    ).fetchall()
    # Migration 4 (exact approvals + external waits + provider requests)
    # joins additively; every row still carries its integrity checksum.
    assert [int(r[0]) for r in rows] == [1, 2, 3, 4]
    assert all(len(r[1]) == 64 for r in rows)
    await store.close()
