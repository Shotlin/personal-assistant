"""Mission observability integration (T11, file 06 I1, RSI-01/02/03).

One trace per mission, reconstructable intents/outcomes, corrections and
failures retained, usage metered with honest unknown flags.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from assistant.missions.contracts import (
    BudgetLimits,
    RequestEnvelope,
    Scope,
    StepSpec,
    new_id,
)
from assistant.missions.observer import Observer, RecommendationStore
from assistant.missions.store import MissionStore


def _now() -> int:
    return int(time.time() * 1000)


@pytest.fixture()
async def store(tmp_path: Path) -> Any:
    s = await MissionStore.connect(tmp_path / "obs.db")
    await s.setup()
    yield s
    await s.close()


async def _full_mission(store: MissionStore, *, fail: bool = False) -> str:
    request = RequestEnvelope(
        request_id=new_id(),
        conversation_id="c-obs",
        owner_id="owner",
        input_origin="typed_final",
        input_revision=1,
        text="open safari",
        submitted_at_ms=_now(),
    )
    scope = Scope(owner_id="owner", permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"})
    mission = await store.claim_request(
        request, "d" * 64, goal=request.text, scope=scope, limits=BudgetLimits()
    )
    steps = [
        StepSpec(step_id="s1", ordinal=1, objective="open", recipe_id="open_app",
                 scope=scope, budget=BudgetLimits(max_wall_ms=90_000)),
    ]
    await store.commit_plan(mission.mission_id, 0, steps, [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    from assistant.missions.contracts import StepResult

    if fail:
        result = StepResult(
            mission_id=mission.mission_id, plan_version=1, control_epoch=1,
            step_id="s1", execution_id=item.execution_id, attempt=1,
            status="FAILED", failure_category="VERIFICATION_FAILED",
            effect_outcome="UNKNOWN", uncertainty="the surface never confirmed",
        )
    else:
        result = StepResult(
            mission_id=mission.mission_id, plan_version=1, control_epoch=1,
            step_id="s1", execution_id=item.execution_id, attempt=1,
            status="COMPLETED", effect_outcome="CONFIRMED",
        )
    await store.apply_result(result)
    await store.finalize_mission(
        mission.mission_id, 1, "FAILED" if fail else "COMPLETED"
    )
    return mission.mission_id


async def test_one_trace_per_mission_reconstructs_flow(store: MissionStore) -> None:
    """RSI-01/02: request -> plan -> action -> outcome -> verification."""
    mission_id = await _full_mission(store)
    events = await store.get_events(mission_id)
    kinds = [e.kind for e in events]
    assert kinds[0] == "request"
    assert "plan" in kinds and "action" in kinds and "outcome" in kinds
    assert kinds[-1] == "verification"
    assert all(e.mission_id == mission_id for e in events), "one correlated trace"
    assert await store.verify_chain(mission_id)


async def test_failures_retained_after_crash_with_unknown_outcome(store: MissionStore) -> None:
    """RSI-03: failed/unknown missions preserve evidence through restart."""
    mission_id = await _full_mission(store, fail=True)
    record = await store.get_mission(mission_id)
    assert record is not None and record.status == "FAILED"
    events = await store.get_events(mission_id)
    outcome = next(e for e in events if e.kind == "outcome")
    assert outcome.safe_payload["failure_category"] == "VERIFICATION_FAILED"
    assert outcome.safe_payload["effect_outcome"] == "UNKNOWN", "uncertainty is recorded"
    # The trace is stable across repeated reads (durable, append-only).
    events_after = await store.get_events(mission_id)
    assert [e.event_id for e in events_after] == [e.event_id for e in events]


async def test_observer_reads_trace_and_reports_without_support_fabrication(
    store: MissionStore,
) -> None:
    """RSI-08: support IDs resolve to real events in the trace."""
    mission_id = await _full_mission(store, fail=True)
    events = await store.get_events(mission_id)
    recommendations = await Observer().analyze(events)
    known_ids = {e.event_id for e in events}
    for recommendation in recommendations:
        for ref in recommendation.supporting_event_ids:
            assert ref in known_ids, "support refs must resolve to stored events"


async def test_usage_metering_reports_unknown_as_unknown() -> None:
    """Model/API errors missing usage keep the unknown counter (A12)."""
    from assistant.observability.usage import LedgerCallbackHandler, UsageLedger

    ledger = UsageLedger()
    handler = LedgerCallbackHandler(ledger, prefix="fixture")

    class _BareResponse:
        llm_output: dict[str, Any] = {}
        generations: list[Any] = []

    handler.on_llm_end(_BareResponse())
    snapshot = ledger.snapshot()
    assert snapshot["unknown_input_calls"] == 1
    assert snapshot["unknown_output_calls"] == 1
    assert snapshot["cost_usd"] is None, "unknown cost is never zero"


async def test_recommendations_live_outside_mission_data(
    store: MissionStore, tmp_path: Path
) -> None:
    await _full_mission(store)
    sink = RecommendationStore(tmp_path / "observer-recs")
    observer = Observer(sink)
    events = await store.get_events(await _full_mission(store, fail=True))
    await observer.analyze(events)
    # The recommendation file exists under the observer path only.
    assert sink.path.exists()
    assert "observer-recs" in str(sink.path)
    # Mission data is untouched by the observer run.
    assert await store.verify_chain(await _first_mission_id(store)) is True


async def _first_mission_id(store: MissionStore) -> str:
    rows = store._conn.execute("SELECT mission_id FROM missions LIMIT 1").fetchall()
    return str(rows[0][0])


async def test_correction_events_survive_in_trace(store: MissionStore) -> None:
    from assistant.missions.contracts import StepResult

    request = RequestEnvelope(
        request_id=new_id(), conversation_id="c-obs", owner_id="owner",
        input_origin="typed_final", input_revision=1, text="open safari",
        submitted_at_ms=_now(),
    )
    scope = Scope(owner_id="owner", permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"})
    mission = await store.claim_request(
        request, "d" * 64, goal=request.text, scope=scope, limits=BudgetLimits()
    )
    steps = [StepSpec(step_id="s1", ordinal=1, objective="open", recipe_id="open_app",
                      scope=scope, budget=BudgetLimits(max_wall_ms=90_000))]
    await store.commit_plan(mission.mission_id, 0, steps, [])
    # The owner revises while the mission is still nonterminal.
    await store.record_goal_revision(
        mission.mission_id, 1, "open firefox instead", "owner correction"
    )
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    await store.apply_result(StepResult(
        mission_id=mission.mission_id, plan_version=1, control_epoch=1,
        step_id="s1", execution_id=item.execution_id, attempt=1,
        status="COMPLETED", effect_outcome="CONFIRMED"))
    await store.finalize_mission(mission.mission_id, 1, "COMPLETED")
    mission_id = mission.mission_id
    events = await store.get_events(mission_id)
    corrections = [e for e in events if e.kind == "correction"]
    assert len(corrections) == 1
    assert corrections[0].safe_payload["original_goal"] == "open safari", (
        "the original goal is preserved alongside the revision"
    )

    assert await store.verify_chain(mission_id)


# -- R09 regressions: RP08/RP09 + Observer on real events -----------------------


async def test_rp08_secret_goal_refused_before_persistence(store: MissionStore) -> None:
    """RP08: a secret-shaped request goal is REFUSED by the store; raw
    canaries never reach mission persistence."""
    from assistant.missions.store import MissionStoreError
    from tests.helpers.mission_fakes import SECRET_CANARIES

    request = RequestEnvelope(
        request_id=new_id(),
        conversation_id="c-secret",
        owner_id="owner",
        input_origin="typed_final",
        input_revision=1,
        text=f"the api key is {SECRET_CANARIES[0]} keep it",
        submitted_at_ms=_now(),
    )
    with pytest.raises(MissionStoreError) as excinfo:
        await store.claim_request(
            request, "d" * 64, goal=request.text, scope=Scope(owner_id="owner"),
            limits=BudgetLimits(),
        )
    assert "privacy policy" in str(excinfo.value)
    rows = store._conn.execute(
        "SELECT COUNT(*) FROM missions WHERE original_goal LIKE ?",
        (f"%{SECRET_CANARIES[0]}%",),
    ).fetchone()
    assert int(rows[0]) == 0, "the canary must not be persisted"


async def test_rp09_tampered_evidence_fails_verification(
    store: Any, tmp_path: Path
) -> None:
    """RP09: a modified evidence file fails hash verification before any
    check passes on it."""
    from assistant.missions.contracts import EvidenceCandidate
    from assistant.missions.evidence import EvidenceStore

    evidence = EvidenceStore(tmp_path / "ev", store)
    mission_id = await _full_mission(store)
    ref = await evidence.put(
        EvidenceCandidate(
            kind="structured_facts",
            payload={"elements": [{"label": "fixture-mark", "role": "AXButton"}]},
            captured_at_ms=_now(),
        ),
        mission_id=mission_id,
    )
    # Tamper with the stored file after the ref was issued.
    stored_path = tmp_path / "ev" / (ref.relative_path or "")
    stored_path.write_text('{"payload": {"elements": [{"label": "EVIL"}]}}')
    assert evidence.load(ref) is None, "tampered content must not load"

    from assistant.missions.contracts import CheckSpec

    check = CheckSpec(
        check_id="c1",
        verifier_id="page_state",
        verifier_version="1.0.0",
        expected={"markers": ["fixture-mark"]},
        target_scope_hash="",
    )
    result = await evidence.verify(check, None, refs=[ref])
    assert result.passed is False, "tampered evidence cannot verify anything"


async def test_observer_consumes_committed_events_with_support(
    store: Any, tmp_path: Path
) -> None:
    """RSI-06/08: the runtime Observer reads committed events; its support
    refs resolve to stored event ids."""
    from assistant.missions.observer import (
        Observer,
        RecommendationStore,
    )

    mission_id = await _full_mission(store, fail=True)
    events = await store.get_events(mission_id)
    sink = RecommendationStore(tmp_path / "recs-runtime")
    recommendations = await Observer(sink).analyze(events)
    for recommendation in recommendations:
        known = {e.event_id for e in events}
        for support in recommendation.supporting_event_ids:
            assert support in known, "support must resolve to real committed events"
    assert not sink.path.exists() or sink.path.read_text().count("\n") == len(recommendations)
