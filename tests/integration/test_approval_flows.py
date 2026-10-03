"""Approval recovery and the owner decision path (D13/D20).

D13: a blocked approval step holds the mission at NEEDS_APPROVAL even with
dependent PENDING steps; pause re-queues the never-dispatched step so it
re-observes and earns a FRESH obligation; the projection carries the exact
target. D20: the complete owner path — renderer-shaped mission.approve +
RESUME over the host IPC handlers, through the service and store — drives
one dependent multi-step mission to a single approved effect.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from assistant.missions.authority import MissionAuthority
from assistant.missions.contracts import (
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
from assistant.missions.controller import DeepController
from assistant.missions.evidence import EvidenceStore
from assistant.missions.executor import VeloExecutor
from assistant.missions.service import MissionService
from assistant.missions.store import MissionStore
from assistant.tools.policy import apply_tool_policy, cua_target_state
from assistant.tools.result_normalizer import ToolOutcome


@pytest.fixture
async def store(tmp_path: Any) -> Any:
    value = await MissionStore.connect(tmp_path / "approval-flows.db")
    await value.setup()
    yield value
    await value.close()


def _world() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import sys

    sys.path.insert(0, ".")
    from tests.unit.velo_fakes import (
        SHARED_APPS,
        app_entry,
        reset_world,
        window_state_payload,
    )

    reset_world()
    SHARED_APPS["list"] = [app_entry("Fixture", pid=9, running=True, active=True,
                                     bundle_id="com.example.fixture")]
    SHARED_APPS["windows"][9] = [
        {"window_id": 2, "is_on_screen": True, "bounds": {"height": 600, "width": 800}}
    ]
    SHARED_APPS["state"][(9, 2)] = window_state_payload(
        [{"role": "AXButton", "label": "fixture-mark-go", "element_token": "tok-go"}],
        pid=9, window_id=2,
    )
    clicks: list[dict[str, Any]] = []

    def _outcome(text: str, structured: dict[str, Any] | None = None) -> ToolOutcome:
        return ToolOutcome(status="ok", effect="confirmed", text=text,
                           structured=structured or {})

    async def list_apps(**_: Any) -> ToolOutcome:
        """inventory"""
        return _outcome("Listed apps.", {"apps": SHARED_APPS["list"]})

    async def list_windows(pid: int, **_: Any) -> ToolOutcome:
        """windows"""
        return _outcome("Listed windows.", {"windows": SHARED_APPS["windows"].get(pid, [])})

    async def get_window_state(pid: int, window_id: int, include_screenshot: bool = False,
                               max_elements: int = 120, max_depth: int = 12) -> ToolOutcome:
        """snapshot"""
        state = cua_target_state.get()
        if state is not None:
            state["observed_at_ms"] = int(time.time() * 1000)
            state.setdefault("apps", {})[pid] = "com.example.fixture"
            state["focus"] = (pid, window_id)
        return _outcome("Read the window.", SHARED_APPS["state"].get((pid, window_id), {}))

    async def click(pid: int, window_id: int, element_token: str, **_: Any) -> ToolOutcome:
        """effect"""
        clicks.append({"pid": pid, "element_token": element_token})
        return _outcome("clicked")

    async def bring_to_front(pid: int, **_: Any) -> ToolOutcome:
        """foreground"""
        return _outcome("Brought to front.")

    wrapped, _ = apply_tool_policy([
        StructuredTool.from_function(coroutine=list_apps, name="list_apps"),
        StructuredTool.from_function(coroutine=list_windows, name="list_windows"),
        StructuredTool.from_function(coroutine=get_window_state, name="get_window_state"),
        StructuredTool.from_function(coroutine=click, name="click"),
        StructuredTool.from_function(coroutine=bring_to_front, name="bring_to_front"),
    ])
    tools = {t.name: t for t in wrapped}
    return tools, clicks


def _plan_scope() -> Scope:
    return Scope(owner_id="sani-local", allowed_apps=["com.example.fixture"],
                 permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL", "EXTERNAL_WRITE"})


def _two_step_plan() -> dict[str, Any]:
    scope = _plan_scope()
    return {"steps": [
        {
            "step_id": "s1", "ordinal": 1,
            "objective": "click the fixture go button",
            "recipe_id": "semantic_ui", "recipe_args": {},
            "scope": scope.model_dump(mode="json"),
            "budget": {"max_actions": 4},
            "effect_class": "EXTERNAL_WRITE",
            "checks": [{"check_id": "fg", "verifier_id": "app_foreground",
                        "verifier_version": "1.0.0",
                        "expected": {"app": "fixture"}, "required": True}],
        },
        {
            "step_id": "s2", "ordinal": 2,
            "objective": "bring the fixture app to the front",
            "recipe_id": "open_app", "recipe_args": {"app_name": "fixture"},
            "scope": scope.model_dump(mode="json"),
            "budget": {"max_actions": 4},
            "effect_class": "REPEATABLE_LOCAL",
            "checks": [{"check_id": "fg2", "verifier_id": "app_foreground",
                        "verifier_version": "1.0.0",
                        "expected": {"app": "fixture"}, "required": True}],
        },
    ], "success_criteria": [], "explanation": "scripted two-step plan"}


def _service(store: MissionStore, tmp_path: Any, transport: Any) -> Any:
    from tests.unit.test_mission_service import _StubSettings
    from tests.unit.velo_fakes import FakeRuntime

    tools, clicks = _world()
    runtime = FakeRuntime(tools)

    async def get_runtime() -> FakeRuntime:
        return runtime

    settings = _StubSettings()
    authority = MissionAuthority(store)
    evidence = EvidenceStore(tmp_path / "ev", store)
    executor = VeloExecutor(settings, get_runtime=get_runtime, authority=authority,
                            evidence=evidence, store=store)

    class _Svc(MissionService):
        def _scope_for(self, request: Any) -> Scope:
            return _plan_scope()

    service = _Svc(settings, store=store, authority=authority, evidence=evidence,
                   executor=executor, controller=DeepController(None))
    service._transport_factory = lambda record: transport
    return service, clicks


def _transport() -> Any:
    async def transport(role: str, tool: str, payload: Any) -> dict[str, Any]:
        if role == "PLAN":
            return {"plan": _two_step_plan()}
        if role == "RECOVER":
            return {"recovery": {"decision": "BLOCK", "reason": "scripted"}}
        return {"review": {"supported_summary": "", "acceptance_check_ids": [],
                           "unresolved_issues": []}}
    return transport


def _request(text: str) -> RequestEnvelope:
    return RequestEnvelope(request_id=new_id(), conversation_id="approval-flows",
                           owner_id="sani-local", input_origin="typed_final",
                           input_revision=1, text=text,
                           submitted_at_ms=int(time.time() * 1000))


async def _wait_terminal(store: MissionStore, mission_id: str,
                         statuses: set[str], *, seconds: float = 12.0) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        record = await store.get_mission(mission_id)
        assert record is not None
        if record.status in statuses:
            return record
        await asyncio.sleep(0.05)
    return record


# -- D13: multi-step block → owner decision → single approved effect --------------


async def test_multi_step_block_owner_approve_resume_single_effect(
    store: MissionStore, tmp_path: Any
) -> None:
    service, clicks = _service(store, tmp_path, _transport())
    mission = await service.submit(_request("click the fixture go button"),
                                   cancel=CancellationToken(), invoke=_transport())
    try:
        # D13: the blocked FIRST step holds the mission even though s2 is
        # still PENDING — the owner's decision path is always available.
        assert mission.status == "NEEDS_APPROVAL", mission.status
        assert clicks == [], "no effect before the owner approves"
        assert mission.pending_approvals, "the blocked step owes an approval"
        pending = mission.pending_approvals[0]
        assert pending.step_id == "s1"

        # D20: the renderer-shaped host IPC path (mission.approve + RESUME).

        from assistant.core.app import SaniCoreApp
        from assistant.core.protocol import Request as IpcRequest

        app = SaniCoreApp.__new__(SaniCoreApp)
        app._mission_provider = lambda: _service_of(service)

        async def _service_of(svc: Any) -> Any:
            return svc

        class _Session:
            def __init__(self) -> None:
                self.frames: list[Any] = []

            async def send(self, frame: Any) -> None:
                self.frames.append(frame)

        session = _Session()
        await app._handle_mission_approve(session, IpcRequest(  # type: ignore[arg-type]
            id="a1", method="mission.approve", params={
                "approval_id": new_id(),
                "mission_id": mission.mission_id,
                "plan_version": pending.plan_version,
                "control_epoch": pending.control_epoch,
                "action_digest": pending.action_digest,
                "scope_hash": _plan_scope().scope_hash,
                "effect_class": "EXTERNAL_WRITE",
                "issued_at_ms": int(time.time() * 1000),
                "expires_at_ms": int(time.time() * 1000) + 300_000,
            }))
        assert session.frames[0]["ok"] is True, session.frames
        await app._handle_mission_control(session, IpcRequest(  # type: ignore[arg-type]
            id="c1", method="mission.control", params={
                "control_id": new_id(),
                "mission_id": mission.mission_id,
                "expected_plan_version": pending.plan_version,
                "expected_control_epoch": pending.control_epoch,
                "kind": "RESUME",
            }))
        assert session.frames[1]["ok"] is True, session.frames

        final = await _wait_terminal(store, mission.mission_id,
                                     {"COMPLETED", "FAILED", "BLOCKED",
                                      "NEEDS_APPROVAL"})
        assert final.status == "COMPLETED", final.status
        # The approved action dispatched exactly once; s2 ran after it.
        assert len(clicks) == 1, f"expected the single approved effect, got {clicks}"
    finally:
        from tests.unit.velo_fakes import reset_world

        reset_world()


# -- D13: pause earns a FRESH obligation, never a stranded step --------------------


async def test_pause_requeues_approval_step_and_earns_fresh_obligation(
    store: MissionStore, tmp_path: Any
) -> None:
    service, clicks = _service(store, tmp_path, _transport())
    mission = await service.submit(_request("click the fixture go button"),
                                   cancel=CancellationToken(), invoke=_transport())
    try:
        assert mission.status == "NEEDS_APPROVAL"
        old_digest = mission.pending_approvals[0].action_digest
        # The owner pauses instead of deciding.
        await service.control(MissionControl(
            control_id=new_id(), mission_id=mission.mission_id,
            expected_plan_version=1, expected_control_epoch=1, kind="PAUSE"))
        states = await store.get_step_states(mission.mission_id, 1)
        assert states["s1"] == "PENDING", (
            "the never-dispatched approval step re-observes instead of stranding")
        await service.control(MissionControl(
            control_id=new_id(), mission_id=mission.mission_id,
            expected_plan_version=1, expected_control_epoch=2, kind="RESUME"))
        resumed = await _wait_terminal(store, mission.mission_id,
                                       {"NEEDS_APPROVAL", "COMPLETED"})
        assert resumed.status == "NEEDS_APPROVAL", (
            "the fresh dispatch is refused and earns a NEW obligation")
        fresh = resumed.pending_approvals[0]
        assert fresh.action_digest != old_digest, (
            "the new epoch's obligation is a different digest")
        # Approve the fresh obligation; exactly one effect ever dispatched.
        now = int(time.time() * 1000)
        from assistant.missions.contracts import ApprovalRecord

        await service.issue_approval(ApprovalRecord(
            approval_id=new_id(), mission_id=mission.mission_id,
            plan_version=fresh.plan_version, control_epoch=fresh.control_epoch,
            action_digest=fresh.action_digest, scope_hash=_plan_scope().scope_hash,
            effect_class="EXTERNAL_WRITE", issued_by="local_owner",
            issued_at_ms=now, expires_at_ms=now + 300_000))
        await service.control(MissionControl(
            control_id=new_id(), mission_id=mission.mission_id,
            expected_plan_version=fresh.plan_version,
            expected_control_epoch=fresh.control_epoch, kind="RESUME"))
        final = await _wait_terminal(store, mission.mission_id, {"COMPLETED"})
        assert final.status == "COMPLETED"
        assert len(clicks) == 1, "the approved action dispatched exactly once"
    finally:
        from tests.unit.velo_fakes import reset_world

        reset_world()


# -- D13: the projection carries the exact persisted target ------------------------


async def test_pending_projection_carries_target(store: MissionStore) -> None:
    scope = Scope(owner_id="owner", allowed_apps=["com.fixture.safe"],
                  account_ref="acct-fixture", workspace_ref="workspace-fixture",
                  permitted_effects={"READ_ONLY", "EXTERNAL_WRITE"})
    request = RequestEnvelope(request_id=new_id(), conversation_id="t",
                              owner_id="owner", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(request, "d" * 64, goal="fixture",
                                        scope=scope, limits=BudgetLimits())
    mission = await store.commit_plan(mission.mission_id, 0, [StepSpec(
        step_id="s1", ordinal=1, objective="fixture", recipe_id="semantic_ui",
        scope=scope, budget=BudgetLimits(), effect_class="EXTERNAL_WRITE")], [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    assert item is not None
    await store.apply_result(StepResult(
        mission_id=mission.mission_id, plan_version=1, control_epoch=1,
        step_id="s1", execution_id=item.execution_id, attempt=1,
        status="BLOCKED", effect_outcome="NOT_ATTEMPTED",
        failure_category="APPROVAL_REQUIRED",
        pending_approval=PendingApprovalDigest(
            step_id="s1", tool="set_value", action_digest="a" * 64,
            plan_version=1, control_epoch=1, target_ref="win-7")))
    projected = await store.get_mission(mission.mission_id)
    assert projected is not None
    assert projected.status == "NEEDS_APPROVAL"
    assert projected.pending_approvals[0].target_ref == "win-7", (
        "the owner sees the exact target, never a null")
    assert projected.pending_approvals[0].account_ref == "acct-fixture"
    assert projected.pending_approvals[0].workspace_ref == "workspace-fixture"
    assert projected.pending_approvals[0].effect_class == "EXTERNAL_WRITE"


# -- D13/D20: wrong target / expired / duplicate approvals never release -----------


async def test_wrong_target_expired_and_duplicate_approvals_refused(
    store: MissionStore,
) -> None:
    from assistant.missions.contracts import ApprovalRecord

    scope = Scope(owner_id="owner", allowed_apps=["com.fixture.safe"],
                  permitted_effects={"READ_ONLY", "EXTERNAL_WRITE"})
    request = RequestEnvelope(request_id=new_id(), conversation_id="t",
                              owner_id="owner", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(request, "d" * 64, goal="fixture",
                                        scope=scope, limits=BudgetLimits())
    mission = await store.commit_plan(mission.mission_id, 0, [StepSpec(
        step_id="s1", ordinal=1, objective="fixture", recipe_id="semantic_ui",
        scope=scope, budget=BudgetLimits(), effect_class="EXTERNAL_WRITE")], [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["click"])
    assert item is not None
    await store.apply_result(StepResult(
        mission_id=mission.mission_id, plan_version=1, control_epoch=1,
        step_id="s1", execution_id=item.execution_id, attempt=1,
        status="BLOCKED", effect_outcome="NOT_ATTEMPTED",
        failure_category="APPROVAL_REQUIRED",
        pending_approval=PendingApprovalDigest(
            step_id="s1", tool="click", action_digest="b" * 64,
            plan_version=1, control_epoch=1, target_ref="win-7")))
    now = int(time.time() * 1000)
    # Wrong target: never releases.
    await store.record_approval(ApprovalRecord(
        approval_id=new_id(), mission_id=mission.mission_id, plan_version=1,
        control_epoch=1, action_digest="b" * 64, scope_hash=scope.scope_hash,
        target_ref="win-8", effect_class="EXTERNAL_WRITE", issued_by="local_owner",
        issued_at_ms=now, expires_at_ms=now + 300_000))
    assert await store.release_approved_blocked_steps(mission.mission_id, 1) == 0
    # Expired: never releases.
    await store.record_approval(ApprovalRecord(
        approval_id=new_id(), mission_id=mission.mission_id, plan_version=1,
        control_epoch=1, action_digest="b" * 64, scope_hash=scope.scope_hash,
        target_ref="win-7", effect_class="EXTERNAL_WRITE", issued_by="local_owner",
        issued_at_ms=now - 400_000, expires_at_ms=now - 100_000))
    assert await store.release_approved_blocked_steps(mission.mission_id, 1) == 0
    # Exact: releases; duplicate/consumed: never releases again.
    right = ApprovalRecord(
        approval_id=new_id(), mission_id=mission.mission_id, plan_version=1,
        control_epoch=1, action_digest="b" * 64, scope_hash=scope.scope_hash,
        target_ref="win-7", effect_class="EXTERNAL_WRITE", issued_by="local_owner",
        issued_at_ms=now, expires_at_ms=now + 300_000)
    await store.record_approval(right)
    assert await store.release_approved_blocked_steps(mission.mission_id, 1) == 1
    assert await store.release_approved_blocked_steps(mission.mission_id, 1) == 0, (
        "a consumed approval cannot release the step twice")
    # Rejection path: cancel stays honest after a block.
    cancelled = await store.control(MissionControl(
        control_id=new_id(), mission_id=mission.mission_id,
        expected_plan_version=1, expected_control_epoch=1, kind="CANCEL"))
    assert cancelled.status == "CANCELLED"
