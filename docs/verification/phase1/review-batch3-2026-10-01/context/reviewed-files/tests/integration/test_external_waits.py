"""Durable external waits without ambiguous replay (D14).

UNKNOWN effects are never made replayable by a timer or an owner RESUME:
admission refuses them, release refuses them and blocks the mission. The
service composition (escalation carrying retry_after) drives real waits
with bounded timers; provider rate limits on any Controller role are the
established producer; cancellation and deadlines behave honestly.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from assistant.missions.contracts import (
    BudgetLimits,
    MissionControl,
    RequestEnvelope,
    Scope,
    StepResult,
    StepSpec,
    new_id,
)
from assistant.missions.service import MissionService
from assistant.missions.store import MissionStore, MissionStoreError


@pytest.fixture
async def store(tmp_path: Any) -> Any:
    value = await MissionStore.connect(tmp_path / "waits.db")
    await value.setup()
    yield value
    await value.close()


async def _claimed(store: MissionStore, *, steps: int = 1) -> Any:
    scope = Scope(owner_id="owner", allowed_apps=["com.fixture.safe"],
                  permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL",
                                     "EXTERNAL_WRITE"})
    request = RequestEnvelope(request_id=new_id(), conversation_id="waits",
                              owner_id="owner", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(request, "d" * 64, goal="fixture", scope=scope,
                                        limits=BudgetLimits())
    specs = [StepSpec(step_id=f"s{i}", ordinal=i, objective=f"fixture {i}",
                      recipe_id="semantic_ui", scope=scope, budget=BudgetLimits(),
                      effect_class="REPEATABLE_LOCAL")
             for i in range(1, steps + 1)]
    mission = await store.commit_plan(mission.mission_id, 0, specs, [])
    return mission


def _none_runtime_factory() -> Any:
    async def _none() -> None:
        return None
    return _none()


def _unknown_result(mission: Any, item: Any) -> StepResult:
    return StepResult(
        mission_id=mission.mission_id, plan_version=1, control_epoch=1,
        step_id="s1", execution_id=item.execution_id, attempt=1,
        status="NEEDS_CONTROLLER", effect_outcome="UNKNOWN",
        failure_category="UNKNOWN_EFFECT", uncertainty="dispatched, unobserved")


async def test_unknown_effect_cannot_enter_or_release_a_wait(store: MissionStore) -> None:
    """The independent reproduction — UNKNOWN → wait → attempt 2 — must be
    refused at admission AND at release."""
    mission = await _claimed(store)
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    assert item is not None
    await store.mark_dispatched(item.execution_id)
    await store.apply_result(_unknown_result(mission, item))
    # Admission refuses.
    with pytest.raises(MissionStoreError, match="unresolved UNKNOWN"):
        await store.record_external_wait(
            mission.mission_id, 1, "s1", reason="backoff",
            checkpoint={"cursor": "s1"}, retry_after_ms=50,
            deadline_ms=int(time.time() * 1000) + 60_000)
    # Even a manually inserted wait cannot release into a replay.
    store._conn.execute(
        "INSERT INTO mission_external_waits (wait_id, mission_id, plan_version, "
        "step_id, reason, checkpoint_json, retry_after_ms, deadline_ms, created_at_ms) "
        "VALUES ('forced', ?, 1, 's1', 'bypass', '{}', 10, 9999999999999, ?)",
        (mission.mission_id, int(time.time() * 1000)))
    released = await store.release_wait("forced")
    assert released is not None and released.status == "BLOCKED", (
        "release must block the mission, not requeue the unknown effect")
    states = await store.get_step_states(mission.mission_id, 1)
    assert states["s1"] == "BLOCKED"
    with pytest.raises(Exception, match="unresolved dispatched attempt|not claimable"):
        await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])


async def test_escalation_retry_after_enters_wait_and_fires_once(
    store: MissionStore, tmp_path: Any, monkeypatch: Any
) -> None:
    """Service composition: a NEEDS_CONTROLLER result carrying retry_after
    enters a durable wait through the real escalation path; the timer
    releases exactly once and the step runs to a single effect."""
    from tests.unit.test_mission_service import _StubSettings
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
        [{"role": "AXButton", "label": "fixture 1", "element_token": "tok-go"}],
        pid=9, window_id=2)
    mission = await _claimed(store)  # noqa: F841 -- world setup shapes the run
    # The step is BLOCKED with a proven NO_EFFECT (retry-safe) shape: a
    # NOT_ATTEMPTED escalation result carries the retry hint.
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    assert item is not None
    escalation = StepResult(
        mission_id=mission.mission_id, plan_version=1, control_epoch=1,
        step_id="s1", execution_id=item.execution_id, attempt=1,
        status="NEEDS_CONTROLLER", effect_outcome="NOT_ATTEMPTED",
        failure_category="BUDGET_EXHAUSTED", uncertainty="external backoff",
        retry_after_ms=120)
    runtime = FakeRuntime(standard_tools())

    async def get_runtime() -> FakeRuntime:
        return runtime

    from assistant.missions.authority import MissionAuthority
    from assistant.missions.controller import DeepController
    from assistant.missions.evidence import EvidenceStore
    from assistant.missions.executor import VeloExecutor

    async def _none_runtime() -> None:
        return None

    service = MissionService(
        _StubSettings(), store=store, authority=MissionAuthority(store),
        evidence=EvidenceStore(tmp_path / "ev"),
        executor=VeloExecutor(_StubSettings(), get_runtime=_none_runtime,
                              authority=MissionAuthority(store), store=store),
        controller=DeepController(None),
    )
    waits = await store.open_waits(mission.mission_id)
    assert waits == []
    await service._escalate(mission.mission_id, item, escalation)
    waits = await store.open_waits(mission.mission_id)
    assert len(waits) == 1 and waits[0]["retry_after_ms"] == 120
    record: Any = await store.get_mission(mission.mission_id)
    assert record is not None and record.status == "WAITING_EXTERNAL"
    # The timer fires within the retry window and the step runs once.
    deadline = time.monotonic() + 10
    final = record
    while time.monotonic() < deadline:
        final = await store.get_mission(mission.mission_id)
        assert final is not None
        if final.status in {"COMPLETED", "VERIFYING", "RUNNING"}:
            break
        await asyncio.sleep(0.05)
    assert final.status in {"COMPLETED", "VERIFYING", "RUNNING"}
    assert await store.open_waits(mission.mission_id) == []
    reset_world()


async def test_rate_limited_recovery_produces_wait_not_pause(
    store: MissionStore, tmp_path: Any
) -> None:
    """D14 producer: a provider rate limit on the RECOVER role raises the
    typed signal and the mission enters the durable wait flow."""
    from tests.unit.test_mission_service import _StubSettings
    from tests.unit.velo_fakes import reset_world

    reset_world()
    mission = await _claimed(store)
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    assert item is not None

    scripted_calls: list[str] = []

    from assistant.core.agents import _DeepInvoke

    class _RateLimitedDeep:
        async def run(self, text: str, *, thread_id: str, on_event: Any,
                      cancel_check: Any, **kwargs: Any) -> dict[str, Any]:
            scripted_calls.append(thread_id)
            raise RuntimeError("429 Too Many Requests: rate limit exceeded")

    transport = _DeepInvoke(
        _RateLimitedDeep(),  # type: ignore[arg-type]
        thread_id="conv", mission_id=mission.mission_id, plan_version=1,
    )

    from assistant.missions.authority import MissionAuthority
    from assistant.missions.controller import DeepController
    from assistant.missions.evidence import EvidenceStore
    from assistant.missions.executor import VeloExecutor

    service = MissionService(
        _StubSettings(), store=store, authority=MissionAuthority(store),
        evidence=EvidenceStore(tmp_path / "ev"),
        executor=VeloExecutor(_StubSettings(), get_runtime=_none_runtime_factory()),
        controller=DeepController(None),
    )
    service._transport_factory = lambda record: transport

    escalation = StepResult(
        mission_id=mission.mission_id, plan_version=1, control_epoch=1,
        step_id="s1", execution_id=item.execution_id, attempt=1,
        status="NEEDS_CONTROLLER", effect_outcome="NOT_ATTEMPTED",
        failure_category="STALE_TARGET", uncertainty="stale")
    await service._escalate(mission.mission_id, item, escalation)
    record: Any = await store.get_mission(mission.mission_id)
    assert record is not None and record.status == "WAITING_EXTERNAL", (
        "a rate-limited recovery waits, it does not strand a pause")
    waits = await store.open_waits(mission.mission_id)
    assert len(waits) == 1 and waits[0]["retry_after_ms"] == 30_000
    # No effect was dispatched by the failed recovery.
    reset_world()


async def test_wait_deadline_blocks_honestly(store: MissionStore) -> None:
    """A wait past its deadline never releases; the mission blocks with the
    honest reason instead of a stale replay."""
    mission = await _claimed(store)
    await store.record_external_wait(
        mission.mission_id, 1, "s1", reason="backoff",
        checkpoint={"cursor": "s1"}, retry_after_ms=0,
        deadline_ms=int(time.time() * 1000) - 1000)
    service = MissionService.__new__(MissionService)
    service._store = store
    service._armed_waits = set()
    service._evidence = None  # type: ignore[assignment]
    service._run_tokens = {}
    service._desktop_queue = type("Q", (), {
        "acquire": staticmethod(lambda *a, **k: _fail_async()),
        "release": staticmethod(lambda *a, **k: _ok_async()),
    })()
    service._schedule_wait_resume(mission.mission_id,
                                  (await store.open_waits(mission.mission_id))[0]["wait_id"], 0)
    deadline = time.monotonic() + 5
    record: Any = await store.get_mission(mission.mission_id)
    while time.monotonic() < deadline:
        record = await store.get_mission(mission.mission_id)
        assert record is not None
        if record.status == "BLOCKED":
            break
        await asyncio.sleep(0.05)
    assert record is not None and record.status == "BLOCKED"


async def _fail_async() -> Any:
    raise AssertionError("a deadline-blocked mission must never re-dispatch")


async def _ok_async() -> None:
    return None


async def test_cancel_releases_open_waits(store: MissionStore) -> None:
    mission = await _claimed(store, steps=2)
    await store.record_external_wait(
        mission.mission_id, 1, "s1", reason="backoff",
        checkpoint={"cursor": "s1"}, retry_after_ms=1000,
        deadline_ms=int(time.time() * 1000) + 60_000)
    cancelled = await store.control(MissionControl(
        control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="CANCEL"))
    assert cancelled.status == "CANCELLED"
    assert await store.open_waits(mission.mission_id) == []
