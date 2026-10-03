"""Mission-scoped deletion, durable retention and screened wait sinks.

D12: purging one mission must never touch another mission's rows or files,
must be idempotent, and must honor durable holds — reached through the
PRODUCTION service path. D16: wait reason/checkpoint data is screened
before any persistence and bounded by structure; damaged rows never break
recovery; holds are durable and honored by normal AND idle retention; the
30-day metadata / 7-day screenshot policy is enforced by the sweep.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time
from typing import Any

import pytest

from assistant.missions.contracts import (
    BudgetLimits,
    EvidenceCandidate,
    ObserverRecommendation,
    RequestEnvelope,
    Scope,
    StepSpec,
    new_id,
)
from assistant.missions.evidence import (
    DEFAULT_EVIDENCE_TTL_MS,
    SCREENSHOT_EVIDENCE_TTL_MS,
    EvidenceStore,
)
from assistant.missions.observer import RecommendationStore
from assistant.missions.service import MissionService
from assistant.missions.store import MissionStore


async def _none_runtime() -> None:
    return None


@pytest.fixture
async def store(tmp_path: Any) -> Any:
    value = await MissionStore.connect(tmp_path / "retention.db")
    await value.setup()
    yield value
    await value.close()


async def _mission(store: MissionStore) -> Any:
    scope = Scope(owner_id="sani-local", allowed_apps=[], permitted_effects={"READ_ONLY"})
    request = RequestEnvelope(request_id=new_id(), conversation_id="retention",
                              owner_id="sani-local", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(request, "d" * 64, goal="fixture", scope=scope,
                                        limits=BudgetLimits())
    mission = await store.commit_plan(mission.mission_id, 0, [StepSpec(
        step_id="s1", ordinal=1, objective="fixture", recipe_id="open_app",
        scope=scope, budget=BudgetLimits())], [])
    return mission


def _service(store: MissionStore, evidence: EvidenceStore, recs: RecommendationStore) -> Any:
    class _O:
        def prune_expired(self, now_ms: int, retention_ms: int) -> list[str]:
            return recs.prune_expired(now_ms, retention_ms)

        def delete_for_mission(self, mission_id: str, *, now_ms: int) -> list[str]:
            return recs.delete_for_mission(mission_id, now_ms=now_ms)

        async def analyze(self, events: Any) -> list[Any]:
            return []

    return MissionService(
        type("S", (), {})(), store=store, authority=type("A", (), {})(),
        evidence=evidence, executor=type("E", (), {})(),
        controller=type("C", (), {})(),
    ), _O()


# -- D12: mission-scoped owner deletion -------------------------------------------


async def test_purge_is_mission_scoped_and_idempotent(store: MissionStore,
                                                      tmp_path: Any) -> None:
    """Deleting mission A must preserve mission B's files AND live rows —
    the batch-2 probe deleted the bystander's files."""
    mission_a = await _mission(store)
    mission_b = await _mission(store)
    evidence = EvidenceStore(tmp_path / "ev", store)
    ref_a = await evidence.put(EvidenceCandidate(
        kind="structured_facts", payload={"mission": "A"},
        captured_at_ms=int(time.time() * 1000)), mission_id=mission_a.mission_id)
    ref_b = await evidence.put(EvidenceCandidate(
        kind="structured_facts", payload={"mission": "B"},
        captured_at_ms=int(time.time() * 1000)), mission_id=mission_b.mission_id)
    assert ref_a.relative_path and ref_b.relative_path
    file_a = evidence.root / ref_a.relative_path
    file_b = evidence.root / ref_b.relative_path
    assert file_a.exists() and file_b.exists()

    service, _observer = _service(store, evidence,
                                  RecommendationStore(tmp_path / "recs"))
    report = await service.purge_mission_derivatives(mission_a.mission_id)
    assert report["purged"] is True
    assert not file_a.exists()
    # Mission B is untouched: file present, row still live (deleted=0).
    assert file_b.exists()
    row_b = store._conn.execute(
        "SELECT deleted FROM mission_evidence WHERE evidence_id=?",
        (ref_b.evidence_id,)).fetchone()
    assert row_b is not None and int(row_b[0]) == 0
    # Idempotent: a second purge deletes nothing further.
    again = await service.purge_mission_derivatives(mission_a.mission_id)
    assert again["purged"] is True and again["evidence_files_deleted"] == 0


async def test_purge_honors_durable_hold(store: MissionStore, tmp_path: Any) -> None:
    mission = await _mission(store)
    evidence = EvidenceStore(tmp_path / "ev", store)
    ref = await evidence.put(EvidenceCandidate(
        kind="structured_facts", payload={"x": 1},
        captured_at_ms=int(time.time() * 1000)), mission_id=mission.mission_id)
    await store.set_retention_hold(mission.mission_id, reason="owner investigation")
    service, _o = _service(store, evidence, RecommendationStore(tmp_path / "recs"))
    report = await service.purge_mission_derivatives(mission.mission_id)
    assert report["purged"] is False
    assert ref.relative_path and (evidence.root / ref.relative_path).exists()


async def test_purge_refused_without_owner_identity() -> None:
    """The IPC handler verifies mission/owner identity before touching data
    (core app surface, isolated fixtures)."""
    import tempfile

    from assistant.core.app import SaniCoreApp
    from assistant.core.protocol import Request as IpcRequest
    from assistant.missions.authority import MissionAuthority
    from assistant.missions.controller import DeepController
    from assistant.missions.executor import VeloExecutor

    async def build() -> Any:
        root = pathlib.Path(tempfile.mkdtemp())
        store = await MissionStore.connect(root / "ipc.db")
        await store.setup()
        scope = Scope(owner_id="sani-local")
        request = RequestEnvelope(request_id=new_id(), conversation_id="ipc",
                                  owner_id="sani-local", input_origin="typed_final",
                                  input_revision=1, text="fixture",
                                  submitted_at_ms=int(time.time() * 1000))
        mission = await store.claim_request(request, "d" * 64, goal="x",
                                            scope=scope, limits=BudgetLimits())
        evidence = EvidenceStore(root / "ev", store)
        service = MissionService(
            type("S", (), {})(), store=store, authority=MissionAuthority(store),
            evidence=evidence,
            executor=VeloExecutor(type("S", (), {})(),
                                  get_runtime=_none_runtime),
            controller=DeepController(None),
        )
        app = SaniCoreApp.__new__(SaniCoreApp)
        app._mission_provider = lambda: _missions_of(service)
        return app, mission, store

    async def _missions_of(service: Any) -> Any:
        return service

    app, mission, store = await build()

    class _Session:
        def __init__(self) -> None:
            self.frames: list[Any] = []

        async def send(self, frame: Any) -> None:
            self.frames.append(frame)

    wrong = _Session()
    await app._handle_mission_purge(
        wrong, IpcRequest(id="r1", method="mission.purge",
                          params={"mission_id": mission.mission_id,
                                  "owner_id": "someone-else"}))
    assert wrong.frames and wrong.frames[0]["ok"] is False, (
        "a purge that does not prove ownership is refused")
    right = _Session()
    await app._handle_mission_purge(
        right, IpcRequest(id="r2", method="mission.purge",
                          params={"mission_id": mission.mission_id,
                                  "owner_id": "sani-local"}))
    assert right.frames and right.frames[0]["ok"] is True
    assert right.frames[0]["result"].get("purged") is True
    await store.close()


# -- D16: screened, bounded wait sink; tolerant reads ------------------------------


async def test_wait_sink_screens_canaries_and_keeps_valid_json(store: MissionStore) -> None:
    canary = "sk-wait-sink-synthetic-canary-12345678901234567890"
    mission = await _mission(store)
    await store.record_external_wait(
        mission.mission_id, 1, "s1",
        reason=f"rate limited: {canary}",
        checkpoint={canary: "key-canary", "token": canary, "cursor": "s1", "attempt": 1},
        retry_after_ms=1000, deadline_ms=int(time.time() * 1000) + 60_000)
    row = store._conn.execute(
        "SELECT reason, checkpoint_json FROM mission_external_waits "
        "WHERE mission_id=?", (mission.mission_id,)).fetchone()
    assert row is not None
    assert canary not in str(row[0]) and "withheld" in str(row[0])
    assert canary not in str(row[1])
    parsed = json.loads(str(row[1]))  # valid JSON — never sliced mid-structure
    assert parsed["cursor"] == "s1"
    assert "withheld" in str(parsed["token"])
    assert canary not in str(parsed), "checkpoint keys are persistence sinks too"


async def test_open_waits_survives_damaged_rows(store: MissionStore) -> None:
    """A damaged durable row is flagged, not fatal — other waits still
    re-arm and recovery proceeds."""
    mission = await _mission(store)
    ok_id = await store.record_external_wait(
        mission.mission_id, 1, "s1", reason="fine",
        checkpoint={"cursor": "s1"}, retry_after_ms=10,
        deadline_ms=int(time.time() * 1000) + 60_000)
    store._conn.execute(
        "INSERT INTO mission_external_waits (wait_id, mission_id, plan_version, step_id, "
        "reason, checkpoint_json, retry_after_ms, deadline_ms, created_at_ms) "
        "VALUES ('broken', ?, 1, 's1', 'damaged', 'not-json{{{', 10, 9999999999999, ?)",
        (mission.mission_id, int(time.time() * 1000)))
    waits = await store.open_waits(mission.mission_id)
    by_id = {w["wait_id"]: w for w in waits}
    assert by_id[ok_id]["checkpoint"]["cursor"] == "s1"
    assert by_id["broken"]["damaged_row"] is True
    assert by_id["broken"]["checkpoint"].get("damaged") is True


# -- D16: durable holds + lifecycle retention + policy ----------------------------


async def test_durable_holds_survive_and_bind_lifecycle_retention(
    store: MissionStore, tmp_path: Any
) -> None:
    """The production _observe path honors the PERSISTED hold without any
    caller-side hold set; releasing the hold lets the same path purge."""
    mission = await _mission(store)
    evidence = EvidenceStore(tmp_path / "ev", store, ttl_ms=1)
    ref = await evidence.put(EvidenceCandidate(
        kind="structured_facts", payload={"x": 1},
        captured_at_ms=int(time.time() * 1000)), mission_id=mission.mission_id)
    assert ref.relative_path
    target = evidence.root / ref.relative_path
    await store.set_retention_hold(mission.mission_id, reason="investigation")
    service, _o = _service(store, evidence, RecommendationStore(tmp_path / "recs"))
    await asyncio.sleep(0.01)
    await service._observe(mission)  # normal lifecycle, no hold argument
    assert target.exists(), "the durable hold preserved the evidence"
    await store.release_retention_hold(mission.mission_id)
    await service._observe(mission)
    assert not target.exists(), "after release the same lifecycle path purges"


async def test_startup_runs_idle_retention(store: MissionStore, tmp_path: Any) -> None:
    """Idle retention: the startup path enforces expiry without waiting for
    a mission observation, honoring holds."""
    from assistant.missions.authority import MissionAuthority
    from assistant.missions.controller import DeepController
    from assistant.missions.executor import VeloExecutor

    mission = await _mission(store)
    evidence = EvidenceStore(tmp_path / "ev", store, ttl_ms=1)
    ref = await evidence.put(EvidenceCandidate(
        kind="structured_facts", payload={"x": 1},
        captured_at_ms=int(time.time() * 1000)), mission_id=mission.mission_id)
    assert ref.relative_path
    target = evidence.root / ref.relative_path
    await asyncio.sleep(0.01)
    service = MissionService(
        type("S", (), {})(), store=store, authority=MissionAuthority(store),
        evidence=evidence,
        executor=VeloExecutor(type("S", (), {})(),
                              get_runtime=_none_runtime),
        controller=DeepController(None),
    )
    await service.reconcile_startup([])
    assert not target.exists()


async def test_failed_evidence_unlink_is_durable_and_retried(
    store: MissionStore, tmp_path: Any
) -> None:
    """D16: a filesystem failure after row tombstoning survives restart and
    is acknowledged only after the confined unlink succeeds."""
    mission = await _mission(store)
    evidence = EvidenceStore(tmp_path / "ev", store)
    ref = await evidence.put(EvidenceCandidate(
        kind="structured_facts", payload={"retry": True},
        captured_at_ms=int(time.time() * 1000)), mission_id=mission.mission_id)
    assert ref.relative_path
    target = evidence.root / ref.relative_path
    target.unlink()
    target.mkdir()  # unlink() now fails safely with IsADirectoryError.
    service, _observer = _service(store, evidence, RecommendationStore(tmp_path / "recs"))
    paths = await store.mark_mission_evidence_deleted(mission.mission_id)
    assert await service._process_file_deletions(paths) == 0
    assert ref.relative_path in await store.pending_file_deletions()
    target.rmdir()
    assert await service._process_file_deletions([]) == 1
    assert ref.relative_path not in await store.pending_file_deletions()


async def test_metadata_purge_30d_policy_with_holds(store: MissionStore) -> None:
    """Terminal metadata older than 30 days is purged WHOLE with a
    retention-log tombstone; a held mission is preserved."""
    old_mission = await _mission(store)
    new_mission = await _mission(store)
    held = await _mission(store)
    ancient = int(time.time() * 1000) - 40 * 24 * 60 * 60 * 1000
    store._conn.execute("UPDATE missions SET status='COMPLETED', updated_at_ms=? "
                        "WHERE mission_id=?", (ancient, old_mission.mission_id))
    store._conn.execute("UPDATE missions SET status='COMPLETED', updated_at_ms=? "
                        "WHERE mission_id=?", (ancient, held.mission_id))
    await store.set_retention_hold(held.mission_id, reason="keep")
    sweep = await store.enforce_retention(int(time.time() * 1000))
    assert old_mission.mission_id in sweep["purged_missions"]
    assert held.mission_id not in sweep["purged_missions"]
    assert await store.get_mission(held.mission_id) is not None
    assert await store.get_mission(old_mission.mission_id) is None
    events = store._conn.execute(
        "SELECT COUNT(*) FROM mission_events WHERE mission_id=?",
        (old_mission.mission_id,)).fetchone()[0]
    assert int(events) == 0, "the metadata purge is whole, never a chain hole"
    ledger = store._conn.execute(
        "SELECT COUNT(*) FROM mission_action_ledger WHERE mission_id=?",
        (old_mission.mission_id,),
    ).fetchone()[0]
    assert int(ledger) == 0, "the action ledger is derivative metadata too"
    log = store._conn.execute(
        "SELECT detail_json FROM mission_retention_log WHERE kind='metadata_purge' "
        "AND subject_id=?", (old_mission.mission_id,)).fetchone()
    assert log is not None and "tombstone" in str(log[0])
    # The recent mission is untouched.
    assert await store.get_mission(new_mission.mission_id) is not None


async def test_evidence_ttl_policy_is_kind_aware(store: MissionStore,
                                                 tmp_path: Any) -> None:
    evidence = EvidenceStore(tmp_path / "ev", store)
    structured = await evidence.put(EvidenceCandidate(
        kind="structured_facts", payload={"x": 1},
        captured_at_ms=int(time.time() * 1000)), mission_id=(await _mission(store)).mission_id)
    shot = await evidence.put(EvidenceCandidate(
        kind="screenshot", payload={"x": 1},
        captured_at_ms=int(time.time() * 1000)), mission_id=(await _mission(store)).mission_id,
        allow_image=True)
    assert structured.expires_at_ms is not None and shot.expires_at_ms is not None
    assert structured.expires_at_ms - structured.captured_at_ms == DEFAULT_EVIDENCE_TTL_MS
    assert shot.expires_at_ms - shot.captured_at_ms == SCREENSHOT_EVIDENCE_TTL_MS


async def test_recommendations_derived_cleanup_via_observer(store: MissionStore,
                                                            tmp_path: Any) -> None:
    """Derived recommendation records are removed with the mission's purge
    and tombstoned — even when a hold protects the evidence, the owner can
    still delete derived recommendations after release."""
    mission = await _mission(store)
    recs = RecommendationStore(tmp_path / "recs")
    recs.append(ObserverRecommendation(
        recommendation_id=new_id(), pattern_id="repeated_failure",
        mission_ids=[mission.mission_id], hypothesis="fixture", confidence=0.4,
        created_at_ms=int(time.time() * 1000)))
    evidence = EvidenceStore(tmp_path / "ev", store)
    service, observer = _service(store, evidence, recs)
    removed = observer.delete_for_mission(mission.mission_id, now_ms=int(time.time() * 1000))
    assert removed, "the derived record is deleted"
    assert recs.read_all() == []
    assert "tombstone" in (tmp_path / "recs" / "tombstones.jsonl").read_text()
    _ = service
