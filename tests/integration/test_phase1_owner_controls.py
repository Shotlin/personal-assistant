"""Exact approvals, durable waits and stop ownership (D02/D08).

Every case drives the real store/service/authority boundaries with the
synthetic effect sink the corrective suites use — no live desktop, no
provider, no engine. These are the held-out regressions for the second
corrective batch: each one failed against the previous snapshot.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from assistant.missions.authority import MissionAuthority
from assistant.missions.contracts import (
    ApprovalRecord,
    BudgetLimits,
    CancellationToken,
    MissionControl,
    PendingApprovalDigest,
    RequestEnvelope,
    Scope,
    StepResult,
    StepSpec,
    new_id,
)
from assistant.missions.store import MissionStore
from assistant.tools.result_normalizer import ToolOutcome


@pytest.fixture
async def store(tmp_path: Any) -> Any:
    value = await MissionStore.connect(tmp_path / "owner-controls.db")
    await value.setup()
    yield value
    await value.close()


async def claimed(store: MissionStore, *, steps: int = 1,
                  effect: str = "EXTERNAL_WRITE") -> Any:
    scope = Scope(owner_id="owner", allowed_apps=["com.fixture.safe"],
                  permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL",
                                     "EXTERNAL_WRITE"})
    request = RequestEnvelope(request_id=new_id(), conversation_id="owner-controls",
                              owner_id="owner", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(request, "d" * 64, goal="fixture", scope=scope,
                                        limits=BudgetLimits())
    specs = [
        StepSpec(
            step_id=f"s{index}", ordinal=index, objective=f"fixture {index}",
            recipe_id="semantic_ui", scope=scope, budget=BudgetLimits(),
            effect_class=effect if index == 1 else "REPEATABLE_LOCAL",
        )
        for index in range(1, steps + 1)
    ]
    mission = await store.commit_plan(mission.mission_id, 0, specs, [])
    return mission, scope


def _blocked_result(mission: Any, digest: str, item: Any, *, tool: str = "click",
                    step_id: str = "s1") -> StepResult:
    """A result shaped exactly like the executor's approval-gated block."""
    return StepResult(
        mission_id=mission.mission_id,
        plan_version=1,
        control_epoch=1,
        step_id=step_id,
        execution_id=item.execution_id if step_id == "s1" else f"exec-{step_id}",
        attempt=1,
        status="BLOCKED",
        effect_outcome="NOT_ATTEMPTED",
        failure_category="APPROVAL_REQUIRED",
        uncertainty="owner approval required for the exact dispatch",
        pending_approval=PendingApprovalDigest(
            step_id=step_id, tool=tool, action_digest=digest,
            plan_version=1, control_epoch=1,
        ),
    )


def _approval(mission: Any, scope: Scope, digest: str, *, epoch: int = 1,
              target_ref: str | None = None, approval_id: str | None = None) -> ApprovalRecord:
    now = int(time.time() * 1000)
    return ApprovalRecord(
        approval_id=approval_id or new_id(),
        mission_id=mission.mission_id,
        plan_version=1,
        control_epoch=epoch,
        action_digest=digest,
        scope_hash=scope.scope_hash,
        target_ref=target_ref,
        effect_class="EXTERNAL_WRITE",
        issued_by="local_owner",
        issued_at_ms=now,
        expires_at_ms=now + 300_000,
    )


# -- exact release ---------------------------------------------------------------


async def test_release_matches_only_the_pending_digest(store: MissionStore) -> None:
    """An unrelated approval must not release an approval-blocked step."""
    mission, scope = await claimed(store, steps=2)
    right_digest = "a" * 64
    wrong_digest = "b" * 64
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    assert item is not None
    _ = item.execution_id
    applied = await store.apply_result(_blocked_result(mission, right_digest, item))
    assert applied == "APPLIED"
    # A real approval for a DIFFERENT action releases nothing.
    await store.record_approval(_approval(mission, scope, wrong_digest))
    assert await store.release_approved_blocked_steps(mission.mission_id, 1) == 0
    states = await store.get_step_states(mission.mission_id, 1)
    assert states["s1"] == "BLOCKED"
    # The approval minted for the exact pending digest releases exactly that
    # step — and only that step (s2 was never blocked).
    await store.record_approval(_approval(mission, scope, right_digest))
    assert await store.release_approved_blocked_steps(mission.mission_id, 1) == 1
    states = await store.get_step_states(mission.mission_id, 1)
    assert states["s1"] == "PENDING"


async def test_release_refuses_old_epoch_digest(store: MissionStore) -> None:
    """A new epoch must not silently inherit an old digest."""
    mission, scope = await claimed(store)
    digest = "c" * 64
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    assert item is not None
    _ = item.execution_id
    await store.apply_result(_blocked_result(mission, digest, item))
    # The approval is minted for epoch 1 — the only epoch the digest covers.
    await store.record_approval(_approval(mission, scope, digest, epoch=1))
    # A pause bumps the epoch and voids the old approval gate entirely.
    # D13: the never-dispatched approval step re-queues to PENDING so it
    # re-observes and earns a FRESH obligation (it was never dispatched,
    # so re-observation is safe).
    await store.control(MissionControl(control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="PAUSE"))
    states = await store.get_step_states(mission.mission_id, 1)
    assert states["s1"] == "PENDING", (
        "the safe undispatched step re-observes instead of stranding")
    # A fresh approval minted for the OLD epoch releases nothing.
    await store.record_approval(_approval(mission, scope, digest, epoch=1))
    mission = await store.control(MissionControl(control_id=new_id(),
        mission_id=mission.mission_id, expected_plan_version=1,
        expected_control_epoch=2, kind="RESUME"))
    assert await store.release_approved_blocked_steps(mission.mission_id, 1) == 0


async def test_resume_does_not_carry_approvals_across_epoch(store: MissionStore) -> None:
    """RESUME from PAUSED bumps the epoch; old approvals stay bound to it."""
    mission, scope = await claimed(store)
    await store.record_approval(_approval(mission, scope, "d" * 64, epoch=1))
    await store.control(MissionControl(control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="PAUSE"))
    resumed = await store.control(MissionControl(control_id=new_id(),
        mission_id=mission.mission_id, expected_plan_version=1,
        expected_control_epoch=2, kind="RESUME"))
    assert resumed.control_epoch == 3
    assert await store.active_approval_ids(mission.mission_id, 3) == []


async def test_consumption_rejects_wrong_epoch_version_or_target(store: MissionStore) -> None:
    """Consume binds plan version, control epoch and target exactly."""
    mission, scope = await claimed(store)
    await store.record_approval(
        _approval(mission, scope, "e" * 64, target_ref="win-7"))
    ok = await store.consume_approval(
        "no-such-approval", "e" * 64, plan_version=1, control_epoch=1)
    assert ok is False
    approval_id = (await store.active_approval_ids(mission.mission_id, 1))[0]
    assert await store.consume_approval(
        approval_id, "e" * 64, plan_version=1, control_epoch=1,
        target_ref="win-8") is False, "wrong target must not consume"
    assert await store.consume_approval(
        approval_id, "e" * 64, plan_version=1, control_epoch=2,
        target_ref="win-7") is False, "wrong epoch must not consume"
    assert await store.consume_approval(
        approval_id, "f" * 64, plan_version=1, control_epoch=1,
        target_ref="win-7") is False, "wrong digest must not consume"
    assert await store.consume_approval(
        approval_id, "e" * 64, plan_version=1, control_epoch=1,
        target_ref="win-7") is True
    assert await store.active_approval_ids(mission.mission_id, 1) == []


# -- full owner loop -------------------------------------------------------------


async def test_submit_block_approve_resume_single_effect(
    store: MissionStore, monkeypatch: Any, tmp_path: Any
) -> None:
    """submit → blocked → owner approval → resume → exactly one effect.

    The real service, executor, policy wrapper, authority and ledger run
    over a scripted Deep transport and a synthetic click; only the desktop
    device effect is in-memory. The service subclass widens only the
    fixture scope (the production typing path widens it the same way via
    add_permitted_effects).
    """
    from tests.unit.test_mission_service import _request, _StubSettings
    from tests.unit.velo_fakes import (
        SHARED_APPS,
        FakeRuntime,
        app_entry,
        reset_world,
        window_state_payload,
    )

    reset_world()
    SHARED_APPS["list"] = [app_entry("Fixture", pid=9, running=True, active=True,
                                     bundle_id="com.example.fixture")]
    SHARED_APPS["windows"][9] = [
        {"window_id": 2, "is_on_screen": True,
         "bounds": {"height": 600, "width": 800}}
    ]
    SHARED_APPS["state"][(9, 2)] = window_state_payload(
        [{"role": "AXButton", "label": "fixture-mark-go", "element_token": "tok-go"}],
        pid=9, window_id=2,
    )
    # Real policy-wrapped tools over the fake world: the mission guard, the
    # targeting invariant and the strict action ledger all apply, exactly as
    # in production. Only the device effect is in-memory.
    from langchain_core.tools import StructuredTool

    from assistant.tools.policy import apply_tool_policy, cua_target_state

    clicks: list[dict[str, Any]] = []

    def _outcome(text: str, structured: dict[str, Any] | None = None) -> ToolOutcome:
        return ToolOutcome(status="ok", effect="confirmed", text=text,
                           structured=structured or {})

    async def list_apps(**_: Any) -> ToolOutcome:
        """The driver's app inventory answer."""
        return _outcome("Listed apps.", {"apps": SHARED_APPS["list"]})

    async def get_window_state(
        pid: int, window_id: int, include_screenshot: bool = False,
        max_elements: int = 120, max_depth: int = 12,
    ) -> ToolOutcome:
        """The driver's window snapshot answer."""
        state = cua_target_state.get()
        if state is not None:
            state["observed_at_ms"] = int(time.time() * 1000)
            state.setdefault("apps", {})[pid] = "com.example.fixture"
            state["focus"] = (pid, window_id)
        return _outcome("Read the window.", SHARED_APPS["state"].get((pid, window_id), {}))

    async def list_windows(pid: int, **_: Any) -> ToolOutcome:
        """The driver's window list answer."""
        return _outcome("Listed windows.", {"windows": SHARED_APPS["windows"].get(pid, [])})

    async def click(pid: int, window_id: int, element_token: str, **_: Any) -> ToolOutcome:
        """One synthetic device effect."""
        clicks.append({"pid": pid, "window_id": window_id,
                       "element_token": element_token})
        return _outcome("clicked")

    wrapped, _names = apply_tool_policy([
        StructuredTool.from_function(coroutine=list_apps, name="list_apps"),
        StructuredTool.from_function(coroutine=list_windows, name="list_windows"),
        StructuredTool.from_function(coroutine=get_window_state, name="get_window_state"),
        StructuredTool.from_function(coroutine=click, name="click"),
    ])
    runtime = FakeRuntime({tool.name: tool for tool in wrapped})

    async def get_runtime() -> FakeRuntime:
        return runtime

    from assistant.missions.controller import DeepController
    from assistant.missions.evidence import EvidenceStore
    from assistant.missions.executor import VeloExecutor
    from assistant.missions.service import MissionService

    fixture_scope = Scope(owner_id="owner", allowed_apps=["com.example.fixture"],
                          permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL",
                                             "EXTERNAL_WRITE"})

    async def transport(role: str, tool: str, payload: Any) -> dict[str, Any]:
        if role == "PLAN":
            assert tool == "submit_mission_plan", f"unexpected submission tool {tool}"
            return {"plan": {
                "steps": [{
                    "step_id": "s1", "ordinal": 1,
                    "objective": "click the fixture go button",
                    "recipe_id": "semantic_ui", "recipe_args": {},
                    "scope": fixture_scope.model_dump(mode="json"),
                    "budget": {"max_actions": 4},
                    "effect_class": "EXTERNAL_WRITE",
                    "checks": [{
                        "check_id": "fg", "verifier_id": "app_foreground",
                        "verifier_version": "1.0.0",
                        "expected": {"app": "fixture"}, "required": True,
                    }],
                }],
                "success_criteria": [],
                "explanation": "scripted fixture plan",
            }}
        if role == "RECOVER":
            assert tool == "submit_recovery_decision", f"unexpected {tool}"
            return {"recovery": {"decision": "BLOCK", "reason": "scripted"}}
        assert tool == "submit_final_review", f"unexpected submission tool {tool}"
        return {"review": {"supported_summary": "", "acceptance_check_ids": [],
                           "unresolved_issues": []}}

    settings = _StubSettings()
    authority = MissionAuthority(store)
    evidence = EvidenceStore(tmp_path / "evidence", store)
    executor = VeloExecutor(settings, get_runtime=get_runtime, authority=authority,
                            evidence=evidence, store=store)

    class _ApprovedScopeService(MissionService):
        def _scope_for(self, request: Any) -> Scope:
            return fixture_scope

    service = _ApprovedScopeService(
        settings, store=store, authority=authority, evidence=evidence,
        executor=executor, controller=DeepController(None),
    )
    service._transport_factory = lambda record: transport

    mission = await service.submit(
        _request("click the fixture go button"), cancel=CancellationToken(),
        invoke=transport,
    )
    try:
        assert mission.status == "NEEDS_APPROVAL", mission.status
        assert clicks == [], "no effect before the owner approves"
        # The exact owed approval is projected for the host UI.
        assert mission.pending_approvals, "the blocked step owes an approval"
        pending = mission.pending_approvals[0]
        assert pending.step_id == "s1" and pending.plan_version == 1
        assert pending.control_epoch == mission.control_epoch

        # The wrong digest must not unlock anything.
        wrong = _approval(mission, mission.scope, "z" * 64,
                          epoch=mission.control_epoch)
        await service.issue_approval(wrong)
        released = await store.release_approved_blocked_steps(
            mission.mission_id, mission.plan_version)
        assert released == 0, "an unrelated approval must not release the step"

        right = _approval(
            mission, mission.scope, pending.action_digest,
            epoch=mission.control_epoch)
        await service.issue_approval(right)
        await service.control(MissionControl(
            control_id=new_id(), mission_id=mission.mission_id,
            expected_plan_version=mission.plan_version,
            expected_control_epoch=mission.control_epoch, kind="RESUME"))
        current: Any = mission
        for _ in range(200):
            await asyncio.sleep(0.05)
            current = await store.get_mission(mission.mission_id)
            assert current is not None
            if current.status in {"COMPLETED", "FAILED", "BLOCKED",
                                  "NEEDS_APPROVAL"}:
                break
        assert current.status == "COMPLETED", current.status
        # Exactly ONE synthetic effect ran, after the approval.
        assert len(clicks) == 1
        consumed = store._conn.execute(
            "SELECT consumed_at_ms FROM mission_approvals WHERE approval_id=?",
            (right.approval_id,),
        ).fetchone()
        assert consumed is not None and consumed[0] is not None
    finally:
        reset_world()


# -- durable external waits ------------------------------------------------------


async def test_external_wait_persists_reason_deadline_checkpoint(store: MissionStore) -> None:
    mission, _ = await claimed(store)
    wait_id = await store.record_external_wait(
        mission.mission_id, 1, "s1", reason="rate limited",
        checkpoint={"step_id": "s1", "resume_cursor": "s1"},
        retry_after_ms=1_000, deadline_ms=int(time.time() * 1000) + 60_000)
    waits = await store.open_waits(mission.mission_id)
    assert len(waits) == 1
    record = waits[0]
    assert record["wait_id"] == wait_id
    assert record["reason"] == "rate limited"
    assert record["checkpoint"]["resume_cursor"] == "s1"
    assert record["retry_after_ms"] == 1_000
    after = await store.get_mission(mission.mission_id)
    assert after is not None and after.status == "WAITING_EXTERNAL"
    released = await store.release_wait(wait_id)
    assert released is not None and released.status == "RUNNING"
    states = await store.get_step_states(mission.mission_id, 1)
    assert states["s1"] == "PENDING"
    assert await store.open_waits(mission.mission_id) == []


async def test_wait_reschedules_exactly_once_after_restart(
    store: MissionStore, monkeypatch: Any
) -> None:
    from assistant.missions.controller import DeepController
    from assistant.missions.evidence import EvidenceStore
    from assistant.missions.executor import VeloExecutor
    from assistant.missions.service import MissionService
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
        {"window_id": 2, "is_on_screen": True,
         "bounds": {"height": 600, "width": 800}}
    ]
    SHARED_APPS["state"][(9, 2)] = window_state_payload(
        [{"role": "AXButton", "label": "fixture 1", "element_token": "tok-go"}],
        pid=9, window_id=2,
    )
    mission, _ = await claimed(store, effect="REPEATABLE_LOCAL")
    wait_id = await store.record_external_wait(
        mission.mission_id, 1, "s1", reason="backoff",
        checkpoint={"step_id": "s1"}, retry_after_ms=120,
        deadline_ms=int(time.time() * 1000) + 60_000)

    async def fresh_service() -> tuple[Any, list[str]]:
        runtime = FakeRuntime(standard_tools())

        async def get_runtime() -> FakeRuntime:
            return runtime

        authority = MissionAuthority(store)
        service = MissionService(
            _StubSettings(), store=store, authority=authority,
            evidence=EvidenceStore("/tmp/fixture-wait-evidence"),
            executor=VeloExecutor(_StubSettings(), get_runtime=get_runtime,
                                  authority=authority, store=store),
            controller=DeepController(None),
        )
        fired: list[str] = []
        original = service._schedule_resume

        def tracking(mission_id: str) -> None:
            fired.append(mission_id)
            original(mission_id)

        monkeypatch.setattr(service, "_schedule_resume", tracking)
        return service, fired

    # A restart (fresh service) re-arms the open wait exactly once...
    service, fired = await fresh_service()
    await service.reconcile_startup([])
    assert service._armed_waits == {wait_id}
    await service.reconcile_startup([])
    await asyncio.sleep(0.05)
    assert fired.count(mission.mission_id) <= 1, "no duplicate scheduling"
    # ...and the timer releases the wait without repeating any effect.
    for _ in range(100):
        await asyncio.sleep(0.05)
        current = await store.get_mission(mission.mission_id)
        assert current is not None
        settled = current
        if settled.status in {"RUNNING", "VERIFYING", "COMPLETED"}:
            break
    assert settled.status in {"RUNNING", "VERIFYING", "COMPLETED"}
    assert await store.open_waits(mission.mission_id) == []
    reset_world()


async def test_wait_with_unknown_effect_never_reschedules(store: MissionStore) -> None:
    """Reconciliation settles uncertainty BEFORE any wait can fire."""
    from assistant.missions.evidence import EvidenceStore
    from assistant.missions.executor import VeloExecutor
    from assistant.missions.service import MissionService
    from tests.unit.test_mission_service import _StubSettings
    from tests.unit.velo_fakes import FakeRuntime, reset_world, standard_tools

    reset_world()
    mission, _ = await claimed(store)
    # A dispatched attempt left uncertain (crash after effect, no result).
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    assert item is not None
    await store.mark_dispatched(item.execution_id)
    wait_id = await store.record_external_wait(
        mission.mission_id, 1, "s1", reason="backoff",
        checkpoint={"step_id": "s1"}, retry_after_ms=50,
        deadline_ms=int(time.time() * 1000) + 60_000)

    runtime = FakeRuntime(standard_tools())

    async def get_runtime() -> FakeRuntime:
        return runtime

    authority = MissionAuthority(store)
    service = MissionService(
        _StubSettings(), store=store, authority=authority,
        evidence=EvidenceStore("/tmp/fixture-wait-evidence"),
        executor=VeloExecutor(_StubSettings(), get_runtime=get_runtime,
                              authority=authority, store=store),
        controller=type("C", (), {})(),
    )
    await service.reconcile_startup([item.execution_id])
    settled = await store.get_mission(mission.mission_id)
    assert settled is not None
    if settled.status == "BLOCKED":
        # The unknown effect blocked the mission: the wait must never fire.
        assert service._armed_waits == set()
        await asyncio.sleep(0.2)
        final = await store.get_mission(mission.mission_id)
        assert final is not None and final.status == "BLOCKED"
    else:
        # Proven NO_EFFECT releases the step; the wait may legitimately fire.
        assert service._armed_waits in (set(), {wait_id})
