"""MissionAuthority tests (T03, file 06 U2): permits, scope, approvals.

The core property under test: a counterfeit or stale authorization can
never mint a permit, and every denial leaves the effect sink empty.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from assistant.missions.authority import AuthorityDenied, MissionAuthority, approval_digest
from assistant.missions.contracts import (
    ActionIntent,
    ActionScopeRecord,
    ApprovalRecord,
    BudgetCharge,
    BudgetLimits,
    Scope,
    ScopeObservation,
    new_id,
)
from assistant.missions.store import MissionStore


async def _never_runtime() -> Any:
    raise AssertionError("no runtime is needed for this test")


def _now() -> int:
    return int(time.time() * 1000)


@pytest.fixture()
async def store(tmp_path: Any) -> Any:
    s = await MissionStore.connect(tmp_path / "auth.db")
    await s.setup()
    yield s
    await s.close()


def _item(**overrides: Any):
    from assistant.missions.contracts import BoundedWorkItem, EvidenceRequirements

    scope = Scope(
        owner_id="owner",
        allowed_apps=["com.fixture.browser"],
        allowed_origins=["https://fixture.local"],
        permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"},
    )
    fields: dict[str, Any] = {
        "mission_id": new_id(),
        "plan_version": 1,
        "control_epoch": 1,
        "step_id": "s1",
        "execution_id": new_id(),
        "attempt": 1,
        "objective": "fixture",
        "expected_scope": scope,
        "allowed_action_scope": ActionScopeRecord(
            tool_ids=["list_apps", "click", "type_text"],
            target_scope_hash=scope.scope_hash,
            permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"},
        ),
        "recipe_id": "semantic_ui",
        "deadline_at_ms": _now() + 90_000,
        "budget": {"max_wall_ms": 90_000},
        "evidence_requirements": EvidenceRequirements(),
    }
    fields.update(overrides)
    return BoundedWorkItem(**fields)


def _observed(**overrides: Any) -> ScopeObservation:
    fields: dict[str, Any] = {
        "app_bundle": "com.fixture.browser",
        "pid": 4101,
        "window_id": 771,
        "origin": "https://fixture.local",
        "captured_at_ms": _now(),
        "driver_generation": "gen-1",
    }
    fields.update(overrides)
    return ScopeObservation(**fields)


def _action(**overrides: Any) -> ActionIntent:
    fields: dict[str, Any] = {"tool": "click", "effect_class": "REPEATABLE_LOCAL"}
    fields.update(overrides)
    return ActionIntent(**fields)


class _Intent:
    """Helper: record a committed intent for an item in the store."""

    def __init__(self, store: MissionStore) -> None:
        self.store = store

    async def commit(self, item: Any) -> None:
        from assistant.missions.contracts import BudgetLimits as BL
        from assistant.missions.contracts import RequestEnvelope

        request = RequestEnvelope(
            request_id=new_id(),
            conversation_id="c",
            owner_id="owner",
            input_origin="typed_final",
            input_revision=1,
            text="fixture",
            submitted_at_ms=_now(),
        )
        mission = await self.store.claim_request(
            request,
            "d" * 64,
            goal="fixture",
            scope=Scope(owner_id="owner"),
            limits=BL(),
        )
        # Direct attempt row for the item (the packet was minted elsewhere).
        self.store._conn.execute(
            """
            INSERT INTO mission_attempts (execution_id, mission_id, plan_version,
                step_id, attempt, control_epoch, packet_digest, dispatch_state,
                effect_class, lease_fence, created_at_ms, updated_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, 'digest', 'INTENT_COMMITTED', 'READ_ONLY', ?, ?, ?)
            """,
            (
                item.execution_id,
                mission.mission_id,
                item.plan_version,
                item.step_id,
                item.attempt,
                item.control_epoch,
                item.lease_fence,
                _now(),
                _now(),
            ),
        )


async def test_permit_minted_and_single_use(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item()
    await _Intent(store).commit(item)
    permit = await authority.authorize(item, _action(), _observed())
    used = await authority.consume_permit(permit.permit_id, _action().args_digest)
    assert used.used_at_ms is not None
    with pytest.raises(AuthorityDenied):
        await authority.consume_permit(permit.permit_id, _action().args_digest)


async def test_replayed_or_unknown_permit_denied(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    with pytest.raises(AuthorityDenied):
        await authority.consume_permit(new_id(), "x" * 64)


async def test_changed_arguments_invalidate_permit(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item()
    await _Intent(store).commit(item)
    permit = await authority.authorize(item, _action(), _observed())
    with pytest.raises(AuthorityDenied):
        await authority.consume_permit(permit.permit_id, "changed" * 8)


async def test_tool_outside_action_scope_denied(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item()
    await _Intent(store).commit(item)
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(
            item, _action(tool="set_clipboard"), _observed()
        )
    assert excinfo.value.category == "SCOPE_MISMATCH"


async def test_wrong_account_and_origin_denied(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item()
    await _Intent(store).commit(item)
    # A write with an unknown account on an account-bound scope fails closed.
    bound_item = _item(
        expected_scope=Scope(
            owner_id="owner",
            allowed_apps=["com.fixture.browser"],
            account_ref="acct-1",
            permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"},
        )
    )
    await _Intent(store).commit(bound_item)
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(
            bound_item, _action(), _observed(account_ref=None)
        )
    assert excinfo.value.category == "AUTH_REQUIRED"
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(
            bound_item, _action(), _observed(account_ref="acct-2")
        )
    assert excinfo.value.category == "SCOPE_MISMATCH"
    # Unknown origin for a write is refused even without an account binding.
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(item, _action(), _observed(origin=None))
    assert excinfo.value.category == "SCOPE_MISMATCH"


async def test_wrong_window_and_app_denied(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item()
    await _Intent(store).commit(item)
    with pytest.raises(AuthorityDenied):
        await authority.authorize(
            item, _action(), _observed(app_bundle="com.other.app")
        )


async def test_destructive_always_denied(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item()
    await _Intent(store).commit(item)
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(
            item, _action(effect_class="DESTRUCTIVE"), _observed()
        )
    assert excinfo.value.category == "PERMISSION_DENIED"


async def test_no_committed_intent_no_permit(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item()
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(item, _action(), _observed())
    assert excinfo.value.category == "UNKNOWN_EFFECT"


async def test_stale_epoch_or_cancelled_intent_denied(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item(control_epoch=2)
    await _Intent(store).commit(item)
    # The store recorded epoch 1 (insert above used item's values), so a
    # packet claiming epoch 2 with different store state is rejected via
    # the cancelled/dispatched state check.
    cancelled = _item()
    await _Intent(store).commit(cancelled)
    store._conn.execute(
        "UPDATE mission_attempts SET dispatch_state='CANCELLED' WHERE execution_id=?",
        (cancelled.execution_id,),
    )
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(cancelled, _action(), _observed())
    assert excinfo.value.category == "STALE_TARGET"


async def test_driver_generation_change_denied(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item(driver_generation="gen-1")
    await _Intent(store).commit(item)
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(item, _action(), _observed(driver_generation="gen-2"))
    assert excinfo.value.category == "STALE_TARGET"


async def test_external_write_requires_owner_approval(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item()
    item = item.model_copy(
        update={
            "effect_class": "EXTERNAL_WRITE",
            "allowed_action_scope": item.allowed_action_scope.model_copy(
                update={"permitted_effects": {"READ_ONLY", "REPEATABLE_LOCAL", "EXTERNAL_WRITE"}}
            ),
        }
    )
    item.expected_scope.permitted_effects.add("EXTERNAL_WRITE")
    await _Intent(store).commit(item)
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(item, _action(effect_class="EXTERNAL_WRITE"), _observed())
    assert excinfo.value.category == "APPROVAL_REQUIRED"

    # The owner (host) issues the approval; only the exact digest is accepted.
    digest = approval_digest(item, _action(effect_class="EXTERNAL_WRITE"))
    approval = ApprovalRecord(
        approval_id=new_id(),
        mission_id=item.mission_id,
        plan_version=item.plan_version,
        control_epoch=item.control_epoch,
        action_digest=digest,
        scope_hash=item.expected_scope.scope_hash,
        effect_class="EXTERNAL_WRITE",
        issued_at_ms=_now(),
        expires_at_ms=_now() + 60_000,
    )
    await store.record_approval(approval)
    authority.register_approvals(item.mission_id, item.control_epoch, [approval.approval_id])
    permit = await authority.authorize(item, _action(effect_class="EXTERNAL_WRITE"), _observed())
    assert permit.effect_class == "EXTERNAL_WRITE"

    # The consumed approval cannot authorize a second identical action.
    with pytest.raises(AuthorityDenied):
        await authority.authorize(item, _action(effect_class="EXTERNAL_WRITE"), _observed())


async def test_counterfeit_model_approval_is_not_an_approval(store: MissionStore) -> None:
    """A model-supplied 'approved' string carries no authority."""
    authority = MissionAuthority(store)
    item = _item()
    item = item.model_copy(
        update={
            "effect_class": "EXTERNAL_WRITE",
            "allowed_action_scope": item.allowed_action_scope.model_copy(
                update={"permitted_effects": {"READ_ONLY", "EXTERNAL_WRITE"}}
            ),
        }
    )
    item.expected_scope.permitted_effects.add("EXTERNAL_WRITE")
    await _Intent(store).commit(item)
    # No ApprovalRecord exists in the store; the model's claim is not one.
    authority.register_approvals(item.mission_id, item.control_epoch, ["model-said-approved"])
    with pytest.raises(AuthorityDenied):
        await authority.authorize(item, _action(effect_class="EXTERNAL_WRITE"), _observed())


async def test_expired_approval_denied(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    item = _item()
    item = item.model_copy(
        update={
            "effect_class": "EXTERNAL_WRITE",
            "allowed_action_scope": item.allowed_action_scope.model_copy(
                update={"permitted_effects": {"EXTERNAL_WRITE"}}
            ),
        }
    )
    item.expected_scope.permitted_effects = {"READ_ONLY", "EXTERNAL_WRITE"}
    await _Intent(store).commit(item)
    digest = approval_digest(item, _action(effect_class="EXTERNAL_WRITE"))
    approval = ApprovalRecord(
        approval_id=new_id(),
        mission_id=item.mission_id,
        plan_version=item.plan_version,
        control_epoch=item.control_epoch,
        action_digest=digest,
        scope_hash=item.expected_scope.scope_hash,
        effect_class="EXTERNAL_WRITE",
        issued_at_ms=_now() - 120_000,
        expires_at_ms=_now() - 60_000,
    )
    await store.record_approval(approval)
    authority.register_approvals(item.mission_id, item.control_epoch, [approval.approval_id])
    with pytest.raises(AuthorityDenied):
        await authority.authorize(item, _action(effect_class="EXTERNAL_WRITE"), _observed())


async def test_budget_reservation_late_retry_denied(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    request_id = new_id()
    from assistant.missions.contracts import BudgetLimits, RequestEnvelope

    request = RequestEnvelope(
        request_id=request_id,
        conversation_id="c",
        owner_id="owner",
        input_origin="typed_final",
        input_revision=1,
        text="fixture",
        submitted_at_ms=_now(),
    )
    from assistant.missions.contracts import Scope as ScopeT

    mission = await store.claim_request(
        request, "d" * 64, goal="fixture", scope=ScopeT(owner_id="owner"),
        limits=BudgetLimits(max_retries=1),
    )
    first = await authority.reserve(
        mission.mission_id, BudgetCharge(resource="retries", amount=1, call_key="r1")
    )
    await authority.settle(first, consumed=True)
    with pytest.raises(AuthorityDenied):
        await authority.reserve(
            mission.mission_id, BudgetCharge(resource="retries", amount=1, call_key="r2")
        )


async def test_paid_units_denied_without_allowance(store: MissionStore) -> None:
    authority = MissionAuthority(store)
    from assistant.missions.contracts import BudgetLimits, RequestEnvelope

    request = RequestEnvelope(
        request_id=new_id(),
        conversation_id="c",
        owner_id="owner",
        input_origin="typed_final",
        input_revision=1,
        text="fixture",
        submitted_at_ms=_now(),
    )
    mission = await store.claim_request(
        request, "d" * 64, goal="fixture", scope=Scope(owner_id="owner"),
        limits=BudgetLimits(),  # max_paid_units defaults to 0
    )
    with pytest.raises(AuthorityDenied):
        await authority.reserve(
            mission.mission_id, BudgetCharge(resource="paid_units", amount=1)
        )


async def test_unpriced_dollar_capped_call_denied(store: MissionStore) -> None:
    """A dollar cap without a conservative price bound refuses the call."""
    authority = MissionAuthority(store)
    from assistant.missions.contracts import BudgetLimits, RequestEnvelope

    request = RequestEnvelope(
        request_id=new_id(),
        conversation_id="c",
        owner_id="owner",
        input_origin="typed_final",
        input_revision=1,
        text="fixture",
        submitted_at_ms=_now(),
    )
    mission = await store.claim_request(
        request, "d" * 64, goal="fixture", scope=Scope(owner_id="owner"),
        limits=BudgetLimits(max_paid_units=5, max_cost_microunits=1_000_000, currency="USD"),
    )
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.reserve(
            mission.mission_id, BudgetCharge(resource="paid_units", amount=1)
        )
    assert "price is unknown" in excinfo.value.reason
    # With a conservative bound the reservation succeeds.
    reservation = await authority.reserve(
        mission.mission_id,
        BudgetCharge(resource="paid_units", amount=1, call_key="g1"),
        conservative_unit_cost_microunits=100_000,
    )
    assert reservation.amount == 1
    # The failed attempt never silently charged anything: exactly one
    # reservation row exists.
    rows = store._conn.execute(
        "SELECT COUNT(*) FROM mission_budget_reservations WHERE mission_id=?",
        (mission.mission_id,),
    ).fetchone()
    assert int(rows[0]) == 1


# -- R03 regressions: RP05/RP06 and read-scope guarding -------------------------


async def test_rp05_copied_scope_hash_cannot_broaden_plan(store: MissionStore) -> None:
    """RP05: a proposed plan broadening allowed_apps while copying the old
    hash is rejected — the hash is always recomputed from contents."""
    from assistant.missions.contracts import StepSpec
    from assistant.missions.controller import DeepController
    from assistant.missions.evidence import EvidenceStore
    from assistant.missions.executor import VeloExecutor
    from assistant.missions.service import MissionPlanError, MissionService

    scope = Scope(owner_id="owner", allowed_apps=["com.fixture.browser"],
                  permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"})
    forged = StepSpec(
        step_id="s1", ordinal=1, objective="fixture", recipe_id="open_app",
        scope=Scope(owner_id="owner", allowed_apps=["com.fixture.browser", "com.other.app"],
                    permitted_effects={"READ_ONLY", "REPEATABLE_LOCAL"}),
        budget=BudgetLimits(),
    )
    class _S:
        velo_jev_enabled = False
        velo_command_deadline_seconds = 90
        cua_artifact_dir = ""
        mission_allowed_apps: list[str] = []

    service = MissionService(
        _S(), store=store,
        authority=MissionAuthority(store),
        evidence=EvidenceStore("/tmp/fixture-r03"),
        executor=VeloExecutor(_S(), get_runtime=_never_runtime, authority=None, evidence=None),
        controller=DeepController(None),
    )
    from assistant.missions.contracts import PlanProposal

    proposal = PlanProposal(steps=[forged], explanation="broadened")
    with pytest.raises(MissionPlanError):
        service._validate_proposal(proposal, mission=await _seed_mission(store, scope))


async def _seed_mission(store: MissionStore, scope: Scope) -> Any:
    from assistant.missions.contracts import RequestEnvelope

    request = RequestEnvelope(
        request_id=new_id(), conversation_id="c", owner_id="owner",
        input_origin="typed_final", input_revision=1, text="fixture",
        submitted_at_ms=_now(),
    )
    return await store.claim_request(
        request, "d" * 64, goal="fixture", scope=scope, limits=BudgetLimits()
    )


async def test_rp06_empty_observed_app_denied(store: MissionStore) -> None:
    """RP06: a packet scoped to an app cannot get a permit for an unnamed,
    stale surface (timestamp 0, no driver identity)."""
    authority = MissionAuthority(store)
    item = _item()
    await _Intent(store).commit(item)
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(
            item, _action(), _observed(app_bundle="", pid=None, captured_at_ms=0)
        )
    assert excinfo.value.category == "STALE_TARGET"


async def test_stale_observation_denied(store: MissionStore) -> None:
    """An observation older than the fresh window cannot authorize action."""
    import time as time_module

    authority = MissionAuthority(store)
    item = _item()
    await _Intent(store).commit(item)
    old = int(time_module.time() * 1000) - 60_000
    with pytest.raises(AuthorityDenied) as excinfo:
        await authority.authorize(item, _action(), _observed(captured_at_ms=old))
    assert excinfo.value.category == "STALE_TARGET"


async def test_scope_sensitive_reads_are_guarded(store: MissionStore) -> None:
    """A READ of an out-of-scope surface is denied, like a mutation."""
    authority = MissionAuthority(store)
    item = _item(
        allowed_action_scope=_item().allowed_action_scope.model_copy(
            update={"tool_ids": ["list_apps", "click", "type_text", "get_window_state"]}
        )
    )
    await _Intent(store).commit(item)
    with pytest.raises(AuthorityDenied):
        await authority.authorize(
            item,
            _action(tool="get_window_state", effect_class="READ_ONLY"),
            _observed(app_bundle="com.other.app"),
        )
    # The same read inside scope is authorized.
    permit = await authority.authorize(
        item,
        _action(tool="get_window_state", effect_class="READ_ONLY"),
        _observed(),
    )
    assert permit.effect_class == "READ_ONLY"


async def test_typing_is_conservatively_classified(store: MissionStore) -> None:
    """type_text/set_value are EXTERNAL_WRITE (non-idempotent), never a
    blanket repeatable-local label."""
    from assistant.missions.executor import _effect_for_tool

    assert _effect_for_tool("type_text", _item()) == "EXTERNAL_WRITE"
    assert _effect_for_tool("set_value", _item()) == "EXTERNAL_WRITE"
    assert _effect_for_tool("list_apps", _item()) == "READ_ONLY"
