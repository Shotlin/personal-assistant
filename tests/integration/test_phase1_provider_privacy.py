"""Provider-request accounting and privacy boundaries (D05/D07).

D05: every actual model transport request — including graph internals and
retries — is metered with a durable per-call id, without double-charging
the outer deep_calls budget, and a hard per-invocation ceiling aborts a
runaway graph. Scripted providers only.

D07: expiry is configured during ordinary evidence creation; retention
runs in the normal lifecycle and respects holds; canaries riding in
exception/final-review/result text are withheld at the sink; owner
deletion removes derivatives and leaves safe tombstones.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from assistant.missions.authority import MissionAuthority
from assistant.missions.contracts import (
    BudgetLimits,
    ObserverRecommendation,
    RequestEnvelope,
    Scope,
    StepResult,
    StepSpec,
    new_id,
)
from assistant.missions.evidence import EvidenceStore
from assistant.missions.observer import RecommendationStore
from assistant.missions.store import MissionStore


@pytest.fixture
async def store(tmp_path: Any) -> Any:
    value = await MissionStore.connect(tmp_path / "provider-privacy.db")
    await value.setup()
    yield value
    await value.close()


async def _claimed(store: MissionStore, tmp_path: Any) -> tuple[Any, EvidenceStore]:
    scope = Scope(owner_id="owner", allowed_apps=["com.fixture.safe"],
                  permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"})
    request = RequestEnvelope(request_id=new_id(), conversation_id="provider-privacy",
                              owner_id="owner", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(request, "d" * 64, goal="fixture", scope=scope,
                                        limits=BudgetLimits())
    mission = await store.commit_plan(mission.mission_id, 0, [StepSpec(
        step_id="s1", ordinal=1, objective="fixture", recipe_id="open_app",
        scope=scope, budget=BudgetLimits())], [])
    evidence = EvidenceStore(tmp_path / "ev", store)
    return mission, evidence


# -- D05: durable per-provider-request metering -----------------------------------


class _ScriptedGraphDeep:
    """A Deep entry double: N provider requests per run THROUGH the real
    admission scope (what the wrapped model does inside the graph), then a
    submission."""

    def __init__(self, requests: int, controller: Any, role: str = "REVIEW") -> None:
        self.requests = requests
        self.controller = controller
        self.role = role

    async def run(self, text: str, *, thread_id: str, on_event: Any, cancel_check: Any,
                  **kwargs: Any) -> dict[str, Any]:
        from assistant.models.admission import ProviderRequestDenied

        scope = self.controller.current_scope()
        assert scope is not None, "the transport must open an invocation scope"
        for index in range(self.requests):
            try:
                scope.admit(f"graph-call-{index}")
            except ProviderRequestDenied:
                raise
            scope.complete(f"graph-call-{index}", None)
        from assistant.missions.submission import submission_capture

        capture = submission_capture.get()
        assert capture is not None, "the transport must set an invocation capture"
        capture[self.role] = {"supported_summary": "fixture review"}
        return {"status": "done"}


async def test_provider_requests_recorded_without_double_charging(
    store: MissionStore, tmp_path: Any
) -> None:
    """Three graph-internal provider requests → three durable rows with
    unique call ids; the OUTER deep_calls budget is not re-charged."""
    from assistant.core.agents import _DeepInvoke
    from assistant.missions.controller import controller_role
    from assistant.models.admission import ProviderAdmissionController

    mission, _ = await _claimed(store, tmp_path)
    controller = ProviderAdmissionController()
    controller.bind_store(store)
    transport = _DeepInvoke(
        _ScriptedGraphDeep(requests=3, controller=controller),  # type: ignore[arg-type]
        thread_id="conv", mission_id=mission.mission_id,
        plan_version=1, admission=controller,
    )
    with controller_role("REVIEW"):
        result = await transport("REVIEW", "submit_final_review", {"prompt": "x"})
    assert result["review"]["supported_summary"] == "fixture review"
    rows = await store.provider_requests(mission.mission_id)
    assert [r["request_id"] for r in rows] == [
        "graph-call-0", "graph-call-1", "graph-call-2"], "one durable id per request"
    assert all(r["plan_version"] == 1 for r in rows)
    refreshed = await store.get_mission(mission.mission_id)
    assert refreshed is not None
    assert refreshed.budget_usage.consumed.get("deep_calls", 0) == 0, (
        "transport metering is evidence, never a second deep_calls charge"
    )


async def test_provider_request_ceiling_aborts_runaway_graph(
    store: MissionStore, tmp_path: Any
) -> None:
    """A graph that exceeds the per-invocation request ceiling is aborted
    AFTER its requests are recorded — no free calls, no silent overrun."""
    from assistant.core.agents import _DeepInvoke
    from assistant.missions.controller import controller_role
    from assistant.models.admission import (
        ProviderAdmissionController,
        ProviderRequestDenied,
    )

    mission, _ = await _claimed(store, tmp_path)
    # D11: the ceiling comes from the mission's own limits (2 here).
    limits_row = store._conn.execute(
        "SELECT limits_json FROM missions WHERE mission_id=?",
        (mission.mission_id,)).fetchone()
    limits = dict(json.loads(str(limits_row[0])))
    limits["max_provider_requests"] = 2
    store._conn.execute("UPDATE missions SET limits_json=? WHERE mission_id=?",
                        (json.dumps(limits), mission.mission_id))
    controller = ProviderAdmissionController()
    controller.bind_store(store)
    transport = _DeepInvoke(
        _ScriptedGraphDeep(requests=4, controller=controller),  # type: ignore[arg-type]
        thread_id="conv", mission_id=mission.mission_id,
        plan_version=1, admission=controller,
    )
    with controller_role("REVIEW"), pytest.raises(ProviderRequestDenied, match="ceiling"):
        await transport("REVIEW", "submit_final_review", {"prompt": "x"})
    rows = await store.provider_requests(mission.mission_id)
    assert len(rows) >= 3, "the actual requests are metered before the abort"


# -- D07: expiry, lifecycle retention, holds, screening, deletion ------------------


async def test_evidence_expires_at_creation(store: MissionStore, tmp_path: Any) -> None:
    from assistant.missions.contracts import EvidenceCandidate

    _mission, evidence = await _claimed(store, tmp_path)
    ref = await evidence.put(EvidenceCandidate(
        kind="structured_facts", payload={"pid": 1},
        captured_at_ms=int(time.time() * 1000)),
        mission_id=(await _mission_id(store)))
    assert ref.expires_at_ms is not None and ref.expires_at_ms > ref.captured_at_ms
    assert ref.relative_path is not None


async def _mission_id(store: MissionStore) -> str:
    row = store._conn.execute(
        "SELECT mission_id FROM missions ORDER BY created_at_ms DESC LIMIT 1").fetchone()
    assert row is not None
    return str(row[0])


async def test_lifecycle_retention_deletes_expired_and_holds_protect(
    store: MissionStore, tmp_path: Any
) -> None:
    """Normal lifecycle retention removes expired evidence (row + file);
    an explicit hold preserves the same evidence."""
    from assistant.missions.contracts import EvidenceCandidate

    mission, evidence = await _claimed(store, tmp_path)
    held_evidence = EvidenceStore(tmp_path / "held", store, ttl_ms=1)
    ref = await held_evidence.put(EvidenceCandidate(
        kind="structured_facts", payload={"pid": 1},
        captured_at_ms=int(time.time() * 1000)),
        mission_id=mission.mission_id)
    assert ref.relative_path is not None
    target = held_evidence.root / ref.relative_path
    assert target.exists()
    await asyncio.sleep(0.01)
    # A hold preserves the evidence through the same sweep.
    sweep = await store.enforce_retention(int(time.time() * 1000),
                                          hold_missions={mission.mission_id})
    assert sweep["deleted_evidence"] == 0
    assert target.exists()
    # The normal lifecycle sweep (no holds) deletes it: row tombstoned, file gone.
    await store.enforce_retention(int(time.time() * 1000))
    assert ref.relative_path is not None
    assert held_evidence.sweep_deleted_files([ref.relative_path]) == 1
    assert not target.exists()
    events = await store.get_events(mission.mission_id)
    assert any(e.kind == "retention" and e.safe_payload.get("tombstone") for e in events)


async def test_canaries_withheld_at_result_and_final_review_sinks(
    store: MissionStore, tmp_path: Any
) -> None:
    """A synthetic canary riding in an exception result or the final review
    is withheld at the persistence sink — never persisted verbatim."""
    canary = "sk-sink-synthetic-canary-12345678901234567890"
    mission, _ = await _claimed(store, tmp_path)
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["list_apps"])
    assert item is not None
    applied = await store.apply_result(StepResult(
        mission_id=mission.mission_id, plan_version=1, control_epoch=1, step_id="s1",
        execution_id=item.execution_id, attempt=1, status="NEEDS_CONTROLLER",
        effect_outcome="NOT_ATTEMPTED", failure_category="TRANSPORT_LOST",
        uncertainty=f"driver said: {canary}"))
    assert applied == "APPLIED"
    raw = store._conn.execute(
        "SELECT result_json FROM mission_attempts WHERE execution_id=?",
        (item.execution_id,)).fetchone()
    assert raw is not None and canary not in str(raw[0])
    assert "withheld" in str(raw[0])
    await store.record_final_review(
        mission.mission_id, 1, summary=f"review {canary}",
        unresolved_issues=[f"issue {canary}"])
    events = await store.get_events(mission.mission_id)
    reviews = [e for e in events if e.kind == "verification" and e.safe_payload.get("final_review")]
    assert reviews, "the advisory review is still recorded honestly"
    blob = json.dumps(reviews[-1].safe_payload)
    assert canary not in blob and "withheld" in blob


async def test_owner_deletion_removes_derivatives_with_tombstones(
    store: MissionStore, tmp_path: Any, monkeypatch: Any
) -> None:
    """Owner deletion removes evidence files and derived recommendations and
    keeps safe tombstones — the audit survives the content."""
    from assistant.missions.contracts import EvidenceCandidate
    from assistant.missions.service import MissionService

    mission, evidence = await _claimed(store, tmp_path)
    rec_store = RecommendationStore(tmp_path / "recs")
    recommendation = ObserverRecommendation(
        recommendation_id=new_id(), pattern_id="repeated_failure",
        mission_ids=[mission.mission_id], hypothesis="fixture hypothesis",
        confidence=0.5, created_at_ms=int(time.time() * 1000))
    rec_store.append(recommendation)
    ref = await evidence.put(EvidenceCandidate(
        kind="structured_facts", payload={"pid": 1},
        captured_at_ms=int(time.time() * 1000)),
        mission_id=mission.mission_id)
    assert ref.relative_path is not None
    assert (evidence.root / ref.relative_path).exists()

    service = MissionService(
        type("S", (), {})(), store=store, authority=MissionAuthority(store),
        evidence=evidence,
        executor=type("E", (), {})(),
        controller=type("C", (), {})(),
    )
    service._observer = type("O", (), {
        "prune_expired": lambda self, *a, **k: [],
        "delete_for_mission": lambda self, mid, *, now_ms: rec_store.delete_for_mission(
            mid, now_ms=now_ms),
        "analyze": lambda self, events: [],
    })()
    report = await service.purge_mission_derivatives(mission.mission_id)
    assert report["recommendations_deleted"], "the derived recommendation is removed"
    assert not (evidence.root / ref.relative_path).exists()
    assert rec_store.read_all() == []
    tombstones = (tmp_path / "recs" / "tombstones.jsonl").read_text()
    assert "tombstone" in tombstones
    rows = store._conn.execute(
        "SELECT kind FROM mission_retention_log WHERE kind='owner_deletion'").fetchall()
    assert rows, "a safe deletion tombstone is recorded"
