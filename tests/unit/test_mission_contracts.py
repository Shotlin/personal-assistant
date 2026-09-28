"""Contract tests for the jarvis.v1 types (T02, file 06 U1).

Strict validation, packet bounds, DAG rules, and honest outcomes: an
UNKNOWN effect can never be reported as a completed unit.
"""

from __future__ import annotations

import time

import pydantic
import pytest

from assistant.missions.contracts import (
    ApprovalRecord,
    BoundedWorkItem,
    BudgetLimits,
    BudgetUsage,
    CheckSpec,
    MissionRecord,
    ObserverRecommendation,
    RequestEnvelope,
    Scope,
    StepResult,
    StepSpec,
    TraceEvent,
    canonical_json,
    digest_of,
    is_uuid,
    new_id,
    text_digest,
)


def _now() -> int:
    return int(time.time() * 1000)


def _scope(**overrides: object) -> Scope:
    fields: dict[str, object] = {"owner_id": "owner-fixture"}
    fields.update(overrides)
    return Scope(**fields)  # type: ignore[arg-type]


def _step(**overrides: object) -> StepSpec:
    fields: dict[str, object] = {
        "step_id": "s1",
        "ordinal": 1,
        "objective": "fixture objective",
        "recipe_id": "open_app",
        "scope": _scope(),
        "budget": BudgetLimits(),
        "effect_class": "READ_ONLY",
    }
    fields.update(overrides)
    return StepSpec(**fields)  # type: ignore[arg-type]


def _mission(**overrides: object) -> MissionRecord:
    now = _now()
    fields: dict[str, object] = {
        "mission_id": new_id(),
        "request_id": new_id(),
        "owner_id": "owner-fixture",
        "conversation_id": "conv-fixture",
        "original_goal": "fixture goal",
        "scope": _scope(),
        "steps": [_step()],
        "created_at_ms": now,
        "updated_at_ms": now,
    }
    fields.update(overrides)
    return MissionRecord(**fields)  # type: ignore[arg-type]


# -- identity and envelope ------------------------------------------------------


def test_new_ids_are_uuids() -> None:
    value = new_id()
    assert is_uuid(value)


def test_request_envelope_bounds_text() -> None:
    with pytest.raises(pydantic.ValidationError):
        RequestEnvelope(
            request_id=new_id(),
            conversation_id="c",
            owner_id="o",
            input_origin="typed_final",
            input_revision=1,
            text="x" * 16001,
            submitted_at_ms=_now(),
        )
    with pytest.raises(pydantic.ValidationError):
        RequestEnvelope(
            request_id=new_id(),
            conversation_id="c",
            owner_id="o",
            input_origin="partial",  # type: ignore[arg-type]
            input_revision=1,
            text="ok",
            submitted_at_ms=_now(),
        )


def test_authority_records_reject_unknown_fields() -> None:
    with pytest.raises(pydantic.ValidationError):
        Scope(owner_id="o", admin_bypass=True)  # type: ignore[call-arg]
    with pytest.raises(pydantic.ValidationError):
        ApprovalRecord(
            approval_id=new_id(),
            mission_id=new_id(),
            plan_version=1,
            control_epoch=1,
            action_digest="a" * 32,
            scope_hash="s",
            effect_class="READ_ONLY",
            issued_at_ms=_now(),
            expires_at_ms=_now() + 1,
            granted_by_model=True,  # type: ignore[call-arg]
        )


# -- scope ------------------------------------------------------------------------


def test_scope_hash_is_stable_and_content_bound() -> None:
    one = _scope(allowed_apps=["com.apple.Safari"])
    two = _scope(allowed_apps=["com.apple.Safari"])
    three = _scope(allowed_apps=["com.apple.Terminal"])
    assert one.scope_hash == two.scope_hash
    assert one.scope_hash != three.scope_hash


def test_scope_permits_effect_rules() -> None:
    scope = _scope(permitted_effects={"READ_ONLY", "EXTERNAL_WRITE"})
    assert scope.permits_effect("READ_ONLY")
    assert scope.permits_effect("EXTERNAL_WRITE")
    # Destructive effects are denied in Phase 1 regardless of scope.
    assert not scope.permits_effect("DESTRUCTIVE")
    assert not _scope().permits_effect("EXTERNAL_WRITE")


# -- budgets ------------------------------------------------------------------------


def test_budget_defaults_deny_paid_units() -> None:
    limits = BudgetLimits()
    assert limits.max_paid_units == 0
    usage = BudgetUsage()
    assert usage.within(limits, "paid_units") is False or usage.total("paid_units") == 0


def test_budget_usage_counts_reservation_plus_consumption() -> None:
    limits = BudgetLimits(max_actions=3)
    usage = BudgetUsage(reserved={"actions": 2}, consumed={"actions": 1})
    assert usage.total("actions") == 3
    assert not usage.within(limits, "actions")


def test_cost_ceiling_requires_currency() -> None:
    with pytest.raises(pydantic.ValidationError):
        BudgetLimits(max_cost_microunits=100)


# -- plans and DAG ------------------------------------------------------------------


def test_mission_rejects_duplicate_and_unknown_dependencies() -> None:
    with pytest.raises(pydantic.ValidationError):
        _mission(steps=[_step(), _step()])
    with pytest.raises(pydantic.ValidationError):
        _mission(steps=[_step(dependencies=["ghost"])])
    with pytest.raises(pydantic.ValidationError):
        _mission(steps=[_step(dependencies=["s1"])])


def test_mission_rejects_dependency_cycles() -> None:
    with pytest.raises(pydantic.ValidationError):
        _mission(
            steps=[
                _step(step_id="a", dependencies=["b"]),
                _step(step_id="b", dependencies=["a"]),
            ]
        )


def test_mission_caps_twenty_steps() -> None:
    steps = [
        _step(step_id=f"s{i}", ordinal=i + 1) for i in range(21)
    ]
    with pytest.raises(pydantic.ValidationError):
        _mission(steps=steps)


# -- bounded packet -------------------------------------------------------------


def _work_item(**overrides: object) -> BoundedWorkItem:
    from assistant.missions.contracts import ActionScopeRecord, EvidenceRequirements

    fields: dict[str, object] = {
        "mission_id": new_id(),
        "plan_version": 1,
        "control_epoch": 1,
        "step_id": "s1",
        "execution_id": new_id(),
        "attempt": 1,
        "objective": "fixture",
        "expected_scope": _scope(),
        "allowed_action_scope": ActionScopeRecord(
            tool_ids=["list_apps"], target_scope_hash="x" * 64
        ),
        "recipe_id": "open_app",
        "deadline_at_ms": _now() + 90_000,
        "budget": BudgetLimits(),
        "evidence_requirements": EvidenceRequirements(),
    }
    fields.update(overrides)
    return BoundedWorkItem(**fields)  # type: ignore[arg-type]


def test_work_item_size_cap() -> None:
    item = _work_item(minimal_context="x" * 4096)
    assert item.serialized_size() <= 16 * 1024
    with pytest.raises(pydantic.ValidationError):
        _work_item(minimal_context="y" * 5000)


def test_work_item_rejects_wildcard_tool_ids() -> None:
    from assistant.missions.contracts import ActionScopeRecord

    with pytest.raises(pydantic.ValidationError):
        ActionScopeRecord(tool_ids=["*"], target_scope_hash="x" * 64)
    with pytest.raises(pydantic.ValidationError):
        ActionScopeRecord(tool_ids=["click*"], target_scope_hash="x" * 64)


def test_work_item_rejects_oversize_context_directly() -> None:
    with pytest.raises(pydantic.ValidationError):
        _work_item(minimal_context="z" * 4097)


# -- results and honest outcomes ------------------------------------------------


def _result(**overrides: object) -> StepResult:
    fields: dict[str, object] = {
        "mission_id": new_id(),
        "plan_version": 1,
        "control_epoch": 1,
        "step_id": "s1",
        "execution_id": new_id(),
        "attempt": 1,
        "status": "COMPLETED",
        "effect_outcome": "CONFIRMED",
    }
    fields.update(overrides)
    return StepResult(**fields)  # type: ignore[arg-type]


def test_completed_with_unknown_effect_rejected() -> None:
    with pytest.raises(pydantic.ValidationError):
        _result(effect_outcome="UNKNOWN")


def test_failed_result_requires_category() -> None:
    with pytest.raises(pydantic.ValidationError):
        _result(status="FAILED")
    result = _result(status="FAILED", failure_category="NO_PROGRESS")
    assert result.failure_category == "NO_PROGRESS"


def test_unknown_failure_category_rejected() -> None:
    with pytest.raises(pydantic.ValidationError):
        _result(status="FAILED", failure_category="SOMETHING_ELSE")


def test_read_only_result_uses_not_attempted() -> None:
    result = _result(effect_outcome="NOT_ATTEMPTED")
    assert result.status == "COMPLETED"
    assert result.effect_outcome == "NOT_ATTEMPTED"


# -- approvals and trace ----------------------------------------------------------


def test_approval_expiry_ordering_enforced() -> None:
    with pytest.raises(pydantic.ValidationError):
        ApprovalRecord(
            approval_id=new_id(),
            mission_id=new_id(),
            plan_version=1,
            control_epoch=1,
            action_digest="a" * 32,
            scope_hash="s",
            effect_class="EXTERNAL_WRITE",
            issued_at_ms=_now(),
            expires_at_ms=_now() - 1,
        )


def test_trace_event_kinds_and_chain() -> None:
    event = TraceEvent(
        event_id=new_id(),
        sequence=1,
        trace_id="m",
        mission_id="m",
        plan_version=1,
        control_epoch=1,
        kind="outcome",
        safe_payload={"ok": True},
        occurred_at_ms=_now(),
    )
    assert event.event_hash == event.compute_hash()
    with pytest.raises(pydantic.ValidationError):
        TraceEvent.model_validate(event.model_dump() | {"kind": "self_destruct"})


def test_trace_chain_detects_edit() -> None:
    event = TraceEvent(
        event_id=new_id(),
        sequence=2,
        trace_id="m",
        mission_id="m",
        plan_version=1,
        control_epoch=1,
        kind="budget",
        safe_payload={"units": 1},
        occurred_at_ms=_now(),
        previous_hash="p" * 64,
    )
    tampered = event.model_copy(update={"safe_payload": {"units": 999}})
    assert tampered.event_hash != tampered.compute_hash()


def test_observer_recommendation_cannot_request_authority() -> None:
    with pytest.raises(pydantic.ValidationError):
        ObserverRecommendation(
            recommendation_id=new_id(),
            pattern_id="p1",
            hypothesis="please activate the experiment runner",
            confidence=0.5,
            created_at_ms=_now(),
        )


def test_checkspec_bounds_expected_payload() -> None:
    with pytest.raises(pydantic.ValidationError):
        CheckSpec(
            check_id="c1",
            verifier_id="fixture",
            verifier_version="1",
            expected={"blob": "x" * 5000},
        )


def test_canonical_digests_are_stable() -> None:
    assert digest_of({"a": 1, "b": 2}) == digest_of({"b": 2, "a": 1})
    assert text_digest("hello") == text_digest("hello")
    assert text_digest("hello") != text_digest("goodbye")
    assert len(canonical_json({"k": [1, 2]})) > 0
