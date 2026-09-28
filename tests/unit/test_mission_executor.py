"""VeloExecutor tests (T04, file 06 U3): bounded units, honest outcomes.

The executor never calls Deep, never escalates implicitly, and never turns
an UNKNOWN effect into a completed unit. All effects run through the
mission authority guard installed by ``work_scope``.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any

import pytest

from assistant.missions.authority import MissionAuthority
from assistant.missions.contracts import (
    ActionScopeRecord,
    BudgetLimits,
    CancellationToken,
    CheckSpec,
    EvidenceRequirements,
    Scope,
    new_id,
)
from assistant.missions.executor import VeloExecutor, exception_packet_for
from assistant.velo.contracts import (
    DecisionStatus,
    JevDecision,
)
from tests.unit.velo_fakes import (
    SHARED_APPS,
    FakeRuntime,
    app_entry,
    reset_world,
    standard_tools,
    window_state_payload,
)


def _now() -> int:
    return int(time.time() * 1000)


class _StubSettings:
    velo_jev_enabled = False
    velo_command_deadline_seconds = 90
    cua_artifact_dir = ""


class _RecordingJev:
    """Returns the scripted decision and records what it saw."""

    def __init__(self, decision: JevDecision) -> None:
        self.decision = decision
        self.requests: list[Any] = []

    async def decide(self, request: Any) -> JevDecision:
        self.requests.append(request)
        return self.decision


@pytest.fixture()
def world() -> Generator[None, None, None]:
    reset_world()
    yield
    reset_world()


def _safari_world(
    pid: int = 9, window_id: int = 2, *, extra_elements: list[dict[str, Any]] | None = None
) -> None:
    SHARED_APPS["list"] = [app_entry("Safari", pid=pid, running=True)]
    SHARED_APPS["windows"][pid] = [
        {"window_id": window_id, "is_on_screen": True, "bounds": {"height": 600, "width": 800}}
    ]
    elements = [
        {"role": "AXButton", "label": "fixture-mark-go", "element_token": "tok-go"},
        {"role": "AXTextField", "label": "Search", "value": "", "element_token": "tok-field"},
    ]
    if extra_elements:
        elements.extend(extra_elements)
    SHARED_APPS["state"][(pid, window_id)] = window_state_payload(
        elements, pid=pid, window_id=window_id
    )


def _scope() -> Scope:
    return Scope(owner_id="owner", allowed_apps=["com.example.safari"])


def _item(**overrides: Any) -> Any:
    from assistant.missions.contracts import BoundedWorkItem

    scope = _scope()
    fields: dict[str, Any] = {
        "mission_id": new_id(),
        "plan_version": 1,
        "control_epoch": 1,
        "step_id": "s1",
        "execution_id": new_id(),
        "attempt": 1,
        "objective": "fixture objective",
        "expected_scope": scope,
        "effect_class": "REPEATABLE_LOCAL",
        "allowed_action_scope": ActionScopeRecord(
            tool_ids=["list_apps", "list_windows", "get_window_state", "click",
                      "type_text", "set_value", "bring_to_front", "launch_app"],
            target_scope_hash=scope.scope_hash,
        ),
        "recipe_id": "semantic_ui",
        "deadline_at_ms": _now() + 90_000,
        "budget": BudgetLimits(max_wall_ms=90_000, max_actions=12),
        "evidence_requirements": EvidenceRequirements(max_age_ms=60_000),
    }
    fields.update(overrides)
    return BoundedWorkItem(**fields)


def _executor(
    tools: dict[str, Any],
    *,
    jev: Any = None,
    authority: MissionAuthority | None = None,
    evidence: Any = None,
) -> tuple[VeloExecutor, FakeRuntime]:
    runtime = FakeRuntime(tools)

    async def get_runtime() -> FakeRuntime:
        return runtime

    executor = VeloExecutor(
        _StubSettings(),
        get_runtime=get_runtime,
        jev_factory=(lambda: jev) if jev else None,
        authority=authority,
        evidence=evidence,
    )
    return executor, runtime


async def test_semantic_ui_completes_with_confirmed_effect(world: None) -> None:
    _safari_world()
    executor, runtime = _executor(standard_tools())
    result = await executor.execute_work_item(
        _item(objective="press fixture-mark-go"), cancel=CancellationToken()
    )
    assert result.status == "COMPLETED"
    assert result.effect_outcome == "CONFIRMED"
    clicks = [c for c in runtime.cua_tools["click"].calls]
    assert clicks and clicks[0]["element_token"] == "tok-go"
    assert runtime.cua_tools["launch_app"].calls == [], "a running target is not relaunched"


async def test_semantic_ui_no_candidates_escalates(world: None) -> None:
    _safari_world(extra_elements=[])
    # Remove the matching labels so no candidate matches.
    SHARED_APPS["state"][(9, 2)] = window_state_payload(
        [{"role": "AXStaticText", "label": "unrelated text", "element_token": "tok-x"}],
        pid=9,
        window_id=2,
    )
    executor, _runtime = _executor(standard_tools())
    result = await executor.execute_work_item(
        _item(objective="press fixture-mark-go"), cancel=CancellationToken()
    )
    assert result.status == "NEEDS_CONTROLLER"
    assert result.failure_category == "UNSUPPORTED_ACTION"


async def test_semantic_ui_sets_exact_payload(world: None) -> None:
    _safari_world()
    executor, runtime = _executor(standard_tools())
    payload = "fixture query text"
    item = _item(
        objective="type the query into Search",
        payload_refs=["user_text_1"],
        allowed_action_scope=_item().allowed_action_scope.model_copy(
            update={"payload_digests": [__import__("hashlib").sha256(payload.encode()).hexdigest()]}
        ),
    )
    executor._payload_resolver = lambda item, refs: {"user_text_1": payload}
    result = await executor.execute_work_item(item, cancel=CancellationToken())
    assert result.status == "COMPLETED"
    set_calls = runtime.cua_tools["set_value"].calls
    assert set_calls and set_calls[0]["value"] == payload, (
        "the exact payload is set, never a rewrite"
    )


async def test_jev_may_only_select_observed_candidate_ids(world: None) -> None:
    _safari_world()
    forged = _RecordingJev(
        JevDecision(status=DecisionStatus.ACT, selected_id="tok-invented-by-jev")
    )
    executor, _runtime = _executor(standard_tools(), jev=forged)
    result = await executor.execute_work_item(
        _item(objective="press fixture-mark-go"), cancel=CancellationToken()
    )
    assert result.status == "NEEDS_HUMAN", "a forged candidate id must not dispatch"
    assert len(forged.requests) == 1
    offered = {c.id for c in forged.requests[0].candidates}
    assert offered == {"tok-go"}, "JEV only ever sees mechanically built candidates"


async def test_unknown_recipe_is_needs_controller_with_bounded_exception(world: None) -> None:
    executor, _runtime = _executor(standard_tools())
    item = _item(recipe_id="execute_javascript", objective="run arbitrary code")
    result = await executor.execute_work_item(item, cancel=CancellationToken())
    assert result.status == "NEEDS_CONTROLLER"
    assert result.failure_category == "UNSUPPORTED_ACTION"
    packet = exception_packet_for(item, result)
    assert len(packet.model_dump_json().encode("utf-8")) <= 8 * 1024
    assert packet.allowed_decisions == ["REVISE", "ASK_OWNER", "BLOCK"]


async def test_failing_required_check_overrides_success(world: None, tmp_path: Any) -> None:
    """RF-02/TC-35: a claimed done with failing evidence is a FAILED unit."""
    _safari_world()
    from assistant.missions.evidence import EvidenceStore

    executor, _runtime = _executor(standard_tools(), evidence=EvidenceStore(tmp_path / "ev"))
    item = _item(
        recipe_id="open_app",
        objective="open safari",
        recipe_args={"app_name": "Safari"},
        expected_postconditions=[
            CheckSpec(
                check_id="marker-present",
                verifier_id="page_state",
                verifier_version="1.0.0",
                expected={"markers": ["definitively-absent-marker"]},
                required=True,
            )
        ],
        evidence_requirements=EvidenceRequirements(required_check_ids=["marker-present"]),
    )
    result = await executor.execute_work_item(item, cancel=CancellationToken())
    assert result.status == "FAILED"
    assert result.failure_category == "VERIFICATION_FAILED"
    assert result.postconditions and result.postconditions[0].passed is False


async def test_passing_required_check_keeps_completion(world: None, tmp_path: Any) -> None:
    _safari_world()
    from assistant.missions.evidence import EvidenceStore

    executor, _runtime = _executor(standard_tools(), evidence=EvidenceStore(tmp_path / "ev"))
    item = _item(
        recipe_id="open_app",
        objective="open safari",
        recipe_args={"app_name": "Safari"},
        expected_postconditions=[
            CheckSpec(
                check_id="marker-present",
                verifier_id="page_state",
                verifier_version="1.0.0",
                expected={"markers": ["fixture-mark-go"]},
                required=True,
            )
        ],
        evidence_requirements=EvidenceRequirements(required_check_ids=["marker-present"]),
    )
    result = await executor.execute_work_item(item, cancel=CancellationToken())
    assert result.status == "COMPLETED"
    assert result.postconditions[0].passed is True


async def test_cancelled_token_never_runs(world: None) -> None:
    _safari_world()
    executor, runtime = _executor(standard_tools())
    token = CancellationToken()
    token.cancel()
    result = await executor.execute_work_item(_item(), cancel=token)
    assert result.status == "CANCELLED"
    assert result.effect_outcome == "NOT_ATTEMPTED"
    assert all(len(tool.calls) == 0 for tool in runtime.cua_tools.values())


async def test_expired_deadline_fails_fast(world: None) -> None:
    _safari_world()
    executor, runtime = _executor(standard_tools())
    item = _item(deadline_at_ms=_now() - 1)
    result = await executor.execute_work_item(item, cancel=CancellationToken())
    assert result.status == "FAILED"
    assert result.failure_category == "DEADLINE"
    assert all(len(tool.calls) == 0 for tool in runtime.cua_tools.values())


async def test_executor_has_no_deep_reference() -> None:
    """Structural guard: the bounded executor cannot call the Deep Agent."""
    import inspect

    from assistant.missions import executor as executor_module

    source = inspect.getsource(executor_module)
    assert "deep_entry" not in source
    assert "DeepAgentEntry" not in source
    assert ".run(" not in source.replace("execute_work_item(", "").replace(".run(", ".run(", 1)


async def test_semantic_ui_jev_ask_user_escalates(world: None) -> None:
    _safari_world()
    jev = _RecordingJev(JevDecision(status=DecisionStatus.ASK_USER))
    executor, _runtime = _executor(standard_tools(), jev=jev)
    result = await executor.execute_work_item(
        _item(objective="press fixture-mark-go"), cancel=CancellationToken()
    )
    assert result.status == "NEEDS_HUMAN"


async def test_rp04_refused_click_never_completes() -> None:
    """RP04 regression: an explicitly refused click yields FAILED with
    UNKNOWN effect, zero postconditions, never COMPLETED/CONFIRMED."""
    _safari_world()

    class _RefusingRuntime(FakeRuntime):
        """The wrapped click refuses (driver-level error reply)."""

        async def ainvoke_bridge(self, *_a: Any, **_k: Any) -> None:
            return None

    runtime = FakeRuntime(standard_tools())
    # Make the fake click reply a refusal: respond returns ok=False text.
    from contextlib import contextmanager as _ctxmgr

    import assistant.velo.adapter as adapter_module
    from assistant.velo.contracts import ToolReply

    @_ctxmgr
    def _refusing_acting_call():
        original_call = adapter_module.CuaAdapter.acting_call

        async def refusing_call(self: Any, task: Any, tool_name: str, **kwargs: Any) -> Any:
            return ToolReply(tool=tool_name, text="Refused by driver", ok=False)

        adapter_module.CuaAdapter.acting_call = refusing_call  # type: ignore[method-assign]
        try:
            yield
        finally:
            adapter_module.CuaAdapter.acting_call = original_call  # type: ignore[method-assign]

    with _refusing_acting_call():

        async def get_runtime() -> FakeRuntime:
            return runtime

        executor = VeloExecutor(
            _StubSettings(), get_runtime=get_runtime, authority=None, evidence=None
        )
        result = await executor.execute_work_item(
            _item(objective="press fixture-mark-go"), cancel=CancellationToken()
        )
    assert result.status == "FAILED"
    assert result.effect_outcome == "UNKNOWN"
    assert result.failure_category == "PERMISSION_DENIED"
    assert result.postconditions == []


async def test_np09_zero_jev_budget_makes_no_call(world: None) -> None:
    """NP09 regression: max_jev_calls=0 means the JEV decision service is
    never invoked — the reservation gate refuses before any provider call."""
    from assistant.velo.contracts import DecisionStatus, JevDecision

    jev = _RecordingJev(JevDecision(status=DecisionStatus.ACT, selected_id="candidate"))
    executor, _ = _executor(standard_tools(), jev=jev)
    from assistant.missions.contracts import BudgetLimits

    item = _item(budget=BudgetLimits(max_jev_calls=0))
    candidates = [
        {"element_token": "candidate", "role": "AXButton", "label": "fixture", "purpose": "primary"}
    ]
    outcome = await executor._choose_candidate(item, candidates)
    assert len(jev.requests) == 0, "zero JEV budget must not reach the provider"
    assert outcome is None, "without a mechanical choice the unit escalates"


async def test_precondition_failure_blocks_with_zero_effects(world: None) -> None:
    """C05/N07: a packet precondition is evaluated BEFORE dispatch; a failed
    precondition blocks the unit and no action reaches the wrapped tool."""
    import tempfile
    from pathlib import Path

    from assistant.missions.contracts import CheckSpec
    from assistant.missions.evidence import EvidenceStore

    executor, runtime = _executor(
        standard_tools(), evidence=EvidenceStore(Path(tempfile.mkdtemp()))
    )
    item = _item()
    item = item.model_copy(
        update={
            "preconditions": [
                CheckSpec(
                    check_id="pre-mark",
                    verifier_id="page_state",
                    verifier_version="1.0.0",
                    expected={"markers": ["never-present-precondition"]},
                    required=True,
                )
            ]
        }
    )
    result = await executor.execute_work_item(item, cancel=CancellationToken())
    assert result.status == "BLOCKED"
    assert result.failure_category == "VERIFICATION_FAILED"
    assert result.effect_outcome == "NOT_ATTEMPTED"
    from assistant.tools.policy import MUTATING_TOOL_NAMES

    mutations = [
        tool.name
        for name, tool in runtime.cua_tools.items()
        if name in MUTATING_TOOL_NAMES and tool.calls
    ]
    assert mutations == [], "no mutating effect may follow a failed precondition"


async def test_superseded_desktop_lease_refuses_dispatch(world: None) -> None:
    """C06/N09: the guard reads CURRENT queue ownership at dispatch. When
    the recorded fence no longer matches (a stop or newer grant took the
    desktop), the dispatch refuses — a stored fence string is not authority."""

    import tempfile
    from pathlib import Path

    from assistant.missions.authority import MissionAuthority
    from assistant.missions.store import MissionStore

    store = await MissionStore.connect(Path(tempfile.mkdtemp()) / "fence.db")
    try:
        await store.setup()
        authority = MissionAuthority(store)
        fences: dict[str, str] = {}

        def probe(mission_id: str) -> tuple[str, int] | None:
            fence = fences.get(mission_id)
            return (fence, 1) if fence else None

        executor, _runtime = _executor(standard_tools(), authority=authority)
        executor._ownership_probe = probe
        from assistant.missions.contracts import (
            BudgetLimits,
            RequestEnvelope,
            Scope,
            new_id,
        )
        from tests.unit.test_mission_authority import _Intent

        request = RequestEnvelope(
            request_id=new_id(), conversation_id="c", owner_id="owner",
            input_origin="typed_final", input_revision=1, text="fixture",
            submitted_at_ms=0,
        )
        mission = await store.claim_request(
            request, "d" * 64, goal="fixture", scope=Scope(owner_id="owner"),
            limits=BudgetLimits(),
        )
        item = _item(mission_id=mission.mission_id, lease_fence="live-fence")
        fences[item.mission_id] = "live-fence"
        await _Intent(store).commit(item)
        ok = await executor._guard_dispatch(
            item, "click", {"pid": 4101, "window_id": 771}, CancellationToken()
        )
        # The lease is current: the guard proceeds past the fence check.
        assert ok is None or "STALE_TARGET: the desktop lease was superseded" not in ok
        # The stop supersedes the lease; the same packet must now refuse.
        fences[item.mission_id] = "newer-fence"
        refused = await executor._guard_dispatch(
            item, "click", {"pid": 4101, "window_id": 771}, CancellationToken()
        )
        assert refused is not None and "STALE_TARGET" in refused
        # No current owner at all: refuse too.
        fences.pop(item.mission_id)
        gone = await executor._guard_dispatch(
            item, "click", {"pid": 4101, "window_id": 771}, CancellationToken()
        )
        assert gone is not None and "STALE_TARGET" in gone
    finally:
        await store.close()
