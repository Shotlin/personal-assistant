"""Protected discovery and exact outcome verification (D03/D06).

Boundary regressions for the second corrective batch: unknown-origin
protected reads must never reach the data sink, and scroll/ordinal/type
recipes must owe exact, independent before/after evidence — a re-observed
window alone proves nothing.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from assistant.missions.authority import MissionAuthority
from assistant.missions.contracts import (
    BudgetLimits,
    CancellationToken,
    CheckSpec,
    EvidenceCandidate,
    RequestEnvelope,
    Scope,
    ScopeObservation,
    StepSpec,
    new_id,
)
from assistant.missions.evidence import EvidenceStore
from assistant.missions.store import MissionStore
from assistant.tools.policy import (
    apply_tool_policy,
    cua_run_scope,
    cua_target_state,
)


@pytest.fixture
async def store(tmp_path: Any) -> Any:
    value = await MissionStore.connect(tmp_path / "scope-outcomes.db")
    await value.setup()
    yield value
    await value.close()


def _scope(origins: list[str] | None = None) -> Scope:
    return Scope(owner_id="owner", allowed_apps=["com.fixture.safe"],
                 allowed_origins=list(origins or []),
                 permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"})


async def _claimed(store: MissionStore, scope: Scope, *, recipe: str = "semantic_ui",
                   effect: str = "REPEATABLE_LOCAL") -> Any:
    request = RequestEnvelope(request_id=new_id(), conversation_id="scope-outcomes",
                              owner_id="owner", input_origin="typed_final",
                              input_revision=1, text="fixture",
                              submitted_at_ms=int(time.time() * 1000))
    mission = await store.claim_request(request, "d" * 64, goal="fixture", scope=scope,
                                        limits=BudgetLimits())
    mission = await store.commit_plan(mission.mission_id, 0, [StepSpec(
        step_id="s1", ordinal=1, objective="fixture step", recipe_id=recipe,
        scope=scope, budget=BudgetLimits(), effect_class=effect)], [])
    item = await store.claim_step(mission.mission_id, 1, 1, tool_ids=["get_window_state"],
                                  lease_fence="fence-1", driver_generation="gen-1")
    return mission, item


def _action(tool: str = "get_window_state", effect: str = "READ_ONLY") -> Any:
    from assistant.missions.contracts import ActionIntent

    return ActionIntent(tool=tool, args={"pid": 9, "window_id": 2}, effect_class=effect)


# -- D03: configured origins bind content reads too -------------------------------


async def test_unknown_origin_cannot_read_protected_content(store: MissionStore) -> None:
    """A READ_ONLY content read with an unknown origin is refused when the
    mission scope configures allowed origins — reading is not a wildcard."""
    from assistant.missions.authority import AuthorityDenied

    scope = _scope(origins=["https://trusted.example"])
    _mission, item = await _claimed(store, scope)
    authority = MissionAuthority(store)
    with pytest.raises(AuthorityDenied) as denied:
        authority._check_surface(item, scope, _action(), ScopeObservation(
            app_bundle="com.fixture.safe", pid=9, window_id=2,
            captured_at_ms=int(time.time() * 1000)))
    assert "origin" in str(denied.value).lower()


async def test_trusted_origin_bootstrap_positive(store: MissionStore) -> None:
    """Minimal inventory resolves without an origin; a content read on an
    observed, in-scope origin passes through the production check."""
    scope = _scope(origins=["https://trusted.example"])
    _mission, item = await _claimed(store, scope)
    authority = MissionAuthority(store)
    now = int(time.time() * 1000)
    # Step 1: trusted minimal inventory (no origin needed).
    authority._check_surface(item, scope, _action("list_apps"), ScopeObservation(
        app_bundle="", pid=None, window_id=None, captured_at_ms=now))
    # Step 2: content read on the trusted origin.
    authority._check_surface(item, scope, _action(), ScopeObservation(
        app_bundle="com.fixture.safe", pid=9, window_id=2,
        origin="https://trusted.example", captured_at_ms=now))


async def test_real_wrapper_blocks_unknown_origin_read_before_sink(
    store: MissionStore, tmp_path: Any
) -> None:
    """Production wrapper regression: with configured allowed_origins, an
    unknown-origin protected read never reaches the data sink."""
    from assistant.missions.executor import VeloExecutor

    scope = _scope(origins=["https://trusted.example"])
    mission, item = await _claimed(store, scope)
    reads: list[dict[str, Any]] = []

    async def get_window_state(pid: int, window_id: int, include_screenshot: bool = False,
                               max_elements: int = 120, max_depth: int = 12) -> str:
        """The data sink a protected read would hit."""
        reads.append({"pid": pid, "window_id": window_id})
        return json.dumps({"pid": pid, "window_id": window_id,
                           "snapshot_id": "s", "elements": []})

    wrapped, _ = apply_tool_policy([
        StructuredTool.from_function(coroutine=get_window_state, name="get_window_state"),
    ])
    from tests.unit.velo_fakes import FakeRuntime

    runtime = FakeRuntime({t.name: t for t in wrapped})

    async def get_runtime() -> FakeRuntime:
        return runtime

    executor = VeloExecutor(type("S", (), {})(), get_runtime=get_runtime,
                            authority=MissionAuthority(store), store=store)
    cancel = CancellationToken()

    async def guard(name: str, args: dict[str, Any]) -> str | None:
        return await executor._guard_dispatch(item, name, args, cancel)

    async with cua_run_scope(budget=None, run=None, ledger=None, mission_guard=guard):
        state = cua_target_state.get()
        assert state is not None
        state["apps"] = {9: "com.fixture.safe"}
        state["observed_at_ms"] = int(time.time() * 1000)
        # No origin has been recorded for this surface: the driver never
        # reported one. The wrapped read must refuse BEFORE the sink.
        reply = await wrapped[0].ainvoke({"pid": 9, "window_id": 2})
    assert reads == [], "a protected read must never reach the data sink"
    assert "refused" in str(reply).lower()
    _ = mission


# -- D06: exact outcome verifiers --------------------------------------------------


def _check(verifier: str, expected: dict[str, Any]) -> CheckSpec:
    return CheckSpec(check_id="outcome", verifier_id=verifier,
                     verifier_version="1.0.0", expected=expected, required=True)


async def test_scroll_recheck_alone_is_not_success(store: MissionStore, tmp_path: Any) -> None:
    """A re-observed window (the old window_visible {}) cannot verify a
    scroll: the offset must move by the required delta."""
    evidence = EvidenceStore(tmp_path / "ev", store)
    scope = _scope()
    mission, item = await _claimed(store, scope)
    now = int(time.time() * 1000)
    window = {"pid": 9, "window_id": 2}
    before = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={**window, "scroll_offset": 0}, captured_at_ms=now - 500),
        mission_id=mission.mission_id)
    # The screen was re-observed but the offset did not move (no-op scroll).
    after = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={**window, "scroll_offset": 0}, captured_at_ms=now),
        mission_id=mission.mission_id)
    result = await evidence.verify(_check("scroll_effect", {"direction": "down",
        "min_delta": 3}), item, refs=[before, after])
    assert not result.passed, "an unchanged offset is a no-op, not a scroll"
    # A real delta passes.
    moved = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={**window, "scroll_offset": 40}, captured_at_ms=now),
        mission_id=mission.mission_id)
    ok = await evidence.verify(_check("scroll_effect", {"direction": "down",
        "min_delta": 3}), item, refs=[before, moved])
    assert ok.passed


async def test_scroll_wrong_direction_or_window_fails(store: MissionStore, tmp_path: Any) -> None:
    evidence = EvidenceStore(tmp_path / "ev", store)
    scope = _scope()
    mission, item = await _claimed(store, scope)
    now = int(time.time() * 1000)
    before = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 2, "scroll_offset": 50}, captured_at_ms=now - 500),
        mission_id=mission.mission_id)
    after = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 2, "scroll_offset": 90}, captured_at_ms=now),
        mission_id=mission.mission_id)
    # Scrolled DOWN; the step promised UP.
    wrong_dir = await evidence.verify(_check("scroll_effect", {"direction": "up",
        "min_delta": 3}), item, refs=[before, after])
    assert not wrong_dir.passed
    # After-observation of a DIFFERENT window is not bound evidence.
    other = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 5, "scroll_offset": 10}, captured_at_ms=now),
        mission_id=mission.mission_id)
    wrong_window = await evidence.verify(_check("scroll_effect", {"direction": "down",
        "min_delta": 3}), item, refs=[before, other])
    assert not wrong_window.passed


async def test_ordinal_press_without_state_change_is_not_success(
    store: MissionStore, tmp_path: Any
) -> None:
    """press_ordinal owes an activation-state flip in the SAME window;
    a refused/no-op press must not complete."""
    evidence = EvidenceStore(tmp_path / "ev", store)
    scope = _scope()
    mission, item = await _claimed(store, scope)
    now = int(time.time() * 1000)
    element = {"element_token": "tok-third", "role": "AXLink", "label": "third",
               "focused": False}
    before = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 2, "elements": [element]}, captured_at_ms=now - 500),
        mission_id=mission.mission_id)
    # No-op: the control did not gain focus/checked/selected.
    after_noop = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 2, "elements": [element]}, captured_at_ms=now),
        mission_id=mission.mission_id)
    noop = await evidence.verify(_check("press_effect", {"ordinal": "third"}), item,
        refs=[before, after_noop])
    assert not noop.passed, "a refused or no-op press is not a completion"
    # The control gained focus: an exact, independent positive.
    activated = dict(element, focused=True)
    after_ok = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 2, "elements": [activated]}, captured_at_ms=now),
        mission_id=mission.mission_id)
    ok = await evidence.verify(_check("press_effect", {"ordinal": "third"}), item,
        refs=[before, after_ok])
    assert ok.passed


async def test_missing_driver_evidence_blocks_honestly(store: MissionStore,
                                                       tmp_path: Any) -> None:
    """A verifier the evidence cannot satisfy blocks; it never passes."""
    evidence = EvidenceStore(tmp_path / "ev", store)
    scope = _scope()
    mission, item = await _claimed(store, scope)
    now = int(time.time() * 1000)
    # The driver reports no scroll_offset at all.
    before = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 2}, captured_at_ms=now - 500),
        mission_id=mission.mission_id)
    after = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 2}, captured_at_ms=now),
        mission_id=mission.mission_id)
    result = await evidence.verify(_check("scroll_effect", {"direction": "down",
        "min_delta": 1}), item, refs=[before, after])
    assert not result.passed
    assert "offset" in result.reason.lower() or "driver" in result.reason.lower()


async def test_field_value_binds_the_focused_field(store: MissionStore,
                                                   tmp_path: Any) -> None:
    """The payload in a DIFFERENT field than the focused one is not success."""
    evidence = EvidenceStore(tmp_path / "ev", store)
    scope = _scope()
    mission, item = await _claimed(store, scope)
    now = int(time.time() * 1000)
    focused = {"element_token": "tok-body", "role": "AXTextField",
               "label": "body", "focused": True}
    before = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 2, "elements": [focused]}, captured_at_ms=now - 500),
        mission_id=mission.mission_id)
    # The same VALUE landed in a different field of the same window.
    wrong_field = {"element_token": "tok-other", "role": "AXTextField",
                   "label": "search", "value": "gentle hello", "focused": False}
    after_wrong = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 2, "elements": [wrong_field],
                 "resolved_payloads": {"user_text_1": "gentle hello"}},
        captured_at_ms=now), mission_id=mission.mission_id)
    result = await evidence.verify(CheckSpec(check_id="field", verifier_id="field_value",
        verifier_version="1.0.0", expected={"payload_ref": "user_text_1"}, required=True),
        item, refs=[before, after_wrong])
    assert not result.passed, "the payload in an unfocused field is not the typed field"
    # The focused field itself carries it: positive.
    right_field = dict(focused, value="gentle hello")
    after_ok = await evidence.put(EvidenceCandidate(kind="structured_facts",
        payload={"pid": 9, "window_id": 2, "elements": [right_field],
                 "resolved_payloads": {"user_text_1": "gentle hello"}},
        captured_at_ms=now), mission_id=mission.mission_id)
    ok = await evidence.verify(CheckSpec(check_id="field", verifier_id="field_value",
        verifier_version="1.0.0", expected={"payload_ref": "user_text_1"}, required=True),
        item, refs=[before, after_ok])
    assert ok.passed


def test_fast_scroll_and_ordinal_owe_exact_checks() -> None:
    """The fast recipes no longer ship the weak window_visible {} check."""
    from assistant.missions.service import _fast_checks_for

    scroll_args: dict[str, str] = {"direction": "down", "amount": "4"}
    scroll = _fast_checks_for("scroll", scroll_args)
    assert scroll and scroll[0].verifier_id == "scroll_effect"
    assert scroll[0].expected == {"direction": "down", "min_delta": 4}
    _ = scroll[0].expected  # dict[str, Any] expectation, checked above
    press = _fast_checks_for("press_ordinal", {"ordinal": "second"})
    assert press and press[0].verifier_id == "press_effect"
